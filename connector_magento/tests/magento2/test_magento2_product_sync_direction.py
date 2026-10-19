# Copyright 2026 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
Tests for the product sync direction of the backend.

A backend that only exports products refuses their imports, and one that only
imports them refuses their exports and deletions, so that nobody runs the
wrong way by mistake. "Import and Export" keeps the previous behaviour.
(The product listeners that would export automatically are not loaded.)
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import Magento2TestCase


@tagged("post_install", "-at_install")
class TestProductSyncDirection(Magento2TestCase):
    def setUp(self):
        super().setUp()
        self.product = self.env["product.product"].create(
            {"name": "ZZ Sync Direction", "default_code": "ZZ-SYNC-DIR", "type": "consu"}
        )
        self.binding = self.create_binding_no_export(
            "magento.product.product", self.product, "ZZ-SYNC-DIR"
        )
        self.template_binding = self.create_binding_no_export(
            "magento.product.template",
            self.product.product_tmpl_id,
            "ZZ-SYNC-DIR-T",
            code_prefix="ZZ-SYNC-DIR-T",
        )

    def test_default_keeps_both_ways(self):
        self.assertEqual(self.backend.product_sync_direction, "both")
        self.assertEqual(self.binding.product_sync_direction, "both")
        with trap_jobs() as trap:
            self.binding.sync_to_magento()
            self.binding.sync_from_magento()
            self.template_binding.sync_to_magento()
            self.template_binding.sync_from_magento()
            trap.assert_jobs_count(4)

    def test_export_only_refuses_imports(self):
        self.backend.product_sync_direction = "export"
        for records, method in (
            (self.binding, "sync_from_magento"),
            (self.template_binding, "sync_from_magento"),
            (self.backend, "import_product_product"),
            (self.backend, "import_product_template"),
        ):
            with self.assertRaises(UserError, msg=method):
                getattr(records, method)()
        with trap_jobs() as trap:
            self.binding.sync_to_magento()
            self.template_binding.delete_from_magento()
            trap.assert_jobs_count(2)

    def test_export_only_backend_is_left_out_of_the_product_import_cron(self):
        self.backend.product_sync_direction = "export"
        # It would raise if the backend was not filtered out.
        self.backend._scheduler_import_product_product(
            domain=[("id", "=", self.backend.id)]
        )

    def test_import_only_refuses_exports_and_deletions(self):
        self.backend.product_sync_direction = "import"
        for records, method in (
            (self.binding, "sync_to_magento"),
            (self.binding, "delete_from_magento"),
            (self.template_binding, "sync_to_magento"),
            (self.template_binding, "delete_from_magento"),
        ):
            with self.assertRaises(UserError, msg=method):
                getattr(records, method)()
        with trap_jobs() as trap:
            self.binding.sync_from_magento()
            trap.assert_jobs_count(1)
