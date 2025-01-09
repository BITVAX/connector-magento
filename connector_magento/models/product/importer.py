# Copyright 2013-2019 Camptocamp SA
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import base64
import logging
import sys
import os

import requests

from odoo import _
from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create, convert
from odoo.addons.connector.exception import MappingError, InvalidDataError
from odoo.addons.connector_magento.components.mapper import normalize_datetime

_logger = logging.getLogger(__name__)


class ProductBatchImporter(Component):
    """ Import the Magento Products.

    For every product category in the list, a delayed job is created.
    Import from a date
    """
    _name = 'magento.product.product.batch.importer'
    _inherit = 'magento.delayed.batch.importer'
    _apply_on = ['magento.product.product']

    def run(self, filters=None):
        """ Run the synchronization """
        from_date = filters.pop('from_date', None)
        to_date = filters.pop('to_date', None)
        external_ids = self.backend_adapter.search(filters,
                                                   from_date=from_date,
                                                   to_date=to_date)
        _logger.info('search for magento products %s returned %s',
                     filters, external_ids)
        for external_id in external_ids:
            self._import_record(external_id)


class CatalogImageImporter(Component):
    """ Import images for a record.

    Usually called from importers, in ``_after_import``.
    For instance from the products importer.
    """
    _name = 'magento.product.image.importer'
    _inherit = 'magento.importer'
    _apply_on = ['magento.product.product']
    _usage = 'product.image.importer'

    def _get_images(self, storeview_id=None, data=None):
        return self.backend_adapter.get_images(
            self.external_id, storeview_id, data=data)

    def _sort_images(self, images):
        """ Returns a list of images sorted by their priority.
        An image with the 'image' type is the the primary one.
        The other images are sorted by their position.

        The returned list is reversed, the items at the end
        of the list have the higher priority.
        """
        if not images:
            return {}

        # place the images where the type is 'image' first then
        # sort them by the reverse priority (last item of the list has
        # the the higher priority)

        def priority(image):
            primary = 'image' in image['types']
            try:
                position = int(image['position'])
            except ValueError:
                position = sys.maxsize
            return (primary, position)

        return sorted(images, key=priority)

    def _get_binary_image(self, image_data):
        url = image_data['url']
        headers = {}
        if self.backend_record.auth_basic_username \
            and self.backend_record.auth_basic_password:
            base64string = base64.b64encode(("%s:%s" % (
                self.backend_record.auth_basic_username,
                self.backend_record.auth_basic_password)
                                             ).encode('utf-8'))
            headers["Authorization"] = "Basic %s" % (
                base64string.decode('utf-8'))
        request = requests.get(
            url, headers=headers, verify=self.backend_record.verify_ssl)
        if request.status_code == 404:
            # the image is just missing, we skip it
            return
        # On any other error, we don't know why we couldn't download the
        # image so we propagate the error, the import will fail and we
        # have to check why it couldn't be accessed
        request.raise_for_status()
        return request.content

    def run(self, external_id, binding, data=None):
        self.external_id = external_id
        images = self._get_images(data=data)
        images = self._sort_images(images)
        image_ids = []
        data = {}
        if len(images):
            # binding.image_ids.unlink()
            # data['image_1920'] = base64.b64encode(self._get_binary_image(images[0]))
        # if len(images) > 1:
        #     images.pop(0)
            c = 0
            for image_data in [ i for i in images if not i['disabled']]:
                binary = self._get_binary_image(image_data)
                if binary:
                    if image_data.get('label','') == '':
                        image_data['label'] = os.path.basename(image_data.get('file','image_{}'.format(c)))
                    image_ids.append({
                        'image_1920': base64.b64encode(binary),
                        'name': image_data.get('label',''),
                        'owner_model': binding.odoo_id._name,
                        'owner_id': binding.odoo_id.id,
                        'sequence': image_data.get('position', c),
                    })
                c = c + 1
            data['image_ids'] = [(6, 0, 0)] + [(0, 0, x) for x in image_ids]
        binding.with_context(connector_no_export=True).write(data)


# TODO: not needed, use inheritance
class BundleImporter(Component):
    """ Can be inherited to change the way the bundle products are
    imported.

    Called at the end of the import of a product.

    Example of action when importing a bundle product:
        - Create a bill of material
        - Import the structure of the bundle in new objects

    By default, the bundle products are not imported: the jobs
    are set as failed, because there is no known way to import them.
    An additional module that implements the import should be installed.

    If you want to create a custom importer for the bundles, you have to
    inherit the Component::

        class BundleImporter(Component):
            _inherit = 'magento.product.bundle.importer'

    And to add the bundle type in the supported product types::

        class MagentoProductProduct(models.Model):
            _inherit = 'magento.product.product'

            @api.model
            def product_type_get(self):
                types = super(MagentoProductProduct, self).product_type_get()
                if 'bundle' not in [item[0] for item in types]:
                    types.append(('bundle', 'Bundle'))
                return types

    """
    _name = 'magento.product.bundle.importer'
    _inherit = 'magento.importer'
    _apply_on = ['magento.product.product']
    _usage = 'product.bundle.importer'

    def run(self, binding, magento_record):
        """ Import the bundle information about a product.

        :param magento_record: product information from Magento
        """


class ProductImportMapper(Component):
    _name = 'magento.product.product.import.mapper'
    _inherit = 'magento.import.mapper'
    _apply_on = ['magento.product.product']

    # TODO :     categ, special_price => minimal_price
    direct = [('name', 'name'),
              ('id', 'magento_internal_id'),
              ('description', 'description'),
              ('weight', 'weight'),
              (convert('status',str), 'magento_status'),
              (convert('visibility',str), 'magento_visibility'),
              ('url_key', 'magento_url_key'),
              # (convert('cost', float), 'standard_price'),
              # (convert('price',float), 'list_price'),
              # ('short_description', 'description_sale'),
              ('sku', 'default_code'),
              ('type_id', 'product_type'),
              (normalize_datetime('created_at'), 'created_at'),
              (normalize_datetime('updated_at'), 'updated_at'),
              ]

    @only_create
    @mapping
    def odoo_id(self, record):
        """ Will bind the product to an existing one with the same code """
        product = self.env['product.product'].search(
            [('default_code', '=', record['sku'])], limit=1)
        if product:
            return {'odoo_id': product.id}

    @mapping
    def external_id(self, record):
        """ Magento 2 to use sku as external id, because this is used as the
        slug in the product REST API """
        if self.collection.version == '2.0':
            return {'external_id': record['sku']}

    # @mapping
    # def is_active(self, record):
    #     """Check if the product is active in Magento
    #     and set active flag in OpenERP
    #     status == 1 in Magento means active.
    #     Magento 2.x returns an integer, 1.x a string """
    #     return {'active': (record.get('status') in (1, '1'))}

    @mapping
    def price(self, record):
        return {
            'standard_price': float(record.get('cost', 0.0)),
            'list_price': float(record.get('price', 0.0))
        }

    @mapping
    def type(self, record):
        if record['type_id'] in ('simple', 'grouped'):
            return {'detailed_type': 'product'}
        elif record['type_id'] in ('virtual', 'downloadable', 'giftcard'):
            return {'detailed_type': 'service'}
        return

    @mapping
    def auto_create_variants(self, record):
        return {'auto_create_variants': False}

    @mapping
    def tax_class_id(self, record):
        # _logger.info("Get tax_class_id from %s", record)
        tax_attribute = [a for a in record['custom_attributes'] if a['attribute_code'] == 'tax_class_id']
        if not tax_attribute:
            return {}
        binder = self.binder_for('magento.account.tax')
        # I have no idea why the binder can not get the record here - as soon as you use sudo it will work...
        # mtax = binder.to_internal(str(tax_attribute[0]['value']), unwrap=False)
        mtax = self.env['magento.account.tax'].sudo().search(
            [('external_id', '=', str(tax_attribute[0]['value'])),
             ('backend_id', '=', self.backend_record.id)]
        )

        if int(tax_attribute[0]['value']) == 0:
            return {}
        if not mtax:
            raise MappingError("The tax class with the id %s "
                               "is not imported." %
                               tax_attribute[0]['value'])
        if not mtax.odoo_id:
            raise MappingError("The tax class with the id %s "
                               "is not mapped to an odoo tax." %
                               tax_attribute[0]['value'])
        data = {}
        if mtax.product_tax_ids:
            data.update({'taxes_id': [(6, 0, mtax.product_tax_ids.ids)]})
        else:
            data.update({'taxes_id': [(4, mtax.odoo_id.id)]})
        if mtax.product_tax_purchase_ids:
            data.update({'supplier_taxes_id': [(6, 0, mtax.product_tax_purchase_ids.ids)]})
        return data

    @mapping
    def website_ids(self, record):
        """ Websites are not returned in Magento 2.x, see
        https://github.com/magento/magento2/issues/3864 """
        website_ids = []
        binder = self.binder_for('magento.website')
        for mag_website_id in record.get('extension_attributes',{}).get('website_ids', []):
            website_binding = binder.to_internal(mag_website_id)
            website_ids.append((4, website_binding.id))
        return {'website_ids': website_ids}

    @mapping
    def categories(self, record):
        """ Fetch categories key for Magento 1.x or category_ids
        for Magento 2.x from product record """
        mag_categories = record.get('category_ids') or record.get(
            'categories', [])
        binder = self.binder_for('magento.product.category')
        category_ids = []
        main_categ_id = None

        for mag_category_id in mag_categories:
            cat = binder.to_internal(mag_category_id, unwrap=True)
            if not cat:
                raise MappingError("The product category with "
                                   "magento id %s is not imported." %
                                   mag_category_id)

            category_ids.append(cat.id)

        result = {'product_category_public_ids': [(6, 0, category_ids)]}
        return result

    @only_create
    @mapping
    def product_links(self, record):
        if record['type_id'] != 'grouped':
            return {}
        product_links = []
        binder = self.binder_for('magento.product.product')
        for link in sorted(record['product_links'], key=lambda x: x['position']):
            product = binder.to_internal(link['linked_product_sku'])
            if not product:
                raise MappingError("The product with sku %s is not imported." %
                                   link['sku'])
            product_links.append(product.id)
        return {'product_links': [(6, 0, product_links)]}

    @mapping
    def backend_id(self, record):
        return {'backend_id': self.backend_record.id}

    @mapping
    def attributes(self, record):
        attribute_binder = self.binder_for('magento.product.attribute')
        value_binder = self.binder_for('magento.product.attribute.value')
        data = {'attribute_line_ids': []}
        value_ids = []
        changes = {}
        binding = self.options.get('binding')
        for attribute in record['custom_attributes']:
            mattribute = attribute_binder.to_internal(attribute['attribute_code'], unwrap=False,
                                                      external_field='attribute_code')
            if mattribute:
                if mattribute.exclude:
                    continue
                if mattribute.field_id:
                    data.update({mattribute.field_id.name: attribute['value']})
                if mattribute.create_variant == 'no_variant' or not mattribute.is_user_defined:
                    continue
                mvalue = value_binder.to_internal("%s_%s" % (mattribute.attribute_id, str(attribute['value'])),
                                                  unwrap=False)
                if not mvalue:
                    raise MappingError("The product attribute value %s in attribute %s is not imported." %
                                       ("%s_%s" % (mattribute.attribute_id, str(attribute['value'])), mattribute.name))
                # Also create an attribute.line.value entrie here
                data['attribute_line_ids'].append((0, 0, {
                    'attribute_id': mattribute.odoo_id.id,
                    'value_ids': [(6, 0, [mvalue.odoo_id.id])],
                }))
                value_ids.append(mvalue.odoo_id.id)
        if binding:
            # data['attribute_line_ids'] = [(5,0,0)] + data['attribute_line_ids']
            lines = data['attribute_line_ids']
            data['attribute_line_ids'] = []
            if set(value_ids) != set(binding.attribute_line_ids.mapped('value_ids').ids):
                for line in lines:
                    odoo_value_ids = binding.attribute_line_ids.filtered(
                        lambda l: l.attribute_id.id == line[2]['attribute_id']).mapped('value_ids').ids
                    if set(odoo_value_ids) != set(line[2]['value_ids'][0][2]):
                        changes[line[2]['attribute_id']] = {
                            'old': odoo_value_ids,
                            'new': line[2]['value_ids'][0][2],
                            'line_id': odoo_value_ids and binding.attribute_line_ids.filtered(
                                lambda l: l.attribute_id.id == line[2]['attribute_id']).id or False
                        }
                if len(changes):
                    for key, value in changes.items():
                        if value['line_id']:
                            data['attribute_line_ids'].append((1, value['line_id'], {
                                'value_ids': [(6, 0, value['new'])]
                            }))
                        else:
                            data['attribute_line_ids'].append((0, 0, {
                                'attribute_id': key,
                                'value_ids': [(6, 0, value['new'])]
                            }))

        if self.options.get('binding_template_id') and len(value_ids):
            if data.get('attribute_line_ids'):
                del data['attribute_line_ids']
            binding_template_id = self.options['binding_template_id']
            template_id = binding_template_id.odoo_id
            ptav_ids = template_id.mapped('attribute_line_ids.product_template_value_ids').filtered(
                lambda x: x.product_attribute_value_id.id in value_ids)
            data['product_template_attribute_value_ids'] = [(6, 0, ptav_ids.ids)]

        return data

    @mapping
    def attribute_set_id(self, record):
        binder = self.binder_for('magento.product.attribute.set')
        attribute_set = binder.to_internal(record['attribute_set_id'])
        return {'attribute_set_id': attribute_set.id}


class ProductImporter(Component):
    _name = 'magento.product.product.importer'
    _inherit = 'magento.importer'
    _apply_on = ['magento.product.product']
    _magento_id_field = 'sku'

    def _import_bundle_dependencies(self):
        """ Import the dependencies for a Bundle """
        if self.collection.version == '1.7':
            for dependency in [
                selection for option in
                self.magento_record['_bundle_data']['options']
                for selection in option['selections']]:
                self._import_dependency(dependency['product_id'],
                                        'magento.product.product')
        else:
            for dependency in [
                product_link for option in self.magento_record[
                    'extension_attributes']['bundle_product_options']
                for product_link in option['product_links']]:
                self._import_dependency(dependency['sku'],
                                        'magento.product.product')

    def _import_dependencies(self):
        """ Import the dependencies for the record"""
        record = self.magento_record
        # import related categories
        self._import_dependency(record['attribute_set_id'],
                                'magento.product.attribute.set')
        # Check and import attributes and values if they do not exist
        self._import_attributes(record)
        for mag_category_id in (record.get('category_ids') or record.get(
            'categories', [])):
            self._import_dependency(mag_category_id,
                                    'magento.product.category')
        if record['type_id'] == 'bundle':
            self._import_bundle_dependencies()
        if record['type_id'] == 'grouped':
            for child in record['product_links']:
                self._import_dependency(child['linked_product_sku'],
                                        'magento.product.product')

    def _import_attributes(self, record):
        attribute_binder = self.binder_for('magento.product.attribute')
        value_binder = self.binder_for('magento.product.attribute.value')
        for attribute in record['custom_attributes']:
            mattribute = attribute_binder.to_internal(attribute['attribute_code'], unwrap=False,
                                                      external_field='attribute_code')
            if not mattribute:
                self._import_dependency(attribute['attribute_code'], 'magento.product.attribute')
                mattribute = attribute_binder.to_internal(attribute['attribute_code'], unwrap=False,
                                                          external_field='attribute_code')
            if mattribute and mattribute.is_user_defined and mattribute.create_variant != 'no_variant':
                mvalue = value_binder.to_internal("%s_%s" % (mattribute.attribute_id, str(attribute['value'])),
                                                  unwrap=False)
                if not mvalue:
                    self._import_dependency(str(attribute['value']),
                                            'magento.product.attribute.value',
                                            attribute_code=attribute['attribute_code'],
                                            magento_attribute=mattribute)

    def _validate_product_type(self, data):
        """ Check if the product type is in the selection (so we can
        prevent the `except_orm` and display a better error message).
        """
        product_type = data['product_type']
        product_model = self.env['magento.product.product']
        types = product_model.product_type_get()
        available_types = [typ[0] for typ in types]
        if product_type not in available_types:
            raise InvalidDataError("The product type '%s' is not "
                                   "yet supported in the connector." %
                                   product_type)

    def _must_skip(self):
        """ Hook called right after we read the data from the backend.

        If the method returns a message giving a reason for the
        skipping, the import will be interrupted and the message
        recorded in the job (if the import is called directly by the
        job, not by dependencies).

        If it returns None, the import will continue normally.

        :returns: None | str | unicode
        """
        if self.magento_record['type_id'] == 'configurable':
            return _('The configurable product is not imported in Odoo, '
                     'because only the simple products are used in the sales '
                     'orders.')

    def _validate_data(self, data):
        """ Check if the values to import are correct

        Pro-actively check before the ``_create`` or
        ``_update`` if some fields are missing or invalid

        Raise `InvalidDataError`
        """
        self._validate_product_type(data)

    def _create(self, data, **kwargs):
        if 'binding_template_id' in kwargs:
            binding_template_id = kwargs['binding_template_id']
            template_id = binding_template_id.odoo_id
            data['product_tmpl_id'] = template_id.id

            # data['magento_configurable_id'] = kwargs['_binding_template_id'].id
            # Name is set on product template on configurables
            if 'name' in data:
                del data['name']
            if 'standard_price' in data:
                del data['standard_price']
            if 'lst_price' in data:
                del data['lst_price']
        binding = super()._create(data, **kwargs)
        if not binding.active:
            # Disable reordering rules that has been created automatically
            binding.orderpoint_ids.write({'active': False})
        self.backend_record.add_checkpoint(binding)
        return binding

    def _update(self, binding, data, **kwargs):
        # enable/disable reordering rules before updating the product as Odoo
        # do not allow to disable a product while having active reordering
        # rules on it
        if 'binding_template_id' in kwargs:
            data['product_tmpl_id'] = kwargs['binding_template_id'].odoo_id.id
            # data['magento_configurable_id'] = kwargs['_binding_template_id'].id
            # Name is set on product template on configurables
            if 'name' in data:
                del data['name']
            if 'standard_price' in data:
                del data['standard_price']
            if 'lst_price' in data:
                del data['lst_price']
        if 'active' in data and not data.get('active'):
            binding.mapped('orderpoint_ids').write({'active': False})
        res = super()._update(binding, data, **kwargs)
        return res

    def _after_import_attributes(self, binding):
        ptav_obj = self.env['product.template.attribute.value']
        ptav_ids = []
        for attribute_line in binding.attribute_line_ids:
            for value in attribute_line.value_ids:
                ptav = ptav_obj.search([
                    ('product_attribute_value_id', '=', value.id),
                    ('attribute_line_id', '=', attribute_line.id)
                ])
                if not ptav:
                    ptav = ptav_obj.create({
                        'product_attribute_value_id': value.id,
                        'attribute_line_id': attribute_line.id,
                    })
                if ptav and binding.id not in ptav.ptav_product_variant_ids.ids:
                    ptav_ids.append(ptav.id)
        if ptav_ids:
            binding.write({'product_template_attribute_value_ids': [(6, 0, ptav_ids)]})

    def _after_import(self, binding):
        """ Hook called at the end of the import """
        self._after_import_attributes(binding)
        translation_importer = self.component(
            usage='translation.importer',
        )
        translation_importer.run(
            self.external_id,
            binding,
            mapper='magento.product.product.import.mapper'
        )
        image_importer = self.component(usage='product.image.importer')
        image_importer.run(self.external_id, binding, data=self.magento_record)

        if self.magento_record['type_id'] == 'bundle':
            bundle_importer = self.component(usage='product.bundle.importer')
            bundle_importer.run(binding, self.magento_record)

    def _preprocess_magento_record(self):
        for attr in self.magento_record.get('custom_attributes', []):
            self.magento_record[attr['attribute_code']] = attr['value']
        return


class ProductInventoryExporter(Component):
    _name = 'magento.product.product.exporter'
    _inherit = 'magento.exporter'
    _apply_on = ['magento.product.product']
    _usage = 'product.inventory.exporter'

    _map_backorders = {'use_default': 0,
                       'no': 0,
                       'yes': 1,
                       'yes-and-notification': 2,
                       }

    def _get_data(self, binding, fields):
        result = {}
        if 'magento_qty' in fields:
            result.update({
                'qty': binding.magento_qty,
                # put the stock availability to "out of stock"
                'is_in_stock': int(binding.magento_qty > 0)
            })
        if 'manage_stock' in fields:
            manage = binding.manage_stock
            result.update({
                'manage_stock': int(manage == 'yes'),
                'use_config_manage_stock': int(manage == 'use_default'),
            })
        if 'backorders' in fields:
            backorders = binding.backorders
            result.update({
                'backorders': self._map_backorders[backorders],
                'use_config_backorders': int(backorders == 'use_default'),
            })
        return result

    def run(self, binding, fields):
        """ Export the product inventory to Magento """
        external_id = self.binder.to_external(binding)
        data = self._get_data(binding, fields)
        self.backend_adapter.update_inventory(external_id, data)


class ProductUpdateWriteMapper(Component):
    _name = 'magento.product.product.update.write.mapper'
    _inherit = 'magento.import.mapper'
    _usage = 'record.update.write'
    _apply_on = ['magento.product.product']

    direct = [('url_key', 'magento_url_key'),
              ('sku', 'external_id'),
              ('id', 'magento_internal_id'),
              (normalize_datetime('created_at'), 'created_at'),
              (normalize_datetime('updated_at'), 'updated_at'),
              ]

    @mapping
    def website_ids(self, record):
        website_ids = []
        binder = self.binder_for('magento.website')
        for mag_website_id in record['extension_attributes']['website_ids']:
            website_binding = binder.to_internal(mag_website_id)
            website_ids.append((4, website_binding.id))
        return {'website_ids': website_ids}

    @mapping
    def attribute_set_id(self, record):
        binder = self.binder_for('magento.product.attribute.set')
        attribute_set = binder.to_internal(record['attribute_set_id'])
        return {'attribute_set_id': attribute_set.id, }

    @mapping
    def no_stock_sync(self, record):
        return {'no_stock_sync': self.backend_record.no_stock_sync}

    # @mapping
    # def category_positions(self, record):
    #     # Only for simple products
    #     if not record['type_id'] == 'simple':
    #         return {}
    #     if not 'extension_attributes' in record or not'category_links' in record['extension_attributes']:
    #         return {}
    #     data = []
    #     for position in record['extension_attributes']['category_links']:
    #         binder = self.binder_for('magento.product.category')
    #         magento_category = binder.to_internal(position['category_id'])
    #         if not magento_category:
    #             raise ValueError('Magento category with id %s is missing on odoo side.' % position['category_id'])
    #         magento_position = self.env['magento.product.position'].search([
    #             ('magento_product_category_id', '=', magento_category.id),
    #             ('product_template_id', '=', self.options.binding.odoo_id.product_tmpl_id.id),
    #         ])
    #         if magento_position:
    #             data.append((1, magento_position.id, {
    #                 'position': position['position'],
    #             }))
    #         else:
    #             data.append((0, 0, {
    #                 'product_template_id': self.options.binding.odoo_id.product_tmpl_id.id,
    #                 'magento_product_category_id': magento_category.id,
    #                 'position': position['position'],
    #             }))
    #     return {'magento_product_position_ids': data}


class ProductUpdateCreateMapper(Component):
    _name = 'magento.product.product.update.create.mapper'
    _inherit = 'magento.product.product.update.write.mapper'
    _usage = 'record.update.create'
    _apply_on = ['magento.product.product']
