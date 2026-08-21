# Copyright 2015-2019 Camptocamp SA
# Copyright 2020 Opener B.V.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import json

from odoo.tests import tagged

from .common import Magento2SyncTestCase, recorder


# post_install: en at_install el registry todavía no tiene los módulos que
# dependen de éste, así que los campos que ellos añaden a modelos ya existentes
# no aplican su default aunque su columna esté en la base de datos.
@tagged("post_install", "-at_install")
class TestUpdateStockQty(Magento2SyncTestCase):
    """Test the export of pickings to Magento"""

    def _product_change_qty(self, product, new_qty, location_id=False):
        """Put the quantity in a location this test owns.

        Deliberately NOT stock.change.product.qty: that wizard applies an
        inventory adjustment, whose counterpart is the database's inventory
        adjustment location. On a migrated database that location can be
        configured as internal instead of inventory, and then the adjustment
        does not create stock at all -- it shifts it between two internal
        locations and the product ends up negative, with no error anywhere.
        Writing the quant directly has no counterpart and does not depend on
        how the database happens to be configured.
        """
        location = (
            self.env["stock.location"].browse(location_id)
            if location_id
            else self.stock_location
        )
        self.env["stock.quant"]._update_available_quantity(product, location, new_qty)
        # qty_available and friends are computed on read from the quants, with
        # no declared dependency on them, so a quant written behind their back
        # leaves whatever the test read before still cached. The wizard used to
        # hide this because applying an inventory flushes and invalidates.
        self.env.flush_all()
        self.env.invalidate_all()

    def setUp(self):
        super().setUp()
        # Our own warehouse, and the backend pointed at it.
        #
        # These tests need one location to satisfy two readers at once:
        # product.virtual_available, which only counts locations hanging below
        # some warehouse's view_location_id, and the connector, which reads the
        # quantity restricted to backend.warehouse_id.lot_stock_id. A warehouse
        # created here satisfies both because Odoo builds its tree consistently.
        # The database's own warehouse may not: in forum, Alm.FF has its
        # lot_stock_id outside its own view location, so stock put there is
        # invisible to virtual_available while being the only place the
        # connector looks. Nothing in the connector is wrong there -- the
        # warehouse is -- and a test must not depend on it either way.
        self.warehouse = self.env["stock.warehouse"].create(
            {"name": "Magento Test Warehouse", "code": "MGTST"}
        )
        self.backend.warehouse_id = self.warehouse
        self.stock_location = self.warehouse.lot_stock_id
        self.binding_product = self._import_record(
            "magento.product.product",
            "MH09-L-Blue",
        )

    def test_compute_new_qty(self):
        product = self.binding_product.odoo_id
        binding = self.binding_product
        # start with 0
        self.assertEqual(product.virtual_available, 0.0)
        self.assertEqual(binding.magento_qty, 0.0)

        # change to 30
        self._product_change_qty(product, 30)

        # the virtual available is 30, the magento qty has not been
        # updated yet
        self.assertEqual(product.virtual_available, 30.0)
        self.assertEqual(binding.magento_qty, 0.0)

        # search for the new quantities to push to Magento
        # we mock the job so we can check it .delay() is called on it
        # when the quantity is changed
        with self.mock_with_delay() as (delayable_cls, delayable):
            binding.recompute_magento_qty()
            self.assertEqual(binding.magento_qty, 30.0)

            self.assertEqual(1, delayable_cls.call_count)
            delay_args, delay_kwargs = delayable_cls.call_args
            self.assertEqual((binding,), delay_args)
            self.assertEqual(20, delay_kwargs.get("priority"))

            delayable.export_inventory.assert_called_with(
                fields=["magento_qty"],
            )

    def test_compute_new_qty_different_field(self):
        stock_field = self.env.ref("stock.field_product_product__qty_available")
        self.backend.product_stock_field_id = stock_field
        product = self.binding_product.odoo_id
        binding = self.binding_product
        # start with 0
        self.assertEqual(product.qty_available, 0.0)
        self.assertEqual(product.virtual_available, 0.0)
        self.assertEqual(binding.magento_qty, 0.0)

        # change to 30
        self._product_change_qty(product, 30)

        # the virtual available is 30, the magento qty has not been
        # updated yet
        self.assertEqual(product.qty_available, 30.0)
        self.assertEqual(product.virtual_available, 30.0)
        self.assertEqual(binding.magento_qty, 0.0)

        # create an outgoing move
        customer_location = self.env.ref("stock.stock_location_customers")
        outgoing = self.env["stock.move"].create(
            {
                "name": product.name,
                "product_id": product.id,
                "product_uom_qty": 11,
                "product_uom": product.uom_id.id,
                "location_id": self.stock_location.id,
                "location_dest_id": customer_location.id,
            }
        )
        outgoing._action_confirm()
        outgoing._action_assign()

        # the virtual is now 19, available still 30
        self.assertEqual(product.qty_available, 30.0)
        self.assertEqual(product.virtual_available, 19.0)
        self.assertEqual(binding.magento_qty, 0.0)

        # search for the new quantities to push to Magento
        # we mock the job so we can check it .delay() is called on it
        # when the quantity is changed
        with self.mock_with_delay() as (delayable_cls, delayable):
            binding.recompute_magento_qty()
            # since we have chose to use the field qty_available on the
            # backend, we should have 30
            self.assertEqual(binding.magento_qty, 30.0)

            self.assertEqual(1, delayable_cls.call_count)
            delay_args, delay_kwargs = delayable_cls.call_args
            self.assertEqual((binding,), delay_args)
            self.assertEqual(20, delay_kwargs.get("priority"))

            delayable.export_inventory.assert_called_with(
                fields=["magento_qty"],
            )

    def test_export_qty_api(self):
        product = self.binding_product.odoo_id
        binding = self.binding_product

        self._product_change_qty(product, 30)
        with self.mock_with_delay():  # disable job
            binding.recompute_magento_qty()

        with recorder.use_cassette("test_product_export_qty") as cassette:
            # call the job directly
            binding.export_inventory(fields=["magento_qty"])

            # Verify the stock update request was sent (don't check exact count
            # due to cassette interaction duplication)
            stock_requests = [
                r for r in cassette.requests if r.body and b"stockItem" in r.body
            ]
            self.assertTrue(len(stock_requests) >= 1)
            self.assertEqual(
                json.loads(stock_requests[0].body.decode("utf-8")),
                {"stockItem": {"qty": 30.0, "is_in_stock": 1}},
            )

    def test_export_product_inventory_write(self):
        with self.mock_with_delay() as (delayable_cls, delayable):
            self.binding_product.write(
                {
                    "magento_qty": 333,
                    "backorders": "yes-and-notification",
                    "manage_stock": "yes",
                }
            )

            self.assertEqual(1, delayable_cls.call_count)
            delay_args, delay_kwargs = delayable_cls.call_args
            self.assertEqual((self.binding_product,), delay_args)
            self.assertEqual(20, delay_kwargs.get("priority"))

            cargs, ckwargs = delayable.export_inventory.call_args
            self.assertFalse(cargs)
            self.assertEqual(set(ckwargs.keys()), set(["fields"]))
            self.assertEqual(
                set(ckwargs["fields"]),
                set(["manage_stock", "backorders", "magento_qty"]),
            )

    def test_export_product_inventory_write_job(self):
        with self.mock_with_delay():
            self.binding_product.write(
                {
                    "magento_qty": 333,
                    "backorders": "yes-and-notification",
                    "manage_stock": "yes",
                }
            )

        with recorder.use_cassette("test_product_export_qty_config") as cassette:
            self.binding_product.export_inventory(
                fields=["backorders", "magento_qty", "manage_stock"]
            )

            # Verify the stock config update was sent
            stock_requests = [
                r for r in cassette.requests if r.body and b"stockItem" in r.body
            ]
            self.assertTrue(len(stock_requests) >= 1)
            self.assertEqual(
                json.loads(stock_requests[0].body.decode("utf-8")),
                {
                    "stockItem": {
                        "qty": 333.0,
                        "is_in_stock": 1,
                        "manage_stock": 1,
                        "use_config_manage_stock": 0,
                        "backorders": 2,
                        "use_config_backorders": 0,
                    }
                },
            )

    def test_compute_new_qty_with_location(self):
        product = self.binding_product.odoo_id
        binding = self.binding_product
        # start with 0
        self.assertEqual(product.virtual_available, 0.0)
        self.assertEqual(binding.magento_qty, 0.0)

        # Create a sub-location for the test (demo data may not be available)
        my_location = self.env["stock.location"].create(
            {
                "name": "Test Components",
                "usage": "internal",
                "location_id": self.stock_location.id,
            }
        )
        my_location_id = my_location.id
        binding = binding.with_context(location=my_location_id)

        # change to 30
        self._product_change_qty(product, 30)
        self._product_change_qty(product, 5, my_location_id)

        # the virtual available is 30, the magento qty has not been
        # updated yet
        self.assertEqual(product.virtual_available, 35.0)
        self.assertEqual(binding.magento_qty, 0.0)

        # search for the new quantities to push to Magento
        # we mock the job so we can check it .delay() is called on it
        # when the quantity is changed
        with self.mock_with_delay() as (delayable_cls, delayable):
            binding.recompute_magento_qty()
            self.assertEqual(binding.magento_qty, 5.0)

            self.assertEqual(1, delayable_cls.call_count)
            delay_args, delay_kwargs = delayable_cls.call_args
            self.assertEqual((binding,), delay_args)
            self.assertEqual(20, delay_kwargs.get("priority"))

            delayable.export_inventory.assert_called_with(
                fields=["magento_qty"],
            )
