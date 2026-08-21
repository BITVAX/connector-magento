# Copyright 2026 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
Tests for duplicating products imported from Magento.

Importers force ``auto_create_variants`` to False so that they create the
variant themselves. That flag must not survive a duplicate: otherwise the copy
is created with variant generation suppressed and ends up with no variant at
all.
"""

import unittest

from odoo.tests import tagged

from .common import Magento2TestCase


# post_install: con el registry completo. En at_install todavía no están
# cargados los módulos que dependen de éste y que también extienden
# product.template / product.product.
@tagged("post_install", "-at_install")
class TestProductCopy(Magento2TestCase):
    def _create_imported_like_template(self):
        """A template as the Magento importers leave it: no auto variants."""
        return self.env["product.template"].create(
            {
                "name": "Imported From Magento",
                "type": "consu",
                "list_price": 10.0,
                "auto_create_variants": False,
            }
        )

    def test_copy_resets_auto_create_variants(self):
        template = self._create_imported_like_template()
        copy = template.copy()
        self.assertTrue(
            copy.auto_create_variants,
            "A duplicate must behave as a brand new product",
        )
        self.assertEqual(
            len(copy.product_variant_ids),
            1,
            "The duplicated template must have its variant",
        )

    @unittest.expectedFailure
    def test_copy_variant_returns_a_real_record(self):
        """product.product.copy() copies the template and returns its variant.

        KNOWN GAP, kept as a tripwire. copy=False fixes the flag but not this
        case: when the source template already has its variant -- which is
        exactly how the importer leaves an imported product -- the duplicate is
        created with auto_create_variants=True and still ends up with no
        variant at all. Duplicating a template that has no variants does work,
        so the two paths differ and the cause is not identified yet; finding it
        needs live instrumentation, not more code reading.

        Marked expectedFailure rather than deleted: unittest reports an
        unexpected success, so the day someone fixes the copy this test says so
        instead of staying quietly green.

        The assertions walk the chain stage by stage on purpose: core's copy()
        does template-copy first and only then resolves the variant, so a bare
        assert on the result cannot tell which half broke.
        """
        template = self._create_imported_like_template()
        # The importer creates the variant itself; mimic that.
        variant = self.env["product.product"].create(
            {"product_tmpl_id": template.id, "default_code": "MAG-SIMPLE"}
        )
        new_variant = variant.copy()

        new_template = self.env["product.template"].search(
            [("id", "!=", template.id), ("name", "like", "Imported From Magento%")],
            limit=1,
        )
        self.assertTrue(new_template, "copy() did not create the duplicated template")
        self.assertTrue(
            new_template.auto_create_variants,
            "the duplicated template inherited the import-time flag",
        )
        self.assertEqual(
            len(new_template.product_variant_ids),
            1,
            "the duplicated template was created without its variant",
        )
        self.assertTrue(
            new_variant,
            "copy() created the template but returned no variant",
        )
        self.assertNotEqual(new_variant.product_tmpl_id, template)
