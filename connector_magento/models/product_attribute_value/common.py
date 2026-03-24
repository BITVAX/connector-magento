import logging
from odoo import models, fields, api
from odoo.addons.component.core import Component
import urllib.request, urllib.parse, urllib.error

from odoo.addons.queue_job.exception import JobError

_logger = logging.getLogger(__name__)


class MagentoProductAttributevalue(models.Model):
    _name = 'magento.product.attribute.value'
    _inherit = 'magento.binding'
    # _inherits = {'product.attribute.value': 'odoo_id'}
    _description = 'Magento attribute value'

    odoo_id = fields.Many2one(comodel_name='product.attribute.value',
                              string='Product attribute value',
                              required=False, # Was True
                              ondelete='cascade')
    magento_attribute_id = fields.Many2one(comodel_name='magento.product.attribute',
                                       string='Magento Product Attribute',
                                       required=True,
                                       ondelete='cascade',
                                       index=True)
    attribute_id = fields.Many2one(related='magento_attribute_id.odoo_id')
    name = fields.Char(related='odoo_id.name')
    magento_attribute_type = fields.Selection(
         related="magento_attribute_id.frontend_input",
         store=True
        )
    # The real magento code - external_id is a combination of attribute_id + _ + code
    code = fields.Char('Magento Code for the value')
    label = fields.Char('Magento Label')
    main_text_code = fields.Char('Main text code eg. swatch or default value')
    backend_id = fields.Many2one(
        related='magento_attribute_id.backend_id',
        string='Magento Backend',
        readonly=True,
        store=True,
        # override 'magento.binding', can't be INSERTed if True:
        required=False,
    )


class ProductAttributevalue(models.Model):
    _inherit = 'product.attribute.value'

    magento_bind_ids = fields.One2many(
        comodel_name='magento.product.attribute.value',
        inverse_name='odoo_id',
        string='Magento Bindings',
    )


class ProductAttributeValueAdapter(Component):
    _name = 'magento.product.attribute.value.adapter'
    _inherit = 'magento.adapter'
    _apply_on = 'magento.product.attribute.value'

    _magento2_model = 'products/attributes/%(attribute_code)s/options'
    _magento2_search = 'options'
    _magento2_key = 'id'
    _magento2_name = 'option'



    def read(self, id, attributes=None,storeview=None, **kwargs):
        """ Returns the information of a record

        :rtype: dict
        """
        res_admin = super(ProductAttributeValueAdapter, self).read(
            id, attributes=attributes, storeview='all',**kwargs)
        if res_admin:
            for attr in res_admin.get('custom_attributes', []):
                res_admin[attr['attribute_code']] = attr['value']
        return res_admin

    def delete(self, magento_value_id, magento_attribute_id):
        """ Delete a record on the external system """
        res = self._call('%s/%s' % (self._magento2_model % {'attribute_code': magento_attribute_id}, self.escape(magento_value_id)), http_method="delete")
        return res

    def write(self, id, data, **kwargs):
        """ Update a record on the external system """
        if 'binding_attribute' in kwargs:
            value = self._call(self._magento2_model % {'attribute_code': kwargs['binding_attribute'].attribute_code}, {"option": data}, http_method="post")
            return "{}_{}".format(kwargs['binding_attribute'].attribute_id, value)
        else:
            raise JobError('Data error: binding or attribute_code not found in kwargs')

    def create(self, data, **kwargs):
        """ Create a record on the external system """
        if 'binding_attribute' in kwargs:
            value = self._call(self._magento2_model % {'attribute_code': kwargs['binding_attribute'].attribute_code},{"option": data}, http_method="post")
            return "{}_{}".format(kwargs['binding_attribute'].attribute_id,value)
        else:
            raise JobError('Data error: binding or attribute_code not found in kwargs')
