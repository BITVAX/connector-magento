# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""18.0.1.4.0: the product strategies get clear names and options, and the
product sync direction is gone (Product Import / Product Export "Do not
Import" / "Do not Export" do its job).

Odoo keeps the old translations of a field label, help or option whose English
text changes, so the Spanish and Catalan ones of the changed texts are dropped
here and the module's .po files fill them again at the end of the update.
"""

FIELDS = (
    "product_synchro_strategy",
    "product_import_strategy",
    "product_export_strategy",
    "import_products_from_date",
    "import_configurables_from_date",
)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE ir_model_fields
           SET field_description = field_description - 'es_ES' - 'ca_ES',
               help = help - 'es_ES' - 'ca_ES'
         WHERE model = 'magento.backend'
           AND name IN %s
        """,
        (FIELDS,),
    )
    cr.execute(
        """
        UPDATE ir_model_fields_selection s
           SET name = s.name - 'es_ES' - 'ca_ES'
          FROM ir_model_fields f
         WHERE s.field_id = f.id
           AND f.model = 'magento.backend'
           AND f.name IN %s
        """,
        (FIELDS,),
    )
