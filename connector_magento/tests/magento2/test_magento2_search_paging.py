# Copyright 2026 TRIVAX INNOVA SL
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html)
"""Magento 2 search endpoints are always paged.

Without ``searchCriteria[pageSize]`` Magento tries to serialise the whole
result set: on a shop with 15,098 customers ``customers/search`` answers
HTTP 500 outright, and ``products`` takes ~9 s for 2,400 SKUs and times out
on bigger catalogues. The generic adapter therefore walks every search
endpoint page by page and merges the pages.

Measured behaviours the loop has to cope with (m2.forumformat.com,
2026-09-14): a page past the end comes back as ``{"items": null,
"total_count": N}`` on this shop, while other Magento builds repeat the
last page instead -- hence the double stop condition (short page OR
``total_count`` reached). Repository endpoints that ignore paging (attribute
options, the category tree) return a plain list and must pass through
untouched.

No network: ``_call`` is replaced by a fake shop.
"""

from unittest.mock import patch

from .common import Magento2TestCase

PAGE = 500


class FakeShop:
    """Serves ``customers/search`` pages like Magento 2 does."""

    def __init__(self, total, repeat_last_page=False, with_total=True):
        self.total = total
        self.repeat_last_page = repeat_last_page
        self.with_total = with_total
        self.calls = []

    def __call__(self, path, params=None, **kwargs):
        self.calls.append((path, dict(params or {})))
        size = params["searchCriteria[pageSize]"]
        page = params["searchCriteria[currentPage]"]
        pages = max(1, -(-self.total // size))
        if page > pages:
            if not self.repeat_last_page:
                return {"items": None, "total_count": self.total}
            page = pages
        start = (page - 1) * size
        items = [{"id": i} for i in range(start + 1, min(start + size, self.total) + 1)]
        res = {"items": items}
        if self.with_total:
            res["total_count"] = self.total
        return res


class TestMagento2SearchPaging(Magento2TestCase):
    def _run(self, shop, method, **kwargs):
        with self.backend.work_on("magento.res.partner") as work:
            adapter = work.component(usage="backend.adapter")
            with patch.object(type(adapter), "_call", side_effect=shop):
                return getattr(adapter, method)(**kwargs)

    def _search(self, shop, **kwargs):
        return self._run(shop, "search", **kwargs)

    def test_search_walks_all_pages(self):
        shop = FakeShop(total=1200)
        ids = self._search(shop, filters={"website_id": {"in": ["1"]}})
        self.assertEqual(ids, list(range(1, 1201)))
        # 3 pages of 500; total_count stops the loop, no 4th call
        self.assertEqual(len(shop.calls), 3)
        for n, (path, params) in enumerate(shop.calls, start=1):
            self.assertEqual(path, "customers/search")
            self.assertEqual(params["searchCriteria[pageSize]"], PAGE)
            self.assertEqual(params["searchCriteria[currentPage]"], n)
            self.assertIn("total_count", params["fields"])
            # the website filter travels with every page
            self.assertEqual(
                params["searchCriteria[filter_groups][0][filters][0][field]"],
                "website_id",
            )

    def test_search_without_filters_sends_no_empty_placeholder(self):
        shop = FakeShop(total=3)
        ids = self._search(shop)
        self.assertEqual(ids, [1, 2, 3])
        self.assertEqual(len(shop.calls), 1)
        self.assertNotIn("searchCriteria", shop.calls[0][1])

    def test_search_stops_on_short_page_without_total_count(self):
        shop = FakeShop(total=620, with_total=False)
        ids = self._search(shop)
        self.assertEqual(len(ids), 620)
        self.assertEqual(len(shop.calls), 2)

    def test_search_survives_a_shop_that_repeats_the_last_page(self):
        # Exact multiple of the page size, on a shop that answers a page past
        # the end with the last page again: a "stop on empty page" loop would
        # never end here. total_count is what stops it.
        shop = FakeShop(total=1000, repeat_last_page=True)
        ids = self._search(shop)
        self.assertEqual(len(ids), 1000)
        self.assertEqual(len(shop.calls), 2)

    def test_search_passes_plain_lists_through(self):
        """Repository endpoints (attribute options) ignore paging."""
        calls = []

        def fake(path, params=None, **kwargs):
            calls.append(path)
            return [{"id": 7}, {"id": 8}]

        ids = self._search(fake)
        self.assertEqual(ids, [7, 8])
        self.assertEqual(len(calls), 1)

    def test_search_read_merges_pages(self):
        shop = FakeShop(total=700)
        res = self._run(shop, "search_read", filters={"website_id": {"eq": "1"}})
        self.assertEqual(len(res["items"]), 700)
        self.assertEqual(res["total_count"], 700)
        self.assertEqual(len(shop.calls), 2)
