# Copyright 2013-2017 Camptocamp SA
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import logging

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping

_logger = logging.getLogger(__name__)


class AttributeSetBatchImporter(Component):
    """Import the Magento Attributes Set.

    For every attributes set in the list, a delayed job is created.
    Import from a date
    """

    _name = "magento.product.attribute.set.batch.importer"
    _inherit = "magento.delayed.batch.importer"
    _apply_on = ["magento.product.attribute.set"]

    def run(self, filters=None):
        """Run the synchronization"""
        _logger.debug("batch filter = %r" % filters)
        external_ids = self.backend_adapter.search(filters)
        _logger.info("search for attributes set %s returned %s", filters, external_ids)
        for external_id in external_ids:
            self._import_record(external_id)


class AttributeSetImportMapper(Component):
    _name = "magento.product.attribute.set.import.mapper"
    _inherit = "magento.import.mapper"
    _apply_on = "magento.product.attribute.set"

    direct = [
        ("attribute_set_name", "name"),
        ("attribute_set_id", "external_id"),
    ]

    @mapping
    def backend_id(self, record):
        return {"backend_id": self.backend_record.id}


class AttributeSet(Component):
    _name = "magento.product.attribute.set.importer"
    _inherit = "magento.importer"
    _apply_on = ["magento.product.attribute.set"]

    def _after_import(self, binding, **kwargs):
        """Hook called at the end of the import"""
        super()._after_import(binding, **kwargs)
        adapter = self.component(
            usage="backend.adapter", model_name="magento.product.attribute.set"
        )
        importer = self.component(
            usage="record.importer", model_name="magento.product.attribute"
        )

        details = adapter.read_detail(self.external_id)
        attributes = []
        attributebinder = self.binder_for("magento.product.attribute")

        for record in details:
            # if not record.get('is_user_defined',False):
            #     continue
            importer.run(record.get("attribute_id"))
            attributes.append(
                attributebinder.to_internal(record.get("attribute_id")).id
            )
        binding.write({"attribute_ids": [(6, 0, attributes)]})
        # Batch Import attribute groups
        # importer = self.component(usage='batch.importer', model_name='magento.product.attributes.group')
        # importer.run()

    # def run(self, external_id, **kwargs):
    #     _logger.info("In attribute set run function")
    #     return super(AttributeSet, self).run(external_id, *kwargs)
