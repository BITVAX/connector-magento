# Copyright 2013-2017 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping


class ProductAttributeValueDefinitionExporter(Component):
    _name = "magento.product.attribute.value.exporter"
    _inherit = "magento.exporter"
    _apply_on = ["magento.product.attribute.value"]

    def _should_import(self):
        return False

    def _update(self, data, **kwargs):
        if "binding_attribute" in kwargs:
            return super()._update(data, **kwargs)
        # An option bound to Magento: update its labels in place.
        self._validate_update_data(data)
        return self.backend_adapter.update_option(
            self.binding.magento_attribute_id.attribute_code, self.binding.code, data
        )

    def _after_export(self):
        res = super()._after_export()
        self.binding.with_context(connector_no_export=True).write(
            {"magento_labels_signature": self.binding._magento_labels_signature()}
        )
        return res


class MagentoProductAttributeValueExportMapper(Component):
    _name = "magento.product.attribute.value.export.mapper"
    _inherit = "magento.export.mapper"
    _apply_on = ["magento.product.attribute.value"]

    @mapping
    def label(self, record):
        # The admin label in the language of the admin store, whatever the
        # language of the user who exports.
        return {"label": record._magento_admin_label()}

    @mapping
    def store_labels(self, record):
        return {"store_labels": record._magento_store_labels()}

    @mapping
    def code(self, record):
        if record.code:
            return {"value": record.code}

    @mapping
    def is_default(self, record):
        return {"is_default": False}

    @mapping
    def sort_order(self, record):
        return {"sort_order": record._magento_sort_order()}


class ProductAttributeValueExportMapper(Component):
    _name = "product.attribute.value.export.mapper"
    _inherit = "magento.export.mapper"
    _apply_on = ["product.attribute.value"]

    direct = [
        ("name", "label"),
    ]
