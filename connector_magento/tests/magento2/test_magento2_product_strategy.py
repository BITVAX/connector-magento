# Copyright 2026 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
Tests for the product import and export strategies of the backend.

"Do not Import" / "Do not Export" refuse that way, so that nobody runs it by
mistake; "Only New Products" and "Only Existing Products" leave out the
products already on the other side or not there yet. "Create and Update"
keeps the previous behaviour. (The product listeners that would export
automatically are not loaded.)
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import Magento2TestCase


@tagged("post_install", "-at_install")
class TestProductStrategy(Magento2TestCase):
    def setUp(self):
        super().setUp()
        self.product = self.env["product.product"].create(
            {"name": "ZZ Strategy", "default_code": "ZZ-STRATEGY", "type": "consu"}
        )
        self.new_product = self.env["product.product"].create(
            {"name": "ZZ Strategy New", "default_code": "ZZ-STRATEGY-NEW", "type": "consu"}
        )
        # Already in Magento: it has the internal id Magento gave it.
        self.binding = self.create_binding_no_export(
            "magento.product.product",
            self.product,
            "ZZ-STRATEGY",
            magento_internal_id="9001",
        )
        # Linked in Odoo but never created in Magento.
        self.new_binding = self.create_binding_no_export(
            "magento.product.product", self.new_product, "ZZ-STRATEGY-NEW"
        )
        self.template_binding = self.create_binding_no_export(
            "magento.product.template",
            self.product.product_tmpl_id,
            "ZZ-STRATEGY-T",
            code_prefix="ZZ-STRATEGY-T",
            magento_id=9002,
        )

    def _batch_importer_allows(self, model_name, external_id):
        with self.backend.work_on(model_name) as work:
            importer = work.component(usage="batch.importer")
            return importer._allowed_by_import_strategy(external_id)

    def test_default_creates_and_updates(self):
        self.assertEqual(self.backend.product_import_strategy, "always")
        self.assertEqual(self.backend.product_export_strategy, "always")
        self.assertEqual(self.binding.product_import_strategy, "always")
        with trap_jobs() as trap:
            (self.binding | self.new_binding).sync_to_magento()
            self.binding.sync_from_magento()
            self.template_binding.sync_to_magento()
            self.template_binding.sync_from_magento()
            trap.assert_jobs_count(5)
        self.assertTrue(
            self._batch_importer_allows("magento.product.product", "ZZ-STRATEGY")
        )
        self.assertTrue(
            self._batch_importer_allows("magento.product.product", "ZZ-UNKNOWN")
        )

    def test_do_not_import_refuses_imports(self):
        self.backend.product_import_strategy = "never"
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

    def test_do_not_import_backend_is_left_out_of_the_product_import_cron(self):
        self.backend.product_import_strategy = "never"
        # It would raise if the backend was not filtered out.
        self.backend._scheduler_import_product_product(
            domain=[("id", "=", self.backend.id)]
        )

    def test_do_not_export_refuses_exports_and_deletions(self):
        self.backend.product_export_strategy = "never"
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

    def test_import_only_new_products(self):
        self.backend.product_import_strategy = "create"
        self.assertFalse(
            self._batch_importer_allows("magento.product.product", "ZZ-STRATEGY")
        )
        self.assertTrue(
            self._batch_importer_allows("magento.product.product", "ZZ-UNKNOWN")
        )
        self.assertFalse(
            self._batch_importer_allows("magento.product.template", "ZZ-STRATEGY-T")
        )
        # A linked product would be updated: refused.
        with self.assertRaises(UserError):
            self.binding.sync_from_magento()
        with self.assertRaises(UserError):
            self.template_binding.sync_from_magento()

    def test_import_only_existing_products(self):
        self.backend.product_import_strategy = "update"
        self.assertTrue(
            self._batch_importer_allows("magento.product.product", "ZZ-STRATEGY")
        )
        self.assertFalse(
            self._batch_importer_allows("magento.product.product", "ZZ-UNKNOWN")
        )
        with trap_jobs() as trap:
            self.binding.sync_from_magento()
            trap.assert_jobs_count(1)

    def test_export_only_new_products(self):
        self.backend.product_export_strategy = "create"
        with trap_jobs() as trap:
            # The one already in Magento is left out.
            (self.binding | self.new_binding).sync_to_magento()
            trap.assert_jobs_count(1)
            trap.assert_enqueued_job(
                self.new_binding.export_record, args=(), kwargs={}
            )
        with self.assertRaises(UserError):
            self.binding.sync_to_magento()
        with self.assertRaises(UserError):
            self.template_binding.sync_to_magento()

    def test_export_only_existing_products(self):
        self.backend.product_export_strategy = "update"
        with trap_jobs() as trap:
            (self.binding | self.new_binding).sync_to_magento()
            self.template_binding.sync_to_magento()
            trap.assert_jobs_count(2)
        with self.assertRaises(UserError):
            self.new_binding.sync_to_magento()
