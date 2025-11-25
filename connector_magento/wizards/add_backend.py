# -*- coding: utf-8 -*-
# Copyright <YEAR(S)> <AUTHOR(S)>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models, fields, _
from odoo.exceptions import UserError


class WizardModel(models.TransientModel):
    _name = "connector_magento.add_backend.wizard"

    # @api.multi
    def get_default_object(self, model):
        domain = []
        active_ids = self.env.context.get('active_ids', False)
        active_model = self.env.context.get('active_model', False)

        if not active_ids:
            return []
        domain.append(('id', 'in', active_ids))
        export = self.env[active_model]
        if active_model == model:
            return export.search(domain)

    # @api.multi
    def get_default_model(self):
        model = self.env.context.get('active_model', False)
        if model:
            return self.env['ir.model'].search([('model', '=', model)], limit=1).id
        return False

    # @api.multi
    def get_default_backend(self):
        return self.env['magento.backend'].search([], limit=1)

    # @api.multi
    def _get_ids_and_model(self):
        active_model = self.env.context.get('active_model', False)
        binding_field= 'magento_bind_ids'
        if active_model == 'product.template':
            binding_field = 'magento_variant_bind_ids'

        if hasattr(self.env[active_model],binding_field):
            bindings=self.env[active_model].browse(self.env.context.get('active_ids', []))
            if active_model == 'product.template':
                # Necesito que me devuelva en el caso de product_template los productos variantes
                # que estan asociados a la plantilla
                bindings = bindings.mapped('product_variant_ids')
            return bindings , getattr(bindings,binding_field)._name
        else:
            raise ValueError('Model not supported')

    def _validate_backend_ready(self):
        """Validate backend has attribute sets before creating bindings"""
        backend = self.backend_id
        if not backend:
            raise UserError(_("No backend selected."))

        if not self.env['magento.product.attribute.set'].search([('backend_id', '=', backend.id)]):
            raise UserError(_(
                "No attribute sets found for backend '%s'. "
                "Please import attribute sets first using the backend form."
            ) % backend.name)

    @api.depends('backend_id', 'product_links')
    def _compute_product_type(self):
        """Automatically detect product type based on context and configuration."""
        for wizard in self:
            active_model = wizard.env.context.get('active_model')
            active_ids = wizard.env.context.get('active_ids', [])

            # Default values
            wizard.product_type = False
            wizard.detected_template_type = False

            if not active_model or not active_ids:
                wizard.detected_template_type = "No product selected"
                continue

            if active_model == 'product.template':
                # Called from template
                templates = wizard.env['product.template'].browse(active_ids)
                if not templates:
                    continue
                template = templates[0]  # Use first template for detection

                if template.has_variant_attributes:
                    wizard.product_type = 'configurable'
                    wizard.detected_template_type = "Configurable Template (has variant attributes)"
                else:
                    wizard.product_type = 'simple'
                    wizard.detected_template_type = "Simple Template (no variant attributes)"

            elif active_model == 'product.product':
                # Called from product variant
                products = wizard.env['product.product'].browse(active_ids)
                if not products:
                    continue
                product = products[0]  # Use first product for detection
                template = product.product_tmpl_id

                # Check if template has variant attributes
                if template.has_variant_attributes:
                    # ERROR: Cannot create binding from variant of configurable template
                    wizard.product_type = False
                    wizard.detected_template_type = "ERROR: This product belongs to a configurable template. Please use the template instead."
                else:
                    # Simple product or grouped
                    if wizard.product_links:
                        wizard.product_type = 'grouped'
                        wizard.detected_template_type = "Grouped Product (simple product with links)"
                    else:
                        wizard.product_type = 'simple'
                        wizard.detected_template_type = "Simple Product (add linked products for Grouped)"

    @api.depends('product_type', 'product_links', 'backend_id')
    def _compute_warnings(self):
        """Generate validation warnings before creating bindings."""
        for wizard in self:
            warnings = []

            # Check for ERROR in detected_template_type
            if wizard.detected_template_type and 'ERROR' in wizard.detected_template_type:
                warnings.append("⚠ " + wizard.detected_template_type)

            # Check if grouped product has no links
            if wizard.product_type == 'grouped' and not wizard.product_links:
                warnings.append("⚠ Grouped products require at least one linked product")

            # Check if product_links have different backends
            if wizard.product_links and wizard.backend_id:
                for link in wizard.product_links:
                    # Get Magento bindings for this product
                    link_bindings = link.magento_bind_ids.filtered(
                        lambda b: b.backend_id == wizard.backend_id
                    )
                    if not link_bindings:
                        warnings.append(f"⚠ Linked product '{link.display_name}' is not configured for backend '{wizard.backend_id.name}'")

            # Validate SKU based on product type
            active_model = wizard.env.context.get('active_model')
            active_ids = wizard.env.context.get('active_ids', [])

            if active_model == 'product.template' and active_ids:
                template = wizard.env['product.template'].browse(active_ids[0])
                if template.has_variant_attributes:
                    # Configurable: requires code_prefix
                    if not template.code_prefix:
                        warnings.append(
                            "⚠️ Configurable template WITHOUT 'code_prefix': "
                            "Required to export to Magento"
                        )
                else:
                    # Simple: requires code_prefix or default_code in variant
                    if not template.code_prefix and not template.product_variant_ids[0].default_code:
                        warnings.append(
                            "⚠️ Simple template without 'code_prefix' or 'default_code': "
                            "SKU will be auto-generated (not recommended)"
                        )

            elif active_model == 'product.product' and active_ids:
                product = wizard.env['product.product'].browse(active_ids[0])
                template = product.product_tmpl_id

                if template.has_variant_attributes:
                    # Variant of configurable: cannot create binding directly
                    warnings.append(
                        "❌ ERROR: This variant belongs to a configurable template. "
                        "Must create binding from the template, not from the variant."
                    )
                elif not product.default_code:
                    warnings.append(
                        "⚠️ Product without 'default_code': "
                        "SKU will be auto-generated (not recommended)"
                    )

            # Check for duplicate bindings
            if wizard.backend_id:
                if active_model == 'product.template' and active_ids:
                    # Check for existing template bindings
                    existing = wizard.env['magento.product.template'].search([
                        ('odoo_id', 'in', active_ids),
                        ('backend_id', '=', wizard.backend_id.id)
                    ])
                    if existing:
                        warnings.append(f"⚠ Backend '{wizard.backend_id.name}' is already configured for this template")

                elif active_model == 'product.product' and active_ids:
                    # Check for existing product bindings
                    existing = wizard.env['magento.product.product'].search([
                        ('odoo_id', 'in', active_ids),
                        ('backend_id', '=', wizard.backend_id.id)
                    ])
                    if existing:
                        warnings.append(f"⚠ Backend '{wizard.backend_id.name}' is already configured for this product")

            wizard.warning_message = '\n'.join(warnings) if warnings else False

    @api.constrains('product_type', 'product_links')
    def _check_grouped_requirements(self):
        """Ensure grouped products have at least one linked product."""
        for wizard in self:
            if wizard.product_type == 'grouped' and not wizard.product_links:
                raise UserError(_("Grouped products require at least one linked product."))

    # DEPRECATED: Use template.has_variant_attributes field instead
    # def _is_configurable_template(self, template):
    #     """Check if template has variant-creating attributes (configurable)
    #
    #     DEPRECATED: This method is deprecated. Use template.has_variant_attributes instead.
    #     The has_variant_attributes field is a stored computed field on product.template
    #     that provides the same functionality with better performance.
    #     """
    #     if not template.attribute_line_ids:
    #         return False
    #
    #     # Check if any attribute creates variants
    #     for line in template.attribute_line_ids:
    #         if line.attribute_id.create_variant in ('always', 'dynamic'):
    #             return True
    #     return False

    def _get_default_attribute_set(self):
        """Safely get default attribute set"""
        attribute_sets = self.env['magento.product.attribute.set'].search([
            ('backend_id', '=', self.backend_id.id)
        ])
        if not attribute_sets:
            raise UserError(_(
                "No attribute sets found for backend '%s'. "
                "Please import attribute sets first using the backend form."
            ) % self.backend_id.name)
        return attribute_sets[0]

    # @api.multi
    def check_backend_binding(self, to_export_ids=None, dest_model=None):
        """Main entry point - routes to appropriate processing based on context"""
        self._validate_backend_ready()

        active_model = self.env.context.get('active_model')

        if active_model == 'product.template':
            # Called from templates - process template bindings
            self._process_template_bindings()
        elif active_model == 'product.product':
            # Called from variants - process product bindings
            self._process_product_bindings()
        else:
            # Fallback to original logic for other models
            if not dest_model or not to_export_ids:
                (to_export_ids, dest_model) = self._get_ids_and_model()

            for model in to_export_ids:
                bind_count = self.env[dest_model].search_count([
                    ('odoo_id', '=', model.id),
                    ('backend_id', '=', self.backend_id.id)
                ])
                if not bind_count:
                    vals = {
                        'odoo_id': model.id,
                        'backend_id': self.backend_id.id,
                        'product_type': self.product_type
                    }
                    if self.product_type == 'grouped':
                        vals['product_links'] = [(6, 0, self.product_links.ids)]
                    binding = self.env[dest_model].create(vals)
                    if self.action == 'import':
                        if getattr(binding, 'sync_from_magento', False):
                            binding.sync_from_magento()
                    elif self.action == 'export':
                        if getattr(binding, 'sync_to_magento', False):
                            binding.sync_to_magento()

    def _process_template_bindings(self):
        """Process bindings when called from product.template context"""
        active_ids = self.env.context.get('active_ids', [])
        templates = self.env['product.template'].browse(active_ids)

        for template in templates:
            if template.has_variant_attributes:
                # Configurable: create template binding
                self._create_template_binding(template)
            else:
                # Simple: create product bindings for variants
                for variant in template.product_variant_ids:
                    self._create_product_binding(variant)

    def _process_product_bindings(self):
        """Process bindings when called from product.product context"""
        active_ids = self.env.context.get('active_ids', [])
        products = self.env['product.product'].browse(active_ids)

        for product in products:
            template = product.product_tmpl_id

            if template.has_variant_attributes:
                # Configurable: create template binding (not product binding)
                self._create_template_binding(template)
            else:
                # Simple: create product binding
                self._create_product_binding(product)

    def _process_grouped_product_binding(self):
        """Process grouped product bindings with linked products."""
        active_ids = self.env.context.get('active_ids', [])
        products = self.env['product.product'].browse(active_ids)

        for product in products:
            template = product.product_tmpl_id

            # Validate product is not from configurable template
            if template.has_variant_attributes:
                raise UserError(_(
                    "Cannot create grouped product from '%s' because it belongs to "
                    "a configurable template. Grouped products can only be created "
                    "from simple products."
                ) % product.display_name)

            # Check if binding already exists
            existing_binding = self.env['magento.product.product'].search([
                ('odoo_id', '=', product.id),
                ('backend_id', '=', self.backend_id.id)
            ])

            if existing_binding:
                # Update existing binding with grouped type and links
                existing_binding.write({
                    'product_type': 'grouped',
                    'product_links': [(6, 0, self.product_links.ids)]
                })
                binding = existing_binding
            else:
                # Create new grouped product binding
                vals = {
                    'odoo_id': product.id,
                    'backend_id': self.backend_id.id,
                    'product_type': 'grouped',
                    'product_links': [(6, 0, self.product_links.ids)],
                    'attribute_set_id': self._get_default_attribute_set().id,
                }
                binding = self.env['magento.product.product'].create(vals)

            # Only sync if action is explicitly 'export'
            if self.action == 'export':
                if getattr(binding, 'sync_to_magento', False):
                    binding.sync_to_magento()
            elif self.action == 'import':
                if getattr(binding, 'sync_from_magento', False):
                    binding.sync_from_magento()

    def _create_template_binding(self, template):
        """Create magento.product.template binding for configurable products"""
        # Check if binding already exists
        existing_binding = self.env['magento.product.template'].search([
            ('odoo_id', '=', template.id),
            ('backend_id', '=', self.backend_id.id)
        ])

        if existing_binding:
            return existing_binding

        vals = {
            'odoo_id': template.id,
            'backend_id': self.backend_id.id,
            'product_type': 'configurable',
            'attribute_set_id': self._get_default_attribute_set().id,
        }

        binding = self.env['magento.product.template'].create(vals)

        if self.action == 'import':
            if getattr(binding, 'sync_from_magento', False):
                binding.sync_from_magento()
        elif self.action == 'export':
            if getattr(binding, 'sync_to_magento', False):
                binding.sync_to_magento()

        return binding

    def _create_product_binding(self, product):
        """Create magento.product.product binding for simple products"""
        # Validate that it's not a variant of a configurable template
        if product.product_tmpl_id.has_variant_attributes:
            raise UserError(_(
                "Cannot create binding for variant '%s': it belongs to a configurable template.\n\n"
                "Please create the binding from the template instead, which will automatically "
                "create bindings for all variants."
            ) % product.display_name)

        # Validate that it has default_code
        if not product.default_code:
            raise UserError(_(
                "Cannot create binding for product '%s': it requires a 'default_code' (SKU).\n\n"
                "Please set the default_code before creating the Magento binding."
            ) % product.display_name)

        # Check if binding already exists
        existing_binding = self.env['magento.product.product'].search([
            ('odoo_id', '=', product.id),
            ('backend_id', '=', self.backend_id.id)
        ])

        if existing_binding:
            return existing_binding

        vals = {
            'odoo_id': product.id,
            'backend_id': self.backend_id.id,
            'product_type': 'simple',
            'attribute_set_id': self._get_default_attribute_set().id,
        }

        binding = self.env['magento.product.product'].create(vals)

        if self.action == 'import':
            if getattr(binding, 'sync_from_magento', False):
                binding.sync_from_magento()
        elif self.action == 'export':
            if getattr(binding, 'sync_to_magento', False):
                binding.sync_to_magento()

        return binding

    # Fields
    product_type = fields.Selection([
        ('simple', 'Simple Product'),
        ('configurable', 'Configurable Product'),
        ('grouped', 'Grouped Product'),
        # ('bundle', 'Bundle Product'),
        # ('virtual', 'Virtual Product'),
        # ('downloadable', 'Downloadable Product'),
    ], string='Product Type',
        compute='_compute_product_type',
        readonly=True,
        store=False,
        help="Automatically detected product type based on context and product configuration")
    product_links = fields.Many2many(
        comodel_name='product.product',
        relation='wizard_magento_product_grouped_links',
        column1='wizard_id',
        column2='product_id',
        string='Linked Products',
        help="Select products to link for grouped product type"
    )
    detected_template_type = fields.Char(
        string='Detected Type',
        compute='_compute_product_type',
        readonly=True,
        store=False,
        help="User-friendly description of detected product type"
    )
    warning_message = fields.Text(
        string='Warnings',
        compute='_compute_warnings',
        readonly=True,
        store=False,
        help="Validation warnings before creating binding"
    )
    backend_id = fields.Many2one(comodel_name='magento.backend', required=True, default=get_default_backend)
    model_id = fields.Many2one('ir.model', default=get_default_model)
    action = fields.Selection([
        ('only_create', 'Only create binding'),
        ('import', 'Import'),
        ('export', 'Export'),
    ], default='only_create', required=True, help="Action to perform after creating binding")


    def action_accept(self):
        """Process wizard and create bindings with validation."""
        self.ensure_one()

        # Validate no ERROR in warnings
        if self.warning_message and 'ERROR' in self.warning_message:
            raise UserError(self.warning_message)

        # Validate backend is ready
        self._validate_backend_ready()

        # Route to appropriate processing based on product_type
        active_model = self.env.context.get('active_model')

        if self.product_type == 'grouped':
            # Grouped products: use special processing
            self._process_grouped_product_binding()
        elif active_model == 'product.template':
            # Template context: process template bindings
            self._process_template_bindings()
        elif active_model == 'product.product':
            # Product context: process product bindings
            self._process_product_bindings()
        else:
            # Fallback to old check_backend_binding for other models
            self.check_backend_binding()

        return {'type': 'ir.actions.act_window_close'}
