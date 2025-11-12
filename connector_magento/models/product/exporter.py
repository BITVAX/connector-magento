# -*- coding: utf-8 -*-
# Copyright 2013-2017 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import base64
import logging
from datetime import datetime

import magic
from slugify import slugify

import odoo
from odoo import _
from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping
from odoo.addons.connector_magento.components.backend_adapter import MAGENTO_DATETIME_FORMAT
import odoo.addons.connector_magento.models.get_exported_value


_logger = logging.getLogger(__name__)


class ProductProductExporter(Component):
    _name = 'magento.product.product.exporter'
    _inherit = 'magento.exporter'
    _apply_on = ['magento.product.product']

    def _run(self, fields=None, **kwargs):
        """ Flow of the synchronization, implemented in inherited classes"""
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
                return _('Nothing to export.')

            # Clear ALL existing images BEFORE update to maintain sync
            self._clear_existing_images_before_update(record)

            data = self._update(record, **kwargs)
            if data:
                self._update_binding_record_after_write(data)
        else:
            record = self._create_data(map_record, fields=fields)
            if not record:
                return _('Nothing to export.')
            data = self._create(record)
            if not data:
                raise UserWarning('Create did not returned anything on %s with binding id %s', self._name,
                                  self.binding.id)
            self._update_binding_record_after_create(record)
            self.external_id = record.get('sku')
            self.binding.recompute_magento_qty()
            self.binding.export_inventory(fields=['magento_qty'])
        return _('Record exported with ID %s on Magento.') % self.external_id

    def _clear_existing_images_before_update(self, record):
        """Clear all existing images from Magento before updating product.

        This prevents image duplication when syncing products.
        Called during update operations, before sending new image data.

        :param record: Product data dictionary to be sent to Magento
        :return: None
        """
        new_image_count = len(record.get('media_gallery_entries', []))
        entity_type = "Product" if self._apply_on == ['magento.product.product'] else "Template"

        _logger.debug("%s %s: clearing existing images before update (%d new)",
                     entity_type, self.external_id, new_image_count)
        try:
            deleted, failed = self.backend_adapter.clear_product_images(self.external_id)
            if failed > 0:
                _logger.warning("%s %s: failed to delete %d images",
                              entity_type, self.external_id, failed)
        except Exception as e:
            _logger.error("%s %s: error clearing images: %s",
                         entity_type, self.external_id, e)

    def _sku_inuse(self, sku):
        search_count = self.env['magento.product.template'].search_count([
            ('backend_id', '=', self.backend_record.id),
            ('external_id', '=', sku),
        ])
        if not search_count:
            search_count += self.env['magento.product.product'].search_count([
                ('backend_id', '=', self.backend_record.id),
                ('external_id', '=', sku),
            ])
        # if not search_count:
        #     search_count += self.env['magento.product.bundle'].search_count([
        #         ('backend_id', '=', self.backend_record.id),
        #         ('external_id', '=', sku),
        #     ])
        return search_count > 0

    def _get_sku_proposal(self):
        if self.binding.default_code:
            sku = self.binding.default_code[0:64]
        else:
            name = self.binding.display_name
            for value in sorted(self.binding.attribute_value_ids, key=lambda x: x.attribute_id.sequence):
                # Check the attribute for the product template - it should have more than one value to be useful here
                line = self.binding.odoo_id.product_tmpl_id.attribute_line_ids.filtered(
                    lambda l: l.attribute_id == value.attribute_id)
                if len(line.value_ids) > 1:
                    name = "%s %s %s" % (name, value.attribute_id.name, value.name)
            sku = slugify(name, lowercase=True)[0:64]
        return sku

    def _create_data(self, map_record, **kwargs):
        # Here we do generate a new default code is none exists for now
        if 'magento.product.product' in self._apply_on and not self.binding.external_id:
            sku = self._get_sku_proposal()
            i = 0
            original_sku = sku
            while self._sku_inuse(sku):
                sku = "%s-%s" % (original_sku[0:(63 - len(str(i)))], i)
                i += 1
                _logger.info("Try next sku: %s", sku)
            self.binding.with_context(connector_no_export=True).external_id = sku
            # TODO: Add backend option to enable / disable this !
            '''
            if not self.binding.default_code:
                self.binding.with_context(connector_no_export=True).default_code = sku
            '''
        return super(ProductProductExporter, self)._create_data(map_record, **kwargs)

    def _create(self, data, **kwargs):
        """ Create the Magento record """
        # special check on data before export
        res = super(ProductProductExporter, self)._create(data, **kwargs)
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
        for attr in data.get('custom_attributes', []):
            data[attr['attribute_code']] = attr['value']
        if self.backend_record.product_synchro_strategy == 'odoo_first':
            mapper = self.component(
                usage='record.update.write',
                model_name='magento.product.product'
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
            self.external_id = data['sku']
            return False
        # If not odoo_first - then make a full update
        # Do use the importer to update the binding
        importer = self.component(usage='record.importer',
                                  model_name='magento.product.product')
        _logger.info("Do update record with: %s", data)
        importer.run(data, force=True, binding=self.binding.sudo())

    def _update_binding_record_after_create(self, data):
        """
        This will only get called on a new product export - not on updates !
        :param data:
        :return:
        """
        for attr in data.get('custom_attributes', []):
            data[attr['attribute_code']] = attr['value']
        if self.backend_record.product_synchro_strategy == 'odoo_first':
            mapper = self.component(
                usage='record.update.create',
                model_name='magento.product.product'
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
            self.external_id = data['sku']
            return False
        # Do use the importer to update the binding
        importer = self.component(usage='record.importer',
                                  model_name='magento.product.product')
        _logger.info("Do update record with: %s", data)
        importer.run(data, force=True, binding=self.binding.sudo())
        self.external_id = data['sku']

    def _delay_import(self):
        """ Schedule an import/export of the record.

        Adapt in the sub-classes when the model is not imported
        using ``import_record``.
        """
        # force is True because the sync_date will be more recent
        # so the import would be skipped
        assert self.external_id
        if self.backend_record.product_synchro_strategy == 'magento_first':
            self.binding.import_record(self.backend_record, self.external_id, force=True)

    def _export_attribute_values(self):
        # Then the attribute values
        record = self.binding
        att_exporter = self.component(usage='record.exporter', model_name='magento.product.attribute')
        mpav_exporter = self.component(usage='record.exporter', model_name='magento.product.attribute.value')
        exported_attribute_ids = []
        for att_line in record.attribute_line_ids:
            m_att_id = self._get_binding('magento.product.attribute',
                                         att_line.attribute_id.id)
            if not m_att_id and att_line.attribute_id.id not in exported_attribute_ids:
                # We need to export the attribute first
                self._export_dependency(att_line.attribute_id, "magento.product.attribute", binding_extra_vals={
                    'attribute_set_ids' : [(4,record.attribute_set_id.id,0)] if record.attribute_set_id else False,
                    'attribute_code': att_line.attribute_id.name.lower(),
                })
                m_att_id = att_line.attribute_id.magento_bind_ids.filtered(
                    lambda m: m.backend_id == self.backend_record)
                if m_att_id:
                    exported_attribute_ids.append(m_att_id)
            if not m_att_id.external_id:
                exported_attribute_ids.append(m_att_id)
            m_att_values = []
            needs_sync = False
            for value_id in att_line.value_ids:
                m_value_id = value_id.magento_bind_ids.filtered(lambda m: m.backend_id == self.backend_record)
                if not m_value_id:
                    m_att_values.append((0, 0, {
                        'attribute_id': att_line.attribute_id.id,
                        'magento_attribute_id': m_att_id.id,
                        'odoo_id': value_id.id,
                        'backend_id': self.backend_record.id,
                    }))
                    needs_sync = True
                else:
                    m_att_values.append((4, m_value_id.id))
            if needs_sync:
                # Write the values - then update the attribute
                m_att_id.sudo().with_context(connector_no_export=True).write({'magento_attribute_value_ids': m_att_values})
                # We only do sync if a new attribute arrived
                for m_att_id in exported_attribute_ids:
                    att_exporter.run(m_att_id)
                for mpav in m_att_id.magento_attribute_value_ids.filtered(lambda m: m.backend_id == self.backend_record and not m.sync_date and not m.code):
                    mpav_exporter.run(mpav, binding_attribute=m_att_id,attribute_code=m_att_id.attribute_code)

    def _export_dependencies(self):
        """ Export the dependencies for the record"""
        # Handle categories (works for both templates and variants)
        for extra_category in self.binding.product_category_public_ids:
            self._export_dependency(extra_category, "magento.product.category")

        # Handle product_links only for variants (not templates)
        if hasattr(self.binding, 'product_links'):
            for link in self.binding.product_links:
                self._export_dependency(link, "magento.product.product")  # Clear spezial prices here

        self._export_attribute_values()
        return


class ProductProductExportMapper(Component):
    _name = 'magento.product.export.mapper'
    _inherit = 'magento.export.mapper'
    _apply_on = ['magento.product.product']

    direct = [
        ('external_id', 'sku'),
        ('product_type', 'typeId'),
        ('magento_visibility', 'visibility'),
    ]

    @mapping
    def names(self, record):
        # 1. Detectar si la plantilla tiene atributos create_variant == 'always'
        always_attrs = [
            line for line in record.product_tmpl_id.attribute_line_ids
            if line.attribute_id.create_variant == 'always'
        ]
        if not always_attrs:
            return {'name': record.name}

        # 2. Recoger valores de atributos de la variante
        ptav_values = [
            v for v in record.product_template_attribute_value_ids
            if v.attribute_id.create_variant == 'always'
        ]

        # 3. Separar color si existe
        color_value = None
        other_values = []
        for v in ptav_values:
            if v.attribute_id.name.strip().lower() == 'color':
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
        sep = ' - '
        name = sep.join([record.product_tmpl_id.name] + values)
        return {'name': name}

    # @mapping
    # def visibility(self, record):
    #     return {'visibility': record.visibility}

    @mapping
    def status(self, record):
        return {'status': record.magento_status}

    mime_to_extension = {
        'image/jpeg': 'jpg',
        'image/png': 'png',
        'image/gif': 'gif',
        'image/bmp': 'bmp',
        'image/webp': 'webp',
        'image/tiff': 'tiff',
        'image/svg+xml': 'svg',
        'image/x-icon': 'ico',
        'image/vnd.microsoft.icon': 'ico',
        'image/heif': 'heif',
        'image/heic': 'heic'
    }
    @mapping
    def get_extension_attributes(self, record):
        data = {}
        data.update(self.get_website_ids(record))
        return {'extension_attributes': data}

    @mapping
    def product_links(self, record):
        if record.product_type == 'grouped':
            data = []
            position = 1
            for link in record.product_links:
                position += 1
                data.append({
                    'sku': record.default_code,
                    'link_type': 'associated',
                    'linked_product_sku': link.default_code,
                    'linked_product_type': link.product_type,
                    'position': position,
                    'extension_attributes': {
                        'qty': 0,
                    }
                })
            return {'product_links': data}
        return {}

    @mapping
    def media_gallery_entries(self, record):
        if record.image_ids:
            media_gallery_entries = []
            mime = magic.Magic(mime=True)
            image_count=0
            img_without_variants=record.image_ids.filtered(lambda i: len(i.product_variant_ids) == 0)
            if len(record.image_ids) > 1 and img_without_variants:
                # If we have multiple images and some are not linked to variants - use them first
                images = record.image_ids - img_without_variants
                if not images:
                    images = record.image_ids
            else:
                images = record.image_ids
            for image in images:
                if not image.image_1920:
                    continue
                mimetype = mime.from_buffer(base64.b64decode(image.image_1920))
                extension = self.mime_to_extension.get(mimetype, 'jpg')
                filename = f"{slugify(image.name or record.default_code)}_{record.id}_{image_count}.{extension}"
                image_count += 1
                media_gallery_entries.append({
                    "media_type": "image",
                    "label": image.name or record.name,
                    "position": image_count,
                    "disabled": False,
                    # "file": filename,
                    "content": {
                        "base64_encoded_data": image.image_1920,
                        "type": mimetype,
                        "name": filename,
                    },
                })
            if len(media_gallery_entries):
                media_gallery_entries[0]['types'] = ['image', 'small_image', 'thumbnail']
            return {'media_gallery_entries': media_gallery_entries}
        return {}

    def get_website_ids(self, record):
        if record.website_ids:
            website_ids = [s.external_id for s in record.website_ids]
        else:
            website_ids = [s.external_id for s in record.backend_id.website_ids]
        return {'website_ids': website_ids}

    def category_ids(self, record):
        magento_categ_ids = record.product_category_public_ids.mapped('magento_bind_ids').filtered(
            lambda bc: bc.backend_id.id == record.backend_id.id)
        c_ids = magento_categ_ids.mapped('external_id')
        return {
            'attribute_code': 'category_ids',
            'value': c_ids
        }

    @mapping
    def weight(self, record):
        return {'weight': int(record.weight) or 0}

    @mapping
    def attribute_set_id(self, record):
        if record.attribute_set_id:
            val = record.attribute_set_id.external_id
        else:
            val = record.backend_id.default_attribute_set_id.external_id
        return {'attributeSetId': val}

    @mapping
    def get_custom_attributes(self, record):
        custom_attributes = []
        if record.product_type in ['simple', 'grouped']:
            for line in record.attribute_line_ids:
                """ Deal with Attributes in the 'variant' part of Odoo"""
                matt_id = line.attribute_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
                if not matt_id:
                    continue
                if not matt_id.is_user_visible or matt_id.create_variant != 'no_variant':
                    continue
                for value_id in line.value_ids:
                    mvalue_id = value_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
                    if not mvalue_id:
                        continue
                    custom_attributes.append({
                        'attribute_code': matt_id.attribute_code,
                        'value': mvalue_id.external_id.split('_')[1]
                    })
            for value_id in record.product_template_attribute_value_ids:
                """ Deal with Attributes in the 'template' part of Odoo"""
                if value_id.attribute_id.create_variant != 'always':
                    continue
                matt_id = value_id.attribute_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
                if not matt_id:
                    continue
                mvalue_id = value_id.product_attribute_value_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
                if not mvalue_id:
                    continue
                custom_attributes.append({
                    'attribute_code': matt_id.attribute_code,
                    'value': mvalue_id.external_id.split('_')[1]
                })
            if record.attribute_set_id:
                for matt_id in record.attribute_set_id.attribute_ids.filtered(lambda a: a.field_id):
                    if record[matt_id.field_id.sudo().name]:
                        custom_attributes.append({
                            'attribute_code': matt_id.attribute_code,
                            'value': get_exported_value(matt_id, record)
                        })
            custom_attributes.append(self.category_ids(record))
            if record.magento_url_key:
                custom_attributes.append({
                    'attribute_code': 'url_key',
                    'value': record.magento_url_key
                })
            # Add meta fields as custom attributes
            if record.odoo_id.meta_title:
                custom_attributes.append({
                    'attribute_code': 'meta_title',
                    'value': record.odoo_id.meta_title
                })

            if record.odoo_id.meta_keyword:
                custom_attributes.append({
                    'attribute_code': 'meta_keyword',
                    'value': record.odoo_id.meta_keyword
                })

            if record.odoo_id.meta_description:
                custom_attributes.append({
                    'attribute_code': 'meta_description',
                    'value': record.odoo_id.meta_description
                })

            _logger.info("Do use custom attributes: %r", custom_attributes)

        return {'custom_attributes': custom_attributes}

    @mapping
    def price(self, record):
        if record.backend_id.pricelist_id and record.backend_id.pricelist_id.discount_policy == 'with_discount':
            price = record.with_context(pricelist=record.backend_id.pricelist_id.id).price
        else:
            price = record['lst_price']
        return {
            'price': price,
        }
