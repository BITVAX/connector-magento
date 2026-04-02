# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping


class AccountTaxBatchImporter(Component):
    """Import the Magento Tax Classes."""

    _name = "magento.account.tax.batch.importer"
    _inherit = "magento.delayed.batch.importer"
    _apply_on = ["magento.account.tax"]

    def _import_record(self, external_id, job_options=None):
        """Delay a job for the import"""
        return super()._import_record(external_id, job_options=job_options)

    def run(self, filters=None):
        """Run the synchronization"""
        if self.work.magento_api._location.version == "2.0":
            importer = self.component(usage="record.importer")
            mclasses = self.backend_adapter.search()
            for class_id in mclasses:
                importer.run(class_id)


class AccountTaxImporter(Component):
    _name = "magento.account.tax.importer"
    _inherit = "magento.importer"
    _apply_on = ["magento.account.tax"]

    def _create(self, data):
        binding = super()._create(data)
        self.backend_record.add_checkpoint(binding)
        return binding

    def run(self, external_id, force=False, **kwargs):
        return super().run(external_id, force=force, **kwargs)


class AccountTaxImportMapper(Component):
    _name = "magento.account.tax.import.mapper"
    _inherit = "magento.import.mapper"
    _apply_on = "magento.account.tax"

    direct = [
        ("class_name", "class_name"),
        ("class_type", "class_type"),
        ("class_id", "external_id"),
    ]

    @mapping
    def odoo_id(self, record):
        # Just use the first tax class - user has to rework it in checkpoint !
        tax = self.env["account.tax"].search([], limit=1)
        if tax:
            return {"odoo_id": tax.id}
        # No tax exists yet: provide required defaults for account.tax (v18+)
        tax_group = self.env["account.tax.group"].search([], limit=1)
        if not tax_group:
            tax_group = self.env["account.tax.group"].create(
                {"name": "Magento Taxes"}
            )
        company = self.backend_record.company_id or self.env.company
        country = (
            company.account_fiscal_country_id
            or company.country_id
            or self.env.ref("base.us", raise_if_not_found=False)
        )
        return {
            "name": "{}-{}".format(record["class_name"], record["class_type"]),
            "amount": 0.0,
            "tax_group_id": tax_group.id,
            "country_id": country.id if country else False,
        }

    @mapping
    def backend_id(self, record):
        return {"backend_id": self.backend_record.id}

    # @mapping
    # def defaults(self, record):
    #     return {'name': '{}-{}'.format(record['class_name'],record['class_type']),
    #             'amount': 0.0,
    #             }
