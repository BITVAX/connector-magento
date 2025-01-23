# -*- coding: utf-8 -*-
# Copyright 2019 Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
from odoo import api, models, fields
from odoo.addons.component.core import Component
# from odoo.addons.queue_job.job import job, related_action
from ...components.backend_adapter import MAGENTO_DATETIME_FORMAT
import urllib.request, urllib.parse, urllib.error
import odoo.addons.decimal_precision as dp
from urllib.parse import urljoin
from odoo.addons.queue_job.job import identity_exact
from odoo.models import NewId


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
            ('simple', 'Simple Product'),
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
                              ondelete='restrict')
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
    magento_product_ids = fields.One2many(comodel_name='magento.product.product',
                                           related='odoo_id.product_variant_ids.magento_bind_ids',
                                           string='Variants',
                                           readonly=True)

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
    magento_url_key = fields.Char(string="URL Key")
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


class ProductTemplate(models.Model):
    _inherit = 'product.template'

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
        related='product_variant_ids.magento_bind_ids',
        string='Magento Variant Bindings',
    )
    auto_create_variants = fields.Boolean('Auto Create Variants', default=True)
    magento_default_code = fields.Char(string="Default code used for magento")
    job_ids = fields.Many2many('queue.job', string="Jobs")
    open_job_count = fields.Integer(string='Open Jobs', compute='_compute_job_counts', store=False)
    failed_job_count = fields.Integer(string='Failed Jobs', compute='_compute_job_counts', store=False)
    magento_internal_id = fields.Char(string="Magento Internal ID")
    magento_url_key = fields.Char(string="URL Key")
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
    _inherit = 'magento.adapter'
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
        return [int(row['product_id']) for row
                in self._call('%s.list' % self._magento_model,
                              [filters] if filters else [{}])]

    def list_variants(self, sku):
        if self.work.magento_api._location.version == '2.0':
            res = self._call('configurable-products/%s/children' % (self.escape(sku)), None)
            return res

    def write(self, id, data, binding=None):
        """ Update records on the external system """
        storeview_id = self.work.storeview_id if hasattr(self.work, 'storeview_id') else False
        if self.work.magento_api._location.version == '2.0':
            # Replace by the
            id = data['sku']
            storeview_code = storeview_id.code if storeview_id else False
            return super(ProductTemplateAdapter, self)._call(
                'products/%s' % id, {
                    'product': data
                },
                http_method='put', storeview=storeview_code)
        return self._call('ol_catalog_product.update',
                          [int(id), data, storeview_id, 'id'])

    # def get_images(self, id, storeview_id=None, data=None):
    #     if self.work.magento_api._location.version == '2.0':
    #         assert data
    #         return (entry for entry in
    #                 data.get('media_gallery_entries', [])
    #                 if entry['media_type'] == 'image')
    #     else:
    #         return self._call('product_media.list', [int(id), storeview_id, 'id'])
    #
    # def read_image(self, id, image_name, storeview_id=None):
    #     if self.work.magento_api._location.version == '2.0':
    #         raise NotImplementedError  # TODO
    #     return self._call('product_media.info',
    #                       [int(id), image_name, storeview_id, 'id'])
    def read(self, external_id, attributes=None,storeview=None, **kwargs):
        """ Returns the information of a record

        :rtype: dict
        """
        # pylint: disable=method-required-super
        if self.collection.version == '1.7':
            return self._call(
                'ol_catalog_product.info',
                [int(external_id), storeview, attributes, 'id'])
        res = super(ProductTemplateAdapter, self).read(
            external_id, attributes=attributes, storeview=storeview)
        if res:
            for attr in res.get('custom_attributes', []):
                res[attr['attribute_code']] = attr['value']
        return res
