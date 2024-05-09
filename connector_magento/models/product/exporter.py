# -*- coding: utf-8 -*-
# Copyright 2013-2017 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import odoo
from datetime import datetime

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create
from slugify import slugify
from odoo.addons.connector_magento.components.backend_adapter import MAGENTO_DATETIME_FORMAT
import magic
import base64
import logging
from odoo import _

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
            data = self._update(record,**kwargs)
            if data:
                self._update_binding_record_after_write(data)
        else:
            record = self._create_data(map_record, fields=fields)
            if not record:
                return _('Nothing to export.')
            data = self._create(record)
            if not data:
                raise UserWarning('Create did not returned anything on %s with binding id %s', self._name, self.binding.id)
            self._update_binding_record_after_create(data)
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
        if not search_count:
            search_count += self.env['magento.product.bundle'].search_count([
                ('backend_id', '=', self.backend_record.id),
                ('external_id', '=', sku),
            ])
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
                sku = "%s-%s" % (original_sku[0:(63-len(str(i)))], i)
                i += 1
                _logger.info("Try next sku: %s", sku)
            self.binding.with_context(connector_no_export=True).external_id = sku
            # TODO: Add backend option to enable / disable this !
            '''
            if not self.binding.default_code:
                self.binding.with_context(connector_no_export=True).default_code = sku
            '''
        return super(ProductProductExporter, self)._create_data(map_record, **kwargs)

    def _create(self, data,  **kwargs):
        """ Create the Magento record """
        # special check on data before export
        res = super(ProductProductExporter, self)._create(data, **kwargs)
        self.binding.with_context(
            no_connector_export=True).magento_id = data['id']
        return res

    def _update(self, data, **kwargs):
        """ Create the Magento record """
        # special check on data before export
        return super(ProductProductExporter, self)._update(data, **kwargs)

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
            stock_importer = self.component(
                usage='record.importer',
                model_name='magento.stock.item'
            )
            _logger.info("Data: %s", data)
            stock_importer.run(data['extension_attributes']['stock_item'])
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
            stock_importer = self.component(
                usage='record.importer',
                model_name='magento.stock.item'
            )
            stock_importer.run(data['extension_attributes']['stock_item'])
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
            self._export_dependency(link.product_id, "magento.product.product")        # Clear spezial prices here
        self._export_attribute_values()
        return

    # def _export_base_image(self):
    #     def sort_by_position(elem):
    #         return elem.position
    #
    #     if not self.binding.export_base_image or not self.binding.odoo_id.image:
    #         # Do not export base image is set or no base image - so check if there was already base image exported - if so - delete it
    #         for media_binding in self.binding.magento_image_bind_ids.filtered(lambda m: m.type == 'product_image'):
    #             media_binding.unlink()
    #         return
    #     # We do export the base image on position 0
    #     mbinding = None
    #     for media_binding in sorted(self.binding.magento_image_bind_ids.filtered(lambda m: m.type == 'product_image'), key=sort_by_position):
    #         mbinding = media_binding
    #         break
    #     # Create new media binding entry for main image
    #     mime = magic.Magic(mime=True)
    #     mimetype = mime.from_buffer(base64.b64decode(self.binding.odoo_id.image))
    #     extension = 'png' if mimetype == 'image/png' else 'jpeg'
    #     if 'magento.product.template' in self._apply_on:
    #         model_key = 'magento_product_tmpl_id'
    #     else:
    #         model_key = 'magento_product_id'
    #     # Find unique filename
    #     filename = "%s.%s" % (slugify(self.binding.odoo_id.name, to_lower=True), extension)
    #     i = 0
    #     while self.env['magento.product.media'].search_count([
    #         ('backend_id', '=', self.binding.backend_id.id),
    #         ('file', '=', filename)
    #     ]) > 0:
    #         filename = "%s-%s.%s" % (slugify(self.binding.odoo_id.name, to_lower=True), i, extension)
    #         i += 1
    #     if not mbinding:
    #         mbinding = self.env['magento.product.media'].sudo().with_context(connector_no_export=True).create({
    #             'backend_id': self.binding.backend_id.id,
    #             model_key: self.binding.id,
    #             'label': self.binding.odoo_id.name,
    #             'file': filename,
    #             'type': 'product_image',
    #             'position': 0,
    #             'mimetype': mimetype,
    #             'image_type_image': True,
    #             'image_type_small_image': True,
    #             'image_type_thumbnail': True,
    #         })
    #     else:
    #         mbinding.sudo().with_context(connector_no_export=True).update({
    #             'label': self.binding.odoo_id.name,
    #             'file': filename,
    #             'position': 0,
    #             'mimetype': mimetype,
    #             'image_type_image': True,
    #             'image_type_small_image': True,
    #             'image_type_thumbnail': True,
    #         })
    #     self._export_dependency(mbinding.sudo(), "magento.product.media", force_update=True)

    def _export_stock(self):
        for stock_item in self.binding.magento_stock_item_ids:
            stock_item.sync_to_magento()

    # def _get_magento_image_ids(self):
    #     record = self.backend_adapter.read(self.external_id)
    #     return [str(entry['id']) for entry in record.get('media_gallery_entries', []) if entry['media_type'] == 'image']
    #
    # def _get_odoo_magento_image_ids(self):
    #     iids = []
    #     # Only return images which are already exported from our side
    #     for image in self.binding.magento_image_bind_ids.filtered(lambda i: i.external_id):
    #         iids.append(image.external_id)
    #     return iids
    #
    # def _check_one_image_main(self):
    #     type_image_ids = self.binding.magento_image_bind_ids.filtered(lambda i: i.image_type_image)
    #     if type_image_ids:
    #         return
    #     found = False
    #     # Do recalc nice positions - to avoid duplicate positions
    #     position = 1
    #     for image in self.binding.magento_image_bind_ids.sorted(key=lambda x: x.position):
    #         _logger.info("Do set new image position on %s to %s", self.binding, position)
    #         if image.image_type_image:
    #             image.with_context(connector_no_export=True).update({
    #                 'position': 0,
    #             })
    #         else:
    #             image.with_context(connector_no_export=True).update({
    #                 'position': position,
    #             })
    #         position += 1
    #     for image in self.binding.magento_image_bind_ids.sorted(key=lambda x: x.position):
    #         if hasattr(image, 'odoo_id') and image.odoo_id and hasattr(image.odoo_id, 'is_primary_image') and image.odoo_id.is_primary_image:
    #             image.update({
    #                 'image_type_image': True,
    #                 'image_type_small_image': True,
    #                 'image_type_thumbnail': True,
    #                 'image_type_swatch': True,
    #             })
    #             found = True
    #             break
    #     if not found:
    #         for image in (self.binding.magento_image_bind_ids
    #                         .filtered(lambda i: i.type == 'product_image')
    #                         .sorted(key=lambda x: x.position)):
    #             image.update({
    #                 'image_type_image': True,
    #                 'image_type_small_image': True,
    #                 'image_type_thumbnail': True,
    #                 'image_type_swatch': True,
    #             })
    #             found = True
    #             break
    #     if not found:
    #         for image in self.binding.magento_image_bind_ids.filtered(lambda i: i.type == 'product_image_ids').sorted(key=sort_by_position):
    #             image.update({
    #                 'image_type_image': True,
    #                 'image_type_small_image': True,
    #                 'image_type_thumbnail': True,
    #                 'image_type_swatch': True,
    #             })
    #             found = True
    #             break
    #     if not found:
    #         for image in self.binding.magento_image_bind_ids.filtered(lambda i: i.type == 'attribute_image').sorted(key=sort_by_position):
    #             image.update({
    #                 'image_type_image': True,
    #                 'image_type_small_image': True,
    #                 'image_type_thumbnail': True,
    #                 'image_type_swatch': True,
    #             })
    #             break
    #
    # def _delete_broken_image_bindings(self):
    #     _logger.info("Do delete these broken image bindings: %s", self.binding.magento_image_bind_ids.filtered(lambda i: not i.type))
    #     self.binding.magento_image_bind_ids.filtered(lambda i: not i.type).unlink()
    #
    # def _sync_images(self):
    #     '''
    #     Do delete images which are on magento side - but not on odoo side
    #     :return:
    #     '''
    #     self._delete_broken_image_bindings()
    #     magento_ids = self._get_magento_image_ids()
    #     odoo_magento_ids = self._get_odoo_magento_image_ids()
    #     magento_delete_ids = [mid for mid in magento_ids if mid not in odoo_magento_ids]
    #     image_backend_adapter = self.component(usage='backend.adapter', model_name='magento.product.media')
    #     for m_image_id in magento_delete_ids:
    #         image_backend_adapter.delete((m_image_id, self.external_id,))
    #     # Now check images which are on odoo side - but not on magento side
    #     odoo_delete_ids = [mid for mid in odoo_magento_ids if mid not in magento_ids]
    #     # Delete bindings
    #     if odoo_delete_ids:
    #         _logger.info("Do delete these image bindings which are not anymore on magento side: %s", self.env['magento.product.media'].search([
    #             ('external_id', 'in', odoo_delete_ids),
    #             ('backend_id', '=', self.backend_record.id),
    #         ]))
    #         self.env['magento.product.media'].search([
    #             ('external_id', 'in', odoo_delete_ids),
    #             ('backend_id', '=', self.backend_record.id),
    #         ]).with_context(connector_no_export=True).sudo().unlink()
    #     # Check for main image
    #     self._check_one_image_main()
    def _after_export(self):
        '''
        Base _after_export method
        :return:
        '''
        _logger.info("AFTEREXPORT: In _after_export at %s", __name__)
        # if not hasattr(self, 'light_sync') or not self.light_sync:
        #     self._sync_images()
        #     self._export_base_image()
        #     self._export_stock()


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
        return {'status': '2' if not record.active else record.magento_status}

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

    def get_website_ids(self, record):
        if record.website_ids:
            website_ids = [s.external_id for s in record.website_ids]
        else:
            website_ids = [s.external_id for s in record.backend_id.website_ids]
        return {'website_ids': website_ids}

    def category_ids(self, record):
        magento_categ_ids = record.product_category_public_ids.mapped('magento_bind_ids').filtered(lambda bc: bc.backend_id.id == record.backend_id.id)
        c_ids = magento_categ_ids.mapped('external_id')
        return {
            'attribute_code': 'category_ids',
            'value': c_ids
        }

    @mapping
    def weight(self, record):
        if record.weight:
            val = record.weight
        else:
            val = 0
        return {'weight': val}

    '''
    @mapping
    def media_gallery_entries(self, record):
        data = []
        for image in record.magento_image_bind_ids:
            data.append({
                'id': image.external_id,
                "media_type": "image",
                "label": image.label,
                "position": image.position,
            })
        return {'media_gallery_entries': data}
    '''
    @mapping
    def attribute_set_id(self, record):
        if record.attribute_set_id:
            val = record.attribute_set_id.external_id
        else:
            val = record.backend_id.default_attribute_set_id.external_id
        return {'attributeSetId': val}

    def get_custom_attributes(self, record):
        custom_attributes = []
        if record.product_type in ['simple','grouped']:
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
                    custom_attributes.append({
                        'attribute_code': matt_id.attribute_code,
                        'value': record[matt_id.field_id.name]
                    })
            custom_attributes.append(self.category_ids(record))
            _logger.info("Do use custom attributes: %r", custom_attributes)

        return {'custom_attributes': custom_attributes}

    @mapping
    def price(self, record):
        if record.backend_id.pricelist_id and record.backend_id.pricelist_id.discount_policy=='with_discount':
            price = record.with_context(pricelist=record.backend_id.pricelist_id.id).price
        else:
            price = record['lst_price']
        return {
            'price': price,
        }