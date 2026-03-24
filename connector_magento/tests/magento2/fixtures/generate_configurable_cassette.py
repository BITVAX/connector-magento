#!/usr/bin/env python3
"""Generate VCR cassette for configurable product template import test.

Usage: python3 generate_configurable_cassette.py
Output: cassettes/import_product_template_CONF-TEST.yaml
"""
import json
import os

BASE = 'http://magento/index.php'
DUPLICATE_COUNT = 2

# ── Helpers ──────────────────────────────────────────────────────

def interaction(uri, body_json, method='GET'):
    """Generate a single VCR interaction in YAML text format."""
    body_escaped = body_json.replace("'", "''")
    return f"""- request:
    body: null
    headers:
      Accept: ['application/json']
      Authorization: ['Bearer test_token']
      Content-Type: ['application/json']
      User-Agent: ['python-requests/2.25.1']
    method: {method}
    uri: {uri}
  response:
    body: {{string: '{body_escaped}'}}
    headers:
      Content-Type: ['application/json; charset=utf-8']
    status: {{code: 200, message: OK}}"""


# ── Product Data ─────────────────────────────────────────────────

CUSTOM_ATTRS_TEMPLATE = [
    {"value": "2", "attribute_code": "tax_class_id"},
    {"value": "test-configurable-product", "attribute_code": "url_key"},
    {"value": "1", "attribute_code": "has_options"},
    {"value": "0", "attribute_code": "required_options"},
    {"value": "Test configurable product description", "attribute_code": "description"},
    {"value": "Test configurable short desc", "attribute_code": "short_description"},
    {"value": "/c/o/conf-test.jpg", "attribute_code": "image"},
    {"value": "/c/o/conf-test.jpg", "attribute_code": "small_image"},
    {"value": "/c/o/conf-test.jpg", "attribute_code": "thumbnail"},
    {"value": "container2", "attribute_code": "options_container"},
    {"value": "0", "attribute_code": "msrp_display_actual_price_type"},
    {"value": ["2"], "attribute_code": "category_ids"},
]

CONFIGURABLE_PRODUCT = {
    "id": 9000,
    "sku": "CONF-TEST",
    "name": "Test Configurable Product",
    "attribute_set_id": 4,
    "price": 0,
    "status": 1,
    "visibility": 4,
    "type_id": "configurable",
    "created_at": "2024-01-01 00:00:00",
    "updated_at": "2024-01-01 00:00:00",
    "weight": 0,
    "extension_attributes": {
        "website_ids": [1],
        "category_links": [{"position": 0, "category_id": "2"}],
        "stock_item": {
            "item_id": 9000, "product_id": 9000, "stock_id": 1,
            "qty": 0, "is_in_stock": True, "is_qty_decimal": False,
            "use_config_min_qty": True, "min_qty": 0,
            "use_config_min_sale_qty": 1, "min_sale_qty": 1,
            "use_config_max_sale_qty": True, "max_sale_qty": 10000,
            "use_config_backorders": True, "backorders": 0,
            "use_config_notify_stock_qty": True, "notify_stock_qty": 1,
            "use_config_qty_increments": True, "qty_increments": 0,
            "use_config_enable_qty_inc": True, "enable_qty_increments": False,
            "use_config_manage_stock": True, "manage_stock": False,
            "low_stock_date": None, "is_decimal_divided": False,
            "stock_status_changed_auto": 0,
            "show_default_notification_message": False,
        },
        "configurable_product_options": [
            {
                "id": 1, "attribute_id": "93", "label": "Color",
                "position": 0, "product_id": 9000,
                "values": [{"value_index": 49}, {"value_index": 50}],
            },
            {
                "id": 2, "attribute_id": "152", "label": "Size",
                "position": 1, "product_id": 9000,
                "values": [{"value_index": 167}, {"value_index": 168}],
            },
        ],
        "configurable_product_links": [1001, 1002],
    },
    "product_links": [],
    "options": [],
    "media_gallery_entries": [
        {
            "id": 9001, "media_type": "image",
            "label": "Test Configurable", "position": 1, "disabled": False,
            "types": ["image", "small_image", "thumbnail"],
            "file": "/c/o/conf-test.jpg",
        },
    ],
    "tier_prices": [],
    "custom_attributes": CUSTOM_ATTRS_TEMPLATE,
}


def make_simple_variant(sku, vid, name, color_val, size_val, url_key):
    custom_attrs = [
        {"value": "2", "attribute_code": "tax_class_id"},
        {"value": url_key, "attribute_code": "url_key"},
        {"value": "0", "attribute_code": "has_options"},
        {"value": "0", "attribute_code": "required_options"},
        {"value": f"{name} description", "attribute_code": "description"},
        {"value": f"{name} short desc", "attribute_code": "short_description"},
        {"value": f"/c/o/{url_key}.jpg", "attribute_code": "image"},
        {"value": f"/c/o/{url_key}.jpg", "attribute_code": "small_image"},
        {"value": f"/c/o/{url_key}.jpg", "attribute_code": "thumbnail"},
        {"value": "container2", "attribute_code": "options_container"},
        {"value": "0", "attribute_code": "msrp_display_actual_price_type"},
        {"value": ["2"], "attribute_code": "category_ids"},
        {"value": str(color_val), "attribute_code": "color"},
        {"value": str(size_val), "attribute_code": "size"},
    ]
    return {
        "id": vid,
        "sku": sku,
        "name": name,
        "attribute_set_id": 4,
        "price": 99.0,
        "status": 1,
        "visibility": 1,
        "type_id": "simple",
        "created_at": "2024-01-01 00:00:00",
        "updated_at": "2024-01-01 00:00:00",
        "weight": 0.5,
        "extension_attributes": {
            "website_ids": [1],
            "category_links": [{"position": 0, "category_id": "2"}],
            "stock_item": {
                "item_id": vid, "product_id": vid, "stock_id": 1,
                "qty": 10, "is_in_stock": True, "is_qty_decimal": False,
                "use_config_min_qty": True, "min_qty": 0,
                "use_config_min_sale_qty": 1, "min_sale_qty": 1,
                "use_config_max_sale_qty": True, "max_sale_qty": 10000,
                "use_config_backorders": True, "backorders": 0,
                "use_config_notify_stock_qty": True, "notify_stock_qty": 1,
                "use_config_qty_increments": True, "qty_increments": 0,
                "use_config_enable_qty_inc": True, "enable_qty_increments": False,
                "use_config_manage_stock": True, "manage_stock": True,
                "low_stock_date": None, "is_decimal_divided": False,
                "stock_status_changed_auto": 0,
                "show_default_notification_message": False,
            },
        },
        "product_links": [],
        "options": [],
        "media_gallery_entries": [
            {
                "id": vid + 5000, "media_type": "image",
                "label": name, "position": 1, "disabled": False,
                "types": ["image", "small_image", "thumbnail"],
                "file": f"/c/o/{url_key}.jpg",
            },
        ],
        "tier_prices": [],
        "custom_attributes": custom_attrs,
        # Flattened attributes (as the adapter does)
        "color": str(color_val),
        "size": str(size_val),
        "tax_class_id": "2",
        "url_key": url_key,
        "has_options": "0",
        "required_options": "0",
        "category_ids": ["2"],
    }


VARIANT_1 = make_simple_variant(
    "CONF-TEST-S-Red", 1001, "Test Configurable - S - Red",
    49, 167, "conf-test-s-red")

VARIANT_2 = make_simple_variant(
    "CONF-TEST-M-Blue", 1002, "Test Configurable - M - Blue",
    50, 168, "conf-test-m-blue")


# ── Attribute Definitions ────────────────────────────────────────

def make_attr(code, attr_id, label, frontend_input="text", options=None):
    result = {
        "attribute_id": attr_id,
        "attribute_code": code,
        "default_frontend_label": label,
        "frontend_input": frontend_input,
        "is_required": False,
        "is_visible": True,
        "scope": "global",
        "options": options or [],
    }
    return result


ATTRIBUTES = {
    "tax_class_id": make_attr("tax_class_id", 132, "Tax Class", "select", [
        {"value": "0", "label": "None"},
        {"value": "2", "label": "Taxable Goods"},
    ]),
    "url_key": make_attr("url_key", 97, "URL Key"),
    "has_options": make_attr("has_options", 101, "Has Options", "boolean"),
    "required_options": make_attr("required_options", 102, "Required Options", "boolean"),
    "description": make_attr("description", 75, "Description", "textarea"),
    "short_description": make_attr("short_description", 76, "Short Description", "textarea"),
    "image": make_attr("image", 87, "Base Image", "media_image"),
    "small_image": make_attr("small_image", 88, "Small Image", "media_image"),
    "thumbnail": make_attr("thumbnail", 89, "Thumbnail", "media_image"),
    "options_container": make_attr("options_container", 96, "Display Product Options In", "select"),
    "msrp_display_actual_price_type": make_attr("msrp_display_actual_price_type", 121,
                                                  "Display Actual Price", "select"),
    "category_ids": make_attr("category_ids", 130, "Categories", "multiselect"),
    "name": make_attr("name", 73, "Product Name"),
    "status": make_attr("status", 94, "Enable Product", "select", [
        {"value": "1", "label": "Enabled"},
        {"value": "2", "label": "Disabled"},
    ]),
    "visibility": make_attr("visibility", 95, "Visibility", "select", [
        {"value": "1", "label": "Not Visible Individually"},
        {"value": "4", "label": "Catalog, Search"},
    ]),
    "color": make_attr("color", 93, "Color", "select", [
        {"value": "49", "label": "Red"},
        {"value": "50", "label": "Blue"},
    ]),
    "size": make_attr("size", 152, "Size", "select", [
        {"value": "167", "label": "S"},
        {"value": "168", "label": "M"},
    ]),
}

ATTRIBUTE_SET = {
    "attribute_set_id": 4,
    "attribute_set_name": "Default",
    "sort_order": 0,
    "entity_type_id": 4,
}

ATTRIBUTE_SET_ATTRS = [
    {"attribute_code": code, "attribute_id": attr["attribute_id"],
     "default_frontend_label": attr["default_frontend_label"],
     "frontend_input": attr["frontend_input"]}
    for code, attr in ATTRIBUTES.items()
]


# ── Categories ───────────────────────────────────────────────────

CATEGORIES = {
    1: {
        "id": 1, "parent_id": 0, "name": "Root Catalog",
        "is_active": True, "position": 0, "level": 0,
        "children": "2", "path": "1",
        "created_at": "2020-01-01 00:00:00",
        "updated_at": "2020-01-01 00:00:00",
        "available_sort_by": [], "include_in_menu": True,
        "extension_attributes": {},
        "custom_attributes": [
            {"attribute_code": "path", "value": "1"},
            {"attribute_code": "children_count", "value": "1"},
            {"attribute_code": "url_key", "value": "root-catalog"},
            {"attribute_code": "is_anchor", "value": "1"},
        ],
    },
    2: {
        "id": 2, "parent_id": 1, "name": "Default Category",
        "is_active": True, "position": 1, "level": 1,
        "children": "", "path": "1/2",
        "created_at": "2020-01-01 00:00:00",
        "updated_at": "2020-01-01 00:00:00",
        "available_sort_by": [], "include_in_menu": True,
        "extension_attributes": {},
        "custom_attributes": [
            {"attribute_code": "path", "value": "1/2"},
            {"attribute_code": "children_count", "value": "0"},
            {"attribute_code": "url_key", "value": "default-category"},
            {"attribute_code": "url_path", "value": "default-category"},
            {"attribute_code": "is_anchor", "value": "1"},
            {"attribute_code": "display_mode", "value": "PRODUCTS"},
        ],
    },
}


# ── Tax Classes ──────────────────────────────────────────────────

TAX_SEARCH = {"items": [{"class_id": 2}, {"class_id": 3}]}
TAX_2 = {"class_id": 2, "class_name": "Taxable Goods", "class_type": "PRODUCT"}
TAX_3 = {"class_id": 3, "class_name": "Retail Customer", "class_type": "CUSTOMER"}


# ── Build Cassette ───────────────────────────────────────────────

def build_interactions():
    """Build all VCR interactions needed for template import."""
    interactions = []

    def add(uri, data):
        interactions.append(interaction(uri, json.dumps(data, separators=(',', ':'))))

    # 1. Read configurable product template
    add(f'{BASE}/rest/V1/products/CONF-TEST', CONFIGURABLE_PRODUCT)

    # 2. Attribute set
    add(f'{BASE}/rest/V1/products/attribute-sets/4', ATTRIBUTE_SET)
    add(f'{BASE}/rest/V1/products/attribute-sets/4/attributes', ATTRIBUTE_SET_ATTRS)

    # 3. Individual attributes (by code — for custom_attributes import)
    for code, attr_data in ATTRIBUTES.items():
        for prefix in ['/rest/V1', '/rest/all/V1']:
            add(f'{BASE}{prefix}/products/attributes/{code}', attr_data)

    # 4. Individual attributes by numeric ID (attribute set importer + configurable options)
    # The attribute set importer calls importer.run(attribute_id) for EACH attribute
    for code, attr_data in ATTRIBUTES.items():
        attr_id = attr_data["attribute_id"]
        for prefix in ['/rest/V1', '/rest/all/V1']:
            add(f'{BASE}{prefix}/products/attributes/{attr_id}', attr_data)

    # 5. Categories
    for cat_id, cat_data in CATEGORIES.items():
        for prefix in ['/rest/V1', '/rest/default/V1']:
            add(f'{BASE}{prefix}/categories/{cat_id}', cat_data)

    # 6. Tax classes
    add(f'{BASE}/rest/V1/taxClasses/search?fields=items%5Bclass_id%5D&searchCriteria=', TAX_SEARCH)
    for prefix in ['/rest/V1', '/rest/all/V1']:
        add(f'{BASE}{prefix}/taxClasses/2', TAX_2)
        add(f'{BASE}{prefix}/taxClasses/3', TAX_3)

    # 7. List configurable product children
    add(f'{BASE}/rest/V1/configurable-products/CONF-TEST/children', [VARIANT_1, VARIANT_2])

    # 8. Read each variant (main + default storeview for translations)
    for variant in [VARIANT_1, VARIANT_2]:
        sku = variant['sku']
        add(f'{BASE}/rest/V1/products/{sku}', variant)
        add(f'{BASE}/rest/default/V1/products/{sku}', variant)

    # 9. Template translation read
    add(f'{BASE}/rest/default/V1/products/CONF-TEST', CONFIGURABLE_PRODUCT)

    return interactions


def build_attribute_set_interactions():
    """Build interactions for attribute set import test."""
    interactions = []

    def add(uri, data):
        interactions.append(interaction(uri, json.dumps(data, separators=(',', ':'))))

    # 1. Attribute set read
    add(f'{BASE}/rest/V1/products/attribute-sets/4', ATTRIBUTE_SET)

    # 2. Attribute set details (list of attributes)
    add(f'{BASE}/rest/V1/products/attribute-sets/4/attributes', ATTRIBUTE_SET_ATTRS)

    # 3. Each attribute by numeric ID (importer calls run(attribute_id))
    for code, attr_data in ATTRIBUTES.items():
        attr_id = attr_data["attribute_id"]
        for prefix in ['/rest/V1', '/rest/all/V1']:
            add(f'{BASE}{prefix}/products/attributes/{attr_id}', attr_data)

    # 4. Each attribute by code (for binder lookups)
    for code, attr_data in ATTRIBUTES.items():
        for prefix in ['/rest/V1', '/rest/all/V1']:
            add(f'{BASE}{prefix}/products/attributes/{code}', attr_data)

    # 5. Tax classes (needed by metadata sync in setUp)
    add(f'{BASE}/rest/V1/taxClasses/search?fields=items%5Bclass_id%5D&searchCriteria=', TAX_SEARCH)
    for prefix in ['/rest/V1', '/rest/all/V1']:
        add(f'{BASE}{prefix}/taxClasses/2', TAX_2)
        add(f'{BASE}{prefix}/taxClasses/3', TAX_3)

    return interactions


def write_cassette(output_path, interactions):
    """Write a VCR cassette file with duplicated interactions."""
    all_blocks = []
    for block in interactions:
        for _ in range(DUPLICATE_COUNT):
            all_blocks.append(block)

    with open(output_path, 'w') as f:
        f.write('# __PATCHED_DUPLICATES__\n')
        f.write('interactions:\n')
        f.write('\n'.join(all_blocks))
        f.write('\nversion: 1\n')

    total = len(all_blocks)
    unique = len(interactions)
    print(f"  Generated {output_path}")
    print(f"    {unique} unique interactions, {total} total (x{DUPLICATE_COUNT} duplicates)")


def build_export_product_interactions():
    """Build interactions for product export test (create new product)."""
    interactions = []

    def add(uri, data, method='GET'):
        interactions.append(interaction(uri, json.dumps(data, separators=(',', ':')), method))

    # POST /products — create new product, Magento returns the created product
    created_product = {
        "id": 5001,
        "sku": "TEST-EXPORT-SIMPLE",
        "name": "Test Export Product",
        "attribute_set_id": 4,
        "price": 49.99,
        "status": 1,
        "visibility": 4,
        "type_id": "simple",
        "created_at": "2024-01-01 00:00:00",
        "updated_at": "2024-01-01 00:00:00",
        "weight": 1,
        "extension_attributes": {
            "website_ids": [1],
            "stock_item": {
                "item_id": 5001, "product_id": 5001, "stock_id": 1,
                "qty": 0, "is_in_stock": True, "manage_stock": True,
            },
        },
        "product_links": [],
        "options": [],
        "media_gallery_entries": [],
        "custom_attributes": [],
    }
    add(f'{BASE}/rest/V1/products', created_product, 'POST')

    # GET /stockItems/{sku} — read stock item before update
    add(f'{BASE}/rest/V1/stockItems/TEST-EXPORT-SIMPLE', {
        "item_id": 5001, "product_id": 5001, "stock_id": 1,
        "qty": 0, "is_in_stock": True,
    })

    # PUT /products/{sku}/stockItems/{id} — export inventory after create
    add(f'{BASE}/rest/V1/products/TEST-EXPORT-SIMPLE/stockItems/5001',
        5001, 'PUT')

    return interactions


def build_export_product_update_interactions():
    """Build interactions for product export update test."""
    interactions = []

    def add(uri, data, method='GET'):
        interactions.append(interaction(uri, json.dumps(data, separators=(',', ':')), method))

    # DELETE images before update
    add(f'{BASE}/rest/V1/products/TEST-EXPORT-UPDATE/media', [], 'GET')

    # PUT /products/{sku} — update product
    updated_product = {
        "id": 5002,
        "sku": "TEST-EXPORT-UPDATE",
        "name": "Updated Export Product",
        "attribute_set_id": 4,
        "price": 59.99,
        "status": 1,
        "visibility": 4,
        "type_id": "simple",
        "created_at": "2024-01-01 00:00:00",
        "updated_at": "2024-01-02 00:00:00",
        "weight": 1,
        "extension_attributes": {"website_ids": [1]},
        "product_links": [],
        "media_gallery_entries": [],
        "custom_attributes": [],
    }
    add(f'{BASE}/rest/all/V1/products/TEST-EXPORT-UPDATE', updated_product, 'PUT')

    return interactions


def main():
    cassette_dir = os.path.join(os.path.dirname(__file__), 'cassettes')

    # Configurable template import cassette
    write_cassette(
        os.path.join(cassette_dir, 'import_product_template_CONF-TEST.yaml'),
        build_interactions())

    # Attribute set import cassette
    write_cassette(
        os.path.join(cassette_dir, 'import_product_attribute_set_4.yaml'),
        build_attribute_set_interactions())

    # Export product cassettes
    write_cassette(
        os.path.join(cassette_dir, 'test_export_product_create.yaml'),
        build_export_product_interactions())
    write_cassette(
        os.path.join(cassette_dir, 'test_export_product_update.yaml'),
        build_export_product_update_interactions())


if __name__ == '__main__':
    main()
