import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ludora.bgg import BggSearchResult, BggThing
from ludora.item_processing import ItemCandidateProcessor, LocalItemMatch, score_bgg_thing, score_local_item
from ludora.models import DiscoveryItemCandidateRecord


class FakeRepository:
    def __init__(self):
        self.local_matches = []
        self.bgg_search_cache = None
        self.bgg_search_cache_queries = []
        self.bgg_search_cache_writes = []
        self.linked_store_items = []
        self.not_boardgame_ids = []
        self.match_not_found_ids = []
        self.processing_errors = []

    def find_local_item_matches(self, title):
        return self.local_matches

    def get_bgg_search_cache(self, query):
        self.bgg_search_cache_queries.append(query)
        return self.bgg_search_cache

    def upsert_bgg_search_cache(self, query, results):
        self.bgg_search_cache_writes.append((query, results))

    def link_item_to_store_item(self, candidate_id, record, match):
        self.linked_store_items.append((candidate_id, record, match))

    def mark_item_candidate_not_boardgame(self, candidate_id, reasons):
        self.not_boardgame_ids.append((candidate_id, reasons))

    def mark_item_candidate_match_not_found(self, candidate_id, reasons):
        self.match_not_found_ids.append((candidate_id, reasons))

    def mark_item_candidate_processing_error(self, candidate_id, error):
        self.processing_errors.append((candidate_id, error))


class FakeBggClient:
    def __init__(self, search_results=None, things=None):
        self.search_results = list(search_results or [])
        self.things = things or {}
        self.search_queries = []

    def search(self, query):
        self.search_queries.append(query)
        return self.search_results

    def fetch_thing(self, bgg_id):
        return self.things.get(bgg_id)


class FakeBggImporter:
    def __init__(self, item_id):
        self.item_id = item_id
        self.imported = []

    def import_thing(self, thing, raw_xml=""):
        self.imported.append((thing, raw_xml))
        return self.item_id


class ItemProcessingTests(unittest.TestCase):
    def test_scores_exact_local_alias_as_high_confidence_match(self):
        result = score_local_item(
            DiscoveryItemCandidateRecord(
                store_id=12,
                source_url="https://example.mx/products/colonizadores",
                title="Los Colonos de Catan",
                item_type="base_game",
            ),
            LocalItemMatch(
                item_id=7,
                name="Catan",
                normalized_name="catan",
                item_type="base_game",
                bgg_id=13,
                aliases=["Los Colonos de Catan"],
            ),
        )

        self.assertGreaterEqual(result.score, 0.9)
        self.assertIn("exact local alias match", result.reasons)

    def test_scores_catan_plus_below_auto_match_threshold_for_catan(self):
        result = score_bgg_thing(
            DiscoveryItemCandidateRecord(
                store_id=12,
                source_url="https://example.mx/products/catan-plus",
                title="Catan Plus",
                item_type="base_game",
            ),
            BggThing(
                bgg_id=13,
                item_type="boardgame",
                name="Catan",
                min_players=3,
                max_players=4,
            ),
        )

        self.assertLess(result.score, 0.9)
        self.assertIn("meaningful extra title token: plus", result.reasons)

    def test_ignores_non_boardgame_without_matching(self):
        repository = FakeRepository()
        processor = ItemCandidateProcessor(repository)
        record = DiscoveryItemCandidateRecord(
            store_id=12,
            source_url="https://example.mx/products/sleeves",
            title="Card Sleeves",
            is_boardgame=False,
            classification_reasons=["non-boardgame terms found: sleeves"],
        )

        processor.process_candidate(101, record)

        self.assertEqual(repository.not_boardgame_ids, [])
        self.assertEqual(repository.linked_store_items, [])

    def test_leaves_uncertain_candidates_unmatched_for_manual_review(self):
        repository = FakeRepository()
        repository.local_matches = [
            LocalItemMatch(
                item_id=7,
                name="Mystery Product",
                normalized_name="mystery product",
                item_type="base_game",
                bgg_id=13,
                aliases=[],
            )
        ]
        bgg_client = FakeBggClient(search_results=[BggSearchResult(bgg_id=13, item_type="boardgame", name="Mystery Product")])
        processor = ItemCandidateProcessor(repository, bgg_client=bgg_client, bgg_importer=FakeBggImporter(item_id=7))
        record = DiscoveryItemCandidateRecord(
            store_id=12,
            source_url="https://example.mx/products/mystery-product",
            title="Mystery Product",
            item_type="base_game",
            is_boardgame=False,
            classification_reasons=["insufficient classification evidence"],
        )

        processor.process_candidate(102, record)

        self.assertEqual(repository.linked_store_items, [])
        self.assertEqual(repository.match_not_found_ids, [])
        self.assertEqual(repository.not_boardgame_ids, [])
        self.assertEqual(repository.processing_errors, [])
        self.assertEqual(bgg_client.search_queries, [])

    def test_links_store_item_from_high_confidence_local_match(self):
        repository = FakeRepository()
        repository.local_matches = [
            LocalItemMatch(
                item_id=7,
                name="Catan",
                normalized_name="catan",
                item_type="base_game",
                bgg_id=13,
                aliases=[],
            )
        ]
        processor = ItemCandidateProcessor(repository)
        record = DiscoveryItemCandidateRecord(
            store_id=12,
            source_url="https://example.mx/products/catan",
            title="Catan",
            item_type="base_game",
            is_boardgame=True,
        )

        processor.process_candidate(102, record)

        self.assertEqual(len(repository.linked_store_items), 1)
        _, _, match = repository.linked_store_items[0]
        self.assertEqual(match.item_id, 7)
        self.assertEqual(match.source, "LOCAL")
        self.assertGreaterEqual(match.score, 0.9)

    def test_imports_bgg_match_and_links_store_item_when_no_local_match_exists(self):
        thing = BggThing(
            bgg_id=377061,
            item_type="boardgame",
            name="Coffee Rush",
            alternate_names=["Cafe Barista"],
            min_players=2,
            max_players=4,
        )
        repository = FakeRepository()
        bgg_client = FakeBggClient(
            search_results=[BggSearchResult(bgg_id=377061, item_type="boardgame", name="Coffee Rush")],
            things={377061: (thing, "<items />")},
        )
        importer = FakeBggImporter(item_id=77)
        processor = ItemCandidateProcessor(repository, bgg_client=bgg_client, bgg_importer=importer)
        record = DiscoveryItemCandidateRecord(
            store_id=12,
            source_url="https://example.mx/products/cafe-barista",
            title="Cafe Barista",
            item_type="base_game",
            is_boardgame=True,
        )

        processor.process_candidate(103, record)

        self.assertEqual(bgg_client.search_queries, ["Cafe Barista"])
        self.assertEqual(importer.imported, [(thing, "<items />")])
        _, _, match = repository.linked_store_items[0]
        self.assertEqual(match.item_id, 77)
        self.assertEqual(match.source, "BGG")
        self.assertEqual(match.bgg_id, 377061)

    def test_uses_cached_bgg_search_results_before_remote_search(self):
        thing = BggThing(
            bgg_id=377061,
            item_type="boardgame",
            name="Coffee Rush",
            alternate_names=["Cafe Barista"],
            min_players=2,
            max_players=4,
        )
        repository = FakeRepository()
        repository.bgg_search_cache = [BggSearchResult(bgg_id=377061, item_type="boardgame", name="Coffee Rush")]
        bgg_client = FakeBggClient(
            search_results=[],
            things={377061: (thing, "<items />")},
        )
        importer = FakeBggImporter(item_id=77)
        processor = ItemCandidateProcessor(repository, bgg_client=bgg_client, bgg_importer=importer)
        record = DiscoveryItemCandidateRecord(
            store_id=12,
            source_url="https://example.mx/products/cafe-barista",
            title="Cafe Barista",
            item_type="base_game",
            is_boardgame=True,
        )

        processor.process_candidate(103, record)

        self.assertEqual(repository.bgg_search_cache_queries, ["Cafe Barista"])
        self.assertEqual(bgg_client.search_queries, [])
        self.assertEqual(repository.bgg_search_cache_writes, [])
        _, _, match = repository.linked_store_items[0]
        self.assertEqual(match.source, "BGG")
        self.assertEqual(match.bgg_id, 377061)

    def test_stores_bgg_search_results_when_cache_misses(self):
        search_result = BggSearchResult(bgg_id=377061, item_type="boardgame", name="Coffee Rush")
        thing = BggThing(
            bgg_id=377061,
            item_type="boardgame",
            name="Coffee Rush",
            alternate_names=["Cafe Barista"],
            min_players=2,
            max_players=4,
        )
        repository = FakeRepository()
        bgg_client = FakeBggClient(
            search_results=[search_result],
            things={377061: (thing, "<items />")},
        )
        importer = FakeBggImporter(item_id=77)
        processor = ItemCandidateProcessor(repository, bgg_client=bgg_client, bgg_importer=importer)
        record = DiscoveryItemCandidateRecord(
            store_id=12,
            source_url="https://example.mx/products/cafe-barista",
            title="Cafe Barista",
            item_type="base_game",
            is_boardgame=True,
        )

        processor.process_candidate(103, record)

        self.assertEqual(repository.bgg_search_cache_queries, ["Cafe Barista"])
        self.assertEqual(bgg_client.search_queries, ["Cafe Barista"])
        self.assertEqual(repository.bgg_search_cache_writes, [("Cafe Barista", [search_result])])

    def test_marks_match_not_found_when_no_candidate_reaches_threshold(self):
        repository = FakeRepository()
        bgg_client = FakeBggClient(
            search_results=[BggSearchResult(bgg_id=13, item_type="boardgame", name="Catan")],
            things={
                13: (
                    BggThing(bgg_id=13, item_type="boardgame", name="Catan"),
                    "<items />",
                )
            },
        )
        processor = ItemCandidateProcessor(repository, bgg_client=bgg_client, bgg_importer=FakeBggImporter(item_id=7))
        record = DiscoveryItemCandidateRecord(
            store_id=12,
            source_url="https://example.mx/products/catan-plus",
            title="Catan Plus",
            item_type="base_game",
            is_boardgame=True,
        )

        processor.process_candidate(104, record)

        self.assertEqual(repository.linked_store_items, [])
        self.assertEqual(repository.match_not_found_ids, [(104, ["no match above threshold"])])


if __name__ == "__main__":
    unittest.main()
