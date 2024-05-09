import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class MagentoTemplateAttributeline(models.Model):
    _name = 'magento.product.template.attribute.line'
    _inherit = 'magento.binding'
    _inherits = {'product.template.attribute.line': 'odoo_id'}
    _description = 'Magento attribute line'

    odoo_id = fields.Many2one(comodel_name='product.template.attribute.line',
                              string='Product attribute line',
                              required=True,
                              ondelete='cascade')

    magento_attribute_id = fields.Many2one(comodel_name='magento.product.attribute',
                                           string='Magento Product Attribute',
                                           required=True,
                                           ondelete='cascade',
                                           index=True)
    magento_template_id = fields.Many2one(comodel_name='magento.product.template',
                                          string='Magento Product Template',
                                          required=True,
                                          ondelete='cascade',
                                          index=True)
    magento_product_attribute_value_ids = fields.Many2many(comodel_name='magento.product.attribute.value',
                                                           relation='magent_product_att_values_rel',
                                                           string='Magento Product Values',
                                                           required=True,
                                                           ondelete='cascade',
                                                           index=True)
    label = fields.Char('Label')
    position = fields.Integer('Position')

    backend_id = fields.Many2one(
        related='magento_attribute_id.backend_id',
        string='Magento Backend',
        readonly=True,
        store=True,
        required=False,
    )

    @api.model
    def write(self, vals):
        # Do resolve the attribute id from the magento binding
        binding = self.env['magento.product.attribute'].browse(vals['magento_attribute_id'])
        vals['attribute_id'] = binding.odoo_id.id
        line = super(MagentoTemplateAttributeline, self).write(vals)
        return line

    @api.model
    def create(self, vals):
        # Do read product_tmpl_id using the magento_tmpl_id
        tmpl_binding = self.env['magento.product.template'].browse(vals['magento_template_id'])
        vals['product_tmpl_id'] = tmpl_binding.odoo_id.id
        # Do resolve the attribute id from the magento binding
        binding = self.env['magento.product.attribute'].browse(vals['magento_attribute_id'])
        vals['attribute_id'] = binding.odoo_id.id
        return super(MagentoTemplateAttributeline, self.with_context(create_product_product=False)).create(vals)


class ProductTemplateAttributeline(models.Model):
    _inherit = 'product.template.attribute.line'

    magento_bind_ids = fields.One2many(
        comodel_name='magento.product.template.attribute.line',
        inverse_name='odoo_id',
        string='Magento Bindings',
    )