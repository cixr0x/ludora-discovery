from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from ludora.admin_matching import AdminItemMatcher
from ludora.collector import collect_stores
from ludora.config import (
    resolve_admin_api_url,
    resolve_brave_api_key,
    resolve_browser_fetch_enabled,
    resolve_database_url,
    resolve_embedding_model,
    resolve_openai_api_key,
)
from ludora.database import DiscoveryRepository, connect_database
from ludora.embeddings import OpenAIEmbeddingClient, build_item_embedding_text, source_text_hash
from ludora.inventory import collect_store_inventory, update_confirmed_store_items


RunStatus = Literal["running", "completed", "failed"]
EmbeddingRefreshMode = Literal["missing", "full"]


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


@dataclass(frozen=True)
class ItemUpdateRunResult:
    updated_items: int

    def to_dict(self) -> dict[str, int]:
        return {
            "updated_items": self.updated_items,
        }


@dataclass(frozen=True)
class ItemEmbeddingRunResult:
    refresh_mode: str
    selected_items: int
    embedded_items: int
    model: str

    def to_dict(self) -> dict[str, object]:
        return {
            "refresh_mode": self.refresh_mode,
            "selected_items": self.selected_items,
            "embedded_items": self.embedded_items,
            "model": self.model,
        }


@dataclass
class StoreDiscoveryRun:
    id: str
    status: RunStatus
    started_at: datetime
    run_type: str = "store_discovery"
    completed_at: datetime | None = None
    result: StoreDiscoveryRunResult | ItemDiscoveryRunResult | ItemUpdateRunResult | ItemEmbeddingRunResult | None = None
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
    admin_api_url = resolve_admin_api_url(env=current_env, dotenv_path=env_file)

    connection = connect_database(database_url)
    try:
        repository = DiscoveryRepository(connection)
        item_processor = AdminItemMatcher(admin_api_url, repository)
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


def run_item_update(
    *,
    env: Mapping[str, str] | None = None,
    env_file: str = ".env",
) -> ItemUpdateRunResult:
    current_env = env if env is not None else os.environ
    database_url = resolve_database_url(None, env=current_env, dotenv_path=env_file)
    if not database_url:
        raise RuntimeError("Missing database URL")
    browser_fetch_enabled = resolve_browser_fetch_enabled(env=current_env, dotenv_path=env_file)

    connection = connect_database(database_url)
    try:
        repository = DiscoveryRepository(connection)
        records = update_confirmed_store_items(
            repository,
            browser_fetch_enabled=browser_fetch_enabled,
        )
        return ItemUpdateRunResult(updated_items=len(records))
    finally:
        connection.close()


def run_item_embeddings(
    *,
    refresh_mode: EmbeddingRefreshMode = "missing",
    env: Mapping[str, str] | None = None,
    env_file: str = ".env",
) -> ItemEmbeddingRunResult:
    current_env = env if env is not None else os.environ
    database_url = resolve_database_url(None, env=current_env, dotenv_path=env_file)
    if not database_url:
        raise RuntimeError("Missing database URL")
    openai_api_key = resolve_openai_api_key(env=current_env, dotenv_path=env_file)
    if not openai_api_key:
        raise RuntimeError("Missing OpenAI API key")
    embedding_model = resolve_embedding_model(env=current_env, dotenv_path=env_file)

    connection = connect_database(database_url)
    try:
        repository = DiscoveryRepository(connection)
        client = OpenAIEmbeddingClient(api_key=openai_api_key, model=embedding_model)
        sources = repository.list_item_search_embedding_sources(refresh_mode=refresh_mode)
        embedded_items = 0
        for source in sources:
            source_text = build_item_embedding_text(source)
            embedding = client.create_embedding(source_text)
            repository.upsert_item_search_embedding(
                item_id=source.item_id,
                embedding=embedding,
                source_text=source_text,
                source_hash=source_text_hash(source_text),
                model=embedding_model,
            )
            embedded_items += 1

        return ItemEmbeddingRunResult(
            refresh_mode=refresh_mode,
            selected_items=len(sources),
            embedded_items=embedded_items,
            model=embedding_model,
        )
    finally:
        connection.close()


class StoreDiscoveryRunManager:
    def __init__(
        self,
        runner: Callable[[], StoreDiscoveryRunResult] | None = None,
        item_runner: Callable[[int, str], ItemDiscoveryRunResult] | None = None,
        item_update_runner: Callable[[], ItemUpdateRunResult] | None = None,
        item_embedding_runner: Callable[[EmbeddingRefreshMode], ItemEmbeddingRunResult] | None = None,
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
        self.item_update_runner = item_update_runner or (lambda: run_item_update(env_file=env_file))
        self.item_embedding_runner = item_embedding_runner or (
            lambda refresh_mode: run_item_embeddings(refresh_mode=refresh_mode, env_file=env_file)
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

    def start_item_update(self) -> StoreDiscoveryRun:
        with self.lock:
            if self.active_run_id and self.runs[self.active_run_id].status == "running":
                raise OperationAlreadyRunning("Discovery operation is already running")

            run = StoreDiscoveryRun(
                id=str(uuid.uuid4()),
                status="running",
                started_at=_utc_now(),
                run_type="item_update",
            )
            self.runs[run.id] = run
            self.latest_run_id = run.id
            self.active_run_id = run.id

        if self.background:
            thread = threading.Thread(target=self._execute_item_update_run, args=(run.id,), daemon=True)
            thread.start()
        else:
            self._execute_item_update_run(run.id)

        return self.get_run(run.id) or run

    def start_item_embeddings(self, refresh_mode: EmbeddingRefreshMode) -> StoreDiscoveryRun:
        with self.lock:
            if self.active_run_id and self.runs[self.active_run_id].status == "running":
                raise OperationAlreadyRunning("Discovery operation is already running")

            run = StoreDiscoveryRun(
                id=str(uuid.uuid4()),
                status="running",
                started_at=_utc_now(),
                run_type="item_embeddings",
            )
            self.runs[run.id] = run
            self.latest_run_id = run.id
            self.active_run_id = run.id

        if self.background:
            thread = threading.Thread(target=self._execute_item_embedding_run, args=(run.id, refresh_mode), daemon=True)
            thread.start()
        else:
            self._execute_item_embedding_run(run.id, refresh_mode)

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

    def _execute_item_update_run(self, run_id: str) -> None:
        try:
            result = self.item_update_runner()
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

    def _execute_item_embedding_run(self, run_id: str, refresh_mode: EmbeddingRefreshMode) -> None:
        try:
            result = self.item_embedding_runner(refresh_mode)
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
