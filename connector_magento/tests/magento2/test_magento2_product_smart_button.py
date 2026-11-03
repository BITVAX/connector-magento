# Copyright 2026 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
The Magento button of a product, for a user who does not manage the connector.

On a backend that only exports products it exports, on one that only imports
them it imports, without opening the bindings or the wizard; a product not in
Magento yet is linked to the default backend in the same click. On a backend
that does both, and for the connector managers, the button keeps opening the
bindings and the wizard.
"""

from unittest import mock

from odoo.tests import new_test_user, tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import Magento2TestCase


@tagged("post_install", "-at_install")
class TestProductSmartButton(Magento2TestCase):
    def setUp(self):
        super().setUp()
        # The wizard and the bindings need an attribute set of the backend.
        self.attr_set = self.env["magento.product.attribute.set"].create(
            {"name": "ZZ Default", "backend_id": self.backend.id, "external_id": "4"}
        )
        self.backend.product_import_strategy = "never"
        self.product = self.env["product.product"].create(
            {"name": "ZZ Smart", "default_code": "ZZ-SMART", "type": "consu"}
        )
        self.salesman = new_test_user(
            self.env,
            login="zz_magento_salesman",
            groups="base.group_user,sales_team.group_sale_salesman",
        )
        self.manager = new_test_user(
            self.env,
            login="zz_magento_manager",
            groups="base.group_user,connector.group_connector_manager",
        )
        # The default backend of the wizard is the first one: on a copy of a
        # production database, the backend of the shop.
        wizard = type(self.env["connector_magento.add_backend.wizard"])
        patcher = mock.patch.object(
            wizard, "get_default_backend", return_value=self.backend
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _bind(self):
        return self.create_binding_no_export(
            "magento.product.product",
            self.product,
            "ZZ-SMART",
            magento_internal_id="9201",
            attribute_set_id=self.attr_set.id,
        )

    def _configurable(self):
        attribute = self.env["product.attribute"].create(
            {
                "name": "ZZ Smart Size",
                "create_variant": "always",
                "value_ids": [(0, 0, {"name": "S"}), (0, 0, {"name": "M"})],
            }
        )
        template = self.env["product.template"].create(
            {
                "name": "ZZ Smart Configurable",
                "type": "consu",
                "attribute_line_ids": [
                    (
                        0,
                        0,
                        {
                            "attribute_id": attribute.id,
                            "value_ids": [(6, 0, attribute.value_ids.ids)],
                        },
                    )
                ],
            }
        )
        binding = self.create_binding_no_export(
            "magento.product.template",
            template,
            "ZZ-SMART-CONF",
            code_prefix="ZZ-SMART-CONF",
            magento_id=9202,
            attribute_set_id=self.attr_set.id,
        )
        return template, binding

    @staticmethod
    def _methods(trap):
        return [job.method_name for job in trap.enqueued_jobs]

    def test_a_user_exports_a_product_already_in_magento(self):
        self._bind()
        with trap_jobs() as trap:
            action = self.product.with_user(
                self.salesman
            ).action_view_magento_bindings()
        self.assertEqual(self._methods(trap), ["export_record"])
        self.assertEqual(action["tag"], "display_notification")

    def test_a_user_exports_a_configurable_already_in_magento(self):
        template, binding = self._configurable()
        with trap_jobs() as trap:
            action = template.with_user(self.salesman).action_view_magento_bindings()
        self.assertEqual(self._methods(trap), ["export_record"])
        self.assertEqual(trap.enqueued_jobs[0].recordset, binding)
        self.assertEqual(action["tag"], "display_notification")

    def test_a_user_imports_when_the_backend_only_imports(self):
        self.backend.write(
            {"product_import_strategy": "always", "product_export_strategy": "never"}
        )
        self._bind()
        with trap_jobs() as trap:
            self.product.with_user(self.salesman).action_view_magento_bindings()
        self.assertEqual(self._methods(trap), ["import_record"])

    def test_a_backend_that_imports_and_exports_keeps_the_bindings(self):
        self.backend.product_import_strategy = "always"
        binding = self._bind()
        with trap_jobs() as trap:
            action = self.product.with_user(
                self.salesman
            ).action_view_magento_bindings()
        self.assertFalse(trap.enqueued_jobs)
        self.assertEqual(action["res_model"], "magento.product.product")
        self.assertEqual(action["res_id"], binding.id)

    def test_a_connector_manager_keeps_the_bindings(self):
        binding = self._bind()
        with trap_jobs() as trap:
            action = self.product.with_user(self.manager).action_view_magento_bindings()
        self.assertFalse(trap.enqueued_jobs)
        self.assertEqual(action["res_id"], binding.id)

    def test_a_user_adds_a_product_to_magento_in_one_click(self):
        with trap_jobs() as trap:
            action = self.product.with_user(self.salesman).action_add_magento_backend()
        binding = self.product.magento_bind_ids
        self.assertEqual(binding.backend_id, self.backend)
        self.assertEqual(self._methods(trap), ["export_record"])
        self.assertEqual(action["tag"], "display_notification")

    def test_a_salesman_reads_the_bindings_of_a_configurable(self):
        """The product page of a configurable reads its Magento bindings: a
        salesman could not open it, only the connector managers read them."""
        template, binding = self._configurable()
        [values] = template.with_user(self.salesman).web_read(
            {
                "magento_bind_ids": {
                    "fields": {
                        "external_id": {},
                        "backend_id": {"fields": {"display_name": {}}},
                    }
                }
            }
        )
        self.assertEqual(
            [b["external_id"] for b in values["magento_bind_ids"]], ["ZZ-SMART-CONF"]
        )

    def test_a_connector_manager_adds_through_the_wizard(self):
        action = self.product.with_user(self.manager).action_add_magento_backend()
        self.assertEqual(action["res_model"], "connector_magento.add_backend.wizard")
        self.assertFalse(self.product.magento_bind_ids)
