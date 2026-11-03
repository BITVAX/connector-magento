# Copyright 2026 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
Deleting products in Magento is for the connector managers only.

The buttons and actions that export, import or delete products are only shown
to them. The server refuses the delete action to the other users, and the
deletion itself when it is called without its button. Export and import are
not refused: the "Add Magento" wizard and the smart button of the product run
them on behalf of any user.
"""

from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from odoo.addons.queue_job.tests.common import trap_jobs

from .common import Magento2TestCase

MANAGER_GROUP = "connector.group_connector_manager"


@tagged("post_install", "-at_install")
class TestProductButtons(Magento2TestCase):
    def setUp(self):
        super().setUp()
        self.attr_set = self.env["magento.product.attribute.set"].create(
            {"name": "ZZ Default", "backend_id": self.backend.id, "external_id": "4"}
        )
        self.product = self.env["product.product"].create(
            {"name": "ZZ Buttons", "default_code": "ZZ-BUTTONS", "type": "consu"}
        )
        self.binding = self.create_binding_no_export(
            "magento.product.product",
            self.product,
            "ZZ-BUTTONS",
            magento_internal_id="9101",
            attribute_set_id=self.attr_set.id,
        )
        # Writes the Magento product bindings, which was all the server actions
        # asked for, but does not manage the connector.
        self.sale_manager = new_test_user(
            self.env,
            login="zz_magento_sale_manager",
            groups="base.group_user,sales_team.group_sale_manager",
        )
        self.connector_manager = new_test_user(
            self.env,
            login="zz_magento_connector_manager",
            groups="base.group_user,%s" % MANAGER_GROUP,
        )

    def _run_delete_action(self, user):
        action = self.env.ref(
            "connector_magento.action_magento_product_delete_from_magento"
        )
        return (
            action.with_user(user)
            .with_context(
                active_model="magento.product.product",
                active_ids=self.binding.ids,
                active_id=self.binding.id,
            )
            .run()
        )

    def test_only_connector_managers_run_the_delete_action(self):
        # The sales manager writes the bindings, which is all the action
        # asked for before.
        with self.assertRaises(AccessError):
            self._run_delete_action(self.sale_manager)
        with trap_jobs() as trap:
            self._run_delete_action(self.connector_manager)
            trap.assert_jobs_count(1)

    def test_only_connector_managers_delete_in_magento(self):
        with self.assertRaises(AccessError):
            self.binding.with_user(self.sale_manager).delete_from_magento()
        with trap_jobs() as trap:
            self.binding.with_user(self.connector_manager).delete_from_magento()
            trap.assert_jobs_count(1)

    def test_other_users_still_export_and_import_through_the_code(self):
        binding = self.binding.with_user(self.sale_manager)
        with trap_jobs() as trap:
            binding.sync_to_magento()
            binding.sync_from_magento()
            trap.assert_jobs_count(2)
