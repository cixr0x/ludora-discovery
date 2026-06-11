from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from urllib.parse import urljoin, urlparse

from ludora.item_classification import apply_item_classification
from ludora.listing_extraction import extract_listing_candidates
from ludora.models import DiscoveryItemCandidateRecord
from ludora.product_detail_extraction import extract_product_detail_candidate
from ludora.sitemap_discovery import _looks_like_site_protection_challenge, discover_product_urls_from_sitemaps
from ludora.webfetch import FetchResult
from ludora.webfetch import fetch_html


class ItemCandidateRepository(Protocol):
    def item_candidate_exists(self, store_id: int | None, source_url: str) -> bool:
        ...

    def upsert_item_candidate(self, record: DiscoveryItemCandidateRecord) -> object | None:
        ...

    def list_confirmed_boardgame_item_candidates(self, limit: int | None = None) -> list[DiscoveryItemCandidateRecord]:
        ...


class ItemCandidateProcessor(Protocol):
    def process_candidate(self, candidate_id: int, record: DiscoveryItemCandidateRecord) -> None:
        ...


def crawl_store_product_details(
    store_url: str,
    store_id: int | None,
    repository: ItemCandidateRepository,
    limit: int | None = None,
    browser_sitemap_fetch_enabled: bool = False,
    browser_fetch_enabled: bool | None = None,
    browser_fetcher: Callable[[str], FetchResult | None] | None = None,
    item_processor: ItemCandidateProcessor | None = None,
) -> list[DiscoveryItemCandidateRecord]:
    use_browser_fetch = browser_sitemap_fetch_enabled if browser_fetch_enabled is None else browser_fetch_enabled
    browser_session = None
    if use_browser_fetch and browser_fetcher is None:
        from ludora.browser_fetch import BrowserTextFetcher

        browser_session = BrowserTextFetcher()
        browser_fetcher = browser_session.__enter__().fetch

    try:
        product_urls = discover_product_urls_from_sitemaps(
            store_url,
            browser_fetcher=browser_fetcher,
            browser_fallback_enabled=use_browser_fetch,
            limit=limit,
        )
        if product_urls:
            source_listing_url = urljoin(store_url, "/sitemap.xml")
            listing_candidates = [
                DiscoveryItemCandidateRecord(
                    store_id=store_id,
                    source_url=product_url,
                    source_listing_url=source_listing_url,
                    title=_title_from_url(product_url),
                )
                for product_url in product_urls
            ]
        else:
            fetched_listing = fetch_html(store_url)
            if fetched_listing is None:
                return []

            source_listing_url = fetched_listing.url
            listing_candidates = extract_listing_candidates(
                html=fetched_listing.text,
                page_url=fetched_listing.url,
                store_id=store_id,
                limit=limit,
            )

        records: list[DiscoveryItemCandidateRecord] = []
        for listing_candidate in listing_candidates:
            if repository.item_candidate_exists(listing_candidate.store_id, listing_candidate.source_url):
                continue

            detail_candidate = _fetch_detail_candidate(
                listing_candidate=listing_candidate,
                source_listing_url=source_listing_url,
                browser_fetcher=browser_fetcher if use_browser_fetch else None,
            )
            apply_item_classification(detail_candidate)
            upsert_result = repository.upsert_item_candidate(detail_candidate)
            if item_processor is not None and getattr(upsert_result, "should_process", False):
                item_processor.process_candidate(int(getattr(upsert_result, "candidate_id")), detail_candidate)
            records.append(detail_candidate)
        return records
    finally:
        if browser_session is not None:
            browser_session.__exit__(None, None, None)


def update_confirmed_store_item_details(
    repository: ItemCandidateRepository,
    limit: int | None = None,
    browser_fetch_enabled: bool = False,
    browser_fetcher: Callable[[str], FetchResult | None] | None = None,
) -> list[DiscoveryItemCandidateRecord]:
    browser_session = None
    if browser_fetch_enabled and browser_fetcher is None:
        from ludora.browser_fetch import BrowserTextFetcher

        browser_session = BrowserTextFetcher()
        browser_fetcher = browser_session.__enter__().fetch

    try:
        records: list[DiscoveryItemCandidateRecord] = []
        for existing_record in repository.list_confirmed_boardgame_item_candidates(limit=limit):
            refreshed_record = _fetch_detail_candidate(
                listing_candidate=existing_record,
                source_listing_url=existing_record.source_listing_url or existing_record.source_url,
                browser_fetcher=browser_fetcher if browser_fetch_enabled else None,
            )
            _preserve_confirmed_item_state(refreshed_record, existing_record)
            repository.upsert_item_candidate(refreshed_record)
            records.append(refreshed_record)
        return records
    finally:
        if browser_session is not None:
            browser_session.__exit__(None, None, None)


def _fetch_detail_candidate(
    listing_candidate: DiscoveryItemCandidateRecord,
    source_listing_url: str,
    browser_fetcher: Callable[[str], FetchResult | None] | None = None,
) -> DiscoveryItemCandidateRecord:
    fetched_detail = fetch_html(listing_candidate.source_url)
    if fetched_detail is not None and _looks_like_site_protection_challenge(fetched_detail.text):
        fetched_detail = None
    if fetched_detail is None and browser_fetcher is not None:
        fetched_detail = browser_fetcher(listing_candidate.source_url)
        if fetched_detail is not None and _looks_like_site_protection_challenge(fetched_detail.text):
            fetched_detail = None
    if fetched_detail is None:
        listing_candidate.source_listing_url = source_listing_url
        return listing_candidate

    detail_candidate = extract_product_detail_candidate(
        html=fetched_detail.text,
        product_url=fetched_detail.url,
        store_id=listing_candidate.store_id,
        source_listing_url=source_listing_url,
    )
    if detail_candidate is None:
        listing_candidate.source_listing_url = source_listing_url
        return listing_candidate

    if not detail_candidate.raw_price:
        detail_candidate.raw_price = listing_candidate.raw_price
    if not detail_candidate.price:
        detail_candidate.price = listing_candidate.price
        detail_candidate.price_source = listing_candidate.price_source
    if detail_candidate.availability == "unknown":
        detail_candidate.availability = listing_candidate.availability
        detail_candidate.availability_source = listing_candidate.availability_source
    return detail_candidate


def _title_from_url(product_url: str) -> str:
    path = urlparse(product_url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    return " ".join(part for part in slug.replace("-", " ").split() if part)


def _preserve_confirmed_item_state(
    refreshed_record: DiscoveryItemCandidateRecord,
    existing_record: DiscoveryItemCandidateRecord,
) -> None:
    refreshed_record.store_id = existing_record.store_id
    refreshed_record.source_url = existing_record.source_url
    refreshed_record.item_id = existing_record.item_id
    refreshed_record.listing_status = existing_record.listing_status
    refreshed_record.is_boardgame = True
    refreshed_record.is_boardgame_confirmed = True
    refreshed_record.category_confidence = existing_record.category_confidence
    refreshed_record.classification_reasons = list(existing_record.classification_reasons)
