# -*- coding: utf-8 -*-
# Copyright 2019 Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import api, models, fields
from odoo.addons.component.core import Component
from odoo.addons.queue_job.job import identity_exact
# from odoo.addons.queue_job.job import job, related_action
from ...components.backend_adapter import MAGENTO_DATETIME_FORMAT

_logger = logging.getLogger(__name__)


class MagentoProductTemplate(models.Model):
    _name = 'magento.product.template'
    _inherit = 'magento.binding'
    _inherits = {'product.template': 'odoo_id'}
    _description = 'Magento Product Template'
    _magento_backend_path = 'catalog/product/edit/id'
    _magento_frontend_path = 'catalog/product/view/id'

    # @api.depends('backend_id', 'external_id')
    # def _compute_magento_backend_url(self):
    #     for binding in self:
    #         if binding._magento_backend_path:
    #             binding.magento_backend_url = "%s/%s" % (urljoin(binding.backend_id.admin_location, binding._magento_backend_path), binding.magento_id)
    #         if binding._magento_frontend_path:
    #             binding.magento_frontend_url = "%s/%s" % (urljoin(binding.backend_id.location, binding._magento_frontend_path), binding.magento_id)

    @api.model
    def product_type_get(self):
        return [
            ('configurable', 'Configurable Product'),
            ('bundle', 'Bundle Product'),
        ]

    # @api.depends('backend_id', 'odoo_id')
    # def _compute_product_categories(self):
    #     for binding in self:
    #         magento_product_position_ids = self.env['magento.product.position'].search([
    #             ('magento_product_category_id.backend_id', '=', binding.backend_id.id),
    #             ('product_template_id', '=', binding.odoo_id.id),
    #         ])
    #         binding.magento_product_category_ids = [mpp.magento_product_category_id.id for mpp in magento_product_position_ids]
    #         binding.magento_product_position_ids = magento_product_position_ids

    # def _inverse_product_category_positions(self):
    #     for position in self.magento_product_position_ids:
    #         if isinstance(position.id, NewId):
    #             self.env['magento.product.position'].create({
    #                 'product_template_id': position.product_template_id.id,
    #                 'magento_product_category_id': position.magento_product_category_id.id,
    #                 'position': position.position,
    #             })
    #         else:
    #             self.env['magento.product.position'].browse(position.id).update({
    #                 'position': position.position,
    #             })

    attribute_set_id = fields.Many2one('magento.product.attribute.set',
                                       string='Attribute set')

    odoo_id = fields.Many2one(comodel_name='product.template',
                              string='Product Template',
                              required=True,
                              ondelete='cascade')
    website_ids = fields.Many2many(comodel_name='magento.website',
                                   string='Websites',
                                   readonly=False)
    product_type = fields.Selection(selection='product_type_get',
                                    string='Magento Product Type',
                                    default='simple',
                                    required=True)
    magento_id = fields.Integer('Magento ID')
    # magento_name = fields.Char('Name', translate=True)
    # magento_price = fields.Float('Backend Preis', default=0.0, digits=dp.get_precision('Product Price'),)
    # magento_stock_item_ids = fields.One2many(
    #     comodel_name='magento.stock.item',
    #     inverse_name='magento_product_template_binding_id',
    #     string="Magento Stock Items",
    # )
    created_at = fields.Datetime('Created At (on Magento)')
    updated_at = fields.Datetime('Updated At (on Magento)')

    magento_template_attribute_line_ids = fields.One2many(
        comodel_name='magento.product.template.attribute.line',
        inverse_name='magento_template_id',
        string='Magento Attribute lines for templates',
    )
    # magento_product_position_ids = fields.One2many(
    #     comodel_name='magento.product.position',
    #     compute='_compute_product_categories',
    #     inverse='_inverse_product_category_positions',
    #     string='Product positions'
    # )
    # magento_product_category_ids = fields.One2many(
    #     comodel_name='magento.product.category',
    #     compute='_compute_product_categories',
    #     string='Product categories'
    # )
    magento_url_key = fields.Char(
        string="URL Key",
        store=True,
        readonly=False
    )
    magento_status = fields.Selection([
        ('2', 'Disabled'),
        ('1', 'Enabled'),
    ], default='1', string="Status")

    _sql_constraints = [
        ('backend_magento_id_uniqueid',
         'UNIQUE (backend_id, magento_id)',
         'Duplicate binding of product detected, maybe SKU changed ?'
         ),
        ('backend_url_key_uniqueid',
         'UNIQUE (backend_id, magento_url_key)',
         'Duplicate URL Key is not allowed - please set a new one !'
         ),
    ]

    # @api.multi
    # @job(default_channel='root.magento')
    def sync_from_magento(self):
        for binding in self:
            delayed = binding.with_delay(identity_key=identity_exact).run_sync_from_magento()
            job = self.env['queue.job'].search([('uuid', '=', delayed.uuid)])
            binding.odoo_id.with_context(connector_no_export=True).job_ids += job

    # @api.multi
    # @job(default_channel='root.magento')
    def run_sync_from_magento(self):
        self.ensure_one()
        with self.backend_id.work_on(self._name) as work:
            importer = work.component(usage='record.importer')
            return importer.run(self.external_id, force=True)

    # def write(self, vals):
    #     if 'attribute_set_id' in vals:
    #         for configurable in self:
    #             for mvariant in configurable.magento_product_ids:
    #                 mvariant.attribute_set_id = vals['attribute_set_id']
    #     return super(MagentoProductTemplate, self).write(vals)
    #
    # def unlink(self):
    #     for template in self:
    #         template.magento_stock_item_ids.unlink()
    #         template.magento_product_ids.unlink()
    #     return super(MagentoProductTemplate, self).unlink()
    def export_inventory(self, fields=None):
        """ Export the inventory configuration and quantity of a product. """
        self.ensure_one()
        with self.backend_id.work_on(self._name) as work:
            exporter = work.component(usage='product.inventory.exporter')
            return exporter.run(self, fields)

    def export_record_with_dependencies(self):
        """Export product template with dependency graph using on_done().

        This method creates a hierarchical job graph with explicit dependencies:
        1. Categories/attributes/values are exported FIRST (dependencies)
        2. Template waits for ALL dependencies via on_done()
        3. Variants wait for template via on_done()

        Job execution flow:
        [Categories, Attrs, Values] → Template → [Variants]
              (parallel)               ↓        (parallel)

        Each job has its own transaction. If a job fails, only that
        transaction rolls back. Dependent jobs wait in 'wait_dependencies'
        state until their parent succeeds.

        Returns:
            delayed: The root job that starts the dependency chain
        """
        self.ensure_one()
        dependency_jobs = []

        # 1. Create category export jobs
        for extra_category in self.product_category_public_ids:
            magento_categ = extra_category.magento_bind_ids.filtered(
                lambda bc: bc.backend_id == self.backend_id
            )
            if not magento_categ:
                magento_categ = self.env['magento.product.category'].with_context(
                    connector_no_export=True
                ).create({
                    'backend_id': self.backend_id.id,
                    'odoo_id': extra_category.id,
                })

            if not magento_categ.external_id:
                cat_job = magento_categ.delayable(priority=20).export_record()
                dependency_jobs.append(cat_job)
                _logger.info("Added category job for: %s", extra_category.name)

        # 2. Create attribute and value export jobs
        for att_line in self.attribute_line_ids:
            m_att = att_line.attribute_id.magento_bind_ids.filtered(
                lambda m: m.backend_id == self.backend_id
            )
            # Only create if doesn't exist and needs export
            if not m_att or not m_att.external_id:
                if not m_att:
                    m_att = self.env['magento.product.attribute'].with_context(
                        connector_no_export=True
                    ).create({
                        'backend_id': self.backend_id.id,
                        'odoo_id': att_line.attribute_id.id,
                        'attribute_set_ids': [(4, self.attribute_set_id.id, 0)]
                        if self.attribute_set_id else False,
                        'attribute_code': att_line.attribute_id.name.lower(),
                    })

                if not m_att.external_id:
                    attr_job = m_att.delayable(priority=20).export_record()
                    dependency_jobs.append(attr_job)
                    _logger.info("Added attribute job for: %s", att_line.attribute_id.name)

                # Export attribute values
                for value in att_line.value_ids:
                    m_value = value.magento_bind_ids.filtered(
                        lambda m: m.magento_attribute_id == m_att
                    )
                    if not m_value or not m_value.external_id:
                        if not m_value:
                            m_value = self.env['magento.product.attribute.value'].with_context(
                                connector_no_export=True
                            ).create({
                                'odoo_id': value.id,
                                'magento_attribute_id': m_att.id,
                            })

                        if not m_value.external_id:
                            value_job = m_value.delayable(priority=20).export_record()
                            dependency_jobs.append(value_job)
                            _logger.info("Added value job for: %s", value.name)

        # 3. Create template export job (with light_sync to avoid re-exporting variants)
        template_job = self.delayable(priority=10).export_record(light_sync=True)
        _logger.info("Created template job for: %s", self.name)

        # 4. Chain all dependencies to template: dependencies → template
        # All dependency jobs must complete before template starts
        if dependency_jobs:
            for dep_job in dependency_jobs:
                dep_job.on_done(template_job)
            _logger.info("Chained %d dependency jobs to template", len(dependency_jobs))

        # 5. Create variant export jobs that depend on template
        variant_jobs = []
        for variant in self.product_variant_ids:
            m_prod = variant.magento_bind_ids.filtered(
                lambda m: m.backend_id == self.backend_id
            )
            if not m_prod:
                m_prod = self.env['magento.product.product'].with_context(
                    connector_no_export=True
                ).create({
                    'backend_id': self.backend_id.id,
                    'odoo_id': variant.id,
                    'attribute_set_id': self.attribute_set_id.id,
                    'magento_visibility': '1',
                })

            if not m_prod.external_id:
                variant_job = m_prod.delayable(priority=5).export_record()
                variant_jobs.append(variant_job)
                # Chain: template → variant (each variant waits for template)
                template_job.on_done(variant_job)
                _logger.info("Added variant job for: %s", variant.display_name)

        _logger.info("Created job graph: %d deps → 1 template → %d variants",
                    len(dependency_jobs), len(variant_jobs))

        # 6. Delay ONLY the first job in the graph to start execution
        # The rest will be triggered automatically via on_done() chains
        if dependency_jobs:
            root_job = dependency_jobs[0]
        else:
            root_job = template_job

        delayed = root_job.delay()
        _logger.info("Started job graph execution from root job")

        # Track the root job
        job = self.env['queue.job'].search([('uuid', '=', delayed.uuid)])
        self.odoo_id.with_context(connector_no_export=True).job_ids += job

        return delayed

class ProductTemplate(models.Model):
    _inherit = 'product.template'

    url_key = fields.Char(
        string="URL Key",
        help="SEO-friendly URL key for this product. Usually imported from Magento.",
        index=True
    )

    meta_title = fields.Char(
        string="Título SEO",
        help="SEO title for this product. Usually imported from Magento.",
        translate=True
    )
    meta_keyword = fields.Char(
        string="Palabras clave",
        help="SEO keywords for this product. Usually imported from Magento."
    )
    meta_description = fields.Text(
        string="Descripción SEO",
        help="SEO description for this product. Usually imported from Magento.",
        translate=True
    )

    product_category_public_ids = fields.Many2many(
        comodel_name='product.category.public',
        relation='product_category_public_rel',
        string='Public Categories'
    )

    website_ids = fields.Many2many(
        comodel_name='magento.website',
        string='Magento Websites',
    )
    root_category_ids = fields.Many2many(
        comodel_name='product.category.public',
        string='Root Categories',
        compute='_compute_root_category_ids',
        invisible=True,
    )

    @api.depends('website_ids')
    def _compute_root_category_ids(self):
        for rec in self:
            rec.root_category_ids = rec.website_ids.mapped('root_category_id.odoo_id')
            if not rec.root_category_ids:
                rec.root_category_ids = self.env['product.category.public'].search([('parent_id', '=', False)])

    @api.depends('job_ids', 'job_ids.state')
    def _compute_job_counts(self):
        for template in self.sudo():
            failed_jobs = template.job_ids.filtered(lambda j: j.state == 'failed')
            open_jobs = template.job_ids.filtered(lambda j: j.state in ['pending', 'enqueued', 'started'])

            template.with_context(connector_no_export=True).update({
                'open_job_count': len(open_jobs),
                'failed_job_count': len(failed_jobs),
            })

    magento_bind_ids = fields.One2many(
        comodel_name='magento.product.template',
        inverse_name='odoo_id',
        string='Magento Bindings',
    )
    magento_variant_bind_ids = fields.One2many(
        comodel_name='magento.product.product',
        compute="_compute_magento_variant_bind_ids",
        string='Magento Variant Bindings',
    )
    auto_create_variants = fields.Boolean('Auto Create Variants', default=True)
    magento_default_code = fields.Char(string="Default code used for magento")
    job_ids = fields.Many2many('queue.job', string="Jobs")
    open_job_count = fields.Integer(string='Open Jobs', compute='_compute_job_counts', store=False)
    failed_job_count = fields.Integer(string='Failed Jobs', compute='_compute_job_counts', store=False)
    magento_internal_id = fields.Char(string="Magento Internal ID")
    magento_status = fields.Selection([
        ('2', 'Disabled'),
        ('1', 'Enabled'),
    ], default='1', string="Status")
    magento_visibility = fields.Selection([
        ('1', 'Not Visible Individually'),
        ('2', 'Catalog'),
        ('3', 'Search'),
        ('4', 'Catalog, Search'),
    ], default='4', string="Visibility")

    has_variant_attributes = fields.Boolean(
        string='Has Variant Attributes',
        compute='_compute_has_variant_attributes',
        store=True,
        help="Technical field: True if template has attributes that create variants (create_variant='always' or 'dynamic')"
    )
    magento_bindings_count = fields.Integer(
        string='Magento Bindings',
        compute='_compute_magento_sync_info',
        help="Number of Magento backend bindings for this product"
    )
    magento_sync_state = fields.Selection([
        ('none', 'Not Configured'),
        ('unpublished', 'Unpublished'),
        ('partial', 'Partially Published'),
        ('published', 'Published'),
    ], string='Magento Sync State',
        compute='_compute_magento_sync_info',
        store=True,
        help="Synchronization state with Magento backends"
    )

    @api.depends('attribute_line_ids.attribute_id.create_variant')
    def _compute_has_variant_attributes(self):
        """Detect if this template has variant-creating attributes (configurable product)."""
        for template in self:
            template.has_variant_attributes = bool(
                template.attribute_line_ids.filtered(
                    lambda line: line.attribute_id.create_variant in ('always', 'dynamic')
                )
            )

    @api.depends('has_variant_attributes', 'magento_bind_ids', 'magento_bind_ids.external_id',
                 'product_variant_ids', 'product_variant_ids.magento_bind_ids',
                 'product_variant_ids.magento_bind_ids.external_id')
    def _compute_magento_sync_info(self):
        """Compute binding count and sync state for smart button display."""
        for template in self:
            # Determine which bindings to use based on product type
            if template.has_variant_attributes:
                # Configurable product: use template bindings
                bindings = template.magento_bind_ids
                # Also get all variant bindings for state calculation
                variant_bindings = template.product_variant_ids.mapped('magento_bind_ids')
            else:
                # Simple product: use first product variant bindings
                bindings = template.product_variant_ids[:1].magento_bind_ids if template.product_variant_ids else self.env['magento.product.product'].browse()
                variant_bindings = self.env['magento.product.product'].browse()

            # Count bindings (only template bindings for configurables, variant for simples)
            template.magento_bindings_count = len(bindings)

            # Calculate sync state based on external_id
            if not bindings:
                template.magento_sync_state = 'none'
            else:
                # Check template bindings state
                template_published = bindings.filtered(lambda b: b.external_id)
                template_unpublished = bindings.filtered(lambda b: not b.external_id)

                # For configurable products, also check variant bindings state
                if template.has_variant_attributes:
                    # Check if all variants have bindings
                    variants_with_bindings = template.product_variant_ids.filtered(lambda v: v.magento_bind_ids)
                    variants_without_bindings = template.product_variant_ids - variants_with_bindings

                    # If there are variants without bindings, it's partial
                    if variants_without_bindings:
                        template.magento_sync_state = 'partial'
                    elif variant_bindings:
                        # All variants have bindings, check their publication state
                        variant_published = variant_bindings.filtered(lambda b: b.external_id)
                        variant_unpublished = variant_bindings.filtered(lambda b: not b.external_id)

                        # Combine states: partial if any combination of published/unpublished exists
                        all_published = template_published and not template_unpublished and variant_published and not variant_unpublished
                        all_unpublished = template_unpublished and not template_published and variant_unpublished and not variant_published

                        if all_published:
                            template.magento_sync_state = 'published'
                        elif all_unpublished:
                            template.magento_sync_state = 'unpublished'
                        else:
                            template.magento_sync_state = 'partial'
                    else:
                        # No variant bindings at all but template has bindings
                        template.magento_sync_state = 'partial'
                else:
                    # Simple product or configurable without variant bindings: only check template/variant bindings
                    if template_published and not template_unpublished:
                        template.magento_sync_state = 'published'
                    elif template_unpublished and not template_published:
                        template.magento_sync_state = 'unpublished'
                    else:
                        template.magento_sync_state = 'partial'

    def _compute_magento_variant_bind_ids(self):
        for rec in self:
            rec.magento_variant_bind_ids = rec.product_variant_ids.mapped('magento_bind_ids')

    def action_view_magento_bindings(self):
        """Smart button action to view, create, or sync Magento bindings.

        Behavior based on sync state:
        - No bindings (none): Open wizard to create binding
        - Unpublished: Execute sync_to_magento() on all bindings
        - Partial: Execute sync_to_magento() only on unpublished bindings (no external_id)
        - Published: Open binding form/tree view
        """
        self.ensure_one()

        # Determine which bindings model to use
        if self.has_variant_attributes:
            # Configurable product: use template bindings
            bindings = self.magento_bind_ids
            res_model = 'magento.product.template'
        else:
            # Simple product: use first product variant bindings
            bindings = self.product_variant_ids[:1].magento_bind_ids if self.product_variant_ids else self.env['magento.product.product'].browse()
            res_model = 'magento.product.product'

        # Determine action based on sync state
        if not bindings:
            # No bindings: open wizard to create
            return self.action_add_magento_backend()

        # Check sync state: unpublished or partial means needs export
        if self.magento_sync_state in ('unpublished', 'partial'):
            # Filter bindings that need sync (those without external_id)
            bindings_to_sync = bindings.filtered(lambda b: not b.external_id)

            if bindings_to_sync:
                # Sync only unpublished bindings
                for binding in bindings_to_sync:
                    binding.sync_to_magento()

                # Return notification action
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Magento Sync',
                        'message': f'Exporting {len(bindings_to_sync)} of {len(bindings)} binding(s) to Magento...',
                        'type': 'info',
                        'sticky': False,
                    }
                }

        # Published state (or partial with all synced): open binding view
        if len(bindings) == 1:
            # Single binding: open form view
            return {
                'type': 'ir.actions.act_window',
                'name': 'Magento Binding',
                'res_model': res_model,
                'res_id': bindings.id,
                'view_mode': 'form',
                'target': 'current',
            }
        else:
            # Multiple bindings: open tree view
            return {
                'type': 'ir.actions.act_window',
                'name': 'Magento Bindings',
                'res_model': res_model,
                'view_mode': 'tree,form',
                'domain': [('id', 'in', bindings.ids)],
                'target': 'current',
            }

    def action_add_magento_backend(self):
        """Open wizard to add a new Magento backend binding."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Add Magento Backend',
            'res_model': 'connector_magento.add_backend.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': 'product.template',
                'active_id': self.id,
                'active_ids': self.ids,
            }
        }

    def action_view_jobs(self):
        self.ensure_one()
        action = self.env.ref('queue_job.action_queue_job').read()[0]
        action.update({
            'domain': [('id', 'in', self.job_ids.ids)],
        })
        return action

    @api.model
    def create(self, vals):
        # Avoid to create variants
        if vals.get('auto_create_variants', True):
            # If auto create is true - then create the normal way
            return super(ProductTemplate, self).create(vals)
        # Else avoid creating the variants
        me = self.with_context(create_product_product=True)

        return super(ProductTemplate, me).create(vals)

    # @api.multi
    def _create_variant_ids(self):
        for rec in self:
            if rec.auto_create_variants:
                super(ProductTemplate, rec)._create_variant_ids()
        return True

    # @api.multi
    def write(self, vals):
        res = False
        for tpl in self:
            if vals.get('auto_create_variants', tpl.auto_create_variants):
                # do auto create variants
                me = tpl
            else:
                # do not auto create variants
                me = tpl.with_context(create_product_product=True)
            res = super(ProductTemplate, me).write(vals)
        return res


class ProductTemplateAdapter(Component):
    _name = 'magento.product.template.adapter'
    _inherit = 'magento.product.adapter'
    _apply_on = 'magento.product.template'

    _magento_model = 'catalog_product'
    _magento2_model = 'products'
    _magento2_search = 'products'
    _magento2_name = 'product'
    _magento2_key = 'sku'
    _admin_path = '/{model}/edit/id/{id}'

    def _get_id_from_create(self, result, data=None):
        return data[self._magento2_key]

    def search(self, filters=None, from_date=None, to_date=None):
        """ Search records according to some criteria
        and returns a list of ids

        :rtype: list
        """
        if filters is None:
            filters = {}
        dt_fmt = MAGENTO_DATETIME_FORMAT
        if from_date is not None:
            filters.setdefault('updated_at', {})
            filters['updated_at']['from'] = from_date.strftime(dt_fmt)
        if to_date is not None:
            filters.setdefault('updated_at', {})
            filters['updated_at']['to'] = to_date.strftime(dt_fmt)
        filters.setdefault('type_id', {})
        filters['type_id']['eq'] = 'configurable'
        if self.work.magento_api._location.version == '2.0':
            return super(ProductTemplateAdapter, self).search(filters=filters)
        # TODO add a search entry point on the Magento API
        raise NotImplementedError

    def list_variants(self, sku):
        if self.work.magento_api._location.version == '2.0':
            res = self._call('configurable-products/%s/children' % (self.escape(sku)), None)
            return res
        raise NotImplementedError
    def write(self, id, data, storeview=None, **kwargs):
        """ Update records on the external system """
        if self.work.magento_api._location.version == '2.0':
            # Replace by the
            id = data['sku']
#            storeview_code = storeview.code if storeview else False
            return super(ProductTemplateAdapter, self)._call(
                'products/%s' % id, {
                    'product': data
                },
                http_method='put', storeview=storeview)
        raise NotImplementedError
