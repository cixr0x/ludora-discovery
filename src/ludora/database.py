from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ludora.bgg import BggSearchResult
from ludora.item_import import normalize_title
from ludora.item_processing import CandidateOfferMatch, LocalItemMatch
from ludora.models import DiscoveryItemCandidateRecord, StoreRecord


def connect_database(database_url: str):
    import psycopg

    return psycopg.connect(database_url)


@dataclass(frozen=True)
class ItemCandidateUpsertResult:
    candidate_id: int
    status: str
    item_id: int | None
    should_process: bool


BGG_SEARCH_TYPE = "boardgame,boardgameexpansion"
TERMINAL_AUTOMATION_STATUSES = {"REJECTED", "NOT_BOARDGAME", "MATCH_NOT_FOUND"}


class DiscoveryRepository:
    def __init__(self, connection: Any):
        self.connection = connection

    def upsert_store_candidate(self, record: StoreRecord) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                insert into discovery_store_candidates (
                    store_name,
                    canonical_domain,
                    website_url,
                    instagram_url,
                    facebook_url,
                    city,
                    state,
                    country,
                    store_logo,
                    status,
                    confidence,
                    source_queries,
                    evidence,
                    last_seen_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, now())
                on conflict (canonical_domain) do update set
                    store_name = excluded.store_name,
                    website_url = excluded.website_url,
                    instagram_url = excluded.instagram_url,
                    facebook_url = excluded.facebook_url,
                    city = excluded.city,
                    state = excluded.state,
                    country = excluded.country,
                    store_logo = excluded.store_logo,
                    status = excluded.status,
                    confidence = excluded.confidence,
                    source_queries = excluded.source_queries,
                    evidence = excluded.evidence,
                    last_seen_at = now()
                """,
                (
                    record.store_name,
                    record.canonical_domain,
                    record.website_url,
                    record.instagram_url,
                    record.facebook_url,
                    record.city,
                    record.state,
                    record.country,
                    record.store_logo,
                    record.status,
                    record.confidence,
                    json.dumps(record.source_queries, ensure_ascii=False),
                    json.dumps(record.evidence, ensure_ascii=False),
                ),
            )
        self.connection.commit()

    def upsert_item_candidate(self, record: DiscoveryItemCandidateRecord) -> ItemCandidateUpsertResult:
        data = record.to_db_dict()
        with self.connection.cursor() as cursor:
            existing = self._find_item_candidate(cursor, record)
            if existing and existing[1] in TERMINAL_AUTOMATION_STATUSES:
                cursor.execute(
                    """
                    update store_items
                    set last_seen_at = now()
                    where id = %s
                    """,
                    (existing[0],),
                )
                result = ItemCandidateUpsertResult(
                    candidate_id=int(existing[0]),
                    status=str(existing[1]),
                    item_id=_optional_int(existing[2]),
                    should_process=False,
                )
            elif existing:
                data["status"] = str(existing[1])
                cursor.execute(
                    _update_item_candidate_sql(),
                    (
                        *self._item_candidate_write_params(data),
                        existing[0],
                    ),
                )
                item_id = _optional_int(existing[2])
                result = ItemCandidateUpsertResult(
                    candidate_id=int(existing[0]),
                    status=str(existing[1]),
                    item_id=item_id,
                    should_process=str(existing[1]) == "NEW",
                )
            else:
                cursor.execute(_insert_item_candidate_sql(), self._item_candidate_write_params(data))
                row = cursor.fetchone()
                result = ItemCandidateUpsertResult(
                    candidate_id=int(row[0]) if row else 0,
                    status=str(row[1]) if row else str(data["status"]),
                    item_id=_optional_int(row[2]) if row else _optional_int(data["item_id"]),
                    should_process=True,
                )
        self.connection.commit()
        return result

    def _find_item_candidate(self, cursor: Any, record: DiscoveryItemCandidateRecord):
        cursor.execute(
            """
            select id, status, item_id
            from store_items
            where store_id is not distinct from %s
              and source_url = %s
            """,
            (record.store_id, record.source_url),
        )
        return cursor.fetchone()

    def _item_candidate_write_params(self, data: dict[str, object]) -> tuple[object, ...]:
        return (
            data["store_id"],
            data["source_url"],
            data["source_listing_url"],
            data["title"],
            data["publisher"],
            data["description"],
            data["item_id"],
            data["item_type"],
            data["min_players"],
            data["max_players"],
            data["min_minutes"],
            data["max_minutes"],
            data["min_age"],
            data["language"],
            data["language_source"],
            data["language_evidence"],
            data["image_url"],
            data["status"],
            data["raw_price"],
            data["price"],
            data["price_source"],
            data["currency"],
            data["availability"],
            data["availability_source"],
            data["store_sku"],
            json.dumps(data["raw_payload"], ensure_ascii=False),
            data["is_boardgame"],
            data["is_boardgame_confirmed"],
            data["category_confidence"],
            json.dumps(data["classification_reasons"], ensure_ascii=False),
        )

    def find_local_item_matches(self, title: str) -> list[LocalItemMatch]:
        normalized_title = normalize_title(title)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                select
                    i.id,
                    i.canonical_name,
                    i.normalized_name,
                    i.item_type,
                    i.bgg_id,
                    coalesce(json_agg(distinct ia.alias) filter (where ia.alias is not null), '[]'::json) as aliases
                from items i
                left join item_aliases ia on ia.item_id = i.id
                where i.normalized_name = %s
                   or ia.normalized_alias = %s
                group by i.id, i.canonical_name, i.normalized_name, i.item_type, i.bgg_id
                order by i.canonical_name asc
                limit 20
                """,
                (normalized_title, normalized_title),
            )
            rows = cursor.fetchall()

        return [
            LocalItemMatch(
                item_id=int(row[0]),
                name=str(row[1] or ""),
                normalized_name=str(row[2] or ""),
                item_type=str(row[3] or "unknown"),
                bgg_id=_optional_int(row[4]),
                aliases=_json_string_list(row[5]),
            )
            for row in rows
        ]

    def get_bgg_search_cache(self, query: str, search_type: str = BGG_SEARCH_TYPE) -> list[BggSearchResult] | None:
        normalized_query = normalize_title(query)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                select id
                from bgg_search_queries
                where normalized_query = %s
                  and search_type = %s
                limit 1
                """,
                (normalized_query, search_type),
            )
            query_row = cursor.fetchone()
            if not query_row:
                return None

            cursor.execute(
                """
                select
                    c.bgg_id,
                    c.name,
                    c.item_type,
                    c.year_published
                from bgg_search_query_results qr
                join bgg_search_cache c on c.id = qr.cache_id
                where qr.query_id = %s
                order by qr.result_rank asc
                """,
                (query_row[0],),
            )
            rows = cursor.fetchall()

        return [
            BggSearchResult(
                bgg_id=int(row[0]),
                name=str(row[1] or ""),
                item_type=str(row[2] or ""),
                year_published=_optional_int(row[3]),
            )
            for row in rows
            if row[0] and row[1]
        ]

    def upsert_bgg_search_cache(
        self,
        query: str,
        results: list[BggSearchResult],
        search_type: str = BGG_SEARCH_TYPE,
    ) -> None:
        normalized_query = normalize_title(query)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                insert into bgg_search_queries (
                    query,
                    normalized_query,
                    search_type,
                    result_count,
                    fetched_at,
                    updated_at
                )
                values (%s, %s, %s, %s, now(), now())
                on conflict (normalized_query, search_type) do update set
                    query = excluded.query,
                    result_count = excluded.result_count,
                    fetched_at = excluded.fetched_at,
                    updated_at = now()
                returning id
                """,
                (
                    query,
                    normalized_query,
                    search_type,
                    len(results),
                ),
            )
            query_row = cursor.fetchone()
            query_id = int(query_row[0])
            cursor.execute(
                """
                delete from bgg_search_query_results
                where query_id = %s
                """,
                (query_id,),
            )
            for rank, result in enumerate(results):
                cursor.execute(
                    """
                    insert into bgg_search_cache (
                        bgg_id,
                        name,
                        item_type,
                        year_published,
                        result_json,
                        updated_at
                    )
                    values (%s, %s, %s, %s, %s::jsonb, now())
                    on conflict (bgg_id) do update set
                        name = excluded.name,
                        item_type = excluded.item_type,
                        year_published = excluded.year_published,
                        result_json = excluded.result_json,
                        updated_at = now()
                    returning id
                    """,
                    (
                        result.bgg_id,
                        result.name,
                        result.item_type,
                        result.year_published,
                        json.dumps(_bgg_search_result_payload(result), ensure_ascii=False),
                    ),
                )
                cache_row = cursor.fetchone()
                cursor.execute(
                    """
                    insert into bgg_search_query_results (
                        query_id,
                        cache_id,
                        result_rank
                    )
                    values (%s, %s, %s)
                    on conflict (query_id, cache_id) do update set
                        result_rank = excluded.result_rank
                    """,
                    (query_id, int(cache_row[0]), rank),
                )
        self.connection.commit()

    def link_item_to_store_item(
        self,
        candidate_id: int,
        record: DiscoveryItemCandidateRecord,
        match: CandidateOfferMatch,
    ) -> None:
        if record.store_id is None:
            raise ValueError("Cannot list a store item without a store id")

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                update store_items
                set item_id = %s,
                    is_boardgame = true,
                    is_boardgame_confirmed = true,
                    status = 'LISTED',
                    match_source = %s,
                    matched_bgg_id = %s,
                    matched_name = %s,
                    match_score = %s,
                    match_reasons = %s::jsonb,
                    match_payload = %s::jsonb,
                    matched_at = now(),
                    processed_at = now(),
                    processing_error = '',
                    last_updated = now()
                where id = %s
                """,
                (
                    match.item_id,
                    match.source,
                    match.bgg_id,
                    match.matched_name,
                    match.score,
                    json.dumps(match.reasons, ensure_ascii=False),
                    json.dumps(match.payload, ensure_ascii=False),
                    candidate_id,
                ),
            )

        self.connection.commit()

    def mark_item_candidate_not_boardgame(self, candidate_id: int, reasons: list[str]) -> None:
        self._mark_item_candidate_terminal_status(candidate_id, "NOT_BOARDGAME", reasons)

    def mark_item_candidate_match_not_found(self, candidate_id: int, reasons: list[str]) -> None:
        self._mark_item_candidate_terminal_status(candidate_id, "MATCH_NOT_FOUND", reasons)

    def mark_item_candidate_processing_error(self, candidate_id: int, error: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                update store_items
                set processing_error = %s,
                    processed_at = now(),
                    last_updated = now()
                where id = %s
                """,
                (error, candidate_id),
            )
        self.connection.commit()

    def _mark_item_candidate_terminal_status(self, candidate_id: int, status: str, reasons: list[str]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                update store_items
                set status = %s,
                    match_source = 'NONE',
                    match_reasons = %s::jsonb,
                    match_payload = '{}'::jsonb,
                    processed_at = now(),
                    processing_error = '',
                    last_updated = now()
                where id = %s
                """,
                (status, json.dumps(reasons, ensure_ascii=False), candidate_id),
            )
        self.connection.commit()


def _insert_item_candidate_sql() -> str:
    return """
    insert into store_items (
        store_id,
        source_url,
        source_listing_url,
        title,
        publisher,
        description,
        item_id,
        item_type,
        min_players,
        max_players,
        min_minutes,
        max_minutes,
        min_age,
        language,
        language_source,
        language_evidence,
        image_url,
        status,
        raw_price,
        price,
        price_source,
        currency,
        availability,
        availability_source,
        store_sku,
        raw_payload,
        is_boardgame,
        is_boardgame_confirmed,
        category_confidence,
        classification_reasons,
        last_seen_at,
        last_updated
    )
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, now(), now())
    returning id, status, item_id
    """


def _update_item_candidate_sql() -> str:
    return """
    update store_items
    set store_id = %s,
        source_url = %s,
        source_listing_url = %s,
        title = %s,
        publisher = %s,
        description = %s,
        item_id = %s,
        item_type = %s,
        min_players = %s,
        max_players = %s,
        min_minutes = %s,
        max_minutes = %s,
        min_age = %s,
        language = %s,
        language_source = %s,
        language_evidence = %s,
        image_url = %s,
        status = %s,
        raw_price = %s,
        price = %s,
        price_source = %s,
        currency = %s,
        availability = %s,
        availability_source = %s,
        store_sku = %s,
        raw_payload = %s::jsonb,
        is_boardgame = %s,
        is_boardgame_confirmed = %s,
        category_confidence = %s,
        classification_reasons = %s::jsonb,
        last_seen_at = now(),
        last_updated = now()
    where id = %s
    """


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _json_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
        return _json_string_list(parsed)
    return []


def _bgg_search_result_payload(result: BggSearchResult) -> dict[str, object]:
    return {
        "bggId": result.bgg_id,
        "name": result.name,
        "type": result.item_type,
        "yearPublished": result.year_published,
    }


def _bgg_search_results(value: object) -> list[BggSearchResult]:
    parsed = _json_value(value)
    if not isinstance(parsed, list):
        return []

    results: list[BggSearchResult] = []
    for item in parsed:
        if isinstance(item, dict):
            result = _bgg_search_result(item)
            if result:
                results.append(result)
    return results


def _bgg_search_result(value: dict[object, object]) -> BggSearchResult | None:
    bgg_id = _optional_int(value.get("bggId") or value.get("bgg_id"))
    name = str(value.get("name") or "").strip()
    item_type = str(value.get("type") or value.get("item_type") or "").strip()
    year_published = _optional_int(value.get("yearPublished") or value.get("year_published"))
    if not bgg_id or not name:
        return None
    return BggSearchResult(
        bgg_id=bgg_id,
        name=name,
        item_type=item_type,
        year_published=year_published,
    )


def _json_value(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value
