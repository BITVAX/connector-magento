# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""18.0.1.5.0: the texts of the product page go through the Odoo field mapped
on each Magento attribute, and the URL key lives on the binding.

- The attributes of fixed code (description, url_key, meta_*...) get their
  default field when they have none: until now field_id was manual.
- product.template.url_key is no longer a column: it shows the URL key of the
  binding of the product. The keys typed or imported on the template are
  copied to that binding, template bindings first, then the bindings of the
  products with a single variant. The old column is left in place by Odoo
  (the field still exists, computed); it is read here before anything else.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def _url_key_expression(cr):
    """SQL expression that reads product_template.url_key as jsonb, whatever
    the type of the column (jsonb where it was already translatable, varchar
    otherwise), or None without the column."""
    cr.execute(
        """
        SELECT data_type FROM information_schema.columns
         WHERE table_name = 'product_template' AND column_name = 'url_key'
        """
    )
    row = cr.fetchone()
    if not row:
        return None
    if row[0] == "jsonb":
        return "t.url_key"
    return "jsonb_build_object('en_US', t.url_key)"


def _copy_url_keys_to_bindings(cr):
    expression = _url_key_expression(cr)
    if not expression:
        return
    # Configurable products: the binding of the template.
    cr.execute(  # pylint: disable=sql-injection
        f"""
        UPDATE magento_product_template b
           SET magento_url_key = {expression}
          FROM product_template t
         WHERE b.odoo_id = t.id
           AND b.magento_url_key IS NULL
           AND t.url_key IS NOT NULL
           AND t.url_key::text NOT IN ('""', '{{}}')
           AND NOT EXISTS (
               SELECT 1 FROM magento_product_template b2
                WHERE b2.backend_id = b.backend_id
                  AND b2.id <> b.id
                  AND b2.magento_url_key = {expression}
           )
        """
    )
    templates = cr.rowcount
    # Simple products (a single variant): the binding of that variant.
    cr.execute(  # pylint: disable=sql-injection
        f"""
        UPDATE magento_product_product b
           SET magento_url_key = {expression}
          FROM product_product p
          JOIN product_template t ON t.id = p.product_tmpl_id
         WHERE b.odoo_id = p.id
           AND b.magento_url_key IS NULL
           AND t.url_key IS NOT NULL
           AND t.url_key::text NOT IN ('""', '{{}}')
           AND (SELECT count(*) FROM product_product p2
                 WHERE p2.product_tmpl_id = t.id AND p2.active) = 1
        """
    )
    _logger.info(
        "URL keys copied from the templates to %s template bindings and %s "
        "product bindings",
        templates,
        cr.rowcount,
    )


def migrate(cr, version):
    if not version:
        return
    _copy_url_keys_to_bindings(cr)
    env = api.Environment(cr, SUPERUSER_ID, {})
    attributes = env["magento.product.attribute"]._apply_default_field_ids()
    _logger.info(
        "Default Odoo field set on %s Magento attributes of fixed code",
        len(attributes),
    )
