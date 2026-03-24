# Copyright 2015-2019 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import unittest
import urllib.error
import mock
from base64 import b64encode

from odoo import models
from odoo.addons.component.core import WorkContext, Component
from odoo.addons.component.tests.common import (
    TransactionComponentRegistryCase,
)
from .. import components
from ..models.product.importer import CatalogImageImporter
from .common import MockResponseImage

# simple square of 4 px filled with green in png, used for the product
# Valid minimal 1x1 PNG image (proper binary, no UTF-8 encoding issues)
import struct
import zlib


def _make_minimal_png():
    """Create a valid minimal 1x1 red pixel PNG."""
    ihdr_data = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b'IHDR' + ihdr_data) & 0xffffffff
    ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + struct.pack('>I', ihdr_crc)
    raw = b'\x00\xff\x00\x00'  # filter=0, R=255 G=0 B=0
    compressed = zlib.compress(raw)
    idat_crc = zlib.crc32(b'IDAT' + compressed) & 0xffffffff
    idat = struct.pack('>I', len(compressed)) + b'IDAT' + compressed + struct.pack('>I', idat_crc)
    iend_crc = zlib.crc32(b'IEND') & 0xffffffff
    iend = struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc)
    return b'\x89PNG\r\n\x1a\n' + ihdr + idat + iend


PNG_IMG_4PX_GREEN = _make_minimal_png()
B64_PNG_IMG_4PX_GREEN = b64encode(PNG_IMG_4PX_GREEN)


class TestImportProductImage(TransactionComponentRegistryCase):
    """ Test the imports of the image of the products. """

    def setUp(self):
        super(TestImportProductImage, self).setUp()
        self._setup_registry(self)
        self.addCleanup(self._teardown_registry, self)
        self.backend_model = self.env['magento.backend']
        warehouse = self.env.ref('stock.warehouse0')
        self.backend = self.backend_model.create(
            {'name': 'Test Magento',
             'version': '1.7',
             'location': 'http://magento',
             'username': 'odoo',
             'warehouse_id': warehouse.id,
             'password': 'odoo42'}
        )

        # Ensure attribute set exists for product bindings
        self.attr_set = self.env['magento.product.attribute.set'].create({
            'name': 'Default Test Set',
            'backend_id': self.backend.id,
        })

        category_model = self.env['product.category.public']
        existing_category = category_model.create({'name': 'all'})
        self.create_binding_no_export(
            'magento.product.category',
            existing_category,
            1
        )
        self.product_model = self.env['magento.product.product']

        # Use a stub for the product adapter, which is called
        # during the tests by the image importer
        class StubProductAdapter(Component):
            _name = 'stub.product.adapter'
            _collection = 'magento.backend'
            _usage = 'backend.adapter'
            _apply_on = 'magento.product.product'

            def get_images(self, external_id, storeview_id=None, data=None):
                return [
                    {'exclude': '1',
                     'file': '/i/n/ink-eater-krylon-bombear-destroyed-tee-2.jpg',  # noqa
                     'label': '',
                     'position': '0',
                     'types': ['thumbnail'],
                     'url': 'http://localhost:9100/media/catalog/product/i/n/ink-eater-krylon-bombear-destroyed-tee-2.jpg'},  # noqa
                    {'exclude': '0',
                     'file': '/i/n/ink-eater-krylon-bombear-destroyed-tee-1.jpg',  # noqa
                     'label': '',
                     'position': '3',
                     'types': ['small_image'],
                     'url': 'http://localhost:9100/media/catalog/product/i/n/ink-eater-krylon-bombear-destroyed-tee-1.jpg'},  # noqa
                    {'exclude': '0',
                     'file': '/m/a/connector_magento_1.png',
                     'label': '',
                     'position': '4',
                     'types': [],
                     'url': 'http://localhost:9100/media/catalog/product/m/a/connector_magento_1.png'},  # noqa
                ]

        # build the Stub and the component we want to test
        self._build_components(StubProductAdapter,
                               components.core.BaseMagentoConnectorComponent,
                               components.importer.MagentoImporter,
                               CatalogImageImporter)
        self.work = WorkContext(model_name='magento.product.product',
                                collection=self.backend,
                                components_registry=self.comp_registry)
        self.image_importer = self.work.component_by_name(
            'magento.product.image.importer'
        )

    def create_binding_no_export(self, model_name, odoo_id, external_id=None,
                                 **cols):
        if isinstance(odoo_id, models.BaseModel):
            odoo_id = odoo_id.id
        values = {
            'backend_id': self.backend.id,
            'odoo_id': odoo_id,
            'external_id': external_id,
        }
        if cols:
            values.update(cols)
        return self.env[model_name].with_context(
            connector_no_export=True
        ).create(values)

    def test_image_priority(self):
        """ Check if the images are sorted in the correct priority """
        file1 = {'file': 'file1', 'types': ['image'], 'position': '10'}
        file2 = {'file': 'file2', 'types': ['thumbnail'], 'position': '3'}
        file3 = {'file': 'file3', 'types': ['thumbnail'], 'position': '4'}
        file4 = {'file': 'file4', 'types': [], 'position': '10'}
        images = [file2, file1, file4, file3]
        # Sorted by (primary=False first, then position ascending)
        # file1 has 'image' type (primary=True) so goes last (highest priority)
        self.assertEqual(self.image_importer._sort_images(images),
                         [file2, file3, file4, file1])

    def test_import_images_404(self):
        """ An image responds a 404 error, skip and take the first valid.
        The importer creates base_multi_image.image records on the template.
        """
        url_tee1 = ('http://localhost:9100/media/catalog/product'
                    '/i/n/ink-eater-krylon-bombear-destroyed-tee-1.jpg')
        url_tee2 = ('http://localhost:9100/media/catalog/product/'
                    'i/n/ink-eater-krylon-bombear-destroyed-tee-2.jpg')

        product = self.env['product.product'].create({'name': 'Test 404 Product'})
        binding = self.create_binding_no_export(
            'magento.product.product', product, '404-test',
            attribute_set_id=self.attr_set.id)

        with mock.patch('requests.get') as requests_get:
            def image_url_response(url, headers=None, verify=None):
                if url in (url_tee1, url_tee2):
                    return MockResponseImage('', code=404)
                else:
                    return MockResponseImage(PNG_IMG_4PX_GREEN)
            requests_get.side_effect = image_url_response

            self.image_importer.run(111, binding)

        # The valid image should have been imported to the template
        template = product.product_tmpl_id
        images = self.env['base_multi_image.image'].search([
            ('owner_id', '=', template.id),
            ('owner_model', '=', 'product.template'),
        ])
        self.assertTrue(len(images) >= 1,
                        "At least one valid image should be imported (404s skipped)")

    def test_import_images_403(self):
        """ Import a product when an image respond a 403 error, should fail.
        The importer propagates non-404 HTTP errors via raise_for_status().
        """
        import requests as _req

        product = self.env['product.product'].create({'name': 'Test 403 Product'})
        binding = self.create_binding_no_export(
            'magento.product.product', product, '403-test',
            attribute_set_id=self.attr_set.id)

        url_tee1 = ('http://localhost:9100/media/catalog/product'
                    '/i/n/ink-eater-krylon-bombear-destroyed-tee-1.jpg')
        url_tee2 = ('http://localhost:9100/media/catalog/product/'
                    'i/n/ink-eater-krylon-bombear-destroyed-tee-2.jpg')
        with mock.patch('requests.get') as requests_get:
            def image_url_response(url, headers=None, verify=None):
                if url == url_tee2:
                    return MockResponseImage('', code=404)  # skipped
                elif url == url_tee1:
                    return MockResponseImage('', code=403)  # raises
                return MockResponseImage(PNG_IMG_4PX_GREEN)

            requests_get.side_effect = image_url_response
            with self.assertRaises(_req.exceptions.HTTPError):
                self.image_importer.run(122, binding)
