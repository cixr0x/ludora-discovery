import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ludora.collector import CollectionSummary
from ludora.operations import (
    ItemDiscoveryRunResult,
    OperationAlreadyRunning,
    StoreDiscoveryRunManager,
    StoreDiscoveryRunResult,
    run_item_discovery,
    run_store_discovery,
)


class StoreDiscoveryOperationsTests(unittest.TestCase):
    def test_run_store_discovery_uses_existing_collector_and_closes_database(self):
        connection = Mock()
        repository = Mock()
        summary = CollectionSummary(
            records=[object(), object()],
            csv_path=None,
            json_path=None,
            audit_csv_path=None,
            audit_json_path=None,
            searched_queries=4,
            candidate_domains=7,
        )

        with patch("ludora.operations.resolve_brave_api_key", return_value="brave-key") as resolve_key, patch(
            "ludora.operations.resolve_database_url", return_value="postgresql://ludora"
        ) as resolve_database_url, patch(
            "ludora.operations.connect_database", return_value=connection
        ) as connect_database, patch(
            "ludora.operations.DiscoveryRepository", return_value=repository
        ), patch(
            "ludora.operations.collect_stores", return_value=summary
        ) as collect_stores:
            result = run_store_discovery(env_file="custom.env")

        resolve_key.assert_called_once()
        self.assertEqual(resolve_key.call_args.kwargs["dotenv_path"], "custom.env")
        resolve_database_url.assert_called_once()
        self.assertEqual(resolve_database_url.call_args.kwargs["dotenv_path"], "custom.env")
        connect_database.assert_called_once_with("postgresql://ludora")
        collect_stores.assert_called_once()
        self.assertEqual(collect_stores.call_args.kwargs["api_key"], "brave-key")
        self.assertIs(collect_stores.call_args.kwargs["discovery_repository"], repository)
        self.assertFalse(collect_stores.call_args.kwargs["export_files"])
        connection.close.assert_called_once_with()
        self.assertEqual(result.searched_queries, 4)
        self.assertEqual(result.candidate_domains, 7)
        self.assertEqual(result.accepted_stores, 2)

    def test_run_store_discovery_requires_brave_key_and_database_url(self):
        with patch("ludora.operations.resolve_brave_api_key", return_value=""), patch(
            "ludora.operations.resolve_database_url", return_value="postgresql://ludora"
        ):
            with self.assertRaisesRegex(RuntimeError, "Missing Brave API key"):
                run_store_discovery()

        with patch("ludora.operations.resolve_brave_api_key", return_value="brave-key"), patch(
            "ludora.operations.resolve_database_url", return_value=""
        ):
            with self.assertRaisesRegex(RuntimeError, "Missing database URL"):
                run_store_discovery()

    def test_run_item_discovery_crawls_one_store_and_closes_database(self):
        connection = Mock()
        repository = Mock()
        records = [object(), object(), object()]

        bgg_client = object()
        bgg_importer = object()
        item_processor = object()

        with patch("ludora.operations.resolve_database_url", return_value="postgresql://ludora") as resolve_database_url, patch(
            "ludora.operations.resolve_browser_fetch_enabled", return_value=True
        ) as resolve_browser_fetch_enabled, patch(
            "ludora.operations.resolve_bgg_api_token", return_value="bgg-token"
        ) as resolve_bgg_api_token, patch(
            "ludora.operations.resolve_bgg_api_base_url", return_value="https://bgg.test/xmlapi2"
        ) as resolve_bgg_api_base_url, patch(
            "ludora.operations.connect_database", return_value=connection
        ) as connect_database, patch(
            "ludora.operations.DiscoveryRepository", return_value=repository
        ), patch(
            "ludora.operations.BggClient", return_value=bgg_client
        ) as bgg_client_factory, patch(
            "ludora.operations.BggItemImporter", return_value=bgg_importer
        ) as bgg_importer_factory, patch(
            "ludora.operations.ItemCandidateProcessor", return_value=item_processor
        ) as item_processor_factory, patch(
            "ludora.operations.collect_store_inventory", return_value=records
        ) as collect_store_inventory:
            result = run_item_discovery(store_id=12, website_url="https://example.mx/", env_file="custom.env")

        resolve_database_url.assert_called_once()
        self.assertEqual(resolve_database_url.call_args.kwargs["dotenv_path"], "custom.env")
        resolve_browser_fetch_enabled.assert_called_once()
        self.assertEqual(resolve_browser_fetch_enabled.call_args.kwargs["dotenv_path"], "custom.env")
        resolve_bgg_api_token.assert_called_once()
        self.assertEqual(resolve_bgg_api_token.call_args.kwargs["dotenv_path"], "custom.env")
        resolve_bgg_api_base_url.assert_called_once()
        self.assertEqual(resolve_bgg_api_base_url.call_args.kwargs["dotenv_path"], "custom.env")
        connect_database.assert_called_once_with("postgresql://ludora")
        bgg_client_factory.assert_called_once_with(api_token="bgg-token", base_url="https://bgg.test/xmlapi2")
        bgg_importer_factory.assert_called_once_with(connection, bgg_client=bgg_client)
        item_processor_factory.assert_called_once_with(repository, bgg_client=bgg_client, bgg_importer=bgg_importer)
        collect_store_inventory.assert_called_once_with(
            "https://example.mx/",
            12,
            repository,
            browser_sitemap_fetch_enabled=True,
            item_processor=item_processor,
        )
        connection.close.assert_called_once_with()
        self.assertEqual(result.store_id, 12)
        self.assertEqual(result.website_url, "https://example.mx/")
        self.assertEqual(result.item_candidates, 3)

    def test_manager_records_successful_run_result(self):
        manager = StoreDiscoveryRunManager(
            runner=lambda: StoreDiscoveryRunResult(
                searched_queries=1,
                candidate_domains=2,
                accepted_stores=3,
            ),
            background=False,
        )

        run = manager.start_store_discovery()

        self.assertEqual(run.status, "completed")
        self.assertEqual(run.result.accepted_stores, 3)
        self.assertIsNone(run.error)
        self.assertEqual(manager.get_latest_run().id, run.id)

    def test_manager_records_successful_item_discovery_run_result(self):
        manager = StoreDiscoveryRunManager(
            runner=lambda: StoreDiscoveryRunResult(0, 0, 0),
            item_runner=lambda store_id, website_url: ItemDiscoveryRunResult(
                store_id=store_id,
                website_url=website_url,
                item_candidates=4,
            ),
            background=False,
        )

        run = manager.start_item_discovery(12, "https://example.mx/")

        self.assertEqual(run.status, "completed")
        self.assertEqual(run.run_type, "item_discovery")
        self.assertEqual(run.result.item_candidates, 4)
        self.assertEqual(run.result.store_id, 12)
        self.assertEqual(manager.get_latest_run().id, run.id)

    def test_manager_passes_env_file_to_default_runners(self):
        with patch(
            "ludora.operations.run_store_discovery",
            return_value=StoreDiscoveryRunResult(1, 2, 3),
        ) as store_runner, patch(
            "ludora.operations.run_item_discovery",
            return_value=ItemDiscoveryRunResult(12, "https://example.mx/", 4),
        ) as item_runner:
            manager = StoreDiscoveryRunManager(env_file="custom.env", background=False)

            manager.start_store_discovery()
            manager.start_item_discovery(12, "https://example.mx/")

        store_runner.assert_called_once_with(env_file="custom.env")
        item_runner.assert_called_once_with(store_id=12, website_url="https://example.mx/", env_file="custom.env")

    def test_manager_records_failed_run_error(self):
        manager = StoreDiscoveryRunManager(
            runner=lambda: (_ for _ in ()).throw(RuntimeError("collector failed")),
            background=False,
        )

        run = manager.start_store_discovery()

        self.assertEqual(run.status, "failed")
        self.assertEqual(run.error, "collector failed")
        self.assertIsNone(run.result)

    def test_manager_rejects_second_active_run(self):
        release_runner = threading.Event()

        def blocking_runner():
            release_runner.wait(timeout=2)
            return StoreDiscoveryRunResult(
                searched_queries=0,
                candidate_domains=0,
                accepted_stores=0,
            )

        manager = StoreDiscoveryRunManager(
            runner=blocking_runner,
            background=True,
        )
        run = manager.start_store_discovery()

        try:
            self.assertEqual(run.status, "running")
            with self.assertRaises(OperationAlreadyRunning):
                manager.start_store_discovery()
        finally:
            release_runner.set()


if __name__ == "__main__":
    unittest.main()
