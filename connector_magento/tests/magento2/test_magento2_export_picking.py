# Copyright 2014-2019 Camptocamp SA
# Copyright 2020 Opener B.V.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import json

from .common import Magento2SyncTestCase, recorder


class TestExportPicking(Magento2SyncTestCase):
    """Test the export of pickings to Magento"""

    def setUp(self):
        super().setUp()
        # import a sales order
        self.order_binding = self._import_record(
            "magento.sale.order",
            "12",
        )
        self.order_binding.ignore_exception = True
        # generate sale's picking
        self.order_binding.odoo_id.action_confirm()
        # Create inventory for add stock qty to lines
        # With this commit https://goo.gl/fRTLM3 the moves that where
        # force-assigned are not transferred in the picking
        # Odoo 16: stock.inventory removed, use stock.quant directly
        stock_location = self.env.ref("stock.stock_location_stock")
        for line in self.order_binding.odoo_id.order_line:
            if line.product_id.type == "product":
                self.env["stock.quant"]._update_available_quantity(
                    line.product_id, stock_location, line.product_uom_qty
                )
        self.picking = self.order_binding.picking_ids
        self.assertEqual(len(self.picking), 1)
        magento_shop = self.picking.sale_id.magento_bind_ids[0].store_id
        magento_shop.send_picking_done_mail = True

    def _validate_picking(self):
        """Validate picking — Odoo 16 compatible."""
        self.picking.action_assign()
        for move in self.picking.move_ids:
            move.quantity_done = move.product_uom_qty
        self.picking.button_validate()

    def test_export_complete_picking_trigger(self):
        """Trigger export of a complete picking"""
        with self.mock_with_delay() as (delayable_cls, delayable):
            self._validate_picking()
            self.assertEqual(self.picking.state, "done")

            picking_binding = self.env["magento.stock.picking"].search(
                [
                    ("odoo_id", "=", self.picking.id),
                    ("backend_id", "=", self.backend.id),
                ],
            )
            self.assertEqual(1, len(picking_binding))
            self.assertEqual("complete", picking_binding.picking_method)

            self.assertEqual(1, delayable_cls.call_count)
            delay_args, delay_kwargs = delayable_cls.call_args
            self.assertEqual((picking_binding,), delay_args)

            delayable.export_picking_done.assert_called_with(with_tracking=False)

    def test_export_complete_picking_job(self):
        """Exporting a complete picking"""
        with self.mock_with_delay():
            self._validate_picking()
            self.assertEqual(self.picking.state, "done")
            picking_binding = self.env["magento.stock.picking"].search(
                [
                    ("odoo_id", "=", self.picking.id),
                    ("backend_id", "=", self.backend.id),
                ],
            )
            self.assertEqual(1, len(picking_binding))

        with recorder.use_cassette("test_export_picking_complete") as cassette:
            picking_binding.export_picking_done(with_tracking=False)

        ship_requests = [r for r in cassette.requests if r.body and b"items" in r.body]
        self.assertTrue(len(ship_requests) >= 1)
        self.assertEqual(
            cassette.requests[0].uri, "http://magento/index.php/rest/V1/order/12/ship"
        )
        self.assertDictEqual(
            json.loads(cassette.requests[0].body.decode("utf-8")),
            {
                "items": [
                    {"order_item_id": "24", "qty": 1.0},
                    {"order_item_id": "25", "qty": 1.0},
                ]
            },
        )

        # Check that we have received and bound the magento ID
        self.assertEqual(picking_binding.external_id, "3")

    def test_export_partial_picking_trigger(self):
        """Trigger export of a partial picking"""
        # Prepare a partial picking
        # The sale order contains 2 lines with 1 product each
        self.picking.action_assign()
        self.picking.move_ids[0].quantity_done = 1
        self.picking.move_ids[1].quantity_done = 0
        # Remove reservation for line index 1
        self.picking.move_ids[1].move_line_ids.unlink()

        with self.mock_with_delay() as (delayable_cls, delayable):
            # Validate partial — Odoo 16 returns backorder wizard via context
            backorder_action = self.picking.button_validate()
            if (
                isinstance(backorder_action, dict)
                and backorder_action.get("res_model") == "stock.backorder.confirmation"
            ):
                ctx = backorder_action.get("context", {})
                wizard = (
                    self.env["stock.backorder.confirmation"]
                    .with_context(**ctx)
                    .create({"pick_ids": [(4, self.picking.id)]})
                )
                wizard.process()

            self.assertEqual(self.picking.state, "done")

            picking_binding = self.env["magento.stock.picking"].search(
                [
                    ("odoo_id", "=", self.picking.id),
                    ("backend_id", "=", self.backend.id),
                ],
            )
            self.assertEqual(1, len(picking_binding))
            self.assertEqual("partial", picking_binding.picking_method)

            self.assertEqual(1, delayable_cls.call_count)
            delay_args, delay_kwargs = delayable_cls.call_args
            self.assertEqual((picking_binding,), delay_args)

            delayable.export_picking_done.assert_called_with(with_tracking=False)

    def test_export_partial_picking_job(self):
        """Exporting a partial picking"""
        self.picking.action_assign()
        self.picking.move_ids[0].quantity_done = 1
        self.picking.move_ids[1].quantity_done = 0

        with self.mock_with_delay():
            backorder_action = self.picking.button_validate()
            if (
                isinstance(backorder_action, dict)
                and backorder_action.get("res_model") == "stock.backorder.confirmation"
            ):
                ctx = backorder_action.get("context", {})
                self.env["stock.backorder.confirmation"].with_context(**ctx).create(
                    {"pick_ids": [(4, self.picking.id)]}
                ).process()
            self.assertEqual(self.picking.state, "done")

            picking_binding = self.env["magento.stock.picking"].search(
                [
                    ("odoo_id", "=", self.picking.id),
                    ("backend_id", "=", self.backend.id),
                ],
            )
            self.assertEqual(1, len(picking_binding))

        with recorder.use_cassette("test_export_picking_partial") as cassette:
            picking_binding.export_picking_done(with_tracking=False)

        ship_requests = [r for r in cassette.requests if r.body and b"items" in r.body]
        self.assertTrue(len(ship_requests) >= 1)
        self.assertDictEqual(
            json.loads(cassette.requests[0].body.decode("utf-8")),
            {
                "items": [
                    {"order_item_id": "24", "qty": 1.0},
                ]
            },
        )

        # Check that we have received and bound the magento ID
        self.assertEqual(picking_binding.external_id, "5")

    def test_export_tracking_after_done_trigger(self):
        """Trigger export of a tracking number"""
        with self.mock_with_delay():
            self._validate_picking()
            self.assertEqual(self.picking.state, "done")

        picking_binding = self.env["magento.stock.picking"].search(
            [("odoo_id", "=", self.picking.id), ("backend_id", "=", self.backend.id)],
        )
        self.assertEqual(1, len(picking_binding))

        with self.mock_with_delay() as (delayable_cls, delayable):
            self.picking.carrier_tracking_ref = "XYZ"

            self.assertEqual(1, delayable_cls.call_count)
            delay_args, delay_kwargs = delayable_cls.call_args
            self.assertEqual((picking_binding,), delay_args)

            delayable.export_tracking_number.assert_called_with()

    def test_export_tracking_after_done_job(self):
        """Job export of a tracking number"""
        with self.mock_with_delay():
            self._validate_picking()
        self.assertEqual(self.picking.state, "done")
        self.picking.carrier_tracking_ref = "XYZ"
        self.order_binding.carrier_id.magento_tracking_title = "Your shipment"

        picking_binding = self.env["magento.stock.picking"].search(
            [("odoo_id", "=", self.picking.id), ("backend_id", "=", self.backend.id)],
        )
        self.assertEqual(1, len(picking_binding))
        picking_binding.external_id = "3"

        with recorder.use_cassette("test_export_tracking_number") as cassette:
            picking_binding.export_tracking_number()

        track_requests = [r for r in cassette.requests if r.body and b"track" in r.body]
        self.assertTrue(len(track_requests) >= 1)
        self.assertEqual(
            cassette.requests[0].uri, "http://magento/index.php/rest/V1/shipment/track"
        )
        self.assertEqual(
            json.loads(cassette.requests[0].body.decode("utf-8")),
            {
                "entity": {
                    "order_id": "12",
                    "parent_id": "3",
                    "weight": 0,
                    "qty": 1,
                    "description": "WH/OUT/00082",
                    "track_number": "XYZ",
                    "title": "Your shipment",
                    "carrier_code": "tablerate",
                }
            },
        )
