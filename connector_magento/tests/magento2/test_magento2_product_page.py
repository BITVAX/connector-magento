# Copyright 2026 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
Tests of the product page of the shop: the texts go through the Odoo field
mapped on each Magento attribute (field_id, set by default for the attributes
of fixed code), the URL key and the attribute set live on the binding, the
gallery is sent only when it changed, every call goes to the admin store, and
each store view gets the texts of its language.
"""

from datetime import date, timedelta
from unittest import mock

from odoo.tests import tagged

from ...components.backend_adapter import (
    MagentoCRUDAdapter,
)
from ...models.product.exporter import (
    add_image_labels,
    gallery_images,
    gallery_label_entries,
    get_exported_value,
    images_signature,
)
from .common import Magento2TestCase

# Smallest valid PNG (1x1 transparent), base64-encoded.
PNG_1X1_B64 = (
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    b"hKmMIQAAAABJRU5ErkJggg=="
)

FIXED_CODES = {
    "description": "textarea",
    "short_description": "textarea",
    "url_key": "text",
    "meta_title": "text",
    "meta_keyword": "text",
    "meta_description": "textarea",
    "news_from_date": "date",
    "news_to_date": "date",
}


@tagged("post_install", "-at_install")
class TestProductPage(Magento2TestCase):
    def setUp(self):
        super().setUp()
        self.env["res.lang"]._activate_lang("es_ES")
        self.backend.default_lang_id = self.env.ref("base.lang_en")
        self.attr_set = self.env["magento.product.attribute.set"].create(
            {"name": "ZZ Default", "backend_id": self.backend.id, "external_id": "4"}
        )
        self.backend.default_attribute_set_id = self.attr_set
        # The attributes of fixed code, as the import creates them (without a
        # field), then mapped by default.
        self.attributes = {}
        for number, (code, frontend_input) in enumerate(FIXED_CODES.items(), 900):
            attribute = self.env["product.attribute"].create(
                {"name": "ZZ %s" % code, "create_variant": "no_variant"}
            )
            self.attributes[code] = self.env["magento.product.attribute"].create(
                {
                    "odoo_id": attribute.id,
                    "backend_id": self.backend.id,
                    "attribute_code": code,
                    "external_id": str(number),
                    "frontend_input": frontend_input,
                    "is_user_defined": False,
                    "attribute_set_ids": [(6, 0, self.attr_set.ids)],
                }
            )
        self.env["magento.product.attribute"]._apply_default_field_ids()
        self.size = self.env["product.attribute"].create(
            {"name": "ZZ Talla", "create_variant": "always"}
        )
        self.xl, self.size_14 = self.env["product.attribute.value"].create(
            [
                {"name": "XL", "attribute_id": self.size.id},
                {"name": "14", "attribute_id": self.size.id},
            ]
        )

    # Helpers

    def _simple(self, name="ZZ Ski Jacket", code="ZZ-JACKET"):
        product = self.env["product.product"].create(
            {"name": name, "default_code": code, "type": "consu"}
        )
        product.product_tmpl_id.with_context(lang="es_ES").name = "ZZ Chaqueta esquí"
        return product

    def _configurable(self):
        template = self.env["product.template"].create(
            {
                "name": "ZZ Midlayer",
                "type": "consu",
                "code_prefix": "ZZ-MIDLAYER",
                "attribute_line_ids": [
                    (
                        0,
                        0,
                        {
                            "attribute_id": self.size.id,
                            "value_ids": [(6, 0, (self.xl | self.size_14).ids)],
                        },
                    ),
                ],
            }
        )
        template.with_context(lang="es_ES").name = "ZZ Capa intermedia"
        return template

    def _bind(self, product, **cols):
        external_id = cols.pop("external_id", None)
        values = dict(attribute_set_id=self.attr_set.id, product_type="simple")
        values.update(cols)
        return self.create_binding_no_export(
            "magento.product.product", product, external_id, **values
        )

    @staticmethod
    def _attribute_record(code, number):
        """A Magento attribute as the import mapper gets it."""
        return {
            "attribute_code": code,
            "attribute_id": number,
            "frontend_input": "text",
            "is_user_defined": False,
            "default_frontend_label": code.replace("_", " ").title(),
            "options": [],
        }

    def _bind_template(self, template, **cols):
        values = dict(
            attribute_set_id=self.attr_set.id,
            product_type="configurable",
            code_prefix="ZZ-MIDLAYER",
        )
        values.update(cols)
        return self.create_binding_no_export(
            "magento.product.template", template, "ZZ-MIDLAYER", **values
        )

    def _custom_attributes(self, binding):
        with self.backend.work_on(binding._name) as work:
            mapper = work.component(usage="export.mapper")
            result = mapper.get_custom_attributes(binding)
        return {a["attribute_code"]: a["value"] for a in result["custom_attributes"]}

    def _gallery(self, binding):
        with self.backend.work_on(binding._name) as work:
            mapper = work.component(usage="export.mapper")
            return mapper.media_gallery_entries(binding)

    def _add_images(self, template, names):
        self.env["base_multi_image.image"].create(
            [
                {
                    "owner_model": "product.template",
                    "owner_id": template.id,
                    "storage": "db",
                    "file_db_store": PNG_1X1_B64,
                    "name": name,
                    "sequence": sequence,
                }
                for sequence, name in enumerate(names, start=1)
            ]
        )
        # image_ids is a One2many on a plain Integer owner_id: re-read it.
        self.env.flush_all()
        self.env.invalidate_all()

    def _storeviews(self):
        website = self.env["magento.website"].create(
            {
                "name": "ZZ Web",
                "code": "zz_web",
                "external_id": "901",
                "backend_id": self.backend.id,
            }
        )
        store = self.env["magento.store"].create(
            {
                "name": "ZZ Store",
                "external_id": "901",
                "website_id": website.id,
                "backend_id": self.backend.id,
            }
        )
        return self.env["magento.storeview"].create(
            [
                {
                    "name": "ZZ English",
                    "code": "zz_en",
                    "external_id": "904",
                    "store_id": store.id,
                    "lang_id": self.env.ref("base.lang_en").id,
                },
                {
                    "name": "ZZ Español",
                    "code": "zz_es",
                    "external_id": "901",
                    "store_id": store.id,
                    "lang_id": self.env["res.lang"]._lang_get("es_ES").id,
                },
            ]
        )

    # The mapping of the attributes of fixed code

    def test_fixed_code_attributes_are_mapped_by_default(self):
        self.assertEqual(self.attributes["description"].field_id.name, "description")
        self.assertEqual(
            self.attributes["description"].field_id.model, "product.template"
        )
        self.assertEqual(
            self.attributes["short_description"].field_id.name,
            "magento_short_description",
        )
        self.assertEqual(self.attributes["url_key"].field_id.name, "magento_url_key")
        self.assertEqual(
            self.attributes["url_key"].field_id.model, "magento.product.product"
        )
        self.assertEqual(
            self.attributes["news_from_date"].field_id.name, "magento_news_from_date"
        )

    def test_import_mapper_sets_the_field_only_when_creating(self):
        with self.backend.work_on("magento.product.attribute") as work:
            mapper = work.component(usage="import.mapper")
            record = self._attribute_record("meta_title", 77)
            created = mapper.map_record(record).values(for_create=True)
            updated = mapper.map_record(record).values()
        self.assertEqual(created["field_id"], self.attributes["meta_title"].field_id.id)
        self.assertNotIn("field_id", updated)

    def test_cleared_field_stays_cleared(self):
        attribute = self.attributes["meta_keyword"]
        attribute.field_id = False
        # A re-import maps nothing (only_create); only the explicit default
        # pass of a migration puts it back.
        with self.backend.work_on("magento.product.attribute") as work:
            mapper = work.component(usage="import.mapper")
            updated = mapper.map_record(
                self._attribute_record("meta_keyword", 78)
            ).values()
        self.assertNotIn("field_id", updated)
        self.assertFalse(attribute.field_id)

    # The exported values

    def test_exported_values_follow_the_type_of_the_field(self):
        product = self._simple()
        template = product.product_tmpl_id
        template.write(
            {
                "meta_title": "Ski jacket",
                "description": '<div data-oe-version="1.2"><p>Text</p></div>',
                "magento_news_from_date": date(2026, 10, 1),
            }
        )
        binding = self._bind(product)
        self.assertEqual(
            get_exported_value(self.attributes["meta_title"], binding), "Ski jacket"
        )
        self.assertEqual(
            get_exported_value(self.attributes["description"], binding),
            "<div><p>Text</p></div>",
        )
        self.assertEqual(
            get_exported_value(self.attributes["news_from_date"], binding),
            "2026-10-01 00:00:00",
        )
        # Empty: None, so that the attribute is left out and Magento keeps
        # its value.
        self.assertIsNone(get_exported_value(self.attributes["meta_keyword"], binding))
        self.assertIsNone(get_exported_value(self.attributes["news_to_date"], binding))

    def test_custom_attributes_are_the_mapped_fields_with_a_value(self):
        product = self._simple()
        template = product.product_tmpl_id
        template.write({"meta_title": "Ski jacket", "meta_keyword": "ski, jacket"})
        binding = self._bind(product, external_id="ZZ-JACKET")
        binding.magento_url_key = "ski-jacket"
        attributes = self._custom_attributes(binding)
        self.assertEqual(attributes["url_key"], "ski-jacket")
        self.assertIs(attributes["save_rewrites_history"], True)
        self.assertEqual(attributes["meta_title"], "Ski jacket")
        self.assertEqual(attributes["meta_keyword"], "ski, jacket")
        # Empty in Odoo: not sent, the shop keeps what it has.
        self.assertNotIn("meta_description", attributes)
        self.assertNotIn("description", attributes)
        self.assertNotIn("news_from_date", attributes)
        # Unmapped: not sent either, whatever the field holds.
        self.attributes["meta_title"].field_id = False
        self.assertNotIn("meta_title", self._custom_attributes(binding))

    def test_custom_attributes_of_a_configurable_product(self):
        template = self._configurable()
        template.meta_title = "Midlayer"
        binding = self._bind_template(template)
        binding.magento_url_key = "zz-midlayer"
        attributes = self._custom_attributes(binding)
        self.assertEqual(attributes["url_key"], "zz-midlayer")
        self.assertEqual(attributes["meta_title"], "Midlayer")

    def test_custom_attributes_follow_the_language_of_the_context(self):
        product = self._simple()
        product.product_tmpl_id.with_context(lang="en_US").meta_title = "Ski jacket"
        product.product_tmpl_id.with_context(
            lang="es_ES"
        ).meta_title = "Chaqueta de esquí"
        binding = self._bind(product)
        binding.with_context(lang="en_US").magento_url_key = "ski-jacket"
        binding.with_context(lang="es_ES").magento_url_key = "chaqueta-esqui"
        attributes = self._custom_attributes(binding.with_context(lang="es_ES"))
        self.assertEqual(attributes["url_key"], "chaqueta-esqui")
        self.assertEqual(attributes["meta_title"], "Chaqueta de esquí")

    # The URL key and the attribute set live on the binding

    def test_url_key_of_the_product_is_the_one_of_its_binding(self):
        product = self._simple()
        template = product.product_tmpl_id
        self.assertFalse(template.url_key)
        binding = self._bind(product)
        binding.with_context(lang="en_US").magento_url_key = "ski-jacket"
        binding.with_context(lang="es_ES").magento_url_key = "chaqueta-esqui"
        self.assertEqual(template.with_context(lang="en_US").url_key, "ski-jacket")
        self.assertEqual(template.with_context(lang="es_ES").url_key, "chaqueta-esqui")
        # Written on the product, it lands on the binding.
        template.with_context(lang="es_ES").url_key = "chaqueta"
        self.assertEqual(binding.with_context(lang="es_ES").magento_url_key, "chaqueta")
        self.assertEqual(
            binding.with_context(lang="en_US").magento_url_key, "ski-jacket"
        )
        # The translation dialog of the product writes the binding too.
        template.update_field_translations("url_key", {"es_ES": "chaqueta-de-esqui"})
        self.assertEqual(
            binding.with_context(lang="es_ES").magento_url_key, "chaqueta-de-esqui"
        )
        self.assertTrue(
            self.env["product.template"].search(
                [("url_key", "=", "ski-jacket"), ("id", "=", template.id)]
            )
        )

    def test_url_key_of_a_configurable_product_is_the_one_of_its_template_binding(self):
        template = self._configurable()
        binding = self._bind_template(template)
        binding.magento_url_key = "zz-midlayer"
        self.assertEqual(template.url_key, "zz-midlayer")
        template.url_key = "midlayer"
        self.assertEqual(binding.magento_url_key, "midlayer")

    def test_variant_takes_the_url_key_of_its_configurable_plus_its_values(self):
        template = self._configurable()
        template_binding = self._bind_template(template)
        template_binding.with_context(lang="en_US").magento_url_key = "zz-midlayer"
        template_binding.with_context(
            lang="es_ES"
        ).magento_url_key = "zz-capa-intermedia"
        variants = {
            v.product_template_attribute_value_ids.filtered(
                lambda ptav: ptav.attribute_id == self.size
            ).name: v
            for v in template.product_variant_ids
        }
        self.assertEqual(
            variants["XL"].with_context(lang="en_US").variant_url_key, "zz-midlayer-xl"
        )
        self.assertEqual(
            variants["14"].with_context(lang="es_ES").variant_url_key,
            "zz-capa-intermedia-14",
        )
        # At export, a variant binding without a key gets it in every
        # language of the shop; one typed by hand is kept.
        self._storeviews()
        xl_binding = self._bind(variants["XL"], external_id="ZZ-MID-XL")
        binding_14 = self._bind(variants["14"], external_id="ZZ-MID-14")
        binding_14.with_context(lang="en_US").magento_url_key = "hand-made"
        with self.backend.work_on("magento.product.product") as work:
            exporter = work.component(usage="record.exporter")
            for binding in (xl_binding, binding_14):
                exporter.binding = binding
                exporter._fill_variant_url_key()
        self.assertEqual(
            xl_binding.with_context(lang="en_US").magento_url_key, "zz-midlayer-xl"
        )
        self.assertEqual(
            xl_binding.with_context(lang="es_ES").magento_url_key,
            "zz-capa-intermedia-xl",
        )
        self.assertEqual(
            binding_14.with_context(lang="en_US").magento_url_key, "hand-made"
        )
        self.assertEqual(
            binding_14.with_context(lang="es_ES").magento_url_key,
            "zz-capa-intermedia-14",
        )

    def test_attribute_set_of_the_product_is_the_one_of_its_binding(self):
        jackets = self.env["magento.product.attribute.set"].create(
            {"name": "ZZ Jackets", "backend_id": self.backend.id, "external_id": "9"}
        )
        product = self._simple()
        template = product.product_tmpl_id
        self.assertFalse(template.magento_attribute_set_id)
        binding = self._bind(product)
        self.assertEqual(template.magento_attribute_set_id, self.attr_set)
        template.magento_attribute_set_id = jackets
        self.assertEqual(binding.attribute_set_id, jackets)
        # A configurable product: its template binding and its variants'.
        template = self._configurable()
        template_binding = self._bind_template(template)
        variant_binding = self._bind(template.product_variant_ids[0])
        template.magento_attribute_set_id = jackets
        self.assertEqual(template_binding.attribute_set_id, jackets)
        self.assertEqual(variant_binding.attribute_set_id, jackets)

    # HTML texts

    def test_short_description_is_whole_html_per_language_and_sanitized(self):
        template = self._simple().product_tmpl_id
        field = template._fields["magento_short_description"]
        self.assertEqual(field.type, "html")
        self.assertIs(field.translate, True)
        template.magento_short_description = (
            '<div data-content-type="row" style="color: red;" onclick="alert(1)">'
            "<h2>Title</h2><p>Text <strong>bold</strong></p><script>alert(3)</script></div>"
        )
        value = template.magento_short_description
        for kept in ("<h2>Title</h2>", "<strong>bold</strong>"):
            self.assertIn(kept, value)
        for dropped in ("data-content-type", "style=", "onclick", "<script"):
            self.assertNotIn(dropped, value)
        template.update_field_translations(
            "magento_short_description",
            {"es_ES": '<h2>Uno y dos</h2><img src="x" onerror="alert(1)">'},
        )
        spanish = template.with_context(lang="es_ES").magento_short_description
        self.assertIn("<h2>Uno y dos</h2>", spanish)
        self.assertNotIn("onerror", spanish)

    # Variants of a configurable product keep the template texts

    def test_variant_import_leaves_the_template_level_fields_alone(self):
        with self.backend.work_on("magento.product.product") as work:
            importer = work.component(usage="record.importer")
            data = importer._variant_data(
                {
                    "magento_url_key": "midlayer-xl",
                    "description": "<p>x</p>",
                    "magento_short_description": "<p>y</p>",
                    "meta_title": "Midlayer",
                    "weight": 1.0,
                },
                binding_template_id=True,
            )
            self.assertEqual(data, {"magento_url_key": "midlayer-xl", "weight": 1.0})
            data = importer._variant_data({"description": "<p>x</p>"})
            self.assertEqual(data, {"description": "<p>x</p>"})

    # Gallery

    def test_configurable_exports_whole_gallery(self):
        template = self._configurable()
        self._add_images(template, ["Front", "Back", "Detail"])
        binding = self._bind_template(template)
        entries = self._gallery(binding)["media_gallery_entries"]
        self.assertEqual([e["label"] for e in entries], ["Front", "Back", "Detail"])
        self.assertEqual([e["position"] for e in entries], [1, 2, 3])
        self.assertEqual(entries[0]["types"], ["image", "small_image", "thumbnail"])
        self.assertNotIn("types", entries[1])
        self.assertEqual(len({e["content"]["name"] for e in entries}), 3)

    def test_gallery_leaves_out_images_of_one_variant(self):
        template = self._configurable()
        self._add_images(template, ["Front", "Only XL"])
        xl = template.product_variant_ids.filtered(
            lambda p: (
                self.xl
                in p.product_template_attribute_value_ids.product_attribute_value_id
            )
        )
        template.image_ids.filtered(
            lambda i: i.name == "Only XL"
        ).product_variant_ids = xl
        binding = self._bind_template(template)
        entries = self._gallery(binding)["media_gallery_entries"]
        self.assertEqual([e["label"] for e in entries], ["Front"])

    def test_gallery_goes_only_when_an_image_changed(self):
        template = self._configurable()
        self._add_images(template, ["Front", "Back"])
        binding = self._bind_template(template)
        # New in Magento: the gallery goes.
        self.assertIn("media_gallery_entries", self._gallery(binding))
        # Exported after the last change of the images: nothing to send.
        binding.write(
            {
                "sync_date": max(template.image_ids.mapped("write_date"))
                + timedelta(seconds=1),
                "magento_images_signature": images_signature(
                    binding, gallery_images(binding)
                ),
            }
        )
        self.assertEqual(self._gallery(binding), {})
        # An image modified after the export: the whole gallery goes again.
        binding.sync_date = min(template.image_ids.mapped("write_date")) - timedelta(
            seconds=1
        )
        self.assertEqual(len(self._gallery(binding)["media_gallery_entries"]), 2)
        # An image removed touches no date: the signature tells.
        binding.sync_date = max(template.image_ids.mapped("write_date")) + timedelta(
            seconds=1
        )
        template.image_ids.sorted("sequence")[1].unlink()
        self.env.invalidate_all()
        self.assertEqual(len(self._gallery(binding)["media_gallery_entries"]), 1)

    def test_images_are_kept_in_magento_when_the_gallery_is_not_sent(self):
        binding = self._bind(self._simple(), external_id="ZZ-JACKET")
        with self.backend.work_on("magento.product.product") as work:
            exporter = work.component(usage="record.exporter")
            exporter.binding = binding
            exporter.external_id = binding.external_id
            media = [{"id": 5, "file": "/a/a.jpg"}]
            with mock.patch.object(
                MagentoCRUDAdapter, "_call", return_value=media
            ) as call:
                exporter._clear_existing_images_before_update({"name": "x"})
            self.assertFalse(call.called)
            with mock.patch.object(
                MagentoCRUDAdapter, "_call", return_value=media
            ) as call:
                exporter._clear_existing_images_before_update(
                    {"media_gallery_entries": []}
                )
            deletes = [
                c.args[0]
                for c in call.call_args_list
                if c.kwargs.get("http_method") == "delete"
            ]
            self.assertEqual(deletes, ["products/ZZ-JACKET/media/5"])

    def test_main_image_name_goes_to_every_label(self):
        values = add_image_labels(
            {
                "custom_attributes": [{"attribute_code": "url_key", "value": "x"}],
                "media_gallery_entries": [
                    {
                        "label": "Sand jacket front",
                        "types": ["image", "small_image", "thumbnail"],
                    },
                    {"label": "Back"},
                ],
            }
        )
        attributes = {
            a["attribute_code"]: a["value"] for a in values["custom_attributes"]
        }
        self.assertEqual(attributes["url_key"], "x")
        for code in ("image_label", "small_image_label", "thumbnail_label"):
            self.assertEqual(attributes[code], "Sand jacket front")

    # Admin store

    def test_product_calls_go_to_the_admin_store(self):
        for model in ("magento.product.product", "magento.product.template"):
            with self.backend.work_on(model) as work:
                adapter = work.component(usage="backend.adapter")
                with mock.patch.object(
                    MagentoCRUDAdapter, "_call", return_value={"id": 7, "sku": "ZZ-SKU"}
                ) as call:
                    adapter.create({"sku": "ZZ-SKU"})
                self.assertEqual(call.call_args.kwargs["storeview"], "all", model)
                self.assertEqual(call.call_args.kwargs["http_method"], "post", model)
                with mock.patch.object(
                    MagentoCRUDAdapter, "_call", return_value=True
                ) as call:
                    adapter.write("ZZ-SKU", {"sku": "ZZ-SKU"})
                self.assertEqual(call.call_args.kwargs["storeview"], "all", model)
                with mock.patch.object(
                    MagentoCRUDAdapter, "_call", return_value=[{"id": 3}]
                ) as call:
                    self.assertEqual(adapter.get_media("ZZ-SKU"), [{"id": 3}])
                    self.assertTrue(adapter.delete_product_image("ZZ-SKU", 3))
                self.assertEqual(
                    [c.kwargs["storeview"] for c in call.call_args_list], ["all", "all"]
                )

    def test_exporter_maps_in_the_language_of_the_admin_store(self):
        from ...components.exporter import (
            MagentoBaseExporter,
        )
        from ...models.product.exporter import (
            ProductProductExporter,
        )

        binding = self._bind(self._simple())
        with self.backend.work_on("magento.product.product") as work:
            exporter = work.component(usage="record.exporter")
            with (
                mock.patch.object(
                    MagentoBaseExporter, "_should_import", return_value=False
                ),
                mock.patch.object(ProductProductExporter, "_run", autospec=True) as run,
            ):
                exporter.run(binding.with_context(lang="es_ES"))
        self.assertEqual(run.call_args.args[0].binding.env.lang, "en_US")

    # Translations per store view

    def _translated_simple(self):
        product = self._simple()
        product.product_tmpl_id.with_context(lang="en_US").write(
            {"meta_title": "Ski jacket", "meta_keyword": "ski"}
        )
        product.product_tmpl_id.with_context(
            lang="es_ES"
        ).meta_title = "Chaqueta de esquí"
        binding = self._bind(product, external_id="ZZ-JACKET")
        binding.with_context(lang="en_US").magento_url_key = "ski-jacket"
        binding.with_context(lang="es_ES").magento_url_key = "chaqueta-esqui"
        return binding

    def _run_translations(self, binding, media=None):
        with self.backend.work_on("magento.product.product") as work:
            exporter = work.component(usage="record.exporter")
            exporter.binding = binding
            exporter.external_id = binding.external_id
            with mock.patch.object(
                MagentoCRUDAdapter, "_call", return_value=media or []
            ) as call:
                exporter._export_translations()
        return [c for c in call.call_args_list if c.kwargs.get("http_method") == "put"]

    def test_translations_go_to_each_store_view_but_the_admin_one(self):
        self._storeviews()
        puts = self._run_translations(self._translated_simple())
        self.assertEqual([c.kwargs["storeview"] for c in puts], ["zz_es"])
        data = puts[0].args[1]["product"]
        attributes = {
            a["attribute_code"]: a["value"] for a in data["custom_attributes"]
        }
        self.assertEqual(data["sku"], "ZZ-JACKET")
        self.assertEqual(data["name"], "ZZ Chaqueta esquí")
        self.assertEqual(attributes["url_key"], "chaqueta-esqui")
        self.assertIs(attributes["save_rewrites_history"], True)
        self.assertEqual(attributes["meta_title"], "Chaqueta de esquí")
        # Not translated: the store view keeps falling back to the admin store.
        self.assertNotIn("meta_keyword", attributes)

    def test_no_translation_sends_nothing(self):
        self._storeviews()
        product = self._simple()
        product.product_tmpl_id.with_context(lang="es_ES").name = "ZZ Ski Jacket"
        binding = self._bind(product, external_id="ZZ-JACKET")
        self.assertEqual(self._run_translations(binding), [])

    def test_without_default_language_no_translation_goes(self):
        self._storeviews()
        self.backend.default_lang_id = False
        self.assertEqual(self._run_translations(self._translated_simple()), [])

    def test_only_a_translated_image_label_is_sent(self):
        self._storeviews()
        product = self._simple()
        product.product_tmpl_id.with_context(lang="es_ES").name = "ZZ Ski Jacket"
        self._add_images(product.product_tmpl_id, ["Front"])
        product.product_tmpl_id.image_ids.with_context(lang="es_ES").name = "Delante"
        binding = self._bind(product, external_id="ZZ-JACKET")
        media = [
            {
                "id": 5,
                "media_type": "image",
                "position": 1,
                "disabled": False,
                "types": ["image"],
                "file": "/a.jpg",
            }
        ]
        puts = self._run_translations(binding, media=media)
        self.assertEqual([c.kwargs["storeview"] for c in puts], ["zz_es"])
        data = puts[0].args[1]["product"]
        self.assertEqual(data["media_gallery_entries"][0]["label"], "Delante")
        self.assertIn(
            {"attribute_code": "image_label", "value": "Delante"},
            data["custom_attributes"],
        )

    def test_gallery_labels_follow_the_admin_store_entries(self):
        media = [
            {
                "id": 12,
                "media_type": "image",
                "position": 2,
                "disabled": False,
                "types": [],
                "file": "/b.jpg",
                "label": "Back",
            },
            {
                "id": 11,
                "media_type": "image",
                "position": 1,
                "disabled": False,
                "types": ["image"],
                "file": "/a.jpg",
                "label": "Front",
            },
        ]
        entries = gallery_label_entries(media, ["Delante", "Detrás"])
        self.assertEqual(
            [(e["id"], e["label"]) for e in entries], [(11, "Delante"), (12, "Detrás")]
        )
        self.assertIsNone(gallery_label_entries(media, ["Delante"]))

    # The jobs obey the strategies

    def test_export_and_import_jobs_obey_the_strategies(self):
        from ...models.product.listener import (
            _export_allowed,
        )

        product = self._simple()
        in_magento = self._bind(
            product, external_id="ZZ-JACKET", magento_internal_id="9001"
        )
        new_product = self._simple(name="ZZ New", code="ZZ-NEW")
        not_yet = self._bind(new_product, external_id="ZZ-NEW")
        self.backend.product_export_strategy = "create"
        self.assertFalse(_export_allowed(in_magento))
        self.assertTrue(_export_allowed(not_yet))
        self.assertIn("Product Export", in_magento.export_record())
        self.backend.product_export_strategy = "never"
        self.assertIn("Product Export", not_yet.export_record())
        self.backend.product_import_strategy = "never"
        self.assertIn(
            "Product Import",
            self.env["magento.product.product"].import_record(
                self.backend, "ZZ-JACKET"
            ),
        )
        self.backend.product_import_strategy = "create"
        self.assertIn(
            "Product Import",
            self.env["magento.product.product"].import_record(
                self.backend, "ZZ-JACKET"
            ),
        )

    def test_option_sort_order_is_the_sequence(self):
        attribute = self.env["product.attribute"].create({"name": "ZZ Gender"})
        women = self.env["product.attribute.value"].create(
            {"name": "Women", "attribute_id": attribute.id, "sequence": 7}
        )
        m_attribute = self.env["magento.product.attribute"].create(
            {
                "odoo_id": attribute.id,
                "backend_id": self.backend.id,
                "attribute_code": "zz_gender",
                "external_id": "569",
                "frontend_input": "select",
            }
        )
        m_women = (
            self.env["magento.product.attribute.value"]
            .with_context(connector_no_export=True)
            .create(
                {
                    "odoo_id": women.id,
                    "magento_attribute_id": m_attribute.id,
                    "code": "199",
                    "external_id": "569_199",
                }
            )
        )
        self.assertEqual(m_women._magento_sort_order(), 7)
