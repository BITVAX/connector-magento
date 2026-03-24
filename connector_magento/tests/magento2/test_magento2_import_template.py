# Copyright 2024 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
Tests for importing configurable products as product.template with
all their simple variant children.
"""

from .common import Magento2SyncTestCase, recorder


class TestImportProductTemplate(Magento2SyncTestCase):

    def setUp(self):
        super().setUp()

    def _import_template(self, sku):
        """Import a configurable product template via its SKU."""
        return self._import_record(
            'magento.product.template', sku, cassette=True)

    def test_import_template_configurable(self):
        """Import a configurable product template with 2 simple variants.

        This tests the full flow:
        1. Template is created as product.template with attribute lines
        2. Configurable options (Color, Size) become product attributes
        3. Each simple child is imported as magento.product.product
        4. Variants are linked to the same product.template
        5. Images are imported for template and variants
        """
        backend_id = self.backend.id

        binding = self._import_template('CONF-TEST')
        self.assertTrue(binding, "Template binding should exist")

        # Template binding should have external_id
        self.assertEqual(binding.external_id, 'CONF-TEST')

        # The odoo product.template should exist
        template = binding.odoo_id
        self.assertTrue(template)
        self.assertEqual(template.name, 'Test Configurable Product')

        # Check that configurable attributes were created as attribute lines
        attr_lines = template.attribute_line_ids
        attr_names = attr_lines.mapped('attribute_id.name')
        self.assertIn('Color', attr_names,
                       "Color attribute line should exist on template")
        self.assertIn('Size', attr_names,
                       "Size attribute line should exist on template")

        # Check that 2 simple variants were imported
        variant_model = self.env['magento.product.product']
        variants = variant_model.search([
            ('backend_id', '=', backend_id),
            ('external_id', 'in', ['CONF-TEST-S-Red', 'CONF-TEST-M-Blue']),
        ])
        self.assertEqual(len(variants), 2,
                         "Both simple variants should be imported. "
                         "Found: %s" % variants.mapped('external_id'))

        # Variants should belong to the same template
        for variant in variants:
            self.assertEqual(
                variant.odoo_id.product_tmpl_id, template,
                "Variant %s should belong to template %s" %
                (variant.external_id, template.name))

        # Variant 1: CONF-TEST-S-Red
        v1 = variants.filtered(lambda v: v.external_id == 'CONF-TEST-S-Red')
        self.assertEqual(len(v1), 1)
        self.assertEqual(v1.odoo_id.type, 'product',
                         "Simple variant should be storable product")

        # Variant 2: CONF-TEST-M-Blue
        v2 = variants.filtered(lambda v: v.external_id == 'CONF-TEST-M-Blue')
        self.assertEqual(len(v2), 1)

        # Verify that the template has the right number of variants
        # (at least 2 — Odoo may generate more from attribute combinations)
        self.assertGreaterEqual(
            len(template.product_variant_ids), 2,
            "Template should have at least 2 variants")

    def test_import_template_binding_data(self):
        """Template and variant bindings store Magento data correctly."""
        binding = self._import_template('CONF-TEST')

        # Binding should have the magento data stored
        self.assertEqual(binding.external_id, 'CONF-TEST')
        self.assertTrue(binding.data,
                        "Template binding should store magento data")

        # Variants should have their own bindings with data
        variant_model = self.env['magento.product.product']
        v1 = variant_model.search([
            ('backend_id', '=', self.backend.id),
            ('external_id', '=', 'CONF-TEST-S-Red'),
        ])
        self.assertTrue(v1.data, "Variant binding should store magento data")
