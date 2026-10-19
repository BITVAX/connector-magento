# Copyright 2026 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
Tests for the store view labels of the attribute options.

Odoo keeps the translations of the attribute values and sends them to
Magento as the store labels of the option. Magento replaces all the labels of
an option when it is saved, so an option never translated in Odoo (a model, a
colour) is left as it is in Magento.
"""

import json
from unittest import mock

from odoo.tests import tagged

from odoo.addons.connector_magento.components.backend_adapter import (
    MagentoCRUDAdapter,
)

from .common import Magento2TestCase


@tagged("post_install", "-at_install")
class TestAttributeValueLabels(Magento2TestCase):
    def setUp(self):
        super().setUp()
        self.env["res.lang"]._activate_lang("es_ES")
        self.env["res.lang"]._activate_lang("de_DE")
        # The store view labels need the language of the admin store.
        self.backend.default_lang_id = self.env.ref("base.lang_en")
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
        for name, code, external_id, lang in (
            ("ZZ English", "zz_en", "904", "en_US"),
            ("ZZ Español", "zz_es", "901", "es_ES"),
            ("ZZ Deutsch", "zz_de", "903", "de_DE"),
        ):
            self.env["magento.storeview"].create(
                {
                    "name": name,
                    "code": code,
                    "external_id": external_id,
                    "store_id": store.id,
                    "lang_id": self.env["res.lang"]._lang_get(lang).id,
                }
            )
        self.attribute = self.env["product.attribute"].create({"name": "ZZ Gender"})
        self.women, self.aura = self.env["product.attribute.value"].create(
            [
                {"name": "Women", "attribute_id": self.attribute.id},
                {"name": "AURA", "attribute_id": self.attribute.id},
            ]
        )
        self.m_attribute = self.env["magento.product.attribute"].create(
            {
                "odoo_id": self.attribute.id,
                "backend_id": self.backend.id,
                "attribute_code": "zz_gender",
                "external_id": "569",
                "frontend_input": "select",
            }
        )
        self.m_women, self.m_aura = (
            self.env["magento.product.attribute.value"]
            .with_context(connector_no_export=True)
            .create(
                [
                    {
                        "odoo_id": value.id,
                        "magento_attribute_id": self.m_attribute.id,
                        "code": code,
                        "external_id": "569_%s" % code,
                    }
                    for value, code in ((self.women, "199"), (self.aura, "209"))
                ]
            )
        )

    def test_store_labels_are_the_translations(self):
        self.women.with_context(lang="es_ES").name = "Mujer"
        self.assertEqual(self.m_women._magento_admin_label(), "Women")
        # English is the admin store; German has no translation of its own.
        self.assertEqual(
            self.m_women._magento_store_labels(), [{"store_id": 901, "label": "Mujer"}]
        )

    def test_without_default_language_options_go_as_before(self):
        self.backend.default_lang_id = False
        self.women.with_context(lang="es_ES").name = "Mujer"
        self.women.sequence = 7
        self.assertEqual(self.m_women._magento_store_labels(), [])
        self.assertFalse(self.m_women._magento_labels_changed())
        self.assertEqual(self.m_women._magento_sort_order(), 7)

    def test_admin_label_ignores_the_user_language(self):
        self.women.with_context(lang="es_ES").name = "Mujer"
        with self.backend.work_on("magento.product.attribute.value") as work:
            mapper = work.component(usage="export.mapper")
            values = mapper.map_record(
                self.m_women.with_context(lang="es_ES")
            ).values()
        self.assertEqual(values["label"], "Women")
        self.assertEqual(values["store_labels"], [{"store_id": 901, "label": "Mujer"}])

    def test_sort_order_is_the_position_in_odoo(self):
        self.assertEqual(self.m_women._magento_sort_order(), 1)
        self.assertEqual(self.m_aura._magento_sort_order(), 2)
        self.women.sequence = 5
        self.assertEqual(self.m_aura._magento_sort_order(), 1)

    def test_untranslated_option_is_left_alone(self):
        self.assertFalse(self.m_aura._magento_labels_changed())

    def test_translated_option_goes_once(self):
        self.women.with_context(lang="es_ES").name = "Mujer"
        self.assertTrue(self.m_women._magento_labels_changed())
        self.m_women.magento_labels_signature = self.m_women._magento_labels_signature()
        self.assertFalse(self.m_women._magento_labels_changed())
        self.women.with_context(lang="de_DE").name = "Damen"
        self.assertTrue(self.m_women._magento_labels_changed())

    def test_export_updates_the_option_in_place(self):
        self.women.with_context(lang="es_ES").name = "Mujer"
        with self.backend.work_on("magento.product.attribute.value") as work:
            exporter = work.component(usage="record.exporter")
            with mock.patch.object(
                MagentoCRUDAdapter, "_call", return_value=True
            ) as call:
                exporter.run(self.m_women)
        (args, kwargs) = call.call_args
        self.assertEqual(args[0], "products/attributes/zz_gender/options/199")
        self.assertEqual(kwargs["http_method"], "put")
        self.assertEqual(args[1]["option"]["label"], "Women")
        self.assertEqual(
            args[1]["option"]["store_labels"], [{"store_id": 901, "label": "Mujer"}]
        )
        self.assertEqual(args[1]["option"]["sort_order"], 1)
        self.assertEqual(
            json.loads(self.m_women.magento_labels_signature),
            ["Women", [{"store_id": 901, "label": "Mujer"}], 1],
        )
        self.assertFalse(self.m_women._magento_labels_changed())

    def test_import_keeps_magento_labels_as_translations(self):
        self.women.with_context(lang="de_DE").name = "Frauen"

        def options(path, data, storeview=None, **kwargs):
            return {
                "all": [{"label": "Women", "value": "199"}, {"label": "AURA", "value": "209"}],
                "zz_es": [{"label": "Mujer", "value": "199"}, {"label": "AURA", "value": "209"}],
                "zz_de": [{"label": "Damen", "value": "199"}],
            }[storeview]

        with self.backend.work_on("magento.product.attribute") as work:
            importer = work.component(usage="record.importer")
            with mock.patch.object(MagentoCRUDAdapter, "_call", side_effect=options):
                importer._import_option_labels(self.m_attribute, "zz_gender")
        self.assertEqual(self.women.with_context(lang="es_ES").name, "Mujer")
        # The German translation of Odoo wins.
        self.assertEqual(self.women.with_context(lang="de_DE").name, "Frauen")
        # Same label as the admin one: no translation.
        name_field = self.aura._fields["name"]
        self.assertNotIn("es_ES", name_field._get_stored_translations(self.aura))
