# Copyright 2026 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
Regression tests for importing a simple product that references a select
attribute option missing on the Odoo side.

Profile under test: attribute with is_user_defined=True,
frontend_input='select' and create_variant='no_variant'. Magento 2 cannot
serve a single option (there is no GET /products/attributes/{code}/options/{id}),
so the importer must reimport the whole attribute — whose _after_import
creates every option — instead of raising MappingError.
"""

from .common import Magento2SyncTestCase


class TestImportProductNoVariantValue(Magento2SyncTestCase):
    def setUp(self):
        super().setUp()
        # Bring in attribute set 4 with the color attribute (93) and its
        # options 49/Red and 50/Blue.
        with self.recorder.use_cassette("import_product_attribute_set_4"):
            self.env["magento.product.attribute.set"].import_record(self.backend, 4)
        self.color_binding = self.env["magento.product.attribute"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("attribute_code", "=", "color"),
            ]
        )
        self.assertEqual(len(self.color_binding), 1)
        # The attribute-set cassette payload carries no is_user_defined key;
        # force the production profile under test: a user defined select
        # attribute that does not create variants.
        self.color_binding.is_user_defined = True
        self.color_binding.create_variant = "no_variant"

    def _drop_option(self, external_id):
        """Simulate an option added on Magento after the attribute import:
        neither the binding nor the odoo value exist on the Odoo side."""
        mvalue = self.env["magento.product.attribute.value"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("external_id", "=", external_id),
            ]
        )
        self.assertTrue(mvalue, "fixture: option %s should exist" % external_id)
        odoo_value = mvalue.odoo_id
        mvalue.with_context(connector_no_export=True).unlink()
        odoo_value.with_context(connector_no_export=True).unlink()

    def test_import_simple_product_missing_novariant_option(self):
        self._drop_option("93_50")
        with self.recorder.use_cassette("import_product_novariant_value"):
            self.env["magento.product.product"].import_record(
                self.backend, "TEST-NOVARIANT"
            )
        binding = self.env["magento.product.product"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("external_id", "=", "TEST-NOVARIANT"),
            ]
        )
        self.assertEqual(len(binding), 1)
        # The attribute was reimported as a dependency: the option missing in
        # Odoo but present on Magento is back, with both id spaces intact
        # (code = Magento option id, external_id = attribute_id + option id).
        mvalue = self.env["magento.product.attribute.value"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("external_id", "=", "93_50"),
            ]
        )
        self.assertEqual(len(mvalue), 1)
        self.assertEqual(mvalue.code, "50")
        self.assertEqual(mvalue.odoo_id.name, "Blue")
        # The product carries the attribute line with the value: this is what
        # the exporter reads back to build custom_attributes.
        line = binding.odoo_id.product_tmpl_id.attribute_line_ids.filtered(
            lambda ln: ln.attribute_id == self.color_binding.odoo_id
        )
        self.assertEqual(len(line), 1)
        self.assertEqual(line.value_ids.mapped("name"), ["Blue"])
        # And the no_variant value must never leak into the variant
        # combination: the core keeps those out of combination_indices, else
        # _get_variant_for_combination stops finding the variant.
        self.assertFalse(binding.odoo_id.product_template_attribute_value_ids)
        self.assertFalse(binding.odoo_id.combination_indices)
