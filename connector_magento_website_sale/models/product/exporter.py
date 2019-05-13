# -*- coding: utf-8 -*-
# © 2019 Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).


from odoo.addons.component.core import Component


class ProductProductExporter(Component):
    _inherit = 'magento.product.product.exporter'

    def _export_categories(self):
        """ Export the dependencies for the record"""
        # Check for categories
        for categ in self.binding.public_categ_ids:
            magento_categ_id = categ.magento_bind_ids.filtered(lambda bc: bc.backend_id.id == self.binding.backend_id.id)
            if not magento_categ_id:
                # We need to export the category first
                self._export_dependency(categ, "magento.product.category")
        return

    def _export_images(self):
        """ Export the product.image's associated with this product """
        for image in self.binding.product_image_ids:
            magento_image = image.magento_bind_ids.filtered(lambda bc: bc.backend_id.id == self.binding.backend_id.id)
            if not magento_image:
                # We need to export the category first
                self._export_dependency(image, "magento.product.media", binding_extra_vals={
                    'product_image_id': image.id,
                    'magento_product_id': self.binding.id,
                    'type': 'product_image_ids',
                })
        return

    def _after_export(self):
        """ Export the dependencies for the record"""
        super(ProductProductExporter, self)._after_export()
        self._export_images()
        return


class ProductProductExportMapper(Component):
    _inherit = 'magento.product.export.mapper'

    def category_ids(self, record):
        position = 0
        categ_vals = []
        for categ in record.public_categ_ids:
            magento_categ_id = categ.magento_bind_ids.filtered(lambda bc: bc.backend_id.id == record.backend_id.id)
            categ_vals.append({
              "position": position,
              "category_id": magento_categ_id.external_id,
            })
            position += 1
        return {'category_links': categ_vals}
