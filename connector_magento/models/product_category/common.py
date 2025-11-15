# © 2013 Guewen Baconnier,Camptocamp SA,Akretion
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo import models, fields, api, _
from odoo.addons.connector.exception import IDMissingInBackend
from odoo.addons.component.core import Component
from ...components.backend_adapter import MAGENTO_DATETIME_FORMAT

_logger = logging.getLogger(__name__)


class MagentoProductCategory(models.Model):
    _name = 'magento.product.category'
    _inherit = 'magento.binding'
    _inherits = {'product.category': 'odoo_id'}
    _description = 'Magento Product Category'

    odoo_id = fields.Many2one(comodel_name='product.category',
                              string='Product Category',
                              required=True,
                              ondelete='cascade')
    description = fields.Text(translate=True)
    magento_parent_id = fields.Many2one(
        comodel_name='magento.product.category',
        string='Magento Parent Category',
        ondelete='cascade',
    )
    magento_child_ids = fields.One2many(
        comodel_name='magento.product.category',
        inverse_name='magento_parent_id',
        string='Magento Child Categories',
    )

    _sql_constraints = [
        ('magento_uniq', 'unique(backend_id, external_id)',
         'A category with the same ID on Magento already exists.'),
    ]


class ProductCategoryPublic(models.Model):
    _name = 'product.category.public'
    _description = 'Product Category Public'

    name = fields.Char(string='Name', required=True)
    child_ids = fields.One2many(
        comodel_name='product.category.public',
        inverse_name='parent_id',
        string='Children',
    )
    parent_id = fields.Many2one(
        comodel_name='product.category.public',
        string='Parent Category',
        ondelete='cascade',
    )


class ProductCategory(models.Model):
    _inherit = 'product.category'

    magento_bind_ids = fields.One2many(
        comodel_name='magento.product.category',
        inverse_name='odoo_id',
        string='Magento Bindings',
    )
    created_at = fields.Datetime(
        string='Created At (on Magento)',
        readonly=True
    )
    updated_at = fields.Datetime(
        string='Updated At (on Magento)',
        readonly=True
    )


class ProductCategoryAdapter(Component):
    _name = 'magento.product.category.adapter'
    _inherit = 'magento.adapter'
    _apply_on = 'magento.product.category'

    _magento2_model = 'categories'
    _magento2_key = 'id'
    _magento2_search = 'categories'

    def _call(self, method, arguments=None, http_method=None, storeview=None):
        try:
            return super(ProductCategoryAdapter, self)._call(
                method, arguments, http_method=http_method, storeview=storeview)
        except Exception as err:
            # this is the only way we have to check if the category exists
            if err.args and err.args[0] in [
                    'Requested category doesn\'t exist',
                    'Category does not exist.'
            ]:
                raise IDMissingInBackend(err)
            else:
                raise

    def search(self, filters=None):
        """ Search records according to some criterias
        and returns a list of ids

        :rtype: list
        """
        dt_fmt = MAGENTO_DATETIME_FORMAT
        if 'from_date' in filters:
            # updated_at include the created records
            filters['updated_at'] = {'from': filters.pop('from_date')}
        if 'to_date' in filters:
            filters['updated_at']['to'] = filters.pop('to_date')
        from_date = filters.get('updated_at', {}).get('from')
        to_date = filters.get('updated_at', {}).get('to')
        if from_date is not None:
            filters.setdefault('updated_at', {})
            filters['updated_at']['from'] = from_date.strftime(dt_fmt)
        if to_date is not None:
            filters.setdefault('updated_at', {})
            filters['updated_at']['to'] = to_date.strftime(dt_fmt)
        return super(ProductCategoryAdapter, self).search(filters=filters)

    def read(self, external_id, attributes=None, storeview=None, **kwargs):
        """ Returns the information of a record

        :rtype: dict
        """
        # pylint: disable=method-required-super
        return super(ProductCategoryAdapter, self).read(
            external_id, attributes=attributes, storeview=storeview, **kwargs)

    def tree(self, parent_id=None, storeview_id=None):
        """ Returns a tree of product categories

        :rtype: dict
        """
        ret = self._call('{}/list'.format(self._magento2_model),
                          {'searchCriteria': ''})
        return {c['id']: c['children'] for c in ret.get('items', [])}

    def move(self, categ_id, parent_id, after_categ_id=None):
        return self._call(
            '%s/%s/move' % (self._magento2_model, categ_id), {
                'parent_id': parent_id,
                'after_id': after_categ_id,
            }, http_method='put')

    def get_assigned_product(self, categ_id):
        raise NotImplementedError("TODO: Implement for Magento 2.0+")

    def assign_product(self, categ_id, product_id, position=0):
        raise NotImplementedError("TODO: Implement for Magento 2.0+")

    def update_product(self, categ_id, product_id, position=0):
        raise NotImplementedError("TODO: Implement for Magento 2.0+")

    def remove_product(self, categ_id, product_id):
        raise NotImplementedError("TODO: Implement for Magento 2.0+")
