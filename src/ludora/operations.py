from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from ludora.bgg import BggClient
from ludora.collector import collect_stores
from ludora.config import (
    resolve_bgg_api_base_url,
    resolve_bgg_api_token,
    resolve_brave_api_key,
    resolve_browser_fetch_enabled,
    resolve_database_url,
)
from ludora.database import DiscoveryRepository, connect_database
from ludora.inventory import collect_store_inventory
from ludora.item_import import BggItemImporter
from ludora.item_processing import ItemCandidateProcessor


RunStatus = Literal["running", "completed", "failed"]


class OperationAlreadyRunning(RuntimeError):
    pass


@dataclass(frozen=True)
class StoreDiscoveryRunResult:
    searched_queries: int
    candidate_domains: int
    accepted_stores: int

    def to_dict(self) -> dict[str, int]:
        return {
            "searched_queries": self.searched_queries,
            "candidate_domains": self.candidate_domains,
            "accepted_stores": self.accepted_stores,
        }


@dataclass(frozen=True)
class ItemDiscoveryRunResult:
    store_id: int
    website_url: str
    item_candidates: int

    def to_dict(self) -> dict[str, object]:
        return {
            "store_id": self.store_id,
            "website_url": self.website_url,
            "item_candidates": self.item_candidates,
        }


@dataclass
class StoreDiscoveryRun:
    id: str
    status: RunStatus
    started_at: datetime
    run_type: str = "store_discovery"
    completed_at: datetime | None = None
    result: StoreDiscoveryRunResult | ItemDiscoveryRunResult | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.run_type,
            "status": self.status,
            "started_at": _format_datetime(self.started_at),
            "completed_at": _format_datetime(self.completed_at) if self.completed_at else None,
            "result": self.result.to_dict() if self.result else None,
            "error": self.error,
        }


def run_store_discovery(
    *,
    env: Mapping[str, str] | None = None,
    env_file: str = ".env",
) -> StoreDiscoveryRunResult:
    current_env = env if env is not None else os.environ
    api_key = resolve_brave_api_key(None, env=current_env, dotenv_path=env_file)
    if not api_key:
        raise RuntimeError("Missing Brave API key")

    database_url = resolve_database_url(None, env=current_env, dotenv_path=env_file)
    if not database_url:
        raise RuntimeError("Missing database URL")

    connection = connect_database(database_url)
    try:
        repository = DiscoveryRepository(connection)
        summary = collect_stores(
            api_key=api_key,
            query_scope="expanded",
            max_queries=None,
            count=20,
            pages=1,
            request_delay=1.1,
            website_delay=0.3,
            max_enrichment_pages=3,
            include_low_confidence=False,
            verbose=False,
            discovery_repository=repository,
            export_files=False,
        )
        return StoreDiscoveryRunResult(
            searched_queries=summary.searched_queries,
            candidate_domains=summary.candidate_domains,
            accepted_stores=len(summary.records),
        )
    finally:
        connection.close()


def run_item_discovery(
    *,
    store_id: int,
    website_url: str,
    env: Mapping[str, str] | None = None,
    env_file: str = ".env",
) -> ItemDiscoveryRunResult:
    current_env = env if env is not None else os.environ
    database_url = resolve_database_url(None, env=current_env, dotenv_path=env_file)
    if not database_url:
        raise RuntimeError("Missing database URL")
    browser_sitemap_fetch_enabled = resolve_browser_fetch_enabled(env=current_env, dotenv_path=env_file)
    bgg_api_token = resolve_bgg_api_token(None, env=current_env, dotenv_path=env_file)
    bgg_api_base_url = resolve_bgg_api_base_url(env=current_env, dotenv_path=env_file)

    connection = connect_database(database_url)
    try:
        repository = DiscoveryRepository(connection)
        bgg_client = BggClient(api_token=bgg_api_token, base_url=bgg_api_base_url) if bgg_api_token else None
        bgg_importer = BggItemImporter(connection, bgg_client=bgg_client) if bgg_client else None
        item_processor = ItemCandidateProcessor(repository, bgg_client=bgg_client, bgg_importer=bgg_importer)
        records = collect_store_inventory(
            website_url,
            store_id,
            repository,
            browser_sitemap_fetch_enabled=browser_sitemap_fetch_enabled,
            item_processor=item_processor,
        )
        return ItemDiscoveryRunResult(
            store_id=store_id,
            website_url=website_url,
            item_candidates=len(records),
        )
    finally:
        connection.close()


class StoreDiscoveryRunManager:
    def __init__(
        self,
        runner: Callable[[], StoreDiscoveryRunResult] | None = None,
        item_runner: Callable[[int, str], ItemDiscoveryRunResult] | None = None,
        *,
        background: bool = True,
        env_file: str = ".env",
    ) -> None:
        self.runner = runner or (lambda: run_store_discovery(env_file=env_file))
        self.item_runner = item_runner or (
            lambda store_id, website_url: run_item_discovery(
                store_id=store_id,
                website_url=website_url,
                env_file=env_file,
            )
        )
        self.background = background
        self.lock = threading.Lock()
        self.runs: dict[str, StoreDiscoveryRun] = {}
        self.latest_run_id: str | None = None
        self.active_run_id: str | None = None

    def start_store_discovery(self) -> StoreDiscoveryRun:
        with self.lock:
            if self.active_run_id and self.runs[self.active_run_id].status == "running":
                raise OperationAlreadyRunning("Store discovery is already running")

            run = StoreDiscoveryRun(
                id=str(uuid.uuid4()),
                status="running",
                started_at=_utc_now(),
            )
            self.runs[run.id] = run
            self.latest_run_id = run.id
            self.active_run_id = run.id

        if self.background:
            thread = threading.Thread(target=self._execute_run, args=(run.id,), daemon=True)
            thread.start()
        else:
            self._execute_run(run.id)

        return self.get_run(run.id) or run

    def start_item_discovery(self, store_id: int, website_url: str) -> StoreDiscoveryRun:
        with self.lock:
            if self.active_run_id and self.runs[self.active_run_id].status == "running":
                raise OperationAlreadyRunning("Discovery operation is already running")

            run = StoreDiscoveryRun(
                id=str(uuid.uuid4()),
                status="running",
                started_at=_utc_now(),
                run_type="item_discovery",
            )
            self.runs[run.id] = run
            self.latest_run_id = run.id
            self.active_run_id = run.id

        if self.background:
            thread = threading.Thread(target=self._execute_item_run, args=(run.id, store_id, website_url), daemon=True)
            thread.start()
        else:
            self._execute_item_run(run.id, store_id, website_url)

        return self.get_run(run.id) or run

    def get_run(self, run_id: str) -> StoreDiscoveryRun | None:
        with self.lock:
            return self.runs.get(run_id)

    def get_latest_run(self) -> StoreDiscoveryRun | None:
        with self.lock:
            if not self.latest_run_id:
                return None
            return self.runs.get(self.latest_run_id)

    def _execute_run(self, run_id: str) -> None:
        try:
            result = self.runner()
        except Exception as exc:  # pragma: no cover - message behavior is tested through manager.
            with self.lock:
                run = self.runs[run_id]
                run.status = "failed"
                run.error = str(exc)
                run.completed_at = _utc_now()
                self.active_run_id = None
            return

        with self.lock:
            run = self.runs[run_id]
            run.status = "completed"
            run.result = result
            run.completed_at = _utc_now()
            self.active_run_id = None

    def _execute_item_run(self, run_id: str, store_id: int, website_url: str) -> None:
        try:
            result = self.item_runner(store_id, website_url)
        except Exception as exc:  # pragma: no cover - message behavior is tested through manager.
            with self.lock:
                run = self.runs[run_id]
                run.status = "failed"
                run.error = str(exc)
                run.completed_at = _utc_now()
                self.active_run_id = None
            return

        with self.lock:
            run = self.runs[run_id]
            run.status = "completed"
            run.result = result
            run.completed_at = _utc_now()
            self.active_run_id = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
