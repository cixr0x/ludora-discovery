import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ludora.database import ItemCandidateUpsertResult
from ludora.inventory import collect_store_inventory
from ludora.product_crawler import crawl_store_product_details
from ludora.webfetch import FetchResult


class FakeRepository:
    def __init__(self, upsert_result=None):
        self.item_records = []
        self.upsert_result = upsert_result

    def upsert_item_candidate(self, record):
        self.item_records.append(record)
        return self.upsert_result


class FakeItemProcessor:
    def __init__(self):
        self.processed = []

    def process_candidate(self, candidate_id, record):
        self.processed.append((candidate_id, record))


class InventoryTests(unittest.TestCase):
    def test_collect_store_inventory_prefers_sitemap_product_urls(self):
        detail_html = """
        <script type="application/ld+json">
        {
          "@type": "Product",
          "name": "Catan",
          "description": "Juego de mesa para 3 a 4 jugadores.",
          "brand": {"name": "Devir"},
          "offers": {"price": "899.00", "priceCurrency": "MXN"}
        }
        </script>
        """
        repository = FakeRepository()

        with patch(
            "ludora.product_crawler.discover_product_urls_from_sitemaps",
            return_value=["https://example.mx/products/catan"],
        ) as discover_product_urls, patch(
            "ludora.product_crawler.fetch_html",
            return_value=FetchResult(url="https://example.mx/products/catan", text=detail_html),
        ) as fetch_html:
            records = collect_store_inventory("https://example.mx/", 12, repository)

        discover_product_urls.assert_called_once_with(
            "https://example.mx/",
            browser_fetcher=None,
            browser_fallback_enabled=False,
            limit=None,
        )
        fetch_html.assert_called_once_with("https://example.mx/products/catan")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Catan")
        self.assertTrue(records[0].is_boardgame)
        self.assertFalse(records[0].is_boardgame_confirmed)
        self.assertEqual(repository.item_records[0].source_listing_url, "https://example.mx/sitemap.xml")

    def test_collect_store_inventory_falls_back_to_homepage_product_links(self):
        html = '<a href="/products/catan">Catan</a><span>$899 MXN</span>'
        detail_html = """
        <script type="application/ld+json">
        {
          "@type": "Product",
          "name": "Catan",
          "brand": {"name": "Devir"},
          "offers": {"price": "899.00", "priceCurrency": "MXN"}
        }
        </script>
        """
        repository = FakeRepository()

        with patch("ludora.product_crawler.discover_product_urls_from_sitemaps", return_value=[]), patch(
            "ludora.product_crawler.fetch_html",
            side_effect=[
                FetchResult(url="https://example.mx/", text=html),
                FetchResult(url="https://example.mx/products/catan", text=detail_html),
            ],
        ):
            records = collect_store_inventory("https://example.mx/", 12, repository)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Catan")
        self.assertEqual(records[0].publisher, "Devir")
        self.assertEqual(repository.item_records[0].store_id, 12)
        self.assertEqual(repository.item_records[0].source_url, "https://example.mx/products/catan")
        self.assertEqual(repository.item_records[0].source_listing_url, "https://example.mx/")

    def test_crawl_store_product_details_uses_browser_for_blocked_detail_page(self):
        challenge_html = """
        <!DOCTYPE html>
        <html>
          <head><title>One moment, please...</title></head>
          <body>
            <script>
              setTimeout(function(){ window.location.reload(); }, 5000);
            </script>
          </body>
        </html>
        """
        detail_html = """
        <script type="application/ld+json">
        {
          "@type": "Product",
          "name": "Exploding Kittens",
          "brand": {"name": "Exploding Kittens"},
          "offers": {"price": "499.00", "priceCurrency": "MXN"}
        }
        </script>
        """
        repository = FakeRepository()
        browser_fetched_urls = []

        def fake_browser_fetcher(url):
            browser_fetched_urls.append(url)
            return FetchResult(url=url, text=detail_html)

        with patch(
            "ludora.product_crawler.discover_product_urls_from_sitemaps",
            return_value=["https://example.mx/producto/exploding-kittens/"],
        ) as discover_product_urls, patch(
            "ludora.product_crawler.fetch_html",
            return_value=FetchResult(url="https://example.mx/producto/exploding-kittens/", text=challenge_html),
        ):
            records = crawl_store_product_details(
                "https://example.mx/",
                12,
                repository,
                browser_fetch_enabled=True,
                browser_fetcher=fake_browser_fetcher,
            )

        discover_product_urls.assert_called_once_with(
            "https://example.mx/",
            browser_fetcher=fake_browser_fetcher,
            browser_fallback_enabled=True,
            limit=None,
        )
        self.assertEqual(browser_fetched_urls, ["https://example.mx/producto/exploding-kittens/"])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Exploding Kittens")
        self.assertEqual(records[0].price, "499.00")

    def test_crawl_store_product_details_processes_new_candidates_after_upsert(self):
        detail_html = """
        <script type="application/ld+json">
        {
          "@type": "Product",
          "name": "Catan",
          "description": "Juego de mesa para 3 a 4 jugadores.",
          "offers": {"price": "899.00", "priceCurrency": "MXN"}
        }
        </script>
        """
        repository = FakeRepository(
            ItemCandidateUpsertResult(candidate_id=101, status="NEW", item_id=None, should_process=True)
        )
        processor = FakeItemProcessor()

        with patch(
            "ludora.product_crawler.discover_product_urls_from_sitemaps",
            return_value=["https://example.mx/products/catan"],
        ), patch(
            "ludora.product_crawler.fetch_html",
            return_value=FetchResult(url="https://example.mx/products/catan", text=detail_html),
        ):
            crawl_store_product_details(
                "https://example.mx/",
                12,
                repository,
                item_processor=processor,
            )

        self.assertEqual(len(processor.processed), 1)
        self.assertEqual(processor.processed[0][0], 101)
        self.assertEqual(processor.processed[0][1].title, "Catan")

    def test_crawl_store_product_details_skips_processing_when_upsert_says_not_to_process(self):
        detail_html = """
        <script type="application/ld+json">
        {"@type": "Product", "name": "Catan"}
        </script>
        """
        repository = FakeRepository(
            ItemCandidateUpsertResult(candidate_id=102, status="MATCH_NOT_FOUND", item_id=None, should_process=False)
        )
        processor = FakeItemProcessor()

        with patch(
            "ludora.product_crawler.discover_product_urls_from_sitemaps",
            return_value=["https://example.mx/products/catan"],
        ), patch(
            "ludora.product_crawler.fetch_html",
            return_value=FetchResult(url="https://example.mx/products/catan", text=detail_html),
        ):
            crawl_store_product_details(
                "https://example.mx/",
                12,
                repository,
                item_processor=processor,
            )

        self.assertEqual(processor.processed, [])


if __name__ == "__main__":
    unittest.main()
