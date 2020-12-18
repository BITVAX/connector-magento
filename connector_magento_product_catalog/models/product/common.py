# -*- coding: utf-8 -*-
# Copyright 2019 Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
from odoo import api, models, fields
from odoo.addons.component.core import Component
from odoo.addons.queue_job.job import job, related_action
from odoo.addons.queue_job.job import identity_exact
import urllib.request, urllib.parse, urllib.error
from urllib.parse import urljoin
from odoo.exceptions import MissingError

_logger = logging.getLogger(__name__)


class MagentoProductProduct(models.Model):
    _inherit = 'magento.product.product'
    
    @api.depends('backend_id', 'external_id')
    def _compute_magento_backend_url(self):
        for binding in self:
            if binding._magento_backend_path:
                binding.magento_backend_url = "%s/%s" % (urljoin(binding.backend_id.admin_location, binding._magento_backend_path), binding.external_id)
            if binding._magento_frontend_path:
                binding.magento_frontend_url = "%s.html" % urljoin(binding.backend_id.location, binding.magento_url_key)

    attribute_set_id = fields.Many2one('magento.product.attributes.set',
                                       string='Attribute set')
    special_price_active = fields.Boolean('Special Price', default=False)
    visibility = fields.Selection([
        ('1', 'Einzel nicht sichtbar'),
        ('4', 'Katalog, Suche'),
    ], string="Visibility", default='4')
    
    @api.multi
    def sync_to_magento(self):
        for binding in self:
            binding.with_delay(identity_key=('magento_product_product_%s' % binding.id), priority=10).run_sync_to_magento()

    @api.multi
    @related_action(action='related_action_unwrap_binding')
    @job(default_channel='root.magento.product_to_magento')
    def run_sync_to_magento(self):
        self.ensure_one()
        try:
            with self.backend_id.work_on(self._name) as work:
                exporter = work.component(usage='record.exporter')
                return exporter.run(self)
        except MissingError as e:
            return True


class ProductProductAdapter(Component):
    _inherit = 'magento.product.product.adapter'
    _magento2_name = 'product'

    def _get_id_from_create(self, result, data=None):
        # Products do use the sku as external_id - but we also need the id - so do return the complete data structure
        return result

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

    def get_media(self, sku):
        if self.work.magento_api._location.version == '2.0':
            return self._call('products/%s/media' % sku, http_method="get")
