# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""18.0.1.6.0: the binding menus of the Magento menu move to Connectors >
Bindings with shorter names (Magento Products -> Products...). Loading the new
name only changes the English one, and the translations of a module update
never overwrite an existing one: the old translations of these menus are
dropped here so that the new ones of the .po files fill them."""

RENAMED_MENUS = (
    "menu_magento_product_product",
    "menu_magento_product_template",
    "menu_magento_product_template_attribute_line",
    "menu_magento_product_attribute",
    "menu_magento_attribute_set",
    "menu_magento_product_category",
    "menu_magento_res_partner",
    "menu_magento_account_tax",
)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE ir_ui_menu m
           SET name = jsonb_build_object('en_US', m.name->>'en_US')
          FROM ir_model_data d
         WHERE d.module = 'connector_magento'
           AND d.model = 'ir.ui.menu'
           AND d.name IN %s
           AND d.res_id = m.id
        """,
        (RENAMED_MENUS,),
    )
