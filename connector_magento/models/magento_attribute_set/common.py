import logging
from odoo import models, fields, api
from odoo.addons.component.core import Component

_logger = logging.getLogger(__name__)


class MagentoProductAttributesSet(models.Model):
    _name = 'magento.product.attribute.set'
    _inherit = 'magento.binding'
    _description = 'Magento attribute set'
    _parent_name = 'backend_id'

    name = fields.Char(string = 'Set Name')
    display_name = fields.Char(string = 'Display Name', compute='_compute_display_name')
    attribute_ids = fields.Many2many('magento.product.attribute', string='Attribute(s)')
    # attribute_group_ids = fields.One2many('magento.product.attributes.group', 'attribute_set_id', string="Groups")

    @api.depends('name', 'backend_id')
    def _compute_display_name(self):
        for record in self:
            record.display_name = f'{record.name}-{record.backend_id.name}'

    @api.model
    def name_get(self):
        res = []
        for record in self:
            res.append((record.id, f'{record.name}-{record.backend_id.name}'))
        return res

class ProductAttributeSetAdapter(Component):
    _name = 'magento.product.attribute.set.adapter'
    _inherit = 'magento.adapter'
    _apply_on = 'magento.product.attribute.set'

    _magento2_model = 'products/attribute-sets'
    _magento2_search = 'products/attribute-sets/sets/list'
    _magento2_key = 'attribute_set_id'

    def read_detail(self, id, attributes=None):
        """ Returns the information of a record

        :rtype: dict
        """
        #TODO: find the way to get the code in options
        if self.collection.version == '2.0':
            res = self._call('products/attribute-sets/%s/attributes' % id,
                            {'attributes':{}})
            return res
