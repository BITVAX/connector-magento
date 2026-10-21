# Copyright 2013-2017 Camptocamp SA
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import logging
import uuid

from odoo import tools

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create
from odoo.addons.connector.exception import IDMissingInBackend, JobError

_logger = logging.getLogger(__name__)


class AttributeBatchImporter(Component):
    """Import the Magento Products attributes.

    For every product attributes in the list, a delayed job is created.
    Import from a date
    """

    _name = "magento.product.attribute.batch.importer"
    _inherit = "magento.delayed.batch.importer"
    _apply_on = ["magento.product.attribute"]


class AttributeImporter(Component):
    _name = "magento.product.attribute.import"
    _inherit = ["magento.importer"]
    _apply_on = ["magento.product.attribute"]
    _magento_id_field = "attribute_id"

    def _after_import(self, binding, **kwargs):
        super()._after_import(binding, **kwargs)
        record = self.magento_record
        importer = self.component(
            usage="record.importer", model_name="magento.product.attribute.value"
        )
        # Do import attribute values here
        _logger.info("Got %s Attribute Record Options", len(record["options"]))
        # if len(record['options']) > 200:
        #     _logger.error('We do not import such big option arrays')
        #     return
        for i in range(len(record["options"])):
            _logger.info("Do work on option %s", i)
            value = record["options"][i]
            if not value["value"]:
                continue
            value["external_id"] = "%s_%s" % (
                str(record.get("attribute_id")),
                tools.ustr(value.get("value")),
            )
            importer.run(
                value,
                magento_attribute=binding,
                attribute_code=record["attribute_code"],
            )
        self._import_option_labels(binding, record["attribute_code"])
        return

    def _import_option_labels(self, binding, attribute_code):
        """Keep the store view labels of the options as translations of the
        Odoo values, for the languages that have none of their own yet: the
        option export sends them back and Magento replaces all the labels of
        an option when it is saved."""
        admin_lang = self.backend_record._magento_admin_lang()
        if not admin_lang:
            return
        storeviews = self.env["magento.storeview"].search(
            [
                ("backend_id", "=", self.backend_record.id),
                ("lang_id", "!=", False),
                ("code", "!=", False),
                ("lang_id.code", "!=", admin_lang),
            ]
        )
        values = binding.magento_attribute_value_ids.filtered(
            lambda v: v.code and v.odoo_id
        )
        if not storeviews or not values:
            return
        name_field = self.env["product.attribute.value"]._fields["name"]

        def option_labels(storeview_code):
            try:
                options = self.backend_adapter._call(
                    "products/attributes/%s/options" % attribute_code,
                    None,
                    storeview=storeview_code,
                )
            except (IDMissingInBackend, JobError) as err:
                _logger.warning(
                    "Labels of %s not read from store view %s: %s",
                    attribute_code,
                    storeview_code,
                    err,
                )
                return None
            return {
                str(option["value"]): option["label"]
                for option in options or []
                if option.get("value") and option.get("label")
            }

        # A store view without a label of its own gives the admin one.
        admin_labels = option_labels("all")
        if admin_labels is None:
            return
        for storeview in storeviews:
            lang = storeview.lang_id.code
            labels = option_labels(storeview.code)
            if not labels:
                continue
            for value in values:
                label = labels.get(value.code)
                odoo_value = value.odoo_id
                if not label or label == admin_labels.get(value.code):
                    continue
                if (name_field._get_stored_translations(odoo_value) or {}).get(lang):
                    continue
                odoo_value.with_context(
                    connector_no_export=True
                ).update_field_translations("name", {lang: label})

    def _before_import(self):
        record = self.magento_record
        # Check for duplicate values here
        existing_values = []
        existing_names = []
        for i in range(len(record["options"])):
            value = record["options"][i]
            if value["value"] in existing_values:
                raise Exception(
                    "Value %s is a duplicate in %s"
                    % (value["value"], record["default_frontend_label"])
                )
            existing_values.append(value["value"])
            if (
                value["label"] in existing_names
                and self.backend_record.rename_duplicate_values
            ):
                self.magento_record["options"][i]["label"] = "%s (%s)" % (
                    value["label"],
                    str(uuid.uuid4()),
                )
            elif (
                value["label"] in existing_names
                and not self.backend_record.rename_duplicate_values
            ):
                raise Exception(
                    "Value %s is a duplicate in %s"
                    % (value["label"], record["default_frontend_label"])
                )
            existing_names.append(value["label"])

    def _update(self, binding, data):
        """Update an OpenERP record"""
        # special check on data before import
        self._validate_data(data)
        binding.with_context(connector_no_export=True).write(data)
        _logger.debug("%d updated from magento %s", binding, self.external_id)
        # Disabled for now - should be configurable using backend option
        # record = self.magento_record
        # values = [r['value'] for r in record['options']]
        # _logger.info("Got values from magento: %s", values)
        # odoo_magento_values = self.env['magento.product.attribute.value'].search([
        #     ('magento_attribute_id', '=', binding.id),
        #     ('code', 'not in', values),
        # ])
        # _logger.info("Got following odoo magento values %s to delete: %r", [
        #     ('magento_attribute_id', '=', binding.id),
        #     ('code', 'not in', values),
        # ], odoo_magento_values)
        # odoo_magento_values.with_context(connector_no_export=True).unlink()
        return


class AttributeImportMapper(Component):
    _name = "magento.product.attribute.import.mapper"
    _inherit = "magento.import.mapper"
    _apply_on = ["magento.product.attribute"]

    direct = [
        ("attribute_code", "attribute_code"),
        ("attribute_id", "attribute_id"),
        ("attribute_id", "external_id"),
        ("frontend_input", "frontend_input"),
        ("is_user_defined", "is_user_defined"),
    ]

    @only_create
    @mapping
    def get_att_id(self, record):
        # Check if we want to always create new odoo attributes
        # if self.backend_record.always_create_new_attributes:
        #     return {}
        # Else search for existing attribute
        att_id = self.env["product.attribute"].search(
            [("name", "=ilike", self._get_name(record)["name"])], limit=1
        )
        if att_id:
            return {"odoo_id": att_id.id}
        return {}

    @mapping
    def _get_name(self, record):
        name = record["attribute_code"]
        if "default_frontend_label" in record and record["default_frontend_label"]:
            name = record["default_frontend_label"]
        return {"name": name}

    @only_create
    @mapping
    def field_id(self, record):
        """The Odoo field of an attribute of fixed code, when the binding is
        created: a re-import respects what the user set or cleared."""
        field = self.env["magento.product.attribute"]._default_field_for_code(
            record.get("attribute_code")
        )
        return {"field_id": field.id} if field else {}

    @only_create
    @mapping
    def create_variant(self, record):
        if not record["frontend_input"] or record["frontend_input"] in [
            "text",
            "date",
            "gallery",
            "media_image",
            "price",
            "textarea",
            "weight",
        ]:
            return {"create_variant": "no_variant"}
        else:
            return {"create_variant": "always"}

    @mapping
    def backend_id(self, record):
        return {"backend_id": self.backend_record.id}
