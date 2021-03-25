# -*- coding: utf-8 -*-
# © 2013 Guewen Baconnier,Camptocamp SA,Akretion
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
import xmlrpc.client

import odoo.addons.decimal_precision as dp

from odoo import models, fields, api, _, registry
from odoo.addons.connector.exception import IDMissingInBackend
from odoo.addons.queue_job.job import job
from odoo.addons.component.core import Component

from ...components.backend_adapter import MAGENTO_DATETIME_FORMAT
from odoo.addons.queue_job.job import identity_exact

_logger = logging.getLogger(__name__)


class MagentoSaleOrder(models.Model):
    _name = 'magento.sale.order'
    _inherit = 'magento.binding'
    _description = 'Magento Sale Order'
    _inherits = {'sale.order': 'odoo_id'}
    _magento_backend_path = 'sales/order/view/order_id'

    magento_order_history_ids = fields.One2many(
        comodel_name='magento.sale.order.historie',
        inverse_name='magento_order_id',
        string="Magento Order Historie",
    )
    odoo_id = fields.Many2one(comodel_name='sale.order',
                              string='Sale Order',
                              required=True,
                              ondelete='cascade')
    magento_order_line_ids = fields.One2many(
        comodel_name='magento.sale.order.line',
        inverse_name='magento_order_id',
        string='Magento Order Lines'
    )
    magento_picking_ids = fields.One2many(
        comodel_name='magento.stock.picking',
        inverse_name='magento_order_id',
        string='Magento Pickings'
    )
    webshop_coupon_code = fields.Char('Webshop Coupon Code')

    total_amount = fields.Float(
        string='Total amount',
        digits=dp.get_precision('Account')
    )
    total_amount_tax = fields.Float(
        string='Total amount w. tax',
        digits=dp.get_precision('Account')
    )
    magento_order_id = fields.Integer(string='Magento Order ID',
                                      help="'order_id' field in Magento")
    # when a sale order is modified, Magento creates a new one, cancels
    # the parent order and link the new one to the canceled parent
    magento_parent_id = fields.Many2one(comodel_name='magento.sale.order',
                                        string='Parent Magento Order')
    storeview_id = fields.Many2one(comodel_name='magento.storeview',
                                   string='Magento Storeview')
    store_id = fields.Many2one(related='storeview_id.store_id',
                               string='Storeview',
                               readonly=True)

    @job(default_channel='root.magento')
    @api.multi
    def export_state_change(self, allowed_states=None,
                            comment=None, notify=None):
        """ Change state of a sales order on Magento """
        self.ensure_one()
        with self.backend_id.work_on(self._name) as work:
            exporter = work.component(usage='sale.state.exporter')
            return exporter.run(self, allowed_states=allowed_states,
                                comment=comment, notify=notify)

    @job(default_channel='root.magento')
    @api.model
    def import_batch(self, backend, filters=None):
        """ Prepare the import of Sales Orders from Magento """
        assert 'magento_storeview_id' in filters, ('Missing information about '
                                                   'Magento Storeview')
        _super = super(MagentoSaleOrder, self)
        return _super.import_batch(backend, filters=filters)


class MagentoSaleOrderHistorie(models.Model):
    _name = 'magento.sale.order.historie'
    _inherit = 'magento.binding'
    _description = 'Magento Sale Order Historie'
    _inherits = {'mail.message': 'odoo_id'}


    magento_order_id = fields.Many2one(comodel_name='magento.sale.order',
                                       string='Magento Sale Order',
                                       required=True,
                                       ondelete='cascade',
                                       index=True)
    odoo_id = fields.Many2one(comodel_name='mail.message',
                              string='Message',
                              required=True,
                              ondelete='cascade')
    backend_id = fields.Many2one(
        related='magento_order_id.backend_id',
        string='Magento Backend',
        readonly=True,
        store=True,
        # override 'magento.binding', can't be INSERTed if True:
        required=False,
    )
    entity_name = fields.Char('Name')
    is_customer_notified = fields.Boolean('Customer Notified')
    status = fields.Char('Status')


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    magento_bind_ids = fields.One2many(
        comodel_name='magento.sale.order',
        inverse_name='odoo_id',
        string="Magento Bindings",
    )
    magento_order_history_ids = fields.One2many(
        comodel_name='magento.sale.order.historie',
        inverse_name='odoo_id',
        string="Magento Order Historie",
    )

    @api.depends('magento_bind_ids', 'magento_bind_ids.magento_parent_id')
    def get_parent_id(self):
        """ Return the parent order.

        For Magento sales orders, the magento parent order is stored
        in the binding, get it from there.
        """
        super(SaleOrder, self).get_parent_id()
        for order in self:
            if not order.magento_bind_ids:
                continue
            # assume we only have 1 SO in Odoo for 1 SO in Magento
            assert len(order.magento_bind_ids) == 1
            magento_order = order.magento_bind_ids[0]
            if magento_order.magento_parent_id:
                self.parent_id = magento_order.magento_parent_id.odoo_id

    def _magento_cancel(self):
        """ Cancel sales order on Magento

        Do not export the other state changes, Magento handles them itself
        when it receives shipments and invoices.
        """
        for order in self:
            old_state = order.state
            if old_state == 'cancel':
                continue  # skip if already canceled
            for binding in order.magento_bind_ids:
                #if self.collection.version == '2.0':
                    #continue # TODO
                job_descr = _("Cancel sales order %s") % (binding.external_id,)
                binding.with_delay(
                    description=job_descr,
                    identity_key=identity_exact
                ).export_state_change(allowed_states=['cancel'])

    @api.multi
    def write(self, vals):
        if vals.get('state') == 'cancel':
            self._magento_cancel()
        return super(SaleOrder, self).write(vals)

    def _magento_link_binding_of_copy(self, new):
        # link binding of the canceled order to the new order, so the
        # operations done on the new order will be sync'ed with Magento
        if self.state != 'cancel':
            return
        binding_model = self.env['magento.sale.order']
        bindings = binding_model.search([('odoo_id', '=', self.id)])
        bindings.write({'odoo_id': new.id})

        for binding in bindings:
            # the sales' status on Magento is likely 'canceled'
            # so we will export the new status (pending, processing, ...)
            if self.collection.version == '2.0':
                continue # TODO
            job_descr = _("Reopen sales order %s") % (binding.external_id,)
            binding.with_delay(
                description=job_descr,
                identity_key=identity_exact
            ).export_state_change()

    @api.multi
    def copy(self, default=None):
        self_copy = self.with_context(__copy_from_quotation=True)
        new = super(SaleOrder, self_copy).copy(default=default)
        self_copy._magento_link_binding_of_copy(new)
        return new


class MagentoSaleOrderLine(models.Model):
    _name = 'magento.sale.order.line'
    _inherit = 'magento.binding'
    _description = 'Magento Sale Order Line'
    _inherits = {'sale.order.line': 'odoo_id'}

    magento_order_id = fields.Many2one(comodel_name='magento.sale.order',
                                       string='Magento Sale Order',
                                       required=True,
                                       ondelete='cascade',
                                       index=True)
    odoo_id = fields.Many2one(comodel_name='sale.order.line',
                              string='Sale Order Line',
                              required=True,
                              ondelete='cascade')
    backend_id = fields.Many2one(
        related='magento_order_id.backend_id',
        string='Magento Backend',
        readonly=True,
        store=True,
        # override 'magento.binding', can't be INSERTed if True:
        required=False,
    )
    parent_item_id = fields.Char('Parent Item ID')
    shipping_item_id = fields.Integer('Shipping Item ID')
    tax_rate = fields.Float(string='Tax Rate',
                            digits=dp.get_precision('Account'))
    notes = fields.Char()

    @api.model
    def create(self, vals):
        magento_order_id = vals['magento_order_id']
        binding = self.env['magento.sale.order'].browse(magento_order_id)
        vals['order_id'] = binding.odoo_id.id
        binding = super(MagentoSaleOrderLine, self).create(vals)
        return binding


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    magento_bind_ids = fields.One2many(
        comodel_name='magento.sale.order.line',
        inverse_name='odoo_id',
        string="Magento Bindings",
    )
    is_bundle_item = fields.Boolean('Bundle item', default=False)

    def _get_to_invoice_qty(self):
        super(SaleOrderLine, self)._get_to_invoice_qty()
        for line in self:
            if line.is_bundle_item:
                line.qty_to_invoice = 0

    @api.model
    def create(self, vals):
        old_line_id = None
        if self.env.context.get('__copy_from_quotation'):
            # when we are copying a sale.order from a canceled one,
            # the id of the copied line is inserted in the vals
            # in `copy_data`.
            old_line_id = vals.pop('__copy_from_line_id', None)
        new_line = super(SaleOrderLine, self).create(vals)
        if old_line_id:
            # link binding of the canceled order lines to the new order
            # lines, happens when we are using the 'New Copy of
            # Quotation' button on a canceled sales order
            binding_model = self.env['magento.sale.order.line']
            bindings = binding_model.search([('odoo_id', '=', old_line_id)])
            if bindings:
                bindings.write({'odoo_id': new_line.id})
        return new_line

    @api.multi
    def copy_data(self, default=None):
        data = super(SaleOrderLine, self).copy_data(default=default)[0]
        if self.env.context.get('__copy_from_quotation'):
            # copy_data is called by `copy` of the sale.order which
            # builds a dict for the full new sale order, so we lose the
            # association between the old and the new line.
            # Keep a trace of the old id in the vals that will be passed
            # to `create`, from there, we'll be able to update the
            # Magento bindings, modifying the relation from the old to
            # the new line.
            data['__copy_from_line_id'] = self.id
        return [data]


    @api.multi
    def _compute_qty_delivered(self):
        lines_without_bundles = self.filtered(lambda l: l.product_id.type != 'bundle')
        super(SaleOrderLine, lines_without_bundles)._compute_qty_delivered()
        for line in lines_without_bundles:
            if line.is_bundle_item and line.magento_bind_ids:
                # Check if all bundle items are delivered - so set bundle item to be delivered
                bundle_line = line.order_id.order_line.filtered(lambda l: l.magento_bind_ids and l.magento_bind_ids[0].external_id == line.magento_bind_ids[0].parent_item_id)
                if not bundle_line:
                    _logger.info("Line %s is bundle item, but no bundle parent found", line.product_id.name)
                    continue
                bdelivered_qty = 0
                bordered_qty = 0
                for bline in line.order_id.order_line.filtered(lambda l: l.magento_bind_ids and l.magento_bind_ids[0].parent_item_id == bundle_line.magento_bind_ids[0].external_id):
                    bdelivered_qty += bline.qty_delivered
                    bordered_qty += bline.product_uom_qty
                pieces_per_bundle = bordered_qty / bundle_line.product_uom_qty
                bundle_line.write({
                    'qty_delivered_manual': bdelivered_qty / pieces_per_bundle,
                    'qty_delivered_method': 'manual',
                })
        for line in self.filtered(lambda l: l.product_id.type == 'bundle'):
            line.qty_delivered = line.qty_delivered_manual


class SaleOrderAdapter(Component):
    _name = 'magento.sale.order.adapter'
    _inherit = 'magento.adapter'
    _apply_on = 'magento.sale.order'

    _magento_model = 'sales_order'
    _magento2_model = 'orders'
    _magento2_search = 'orders'
    _magento2_key = 'entity_id'
    _admin_path = '{model}/view/order_id/{id}'

    def _call(self, method, arguments, http_method=None, storeview=None):
        try:
            return super(SaleOrderAdapter, self)._call(method, arguments, http_method=http_method, storeview=storeview)
        except xmlrpc.client.Fault as err:
            # this is the error in the Magento API
            # when the sales order does not exist
            if err.faultCode == 100:
                raise IDMissingInBackend
            else:
                raise
            
    def get_search_arguments(self, filters):
        if self.collection.version == '2.0':
            return filters
        return {
            'imported': False,
            # 'limit': 200,
            'filters': filters,
        }

    def search(self, filters=None, from_date=None, to_date=None,
               magento_storeview_ids=None):
        """ Search records according to some criteria
        and returns a list of ids

        :rtype: list
        """
        if filters is None:
            filters = {}
        dt_fmt = MAGENTO_DATETIME_FORMAT
        if from_date is not None:
            filters.setdefault('created_at', {})
            filters['created_at']['from'] = from_date.strftime(dt_fmt)
        if to_date is not None:
            filters.setdefault('created_at', {})
            filters['created_at']['to'] = to_date.strftime(dt_fmt)
        if magento_storeview_ids is not None:
            filters['store_id'] = {'in': magento_storeview_ids}

        arguments = self.get_search_arguments(filters)
        return super(SaleOrderAdapter, self).search(arguments)

    def read(self, id, attributes=None, binding=None):
        """ Returns the information of a record

        :rtype: dict
        """
        if self.collection.version == '2.0':
            res = super(SaleOrderAdapter, self).read(
                id, attributes=attributes)
            return res
        record = self._call('%s.info' % self._magento_model,
                            [id, attributes])
        return record

    def get_parent(self, id, magento_storeview_ids=None):
        if self.collection.version == '2.0':
            filters = {}
            filters['entity_id'] = {'eq': id}
            result = self.search_read(filters=filters)
            
            if result.get("items"):
                _logger.info(" %r" % result.get("items")[0].get("relation_parent_id") )
                return result.get("items")[0].get("relation_parent_id")
            return 0
        return self._call('%s.get_parent' % self._magento_model, [id])

    def add_comment(self, id, status, comment=None, notify=False):
        if self.collection.version == '2.0':
            customer_not = 0
            if notify:
                customer_not = 1
            return self._call('orders/%s/comments' % id, { "statusHistory": { "comment": comment, "isCustomerNotified": customer_not, "status": status }}, http_method='post')
        return self._call('%s.addComment' % self._magento_model,
                          [id, status, comment, notify])
