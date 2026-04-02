# Copyright 2024 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
Tests for importing and exporting Magento product attributes and values.

Uses the configurable product cassette which includes full attribute
set, attribute, and attribute value import flows.
"""

from .common import Magento2SyncTestCase


class TestImportAttribute(Magento2SyncTestCase):
    def setUp(self):
        super().setUp()

    def _import_attribute_set(self, set_id):
        """Import an attribute set which triggers attribute + value imports."""
        return self._import_record(
            "magento.product.attribute.set", set_id, cassette=True
        )

    def _import_attribute(self, attr_id):
        """Import a single attribute by its numeric ID."""
        return self._import_record("magento.product.attribute", attr_id, cassette=True)

    def test_import_attribute_set(self):
        """Import attribute set creates set + all its attributes."""
        binding = self._import_attribute_set(4)
        self.assertTrue(binding, "Attribute set binding should exist")
        self.assertEqual(binding.external_id, "4")
        self.assertEqual(binding.name, "Default")

        # Should have imported attributes linked to this set
        self.assertTrue(
            binding.attribute_ids, "Attribute set should have attributes linked"
        )

    def test_import_attribute_select(self):
        """Import a select attribute creates attribute + its values."""
        # Import attribute set first (triggers attribute imports)
        self._import_attribute_set(4)

        # Check Color attribute was imported
        color_binding = self.env["magento.product.attribute"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("attribute_code", "=", "color"),
            ]
        )
        self.assertEqual(len(color_binding), 1, "Color attribute should be imported")
        self.assertEqual(color_binding.frontend_input, "select")

        # The Odoo attribute should exist
        odoo_attr = color_binding.odoo_id
        self.assertTrue(odoo_attr)
        self.assertEqual(odoo_attr.name, "Color")
        # select attributes should create variants
        self.assertEqual(odoo_attr.create_variant, "always")

    def test_import_attribute_values(self):
        """Import attribute creates values as product.attribute.value."""
        # Import attribute set first
        self._import_attribute_set(4)

        # Check Color values were imported
        color_binding = self.env["magento.product.attribute"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("attribute_code", "=", "color"),
            ]
        )
        value_bindings = self.env["magento.product.attribute.value"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("magento_attribute_id", "=", color_binding.id),
            ]
        )
        self.assertGreaterEqual(
            len(value_bindings), 2, "Color should have at least 2 values (Red, Blue)"
        )

        # Check specific values
        red = value_bindings.filtered(lambda v: v.label == "Red")
        self.assertEqual(len(red), 1, "Red value should exist")
        self.assertEqual(red.external_id, "93_49")
        self.assertEqual(red.code, "49")

        blue = value_bindings.filtered(lambda v: v.label == "Blue")
        self.assertEqual(len(blue), 1, "Blue value should exist")
        self.assertEqual(blue.external_id, "93_50")
        self.assertEqual(blue.code, "50")

        # Values should be linked to the Odoo attribute
        self.assertEqual(red.odoo_id.attribute_id, color_binding.odoo_id)
        self.assertEqual(blue.odoo_id.attribute_id, color_binding.odoo_id)

    def test_import_attribute_text(self):
        """Text attributes should not create variants."""
        self._import_attribute_set(4)

        url_key = self.env["magento.product.attribute"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("attribute_code", "=", "url_key"),
            ]
        )
        self.assertEqual(len(url_key), 1)
        self.assertEqual(url_key.frontend_input, "text")
        # text attributes should NOT create variants
        self.assertEqual(url_key.odoo_id.create_variant, "no_variant")

    def test_import_attribute_size_values(self):
        """Size attribute has correct values S and M."""
        self._import_attribute_set(4)

        size_binding = self.env["magento.product.attribute"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("attribute_code", "=", "size"),
            ]
        )
        self.assertTrue(size_binding, "Size attribute should be imported")

        value_bindings = self.env["magento.product.attribute.value"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("magento_attribute_id", "=", size_binding.id),
            ]
        )
        labels = value_bindings.mapped("label")
        self.assertIn("S", labels, "Size S should exist")
        self.assertIn("M", labels, "Size M should exist")

    def test_import_attribute_idempotent(self):
        """Re-importing attributes does not duplicate them."""
        self._import_attribute_set(4)
        count1 = self.env["magento.product.attribute"].search_count(
            [
                ("backend_id", "=", self.backend.id),
            ]
        )

        # Import again
        self._import_attribute_set(4)
        count2 = self.env["magento.product.attribute"].search_count(
            [
                ("backend_id", "=", self.backend.id),
            ]
        )
        self.assertEqual(count1, count2, "Re-import should not duplicate attributes")
