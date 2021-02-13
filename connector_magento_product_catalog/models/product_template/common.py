# -*- coding: utf-8 -*-
# Copyright 2019 Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
from odoo import api, models, fields
from odoo.addons.queue_job.job import job, related_action
from odoo.addons.component.core import Component
from odoo.addons.queue_job.job import identity_exact
import urllib.request, urllib.parse, urllib.error
from odoo.exceptions import MissingError


_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    export_base_image = fields.Boolean('Export Base Image', default=True)

    @api.multi
    def button_resync(self):
        for template in self.sudo():
            for binding in template.magento_template_bind_ids:
                binding.sync_to_magento()


class MagentoProductTemplate(models.Model):
    _inherit = 'magento.product.template'

    special_price_active = fields.Boolean('Special Price', default=False)

    @api.multi
    def sync_to_magento(self):
        for binding in self:
            delayed = binding.with_delay(identity_key=('magento_product_template_%s' % binding.id), priority=10).run_sync_to_magento()
            job = self.env['queue.job'].search([('uuid', '=', delayed.uuid)])
            binding.odoo_id.with_context(connector_no_export=True).job_ids += job

    @api.multi
    @job(default_channel='root.magento.product_to_magento')
    @related_action(action='related_action_unwrap_binding')
    def run_sync_to_magento(self):
        self.ensure_one()
        try:
            with self.backend_id.work_on(self._name) as work:
                exporter = work.component(usage='record.exporter')
                return exporter.run(self)
        except MissingError as e:
            return True

    @api.multi
    def light_sync_to_magento(self):
        for binding in self:
            delayed = binding.with_delay(identity_key=('magento_product_template_%s' % binding.id), priority=10).run_light_sync_to_magento()
            job = self.env['queue.job'].search([('uuid', '=', delayed.uuid)])
            binding.odoo_id.with_context(connector_no_export=True).job_ids += job

    @api.multi
    @job(default_channel='root.magento.product_to_magento')
    @related_action(action='related_action_unwrap_binding')
    def run_light_sync_to_magento(self):
        self.ensure_one()
        try:
            with self.backend_id.work_on(self._name) as work:
                exporter = work.component(usage='record.exporter')
                return exporter.run(self, light_sync=True)
        except MissingError as e:
            return True


    @job(default_channel='root.magento.productexport')
    @related_action(action='related_action_unwrap_binding')
    @api.multi
    def export_product_template_for_storeview(self, fields=None, storeview_id=None):
        """ Export the attributes configuration of a product. """
        self.ensure_one()
        with self.backend_id.work_on(self._name, storeview_id=storeview_id) as work:
            exporter = work.component(usage='record.exporter')
            return exporter.run(self)


class ProductTemplateAdapter(Component):
    _inherit = 'magento.product.template.adapter'
    _magento2_name = 'product'

    def _get_id_from_create(self, result, data=None):
        # Products do use the sku as external_id - but we also need the id - so do return the complete data structure
        return result

    def update_product_links(self, sku, items):
        def escape(term):
            if isinstance(term, str):
                return urllib.parse.quote(term.encode('utf-8'), safe='')
            return term

        if self.work.magento_api._location.version == '2.0':
            res = self._call('products/%s/links' % escape(sku), {
                "items": items
            }, http_method="post")
            _logger.info("Got result for items: %s.", res)
            return res

    def remove_special_price(self, sku):
        def escape(term):
            if isinstance(term, str):
                return urllib.parse.quote(term.encode('utf-8'), safe='')
            return term

        if self.work.magento_api._location.version == '2.0':
            res = self._call('products/special-price-information', {
                "skus": [sku]
            }, http_method="post")
            if res and len(res) > 0:
                _logger.info("Got special prices: %s. Do delete them", res)
                res = self._call('products/special-price-delete', {
                    "prices": res
                }, http_method="post")
            return res
