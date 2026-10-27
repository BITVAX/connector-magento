# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""18.0.1.7.0: the description of the product page of the shop gets a field of
its own, magento_description, translated as a whole in every language.

The Magento attribute "description" was mapped on the description of the
product: an internal note in Odoo, translated paragraph by paragraph, so a
translation with other paragraphs than the base text replaced the text of every
language. Here the texts of the products linked to Magento are copied to
magento_description (only where it is still empty) and the attribute is mapped
on it.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    model_fields = env["ir.model.fields"]
    old_field = model_fields._get("product.template", "description")
    new_field = model_fields._get("product.template", "magento_description")
    attributes = env["magento.product.attribute"].search(
        [
            ("attribute_code", "=", "description"),
            ("field_id", "in", (old_field | new_field).ids),
        ]
    )
    if not attributes:
        return
    backend_ids = tuple(attributes.backend_id.ids)
    cr.execute(
        """
        UPDATE product_template t
           SET magento_description = t.description
         WHERE t.magento_description IS NULL
           AND t.description IS NOT NULL
           AND (EXISTS (SELECT 1 FROM magento_product_template b
                         WHERE b.odoo_id = t.id AND b.backend_id IN %s)
                OR EXISTS (SELECT 1 FROM magento_product_product b
                             JOIN product_product p ON p.id = b.odoo_id
                            WHERE p.product_tmpl_id = t.id
                              AND b.backend_id IN %s))
        """,
        (backend_ids, backend_ids),
    )
    copied = cr.rowcount
    remapped = attributes.filtered(lambda a: a.field_id == old_field)
    remapped.with_context(connector_no_export=True).write({"field_id": new_field.id})
    _logger.info(
        "Magento description: %s product texts copied to magento_description, "
        "%s Magento attributes mapped on it",
        copied,
        len(remapped),
    )
