# -*- coding: utf-8 -*-
# Copyright 2013-2017 Camptocamp SA
# Copyright 2019 Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)


from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create
from odoo.addons.connector.exception import MappingError
from slugify import slugify
import logging
from odoo import _

_logger = logging.getLogger(__name__)


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
        if self.binding.magento_default_code:
            sku = self.binding.magento_default_code[0:64]
        else:
            sku = slugify(self.binding.display_name, to_lower=True)[0:64]
        return sku

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
            # Update / Import stock item
            stock_importer = self.component(
                usage='record.importer',
                model_name='magento.stock.item'
            )
            stock_importer.run(data['extension_attributes']['stock_item'])
            return False
        # Do use the importer to update the binding
        importer = self.component(usage='record.importer',
                                model_name='magento.product.template')
        _logger.info("Do update record with: %s", data)
        importer.run(data, force=True, binding=self.binding)
        self.external_id = data['sku']

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
            # Update / Import stock item
            stock_importer = self.component(
                usage='record.importer',
                model_name='magento.stock.item'
            )
            stock_importer.run(data['extension_attributes']['stock_item'])
            return False
        _logger.info("Got result data: %s", data)

    def _must_update_variants(self):
        return True

    def _export_variants(self):
        record = self.binding
        variant_exporter = self.component(usage='record.exporter', model_name='magento.product.product')
        for p in record.product_variant_ids:
            m_prod = p.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
            created = False
            if not m_prod.id:
                m_prod = self.env['magento.product.product'].with_context(connector_no_export=True).create({
                    'backend_id': self.backend_record.id,
                    'odoo_id': p.id,
                    'attribute_set_id': record.attribute_set_id.id,
                    'magento_configurable_id': record.id,
                    'visibility': '1',
                })
                created = True
            if self._must_update_variants() or created or not m_prod.external_id:
                if created or not m_prod.external_id:
                    _logger.info("Do export variant: %s", m_prod)
                    variant_exporter.run(m_prod)
                else:
                    _logger.info("Do queue export variant: %s", m_prod)
                    delayed = m_prod.with_delay(identity_key=('magento_product_product_%s' % m_prod.id), priority=5).run_sync_to_magento()
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

    def _export_dependencies(self):
        """ Export the dependencies for the record"""
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

    direct = []

    @mapping
    def names(self, record):
        storeview_id = self.work.storeview_id or False
        name = record.name
        if storeview_id:
            value_ids = record.\
            magento_template_attribute_value_ids.filtered(
                lambda att:
                    att.odoo_field_name.name == 'name'
                    and att.store_view_id.id == storeview_id.id
                    and att.attribute_id.create_variant != True
                    and (
                        att.attribute_text != False
                    )
                )
        if len(value_ids) == 0:
            _logger.debug("No name found for %s on storeview %s" % (name, storeview_id))
        else:
            name = value_ids[0].attribute_text
        return {'name': name}


    @mapping
    def visibility(self, record):
        return {'visibility': 4}


    @mapping
    def product_type(self, record):
        product_type = 'simple'
        if record.product_variant_count > 1:
            product_type = 'configurable'
        return {'typeId': product_type}

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
        att_lines = record.attribute_line_ids.filtered(lambda l: l.attribute_id.create_variant in ['always', 'dynamic'] and len(l.value_ids)>1 and len(
            l.attribute_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)) > 0)
        for l in att_lines:
            available_attribute_ids.append(l.attribute_id.id)
        for p in record.product_variant_ids:
            mp = p.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
            if not mp.external_id:
                continue
            # We do check to avoid variants with duplicates attribute sets
            key = ""
            for value in p.attribute_value_ids.filtered(lambda v: v.attribute_id.id in available_attribute_ids).sorted(lambda v: v.attribute_id.id):
                binding_value_ids = value.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
                binding_value = binding_value_ids[0] if binding_value_ids else None
                if not binding_value:
                    continue
                key += "%s%s" % (value.attribute_id.id, value.name)
            if key not in pavalues:
                links.append(mp.magento_id)
                pavalues.append(key)
        return {'configurable_product_links': links}

    def configurable_product_options(self, record):
        option_ids = []
        att_lines = record.attribute_line_ids.filtered(lambda l: l.attribute_id.create_variant in ['always', 'dynamic'] and len(l.attribute_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)) > 0)
        for l in att_lines:
            if not l.value_ids or len(l.value_ids) < 2:
                # Do not export attributes with only one selectable value !
                continue
            m_att_id = l.attribute_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id)
            if not m_att_id:
                raise MappingError("The product attribute %s "
                                   "is not exported yet." %
                                   l.attribute_id.name)
            opt = {
                "id": 1,
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
        c_ids = []
        c_ids.append(record.categ_id.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id).external_id)
        for c in record.categ_ids:
            c_ids.append(c.magento_bind_ids.filtered(lambda m: m.backend_id == record.backend_id).external_id)
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
    def attribute_set_id(self, record):
        if record.attribute_set_id:
            val = record.attribute_set_id.external_id
        else:
            val = record.backend_id.default_attribute_set_id.external_id
        return {'attributeSetId': val}

    @mapping
    def get_custom_attributes(self, record):
        custom_attributes = []
        custom_attributes.append(self.category_ids(record))
        if record.magento_url_key:
            custom_attributes.append({
                'attribute_code': 'url_key',
                'value': record.magento_url_key
            })
        result = {'custom_attributes': custom_attributes}
        return result

    @mapping
    def status(self, record):
        return {'status': '2' if not record.active else record.magento_status}

    @mapping
    def option_products(self, record):
        return {}


    @mapping
    def crossproducts(self, record):
        return {}
