# -*- coding: utf-8 -*-
# Copyright 2025
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import logging
from odoo.addons.component.core import Component

_logger = logging.getLogger(__name__)


class MagentoProductAdapter(Component):
    """
    Base adapter for product operations in Magento

    Provides methods for product management including image deletion,
    inventory updates, and product read operations.
    This is an abstract component inherited by both product.product
    and product.template adapters.

    IMPORTANT: Image deletion only supports Magento 2.x. Magento 1.7 will raise NotImplementedError.
    """
    _name = 'magento.product.adapter'
    _inherit = 'magento.adapter'
    # No _apply_on: this is an abstract base component

    def get_media(self, external_id):
        """Get media gallery entries for a product (Magento 2.x only)

        Retrieves the list of media entries (images) for a product.
        API: GET /V1/products/{sku}/media

        :param external_id: Product SKU
        :return: List of media gallery entry dicts with 'id', 'file', 'types', etc.
        :raises NotImplementedError: If Magento version is 1.7
        """
        if self.collection.version == '1.7':
            raise NotImplementedError(
                "get_media is not supported for Magento 1.7. "
                "Only Magento 2.x is supported."
            )

        # Magento 2: GET /V1/products/{sku}/media
        media_entries = self._call(
            'products/%s/media' % self.escape(external_id),
            None
        )
        return media_entries if isinstance(media_entries, list) else []

    def delete_product_image(self, external_id, entry_id):
        """Delete a single media gallery entry (Magento 2.x only)

        :param external_id: Product SKU
        :param entry_id: Media entry ID from Magento
        :return: True if deleted successfully, False otherwise
        """
        if self.collection.version == '1.7':
            raise NotImplementedError(
                "Image deletion is not supported for Magento 1.7. "
                "Only Magento 2.x is supported."
            )

        # Magento 2: DELETE /V1/products/{sku}/media/{entryId}
        try:
            self._call(
                'products/%s/media/%s' % (self.escape(external_id), entry_id),
                None,
                http_method='delete'
            )
            return True
        except Exception as e:
            _logger.error("Failed to delete image %s for product %s: %s",
                        entry_id, external_id, e)
            return False

    def clear_product_images(self, external_id):
        """Delete all media gallery entries for a product (Magento 2.x only)

        Called BEFORE updating a product to prevent image duplication.
        Retrieves current images and deletes them one by one.

        :param external_id: Product SKU
        :return: Tuple (deleted_count, failed_count)
        :raises NotImplementedError: If Magento version is 1.7
        """
        if self.collection.version == '1.7':
            raise NotImplementedError(
                "Image clearing is not supported for Magento 1.7. "
                "Only Magento 2.x is supported."
            )

        # Get current media entries using get_media()
        try:
            media_entries = self.get_media(external_id)
        except Exception as e:
            _logger.warning("Could not get media for product %s: %s", external_id, e)
            return (0, 0)

        if not media_entries:
            _logger.debug("No images to delete for product %s", external_id)
            return (0, 0)

        deleted = 0
        failed = 0

        for entry in media_entries:
            entry_id = entry.get('id')
            if not entry_id:
                continue

            if self.delete_product_image(external_id, entry_id):
                deleted += 1
                _logger.debug("Deleted image %s from product %s", entry_id, external_id)
            else:
                failed += 1

        _logger.info("Product %s: deleted %d images, %d failed",
                    external_id, deleted, failed)
        return (deleted, failed)

    def read(self, external_id, storeview=None, attributes=None, **kwargs):
        """ Returns the information of a record

        :rtype: dict
        """
        # pylint: disable=method-required-super
        if self.collection.version == '1.7':
            return self._call(
                'ol_catalog_product.info',
                [int(external_id), storeview, attributes, 'id'])
        res = super(MagentoProductAdapter, self).read(
            external_id, attributes=attributes, storeview=storeview)
        if res:
            for attr in res.get('custom_attributes', []):
                res[attr['attribute_code']] = attr['value']
        return res

    def get_images(self, external_id, storeview_id=None, data=None):
        """ Fetch image metadata either by querying Magento 1.x, or extracting
        it from the product data for Magento 2.x """
        if self.collection.version == '1.7':
            return self._call('product_media.list',
                              [int(external_id), storeview_id, 'id'])

        res = []
        # Fetch base media url from storeview
        storeview = (
            self.env['magento.storeview'].browse(storeview_id) if storeview_id
            else self.env['magento.storeview'].search(
                [('backend_id', '=', self.collection.id),
                 ('code', '=', 'default')]))
        base_url = (storeview.base_media_url or
                    '%s/media/' % self.backend_record.location)

        for entry in data.get('media_gallery_entries', []):
            if entry['media_type'] == 'image':
                entry['url'] = '%scatalog/product/%s' % (
                    base_url, entry['file'])
                res.append(entry)
        return res

    def update_inventory(self, external_id, data):
        """ Update the default stock. For Magento2, first retrieve the stock
        item that applies to this stock for the product. """
        if self.collection.version == '1.7':
            # product_stock.update is too slow
            return self._call('oerp_cataloginventory_stock_item.update',
                              [int(external_id), data])

        # Magento2
        data = {'stockItem': data}
        res = self._call('stockItems/%s' % self.escape(external_id), None)
        if isinstance(res, dict):
            res = [res]
        item_id = 0
        for item in res:
            if item['stock_id'] == 1:
                item_id = item['item_id']
                break
        else:
            raise ValueError(
                'No stock item found for product %s for default stock_id 1' %
                external_id)
        self._call('products/%s/stockItems/%s' % (
            self.escape(external_id), item_id), data, http_method='put')
