# Copyright 2013-2017 Camptocamp SA
# © 2016 Sodexis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)

import logging

from odoo.addons.component.core import Component
from odoo.addons.connector.components.mapper import mapping, only_create
from odoo.addons.connector.exception import MappingError

_logger = logging.getLogger(__name__)


class ProductTemplateBatchImporter(Component):
    """Import the Magento configureable Products.

    For every product template in the list, a delayed job is created.
    Import from a date
    """

    _name = "magento.product.template.batch.importer"
    _inherit = "magento.delayed.batch.importer"
    _apply_on = ["magento.product.template"]

    def run(self, filters=None):
        """Run the synchronization"""
        from_date = filters.pop("from_date", None)
        to_date = filters.pop("to_date", None)
        # Variants to have visibility=1
        filters["visibility"] = {"eq": 4}
        filters["type_id"] = {"eq": "configurable"}
        filters["status"] = {"eq": 1}
        external_ids = self.backend_adapter.search(
            filters, from_date=from_date, to_date=to_date
        )
        _logger.info(
            "search for magento product templates %s returned %s", filters, external_ids
        )
        for external_id in external_ids:
            self._import_record(external_id)


class ProductTemplateImporter(Component):
    _name = "magento.product.template.importer"
    _inherit = "magento.importer"
    _apply_on = ["magento.product.template"]
    _magento_id_field = "sku"

    # def _create(self, data):
    #     # create_product_product - Avoid creating variant products
    #     binding = super(ProductTemplateImporter, self)._create(data)
    #     self.backend_record.add_checkpoint(binding)
    #     return binding

    # def _import_dependency(self, external_id, binding_model,
    #                        importer=None, always=False, binding_template_id=None, external_field=None):
    #     """ Import a dependency.
    #
    #     The importer class is a class or subclass of
    #     :class:`MagentoImporter`. A specific class can be defined.
    #
    #     :param external_id: id of the related binding to import
    #     :param binding_model: name of the binding model for the relation
    #     :type binding_model: str | unicode
    #     :param importer_component: component to use for import
    #                                By default: 'importer'
    #     :type importer_component: Component
    #     :param always: if True, the record is updated even if it already
    #                    exists, note that it is still skipped if it has
    #                    not been modified on Magento since the last
    #                    update. When False, it will import it only when
    #                    it does not yet exist.
    #     :type always: boolean
    #     """
    #     if not external_id:
    #         return
    #     binder = self.binder_for(binding_model)
    #     if always or not binder.to_internal(external_id, external_field=external_field):
    #         if importer is None:
    #             importer = self.component(usage='record.importer',
    #                                       model_name=binding_model)
    #         try:
    #             if binding_model == "magento.product.product":
    #                 importer.run(external_id, binding_template_id=binding_template_id)
    #             else:
    #                 importer.run(external_id)
    #         except NothingToDoJob:
    #             _logger.info(
    #                 'Dependency import of %s(%s) has been ignored.',
    #                 binding_model._name, external_id
    #             )

    def _preprocess_magento_record(self):
        for attr in self.magento_record.get("custom_attributes", []):
            self.magento_record[attr["attribute_code"]] = attr["value"]
        return

    def _update_price(self, binding, price):
        # Update price if price is 0
        if binding.price == 0:
            binding.with_context(connector_no_export=True).price = price

    def _import_category_positions(self, binding):
        record = self.magento_record
        binder = self.binder_for("magento.product.category")
        if "category_links" not in record["extension_attributes"]:
            return
        category_links = record["extension_attributes"]["category_links"]
        _logger.info("Links: %s", category_links)
        for category_link in category_links:
            cat = binder.to_internal(category_link["category_id"], unwrap=False)
            if not cat:
                raise MappingError(
                    "The product category with "
                    "magento id %s is not imported." % category_link["category_id"]
                )
            # Search for position
            position = self.env["magento.product.position"].search(
                [
                    (
                        "magento_product_category_id.backend_id",
                        "=",
                        self.backend_record.id,
                    ),
                    ("product_template_id", "=", binding.odoo_id.id),
                    ("magento_product_category_id", "=", cat.id),
                ]
            )
            if not position:
                _logger.info("Do create new position entrie")
                self.env["magento.product.position"].create(
                    {
                        "product_template_id": binding.odoo_id.id,
                        "magento_product_category_id": cat.id,
                        "position": category_link["position"],
                    }
                )
            else:
                position.update(
                    {
                        "position": category_link["position"],
                    }
                )

    def _coalesce_template_images(self, template):
        """
        Coalesce duplicate images after importing template and variants.
        Uses ir_attachment checksum to identify duplicates.

        Two groups:
        - Without variants (configurable): duplicates are simply deleted
        - With variants: duplicates are merged, combining variant associations
        """
        template.invalidate_recordset(["image_ids"])
        all_images = template.image_ids
        if not all_images:
            return

        # Get checksums from ir_attachment
        attachments = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "base_multi_image.image"),
                ("res_id", "in", all_images.ids),
                ("res_field", "=", "image_1920"),
            ]
        )
        image_checksums = {att.res_id: att.checksum for att in attachments}

        # Separate images by type
        configurable_images = all_images.filtered(
            lambda img: not img.product_variant_ids
        )
        variant_images = all_images.filtered(lambda img: img.product_variant_ids)

        images_to_delete = self.env["base_multi_image.image"]

        # Group 1: Configurable images (no variants) - just delete duplicates
        checksum_to_config_images = {}
        for image in configurable_images:
            checksum = image_checksums.get(image.id)
            if checksum:
                checksum_to_config_images.setdefault(checksum, []).append(image)

        for checksum, images in checksum_to_config_images.items():
            if len(images) > 1:
                # Keep first, delete rest
                for img in images[1:]:
                    images_to_delete |= img

        # Group 2: Variant images - merge duplicates, combine variants
        checksum_to_variant_images = {}
        for image in variant_images:
            checksum = image_checksums.get(image.id)
            if checksum:
                checksum_to_variant_images.setdefault(checksum, []).append(image)

        for checksum, images in checksum_to_variant_images.items():
            if len(images) > 1:
                keeper = images[0]
                # Collect all variant IDs from duplicates
                all_variant_ids = []
                for img in images:
                    all_variant_ids.extend(img.product_variant_ids.ids)
                # Update keeper with all variants
                keeper.product_variant_ids = [(6, 0, list(set(all_variant_ids)))]
                # Mark others for deletion
                for img in images[1:]:
                    images_to_delete |= img

        _logger.info(
            "COALESCE: Template %s - deleting %s duplicate images",
            template.id,
            len(images_to_delete),
        )
        if images_to_delete:
            images_to_delete.unlink()

    def _after_import(self, binding, **kwargs):
        super()._after_import(binding, **kwargs)

        def sort_by_position(elem):
            return elem.position

        image_importer = self.component(usage="product.image.importer")
        image_importer.run(self.external_id, binding, data=self.magento_record)

        # Import Category positions
        # self._import_category_positions(binding)
        # Do import stock item
        # self._import_stock(binding)
        # if self.backend_record.product_synchro_strategy == 'odoo_first':
        #     return
        # Import Images
        # media_importer = self.component(usage='product.media.importer', model_name='magento.product.media')
        # for media in self.magento_record['media_gallery_entries']:
        #     media_importer.run(media, product_binding=binding)
        # # Here do choose the image at the smallest position as the main image
        # for media_binding in sorted(binding.magento_image_bind_ids.filtered(lambda m: m.media_type == 'image'), key=sort_by_position):
        #     binding.with_context(connector_no_export=True).image = media_binding.image
        #     break
        # # Import variants
        magento_variants = self.backend_adapter.list_variants(self.external_id)
        variant_binder = self.binder_for("magento.product.product")
        templates_delete = {}
        price = 0
        for magento_variant in magento_variants:
            if not price or magento_variant["price"] < price:
                price = magento_variant["price"]
            # Search by sku - because this is also what is available in the mapper !
            variant = variant_binder.to_internal(magento_variant["sku"], unwrap=False)
            # Import / Update the variant here
            if not variant:
                # Pass product_template_id in arguments - so the product mapper will map it
                self._import_dependency(
                    magento_variant["sku"],
                    "magento.product.product",
                    always=True,
                    binding_template_id=binding,
                )
            elif variant.odoo_id.product_tmpl_id.id != binding.odoo_id.id:
                # Variant does exists already - and is at wrong odoo template - so reassign it - and delete old template
                old_template = variant.odoo_id.product_tmpl_id
                variant.odoo_id.product_tmpl_id = binding.odoo_id.id
                templates_delete[old_template.id] = old_template
            if variant:
                # Update the variant
                updater = self.component(
                    usage="record.importer", model_name="magento.product.product"
                )
                updater.run(
                    variant.external_id, force=True, binding_template_id=binding
                )

        for template_delete in templates_delete:
            templates_delete[template_delete].unlink()

        # Coalesce duplicate images after importing all variants
        self._coalesce_template_images(binding.odoo_id)

        # self._update_price(binding, price)
        # Do also import translations
        translation_importer = self.component(
            usage="translation.importer",
        )
        translation_importer.run(
            self.external_id, binding, mapper="magento.product.template.import.mapper"
        )
        return

    def _import_stock(self, binding):
        stock_importer = self.component(
            usage="record.importer", model_name="magento.stock.item"
        )
        stock_importer.run(self.magento_record["extension_attributes"]["stock_item"])

    def _is_uptodate(self, binding):
        # TODO: Remove for production - only to test the update
        return False

    def _get_binding(self):
        binding = super()._get_binding()
        if not binding:
            # Do search using the magento_id - maybe the sku did changed !
            binding = self.env["magento.product.template"].search(
                [
                    ("backend_id", "=", self.backend_record.id),
                    ("magento_id", "=", self.magento_record["id"]),
                ]
            )
            # if we found binding here - then the update will also update the external_id on the binding record
        return binding

    def _import_stock_warehouse(self):
        record = self.magento_record
        stock_item = record["extension_attributes"]["stock_item"]
        binder = self.binder_for("magento.stock.warehouse")
        mwarehouse = binder.to_internal(stock_item["stock_id"])
        if not mwarehouse:
            # We do create the warehouse binding directly here - did not found a mapping on magento api
            # We do create the warehouse binding directly here - did not found a mapping on magento api
            binding = self.env["magento.stock.warehouse"].create(
                {
                    "backend_id": self.backend_record.id,
                    "external_id": stock_item["stock_id"],
                    "odoo_id": self.env["stock.warehouse"]
                    .search(
                        [("company_id", "=", self.backend_record.company_id.id)],
                        limit=1,
                    )
                    .id,
                }
            )
            self.backend_record.add_checkpoint(binding)

    def _import_category_dependencies(self):
        record = self.magento_record
        if "category_links" not in record["extension_attributes"]:
            return
        category_links = record["extension_attributes"]["category_links"]
        _logger.info("cat links: %s", category_links)
        binder = self.binder_for("magento.product.category")
        for category_link in category_links:
            cat = binder.to_internal(category_link["category_id"], unwrap=False)
            if not cat:
                _logger.info("import cat link: %s", category_link)
                self._import_dependency(
                    category_link["category_id"], "magento.product.category"
                )

    def _import_dependencies(self, **kwargs):
        record = self.magento_record
        # Import attribute deps
        for attribute in record.get("custom_attributes"):
            # We do search binding using attribute_code - default is attribute_id !
            self._import_dependency(
                attribute["attribute_code"],
                "magento.product.attribute",
                external_field="attribute_code",
            )
        self._import_category_dependencies()
        # Check for attributes in configurable - with values
        product_options = record["extension_attributes"]["configurable_product_options"]
        attribute_binder = self.binder_for("magento.product.attribute")
        attribute_value_binder = self.binder_for("magento.product.attribute.value")
        for product_option in product_options:
            attribute = attribute_binder.to_internal(
                product_option["attribute_id"], unwrap=True
            )
            if not attribute:
                # Do import the attribute
                self._import_dependency(
                    product_option["attribute_id"], "magento.product.attribute"
                )
                attribute = attribute_binder.to_internal(
                    product_option["attribute_id"], unwrap=True
                )
            # This is a configurable product option attribute - so set the create_variant flag
            if not attribute.create_variant:
                attribute.with_context(
                    connector_no_export=True
                ).create_variant = "always"
            # Check for attribute values
            for option_value in product_option["values"]:
                attribute_value = attribute_value_binder.to_internal(
                    "%s_%s"
                    % (product_option["attribute_id"], option_value["value_index"]),
                    unwrap=True,
                )
                if not attribute_value:
                    # Do update the attribute - so the value will get added
                    self._import_dependency(
                        product_option["attribute_id"],
                        "magento.product.attribute",
                        always=True,
                    )
        # self._import_stock_warehouse()


class ProductTemplateImportMapper(Component):
    _name = "magento.product.template.import.mapper"
    _inherit = "magento.product.product.import.mapper"
    _apply_on = ["magento.product.template"]

    children = []

    @mapping
    def price(self, record):
        """Override for templates: use list_price directly.

        Templates don't have fix_price, so we write directly to list_price.
        Variants use lst_price which triggers the fix_price inverse.
        standard_price (cost) is stored per variant, not on template.
        """
        return {
            "list_price": float(record.get("price") or 0.0),
        }

    # @mapping
    # def custom_values(self, record):
    #     """
    #     Force the custom attributes to be in the dictionnary
    #     so that creation of the template will get the custom values
    #     """
    #     custom_values = record['custom_attributes']
    #     return {'custom_attributes': custom_values}

    @mapping
    def attributes(self, record):
        """
        We do overwrite the attributes function from product.product
        :param record:
        :return:
        """
        attribute_binder = self.binder_for("magento.product.attribute")
        line_binder = self.binder_for("magento.product.template.attribute.line")
        product_options = record["extension_attributes"]["configurable_product_options"]
        linemapper = self.component(
            usage="import.mapper", model_name="magento.product.template.attribute.line"
        )
        binding = self.options.get("binding")
        odoo_options = []

        for product_option in product_options:
            # Check if it does already exists
            # Get internal attribute
            attribute = attribute_binder.to_internal(
                product_option["attribute_id"], unwrap=True
            )
            if not attribute:
                raise MappingError(
                    "The product attribute with "
                    "magento id %s is not imported." % product_option["attribute_id"]
                )

            # First, try to find existing magento binding
            line = line_binder.to_internal(product_option["id"], unwrap=False)
            map_record = linemapper.map_record(product_option, parent=record)

            if not line and binding:
                # If no magento binding exists but we're updating, check if there's
                # an odoo line for this attribute without binding (auto-created by Odoo)
                existing_odoo_line = binding.odoo_id.attribute_line_ids.filtered(
                    lambda l: (
                        l.attribute_id.id == attribute.id and not l.magento_bind_ids
                    )
                )
                if existing_odoo_line:
                    # Found an existing odoo line without magento binding
                    # Create a magento binding for it by adding odoo_id to the values
                    vals = map_record.values(for_create=True)
                    vals["odoo_id"] = existing_odoo_line[0].id
                    odoo_options.append((0, 0, vals))
                    _logger.info(
                        "Linking existing odoo attribute line %s to new magento binding for %s",
                        existing_odoo_line[0].id,
                        product_option["id"],
                    )
                else:
                    # No existing line found, create new one
                    odoo_options.append((0, 0, map_record.values(for_create=True)))
            elif not line:
                # Create line (first import)
                odoo_options.append((0, 0, map_record.values(for_create=True)))
            else:
                # Update existing magento binding line
                odoo_options.append((1, line.id, map_record.values(for_create=False)))
        return {"magento_template_attribute_line_ids": odoo_options}

    #
    # @mapping
    # def categories(self, record):
    #     # No categories on configurable product
    #     if not 'category_links' in record['extension_attributes']:
    #         return
    #     category_links = record['extension_attributes']['category_links']
    #     _logger.info("Links: %s", category_links)
    #     binder = self.binder_for('magento.product.category')
    #     category_ids = []
    #     main_categ_id = None
    #     # TODO: Read also position !
    #     for category_link in category_links:
    #         cat = binder.to_internal(category_link['category_id'], unwrap=True)
    #         if not cat:
    #             raise MappingError("The product category with "
    #                                "magento id %s is not imported." %
    #                                category_link['category_id'])
    #
    #         category_ids.append(cat.id)
    #
    #     if category_ids:
    #         main_categ_id = category_ids.pop(0)
    #
    #     if main_categ_id is None:
    #         default_categ = self.backend_record.default_category_id
    #         if default_categ:
    #             main_categ_id = default_categ.id
    #
    #     result = {'categ_ids': [(6, 0, category_ids)]}
    #     if main_categ_id:  # OpenERP assign 'All Products' if not specified
    #         result['categ_id'] = main_categ_id
    #     return result

    @only_create
    @mapping
    def auto_create_variants(self, records):
        # By default we disable auto create variants when product is coming from a webshop
        # TODO: Make this configurable using the backend record !
        return {"auto_create_variants": False}

    @mapping
    def price(self, record):
        return {
            "list_price": record.get("price", 0.0),
        }

    @mapping
    def cost(self, record):
        return {
            "standard_price": record.get("cost", 0.0),
        }

    @mapping
    def product_name(self, record):
        return {
            "name": record.get("name", ""),
        }

    @only_create
    @mapping
    def odoo_id(self, record):
        """Will bind the product to an existing one with the same code"""
        template = self.env["product.template"].search(
            [("default_code", "=", record["sku"])], limit=1
        )
        if template:
            return {"odoo_id": template.id}
        return {}

    @mapping
    def type(self, record):
        return {"type": "consu", "is_storable": True}

    @mapping
    def attributes_no_variant(self, record):
        attribute_binder = self.binder_for("magento.product.attribute")
        value_binder = self.binder_for("magento.product.attribute.value")
        data = {"attribute_line_ids": []}
        value_ids = []
        changes = {}
        binding = self.options.get("binding")
        for attribute in record["custom_attributes"]:
            mattribute = attribute_binder.to_internal(
                attribute["attribute_code"],
                unwrap=False,
                external_field="attribute_code",
            )
            if mattribute:
                if mattribute.exclude:
                    continue
                if mattribute.field_id:
                    data.update({mattribute.field_id.name: attribute["value"]})
                    continue
                if (
                    mattribute.create_variant != "no_variant"
                    or not mattribute.is_user_defined
                    or mattribute.frontend_input != "select"
                ):
                    continue
                mvalue = value_binder.to_internal(
                    "%s_%s" % (mattribute.attribute_id, str(attribute["value"])),
                    unwrap=False,
                )
                if not mvalue:
                    raise MappingError(
                        "The product attribute value %s in attribute %s is not imported."
                        % (
                            "%s_%s"
                            % (mattribute.attribute_id, str(attribute["value"])),
                            mattribute.name,
                        )
                    )
                # Also create an attribute.line.value entrie here
                data["attribute_line_ids"].append(
                    (
                        0,
                        0,
                        {
                            "attribute_id": mattribute.odoo_id.id,
                            "value_ids": [(6, 0, [mvalue.odoo_id.id])],
                        },
                    )
                )
                value_ids.append(mvalue.odoo_id.id)
        if binding:
            # data['attribute_line_ids'] = [(5,0,0)] + data['attribute_line_ids']
            lines = data["attribute_line_ids"]
            data["attribute_line_ids"] = []
            if set(value_ids) != set(
                binding.attribute_line_ids.mapped("value_ids").ids
            ):
                for line in lines:
                    odoo_value_ids = (
                        binding.attribute_line_ids.filtered(
                            lambda l: l.attribute_id.id == line[2]["attribute_id"]
                        )
                        .mapped("value_ids")
                        .ids
                    )
                    if set(odoo_value_ids) != set(line[2]["value_ids"][0][2]):
                        changes[line[2]["attribute_id"]] = {
                            "old": odoo_value_ids,
                            "new": line[2]["value_ids"][0][2],
                            "line_id": odoo_value_ids
                            and binding.attribute_line_ids.filtered(
                                lambda l: l.attribute_id.id == line[2]["attribute_id"]
                            ).id
                            or False,
                        }
                if len(changes):
                    for key, value in changes.items():
                        if value["line_id"]:
                            data["attribute_line_ids"].append(
                                (
                                    1,
                                    value["line_id"],
                                    {"value_ids": [(6, 0, value["new"])]},
                                )
                            )
                        else:
                            data["attribute_line_ids"].append(
                                (
                                    0,
                                    0,
                                    {
                                        "attribute_id": key,
                                        "value_ids": [(6, 0, value["new"])],
                                    },
                                )
                            )

        if self.options.get("binding_template_id") and len(value_ids):
            if data.get("attribute_line_ids"):
                del data["attribute_line_ids"]
            binding_template_id = self.options["binding_template_id"]
            template_id = binding_template_id.odoo_id
            ptav_ids = template_id.mapped(
                "attribute_line_ids.product_template_value_ids"
            ).filtered(lambda x: x.product_attribute_value_id.id in value_ids)
            data["product_template_attribute_value_ids"] = [(6, 0, ptav_ids.ids)]

        return data


class ProductTemplateUpdateWriteMapper(Component):
    _name = "magento.product.template.update.write.mapper"
    _inherit = "magento.product.product.update.write.mapper"
    _usage = "record.update.write"
    _apply_on = ["magento.product.template"]

    @mapping
    def no_stock_sync(self, record):
        return {}

    # @mapping
    # def category_positions(self, record):
    #     # Only for configure products
    #     if not record['type_id'] == 'configurable':
    #         return {}
    #     if not 'extension_attributes' in record or not'category_links' in record['extension_attributes']:
    #         return {}
    #     data = []
    #     for position in record['extension_attributes']['category_links']:
    #         binder = self.binder_for('magento.product.category')
    #         magento_category = binder.to_internal(position['category_id'])
    #         if not magento_category:
    #             raise ValueError('Magento category with id %s is missing on odoo side.' % position['category_id'])
    #         magento_position = self.env['magento.product.position'].search([
    #             ('magento_product_category_id', '=', magento_category.id),
    #             ('product_template_id', '=', self.options.binding.odoo_id.id),
    #         ])
    #         if magento_position:
    #             data.append((1, magento_position.id, {
    #                 'position': position['position'],
    #             }))
    #         else:
    #             data.append((0, 0, {
    #                 'product_template_id': self.options.binding.odoo_id.id,
    #                 'magento_product_category_id': magento_category.id,
    #                 'position': position['position'],
    #             }))
    #     return {'magento_product_position_ids': data}


class ProductTemplateUpdateCreateMapper(Component):
    _name = "magento.product.template.update.create.mapper"
    _inherit = "magento.product.product.update.create.mapper"
    _usage = "record.update.create"
    _apply_on = ["magento.product.template"]

    @mapping
    def no_stock_sync(self, record):
        return {}

    @mapping
    def magento_id(self, record):
        return {"magento_id": record.get("id", "")}
