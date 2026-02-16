# © 2013-2019 Guewen Baconnier,Camptocamp SA,Akretion
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, models, fields, _
from odoo.addons.queue_job.job import identity_exact


class MagentoBinding(models.AbstractModel):
    """ Abstract Model for the Bindings.

    All the models used as bindings between Magento and Odoo
    (``magento.res.partner``, ``magento.product.product``, ...) should
    ``_inherit`` it.
    """
    _name = 'magento.binding'
    _inherit = 'external.binding'
    _description = 'Magento Binding (abstract)'

    # odoo_id = odoo-side id must be declared in concrete model
    backend_id = fields.Many2one(
        comodel_name='magento.backend',
        string='Magento Backend',
        required=True,
        ondelete='restrict',
    )
    # fields.Char because 0 is a valid Magento ID
    external_id = fields.Char(string='ID on Magento', oldname='magento_id')

    data = fields.Json(
        string='Raw Json Data',
        help='Serialized data from Magento.',
    )
    data_str = fields.Text(
        string='Raw Json Data',
        help='Serialized data from Magento.',
        compute='_compute_data_str',
    )
    _sql_constraints = [
        ('magento_uniq', 'unique(backend_id, external_id)',
         'A binding already exists with the same Magento ID.'),
    ]

    @api.depends('data')
    def _compute_data_str(self):
        for record in self:
            record.data_str = record.data and str(record.data) or ''

    # @job(default_channel='root.magento')
    @api.model
    def import_batch(self, backend, filters=None, **kwargs ):
        """ Prepare the import of records modified on Magento """
        if filters is None:
            filters = {}
        with backend.work_on(self._name) as work:
            importer = work.component(usage='batch.importer')
            return importer.run(filters=filters, **kwargs)

    # @job(default_channel='root.magento')
    # @related_action(action='related_action_magento_link')
    @api.model
    def import_record(self, backend, external_id, force=False, **kwargs):
        """ Import a Magento record """
        with backend.with_env(self.env).work_on(self._name) as work:
            importer = work.component(usage='record.importer')
            return importer.run(external_id, force=force, **kwargs)

    # @job(default_channel='root.magento')
    # @related_action(action='related_action_unwrap_binding')
    # @api.multi
    def export_record(self, fields=None, **kwargs):
        """ Export a record on Magento """
        self.ensure_one()
        with self.backend_id.work_on(self._name) as work:
            exporter = work.component(usage='record.exporter')
            return exporter.run(self, fields, **kwargs)

    # @job(default_channel='root.magento')
    # @related_action(action='related_action_magento_link')
    def export_delete_record(self, backend, external_id, **kwargs):
        """ Delete a record on Magento """
        with backend.work_on(self._name) as work:
            deleter = work.component(usage='record.exporter.deleter')
            return deleter.run(external_id, **kwargs)

    def sync_from_magento(self):
        for binding in self:
            binding.with_delay(
                identity_key=identity_exact,
                description=_("Import %s (ID: %s) from Magento") % (binding.display_name or '', binding.external_id or ''),
            ).import_record(
                binding.backend_id, binding.external_id, force=True)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import from Magento'),
                'message': _('%d record(s) queued for import from Magento') % len(self),
                'type': 'info',
                'sticky': False,
            }
        }

    def sync_to_magento(self):
        for binding in self:
            binding.with_delay(
                identity_key=identity_exact, priority=10,
                description=_("Export %s to Magento") % (binding.display_name or ''),
            ).export_record()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Export to Magento'),
                'message': _('%d record(s) queued for export to Magento') % len(self),
                'type': 'info',
                'sticky': False,
            }
        }

    def delete_from_magento(self):
        for binding in self:
            binding.with_delay(
                identity_key=identity_exact,
                description=_("Delete %s (ID: %s) from Magento") % (binding.display_name or '', binding.external_id or ''),
            ).export_delete_record(
                binding.backend_id, binding.external_id)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Delete from Magento'),
                'message': _('%d record(s) queued for deletion from Magento') % len(self),
                'type': 'warning',
                'sticky': False,
            }
        }



