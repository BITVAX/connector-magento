# Copyright 2013-2019 Camptocamp SA
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
import xmlrpc.client

from collections import defaultdict

from odoo import models, fields, api
from odoo.addons.connector.exception import IDMissingInBackend
from odoo.addons.component.core import Component
from odoo.addons.component_event import skip_if
# # from odoo.addons.queue_job.job import job3, related_action
from odoo.exceptions import UserError, MissingError
from odoo.tools.translate import _
from ...components.backend_adapter import MAGENTO_DATETIME_FORMAT

_logger = logging.getLogger(__name__)


def chunks(items, length):
    for index in range(0, len(items), length):
        yield items[index:index + length]


class MagentoProductProduct(models.Model):
    _name = 'magento.product.product'
    _inherit = 'magento.binding'
    _inherits = {'product.product': 'odoo_id'}
    _description = 'Magento Product'

    @api.model
    def product_type_get(self):
        return [
            ('simple', 'Simple Product'),
            ('configurable', 'Configurable Product'),
            ('virtual', 'Virtual Product'),
            ('downloadable', 'Downloadable Product'),
            ('giftcard', 'Giftcard'),
            # XXX activate when supported
            ('grouped', 'Grouped Product'),
            # ('bundle', 'Bundle Product'),
        ]

    odoo_id = fields.Many2one(comodel_name='product.product',
                              string='Odoo Product',
                              required=True,
                              ondelete='cascade')
    magento_internal_id = fields.Char(
        help=(
            'In Magento2, we have to keep track of both the external_id (the '
            'product SKU) which is used in the Magento2 REST API, as well as '
            'the Magento internal id as used in the admin URL.'))
    # XXX website_ids can be computed from categories
    # website_ids = fields.Many2many(comodel_name='magento.website',
    #                                string='Websites',
    #                                )
    created_at = fields.Datetime('Created At (on Magento)')
    updated_at = fields.Datetime('Updated At (on Magento)')
    product_type = fields.Selection(selection='product_type_get',
                                    string='Magento Product Type',
                                    default='simple',
                                    required=True)
    manage_stock = fields.Selection(
        selection=[('use_default', 'Use Default Config'),
                   ('no', 'Do Not Manage Stock'),
                   ('yes', 'Manage Stock')],
        string='Manage Stock Level',
        default='use_default',
        required=True,
    )
    backorders = fields.Selection(
        selection=[('use_default', 'Use Default Config'),
                   ('no', 'No Sell'),
                   ('yes', 'Sell Quantity < 0'),
                   ('yes-and-notification', 'Sell Quantity < 0 and '
                                            'Use Customer Notification')],
        string='Manage Inventory Backorders',
        default='use_default',
        required=True,
    )
    magento_qty = fields.Float(string='Computed Quantity',
                               help="Last computed quantity to send "
                                    "on Magento.")
    no_stock_sync = fields.Boolean(
        string='No Stock Synchronization',
        required=False,
        help="Check this to exclude the product "
             "from stock synchronizations.",
    )

    magento_url_key = fields.Char(
        string="URL Key",
        store=True,
        readonly=False
    )
    attribute_set_id = fields.Many2one(
        comodel_name='magento.product.attribute.set',
        string='Attribute Set',
        required=True,
        default=lambda self: self.env['magento.product.attribute.set'].search([])[0] if self.env['magento.product.attribute.set'].search([]) else False,
    )
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
    RECOMPUTE_QTY_STEP = 1000  # products at a time

    product_links = fields.Many2many(
        comodel_name='magento.product.product',
        relation='magento_product_product_link',
        column1='product_id',
        column2='linked_product_id',
        string='Related Products',
    )


     # @api.multi
    # @related_action(action='related_action_unwrap_binding')
    # @job(default_channel='root.magento.product_to_magento')
    # def run_sync_to_magento(self):
    #     self.ensure_one()
    #     try:
    #         with self.backend_id.work_on(self._name) as work:
    #             exporter = work.component(usage='record.exporter')
    #             return exporter.run(self)
    #     except MissingError as e:
    #         return True

    # @job(default_channel='root.magento')
    # @related_action(action='related_action_unwrap_binding')
    # @api.multi
    def export_inventory(self, fields=None):
        """ Export the inventory configuration and quantity of a product. """
        self.ensure_one()
        with self.backend_id.work_on(self._name) as work:
            exporter = work.component(usage='product.inventory.exporter')
            return exporter.run(self, fields)

    def export_record_with_dependencies(self):
        """Export product with dependency graph using on_done().

        This method creates a hierarchical job graph with explicit dependencies:
        1. Categories/attributes/values are exported FIRST (dependencies)
        2. Product waits for ALL dependencies via on_done()

        Job execution flow:
        [Categories, Attrs, Values] → Product
              (parallel)               ↓

        Each job has its own transaction. If a job fails, only that
        transaction rolls back. Dependent jobs wait in 'wait_dependencies'
        state until their parent succeeds.

        This is used for simple products (product.product) without variants.

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
                _logger.info("Added category job for product: %s", extra_category.name)

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
                    _logger.info("Added attribute job for product: %s", att_line.attribute_id.name)

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
                            _logger.info("Added value job for product: %s", value.name)

        # 3. Create product export job
        product_job = self.delayable(priority=10).export_record()
        _logger.info("Created product job for: %s", self.name)

        # 4. Chain all dependencies to product: dependencies → product
        # All dependency jobs must complete before product starts
        if dependency_jobs:
            for dep_job in dependency_jobs:
                dep_job.on_done(product_job)
            _logger.info("Chained %d dependency jobs to product", len(dependency_jobs))

        _logger.info("Created job graph: %d deps → 1 product",
                    len(dependency_jobs))

        # 5. Delay ONLY the first job in the graph to start execution
        # The rest will be triggered automatically via on_done() chains
        if dependency_jobs:
            root_job = dependency_jobs[0]
        else:
            root_job = product_job

        delayed = root_job.delay()
        _logger.info("Started product job graph execution from root job")

        # Track the root job
        job = self.env['queue.job'].search([('uuid', '=', delayed.uuid)])
        self.odoo_id.with_context(connector_no_export=True).job_ids += job

        return delayed

    # @api.multi
    def recompute_magento_qty(self):
        """ Check if the quantity in the stock location configured
        on the backend has changed since the last export.

        If it has changed, write the updated quantity on `magento_qty`.
        The write on `magento_qty` will trigger an `on_record_write`
        event that will create an export job.

        It groups the products by backend to avoid to read the backend
        informations for each product.
        """
        # group products by backend
        backends = defaultdict(set)
        for product in self:
            backends[product.backend_id].add(product.id)

        for backend, product_ids in list(backends.items()):
            self._recompute_magento_qty_backend(backend,
                                                self.browse(product_ids))
        return True

    # @api.multi
    def _recompute_magento_qty_backend(self, backend, products,
                                       read_fields=None):
        """ Recompute the products quantity for one backend.

        If field names are passed in ``read_fields`` (as a list), they
        will be read in the product that is used in
        :meth:`~._magento_qty`.

        """
        if backend.product_stock_field_id:
            stock_field = backend.product_stock_field_id.name
        else:
            stock_field = 'virtual_available'

        location = self.env['stock.location']
        if self.env.context.get('location'):
            location = location.browse(self.env.context['location'])
        else:
            location = backend.warehouse_id.lot_stock_id

        product_fields = ['magento_qty', stock_field]
        if read_fields:
            product_fields += read_fields

        self_with_location = self.with_context(location=location.id)
        for chunk_ids in chunks(products.ids, self.RECOMPUTE_QTY_STEP):
            records = self_with_location.browse(chunk_ids)
            for product in records.read(fields=product_fields):
                new_qty = self._magento_qty(product,
                                            backend,
                                            location,
                                            stock_field)
                if new_qty != product['magento_qty']:
                    self.browse(product['id']).magento_qty = new_qty

    # @api.multi
    def _magento_qty(self, product, backend, location, stock_field):
        """ Return the current quantity for one product.

        Can be inherited to change the way the quantity is computed,
        according to a backend / location.

        If you need to read additional fields on the product, see the
        ``read_fields`` argument of :meth:`~._recompute_magento_qty_backend`

        """
        return product[stock_field]

    @api.model
    def _get_admin_path(self, backend, external_id):
        """ In Magento2, we can only link to the product when we have already
        imported it """
        if backend.version == '1.7':
            return '/{model}/edit/id/{id}'
        magento_internal_id = self.search(
            [('backend_id', '=', backend.id),
             ('external_id', '=', external_id)],
            limit=1).magento_internal_id
        if magento_internal_id:
            return 'catalog/product/edit/id/%s' % magento_internal_id
        raise UserError(_(
            'We have to import the product before we can provide the admin '
            'link to it.'))

class ProductProduct(models.Model):
    _inherit = 'product.product'

    magento_bind_ids = fields.One2many(
        comodel_name='magento.product.product',
        inverse_name='odoo_id',
        string='Magento Bindings',
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

    @api.depends('magento_bind_ids', 'magento_bind_ids.external_id')
    def _compute_magento_sync_info(self):
        """Compute binding count and sync state for smart button display."""
        for product in self:
            bindings = product.magento_bind_ids

            # Count bindings
            product.magento_bindings_count = len(bindings)

            # Calculate sync state based on external_id
            if not bindings:
                product.magento_sync_state = 'none'
            else:
                published = bindings.filtered(lambda b: b.external_id)
                unpublished = bindings.filtered(lambda b: not b.external_id)

                if published and not unpublished:
                    product.magento_sync_state = 'published'
                elif unpublished and not published:
                    product.magento_sync_state = 'unpublished'
                else:
                    product.magento_sync_state = 'partial'

    def action_view_magento_bindings(self):
        """Smart button action to view, create, or sync Magento bindings.

        Behavior based on sync state:
        - No bindings (none): Open wizard to create binding
        - Unpublished: Execute sync_to_magento() on all bindings
        - Partial: Execute sync_to_magento() only on unpublished bindings (no external_id)
        - Published: Open binding form/tree view
        """
        self.ensure_one()
        bindings = self.magento_bind_ids

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
                'res_model': 'magento.product.product',
                'res_id': bindings.id,
                'view_mode': 'form',
                'target': 'current',
            }
        else:
            # Multiple bindings: open tree view
            return {
                'type': 'ir.actions.act_window',
                'name': 'Magento Bindings',
                'res_model': 'magento.product.product',
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
                'active_model': 'product.product',
                'active_id': self.id,
                'active_ids': self.ids,
            }
        }

    #
    # variant_url_key = fields.Char(
    #     compute='_compute_variant_url_key',
    #     store=True,
    #     string="Variant URL Key",
    #     help="SEO-friendly URL key for product variants. Auto-generated from template + variant attributes."
    # )
    #
    # @api.depends('product_tmpl_id.url_key', 'product_template_attribute_value_ids')
    # def _compute_variant_url_key(self):
    #     for product in self:
    #         if product.product_tmpl_id.url_key:
    #             base_url = product.product_tmpl_id.url_key
    #
    #             # Extract variant attributes that create variants (dynamic)
    #             variant_parts = []
    #             for ptav in product.product_template_attribute_value_ids:
    #                 if ptav.attribute_id.create_variant == 'always':
    #                     variant_parts.append(ptav.name.lower().replace(' ', '-'))
    #
    #             if variant_parts:
    #                 # Has real variant attributes: add them to URL
    #                 product.variant_url_key = f"{base_url}-{'-'.join(variant_parts)}"
    #             else:
    #                 # Simple product without variants: use template URL as-is
    #                 product.variant_url_key = base_url
    #         else:
    #             product.variant_url_key = False

class ProductProductAdapter(Component):
    _name = 'magento.product.product.adapter'
    _inherit = 'magento.product.adapter'
    _apply_on = 'magento.product.product'

    _magento_model = 'catalog_product'
    _magento2_model = 'products'
    _magento2_search = 'products'
    _magento2_key = 'sku'
    _admin_path = '/{model}/edit/id/{id}'
    _magento2_name = 'product'


    def _call(self, method, arguments=None, http_method=None, storeview=None):
        try:
            return super(ProductProductAdapter, self)._call(
                method, arguments, http_method=http_method,
                storeview=storeview)
        except xmlrpc.client.Fault as err:
            # this is the error in the Magento API
            # when the product does not exist
            if err.faultCode == 101:
                raise IDMissingInBackend
            else:
                raise

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
        if self.collection.version == '1.7':
            # TODO add a search entry point on the Magento API
            return [int(row['product_id']) for row
                    in self._call('%s.list' % self._magento_model,
                                  [filters] if filters else [{}])]
        return super(ProductProductAdapter, self).search(filters=filters)

    def write(self, external_id, data, storeview=None, **kwargs):
        """ Update records on the external system """
        # pylint: disable=method-required-super
        # XXX actually only ol_catalog_product.update works
        # the PHP connector maybe breaks the catalog_product.update
        if self.collection.version == '1.7':
            return self._call('ol_catalog_product.update',
                              [int(external_id), data, storeview, 'id'])
        return super(ProductProductAdapter, self).write(
            external_id, data, storeview=storeview, **kwargs)

    def read_image(self, external_id, image_name, storeview_id=None):
        if self.collection.version == '1.7':
            return self._call(
                'product_media.info',
                [int(external_id), image_name, storeview_id, 'id'])
        raise NotImplementedError  # TODO


class MagentoBindingProductListener(Component):
    _name = 'magento.binding.product.product.listener'
    _inherit = 'base.connector.listener'
    _apply_on = ['magento.product.product']

    # fields which should not trigger an export of the products
    # but an export of their inventory
    INVENTORY_FIELDS = ('manage_stock',
                        'backorders',
                        'magento_qty',
                        )

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if record.no_stock_sync:
            return
        inventory_fields = list(
            set(fields).intersection(self.INVENTORY_FIELDS)
        )
        if inventory_fields:
            record.with_delay(priority=20).export_inventory(
                fields=inventory_fields
            )
