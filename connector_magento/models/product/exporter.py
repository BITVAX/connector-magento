# Copyright 2013-2017 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import base64
import logging
import re

import magic
from slugify import slugify

from odoo import _
from odoo.exceptions import ValidationError

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping
from odoo.addons.connector.exception import IDMissingInBackend, JobError

from ...components.backend_adapter import MAGENTO_DATETIME_FORMAT

# Dates go to Magento as datetimes at midnight (news_from_date...).
MAGENTO_DATE_FORMAT = "%Y-%m-%d 00:00:00"

# Bookkeeping attributes of Odoo's HTML editor (data-oe-version...): they mean
# nothing to Magento.
ODOO_EDITOR_ATTRIBUTES_RE = re.compile(r"""\s+data-oe-[\w-]+=(?:"[^"]*"|'[^']*')""")

# Roles of the main image and the attributes that hold its label (alt text).
IMAGE_LABEL_CODES = {
    "image": "image_label",
    "small_image": "small_image_label",
    "thumbnail": "thumbnail_label",
}
# Keys of a gallery entry of Magento that are sent back to it when only the
# labels of a store view are updated.
MEDIA_ENTRY_KEYS = ("id", "media_type", "position", "disabled", "types", "file")


def get_exported_value(matt_id, record):
    """Value of the Odoo field the Magento attribute ``matt_id`` is mapped on,
    read on ``record`` (a product binding) in the language of its context and
    turned into what Magento takes. None when the field is empty: the
    attribute is then left out of the payload and the shop keeps its value."""
    field = matt_id.field_id.sudo()
    if not field or field.name not in record._fields:
        return None
    value = record[field.name]
    if field.ttype == "boolean":
        return int(value)
    if not value:
        return None
    if field.ttype == "html":
        return ODOO_EDITOR_ATTRIBUTES_RE.sub("", str(value))
    if field.ttype in ("char", "text", "selection"):
        return str(value)
    if field.ttype == "date":
        return value.strftime(MAGENTO_DATE_FORMAT)
    if field.ttype == "datetime":
        return value.strftime(MAGENTO_DATETIME_FORMAT)
    if field.ttype in ("many2one", "one2many", "many2many"):
        return ", ".join(value.mapped("display_name"))
    return value


def mapped_attributes(binding):
    """Magento attributes of the attribute set of ``binding`` (a product
    binding) that are synchronized with an Odoo field (``field_id``)."""
    attribute_set = (
        binding.attribute_set_id or binding.backend_id.default_attribute_set_id
    )
    return attribute_set.attribute_ids.filtered(
        lambda a: a.field_id and a.backend_id == binding.backend_id
    )


def mapped_custom_attributes(binding):
    """Custom attributes of the fields mapped on the attribute set of
    ``binding``, with the values that are not empty, read in the language of
    its context. A URL key goes with ``save_rewrites_history``: Magento keeps
    the previous URL as a 301 redirect when the key changes."""
    attributes = []
    for matt_id in mapped_attributes(binding):
        value = get_exported_value(matt_id, binding)
        if value is None:
            continue
        attributes.append({"attribute_code": matt_id.attribute_code, "value": value})
        if matt_id.attribute_code == "url_key":
            attributes.append(
                {"attribute_code": "save_rewrites_history", "value": True}
            )
    return attributes


def translated_codes(binding):
    """Codes of the mapped attributes whose Odoo field is translatable: the
    ones a store view holds in its language."""
    return {
        attribute.attribute_code
        for attribute in mapped_attributes(binding)
        if attribute.field_id.translate
    }


def translation_storeviews(backend):
    """Store views of ``backend`` whose language is not the one of the admin
    store (the default language of the backend): the ones that hold
    translations. None without a default language."""
    admin_lang = backend._magento_admin_lang()
    if not admin_lang:
        return backend.env["magento.storeview"]
    return backend.env["magento.storeview"].search(
        [
            ("backend_id", "=", backend.id),
            ("lang_id", "!=", False),
            ("code", "!=", False),
            ("lang_id.code", "!=", admin_lang),
        ]
    )


# Gallery. The payload carries the gallery only when it changed since the last
# export (a product new in Magento, another set of images, an image modified in
# Odoo); without it Magento keeps the images it has.


def gallery_images(binding):
    """Images sent to the gallery of ``binding``, in their order: for a
    configurable product those not tied to one variant (or all of them when
    every image belongs to a variant); for a simple product the images of the
    variant when there are some, otherwise all of them."""
    product = binding.odoo_id
    if binding._name == "magento.product.template":
        images = (
            product.image_ids.filtered(lambda i: not i.product_variant_ids)
            or product.image_ids
        )
    else:
        images = product.image_ids
        shared = images.filtered(lambda i: not i.product_variant_ids)
        if len(images) > 1 and shared:
            images = (images - shared) or images
    return images.filtered("image_1920")


def image_base_name(binding):
    """Start of the file names of the images of ``binding`` in Magento: the
    URL key of the product and its reference, the same in every export."""
    product = binding.odoo_id
    template = (
        product if product._name == "product.template" else product.product_tmpl_id
    )
    url_key = binding.magento_url_key or slugify(template.name or "")
    if product._name == "product.template":
        reference = binding.code_prefix or binding.external_id
    else:
        reference = product.default_code or binding.external_id
    return slugify("%s %s" % (url_key, reference or ""))


def images_signature(binding, images):
    """What the gallery of ``binding`` looks like: its file names and their
    order. A change (an image removed, another URL key...) touches no image
    date, so it is compared with the one of the last export."""
    base = image_base_name(binding)
    return ",".join("%s-%s" % (base, image.id) for image in images)


def images_changed(binding, images):
    """Whether the gallery has to go to Magento: a product new there, a
    gallery other than the one exported last, or an image modified in Odoo
    since the last export."""
    if not binding.external_id or not binding.sync_date:
        return True
    if binding.magento_images_signature != images_signature(binding, images):
        return True
    return any(
        image.write_date and image.write_date > binding.sync_date for image in images
    )


def media_gallery_entries(mapper, binding, images):
    """Magento gallery entries for ``images``, in their order. The first one
    takes the image, small_image and thumbnail roles. Each file is named
    after the product and the image, the same in every export."""
    mime = magic.Magic(mime=True)
    base = image_base_name(binding)
    entries = []
    for image in images:
        # image_1920 is a bytes value holding base64-encoded data
        image_data = image.image_1920
        if not image_data:
            continue
        mimetype = mime.from_buffer(base64.b64decode(image_data))
        extension = mapper.mime_to_extension.get(mimetype, "jpg")
        entries.append(
            {
                "media_type": "image",
                "label": image.name or binding.name,
                "position": len(entries) + 1,
                "disabled": False,
                "content": {
                    # decode() because json.dumps cannot serialize bytes
                    "base64_encoded_data": image_data.decode(),
                    "type": mimetype,
                    "name": "%s-%s.%s" % (base, image.id, extension),
                },
            }
        )
    if entries:
        entries[0]["types"] = ["image", "small_image", "thumbnail"]
    return entries


def gallery_values(mapper, binding):
    """The gallery of ``binding`` when it has to be sent, otherwise nothing."""
    images = gallery_images(binding)
    if not images_changed(binding, images):
        return {}
    return {"media_gallery_entries": media_gallery_entries(mapper, binding, images)}


def add_image_labels(values):
    """Send the label of the image that takes each role (the name of the image
    in Odoo) to the matching *_label attribute too."""
    labels = {}
    for entry in values.get("media_gallery_entries") or []:
        for role in entry.get("types") or []:
            if role in IMAGE_LABEL_CODES and entry.get("label"):
                labels[IMAGE_LABEL_CODES[role]] = entry["label"]
    if labels:
        attributes = [
            attr
            for attr in values.get("custom_attributes", [])
            if attr.get("attribute_code") not in labels
        ]
        attributes += [
            {"attribute_code": code, "value": label} for code, label in labels.items()
        ]
        values["custom_attributes"] = attributes
    return values


# Translations. The admin store holds the texts in the default language of the
# backend; each store view of another language gets only what differs.


def translatable_values(mapper, binding):
    """Name, translatable mapped attributes and image labels of ``binding``
    in the language of its context."""
    if binding._name == "magento.product.template":
        name = binding.name
    else:
        name = mapper.names(binding)["name"]
    codes = translated_codes(binding)
    attributes = {
        attr["attribute_code"]: attr["value"]
        for attr in mapped_custom_attributes(binding)
        if attr["attribute_code"] in codes
    }
    labels = [image.name or binding.name for image in gallery_images(binding)]
    return name, attributes, labels


def translation_payload(base, values):
    """What a store view must hold on top of the admin store: only the values
    that differ from the base language, so the rest keeps falling back."""
    name, attributes, labels = values
    base_name, base_attributes, base_labels = base
    data = {}
    if name and name != base_name:
        data["name"] = name
    custom_attributes = [
        {"attribute_code": code, "value": value}
        for code, value in attributes.items()
        if value and value != base_attributes.get(code)
    ]
    if any(attr["attribute_code"] == "url_key" for attr in custom_attributes):
        custom_attributes.append(
            {"attribute_code": "save_rewrites_history", "value": True}
        )
    if custom_attributes:
        data["custom_attributes"] = custom_attributes
    return data


def add_store_view_labels(data, labels):
    """Image labels of the store view, translated or not. Saving a product on
    a store view makes Magento copy the image labels to it, so whenever a
    store view gets something its labels go along and its copies stay right."""
    if not labels:
        return
    codes = set(IMAGE_LABEL_CODES.values())
    custom_attributes = [
        attr
        for attr in data.get("custom_attributes", [])
        if attr["attribute_code"] not in codes
    ]
    custom_attributes += [
        {"attribute_code": code, "value": labels[0]} for code in sorted(codes)
    ]
    data["custom_attributes"] = custom_attributes


def gallery_label_entries(media_entries, labels):
    """Gallery entries of the admin store with the labels of a store view, or
    None when they do not match the images sent."""
    if not labels or len(media_entries) != len(labels):
        return None
    entries = sorted(media_entries, key=lambda e: e.get("position") or 0)
    return [
        dict({key: entry.get(key) for key in MEDIA_ENTRY_KEYS}, label=label)
        for entry, label in zip(entries, labels)
    ]


_logger = logging.getLogger(__name__)


class ProductProductExporter(Component):
    _name = "magento.product.product.exporter"
    _inherit = "magento.exporter"
    _apply_on = ["magento.product.product"]

    def _run(self, fields=None, **kwargs):
        """Flow of the synchronization, implemented in inherited classes"""
        assert self.binding

        if not self.external_id:
            fields = None  # should be created with all the fields

        if self._has_to_skip():
            return

        # export the missing linked resources
        self._export_dependencies()

        # prevent other jobs to export the same record
        # will be released on commit (or rollback)
        self._lock()

        map_record = self._map_data()

        # _logger.info("External ID is: %s", self.external_id)
        if self.external_id and self.binding.magento_internal_id:
            _logger.info("External ID is: %s", self.external_id)
            record = self._update_data(map_record, fields=fields)
            if not record:
                return _("Nothing to export.")

            # Clear ALL existing images BEFORE update to maintain sync
            self._clear_existing_images_before_update(record)

            data = self._update(record, **kwargs)
            if data:
                self._update_binding_record_after_write(data)
        else:
            record = self._create_data(map_record, fields=fields)
            if not record:
                return _("Nothing to export.")
            data = self._create(record)
            if not data:
                raise UserWarning(
                    _(
                        "Create did not returned anything on %(name)s"
                        " with binding id %(binding_id)s",
                        name=self._name,
                        binding_id=self.binding.id,
                    )
                )
            self._update_binding_record_after_create(record)
            self.external_id = record.get("sku")
            self.binding.recompute_magento_qty()
            self.binding.export_inventory(fields=["magento_qty"])
        return _("Record exported with ID %s on Magento.") % self.external_id

    def run(self, binding, *args, **kwargs):
        # The payload goes to the admin store: every text is read in its
        # language, the default language of the backend, whatever the language
        # of the user or the job that exports.
        admin_lang = self.backend_record._magento_admin_lang()
        if admin_lang:
            binding = binding.with_context(lang=admin_lang)
        return super().run(binding, *args, **kwargs)

    def _after_export(self):
        res = super()._after_export()
        binding = self.binding
        signature = images_signature(binding, gallery_images(binding))
        if binding.magento_images_signature != signature:
            binding.with_context(
                connector_no_export=True
            ).magento_images_signature = signature
        self._export_translations()
        return res

    def _shop_langs(self):
        """Languages of the shop: the one of the admin store first, then the
        ones of the store views that hold translations."""
        backend = self.backend_record
        langs = [backend._magento_admin_lang() or self.env.lang or "en_US"]
        for storeview in translation_storeviews(backend):
            if storeview.lang_id.code not in langs:
                langs.append(storeview.lang_id.code)
        return langs

    def _fill_variant_url_key(self):
        """A variant of a configurable product takes, in every language of the
        shop where it has none of its own, the URL key of its configurable
        followed by the values that make the variant (e.g. -xl)."""
        binding = self.binding
        if binding._name != "magento.product.product":
            return
        product = binding.odoo_id
        template = product.product_tmpl_id
        if len(template.product_variant_ids) == 1:
            return
        template_binding = template.magento_bind_ids.filtered(
            lambda b: b.backend_id == self.backend_record
        )[:1]
        if not template_binding:
            return
        field = binding._fields["magento_url_key"]
        for lang in self._shop_langs():
            binding.flush_recordset(["magento_url_key"])
            if (field._get_stored_translations(binding) or {}).get(lang):
                continue
            key = product.with_context(lang=lang)._magento_variant_url_key(
                template_binding.with_context(lang=lang).magento_url_key
            )
            if key:
                binding.with_context(
                    lang=lang, connector_no_export=True
                ).magento_url_key = key

    def _export_translations(self):
        """Send to each store view of the backend the texts of the product in
        the language of the store view: the name, the translatable mapped
        attributes and the image labels, only what differs from the admin
        store, which already holds the default language."""
        backend = self.backend_record
        admin_lang = backend._magento_admin_lang()
        if not admin_lang or not self.external_id:
            return
        storeviews = translation_storeviews(backend)
        if not storeviews:
            return
        mapper = self.component(usage="export.mapper")
        base = translatable_values(mapper, self.binding.with_context(lang=admin_lang))
        media_entries = None
        for storeview in storeviews:
            values = translatable_values(
                mapper, self.binding.with_context(lang=storeview.lang_id.code)
            )
            data = translation_payload(base, values)
            labels = values[2]
            if not data and labels == base[2]:
                continue
            add_store_view_labels(data, labels)
            if labels:
                if media_entries is None:
                    media_entries = self.backend_adapter.get_media(self.external_id)
                entries = gallery_label_entries(media_entries, labels)
                if entries:
                    data["media_gallery_entries"] = entries
            data["sku"] = self.external_id
            _logger.info(
                "Export %s translations to store view %s: %s",
                self.external_id,
                storeview.code,
                sorted(data),
            )
            self.backend_adapter.write(self.external_id, data, storeview=storeview.code)

    def _clear_existing_images_before_update(self, record):
        """Clear all existing images from Magento before updating product.

        This prevents image duplication when syncing products.
        Called during update operations, before sending new image data. Without
        a gallery in the payload Magento keeps its images: deleting them would
        leave the product without any.

        :param record: Product data dictionary to be sent to Magento
        :return: None
        """
        if "media_gallery_entries" not in record:
            return
        new_image_count = len(record.get("media_gallery_entries", []))
        entity_type = (
            "Product" if self._apply_on == ["magento.product.product"] else "Template"
        )

        _logger.debug(
            "%s %s: clearing existing images before update (%d new)",
            entity_type,
            self.external_id,
            new_image_count,
        )
        try:
            deleted, failed = self.backend_adapter.clear_product_images(
                self.external_id
            )
            if failed > 0:
                _logger.warning(
                    "%s %s: failed to delete %d images",
                    entity_type,
                    self.external_id,
                    failed,
                )
        except Exception as e:
            _logger.error(
                "%s %s: error clearing images: %s", entity_type, self.external_id, e
            )

    def _sku_inuse(self, sku):
        search_count = self.env["magento.product.template"].search_count(
            [
                ("backend_id", "=", self.backend_record.id),
                ("external_id", "=", sku),
            ]
        )
        if not search_count:
            search_count += self.env["magento.product.product"].search_count(
                [
                    ("backend_id", "=", self.backend_record.id),
                    ("external_id", "=", sku),
                ]
            )
        # if not search_count:
        #     search_count += self.env['magento.product.bundle'].search_count([
        #         ('backend_id', '=', self.backend_record.id),
        #         ('external_id', '=', sku),
        #     ])
        return search_count > 0

    def _get_sku_proposal(self):
        """Generate or validate SKU for product variant.

        For variants of configurable templates: default_code MUST exist (never generated).
        For variants of simple templates: default_code should exist, but can be generated as fallback.
        """
        # Check if this variant belongs to a configurable template
        template = self.binding.odoo_id.product_tmpl_id
        is_configurable_variant = template.has_variant_attributes

        if is_configurable_variant:
            # Configurable variant: default_code is REQUIRED, never generate
            if not self.binding.default_code:
                raise ValidationError(
                    _(
                        "Cannot export variant '%(name)s' (ID: %(id)s): it belongs to a configurable "
                        "template and MUST have a 'default_code' (SKU) defined. "
                        "SKU cannot be auto-generated for configurable variants."
                    )
                    % {"name": self.binding.display_name, "id": self.binding.id}
                )
            sku = self.binding.default_code[0:64]
        else:
            # Simple product: prefer default_code, generate as fallback
            if self.binding.default_code:
                sku = self.binding.default_code[0:64]
            else:
                _logger.warning(
                    "Product '%s' (ID: %s) has no default_code, generating SKU from name. "
                    "This is not recommended - please set default_code manually.",
                    self.binding.display_name,
                    self.binding.id,
                )
                name = self.binding.display_name
                for value in sorted(
                    self.binding.attribute_value_ids,
                    key=lambda x: x.attribute_id.sequence,
                ):
                    # Check the attribute for the product template - it should have more than one value to be useful here
                    line = self.binding.odoo_id.product_tmpl_id.attribute_line_ids.filtered(
                        lambda l: l.attribute_id == value.attribute_id
                    )
                    if len(line.value_ids) > 1:
                        name = "%s %s %s" % (name, value.attribute_id.name, value.name)
                sku = slugify(name, lowercase=True)[0:64]
        return sku

    def _create_data(self, map_record, **kwargs):
        # Here we do generate a new default code is none exists for now
        if "magento.product.product" in self._apply_on and not self.binding.external_id:
            sku = self._get_sku_proposal()
            i = 0
            original_sku = sku
            while self._sku_inuse(sku):
                sku = "%s-%s" % (original_sku[0 : (63 - len(str(i)))], i)
                i += 1
                _logger.info("Try next sku: %s", sku)
            self.binding.with_context(connector_no_export=True).external_id = sku
            # TODO: Add backend option to enable / disable this !
            # if not self.binding.default_code:
            #     self.binding.with_context(connector_no_export=True).default_code = sku
        return super()._create_data(map_record, **kwargs)

    def _create(self, data, **kwargs):
        """Create the Magento record"""
        # special check on data before export
        res = super()._create(data, **kwargs)
        self.binding.with_context(no_connector_export=True).magento_internal_id = res
        return res

    # def _should_import(self):
    #     """ Before the export, compare the update date
    #     in Magento and the last sync date in Odoo,
    #     Regarding the product_synchro_strategy Choose
    #     to whether the import or the export is necessary
    #     """
    #     assert self.binding
    #     if not self.external_id:
    #         return False
    #     # if self.backend_record.product_synchro_strategy == 'odoo_first':
    #     #     return False
    #     sync = self.binding.sync_date
    #     if not sync:
    #         return True
    #     record = self.backend_adapter.read(self.external_id,
    #                                    attributes=['updated_at'])
    #
    #     if not record['updated_at']:
    #         # in rare case it can be empty, in doubt, import it0
    #         return True
    #     sync_date = odoo.fields.Datetime.from_string(sync)
    #     magento_date = datetime.strptime(record['updated_at'],
    #                                      MAGENTO_DATETIME_FORMAT)
    #     return sync_date < magento_date

    def _update_binding_record_after_write(self, data):
        """
        This will only get called on a new product export - not on updates !
        :param data:
        :return:
        """
        for attr in data.get("custom_attributes", []):
            data[attr["attribute_code"]] = attr["value"]
        if self.backend_record.product_synchro_strategy == "odoo_first":
            mapper = self.component(
                usage="record.update.write", model_name="magento.product.product"
            )
            map_record = mapper.map_record(data)
            update_data = map_record.values(binding=self.binding)
            _logger.info("Got Update data: %s", update_data)
            self.binding.with_context(connector_no_export=True).update(update_data)
            # stock_importer = self.component(
            #     usage='record.importer',
            #     model_name='magento.stock.item'
            # )
            # _logger.info("Data: %s", data)
            # stock_importer.run(data['extension_attributes']['stock_item'])
            self.external_id = data["sku"]
            return False
        # If not odoo_first - then make a full update
        # Do use the importer to update the binding
        importer = self.component(
            usage="record.importer", model_name="magento.product.product"
        )
        _logger.info("Do update record with: %s", data)
        importer.run(data, force=True, binding=self.binding.sudo())

    def _update_binding_record_after_create(self, data):
        """
        This will only get called on a new product export - not on updates !
        :param data:
        :return:
        """
        for attr in data.get("custom_attributes", []):
            data[attr["attribute_code"]] = attr["value"]
        if self.backend_record.product_synchro_strategy == "odoo_first":
            mapper = self.component(
                usage="record.update.create", model_name="magento.product.product"
            )
            map_record = mapper.map_record(data)
            update_data = map_record.values(binding=self.binding)
            _logger.info("Got Update data: %s", update_data)

            self.binding.with_context(connector_no_export=True).update(update_data)
            # stock_importer = self.component(
            #     usage='record.importer',
            #     model_name='magento.stock.item'
            # )
            # stock_importer.run(data['extension_attributes']['stock_item'])
            self.external_id = data["sku"]
            return False
        # Do use the importer to update the binding
        importer = self.component(
            usage="record.importer", model_name="magento.product.product"
        )
        _logger.info("Do update record with: %s", data)
        importer.run(data, force=True, binding=self.binding.sudo())
        self.external_id = data["sku"]

    def _delay_import(self):
        """Schedule an import/export of the record.

        Adapt in the sub-classes when the model is not imported
        using ``import_record``.
        """
        # force is True because the sync_date will be more recent
        # so the import would be skipped
        assert self.external_id
        if self.backend_record.product_synchro_strategy == "magento_first":
            self.binding.import_record(
                self.backend_record, self.external_id, force=True
            )

    def _export_attribute_values(self):
        # Then the attribute values
        record = self.binding
        att_exporter = self.component(
            usage="record.exporter", model_name="magento.product.attribute"
        )
        mpav_exporter = self.component(
            usage="record.exporter", model_name="magento.product.attribute.value"
        )
        exported_attribute_ids = []
        for att_line in record.attribute_line_ids:
            m_att_id = self._get_binding(
                "magento.product.attribute", att_line.attribute_id.id
            )
            if not m_att_id and att_line.attribute_id.id not in exported_attribute_ids:
                # We need to export the attribute first
                self._export_dependency(
                    att_line.attribute_id,
                    "magento.product.attribute",
                    binding_extra_vals={
                        "attribute_set_ids": [(4, record.attribute_set_id.id, 0)]
                        if record.attribute_set_id
                        else False,
                        "attribute_code": att_line.attribute_id.name.lower(),
                    },
                )
                m_att_id = att_line.attribute_id.magento_bind_ids.filtered(
                    lambda m: m.backend_id == self.backend_record
                )
                if m_att_id:
                    exported_attribute_ids.append(m_att_id)
            if not m_att_id.external_id:
                exported_attribute_ids.append(m_att_id)
            m_att_values = []
            needs_sync = False
            for value_id in att_line.value_ids:
                m_value_id = value_id.magento_bind_ids.filtered(
                    lambda m: m.backend_id == self.backend_record
                )
                if not m_value_id:
                    m_att_values.append(
                        (
                            0,
                            0,
                            {
                                "attribute_id": att_line.attribute_id.id,
                                "magento_attribute_id": m_att_id.id,
                                "odoo_id": value_id.id,
                                "backend_id": self.backend_record.id,
                            },
                        )
                    )
                    needs_sync = True
                else:
                    m_att_values.append((4, m_value_id.id))
                    if m_value_id._magento_labels_changed():
                        # Its translations changed in Odoo since they were
                        # last sent: update the option in Magento. A refused
                        # option must not stop the export of the product.
                        try:
                            mpav_exporter.run(m_value_id)
                        except (IDMissingInBackend, JobError) as err:
                            _logger.warning(
                                "Labels of option %s not exported to Magento: %s",
                                m_value_id.external_id,
                                err,
                            )
            if needs_sync:
                # Write the values - then update the attribute
                m_att_id.sudo().with_context(connector_no_export=True).write(
                    {"magento_attribute_value_ids": m_att_values}
                )
                # We only do sync if a new attribute arrived
                for m_att_id in exported_attribute_ids:
                    att_exporter.run(m_att_id)
                for mpav in m_att_id.magento_attribute_value_ids.filtered(
                    lambda m: (
                        m.backend_id == self.backend_record
                        and not m.sync_date
                        and not m.code
                    )
                ):
                    mpav_exporter.run(
                        mpav,
                        binding_attribute=m_att_id,
                        attribute_code=m_att_id.attribute_code,
                    )

    def _export_dependencies(self):
        """Export the dependencies for the record"""
        # Handle categories (works for both templates and variants)
        for extra_category in self.binding.product_category_public_ids:
            self._export_dependency(extra_category, "magento.product.category")

        # Handle product_links only for variants (not templates)
        if hasattr(self.binding, "product_links"):
            for link in self.binding.product_links:
                self._export_dependency(
                    link, "magento.product.product"
                )  # Clear spezial prices here

        self._export_attribute_values()
        self._fill_variant_url_key()
        return


class ProductProductExportMapper(Component):
    _name = "magento.product.product.export.mapper"
    _inherit = "magento.product.export.mapper"
    _apply_on = ["magento.product.product"]

    direct = [
        ("external_id", "sku"),
        ("product_type", "typeId"),
        ("magento_visibility", "visibility"),
    ]

    @mapping
    def names(self, record):
        # 1. Detectar si la plantilla tiene atributos create_variant == 'always'
        always_attrs = [
            line
            for line in record.product_tmpl_id.attribute_line_ids
            if line.attribute_id.create_variant == "always"
        ]
        if not always_attrs:
            return {"name": record.name}

        # 2. Recoger valores de atributos de la variante
        ptav_values = [
            v
            for v in record.product_template_attribute_value_ids
            if v.attribute_id.create_variant == "always"
        ]

        # 3. Separar color si existe
        color_value = None
        other_values = []
        for v in ptav_values:
            if v.attribute_id.name.strip().lower() == "color":
                color_value = v.name
            else:
                other_values.append((v.attribute_id.sequence, v.name))

        # 4. Ordenar el resto por secuencia
        other_values.sort()
        values = []
        if color_value:
            values.append(color_value)
        values.extend([name for seq, name in other_values])

        # 5. Componer el nombre final con espacios delante y detrás del guion
        sep = " - "
        name = sep.join([record.product_tmpl_id.name] + values)
        return {"name": name}

    # @mapping
    # def visibility(self, record):
    #     return {'visibility': record.visibility}

    @mapping
    def status(self, record):
        return {"status": record.magento_status}

    mime_to_extension = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/bmp": "bmp",
        "image/webp": "webp",
        "image/tiff": "tiff",
        "image/svg+xml": "svg",
        "image/x-icon": "ico",
        "image/vnd.microsoft.icon": "ico",
        "image/heif": "heif",
        "image/heic": "heic",
    }

    @mapping
    def get_extension_attributes(self, record):
        data = {}
        data.update(self.get_website_ids(record))
        return {"extension_attributes": data}

    @mapping
    def product_links(self, record):
        if record.product_type == "grouped":
            data = []
            position = 1
            for link in record.product_links:
                position += 1
                data.append(
                    {
                        "sku": record.default_code,
                        "link_type": "associated",
                        "linked_product_sku": link.default_code,
                        "linked_product_type": link.product_type,
                        "position": position,
                        "extension_attributes": {
                            "qty": 0,
                        },
                    }
                )
            return {"product_links": data}
        return {}

    @mapping
    def media_gallery_entries(self, record):
        return gallery_values(self, record)

    def finalize(self, map_record, values):
        return add_image_labels(super().finalize(map_record, values))

    def get_website_ids(self, record):
        if record.website_ids:
            website_ids = [s.external_id for s in record.website_ids]
        else:
            website_ids = [s.external_id for s in record.backend_id.website_ids]
        return {"website_ids": website_ids}

    def category_ids(self, record):
        magento_categ_ids = record.product_category_public_ids.mapped(
            "magento_bind_ids"
        ).filtered(lambda bc: bc.backend_id.id == record.backend_id.id)
        c_ids = magento_categ_ids.mapped("external_id")
        return {"attribute_code": "category_ids", "value": c_ids}

    @mapping
    def weight(self, record):
        return {"weight": int(record.weight) or 0}

    @mapping
    def attribute_set_id(self, record):
        if record.attribute_set_id:
            val = record.attribute_set_id.external_id
        else:
            val = record.backend_id.default_attribute_set_id.external_id
        return {"attributeSetId": val}

    @mapping
    def get_custom_attributes(self, record):
        custom_attributes = []
        if record.product_type in ["simple", "grouped"]:
            for line in record.attribute_line_ids:
                # Deal with Attributes in the 'variant' part of Odoo
                matt_id = line.attribute_id.magento_bind_ids.filtered(
                    lambda m: m.backend_id == record.backend_id
                )
                if not matt_id:
                    continue
                if (
                    not matt_id.is_user_visible
                    or matt_id.create_variant != "no_variant"
                ):
                    continue
                for value_id in line.value_ids:
                    mvalue_id = value_id.magento_bind_ids.filtered(
                        lambda m: m.backend_id == record.backend_id
                    )
                    if not mvalue_id:
                        continue
                    custom_attributes.append(
                        {
                            "attribute_code": matt_id.attribute_code,
                            "value": mvalue_id.external_id.split("_")[1],
                        }
                    )
            for value_id in record.product_template_attribute_value_ids:
                # Deal with Attributes in the 'template' part of Odoo
                if value_id.attribute_id.create_variant != "always":
                    continue
                matt_id = value_id.attribute_id.magento_bind_ids.filtered(
                    lambda m: m.backend_id == record.backend_id
                )
                if not matt_id:
                    continue
                mvalue_id = (
                    value_id.product_attribute_value_id.magento_bind_ids.filtered(
                        lambda m: m.backend_id == record.backend_id
                    )
                )
                if not mvalue_id:
                    continue
                custom_attributes.append(
                    {
                        "attribute_code": matt_id.attribute_code,
                        "value": mvalue_id.external_id.split("_")[1],
                    }
                )
            # The fields mapped on the attributes of the set (URL key, SEO,
            # descriptions...): every text of the product page goes this way.
            custom_attributes.extend(mapped_custom_attributes(record))
            custom_attributes.append(self.category_ids(record))

            # Export tax_class_id from product's taxes_id (inherited from template)
            tax_class_value = self._get_tax_class_id(record)
            if tax_class_value:
                custom_attributes.append(
                    {"attribute_code": "tax_class_id", "value": tax_class_value}
                )

            # Export special_price from pricelist (if configured)
            special_price_attrs = self._get_special_price_attributes(record)
            if special_price_attrs:
                custom_attributes.extend(special_price_attrs)

            _logger.info("Do use custom attributes: %r", custom_attributes)

        return {"custom_attributes": custom_attributes}

    def _get_special_price_attributes(self, record):
        """Get special_price custom attribute for export.

        Returns empty list if no pricelist configured (respects Magento value).
        """
        backend = record.backend_id
        pricelist = backend.special_price_pricelist_id

        if not pricelist:
            return []

        price = pricelist._get_products_price(record.odoo_id, quantity=1.0).get(
            record.odoo_id.id, 0.0
        )

        if not price:
            return [{"attribute_code": "special_price", "value": None}]

        return [{"attribute_code": "special_price", "value": str(price)}]

    @mapping
    def price(self, record):
        if (
            record.backend_id.pricelist_id
            and record.backend_id.pricelist_id.discount_policy == "with_discount"
        ):
            price = record.with_context(
                pricelist=record.backend_id.pricelist_id.id
            ).price
        else:
            price = record["lst_price"]
        return {
            "price": price,
        }
