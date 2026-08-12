# Copyright 2026 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Remove no_variant PTAVs wrongly written into variant combinations.

    Until 18.0.1.1.0, ProductImporter._after_import_attributes copied every
    attribute line value into product_template_attribute_value_ids, including
    values of no_variant attributes. The core never allows those in a variant
    combination: the stored combination_indices stops matching what
    _get_variant_for_combination computes and the variant becomes unreachable.
    Strip the wrong links and recompute combination_indices exactly like
    product.template.attribute.value._ids2str does (sorted ids, comma-joined,
    empty string for an empty combination).
    """
    if not version:
        return
    cr.execute(
        """
        DELETE FROM product_variant_combination pvc
        USING product_template_attribute_value ptav
        JOIN product_attribute pa ON pa.id = ptav.attribute_id
        WHERE ptav.id = pvc.product_template_attribute_value_id
          AND pa.create_variant = 'no_variant'
        """
    )
    _logger.info(
        "connector_magento: removed %s no_variant links from variant combinations",
        cr.rowcount,
    )
    cr.execute(
        """
        UPDATE product_product pp
        SET combination_indices = COALESCE(
            (SELECT string_agg(
                        pvc.product_template_attribute_value_id::text, ','
                        ORDER BY pvc.product_template_attribute_value_id)
               FROM product_variant_combination pvc
              WHERE pvc.product_product_id = pp.id), '')
        WHERE combination_indices IS DISTINCT FROM COALESCE(
            (SELECT string_agg(
                        pvc.product_template_attribute_value_id::text, ','
                        ORDER BY pvc.product_template_attribute_value_id)
               FROM product_variant_combination pvc
              WHERE pvc.product_product_id = pp.id), '')
        """
    )
    _logger.info(
        "connector_magento: recomputed combination_indices on %s variants",
        cr.rowcount,
    )
