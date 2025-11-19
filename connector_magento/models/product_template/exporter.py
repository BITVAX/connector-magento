# -*- coding: utf-8 -*-
# Copyright 2013-2017 Camptocamp SA
# Copyright 2019 Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import base64
import magic
from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create
from odoo.addons.connector.exception import MappingError
from slugify import slugify
import logging
from odoo import _

_logger = logging.getLogger(__name__)

# import odoo.addons.connector_magento.models.get_exported_value

def get_exported_value(matt_id, record):
    if matt_id.field_id.ttype == 'boolean':
        return int(record[matt_id.field_id.sudo().name])

    return record[matt_id.field_id.sudo().name]

class ProductTemplateDefinitionExporter(Component):
    _name = 'magento.product.template.exporter'
    _inherit = 'magento.product.product.exporter'
    _apply_on = ['magento.product.template']

    def run(self, binding, *args, **kwargs):
        self.light_sync = kwargs.get('light_sync', False)
        _logger.info("Set light_sync=%s", self.light_sync)
        return super(ProductTemplateDefinitionExporter, self).run(binding)

    def _run(self, fields=None):
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

        _logger.info("External ID is: %s", self.external_id)
        if self.external_id and self.binding.magento_id:
            _logger.info("External ID is: %s", self.external_id)
            record = self._update_data(map_record, fields=fields)
            if not record:
                return _('Nothing to export.')

            # Clear ALL existing images BEFORE update to maintain sync
            self._clear_existing_images_before_update(record)

            data = self._update(record)
            if data:
                self._update_binding_record_after_write(data)
        else:
            record = self._create_data(map_record, fields=fields)
            if not record:
                return _('Nothing to export.')
            data = self._create(record)
            if not data:
                raise UserWarning('Create did not returned anything on %s with binding id %s', self._name, self.binding.id)
            self._update_binding_record_after_create(record)
        return _('Record exported with ID %s on Magento.') % self.external_id

    def _get_sku_proposal(self):
        if self.binding.code_prefix:
            return self.binding.code_prefix
        # Fallback: si el template tiene variantes, usar los 5 primeros caracteres del default_code de la primera variante que lo tenga
        for variant in self.binding.product_variant_ids:
            if variant.default_code:
                return variant.default_code[:5]
        # Fallback del fallback: lógica previa
        if self.binding.magento_default_code:
            sku = self.binding.magento_default_code[0:64]
        else:
            sku = slugify(self.binding.display_name, to_lower=True)[0:64]
        return sku

    def _search_existing(self, data):
        """ Search for existing product template in Magento by SKU (idempotency).

        Before creating a product template, search if it already exists in Magento.
        This handles retry scenarios where the template was created in Magento
        but the Odoo commit failed, leaving an orphaned product.

        :param data: dict with product data including 'sku'
        :return: SKU (external_id) if found, None otherwise
        """
        sku = data.get('sku')
        if not sku:
            return None

        try:
            # Try to read the product from Magento by SKU
            existing = self.backend_adapter.read(sku)
            if existing and existing.get('sku'):
                _logger.info(
                    "Found existing product template in Magento with SKU %s "
                    "(likely from previous failed commit), linking binding",
                    sku
                )
                # Also save the internal ID if available
                if existing.get('id'):
                    self.binding.with_context(
                        connector_no_export=True
                    ).magento_id = existing['id']
                return existing['sku']
        except Exception as e:
            # Product doesn't exist, will create it
            _logger.debug(
                "Product template with SKU %s not found in Magento (expected for new product): %s",
                sku, str(e)
            )
            return None

    def _create_data(self, map_record, **kwargs):
        # Here we do generate a new default code is none exists for now
        if not self.binding.external_id:
            sku = self._get_sku_proposal()
            i = 0
            original_sku = sku
            while self._sku_inuse(sku):
                sku = "%s-%s" % (original_sku[0:(63-len(str(i)))], i)
                i += 1
                _logger.info("Try next sku: %s", sku)
            self.binding.with_context(connector_no_export=True).external_id = sku
        return super(ProductTemplateDefinitionExporter, self)._create_data(map_record, **kwargs)

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
                model_name='magento.product.template'
            )
            map_record = mapper.map_record(data)
            update_data = map_record.values(binding=self.binding)
            _logger.info("Got Create data: %s", update_data)
            self.binding.with_context(connector_no_export=True).write(update_data)
            self.binding.export_inventory()
            return False
        # Do use the importer to update the binding
        importer = self.component(usage='record.importer',
                                model_name='magento.product.template')
        _logger.info("Do update record with: %s", data)
        importer.run(data, force=True, binding=self.binding)
        self.external_id = data['sku']
        self.magento_id = data['id']
        self.binding.export_inventory()

    def _update_binding_record_after_write(self, data):
        for attr in data.get('custom_attributes', []):
            data[attr['attribute_code']] = attr['value']
        if self.backend_record.product_synchro_strategy == 'odoo_first':
            mapper = self.component(
                usage='record.update.write',
                model_name='magento.product.template'
            )
            map_record = mapper.map_record(data)
            update_data = map_record.values(binding=self.binding)
            _logger.info("Got Update data: %s for binding %s", update_data, self.binding)
            self.binding.with_context(connector_no_export=True).write(update_data)
            # # Update / Import stock item
            # stock_importer = self.component(
            #     usage='record.importer',
            #     model_name='magento.stock.item'
            # )
            # stock_importer.run(data['extension_attributes']['stock_item'])
            return False
        _logger.info("Got result data: %s", data)

    def _must_update_variants(self):
        return True

    def _export_variants(self):
        """Export product variants as independent delayed jobs.

        Each variant export is now a separate job with its own transaction.
        This ensures atomic transactionality: if a variant export fails,
        only that specific job is rolled back, not all previous variants.
        This prevents orphaned data in Magento without Odoo bindings.
        """
        record = self.binding
        for p in record.product_variant_ids:
            m_prod = p.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
            if not m_prod:
                m_prod = self.env['magento.product.product'].with_context(connector_no_export=True).create({
                    'backend_id': self.backend_record.id,
                    'odoo_id': p.id,
                    'attribute_set_id': record.attribute_set_id.id,
                    # 'magento_configurable_id': record.id,
                    'magento_visibility': '1',
                })

            # Always use delayed jobs for all variants (new or existing)
            # to ensure each variant has its own independent transaction
            if self._must_update_variants() or not m_prod.external_id:
                _logger.info("Queueing export for variant: %s", m_prod)
                delayed = m_prod.with_delay(
                    identity_key=('magento_product_product_%s' % m_prod.id),
                    priority=5
                ).export_record()
                job = self.env['queue.job'].search([('uuid', '=', delayed.uuid)])
                self.binding.odoo_id.with_context(connector_no_export=True).job_ids += job

    def _create_attribute_lines(self):
        record = self.binding
        for line in record.attribute_line_ids:
            m_line = line.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
            m_att_id = line.attribute_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
            if not m_att_id:
                raise MappingError("The product attribute %s "
                                   "is not exported yet." %
                                   line.attribute_id.name)

            if not m_line:
                self.env['magento.product.template.attribute.line'].sudo().create({
                    'odoo_id': line.id,
                    'magento_attribute_id': m_att_id.id,
                    'magento_template_id': record.id,
                    'label': m_att_id.name,
                    'position': m_att_id.sequence,
                })

    def get_or_create_dependency_job(self, odoo_record, binding_model, binding_extra_vals=None):
        """Create or get binding and return a delayable export job.

        This method ensures the binding is created within the CALLER's transaction,
        then returns a job that will export it. The export happens in the job's
        own transaction with its own commit.

        :param odoo_record: the Odoo record (e.g., product.category)
        :param binding_model: binding model name (e.g., 'magento.product.category')
        :param binding_extra_vals: extra values for binding creation
        :return: delayable job or None if already exported
        """
        # Check if binding exists
        binding = odoo_record.magento_bind_ids.filtered(
            lambda m: m.backend_id == self.backend_record
        )

        # Create binding if doesn't exist (in caller's transaction)
        if not binding:
            bind_values = {
                'backend_id': self.backend_record.id,
                'odoo_id': odoo_record.id,
            }
            if binding_extra_vals:
                bind_values.update(binding_extra_vals)

            binding = self.env[binding_model].with_context(
                connector_no_export=True
            ).create(bind_values)

        # Only create job if needs export
        if not binding.external_id:
            return binding.delayable(priority=20).export_record()

        return None

    def _export_dependencies(self):
        """ Export the dependencies for the record (synchronous legacy method)"""
        super(ProductTemplateDefinitionExporter, self)._export_dependencies()
        self._create_attribute_lines()
        if not hasattr(self, 'light_sync') or not self.light_sync:
            self._export_variants()
        return

    def _after_export(self):
        _logger.info("AFTEREXPORT: In _after_export at %s", __name__)
        super(ProductTemplateDefinitionExporter, self)._after_export()
        storeview_id = self.work.storeview_id if hasattr(self.work, 'storeview_id') else False
        if storeview_id:
            # We are already in the storeview specific export
            return
        # TODO Fix and enable again
        '''
        for storeview_id in self.env['magento.storeview'].search([('backend_id', '=', self.backend_record.id)]):
            self.binding.export_product_template_for_storeview(storeview_id=storeview_id)
        '''


class ProductTemplateExportMapper(Component):
    _name = 'magento.product.template.export.mapper'
    _inherit = 'magento.export.mapper'
    _apply_on = ['magento.product.template']

    direct = [
        ('name', 'name'),
    ]

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
    def visibility(self, record):
        return {'visibility': 4}


    @mapping
    def product_type(self, record):
        return {'typeId': 'configurable'}

    @mapping
    def default_code(self, record):
        return {'sku': record.external_id}

    @mapping
    def price(self, record):
        if record.backend_id.pricelist_id.discount_policy=='with_discount':
            price = record.with_context(pricelist=record.backend_id.pricelist_id.id).price
        else:
            price = record['list_price']
        return {'price': price}

    @mapping
    def get_extension_attributes(self, record):
        data = {}
        data.update(self.get_website_ids(record))
        data.update(self.configurable_product_options(record))
        data.update(self.configurable_product_links(record))
        return {'extension_attributes': data}

    def configurable_product_links(self, record):
        links = []
        pavalues = []
        available_attribute_ids = []
        att_lines = record.attribute_line_ids.filtered(
            lambda l: l.attribute_id.create_variant != 'no_variant'
            and len(l.attribute_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)) > 0
        )
        for l in att_lines:
            available_attribute_ids.append(l.attribute_id.id)
        for p in record.product_variant_ids:
            mp = p.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
            if not mp.external_id:
                continue
            # Adaptación Odoo 16: usar product_template_attribute_value_ids
            key = ""
            ptavs = p.product_template_attribute_value_ids.filtered(
                lambda ptav: ptav.attribute_id.id in available_attribute_ids
            ).sorted(lambda ptav: ptav.attribute_id.id)
            for ptav in ptavs:
                binding_value_ids = ptav.product_attribute_value_id.magento_bind_ids.filtered(
                    lambda m: m.backend_id == record.backend_id
                )
                binding_value = binding_value_ids[0] if binding_value_ids else None
                if not binding_value:
                    continue
                key += "%s%s" % (ptav.attribute_id.id, ptav.product_attribute_value_id.name)
            if key not in pavalues:
                links.append(mp.magento_internal_id)
                pavalues.append(key)
        return {'configurable_product_links': links}

    def configurable_product_options(self, record):
        option_ids = []
        att_lines = record.attribute_line_ids.filtered(lambda l: l.attribute_id.create_variant in ['always', 'dynamic'] and len(l.attribute_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)) > 0)
        for l in att_lines:
            if not l.value_ids:
                # Do not export attributes with only one selectable value !
                continue
            m_att_id = l.attribute_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
            if not m_att_id:
                raise MappingError("The product attribute %s "
                                   "is not exported yet." %
                                   l.attribute_id.name)
            opt = {
                "id": 0,
                "attribute_id": m_att_id.external_id,
                "label": m_att_id.attribute_code,
                "position": 0,
                "values": []
            }
            for v in l.value_ids:
                v_ids = v.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
                for v_id in v_ids:
                    opt['values'].append({"value_index": v_id.external_id.split('_')[1]})

            option_ids.append(opt)
        return {'configurable_product_options': option_ids}

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
        if record.weight:
            val = record.weight
        else:
            val = 0
        return {'weight': val}

    @mapping
    def media_gallery_entries(self, record):
        if record.image_ids:
            media_gallery_entries = []
            mime = magic.Magic(mime=True)
            image_count = 0
            for image in [record.odoo_id]:
                mimetype = mime.from_buffer(base64.b64decode(image.image_1920))
                extension = self.mime_to_extension.get(mimetype, 'jpg')
                filename = f"{slugify(image.name or record.external_id)}_{record.id}_{image_count}.{extension}"
                image_count += 1
                media_gallery_entries.append({
                    "media_type": "image",
                    "label": image.name or record.name,
                    "position": image_count,
                    "disabled": False,
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

    @mapping
    def attribute_set_id(self, record):
        if record.attribute_set_id:
            val = record.attribute_set_id.external_id
        else:
            val = record.backend_id.default_attribute_set_id.external_id
        return {'attributeSetId': val}

    def get_non_configurable_attributes(self, record):
        non_configurable_attributes = []
        for line in record.attribute_line_ids:
            if line.attribute_id.create_variant in ['always', 'dynamic'] or len(line.value_ids) > 1 or not line.value_ids:
                continue
            m_att_id = line.attribute_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
            if not m_att_id:
                raise MappingError("The product attribute %s "
                                   "is not exported yet." %
                                   line.attribute_id.name)
            # Take the first value only - non configurable attributes should not have more than one value anyway
            v = line.value_ids[0]
            v_ids = v.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
            if not v_ids:
                raise MappingError("The product attribute value %s "
                                   "is not exported yet." %
                                   v.name)
            non_configurable_attributes.append({
                'attribute_code': m_att_id.attribute_code,
                'value': v_ids[0].external_id.split('_')[1]
            })

        return  non_configurable_attributes


    @mapping
    def get_custom_attributes(self, record):
        custom_attributes = []
        custom_attributes.append(self.category_ids(record))
        custom_attributes.extend(self.get_non_configurable_attributes(record))
        if record.attribute_set_id:
            for matt_id in record.attribute_set_id.attribute_ids.filtered(lambda a: a.field_id):
                if record[matt_id.field_id.sudo().name]:
                    custom_attributes.append({
                        'attribute_code': matt_id.attribute_code,
                        'value': get_exported_value(matt_id,record)
                    })
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

        result = {'custom_attributes': custom_attributes}
        return result

    @mapping
    def status(self, record):
        return {'status': '2' if not record.active else record.magento_status}

    # Meta fields now handled in get_custom_attributes method

    @mapping
    def option_products(self, record):
        return {}


    @mapping
    def crossproducts(self, record):
        return {}
