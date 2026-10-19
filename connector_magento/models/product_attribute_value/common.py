import json
import logging

from odoo import fields, models

from odoo.addons.component.core import Component
from odoo.addons.queue_job.exception import JobError

_logger = logging.getLogger(__name__)


class MagentoProductAttributevalue(models.Model):
    _name = "magento.product.attribute.value"
    _inherit = "magento.binding"
    # _inherits = {'product.attribute.value': 'odoo_id'}
    _description = "Magento attribute value"

    odoo_id = fields.Many2one(
        comodel_name="product.attribute.value",
        string="Product attribute value",
        required=False,  # Was True
        ondelete="cascade",
    )
    magento_attribute_id = fields.Many2one(
        comodel_name="magento.product.attribute",
        string="Magento Product Attribute",
        required=True,
        ondelete="cascade",
        index=True,
    )
    attribute_id = fields.Many2one(related="magento_attribute_id.odoo_id")
    name = fields.Char(related="odoo_id.name")
    magento_attribute_type = fields.Selection(
        related="magento_attribute_id.frontend_input", store=True
    )
    # The real magento code - external_id is a combination of attribute_id + _ + code
    code = fields.Char("Magento Code for the value")
    label = fields.Char("Magento Label")
    main_text_code = fields.Char("Main text code eg. swatch or default value")
    backend_id = fields.Many2one(
        related="magento_attribute_id.backend_id",
        string="Magento Backend",
        readonly=True,
        store=True,
        # override 'magento.binding', can't be INSERTed if True:
        required=False,
    )
    magento_labels_signature = fields.Char(
        copy=False,
        help="Admin label and store view labels last sent to Magento.",
    )

    def _magento_sort_order(self):
        """Position of the value in its Odoo attribute, from 1. Magento saves
        the order of an option with its labels, and the sequences of the
        values are often all the same. Without a default language on the
        backend, the sequence as before."""
        self.ensure_one()
        if not self.backend_id._magento_admin_lang():
            return self.odoo_id.sequence or 0
        values = self.odoo_id.attribute_id.value_ids.sorted(lambda v: (v.sequence, v.id))
        return values.ids.index(self.odoo_id.id) + 1 if self.odoo_id in values else 0

    def _magento_admin_label(self):
        self.ensure_one()
        lang = self.backend_id._magento_admin_lang() or self.env.lang
        return self.odoo_id.with_context(lang=lang).name

    def _magento_store_labels(self):
        """Labels of the value for the store views whose language has a
        translation of its own, as Magento's ``store_labels``. A store view
        without one falls back to the admin label."""
        self.ensure_one()
        admin_lang = self.backend_id._magento_admin_lang()
        if not admin_lang or not self.odoo_id:
            return []
        admin_label = self._magento_admin_label()
        # Only the translations of their own: reading the field in a language
        # without one gives the source text.
        translations = (
            self.odoo_id._fields["name"]._get_stored_translations(self.odoo_id) or {}
        )
        storeviews = self.env["magento.storeview"].search(
            [("backend_id", "=", self.backend_id.id), ("lang_id", "!=", False)]
        )
        labels = []
        for storeview in storeviews.sorted(lambda s: int(s.external_id or 0)):
            lang = storeview.lang_id.code
            if lang == admin_lang or not storeview.external_id:
                continue
            label = translations.get(lang)
            if label and label != admin_label:
                labels.append({"store_id": int(storeview.external_id), "label": label})
        return labels

    def _magento_labels_signature(self):
        return json.dumps(
            [
                self._magento_admin_label(),
                self._magento_store_labels(),
                self._magento_sort_order(),
            ],
            ensure_ascii=False,
        )

    def _magento_labels_changed(self):
        """Whether the labels of the option have to go to Magento again.

        Magento replaces all the store labels of an option when it is saved,
        so an option is only sent once Odoo has translations for it: one that
        was never translated in Odoo (a model, a colour) keeps the labels it
        has in Magento.
        """
        self.ensure_one()
        if not self.external_id or not self.code or not self.odoo_id:
            return False
        if not self.backend_id._magento_admin_lang():
            return False
        if not self.magento_labels_signature and not self._magento_store_labels():
            return False
        return self.magento_labels_signature != self._magento_labels_signature()


class ProductAttributevalue(models.Model):
    _inherit = "product.attribute.value"

    magento_bind_ids = fields.One2many(
        comodel_name="magento.product.attribute.value",
        inverse_name="odoo_id",
        string="Magento Bindings",
    )


class ProductAttributeValueAdapter(Component):
    _name = "magento.product.attribute.value.adapter"
    _inherit = "magento.adapter"
    _apply_on = "magento.product.attribute.value"

    _magento2_model = "products/attributes/%(attribute_code)s/options"
    _magento2_search = "options"
    _magento2_key = "id"
    _magento2_name = "option"

    def read(self, id, attributes=None, storeview=None, **kwargs):
        """Returns the information of a record

        :rtype: dict
        """
        res_admin = super().read(id, attributes=attributes, storeview="all", **kwargs)
        if res_admin:
            for attr in res_admin.get("custom_attributes", []):
                res_admin[attr["attribute_code"]] = attr["value"]
        return res_admin

    def delete(self, magento_value_id, magento_attribute_id):
        """Delete a record on the external system"""
        res = self._call(
            "%s/%s"
            % (
                self._magento2_model % {"attribute_code": magento_attribute_id},
                self.escape(magento_value_id),
            ),
            http_method="delete",
        )
        return res

    def update_option(self, attribute_code, option_id, data):
        """Update the labels of an existing option. Unlike the POST of
        write(), it does not add a new option."""
        return self._call(
            "%s/%s"
            % (
                self._magento2_model % {"attribute_code": attribute_code},
                self.escape(option_id),
            ),
            {"option": data},
            http_method="put",
            storeview="all",
        )

    def write(self, id, data, **kwargs):  # pylint: disable=method-required-super
        """Update a record on the external system"""
        if "binding_attribute" in kwargs:
            value = self._call(
                self._magento2_model
                % {"attribute_code": kwargs["binding_attribute"].attribute_code},
                {"option": data},
                http_method="post",
            )
            return "{}_{}".format(kwargs["binding_attribute"].attribute_id, value)
        else:
            raise JobError("Data error: binding or attribute_code not found in kwargs")

    def create(self, data, **kwargs):  # pylint: disable=method-required-super
        """Create a record on the external system"""
        if "binding_attribute" in kwargs:
            value = self._call(
                self._magento2_model
                % {"attribute_code": kwargs["binding_attribute"].attribute_code},
                {"option": data},
                http_method="post",
            )
            return "{}_{}".format(kwargs["binding_attribute"].attribute_id, value)
        else:
            raise JobError("Data error: binding or attribute_code not found in kwargs")
