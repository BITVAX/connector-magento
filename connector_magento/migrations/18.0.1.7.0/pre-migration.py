# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""18.0.1.7.0: the help of the Magento short description no longer says that
the description of the page is the description of the product (it has a field
of its own now, magento_description).

Odoo keeps the old translations of a field help whose English text changes, so
the Spanish and Catalan ones are dropped here and the module's .po files fill
them again at the end of the update.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE ir_model_fields
           SET help = help - 'es_ES' - 'ca_ES'
         WHERE model = 'product.template'
           AND name = 'magento_short_description'
        """
    )
