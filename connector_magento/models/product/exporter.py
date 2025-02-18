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
        return _('Record exported with ID %s on Magento.') % self.external_id

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

    def _should_import(self):
        """ Before the export, compare the update date
        in Magento and the last sync date in Odoo,
        Regarding the product_synchro_strategy Choose
        to whether the import or the export is necessary
        """
        assert self.binding
        if not self.external_id:
            return False
        # if self.backend_record.product_synchro_strategy == 'odoo_first':
        #     return False
        sync = self.binding.sync_date
        if not sync:
            return True
        record = self.backend_adapter.read(self.external_id,
                                           attributes=['updated_at'])
        if not record['updated_at']:
            # in rare case it can be empty, in doubt, import it0
            return True
        sync_date = odoo.fields.Datetime.from_string(sync)
        magento_date = datetime.strptime(record['updated_at'],
                                         MAGENTO_DATETIME_FORMAT)
        return sync_date < magento_date

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
        exported_attribute_ids = []
        for att_line in record.attribute_line_ids:
            m_att_id = self._get_binding('magento.product.attribute',
                                         att_line.attribute_id.id)
            if not m_att_id and att_line.attribute_id.id not in exported_attribute_ids:
                # We need to export the attribute first
                self._export_dependency(att_line.attribute_id, "magento.product.attribute", binding_extra_vals={
                    'create_variant': True,
                })
                m_att_id = att_line.attribute_id.magento_bind_ids.filtered(
                    lambda m: m.backend_id == self.backend_record)
                exported_attribute_ids.append(att_line.attribute_id.id)
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
                m_att_id.sudo().with_context(connector_no_export=True).magento_attribute_value_ids = m_att_values
                # We only do sync if a new attribute arrived
                att_exporter.run(m_att_id)

    def _export_dependencies(self):
        """ Export the dependencies for the record"""
        for extra_category in self.binding.product_category_public_ids:
            self._export_dependency(extra_category, "magento.product.category")
        for link in self.binding.product_links:
            self._export_dependency(link, "magento.product.product")  # Clear spezial prices here
        self._export_attribute_values()
        return

    def _export_stock(self):
        for stock_item in self.binding.magento_stock_item_ids:
            stock_item.sync_to_magento()


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
        return {'name': record.name}

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
            for image in record.image_ids:
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
                    "types": [
                        "image",
                        "small_image",
                        "thumbnail",
                    ],
                    # "file": filename,
                    "content": {
                        "base64_encoded_data": image.image_1920,
                        "type": mimetype,
                        "name": filename,
                    },
                })
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
                for value_id in line.value_ids:
                    mvalue_id = value_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
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
                            'value': record[matt_id.field_id.sudo().name]
                        })
            custom_attributes.append(self.category_ids(record))
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
