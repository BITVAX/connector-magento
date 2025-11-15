# Copyright 2013-2019 Camptocamp SA
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging
from odoo import models, fields, api, _
from odoo.addons.connector.exception import IDMissingInBackend
from odoo.addons.component.core import Component
from ...components.backend_adapter import MAGENTO_DATETIME_FORMAT
from odoo.tools.translate import html_translate

_logger = logging.getLogger(__name__)

class ProductCategoryPublic(models.Model):
    _name = 'product.category.public'
    _description = 'Public Product Category'
    _inherit = 'image.mixin'
    description = fields.Text(translate=True)
    parent_id = fields.Many2one(
        comodel_name='product.category.public',
        string='Public Parent Category',
        ondelete='cascade',
    )
    child_ids = fields.One2many(
        comodel_name='product.category.public',
        inverse_name='parent_id',
        string='Public Child Categories',
    )
    _parent_store = True
    _order = "sequence, name, id"

    def _default_sequence(self):
        cat = self.search([], limit=1, order="sequence DESC")
        if cat:
            return cat.sequence + 5
        return 10000

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(help="Gives the sequence order when displaying a list of product categories.", index=True, default=_default_sequence)
    website_description = fields.Html('Category Description', sanitize_overridable=True, sanitize_attributes=False, translate=html_translate, sanitize_form=False)
    product_tmpl_ids = fields.Many2many('product.template', relation='product_category_public_rel')
    product_count = fields.Integer(
        compute='_compute_product_count',
        store=True,
        string='# Products',
        help="The number of products under this category (Does not consider the children categories)",
    )
    parent_path = fields.Char(index=True, unaccent=False)
    parents_and_self = fields.Many2many('product.category.public', compute='_compute_parents_and_self')
    display_name = fields.Char(compute='_compute_display_name', )

    @api.depends('product_tmpl_ids')
    def _compute_product_count(self):
        """ Compute the number of products in this category and its children """
        for category in self:
            category.product_count = len(category.product_tmpl_ids)
        return

    # @api.depends('name', 'parent_id.name')
    def _compute_display_name(self):
        for category in self:
            category.display_name = category.name_get()[0][1]

    magento_bind_ids = fields.One2many(
        comodel_name='magento.product.category',
        inverse_name='odoo_id',
        string="Magento Bindings",
    )
    @api.constrains('parent_id')
    def check_parent_id(self):
        if not self._check_recursion():
            raise ValueError(_('Error ! You cannot create recursive categories.'))

    def name_get(self):
        res = []
        for category in self:
            res.append((category.id, "/".join(category.parents_and_self.mapped(lambda x: x.name if x.parent_id else ''))))
        return res

    def _compute_parents_and_self(self):
        for category in self:
            if category.parent_path:
                category.parents_and_self = self.env['product.category.public'].browse([int(p) for p in category.parent_path.split('/')[:-1]])
            else:
                category.parents_and_self = category

    def action_view_products(self):
        """ Action to view products in this category """
        action = self.env.ref('product.product_template_action').read()[0]
        action['domain'] = [('id', 'in', self.product_tmpl_ids.ids)]
        return action

#
# class ProductCategory(models.Model):
#     _inherit = 'product.category'
#
#
class MagentoProductCategory(models.Model):
    _name = 'magento.product.category'
    _inherits = {'product.category.public': 'odoo_id'}
    _description = 'Magento Product Category'
    _inherit = [
        'magento.binding',
        'image.mixin',
    ]
    odoo_id = fields.Many2one('product.category.public')

class ProductCategoryAdapter(Component):
    _name = 'magento.product.category.adapter'
    _inherit = 'magento.adapter'
    _apply_on = 'magento.product.category'

    _magento_model = 'catalog_category'
    _magento2_model = 'categories'
    _magento2_search = 'categories'
    _magento2_key = 'id'
    _admin_path = '/{model}/index/'
    _magento2_name = 'category'
    # Not valid without security key
    # _admin2_path = '/catalog/category/index/'

    def _call(self, method, arguments, http_method=None, storeview=None):
        try:
            return super(ProductCategoryAdapter, self)._call(
                method, arguments, http_method=http_method,
                storeview=storeview)
        except Exception as err:
            # 101 is the error in the Magento API
            # when the category does not exist
            if err.faultCode == 102:
                raise IDMissingInBackend
            else:
                raise

    def search(self, filters=None, from_date=None, to_date=None):
        """ Search records according to some criteria and return a
        list of ids

        :rtype: list
        """
        if filters is None:
            filters = {}

        dt_fmt = MAGENTO_DATETIME_FORMAT
        if from_date is not None:
            filters.setdefault('updated_at', {})
            # updated_at include the created records
            filters['updated_at']['from'] = from_date.strftime(dt_fmt)
        if to_date is not None:
            filters.setdefault('updated_at', {})
            filters['updated_at']['to'] = to_date.strftime(dt_fmt)
        if self.collection.version == '1.7':
            return self._call('oerp_catalog_category.search',
                              [filters] if filters else [{}])
        return super(ProductCategoryAdapter, self).search(filters=filters)

    def read(self, external_id, attributes=None,storeview=None, **kwargs):
        """ Returns the information of a record

        :rtype: dict
        """
        # pylint: disable=method-required-super
        if self.collection.version == '1.7':
            return self._call('%s.info' % self._magento_model,
                              [int(external_id), storeview
                               , attributes])
        return super(ProductCategoryAdapter, self).read(
            external_id, attributes=attributes, storeview=storeview, **kwargs)

    def tree(self, parent_id=None, storeview_id=None):
        """ Returns a tree of product categories

        :rtype: dict
        """
        def filter_ids(tree):
            children = {}
            if tree['children']:
                for node in tree['children']:
                    children.update(filter_ids(node))
            category_id = {tree['category_id']: children}
            return category_id

        if self.collection.version == '1.7':
            if parent_id:
                parent_id = int(parent_id)
                tree = self._call('%s.tree' % self._magento_model,
                                  [parent_id, storeview_id])
            return filter_ids(tree)
        if self.collection.version == '2.0':
            ret = self._call('{}/list'.format(self._magento2_model),
                              {'searchCriteria': ''})
            return { c['id']: c['children'] for c in ret.get('items',[])}
        raise NotImplementedError  # TODO

    def move(self, categ_id, parent_id, after_categ_id=None):
        if self.collection.version == '1.7':
            return self._call('%s.move' % self._magento_model,
                              [categ_id, parent_id, after_categ_id])
        return self._call(
            '%s/%s/move' % (self._magento2_model, categ_id), {
                'parent_id': parent_id,
                'after_id': after_categ_id,
            })

    def get_assigned_product(self, categ_id):
        if self.collection.version == '1.7':
            return self._call('%s.assignedProducts' % self._magento_model,
                              [categ_id])
        raise NotImplementedError  # TODO

    def assign_product(self, categ_id, product_id, position=0):
        if self.collection.version == '1.7':
            return self._call('%s.assignProduct' % self._magento_model,
                              [categ_id, product_id, position, 'id'])
        raise NotImplementedError  # TODO

    def update_product(self, categ_id, product_id, position=0):
        if self.collection.version == '1.7':
            return self._call('%s.updateProduct' % self._magento_model,
                              [categ_id, product_id, position, 'id'])
        raise NotImplementedError  # TODO

    def remove_product(self, categ_id, product_id):
        if self.collection.version == '1.7':
            return self._call('%s.removeProduct' % self._magento_model,
                              [categ_id, product_id, 'id'])
        raise NotImplementedError  # TODO

    def move_category(self, category_id, source_id, target_id):
        if self.work.magento_api._location.version == '2.0':
            return self._call("categories/%s/move" % category_id, {
                "parentId": int(source_id),
                "afterId": int(target_id)
            }, storeview=None, http_method="put")

    def update_category_position(self, category_id, sku, position):
        if self.work.magento_api._location.version == '2.0':
            payload = {
              "productLink": {
                "sku": sku,
                "position": position,
                "category_id": category_id,
                "extension_attributes": {}
              }
            }
            _logger.info("Do call api endpoint categories/%s/products with payload: %s", category_id, payload)
            res = self._call('categories/%s/products' % category_id, payload, http_method="post")
            _logger.info("Got res: %s", res)
            return res
