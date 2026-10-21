import logging

from odoo import _, api, fields, models

from odoo.addons.component.core import Component

# from odoo.addons.queue_job.job import job, related_action, identity_exact
from odoo.addons.queue_job.job import identity_exact

_logger = logging.getLogger(__name__)


class MagentoProductAttribute(models.Model):
    _name = "magento.product.attribute"
    _inherit = "magento.binding"
    _inherits = {"product.attribute": "odoo_id"}
    _description = "Magento attribute"

    odoo_id = fields.Many2one(
        comodel_name="product.attribute",
        string="Product attribute",
        required=True,
        ondelete="cascade",
    )
    magento_attribute_value_ids = fields.One2many(
        comodel_name="magento.product.attribute.value",
        inverse_name="magento_attribute_id",
        string="Magento product attribute value",
    )
    field_id = fields.Many2one(
        comodel_name="ir.model.fields",
        string="Odoo Field",
        domain=[
            (
                "model",
                "in",
                (
                    "product.template",
                    "product.product",
                    "magento.product.product",
                    "magento.product.template",
                ),
            )
        ],
        help="Field of Odoo this attribute is synchronized with: the export "
        "sends its value and the import writes it. Empty: the attribute is "
        "left alone in both ways. The attributes of fixed code (description, "
        "url_key, meta_title...) get their field when they are imported; "
        "clearing it here switches them off, and a re-import does not put it "
        "back.",
    )

    attribute_id = fields.Integer(string="Magento Attribute ID")
    attribute_code = fields.Char(string="Magento Attribute Attribute Code")
    is_user_defined = fields.Boolean(string="Magento Attribute User Defined")
    exclude = fields.Boolean(string="Exclude from Import", default=False)
    nl2br = fields.Boolean("Enable NL2BR", default=False)
    frontend_input = fields.Selection(
        [
            ("text", "Text"),
            ("textarea", "Text Area"),
            ("select", "Selection"),
            ("multiselect", "Multi-Selection"),
            ("boolean", "Yes/No"),
            ("date", "Date"),
            ("price", "Price"),
            ("weight", "Weight"),
            ("media_image", "Media Image"),
            ("gallery", "Gallery"),
            ("weee", "Fixed Product Tax"),
            ("image", "Image"),
            ("hidden", "Hidden"),
            ("None", "None"),
        ],
        default="select",
    )

    attribute_set_ids = fields.Many2many(
        "magento.product.attribute.set", string="Attribute_set(s)"
    )
    is_pivot_attribute = fields.Boolean(string="Magento Pivot Attribute", default=False)

    _sql_constraints = [
        (
            "product_attribute_backend_uniq",
            "unique(odoo_id, external_id, backend_id)",
            "This attribute is already mapped to a magento backend!",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):
        return super().create(vals_list)

    @api.model
    def _default_field_by_code(self):
        """Odoo field each Magento attribute of fixed code is synchronized
        with, as {attribute_code: (model, field name)}: the fields the product
        page of the shop is made of. Another module adds the attributes of its
        shop by extending it. The import puts the field on the attribute when it
        creates it; clearing it on the attribute switches the mapping off."""
        return {
            "description": ("product.template", "description"),
            "short_description": ("product.template", "magento_short_description"),
            "meta_title": ("product.template", "meta_title"),
            "meta_keyword": ("product.template", "meta_keyword"),
            "meta_description": ("product.template", "meta_description"),
            "news_from_date": ("product.template", "magento_news_from_date"),
            "news_to_date": ("product.template", "magento_news_to_date"),
            # The URL key is per product in Magento, and per binding here.
            "url_key": ("magento.product.product", "magento_url_key"),
        }

    @api.model
    def _default_field_for_code(self, attribute_code):
        """The ir.model.fields record ``attribute_code`` maps on by default,
        or an empty recordset."""
        model_field = self._default_field_by_code().get(attribute_code)
        if not model_field:
            return self.env["ir.model.fields"]
        model, name = model_field
        return self.env["ir.model.fields"].search(
            [("model", "=", model), ("name", "=", name)], limit=1
        )

    @api.model
    def _apply_default_field_ids(self):
        """Map the attributes of fixed code that have no field yet (those
        imported before their default existed). An attribute whose field was
        cleared on purpose gets it back only here, never on a re-import."""
        codes = list(self._default_field_by_code())
        attributes = self.search(
            [("field_id", "=", False), ("attribute_code", "in", codes)]
        )
        for attribute in attributes:
            field = self._default_field_for_code(attribute.attribute_code)
            if field:
                attribute.with_context(connector_no_export=True).field_id = field
        return attributes

    def export_product_attribute_button(self):
        self.ensure_one()
        attr_name = self.name or (self.odoo_id and self.odoo_id.name) or ""
        self.with_delay(
            priority=20,
            identity_key=identity_exact,
            description=_("Export attribute '%s' to Magento") % attr_name,
        ).export_product_attribute()

    def import_product_attribute_button(self):
        self.ensure_one()
        attr_name = self.name or (self.odoo_id and self.odoo_id.name) or ""
        self.with_delay(
            priority=20,
            identity_key=identity_exact,
            description=_("Import attribute '%s' from Magento") % attr_name,
        ).import_product_attribute()

    # @job(default_channel='root.magento')
    # @related_action(action='related_action_unwrap_binding')
    def export_product_attribute(self, fields=None):
        """Export a simple attribute."""
        self.ensure_one()
        with self.backend_id.work_on(self._name) as work:
            exporter = work.component(usage="record.exporter")
            return exporter.run(self)

    # @job(default_channel='root.magento')
    # @related_action(action='related_action_unwrap_binding')
    def import_product_attribute(self):
        """Import a simple attribute."""
        self.ensure_one()
        with self.backend_id.work_on(self._name) as work:
            importer = work.component(usage="record.importer")
            return importer.run(self.external_id)


class ProductAttribute(models.Model):
    _inherit = "product.attribute"

    magento_bind_ids = fields.One2many(
        comodel_name="magento.product.attribute",
        inverse_name="odoo_id",
        string="Magento Bindings",
    )

    is_user_visible = fields.Boolean(
        string="User Visible", compute="_compute_is_user_visible", store=True
    )

    @api.depends(
        "magento_bind_ids.exclude", "magento_bind_ids.is_user_defined", "create_variant"
    )
    def _compute_is_user_visible(self):
        for record in self:
            record.is_user_visible = not (
                any(
                    [
                        any([x.exclude for x in record.magento_bind_ids]),
                        record.create_variant == "always",
                        any([not x.is_user_defined for x in record.magento_bind_ids]),
                    ]
                )
            )


class ProductAttributeAdapter(Component):
    _name = "magento.product.attribute.adapter"
    _inherit = "magento.adapter"
    _apply_on = "magento.product.attribute"

    _magento2_model = "products/attributes"
    _magento2_search = "products/attributes"
    _magento2_key = "attribute_id"
    _magento2_name = "attribute"

    def read(self, id, attributes=None, storeview=None, **kwargs):
        """Returns the information of a record
        :rtype: dict
        """
        if self.work.magento_api._location.version == "2.0":
            # Force the read on all storeviews so that the admin value is returned
            # https://github.com/magento/magento2/issues/3430
            res = super().read(id, attributes=attributes, storeview="all", **kwargs)
            return res
        return super().read(id, attributes=None, storeview=None, **kwargs)

    def _get_id_from_create(self, result, data=None):
        # We do need the complete result after the create function - to work on the options...
        return result.get("attribute_id")

    def create(self, data, storeview_code=None, **kwargs):  # pylint: disable=method-required-super
        """Create a record on the external system"""
        if self.work.magento_api._location.version == "2.0":
            if self._magento2_name:
                set_id = data["attribute_set_id"]
                # group_id = data['attribute_group_id']
                del data["attribute_set_id"]
                # del data['attribute_group_id']
                new_object = self._call(
                    self._magento2_model % kwargs,
                    {self._magento2_name: data, "saveOptions": True},
                    http_method="post",
                )
                # Make a second call to add the new attribute to the correct attribute set
                # TODO: We need to map the attributeGroups !!!
                self._call(
                    "products/attribute-sets/attributes",
                    {
                        "attributeSetId": set_id,
                        "attributeGroupId": self.backend_record.default_attribute_group_id
                        or 7,  # Configurable group
                        "attributeCode": new_object[
                            "attribute_code"
                        ],  # El valor se obtiene del backend configurado
                        "sortOrder": 0,
                    },
                    http_method="post",
                )
                if isinstance(new_object, dict):
                    data.update(new_object)
            else:
                new_object = self._call(
                    self._magento2_model % kwargs, data, http_method="post"
                )
            return self._get_id_from_create(new_object, data)
        raise NotImplementedError(
            "The create method is not implemented for Magento 1.x, please use the Magento 2.x version of this module."
        )
