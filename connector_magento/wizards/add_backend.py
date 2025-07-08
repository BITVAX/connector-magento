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

    def _is_configurable_template(self, template):
        """Check if template has variant-creating attributes (configurable)"""
        if not template.attribute_line_ids:
            return False

        # Check if any attribute creates variants
        for line in template.attribute_line_ids:
            if line.attribute_id.create_variant in ('always', 'dynamic'):
                return True
        return False

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
            if self._is_configurable_template(template):
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

            if self._is_configurable_template(template):
                # Configurable: create template binding (not product binding)
                self._create_template_binding(template)
            else:
                # Simple: create product binding
                self._create_product_binding(product)

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
    ], default='simple', required=True)
    product_links = fields.Many2many('magento.product.product',
                                     'magento_product_product_grouped_rel',
                                        string='Linked Products')
    backend_id = fields.Many2one(comodel_name='magento.backend', required=True, default=get_default_backend)
    model_id = fields.Many2one('ir.model', default=get_default_model)
    action = fields.Selection([
        ('only_create', 'Only create binding'),
        ('import', 'Import'),
        ('export', 'Export'),
    ], default='export', required=True)


    def action_accept(self):
        self.ensure_one()
        self.check_backend_binding()
