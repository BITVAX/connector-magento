import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class MagentoTemplateAttributeline(models.Model):
    _name = "magento.product.template.attribute.line"
    _inherit = "magento.binding"
    _inherits = {"product.template.attribute.line": "odoo_id"}
    _description = "Magento attribute line"

    odoo_id = fields.Many2one(
        comodel_name="product.template.attribute.line",
        string="Product attribute line",
        required=True,
        ondelete="cascade",
    )

    magento_attribute_id = fields.Many2one(
        comodel_name="magento.product.attribute",
        string="Magento Product Attribute",
        required=True,
        ondelete="cascade",
        index=True,
    )
    magento_template_id = fields.Many2one(
        comodel_name="magento.product.template",
        string="Magento Product Template",
        required=True,
        ondelete="cascade",
        index=True,
    )
    magento_product_attribute_value_ids = fields.Many2many(
        comodel_name="magento.product.attribute.value",
        relation="magent_product_att_values_rel",
        string="Magento Product Values",
        required=True,
        ondelete="cascade",
        index=True,
    )
    label = fields.Char()
    position = fields.Integer()

    backend_id = fields.Many2one(
        related="magento_attribute_id.backend_id",
        string="Magento Backend",
        readonly=True,
        store=True,
        required=False,
    )

    def write(self, vals):
        # Writing the attribute on the line (through _inherits) recomputes the
        # variants of the template, which with product_variant_sale_price gives
        # every variant the price of the template: only when it really changes.
        if "magento_attribute_id" in vals:
            attribute = (
                self.env["magento.product.attribute"]
                .browse(vals["magento_attribute_id"])
                .odoo_id
            )
            if any(line.attribute_id != attribute for line in self):
                vals["attribute_id"] = attribute.id
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # The binding of an existing line only links it: its template and
            # attribute would be written on the line (see write), and the first
            # export of a configurable left all its variants at the price of
            # the template.
            if vals.get("odoo_id"):
                continue
            # Do read product_tmpl_id using the magento_tmpl_id
            tmpl_binding = self.env["magento.product.template"].browse(
                vals["magento_template_id"]
            )
            vals["product_tmpl_id"] = tmpl_binding.odoo_id.id
            # Do resolve the attribute id from the magento binding
            binding = self.env["magento.product.attribute"].browse(
                vals["magento_attribute_id"]
            )
            vals["attribute_id"] = binding.odoo_id.id
        return super(
            MagentoTemplateAttributeline,
            self.with_context(create_product_product=False),
        ).create(vals_list)


class ProductTemplateAttributeline(models.Model):
    _inherit = "product.template.attribute.line"

    magento_bind_ids = fields.One2many(
        comodel_name="magento.product.template.attribute.line",
        inverse_name="odoo_id",
        string="Magento Bindings",
    )
