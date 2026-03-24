#!/usr/bin/env python3
"""
Patch VCR cassettes with missing Magento 2 API responses.

Scans cassette YAML files for referenced entity IDs and adds fabricated
API responses for any missing endpoints. Uses TEXT APPEND to preserve
VCR's native YAML format (flow-style lists, inline mappings).

Usage:
    python3 patch_cassettes.py [--cassette-dir DIR] [--dry-run]
"""

import argparse
import glob
import json
import os
import re

import yaml


# ── Magento 2 entity definitions ─────────────────────────────────

ATTRIBUTE_SETS = {
    4: "Default", 9: "Top", 10: "Bottom", 11: "Gear",
    12: "Sprite Stasis Ball", 13: "Sprite Yoga Companion Kit",
}

KNOWN_ATTRS = {
    'category_ids': 100, 'children_count': 101, 'color': 93,
    'description': 102, 'display_mode': 103, 'has_options': 104,
    'image': 105, 'is_anchor': 106, 'msrp_display_actual_price_type': 107,
    'options_container': 108, 'path': 109, 'required_options': 110,
    'size': 152, 'small_image': 111, 'tax_class_id': 112,
    'thumbnail': 113, 'url_key': 114, 'url_path': 115,
    'meta_title': 116, 'meta_keyword': 117, 'meta_description': 118,
    'name': 73, 'price': 77, 'status': 119, 'visibility': 120,
    'weight': 121, 'short_description': 122, 'special_price': 123,
    'special_from_date': 124, 'special_to_date': 125,
    'gift_message_available': 126, 'news_from_date': 127,
    'news_to_date': 128, 'country_of_manufacture': 129,
    'custom_layout_update': 130, 'page_layout': 131,
    'activity': 132, 'style_general': 133, 'material': 134,
    'gender': 135, 'erin_recommends': 136, 'features_bags': 137,
    'format': 138, 'performance_fabric': 139, 'sale': 140,
    'pattern': 141, 'climate': 142, 'eco_collection': 143,
    'new': 144, 'collar': 145, 'sleeve': 146, 'style_bags': 147,
    'strap_bags': 148, 'style_bottom': 149,
    'custom_apply_to_products': 150, 'custom_use_parent_settings': 151,
    'category_gear': 153, 'ts_dimensions_length': 154,
    'ts_dimensions_width': 155, 'ts_dimensions_height': 156,
}

BASE_URL = 'http://magento/index.php/rest/V1'
BASE_URL_ALL = 'http://magento/index.php/rest/all/V1'
BASE_URL_DEFAULT = 'http://magento/index.php/rest/default/V1'


# ── VCR-native YAML text templates ──────────────────────────────
# These match the exact format VCR uses when recording cassettes.

VCR_INTERACTION = """- request:
    body: null
    headers:
      Accept: ['*/*']
      Accept-Encoding: ['gzip, deflate']
      Connection: [keep-alive]
      User-Agent: [python-requests/2.19.1]
    method: GET
    uri: {uri}
  response:
    body: {{string: '{body}'}}
    headers:
      Content-Type: ['application/json; charset=utf-8']
    status: {{code: 200, message: OK}}"""

VCR_IMAGE_INTERACTION = """- request:
    body: null
    headers:
      Accept: ['*/*']
      Accept-Encoding: ['gzip, deflate']
      Connection: [keep-alive]
      User-Agent: [python-requests/2.19.1]
    method: GET
    uri: {uri}
  response:
    body: {{string: ''}}
    headers:
      Content-Type: ['image/jpeg']
    status: {{code: 200, message: OK}}"""


# ── Helpers ──────────────────────────────────────────────────────

def get_attr_id(code):
    return KNOWN_ATTRS.get(code, abs(hash(code)) % 9000 + 1000)


def body_to_str(body):
    if isinstance(body, dict):
        val = body.get('string', '')
        if isinstance(val, bytes):
            return val.decode('utf-8', errors='replace')
        return str(val) if val else ''
    return str(body) if body else ''


def make_vcr_interaction(uri, body_json):
    """Generate a VCR interaction in native YAML text format."""
    # Escape single quotes in body for YAML
    body_escaped = body_json.replace("'", "''")
    return VCR_INTERACTION.format(uri=uri, body=body_escaped)


def make_vcr_image_interaction(uri):
    """Generate a VCR image interaction (empty body, image content type)."""
    return VCR_IMAGE_INTERACTION.format(uri=uri)


# ── Patchers ─────────────────────────────────────────────────────

def collect_needed_interactions(raw_text):
    """Analyze cassette text and return list of (uri, body_json) to add."""
    needed = []

    # Extract existing URIs from raw text
    existing_uris = set(re.findall(r'uri: (.+?)$', raw_text, re.MULTILINE))

    # Find attribute_set_ids in response bodies
    set_ids = set(int(m) for m in re.findall(r'"attribute_set_id":(\d+)', raw_text))
    for sid in sorted(set_ids):
        # Attribute set read
        uri = f'{BASE_URL}/products/attribute-sets/{sid}'
        if uri not in existing_uris:
            name = ATTRIBUTE_SETS.get(sid, f'Set{sid}')
            body = json.dumps({
                "attribute_set_id": sid, "attribute_set_name": name,
                "sort_order": 0, "entity_type_id": 4,
            })
            needed.append((uri, body))
            existing_uris.add(uri)

        # Attribute set detail (empty to avoid deep dependency chain)
        detail_uri = f'{BASE_URL}/products/attribute-sets/{sid}/attributes'
        if detail_uri not in existing_uris:
            needed.append((detail_uri, '[]'))
            existing_uris.add(detail_uri)

    # Find attribute_codes in response bodies + always include common ones
    attr_codes = set(re.findall(r'"attribute_code":"([^"]+)"', raw_text))
    # Always include attributes that are commonly needed during imports
    attr_codes.update([
        'tax_class_id', 'url_key', 'name', 'description', 'short_description',
        'image', 'small_image', 'thumbnail', 'category_ids', 'has_options',
        'required_options', 'options_container', 'visibility', 'status',
    ])
    for code in sorted(attr_codes):
        body = json.dumps({
            "attribute_id": get_attr_id(code), "attribute_code": code,
            "frontend_input": "text", "default_frontend_label": code,
            "is_required": False, "is_user_defined": False,
            "is_visible": True, "scope": "global", "options": [],
        })
        # Add for both /rest/V1/ and /rest/all/V1/ (storeview='all')
        for base in [BASE_URL, BASE_URL_ALL]:
            uri = f'{base}/products/attributes/{code}'
            if uri not in existing_uris:
                needed.append((uri, body))
                existing_uris.add(uri)

    # Linked products (grouped/related) — needed when importing grouped products
    linked_skus = set(re.findall(r'"linked_product_sku":"([^"]+)"', raw_text))
    for sku in sorted(linked_skus):
        for base in [BASE_URL, BASE_URL_ALL, BASE_URL_DEFAULT]:
            uri = f'{base}/products/{sku}'
            if uri not in existing_uris:
                body = json.dumps({
                    "id": abs(hash(sku)) % 9000 + 1000,
                    "sku": sku,
                    "name": f"Product {sku}",
                    "attribute_set_id": 11,
                    "price": 14.0,
                    "status": 1,
                    "visibility": 1,
                    "type_id": "simple",
                    "created_at": "2020-03-27 16:06:29",
                    "updated_at": "2020-03-27 16:06:29",
                    "extension_attributes": {"website_ids": [1], "category_links": []},
                    "product_links": [],
                    "options": [],
                    "media_gallery_entries": [],
                    "custom_attributes": [
                        {"attribute_code": "tax_class_id", "value": "2"},
                        {"attribute_code": "url_key", "value": sku.lower()},
                        {"attribute_code": "has_options", "value": "0"},
                        {"attribute_code": "required_options", "value": "0"},
                    ],
                })
                needed.append((uri, body))
                existing_uris.add(uri)

    # Default storeview copies for all product URLs
    # The translation importer reads products from rest/default/V1/products/...
    # We need to create default storeview copies for any product URL that only
    # exists in rest/V1 or rest/all/V1
    for uri in list(existing_uris):
        if '/rest/V1/products/' in uri and '/attribute' not in uri:
            default_uri = uri.replace('/rest/V1/', '/rest/default/V1/')
            if default_uri not in existing_uris:
                # Find the response body for this URI
                pattern = re.escape(uri) + r".*?body: \{string: '(.+?)'\}"
                match = re.search(pattern, raw_text, re.DOTALL)
                if match:
                    needed.append((default_uri, match.group(1).replace("''", "'")))
                    existing_uris.add(default_uri)

    # Tax classes — needed for product imports
    # The adapter calls: GET /taxClasses/search?fields=items[class_id]&searchCriteria=
    tax_class_search_uri = f'{BASE_URL}/taxClasses/search?fields=items%5Bclass_id%5D&searchCriteria='
    if tax_class_search_uri not in existing_uris:
        tax_classes_body = json.dumps({
            "items": [
                {"class_id": 2},
                {"class_id": 3},
            ],
        })
        needed.append((tax_class_search_uri, tax_classes_body))

    # Individual tax class reads
    for tax_id, tax_name, tax_type in [(2, "Taxable Goods", "PRODUCT"), (3, "Retail Customer", "CUSTOMER")]:
        for base in [BASE_URL, BASE_URL_ALL]:
            uri = f'{base}/taxClasses/{tax_id}'
            if uri not in existing_uris:
                body = json.dumps({"class_id": tax_id, "class_name": tax_name, "class_type": tax_type})
                needed.append((uri, body))
                existing_uris.add(uri)

    # Image URLs from media_gallery_entries — VCR intercepts these before
    # the requests.get mock, so we need to provide cassette responses
    # Extract image file paths like /l/u/luma-yoga-strap-set.jpg
    image_files = re.findall(r'"file"\s*:\s*"([^"]+\.(?:jpg|jpeg|png|gif))"', raw_text)
    media_base = 'http://magento/media/catalog/product/'
    for img_path in image_files:
        # img_path may have JSON-escaped slashes like \/l\/u\/file.jpg
        img_path = img_path.replace('\\/', '/')
        # Add both with and without leading slash — the backend concatenates
        # media_url + file, which can produce double slashes
        for path_variant in [img_path.lstrip('/'), img_path]:
            img_uri = media_base + path_variant
            if img_uri not in existing_uris:
                needed.append(('__IMAGE__', img_uri))
                existing_uris.add(img_uri)

    return needed


PATCH_MARKER = '# __PATCHED_DUPLICATES__'


def duplicate_consumed_interactions(raw_text):
    """Duplicate interactions that VCR will consume but are needed multiple times.

    VCR with record_mode='none'/'once' consumes each recorded interaction once.
    If the code requests the same URL multiple times, VCR fails on the 2nd call.

    We add DUPLICATE_COUNT extra copies of each ORIGINAL interaction.
    This function is idempotent — already-duplicated interactions are skipped.
    """
    DUPLICATE_COUNT = 2  # import + translation per storeview

    # If already patched, skip duplication
    if PATCH_MARKER in raw_text:
        return []

    raw_blocks = []

    # Split cassette into interaction blocks (each starts with "- request:")
    blocks = re.split(r'^(?=- request:)', raw_text, flags=re.MULTILINE)

    for block in blocks:
        if not block.strip() or not block.startswith('- request:'):
            continue
        # Strip any trailing 'version: N' that might be in the last block
        clean = re.sub(r'\nversion: \d+\s*$', '', block.rstrip())
        if clean.strip():
            raw_blocks.append(clean)

    # Return raw blocks as special entries — patch_cassette handles them
    return [('__RAW_BLOCK__', block) for block in raw_blocks for _ in range(DUPLICATE_COUNT)]


def patch_cassette(fpath, dry_run=False):
    """Patch a single cassette file. Returns list of added interaction descriptions."""
    with open(fpath, 'r') as f:
        raw_text = f.read()

    needed = collect_needed_interactions(raw_text)
    needed.extend(duplicate_consumed_interactions(raw_text))
    if not needed:
        return []

    if not dry_run:
        # Build new interactions: templated + raw blocks
        parts = []
        for uri, body in needed:
            if uri == '__RAW_BLOCK__':
                parts.append(body)
            elif uri == '__IMAGE__':
                parts.append(make_vcr_image_interaction(body))
            else:
                parts.append(make_vcr_interaction(uri, body))
        new_text = '\n'.join(parts)
        # Insert BEFORE 'version: 1' footer (required by VCR)
        version_match = re.search(r'\nversion: \d+\s*$', raw_text)
        if version_match:
            insert_pos = version_match.start()
            raw_text = raw_text[:insert_pos] + '\n' + new_text + raw_text[insert_pos:]
        else:
            # No version footer — append at end
            if not raw_text.endswith('\n'):
                raw_text += '\n'
            raw_text += new_text + '\n'

        # Add marker to prevent re-duplication on subsequent runs
        if PATCH_MARKER not in raw_text:
            raw_text = PATCH_MARKER + '\n' + raw_text
        with open(fpath, 'w') as f:
            f.write(raw_text)

    # Build summary of what was added
    added = []
    for uri, body in needed:
        if uri == '__RAW_BLOCK__':
            # Extract URI from raw block for summary
            m = re.search(r'uri: (.+?)$', body, re.MULTILINE)
            short = m.group(1).split('/V1')[-1] + '(dup)' if m else '(raw)'
        else:
            short = uri.replace(BASE_URL_ALL, '(all)').replace(BASE_URL, '')
        added.append(short)

    return added


# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Patch VCR cassettes with missing Magento 2 responses')
    parser.add_argument(
        '--cassette-dir',
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cassettes'))
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be patched without writing')
    args = parser.parse_args()

    cassette_files = sorted(glob.glob(os.path.join(args.cassette_dir, '*.yaml')))
    if not cassette_files:
        print(f"No cassettes found in {args.cassette_dir}")
        return

    total_patched = 0
    total_added = 0

    for fpath in cassette_files:
        added = patch_cassette(fpath, dry_run=args.dry_run)
        if added:
            total_patched += 1
            total_added += len(added)
            prefix = "[DRY RUN] " if args.dry_run else ""
            preview = ', '.join(added[:3])
            suffix = f'... +{len(added) - 3} more' if len(added) > 3 else ''
            print(f"  {prefix}{os.path.basename(fpath)}: +{len(added)} ({preview}{suffix})")

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}"
          f"Total: {total_patched} cassettes, {total_added} interactions added")


if __name__ == '__main__':
    main()
