import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ludora.collector import CollectionSummary
from ludora.operations import (
    ItemEmbeddingRunResult,
    ItemDiscoveryRunResult,
    ItemUpdateRunResult,
    OperationAlreadyRunning,
    StoreDiscoveryRunManager,
    StoreDiscoveryRunResult,
    run_item_embeddings,
    run_item_discovery,
    run_item_update,
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

        item_processor = object()

        with patch("ludora.operations.resolve_database_url", return_value="postgresql://ludora") as resolve_database_url, patch(
            "ludora.operations.resolve_browser_fetch_enabled", return_value=True
        ) as resolve_browser_fetch_enabled, patch(
            "ludora.operations.resolve_admin_api_url", return_value="http://admin.test"
        ) as resolve_admin_api_url, patch(
            "ludora.operations.connect_database", return_value=connection
        ) as connect_database, patch(
            "ludora.operations.DiscoveryRepository", return_value=repository
        ), patch(
            "ludora.operations.AdminItemMatcher", return_value=item_processor
        ) as admin_item_matcher, patch(
            "ludora.operations.collect_store_inventory", return_value=records
        ) as collect_store_inventory:
            result = run_item_discovery(store_id=12, website_url="https://example.mx/", env_file="custom.env")

        resolve_database_url.assert_called_once()
        self.assertEqual(resolve_database_url.call_args.kwargs["dotenv_path"], "custom.env")
        resolve_browser_fetch_enabled.assert_called_once()
        self.assertEqual(resolve_browser_fetch_enabled.call_args.kwargs["dotenv_path"], "custom.env")
        resolve_admin_api_url.assert_called_once()
        self.assertEqual(resolve_admin_api_url.call_args.kwargs["dotenv_path"], "custom.env")
        connect_database.assert_called_once_with("postgresql://ludora")
        admin_item_matcher.assert_called_once_with("http://admin.test", repository)
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

    def test_run_item_update_refreshes_confirmed_boardgames_and_closes_database(self):
        connection = Mock()
        repository = Mock()
        records = [object(), object()]

        with patch("ludora.operations.resolve_database_url", return_value="postgresql://ludora") as resolve_database_url, patch(
            "ludora.operations.resolve_browser_fetch_enabled", return_value=True
        ) as resolve_browser_fetch_enabled, patch(
            "ludora.operations.connect_database", return_value=connection
        ) as connect_database, patch(
            "ludora.operations.DiscoveryRepository", return_value=repository
        ), patch(
            "ludora.operations.update_confirmed_store_items", return_value=records
        ) as update_confirmed_store_items:
            result = run_item_update(env_file="custom.env")

        resolve_database_url.assert_called_once()
        self.assertEqual(resolve_database_url.call_args.kwargs["dotenv_path"], "custom.env")
        resolve_browser_fetch_enabled.assert_called_once()
        self.assertEqual(resolve_browser_fetch_enabled.call_args.kwargs["dotenv_path"], "custom.env")
        connect_database.assert_called_once_with("postgresql://ludora")
        update_confirmed_store_items.assert_called_once_with(
            repository,
            browser_fetch_enabled=True,
        )
        connection.close.assert_called_once_with()
        self.assertEqual(result.updated_items, 2)

    def test_run_item_embeddings_embeds_selected_sources_and_closes_database(self):
        connection = Mock()
        repository = Mock()
        source = Mock(item_id=77)
        repository.list_item_search_embedding_sources.return_value = [source]
        client = Mock()
        client.create_embedding.return_value = [0.1, 0.2, 0.3]

        with patch("ludora.operations.resolve_database_url", return_value="postgresql://ludora") as resolve_database_url, patch(
            "ludora.operations.resolve_openai_api_key", return_value="openai-key"
        ) as resolve_openai_api_key, patch(
            "ludora.operations.resolve_embedding_model", return_value="text-embedding-3-small"
        ) as resolve_embedding_model, patch(
            "ludora.operations.connect_database", return_value=connection
        ) as connect_database, patch(
            "ludora.operations.DiscoveryRepository", return_value=repository
        ), patch(
            "ludora.operations.OpenAIEmbeddingClient", return_value=client
        ) as embedding_client, patch(
            "ludora.operations.build_item_embedding_text", return_value="Name: Calico"
        ), patch(
            "ludora.operations.source_text_hash", return_value="source-hash"
        ):
            result = run_item_embeddings(refresh_mode="missing", env_file="custom.env")

        resolve_database_url.assert_called_once()
        self.assertEqual(resolve_database_url.call_args.kwargs["dotenv_path"], "custom.env")
        resolve_openai_api_key.assert_called_once()
        self.assertEqual(resolve_openai_api_key.call_args.kwargs["dotenv_path"], "custom.env")
        resolve_embedding_model.assert_called_once()
        self.assertEqual(resolve_embedding_model.call_args.kwargs["dotenv_path"], "custom.env")
        connect_database.assert_called_once_with("postgresql://ludora")
        embedding_client.assert_called_once_with(api_key="openai-key", model="text-embedding-3-small")
        repository.list_item_search_embedding_sources.assert_called_once_with(refresh_mode="missing")
        client.create_embedding.assert_called_once_with("Name: Calico")
        repository.upsert_item_search_embedding.assert_called_once_with(
            item_id=77,
            embedding=[0.1, 0.2, 0.3],
            source_text="Name: Calico",
            source_hash="source-hash",
            model="text-embedding-3-small",
        )
        connection.close.assert_called_once_with()
        self.assertEqual(result.refresh_mode, "missing")
        self.assertEqual(result.selected_items, 1)
        self.assertEqual(result.embedded_items, 1)
        self.assertEqual(result.model, "text-embedding-3-small")

    def test_run_item_embeddings_requires_openai_key(self):
        with patch("ludora.operations.resolve_database_url", return_value="postgresql://ludora"), patch(
            "ludora.operations.resolve_openai_api_key", return_value=""
        ):
            with self.assertRaisesRegex(RuntimeError, "Missing OpenAI API key"):
                run_item_embeddings()

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

    def test_manager_records_successful_item_update_run_result(self):
        manager = StoreDiscoveryRunManager(
            runner=lambda: StoreDiscoveryRunResult(0, 0, 0),
            item_update_runner=lambda: ItemUpdateRunResult(updated_items=6),
            background=False,
        )

        run = manager.start_item_update()

        self.assertEqual(run.status, "completed")
        self.assertEqual(run.run_type, "item_update")
        self.assertEqual(run.result.updated_items, 6)
        self.assertEqual(manager.get_latest_run().id, run.id)

    def test_manager_records_successful_item_embedding_run_result(self):
        manager = StoreDiscoveryRunManager(
            runner=lambda: StoreDiscoveryRunResult(0, 0, 0),
            item_embedding_runner=lambda refresh_mode: ItemEmbeddingRunResult(
                refresh_mode=refresh_mode,
                selected_items=7,
                embedded_items=7,
                model="text-embedding-3-small",
            ),
            background=False,
        )

        run = manager.start_item_embeddings("full")

        self.assertEqual(run.status, "completed")
        self.assertEqual(run.run_type, "item_embeddings")
        self.assertEqual(run.result.refresh_mode, "full")
        self.assertEqual(run.result.embedded_items, 7)
        self.assertEqual(manager.get_latest_run().id, run.id)

    def test_manager_passes_env_file_to_default_runners(self):
        with patch(
            "ludora.operations.run_store_discovery",
            return_value=StoreDiscoveryRunResult(1, 2, 3),
        ) as store_runner, patch(
            "ludora.operations.run_item_discovery",
            return_value=ItemDiscoveryRunResult(12, "https://example.mx/", 4),
        ) as item_runner, patch(
            "ludora.operations.run_item_update",
            return_value=ItemUpdateRunResult(5),
        ) as item_update_runner, patch(
            "ludora.operations.run_item_embeddings",
            return_value=ItemEmbeddingRunResult("missing", 6, 6, "text-embedding-3-small"),
        ) as item_embedding_runner:
            manager = StoreDiscoveryRunManager(env_file="custom.env", background=False)

            manager.start_store_discovery()
            manager.start_item_discovery(12, "https://example.mx/")
            manager.start_item_update()
            manager.start_item_embeddings("missing")

        store_runner.assert_called_once_with(env_file="custom.env")
        item_runner.assert_called_once_with(store_id=12, website_url="https://example.mx/", env_file="custom.env")
        item_update_runner.assert_called_once_with(env_file="custom.env")
        item_embedding_runner.assert_called_once_with(refresh_mode="missing", env_file="custom.env")

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
