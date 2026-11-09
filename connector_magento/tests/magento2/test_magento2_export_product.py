# Copyright 2024 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
Tests for exporting products to Magento (create and update).
"""

import json

from odoo.tests import tagged

from .common import Magento2SyncTestCase, recorder

# Smallest valid PNG (1x1 transparent), base64-encoded, so that python-magic
# detects a real mimetype from the decoded buffer.
PNG_1X1_B64 = (
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    b"hKmMIQAAAABJRU5ErkJggg=="
)


def _add_image(env, owner, name="Test image"):
    """Give a product template an image, through product_multi_image's own API.

    Writing image_1920 goes through _inverse_image_1920, which creates the
    base_multi_image.image AND keeps the stored image_1920 consistent with it.

    Creating that row directly instead does not work: image_ids is a One2many
    whose inverse (owner_id) is a plain Integer, so the ORM has no relational
    link to notice the new child -- neither image_ids nor the stored compute
    that reads it get invalidated. The export mapper checks image_ids but then
    reads the template's own image_1920, so a row created behind the module's
    back yields an empty gallery and a failure far from its cause.

    Both calls below are needed, and for different reasons: flush_all() makes
    the inverse actually persist the row, and invalidate_all() drops the stale
    One2many so the next read goes to the database. Flushing alone leaves
    image_ids serving its cached empty value; invalidating alone leaves the
    stored image_1920 unrefreshed.
    """
    owner.image_1920 = PNG_1X1_B64
    env.flush_all()
    env.invalidate_all()
    assert owner.image_ids, "the owner does not see the image just created"
    assert owner.image_1920, "the stored image_1920 was not refreshed"
    if name:
        owner.image_ids[:1].name = name
        env.flush_all()
    return owner.image_ids[:1]


def _set_backend_pricelist(env, backend, item):
    """Give the backend a pricelist with one fixed-price rule."""
    backend.pricelist_id = env["product.pricelist"].create(
        {
            "name": "ZZ Magento prices",
            "item_ids": [(0, 0, dict(item, compute_price="fixed"))],
        }
    )


# post_install: estas pruebas necesitan el registry COMPLETO. Las pruebas
# at_install corren dentro de load_module_graph, justo después de cargar este
# módulo, cuando los módulos que dependen de él todavía no están cargados; sus
# extensiones de modelo (campos con default incluidos) aún no existen, aunque
# sus columnas sí estén ya en la base de datos.
@tagged("post_install", "-at_install")
class TestExportProduct(Magento2SyncTestCase):
    def setUp(self):
        super().setUp()
        # Import attribute set so we have it available
        self._import_record("magento.product.attribute.set", 4, cassette=True)
        self.attr_set = self.env["magento.product.attribute.set"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("external_id", "=", "4"),
            ],
            limit=1,
        )

    def _create_product_binding(self, name, default_code, **extra):
        """Create a product with a magento binding ready for export."""
        product = self.env["product.product"].create(
            {
                "name": name,
                "default_code": default_code,
                "type": "consu",
                "is_storable": True,
                "list_price": extra.pop("list_price", 49.99),
                "weight": extra.pop("weight", 1.0),
            }
        )
        binding_vals = {
            "odoo_id": product.id,
            "backend_id": self.backend.id,
            "attribute_set_id": self.attr_set.id,
            "magento_status": "1",
            "magento_visibility": "4",
            "product_type": "simple",
        }
        binding_vals.update(extra)
        binding = self.env["magento.product.product"].create(binding_vals)
        return binding

    def test_export_product_create_job(self):
        """Exporting a new product calls POST to Magento API."""
        binding = self._create_product_binding(
            "Test Export Product", "TEST-EXPORT-SIMPLE"
        )

        with recorder.use_cassette("test_export_product_create") as cassette:
            with self.mock_with_delay():
                binding.export_record()

        # Verify a POST was made to /products
        post_requests = [
            r for r in cassette.requests if r.method == "POST" and "/products" in r.uri
        ]
        self.assertTrue(len(post_requests) >= 1, "Should have made a POST to /products")

        # Binding should now have an external_id (SKU)
        self.assertEqual(binding.external_id, "TEST-EXPORT-SIMPLE")

    def test_export_product_update_job(self):
        """Exporting an existing product calls PUT to Magento API."""
        binding = self._create_product_binding(
            "Updated Export Product",
            "TEST-EXPORT-UPDATE",
            external_id="TEST-EXPORT-UPDATE",
        )
        binding.magento_internal_id = 5002

        with recorder.use_cassette("test_export_product_update") as cassette:
            binding.export_record()

        # Verify a PUT was made
        put_requests = [
            r for r in cassette.requests if r.method == "PUT" and "/products/" in r.uri
        ]
        self.assertTrue(
            len(put_requests) >= 1, "Should have made a PUT to /products/{sku}"
        )

    def test_export_product_mapper_name_simple(self):
        """Export mapper generates correct name for simple product."""
        binding = self._create_product_binding("Simple Product Name", "SIMPLE-SKU")

        with self.backend.work_on("magento.product.product") as work:
            mapper = work.component(usage="export.mapper")
            map_record = mapper.map_record(binding)
            data = map_record.values()

        self.assertEqual(data.get("name"), "Simple Product Name")
        self.assertEqual(data.get("sku"), binding.external_id or "SIMPLE-SKU")
        self.assertEqual(data.get("status"), "1")
        self.assertIn("attributeSetId", data)
        self.assertIn("weight", data)

    def test_export_product_mapper_image_is_json_serializable(self):
        """Image payload must be JSON-serializable (image_1920 returns bytes)."""
        binding = self._create_product_binding("Image Test", "IMAGE-SKU")
        _add_image(self.env, binding.odoo_id.product_tmpl_id)

        with self.backend.work_on("magento.product.product") as work:
            mapper = work.component(usage="export.mapper")
            data = mapper.map_record(binding).values()

        entries = data.get("media_gallery_entries")
        self.assertTrue(entries, "The image should reach media_gallery_entries")
        self.assertIsInstance(
            entries[0]["content"]["base64_encoded_data"],
            str,
            "base64_encoded_data must be str, not bytes",
        )
        # This is what requests(json=...) does and what used to raise
        # TypeError: Object of type bytes is not JSON serializable
        json.dumps(data)

    def test_export_product_mapper_website_ids(self):
        """Export mapper includes website_ids in extension_attributes."""
        binding = self._create_product_binding("Website Test", "WEBSITE-SKU")

        with self.backend.work_on("magento.product.product") as work:
            mapper = work.component(usage="export.mapper")
            map_record = mapper.map_record(binding)
            data = map_record.values()

        ext_attrs = data.get("extension_attributes", {})
        self.assertIn(
            "website_ids", ext_attrs, "extension_attributes should contain website_ids"
        )

    def test_export_product_mapper_price_from_the_backend_pricelist(self):
        """With a pricelist on the backend, the price comes from it.

        Odoo 18 has neither ``product.pricelist.discount_policy`` nor a
        ``price`` field computed from the ``pricelist`` context: the mapper
        raised AttributeError as soon as the backend had a pricelist.
        """
        binding = self._create_product_binding(
            "Pricelist Product", "PRICELIST-SKU", list_price=49.99
        )
        _set_backend_pricelist(
            self.env,
            self.backend,
            {
                "applied_on": "0_product_variant",
                "product_id": binding.odoo_id.id,
                "fixed_price": 39.0,
            },
        )

        with self.backend.work_on("magento.product.product") as work:
            mapper = work.component(usage="export.mapper")
            data = mapper.map_record(binding).values()

        self.assertEqual(data["price"], 39.0)

    def test_export_product_mapper_price_without_pricelist(self):
        """Without a pricelist on the backend, the sales price of the variant."""
        binding = self._create_product_binding(
            "No Pricelist Product", "NO-PRICELIST-SKU"
        )
        # product_variant_sale_price computes list_price on the variant, without
        # an inverse: the sales price of a variant is written through lst_price.
        binding.odoo_id.lst_price = 49.99

        with self.backend.work_on("magento.product.product") as work:
            mapper = work.component(usage="export.mapper")
            data = mapper.map_record(binding).values()

        self.assertEqual(data["price"], 49.99)

    def _special_price(self, binding):
        """The special_price that the export mapper sends for ``binding``."""
        with self.backend.work_on("magento.product.product") as work:
            mapper = work.component(usage="export.mapper")
            data = mapper.map_record(binding).values()
        values = [
            attribute["value"]
            for attribute in data["custom_attributes"]
            if attribute["attribute_code"] == "special_price"
        ]
        self.assertEqual(len(values), 1, "special_price must go exactly once")
        return values[0]

    def test_export_product_mapper_special_price_only_with_a_rule(self):
        """The special price of a variant comes from the special price
        pricelist of the backend only when one of its rules applies to it.

        Without a rule the pricelist gives the sales price, which is no offer,
        and the variant went with its sales price as special_price. It must go
        empty, so that Magento clears it.
        """
        on_sale = self._create_product_binding("On Sale", "ON-SALE-SKU")
        not_on_sale = self._create_product_binding("Not On Sale", "NOT-ON-SALE-SKU")
        not_on_sale.odoo_id.lst_price = 59.99
        self.backend.special_price_pricelist_id = self.env["product.pricelist"].create(
            {
                "name": "ZZ Magento special prices",
                "item_ids": [
                    (
                        0,
                        0,
                        {
                            "applied_on": "0_product_variant",
                            "product_id": on_sale.odoo_id.id,
                            "compute_price": "fixed",
                            "fixed_price": 39.0,
                        },
                    )
                ],
            }
        )

        self.assertEqual(self._special_price(on_sale), "39.0")
        self.assertIsNone(self._special_price(not_on_sale))

    def _in_magento(self, name, sku, magento_id):
        """The template of a simple product already in Magento as ``sku``."""
        binding = self._create_product_binding(
            name, sku, external_id=sku, magento_internal_id=magento_id
        )
        return binding.odoo_id.product_tmpl_id

    def test_export_product_mapper_linked_products(self):
        """A simple product sends its related, up-sell and cross-sell (its
        optional) products, by their SKU; those not in Magento are left out."""
        binding = self._create_product_binding(
            "Linking Product", "LINKING-SKU", external_id="LINKING-SKU"
        )
        not_in_magento = self.env["product.template"].create({"name": "Not There"})
        related = self._in_magento("Related", "RELATED-SKU", "9301")
        binding.odoo_id.product_tmpl_id.write(
            {
                "magento_related_product_ids": [
                    (6, 0, [related.id, not_in_magento.id])
                ],
                "magento_upsell_product_ids": [
                    (6, 0, self._in_magento("Upsell", "UPSELL-SKU", "9302").ids)
                ],
                "optional_product_ids": [
                    (6, 0, self._in_magento("Cross", "CROSS-SKU", "9303").ids)
                ],
            }
        )

        with self.backend.work_on("magento.product.product") as work:
            mapper = work.component(usage="export.mapper")
            links = mapper.map_record(binding).values()["product_links"]

        self.assertEqual(
            [
                (link["sku"], link["link_type"], link["linked_product_sku"])
                for link in links
            ],
            [
                ("LINKING-SKU", "related", "RELATED-SKU"),
                ("LINKING-SKU", "upsell", "UPSELL-SKU"),
                ("LINKING-SKU", "crosssell", "CROSS-SKU"),
            ],
        )

    def test_export_product_mapper_without_linked_products_clears_them(self):
        """Odoo is the master of the linked products: a product without any
        sends an empty list, and Magento removes the ones it had."""
        binding = self._create_product_binding(
            "No Links", "NO-LINKS-SKU", external_id="NO-LINKS-SKU"
        )

        with self.backend.work_on("magento.product.product") as work:
            mapper = work.component(usage="export.mapper")
            data = mapper.map_record(binding).values()

        self.assertEqual(data["product_links"], [])


@tagged("post_install", "-at_install")
class TestExportProductTemplate(Magento2SyncTestCase):
    """Tests for exporting configurable product templates with variants."""

    def setUp(self):
        super().setUp()
        self._import_record("magento.product.attribute.set", 4, cassette=True)
        self.attr_set = self.env["magento.product.attribute.set"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("external_id", "=", "4"),
            ],
            limit=1,
        )

    def _create_configurable_template(self):
        """Create a product template with 2 variants and magento binding."""
        # Get or create Color attribute
        color_attr = self.env["product.attribute"].search(
            [("name", "=", "Color")], limit=1
        )
        if not color_attr:
            color_attr = self.env["product.attribute"].create(
                {"name": "Color", "create_variant": "always"}
            )
        red = self.env["product.attribute.value"].search(
            [("name", "=", "Red"), ("attribute_id", "=", color_attr.id)], limit=1
        )
        if not red:
            red = self.env["product.attribute.value"].create(
                {"name": "Red", "attribute_id": color_attr.id}
            )
        blue = self.env["product.attribute.value"].search(
            [("name", "=", "Blue"), ("attribute_id", "=", color_attr.id)], limit=1
        )
        if not blue:
            blue = self.env["product.attribute.value"].create(
                {"name": "Blue", "attribute_id": color_attr.id}
            )

        # Ensure magento binding for color attribute
        mag_color = color_attr.magento_bind_ids.filtered(
            lambda m: m.backend_id == self.backend
        )
        if not mag_color:
            mag_color = self.env["magento.product.attribute"].create(
                {
                    "odoo_id": color_attr.id,
                    "backend_id": self.backend.id,
                    "attribute_code": "color",
                    "attribute_id": 93,
                    "external_id": "93",
                    "frontend_input": "select",
                }
            )
        # Bindings for values
        for val, code in [(red, "49"), (blue, "50")]:
            if not val.magento_bind_ids.filtered(
                lambda m: m.backend_id == self.backend
            ):
                self.env["magento.product.attribute.value"].create(
                    {
                        "odoo_id": val.id,
                        "backend_id": self.backend.id,
                        "magento_attribute_id": mag_color.id,
                        "external_id": "93_%s" % code,
                        "code": code,
                        "label": val.name,
                    }
                )

        # Create template with color attribute line
        template = self.env["product.template"].create(
            {
                "name": "Configurable Export Test",
                "type": "consu",
                "list_price": 99.0,
                "code_prefix": "CONF-EXPORT",
                "attribute_line_ids": [
                    (
                        0,
                        0,
                        {
                            "attribute_id": color_attr.id,
                            "value_ids": [(6, 0, [red.id, blue.id])],
                        },
                    )
                ],
            }
        )

        # Set default_code on variants
        for variant in template.product_variant_ids:
            color_val = variant.product_template_attribute_value_ids.filtered(
                lambda v: v.attribute_id == color_attr
            )
            if color_val:
                variant.default_code = "CONF-EXPORT-%s" % color_val.name.upper()

        # Create template binding
        binding = (
            self.env["magento.product.template"]
            .with_context(connector_no_export=True)
            .create(
                {
                    "odoo_id": template.id,
                    "backend_id": self.backend.id,
                    "attribute_set_id": self.attr_set.id,
                    "product_type": "configurable",
                    "code_prefix": "CONF-EXPORT",
                }
            )
        )
        return binding

    def test_template_has_variants(self):
        """Configurable template should have variant products."""
        binding = self._create_configurable_template()
        template = binding.odoo_id
        self.assertTrue(template.has_variant_attributes)
        self.assertEqual(len(template.product_variant_ids), 2)

    def _link_attribute_lines(self, binding):
        with self.backend.work_on("magento.product.template") as work:
            exporter = work.component(usage="record.exporter")
            exporter.binding = binding
            exporter._create_attribute_lines()
        return binding.odoo_id.attribute_line_ids.magento_bind_ids

    def _variant_prices(self, binding):
        self.env.invalidate_all()
        return binding.odoo_id.product_variant_ids.mapped("lst_price")

    def test_first_export_keeps_the_prices_of_the_variants(self):
        """The first export of a configurable links its attribute lines to
        Magento. That must not touch its variants: recomputing them gives each
        one the price of the template (product_variant_sale_price), as it
        happened at Taramundi on 01/11."""
        binding = self._create_configurable_template()
        first, second = binding.odoo_id.product_variant_ids
        first.lst_price = 79.0
        second.lst_price = 109.0

        self.assertTrue(self._link_attribute_lines(binding))

        self.assertEqual(self._variant_prices(binding), [79.0, 109.0])

    def test_updating_a_linked_attribute_line_keeps_the_prices_of_the_variants(self):
        binding = self._create_configurable_template()
        line_binding = self._link_attribute_lines(binding)
        first, second = binding.odoo_id.product_variant_ids
        first.lst_price = 79.0
        second.lst_price = 109.0

        line_binding.write(
            {
                "magento_attribute_id": line_binding.magento_attribute_id.id,
                "position": 3,
            }
        )

        self.assertEqual(self._variant_prices(binding), [79.0, 109.0])

    def test_template_sku_proposal(self):
        """Template exporter uses code_prefix as SKU."""
        binding = self._create_configurable_template()
        with self.backend.work_on("magento.product.template") as work:
            exporter = work.component(usage="record.exporter")
            exporter.binding = binding
            exporter.external_id = binding.external_id
            sku = exporter._get_sku_proposal()
        self.assertEqual(sku, "CONF-EXPORT")

    def test_template_export_mapper(self):
        """Template export mapper generates correct data."""
        binding = self._create_configurable_template()
        with self.backend.work_on("magento.product.template") as work:
            mapper = work.component(usage="export.mapper")
            map_record = mapper.map_record(binding)
            data = map_record.values()

        self.assertEqual(data.get("name"), "Configurable Export Test")
        self.assertIn("extension_attributes", data)
        self.assertIn("weight", data)

    def test_template_export_mapper_price_from_the_backend_pricelist(self):
        """The configurable takes its price from the backend's pricelist too:
        Odoo 18 has no ``price`` field computed from the context."""
        binding = self._create_configurable_template()
        _set_backend_pricelist(
            self.env,
            self.backend,
            {
                "applied_on": "1_product",
                "product_tmpl_id": binding.odoo_id.id,
                "fixed_price": 79.0,
            },
        )

        with self.backend.work_on("magento.product.template") as work:
            mapper = work.component(usage="export.mapper")
            data = mapper.map_record(binding).values()

        self.assertEqual(data["price"], 79.0)

    def test_template_export_mapper_price_without_pricelist(self):
        """Without a pricelist on the backend, the sales price of the template."""
        binding = self._create_configurable_template()

        with self.backend.work_on("magento.product.template") as work:
            mapper = work.component(usage="export.mapper")
            data = mapper.map_record(binding).values()

        self.assertEqual(data["price"], 99.0)

    def test_template_export_mapper_linked_products(self):
        """The configurable sends its linked products: a configurable one by
        the SKU of its configurable product, a simple one by its own."""
        binding = self._create_configurable_template()
        binding.with_context(connector_no_export=True).external_id = "CONF-EXPORT"
        size = self.env["product.attribute"].create(
            {
                "name": "ZZ Link Size",
                "create_variant": "always",
                "value_ids": [(0, 0, {"name": "S"}), (0, 0, {"name": "M"})],
            }
        )
        linked_configurable = self.env["product.template"].create(
            {
                "name": "ZZ Linked Configurable",
                "type": "consu",
                "attribute_line_ids": [
                    (
                        0,
                        0,
                        {
                            "attribute_id": size.id,
                            "value_ids": [(6, 0, size.value_ids.ids)],
                        },
                    )
                ],
            }
        )
        self.env["magento.product.template"].with_context(
            connector_no_export=True
        ).create(
            {
                "odoo_id": linked_configurable.id,
                "backend_id": self.backend.id,
                "attribute_set_id": self.attr_set.id,
                "product_type": "configurable",
                "code_prefix": "ZZ-LINK-CONF",
                "external_id": "ZZ-LINK-CONF",
                "magento_id": 9401,
            }
        )
        linked_simple = self.env["product.product"].create(
            {"name": "ZZ Linked Simple", "default_code": "ZZ-LINK-SIMPLE"}
        )
        self.env["magento.product.product"].with_context(
            connector_no_export=True
        ).create(
            {
                "odoo_id": linked_simple.id,
                "backend_id": self.backend.id,
                "attribute_set_id": self.attr_set.id,
                "external_id": "ZZ-LINK-SIMPLE",
                "magento_internal_id": "9402",
            }
        )
        binding.odoo_id.write(
            {
                "magento_related_product_ids": [(6, 0, linked_configurable.ids)],
                "optional_product_ids": [(6, 0, linked_simple.product_tmpl_id.ids)],
            }
        )

        with self.backend.work_on("magento.product.template") as work:
            mapper = work.component(usage="export.mapper")
            links = mapper.map_record(binding).values()["product_links"]

        self.assertEqual(
            [
                (
                    link["sku"],
                    link["link_type"],
                    link["linked_product_sku"],
                    link["linked_product_type"],
                )
                for link in links
            ],
            [
                ("CONF-EXPORT", "related", "ZZ-LINK-CONF", "configurable"),
                ("CONF-EXPORT", "crosssell", "ZZ-LINK-SIMPLE", "simple"),
            ],
        )

    def test_variants_of_a_configurable_send_no_linked_products(self):
        """The shop shows the configurable, which has the links: its variants
        send none, and Magento leaves theirs as they are."""
        binding = self._create_configurable_template()
        variant_binding = (
            self.env["magento.product.product"]
            .with_context(connector_no_export=True)
            .create(
                {
                    "odoo_id": binding.odoo_id.product_variant_ids[0].id,
                    "backend_id": self.backend.id,
                    "attribute_set_id": self.attr_set.id,
                }
            )
        )

        with self.backend.work_on("magento.product.product") as work:
            mapper = work.component(usage="export.mapper")
            data = mapper.map_record(variant_binding).values()

        self.assertNotIn("product_links", data)

    def test_template_export_variants_created(self):
        """Exporting template creates variant bindings."""
        from unittest import mock

        binding = self._create_configurable_template()
        template = binding.odoo_id

        # Initially no variant bindings
        variant_bindings = self.env["magento.product.product"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("odoo_id", "in", template.product_variant_ids.ids),
            ]
        )
        self.assertEqual(len(variant_bindings), 0)

        # Call _export_variants with mocked variant exporter (no API calls)
        with self.backend.work_on("magento.product.template") as work:
            exporter = work.component(usage="record.exporter")
            exporter.binding = binding
            exporter.external_id = binding.external_id
            exporter.light_sync = False
            # Mock the variant exporter.run() to avoid real API calls
            variant_exporter = work.component(
                usage="record.exporter", model_name="magento.product.product"
            )
            with mock.patch.object(type(variant_exporter), "run"):
                exporter._export_variants()

        # Now variant bindings should exist
        variant_bindings = self.env["magento.product.product"].search(
            [
                ("backend_id", "=", self.backend.id),
                ("odoo_id", "in", template.product_variant_ids.ids),
            ]
        )
        self.assertEqual(
            len(variant_bindings), 2, "Should have created 2 variant bindings"
        )

    def test_template_export_mapper_image_is_json_serializable(self):
        """Template image payload must be JSON-serializable too."""
        binding = self._create_configurable_template()
        _add_image(self.env, binding.odoo_id)

        with self.backend.work_on("magento.product.template") as work:
            mapper = work.component(usage="export.mapper")
            data = mapper.map_record(binding).values()

        entries = data.get("media_gallery_entries")
        self.assertTrue(entries, "The image should reach media_gallery_entries")
        self.assertIsInstance(
            entries[0]["content"]["base64_encoded_data"],
            str,
            "base64_encoded_data must be str, not bytes",
        )
        json.dumps(data)
