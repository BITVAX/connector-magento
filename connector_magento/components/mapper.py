# © 2013 Guewen Baconnier,Camptocamp SA,Akretion
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

import logging

from odoo.addons.component.core import AbstractComponent
from odoo.addons.connector.components.mapper import mapping

_logger = logging.getLogger(__name__)


class MagentoImportMapper(AbstractComponent):
    _name = "magento.import.mapper"
    _inherit = ["base.magento.connector", "base.import.mapper"]
    _usage = "import.mapper"

    @mapping
    def data(self, record):
        return {"data": record}


class MagentoExportMapper(AbstractComponent):
    _name = "magento.export.mapper"
    _inherit = ["base.magento.connector", "base.export.mapper"]
    _usage = "export.mapper"


class MagentoProductExportMapper(AbstractComponent):
    """Base export mapper for product-related bindings (template and product).

    Provides shared functionality for exporting products to Magento,
    including tax class mapping.
    """

    _name = "magento.product.export.mapper"
    _inherit = "magento.export.mapper"

    def _get_tax_class_id(self, record):
        """
        Get the Magento tax_class_id from the record's sale taxes.

        Returns the external_id of the magento.account.tax binding
        that corresponds to the first sale tax of the record.
        Works for both product.template and product.product bindings.
        """
        sale_tax = record.odoo_id.taxes_id[:1]
        if not sale_tax:
            return None

        magento_tax = (
            self.env["magento.account.tax"]
            .sudo()
            .search(
                [
                    ("odoo_id", "=", sale_tax.id),
                    ("backend_id", "=", record.backend_id.id),
                ],
                limit=1,
            )
        )

        if not magento_tax or not magento_tax.external_id:
            _logger.debug(
                "Tax '%s' has no Magento binding for backend %s - skipping tax_class_id export",
                sale_tax.name,
                record.backend_id.name,
            )
            return None

        return magento_tax.external_id


def normalize_datetime(field):
    """Change a invalid date which comes from Magento, if
    no real date is set to null for correct import to
    OpenERP"""

    def modifier(self, record, to_attr):
        if record[field] == "0000-00-00 00:00:00":
            return None
        return record[field]

    return modifier
