# -*- coding: utf-8 -*-
# Copyright 2013-2017 Camptocamp SA
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
import xmlrpc.client
from odoo import models, fields, api
from odoo.addons.queue_job.job import job
from odoo.addons.component.core import Component

from odoo.addons.connector.exception import IDMissingInBackend
from ...components.backend_adapter import MAGENTO_DATETIME_FORMAT
from odoo.addons.queue_job.job import identity_exact


class ResPartner(models.Model):
    _inherit = 'res.partner'

    magento_bind_ids = fields.One2many(
        comodel_name='magento.res.partner',
        inverse_name='odoo_id',
        string="Magento Bindings",
    )
    magento_address_bind_ids = fields.One2many(
        comodel_name='magento.address',
        inverse_name='odoo_id',
        string="Magento Address Bindings",
    )
    birthday = fields.Date(string='Birthday')
    company = fields.Char(string='Company')

    @api.model
    def _address_fields(self):
        """ Returns the list of address fields that are synced from the
        parent.

        """
        fields = super(ResPartner, self)._address_fields()
        fields.append('company')
        return fields

    @job(default_channel='root.magento')
    @api.model
    def import_batch(self, backend, filters=None):
        assert 'magento_website_id' in filters, (
            'Missing information about Magento Website')
        return super(ResPartner, self).import_batch(backend, filters=filters)


class MagentoResPartner(models.Model):
    _name = 'magento.res.partner'
    _inherit = 'magento.binding'
    _inherits = {'res.partner': 'odoo_id'}
    _description = 'Magento Partner'
    _magento_backend_path = None
    _magento_frontend_path = None

    _rec_name = 'name'

    @api.depends('backend_id', 'external_id')
    def _compute_magento_backend_url(self):
        for binding in self:
            binding.magento_backend_url = None
            binding.magento_frontend_url = None

    odoo_id = fields.Many2one(comodel_name='res.partner',
                              string='Partner',
                              required=True,
                              ondelete='cascade')
    backend_id = fields.Many2one(
        related='website_id.backend_id',
        comodel_name='magento.backend',
        string='Magento Backend',
        store=True,
        readonly=True,
        # override 'magento.binding', can't be INSERTed if True:
        required=False,
    )
    website_id = fields.Many2one(comodel_name='magento.website',
                                 string='Magento Website',
                                 required=True,
                                 ondelete='restrict')
    group_id = fields.Many2one(comodel_name='magento.res.partner.category',
                               string='Magento Group (Category)')
    created_at = fields.Datetime(string='Created At (on Magento)',
                                 readonly=True)
    updated_at = fields.Datetime(string='Updated At (on Magento)',
                                 readonly=True)
    emailid = fields.Char(string='E-mail address')
    # Replaced by the VAT field on the company
    #taxvat = fields.Char(string='Magento VAT')
    newsletter = fields.Boolean(string='Newsletter')
    guest_customer = fields.Boolean(string='Guest Customer')
    consider_as_company = fields.Boolean(
        string='Considered as company',
        help="An account imported with a 'company' in "
             "the billing address is considered as a company.\n "
             "The partner takes the name of the company and "
             "is not merged with the billing address.",
    )

    @api.multi
    @job(default_channel='root.magento')
    def sync_from_magento(self):
        for binding in self:
            binding.with_delay(identity_key=identity_exact).run_sync_from_magento()

    @api.multi
    @job(default_channel='root.magento')
    def run_sync_from_magento(self):
        self.ensure_one()
        with self.backend_id.work_on(self._name) as work:
            importer = work.component(usage='record.importer')
            return importer.run(self.external_id, force=True)


class MagentoAddress(models.Model):
    _name = 'magento.address'
    _inherit = 'magento.binding'
    _inherits = {'res.partner': 'odoo_id'}
    _description = 'Magento Address'

    _rec_name = 'backend_id'

    odoo_id = fields.Many2one(comodel_name='res.partner',
                              string='Partner',
                              required=True,
                              ondelete='cascade')
    created_at = fields.Datetime(string='Created At (on Magento)',
                                 readonly=True)
    updated_at = fields.Datetime(string='Updated At (on Magento)',
                                 readonly=True)
    is_default_billing = fields.Boolean(string='Default Invoice')
    is_default_shipping = fields.Boolean(string='Default Shipping')
    magento_partner_id = fields.Many2one(comodel_name='magento.res.partner',
                                         string='Magento Partner',
                                         required=True,
                                         ondelete='cascade')
    backend_id = fields.Many2one(
        related='magento_partner_id.backend_id',
        comodel_name='magento.backend',
        string='Magento Backend',
        store=True,
        readonly=True,
        # override 'magento.binding', can't be INSERTed if True:
        required=False,
    )
    website_id = fields.Many2one(
        related='magento_partner_id.website_id',
        comodel_name='magento.website',
        string='Magento Website',
        store=True,
        readonly=True,
    )
    is_magento_order_address = fields.Boolean(
        string='Address from a Magento Order',
    )

    _sql_constraints = [
        ('odoo_uniq', 'Check(1=1)',
         'Dummy to get rid of the old constraint.'),
    ]


class PartnerAdapter(Component):
    _name = 'magento.partner.adapter'
    _inherit = 'magento.adapter'
    _apply_on = 'magento.res.partner'

    _magento_model = 'customer'
    _magento2_model = 'customers'
    _magento2_search = 'customers/search'
    _magento2_key = 'id'
    _admin_path = '/{model}/edit/id/{id}'


#     def _call(self, method, arguments, http_method=None, storeview=None):
#         try:
#             return super(PartnerAdapter, self)._call(method, arguments, http_method=http_method, storeview=storeview)
#         except xmlrpclib.Fault as err:
#             # this is the error in the Magento API
#             # when the Partner does not exist
#             if err.faultCode == 102:
#                 raise IDMissingInBackend
#             else:
#                 raise


#     def _call(self, method, arguments):
#         try:
#             return super(PartnerAdapter, self)._call(method, arguments)
#         except xmlrpclib.Fault as err:
#             # this is the error in the Magento API
#             # when the customer does not exist
#             if err.faultCode == 102:
#                 raise IDMissingInBackend
#             else:
#                 raise

    def search(self, filters=None, from_date=None, to_date=None,
               magento_website_ids=None):
        """ Search records according to some criteria and return a
        list of ids

        :rtype: list
        """
        if filters is None:
            filters = {}

        dt_fmt = MAGENTO_DATETIME_FORMAT
        if from_date is not None:
            # updated_at include the created records
            filters.setdefault('updated_at', {})
            filters['updated_at']['from'] = from_date.strftime(dt_fmt)
        if to_date is not None:
            filters.setdefault('updated_at', {})
            filters['updated_at']['to'] = to_date.strftime(dt_fmt)
        if magento_website_ids is not None:
            filters['website_id'] = {'in': magento_website_ids}
            
        if self.work.magento_api._location.version == '2.0':
            return super(PartnerAdapter, self).search(filters=filters)

        # the search method is on ol_customer instead of customer
        return self._call('ol_customer.search',
                          [filters] if filters else [{}])


class AddressAdapter(Component):
    _name = 'magento.address.adapter'
    _inherit = 'magento.adapter'
    _apply_on = 'magento.address'

    _magento_model = 'customer_address'

    def search(self, filters=None):
        """ Search records according to some criterias
        and returns a list of ids

        :rtype: list
        """
        return [int(row['customer_address_id']) for row
                in self._call('%s.list' % self._magento_model,
                              [filters] if filters else [{}])]

    def create(self, customer_id, data):
        """ Create a record on the external system """
        return self._call('%s.create' % self._magento_model,
                          [customer_id, data])
