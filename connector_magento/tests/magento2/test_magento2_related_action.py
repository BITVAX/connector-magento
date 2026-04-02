# Copyright 2014-2019 Camptocamp SA
# Copyright 2020 Opener B.V.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

from odoo import exceptions

from .common import Magento2SyncTestCase


class TestRelatedActionStorage(Magento2SyncTestCase):
    """Test related actions on stored jobs"""

    def setUp(self):
        super().setUp()
        self.MagentoProduct = self.env["magento.product.product"]
        self.QueueJob = self.env["queue.job"]
        self.test_product = self.env["product.product"].create(
            {
                "name": "Test Product for Related Action",
                "default_code": "TEST-RA-001",
            }
        )

    def test_unwrap_binding(self):
        """Open a related action opening an unwrapped binding"""
        product = self.test_product
        attr_set = self.env["magento.product.attribute.set"].search([], limit=1)
        if not attr_set:
            attr_set = self.env["magento.product.attribute.set"].create(
                {"name": "Default", "backend_id": self.backend.id}
            )
        magento_product = self.MagentoProduct.create(
            {
                "odoo_id": product.id,
                "magento_internal_id": "1234356",
                "attribute_set_id": attr_set.id,
                "backend_id": self.backend.id,
            }
        )
        job = magento_product.with_delay().export_record()
        stored = job.db_record()

        # Test the unwrap_binding related action directly
        result = stored.related_action_unwrap_binding()
        self.assertEqual(result["type"], "ir.actions.act_window")
        self.assertEqual(result["res_model"], "product.product")
        self.assertEqual(result["res_id"], product.id)
        self.assertIn("form", result["view_mode"])

    def test_link(self):
        """Open a related action opening an url on Magento.
        It only succeeds if we already have the magento internal id."""
        self.backend.write({"admin_location": "http://www.example.com/admin"})
        product = self.test_product
        attr_set = self.env["magento.product.attribute.set"].search([], limit=1)
        if not attr_set:
            attr_set = self.env["magento.product.attribute.set"].create(
                {"name": "Default", "backend_id": self.backend.id}
            )

        # Create a binding WITHOUT magento_internal_id first
        job = self.MagentoProduct.with_delay().import_record(
            self.backend,
            product.default_code,
        )
        stored = job.db_record()
        # No binding exists yet, so magento_link should fail
        with self.assertRaisesRegex(
            exceptions.UserError, "No admin URL configured|import the product before"
        ):
            stored.related_action_magento_link()

        # Now create the binding with magento_internal_id
        self.MagentoProduct.create(
            {
                "odoo_id": product.id,
                "external_id": product.default_code,
                "magento_internal_id": "1234356",
                "attribute_set_id": attr_set.id,
                "backend_id": self.backend.id,
            }
        )

        url = "http://www.example.com/admin/catalog/product/edit/id/1234356"
        expected = {
            "type": "ir.actions.act_url",
            "target": "new",
            "url": url,
        }
        self.assertEqual(stored.related_action_magento_link(), expected)

    def test_link_no_location(self):
        """Related action opening an url, admin location is not configured"""
        self.backend.write({"admin_location": False})
        job = self.MagentoProduct.with_delay().import_record(self.backend, "123456")
        stored = job.db_record()
        msg = r"No admin URL configured.*"
        with self.assertRaisesRegex(exceptions.UserError, msg):
            stored.related_action_magento_link()
