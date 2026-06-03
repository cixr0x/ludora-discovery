from __future__ import annotations

from typing import Protocol

from ludora.models import DiscoveryItemCandidateRecord
from ludora.product_crawler import crawl_store_product_details, ItemCandidateProcessor


class ItemCandidateRepository(Protocol):
    def upsert_item_candidate(self, record: DiscoveryItemCandidateRecord) -> object | None:
        ...


def collect_store_inventory(
    store_url: str,
    store_id: int | None,
    repository: ItemCandidateRepository,
    limit: int | None = None,
    browser_sitemap_fetch_enabled: bool = False,
    item_processor: ItemCandidateProcessor | None = None,
) -> list[DiscoveryItemCandidateRecord]:
    return crawl_store_product_details(
        store_url,
        store_id,
        repository,
        limit=limit,
        browser_sitemap_fetch_enabled=browser_sitemap_fetch_enabled,
        item_processor=item_processor,
    )
