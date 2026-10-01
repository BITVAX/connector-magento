# Copyright 2026 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
Access to public categories for users who are not connector managers.

The connector adds ``product_category_public_ids`` to every product template,
so any user opening a product form reads ``product.category.public``. Reading
must be open to everyone; changing the category tree stays with the connector
managers.
"""

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


# post_install: con el registry completo, como lo ve la ficha de producto real.
@tagged("post_install", "-at_install")
class TestProductCategoryPublicAccess(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.category = cls.env["product.category.public"].create(
            {"name": "Public Categ Access"}
        )
        cls.template = cls.env["product.template"].create(
            {
                "name": "Product With Public Categ",
                "product_category_public_ids": [(6, 0, cls.category.ids)],
            }
        )
        # A salesman, not a connector manager: the user who opens products.
        # The form also computes root_category_ids, which reads magento.website,
        # readable by salesmen.
        cls.user = new_test_user(
            cls.env,
            login="categ_public_user",
            groups="base.group_user,sales_team.group_sale_salesman",
        )

    def test_internal_user_reads_public_categories_of_a_product(self):
        """Opening the product form must not raise an AccessError."""
        # Same specification the form view sends for the many2many tags.
        [values] = self.template.with_user(self.user).web_read(
            {
                "product_category_public_ids": {"fields": {"display_name": {}}},
                "root_category_ids": {"fields": {"display_name": {}}},
            }
        )
        self.assertEqual(
            [c["display_name"] for c in values["product_category_public_ids"]],
            ["Public Categ Access"],
        )

    def test_internal_user_cannot_change_public_categories(self):
        Category = self.env["product.category.public"].with_user(self.user)
        with self.assertRaises(AccessError):
            Category.create({"name": "Not Allowed"})
        with self.assertRaises(AccessError):
            self.category.with_user(self.user).write({"name": "Renamed"})
        with self.assertRaises(AccessError):
            self.category.with_user(self.user).unlink()
