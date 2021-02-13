# -*- coding: utf-8 -*-
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

from odoo.addons.component.core import Component
from odoo.addons.component_event import skip_if
import magic
import base64
import datetime
import logging


_logger = logging.getLogger(__name__)


class MagentoProductImageExportListener(Component):
    _name = 'magento.product.image.export.listener'
    _inherit = 'base.connector.listener'
    _apply_on = ['product.image']

    def _check_create_binding(self, record, do_delay=True):
        def sort_by_position(elem):
            return elem.position

        def create_binding(data, backend):
            mime = magic.Magic(mime=True)
            mimetype = mime.from_buffer(base64.b64decode(record.image))
            data.update({
                'mimetype': mimetype,
                'file': self.env['magento.product.media'].get_unique_filename(image=record, backend_id=backend, mimetype=mimetype),
                'backend_id': backend.id,
                'odoo_id': record.id,
                'position': record.sequence,
                'image_type_image': True if record.is_primary_image else False,
                'image_type_small_image': True if record.is_primary_image else False,
                'image_type_thumbnail': True if record.is_primary_image else False,
            })
            return self.env['magento.product.media'].sudo().with_context(connector_no_export=True).create(data)

        def export_one_variant(product, type):
            magento_product_binding_ids = self.env['magento.product.product'].search([
                ('odoo_id', '=', product.id),
            ])
            for magento_product_binding in magento_product_binding_ids:
                # Create new binding if no matching binding found
                matching_bindings = record.magento_bind_ids.filtered(
                        lambda b:
                        b.backend_id == magento_product_binding.backend_id and
                        b.type == type and
                        b.magento_product_id == magento_product_binding
                )
                assert len(matching_bindings) <= 1, "To many matching bindings for an image with type product_image_ids"
                if not matching_bindings:
                    create_binding({
                        'type': type,
                        'magento_product_id': magento_product_binding.id,
                        'label': magento_product_binding.magento_name or magento_product_binding.name,
                    }, magento_product_binding.backend_id)

        def export_one_template(template, type):
            _logger.info("In export_one_template")
            magento_product_binding_ids = self.env['magento.product.template'].search([
                ('odoo_id', '=', template.id),
            ])
            for magento_product_binding in magento_product_binding_ids:
                # Create new binding if no matching binding found
                matching_bindings = record.magento_bind_ids.filtered(
                        lambda b:
                        b.backend_id == magento_product_binding.backend_id and
                        b.type == type and
                        b.magento_product_tmpl_id == magento_product_binding
                )
                assert len(matching_bindings) <= 1, "To many matching bindings for an image with type product_image_ids"
                if not matching_bindings:
                    create_binding({
                        'type': type,
                        'magento_product_tmpl_id': magento_product_binding.id,
                        'label': magento_product_binding.magento_name or magento_product_binding.name,
                    }, magento_product_binding.backend_id)

        def remove_one_template(template, type):
            _logger.info("In remove_one_template, %s", template)
            magento_product_binding_ids = self.env['magento.product.template'].search([
                ('odoo_id', '=', template.id),
            ])
            _logger.info("In remove_one_template %s", magento_product_binding_ids)
            for magento_product_binding in magento_product_binding_ids:
                # Create new binding if no matching binding found
                matching_bindings = record.magento_bind_ids.filtered(
                        lambda b:
                        b.backend_id == magento_product_binding.backend_id and
                        b.type == type and
                        b.magento_product_tmpl_id == magento_product_binding
                )
                _logger.info("In Matching binding %s", matching_bindings)
                if matching_bindings:
                    matching_bindings.unlink()

        if not record.image:
            return
        # Then to the variants
        if record.image_product_id:
            # There can only be one...
            # delete other bindings which does not match criteria any more
            record.magento_bind_ids.filtered(
                    lambda b:
                    b.type != 'product_image_ids' or (
                        b.magento_product_id.odoo_id != record.image_product_id
                        and
                        b.magento_product_tmpl_id.odoo_id != record.image_product_id.product_tmpl_id
                    )
                    ).unlink()
            # Export image for the template
            if not hasattr(record, 'use_for_template') or (hasattr(record, 'use_for_template') and record.use_for_template):
                export_one_template(template=record.image_product_id.product_tmpl_id, type='product_image_ids')
            else:
                remove_one_template(template=record.image_product_id.product_tmpl_id, type='product_image_ids')
            # Check on which backends target product is available
            export_one_variant(product=record.image_product_id, type='product_image_ids')
        if record.attribute_value_id:
            _logger.info("In product image attached to an attribute")
            # find all product variants where this value is set
            variants = record.base_product_tmpl_id.product_variant_ids.filtered(lambda v: record.attribute_value_id in v.attribute_value_ids)
            # Now find and delete all bindings which do not match any more
            record.magento_bind_ids.filtered(
                    lambda b:
                    b.type != 'attribute_image' or (
                        b.magento_product_id.odoo_id not in variants
                        and
                        b.magento_product_tmpl_id.odoo_id != record.base_product_tmpl_id
                    )
                    ).unlink()
            # Export image for the template
            if not hasattr(record, 'use_for_template') or (hasattr(record, 'use_for_template') and record.use_for_template):
                export_one_template(template=record.base_product_tmpl_id, type='attribute_image')
            else:
                remove_one_template(template=record.base_product_tmpl_id, type='attribute_image')
            for variant in variants:
                # Export for Variant
                export_one_variant(product=variant, type='attribute_image')
        # Here do now queue export of all related bindings
        mtemplates = record.magento_bind_ids.mapped('magento_product_tmpl_id')
        for mtemplate in mtemplates:
            position = 1
            for image in mtemplate.magento_image_bind_ids.sorted(key=sort_by_position):
                if image.image_type_image:
                    image.with_context(connector_no_export=True).update({
                        'position': 0,
                    })
                else:
                    image.with_context(connector_no_export=True).update({
                        'position': position,
                    })
                    position += 1

        _logger.info("Do create media: %s", record.magento_bind_ids)
        for binding in record.magento_bind_ids:
            key = "magento_product_media_%s_%s" % (binding.id, binding.backend_id.id, )
            eta = datetime.datetime.now() + datetime.timedelta(seconds=10)
            if do_delay:
                delayed = binding.with_delay(identity_key=key, eta=eta, priority=0).export_record(binding.backend_id)
                if binding.magento_product_tmpl_id:
                    job = self.env['queue.job'].search([('uuid', '=', delayed.uuid)])
                    binding.magento_product_tmpl_id.odoo_id.with_context(connector_no_export=True).job_ids += job
            else:
                binding.export_record(binding.backend_id)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_create(self, record, fields=None):
        if not record.image:
            return
        self._check_create_binding(record)

    @skip_if(lambda self, record, **kwargs: self.no_connector_export(record))
    def on_record_write(self, record, fields=None):
        if not record.image:
            return
        self._check_create_binding(record)

    def on_record_unlink(self, record):
        for binding in record.magento_bind_ids:
            # Will trigger the unlink operation from the binding
            binding.unlink()