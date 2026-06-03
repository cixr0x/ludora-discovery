import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ludora.bgg import BggSearchResult
from ludora.database import DiscoveryRepository
from ludora.item_processing import CandidateOfferMatch
from ludora.models import DiscoveryItemCandidateRecord, StoreRecord


class FakeCursor:
    def __init__(self, fetchone_rows=None, fetchall_rows=None):
        self.executions = []
        self.fetchone_rows = list(fetchone_rows or [])
        self.fetchall_rows = list(fetchall_rows or [])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        self.executions.append((sql, params))

    def fetchone(self):
        if self.fetchone_rows:
            return self.fetchone_rows.pop(0)
        return None

    def fetchall(self):
        if self.fetchall_rows:
            return self.fetchall_rows.pop(0)
        return []


class FakeConnection:
    def __init__(self, fetchone_rows=None, fetchall_rows=None):
        self.cursor_instance = FakeCursor(fetchone_rows=fetchone_rows, fetchall_rows=fetchall_rows)
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1


class DatabaseRepositoryTests(unittest.TestCase):
    def test_upsert_store_candidate_writes_dirty_store_record(self):
        connection = FakeConnection()
        repository = DiscoveryRepository(connection)
        record = StoreRecord(
            store_name="Example",
            canonical_domain="example.mx",
            website_url="https://example.mx/",
            instagram_url="https://instagram.com/example",
            facebook_url="https://facebook.com/example",
            city="Ciudad de Mexico",
            state="CDMX",
            country="Mexico",
            store_logo="https://example.mx/logo.png",
            status="ACCEPTED",
            confidence=0.91,
            source_queries=["juegos de mesa mexico"],
            evidence=["boardgame", "online_store", "mexico"],
        )

        repository.upsert_store_candidate(record)

        sql, params = connection.cursor_instance.executions[0]
        normalized_sql = sql.casefold()
        self.assertIn("insert into discovery_store_candidates", normalized_sql)
        for column_name in StoreRecord.output_fields():
            self.assertIn(column_name, normalized_sql)
        for audit_column_name in ["accepted", "reasons", "title", "description"]:
            self.assertNotIn(audit_column_name, normalized_sql)
        self.assertEqual(params[0], "Example")
        self.assertEqual(params[1], "example.mx")
        self.assertEqual(params[2], "https://example.mx/")
        self.assertEqual(params[3], "https://instagram.com/example")
        self.assertEqual(params[4], "https://facebook.com/example")
        self.assertEqual(params[5], "Ciudad de Mexico")
        self.assertEqual(params[6], "CDMX")
        self.assertEqual(params[7], "Mexico")
        self.assertEqual(params[8], "https://example.mx/logo.png")
        self.assertEqual(params[9], "ACCEPTED")
        self.assertEqual(params[10], 0.91)
        self.assertEqual(json.loads(params[11]), ["juegos de mesa mexico"])
        self.assertEqual(json.loads(params[12]), ["boardgame", "online_store", "mexico"])
        self.assertEqual(connection.commits, 1)

    def test_upsert_item_candidate_writes_dirty_item_record(self):
        connection = FakeConnection()
        repository = DiscoveryRepository(connection)
        record = DiscoveryItemCandidateRecord(
            store_id=12,
            source_url="https://example.mx/products/catan",
            source_listing_url="https://example.mx/collections/juegos",
            title="Catan",
            publisher="Devir",
            description="Juego base",
            item_id=None,
            item_type="base_game",
            min_players=3,
            max_players=4,
            min_minutes=60,
            max_minutes=90,
            min_age=10,
            language="es",
            language_source="product_highlights",
            language_evidence="Highlights: 10+ 3-4 jugadores 60-90 min Español",
            image_url="https://example.mx/catan.jpg",
            raw_price="$899",
            price="899.00",
            price_source="json_ld_offer",
            currency="MXN",
            availability="available",
            availability_source="json_ld_offer",
            store_sku="CATAN-ES",
            raw_payload={"json_ld": {"name": "Catan"}},
            is_boardgame=True,
            is_boardgame_confirmed=False,
            category_confidence=0.87,
            classification_reasons=["player count found", "boardgame category found"],
        )

        repository.upsert_item_candidate(record)

        sql, params = connection.cursor_instance.executions[1]
        self.assertEqual(sql.count("%s"), len(params))
        self.assertIn("insert into store_items", sql.casefold())
        self.assertNotIn("on conflict (store_id, source_url)", sql.casefold())
        self.assertNotIn("title = excluded.title", sql.casefold())
        self.assertNotIn("on conflict (store_id, source_url, title)", sql.casefold())
        self.assertNotIn("discovery_listing_candidates", sql.casefold())
        for column_name in [
            "source_listing_url",
            "image_url",
            "item_type",
            "min_minutes",
            "max_minutes",
            "min_age",
            "currency",
            "store_sku",
            "raw_payload",
            "price_source",
            "availability_source",
            "is_boardgame",
            "is_boardgame_confirmed",
            "category_confidence",
            "classification_reasons",
            "language_source",
            "language_evidence",
            "last_seen_at",
        ]:
            self.assertIn(column_name, sql.casefold())
        self.assertEqual(params[0], 12)
        self.assertEqual(params[2], "https://example.mx/collections/juegos")
        self.assertEqual(params[3], "Catan")
        self.assertEqual(params[4], "Devir")
        self.assertEqual(params[7], "base_game")
        self.assertEqual(params[13], "es")
        self.assertEqual(params[14], "product_highlights")
        self.assertEqual(params[15], "Highlights: 10+ 3-4 jugadores 60-90 min Español")
        self.assertEqual(params[16], "https://example.mx/catan.jpg")
        self.assertEqual(params[18], "$899")
        self.assertEqual(params[19], "899.00")
        self.assertEqual(params[20], "json_ld_offer")
        self.assertEqual(params[21], "MXN")
        self.assertEqual(params[23], "json_ld_offer")
        self.assertEqual(params[24], "CATAN-ES")
        self.assertEqual(json.loads(params[25]), {"json_ld": {"name": "Catan"}})
        self.assertEqual(params[26], True)
        self.assertEqual(params[27], False)
        self.assertEqual(params[28], 0.87)
        self.assertEqual(json.loads(params[29]), ["player count found", "boardgame category found"])
        self.assertEqual(connection.commits, 1)

    def test_upsert_terminal_item_candidate_only_updates_last_seen(self):
        connection = FakeConnection(fetchone_rows=[(55, "MATCH_NOT_FOUND", None)])
        repository = DiscoveryRepository(connection)
        record = DiscoveryItemCandidateRecord(
            store_id=12,
            source_url="https://example.mx/products/unknown",
            title="Unknown Product",
            raw_price="$100",
            price="100.00",
            availability="available",
        )

        result = repository.upsert_item_candidate(record)

        self.assertEqual(result.candidate_id, 55)
        self.assertFalse(result.should_process)
        self.assertEqual(len(connection.cursor_instance.executions), 2)
        sql, params = connection.cursor_instance.executions[1]
        normalized_sql = sql.casefold()
        self.assertIn("update store_items", normalized_sql)
        self.assertIn("last_seen_at = now()", normalized_sql)
        self.assertNotIn("raw_price", normalized_sql)
        self.assertEqual(params, (55,))

    def test_upsert_listed_store_item_refreshes_store_item_only(self):
        connection = FakeConnection(fetchone_rows=[(56, "LISTED", 7)])
        repository = DiscoveryRepository(connection)
        record = DiscoveryItemCandidateRecord(
            store_id=12,
            source_url="https://example.mx/products/catan",
            source_listing_url="https://example.mx/collections/juegos",
            title="Catan",
            language="es",
            publisher="Devir",
            raw_price="$899",
            price="899.00",
            availability="available",
        )

        result = repository.upsert_item_candidate(record)

        self.assertEqual(result.candidate_id, 56)
        self.assertFalse(result.should_process)
        self.assertEqual(result.item_id, 7)
        self.assertEqual(len(connection.cursor_instance.executions), 2)
        candidate_sql, candidate_params = connection.cursor_instance.executions[1]
        self.assertIn("update store_items", candidate_sql.casefold())
        self.assertIn("raw_price = %s", candidate_sql.casefold())
        self.assertEqual(candidate_params[17], "LISTED")

    def test_find_local_item_matches_returns_aliases_for_exact_candidates(self):
        connection = FakeConnection(
            fetchall_rows=[
                [
                    (
                        7,
                        "Catan",
                        "catan",
                        "base_game",
                        13,
                        '["Los Colonos de Catan"]',
                    )
                ]
            ]
        )
        repository = DiscoveryRepository(connection)

        matches = repository.find_local_item_matches("Los Colonos de Catan")

        sql, params = connection.cursor_instance.executions[0]
        self.assertIn("from items i", sql.casefold())
        self.assertIn("left join item_aliases", sql.casefold())
        self.assertEqual(params, ("los colonos de catan", "los colonos de catan"))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].item_id, 7)
        self.assertEqual(matches[0].aliases, ["Los Colonos de Catan"])

    def test_get_bgg_search_cache_returns_cached_results(self):
        connection = FakeConnection(
            fetchone_rows=[
                (91,),
            ],
            fetchall_rows=[
                [
                    (377061, "Coffee Rush", "boardgame", 2023),
                ],
            ]
        )
        repository = DiscoveryRepository(connection)

        results = repository.get_bgg_search_cache("Cafe Barista")

        query_sql, query_params = connection.cursor_instance.executions[0]
        result_sql, result_params = connection.cursor_instance.executions[1]
        self.assertIn("from bgg_search_queries", query_sql.casefold())
        self.assertIn("normalized_query = %s", query_sql.casefold())
        self.assertIn("search_type = %s", query_sql.casefold())
        self.assertEqual(query_params, ("cafe barista", "boardgame,boardgameexpansion"))
        self.assertIn("from bgg_search_query_results", result_sql.casefold())
        self.assertIn("join bgg_search_cache", result_sql.casefold())
        self.assertEqual(result_params, (91,))
        self.assertEqual(
            results,
            [BggSearchResult(bgg_id=377061, name="Coffee Rush", item_type="boardgame", year_published=2023)],
        )

    def test_upsert_bgg_search_cache_writes_query_results(self):
        connection = FakeConnection(fetchone_rows=[(91,), (92,)])
        repository = DiscoveryRepository(connection)
        results = [BggSearchResult(bgg_id=377061, name="Coffee Rush", item_type="boardgame", year_published=2023)]

        repository.upsert_bgg_search_cache("Cafe Barista", results)

        query_sql, query_params = connection.cursor_instance.executions[0]
        delete_sql, delete_params = connection.cursor_instance.executions[1]
        cache_sql, cache_params = connection.cursor_instance.executions[2]
        mapping_sql, mapping_params = connection.cursor_instance.executions[3]
        self.assertIn("insert into bgg_search_queries", query_sql.casefold())
        self.assertIn("on conflict (normalized_query, search_type)", query_sql.casefold())
        self.assertEqual(query_params[0], "Cafe Barista")
        self.assertEqual(query_params[1], "cafe barista")
        self.assertEqual(query_params[2], "boardgame,boardgameexpansion")
        self.assertEqual(query_params[3], 1)
        self.assertIn("delete from bgg_search_query_results", delete_sql.casefold())
        self.assertEqual(delete_params, (91,))
        self.assertIn("insert into bgg_search_cache", cache_sql.casefold())
        self.assertIn("on conflict (bgg_id)", cache_sql.casefold())
        self.assertEqual(cache_params[0], 377061)
        self.assertEqual(cache_params[1], "Coffee Rush")
        self.assertEqual(cache_params[2], "boardgame")
        self.assertEqual(cache_params[3], 2023)
        self.assertEqual(json.loads(cache_params[4]), {"bggId": 377061, "name": "Coffee Rush", "type": "boardgame", "yearPublished": 2023})
        self.assertIn("insert into bgg_search_query_results", mapping_sql.casefold())
        self.assertEqual(mapping_params, (91, 92, 0))
        self.assertEqual(connection.commits, 1)

    def test_link_item_to_store_item_marks_store_item_listed(self):
        connection = FakeConnection()
        repository = DiscoveryRepository(connection)
        record = DiscoveryItemCandidateRecord(
            store_id=12,
            source_url="https://example.mx/products/catan",
            title="Catan",
            publisher="Devir",
            language="es",
            price="899.00",
            availability="available",
        )
        match = CandidateOfferMatch(
            item_id=7,
            source="LOCAL",
            matched_name="Catan",
            score=0.94,
            reasons=["exact local item name match"],
            bgg_id=13,
            payload={"item": {"id": 7}},
        )

        repository.link_item_to_store_item(56, record, match)

        candidate_sql, candidate_params = connection.cursor_instance.executions[0]
        self.assertIn("update store_items", candidate_sql.casefold())
        self.assertIn("status = 'LISTED'", candidate_sql)
        self.assertIn("is_boardgame_confirmed = true", candidate_sql.casefold())
        self.assertIn("match_source = %s", candidate_sql)
        self.assertEqual(candidate_params[0], 7)
        self.assertEqual(candidate_params[1], "LOCAL")
        self.assertEqual(candidate_params[-1], 56)
        self.assertEqual(connection.commits, 1)

    def test_marks_automated_terminal_candidate_statuses(self):
        connection = FakeConnection()
        repository = DiscoveryRepository(connection)

        repository.mark_item_candidate_not_boardgame(56, ["non-boardgame terms found: sleeves"])
        repository.mark_item_candidate_match_not_found(57, ["no match above threshold"])
        repository.mark_item_candidate_processing_error(58, "BGG client is not configured")

        status_sql, status_params = connection.cursor_instance.executions[0]
        missing_sql, missing_params = connection.cursor_instance.executions[1]
        error_sql, error_params = connection.cursor_instance.executions[2]
        self.assertIn("status = %s", status_sql.casefold())
        self.assertEqual(status_params[0], "NOT_BOARDGAME")
        self.assertEqual(status_params[-1], 56)
        self.assertEqual(missing_params[0], "MATCH_NOT_FOUND")
        self.assertEqual(missing_params[-1], 57)
        self.assertIn("processing_error = %s", error_sql.casefold())
        self.assertEqual(error_params, ("BGG client is not configured", 58))
        self.assertEqual(connection.commits, 3)


if __name__ == "__main__":
    unittest.main()
