# -*- coding: utf-8 -*-
# Copyright 2019 Callino
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)


from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create


# class ProductPositionExporter(Component):
#     _name = 'magento.product.position.exporter'
#     _inherit = 'magento.exporter'
#     _usage = 'position.exporter'
#     _apply_on = ['magento.product.category']
#
#     def _should_import(self):
#         return False
#
#     def _has_to_skip(self):
#         return False
#
#     def run(self, binding, mcategory):
#         """ Run the synchronization
#
#         :param binding: binding record to export
#         """
#         if binding._name == 'magento.product.product':
#             mpos = self.env['magento.product.position'].search([
#                 ('product_template_id', '=', binding.odoo_id.product_tmpl_id.id),
#                 ('magento_product_category_id', '=', mcategory.id)
#             ])
#         elif binding._name == 'magento.product.template':
#             mpos = self.env['magento.product.position'].search([
#                 ('product_template_id', '=', binding.odoo_id.id),
#                 ('magento_product_category_id', '=', mcategory.id)
#             ])
#         position = mpos.position if mpos else 9999
#         self.backend_adapter.update_category_position(mcategory.external_id, binding.external_id, position)
#

class ProductCategoryExporter(Component):
    _name = 'magento.product.category.exporter'
    _inherit = 'magento.exporter'
    _apply_on = ['magento.product.category']

    '''
    Category move does not work on magento side...
    def run(self, binding, *args, **kwargs):
        if binding.parent_id and binding.magento_parent_id and binding.magento_parent_id.odoo_id.id != binding.parent_id.id:
            # This is a category move - we have to handle it seperate - after it do the normal export
            self._run_category_move(binding)

        return super(ProductCategoryExporter, self).run(binding, *args, **kwargs)
    '''

    def _run_category_move(self, binding):
        # Get the current magento category id - and the new magento category id
        parent_binding = binding.parent_id.magento_bind_ids.filtered(lambda b: b.backend_id == binding.backend_id)
        if not parent_binding:
            self._export_dependency(binding.parent_id, "magento.product.category")
            parent_binding = self.env['magento.product.category'].search([
                ('odoo_id', '=', binding.parent_id.id),
                ('backend_id', '=', binding.backend_id.id)
            ])
        mag_cat_id = self.binder.to_external(binding)
        source_mag_cat_id = self.binder.to_external(binding.magento_parent_id)
        target_mag_cat_id = self.binder.to_external(parent_binding)
        res = self.backend_adapter.move_category(mag_cat_id, source_mag_cat_id, target_mag_cat_id)
        if not res:
            raise UserWarning('Failed to move category')
        binding.magento_parent_id = parent_binding

    def _export_dependencies(self):
        """ Export the dependencies for the record"""
        # Check parent category
        if self.binding.parent_id and not self.binder_for('magento.product.category').to_external(self.binding.parent_id, wrap=True):
            self._export_dependency(self.binding.parent_id, "magento.product.category", force_update=True)

    def _has_to_skip(self):
        """ Allow export of any category - removed artificial root category restriction """
        return False

    def _should_import(self):
        return False

    # def _create(self, data):
    #     """ Create the Magento record """
    #     # special check on data before export
    #     self._validate_create_data(data)
    #     return self.backend_adapter.create(data, binding=self.binding)
    #
    # def _update(self, data):
    #     """ Update an External record """
    #     assert self.external_id
    #     # special check on data before export
    #     self._validate_update_data(data)
    #     self.backend_adapter.write(self.external_id, data)


class ProductCategoryExportMapper(Component):
    _name = 'magento.product.category.export.mapper'
    _inherit = 'magento.export.mapper'
    _apply_on = ['magento.product.category']

    @mapping
    def name(self, record):
        return {
            'name': record.name
        }

    @mapping
    def parent_id(self, record):
        if record.parent_id:
            return {
                'parent_id': self.binder_for('magento.product.category').to_external(record.parent_id, wrap=True)
            }

    @mapping
    def is_active(self, record):
        return {
            'is_active': True
        }
