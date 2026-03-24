# Copyright 2024 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

"""
Tests for exporting products to Magento (create and update).
"""

from .common import Magento2SyncTestCase, recorder


class TestExportProduct(Magento2SyncTestCase):

    def setUp(self):
        super().setUp()
        # Import attribute set so we have it available
        self._import_record(
            'magento.product.attribute.set', 4, cassette=True)
        self.attr_set = self.env['magento.product.attribute.set'].search([
            ('backend_id', '=', self.backend.id),
            ('external_id', '=', '4'),
        ], limit=1)

    def _create_product_binding(self, name, default_code, **extra):
        """Create a product with a magento binding ready for export."""
        product = self.env['product.product'].create({
            'name': name,
            'default_code': default_code,
            'type': 'product',
            'list_price': extra.pop('list_price', 49.99),
            'weight': extra.pop('weight', 1.0),
        })
        binding_vals = {
            'odoo_id': product.id,
            'backend_id': self.backend.id,
            'attribute_set_id': self.attr_set.id,
            'magento_status': '1',
            'magento_visibility': '4',
            'product_type': 'simple',
        }
        binding_vals.update(extra)
        binding = self.env['magento.product.product'].create(binding_vals)
        return binding

    def test_export_product_create_job(self):
        """Exporting a new product calls POST to Magento API."""
        binding = self._create_product_binding(
            'Test Export Product', 'TEST-EXPORT-SIMPLE')

        with recorder.use_cassette('test_export_product_create') as cassette:
            with self.mock_with_delay():
                binding.export_record()

        # Verify a POST was made to /products
        post_requests = [
            r for r in cassette.requests
            if r.method == 'POST' and '/products' in r.uri
        ]
        self.assertTrue(len(post_requests) >= 1,
                        "Should have made a POST to /products")

        # Binding should now have an external_id (SKU)
        self.assertEqual(binding.external_id, 'TEST-EXPORT-SIMPLE')

    def test_export_product_update_job(self):
        """Exporting an existing product calls PUT to Magento API."""
        binding = self._create_product_binding(
            'Updated Export Product', 'TEST-EXPORT-UPDATE',
            external_id='TEST-EXPORT-UPDATE')
        binding.magento_internal_id = 5002

        with recorder.use_cassette('test_export_product_update') as cassette:
            binding.export_record()

        # Verify a PUT was made
        put_requests = [
            r for r in cassette.requests
            if r.method == 'PUT' and '/products/' in r.uri
        ]
        self.assertTrue(len(put_requests) >= 1,
                        "Should have made a PUT to /products/{sku}")

    def test_export_product_mapper_name_simple(self):
        """Export mapper generates correct name for simple product."""
        binding = self._create_product_binding(
            'Simple Product Name', 'SIMPLE-SKU')

        with self.backend.work_on('magento.product.product') as work:
            mapper = work.component(usage='export.mapper')
            map_record = mapper.map_record(binding)
            data = map_record.values()

        self.assertEqual(data.get('name'), 'Simple Product Name')
        self.assertEqual(data.get('sku'), binding.external_id or 'SIMPLE-SKU')
        self.assertEqual(data.get('status'), '1')
        self.assertIn('attributeSetId', data)
        self.assertIn('weight', data)

    def test_export_product_mapper_website_ids(self):
        """Export mapper includes website_ids in extension_attributes."""
        binding = self._create_product_binding(
            'Website Test', 'WEBSITE-SKU')

        with self.backend.work_on('magento.product.product') as work:
            mapper = work.component(usage='export.mapper')
            map_record = mapper.map_record(binding)
            data = map_record.values()

        ext_attrs = data.get('extension_attributes', {})
        self.assertIn('website_ids', ext_attrs,
                       "extension_attributes should contain website_ids")


class TestExportProductTemplate(Magento2SyncTestCase):
    """Tests for exporting configurable product templates with variants."""

    def setUp(self):
        super().setUp()
        self._import_record(
            'magento.product.attribute.set', 4, cassette=True)
        self.attr_set = self.env['magento.product.attribute.set'].search([
            ('backend_id', '=', self.backend.id),
            ('external_id', '=', '4'),
        ], limit=1)

    def _create_configurable_template(self):
        """Create a product template with 2 variants and magento binding."""
        # Get or create Color attribute
        color_attr = self.env['product.attribute'].search(
            [('name', '=', 'Color')], limit=1)
        if not color_attr:
            color_attr = self.env['product.attribute'].create({
                'name': 'Color', 'create_variant': 'always'})
        red = self.env['product.attribute.value'].search(
            [('name', '=', 'Red'), ('attribute_id', '=', color_attr.id)], limit=1)
        if not red:
            red = self.env['product.attribute.value'].create({
                'name': 'Red', 'attribute_id': color_attr.id})
        blue = self.env['product.attribute.value'].search(
            [('name', '=', 'Blue'), ('attribute_id', '=', color_attr.id)], limit=1)
        if not blue:
            blue = self.env['product.attribute.value'].create({
                'name': 'Blue', 'attribute_id': color_attr.id})

        # Ensure magento binding for color attribute
        mag_color = color_attr.magento_bind_ids.filtered(
            lambda m: m.backend_id == self.backend)
        if not mag_color:
            mag_color = self.env['magento.product.attribute'].create({
                'odoo_id': color_attr.id,
                'backend_id': self.backend.id,
                'attribute_code': 'color',
                'attribute_id': 93,
                'external_id': '93',
                'frontend_input': 'select',
            })
        # Bindings for values
        for val, code in [(red, '49'), (blue, '50')]:
            if not val.magento_bind_ids.filtered(
                    lambda m: m.backend_id == self.backend):
                self.env['magento.product.attribute.value'].create({
                    'odoo_id': val.id,
                    'backend_id': self.backend.id,
                    'magento_attribute_id': mag_color.id,
                    'external_id': '93_%s' % code,
                    'code': code,
                    'label': val.name,
                })

        # Create template with color attribute line
        template = self.env['product.template'].create({
            'name': 'Configurable Export Test',
            'type': 'product',
            'list_price': 99.0,
            'code_prefix': 'CONF-EXPORT',
            'attribute_line_ids': [(0, 0, {
                'attribute_id': color_attr.id,
                'value_ids': [(6, 0, [red.id, blue.id])],
            })],
        })

        # Set default_code on variants
        for variant in template.product_variant_ids:
            color_val = variant.product_template_attribute_value_ids.filtered(
                lambda v: v.attribute_id == color_attr)
            if color_val:
                variant.default_code = 'CONF-EXPORT-%s' % color_val.name.upper()

        # Create template binding
        binding = self.env['magento.product.template'].with_context(
            connector_no_export=True).create({
            'odoo_id': template.id,
            'backend_id': self.backend.id,
            'attribute_set_id': self.attr_set.id,
            'product_type': 'configurable',
            'code_prefix': 'CONF-EXPORT',
        })
        return binding

    def test_template_has_variants(self):
        """Configurable template should have variant products."""
        binding = self._create_configurable_template()
        template = binding.odoo_id
        self.assertTrue(template.has_variant_attributes)
        self.assertEqual(len(template.product_variant_ids), 2)

    def test_template_sku_proposal(self):
        """Template exporter uses code_prefix as SKU."""
        binding = self._create_configurable_template()
        with self.backend.work_on('magento.product.template') as work:
            exporter = work.component(usage='record.exporter')
            exporter.binding = binding
            exporter.external_id = binding.external_id
            sku = exporter._get_sku_proposal()
        self.assertEqual(sku, 'CONF-EXPORT')

    def test_template_export_mapper(self):
        """Template export mapper generates correct data."""
        binding = self._create_configurable_template()
        with self.backend.work_on('magento.product.template') as work:
            mapper = work.component(usage='export.mapper')
            map_record = mapper.map_record(binding)
            data = map_record.values()

        self.assertEqual(data.get('name'), 'Configurable Export Test')
        self.assertIn('extension_attributes', data)
        self.assertIn('weight', data)

    def test_template_export_variants_created(self):
        """Exporting template creates variant bindings."""
        from unittest import mock
        binding = self._create_configurable_template()
        template = binding.odoo_id

        # Initially no variant bindings
        variant_bindings = self.env['magento.product.product'].search([
            ('backend_id', '=', self.backend.id),
            ('odoo_id', 'in', template.product_variant_ids.ids),
        ])
        self.assertEqual(len(variant_bindings), 0)

        # Call _export_variants with mocked variant exporter (no API calls)
        with self.backend.work_on('magento.product.template') as work:
            exporter = work.component(usage='record.exporter')
            exporter.binding = binding
            exporter.external_id = binding.external_id
            exporter.light_sync = False
            # Mock the variant exporter.run() to avoid real API calls
            variant_exporter = work.component(
                usage='record.exporter',
                model_name='magento.product.product')
            with mock.patch.object(type(variant_exporter), 'run'):
                exporter._export_variants()

        # Now variant bindings should exist
        variant_bindings = self.env['magento.product.product'].search([
            ('backend_id', '=', self.backend.id),
            ('odoo_id', 'in', template.product_variant_ids.ids),
        ])
        self.assertEqual(len(variant_bindings), 2,
                         "Should have created 2 variant bindings")
