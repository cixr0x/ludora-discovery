from __future__ import annotations

from typing import Protocol

from ludora.models import DiscoveryItemCandidateRecord
from ludora.product_crawler import crawl_store_product_details, ItemCandidateProcessor, update_confirmed_store_item_details


class ItemCandidateRepository(Protocol):
    def item_candidate_exists(self, store_id: int | None, source_url: str) -> bool:
        ...

    def upsert_item_candidate(self, record: DiscoveryItemCandidateRecord) -> object | None:
        ...

    def list_confirmed_boardgame_item_candidates(self, limit: int | None = None) -> list[DiscoveryItemCandidateRecord]:
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


def update_confirmed_store_items(
    repository: ItemCandidateRepository,
    limit: int | None = None,
    browser_fetch_enabled: bool = False,
) -> list[DiscoveryItemCandidateRecord]:
    return update_confirmed_store_item_details(
        repository,
        limit=limit,
        browser_fetch_enabled=browser_fetch_enabled,
    )
