from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ludora.bgg import BggSearchResult, BggThing
from ludora.item_import import BggItemImporter, normalize_title
from ludora.models import DiscoveryItemCandidateRecord


HIGH_CONFIDENCE_MATCH_THRESHOLD = 0.9
MEANINGFUL_EXTRA_TOKENS = {
    "5",
    "6",
    "anniversary",
    "big",
    "box",
    "card",
    "collector",
    "dice",
    "duel",
    "expansion",
    "juego",
    "junior",
    "legacy",
    "plus",
    "roll",
    "travel",
    "write",
}


@dataclass(frozen=True)
class MatchScore:
    score: float
    reasons: list[str]


@dataclass(frozen=True)
class LocalItemMatch:
    item_id: int
    name: str
    normalized_name: str
    item_type: str = "unknown"
    bgg_id: int | None = None
    aliases: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateOfferMatch:
    item_id: int
    source: str
    matched_name: str
    score: float
    reasons: list[str]
    bgg_id: int | None = None
    payload: dict[str, object] = field(default_factory=dict)


class ItemProcessingRepository(Protocol):
    def find_local_item_matches(self, title: str) -> list[LocalItemMatch]:
        ...

    def get_bgg_search_cache(self, query: str) -> list[BggSearchResult] | None:
        ...

    def upsert_bgg_search_cache(self, query: str, results: list[BggSearchResult]) -> None:
        ...

    def link_item_to_store_item(
        self,
        candidate_id: int,
        record: DiscoveryItemCandidateRecord,
        match: CandidateOfferMatch,
    ) -> None:
        ...

    def mark_item_candidate_not_boardgame(self, candidate_id: int, reasons: list[str]) -> None:
        ...

    def mark_item_candidate_match_not_found(self, candidate_id: int, reasons: list[str]) -> None:
        ...

    def mark_item_candidate_processing_error(self, candidate_id: int, error: str) -> None:
        ...


class ItemCandidateProcessor:
    def __init__(
        self,
        repository: ItemProcessingRepository,
        *,
        bgg_client: Any | None = None,
        bgg_importer: BggItemImporter | None = None,
        threshold: float = HIGH_CONFIDENCE_MATCH_THRESHOLD,
        max_bgg_results: int = 10,
    ) -> None:
        self.repository = repository
        self.bgg_client = bgg_client
        self.bgg_importer = bgg_importer
        self.threshold = threshold
        self.max_bgg_results = max_bgg_results

    def process_candidate(self, candidate_id: int, record: DiscoveryItemCandidateRecord) -> None:
        try:
            if not record.is_boardgame:
                return

            local_match = self._best_local_match(record)
            if local_match:
                self.repository.link_item_to_store_item(candidate_id, record, local_match)
                return

            if not self.bgg_client or not self.bgg_importer:
                self.repository.mark_item_candidate_processing_error(candidate_id, "BGG client is not configured")
                return

            bgg_match = self._best_bgg_match(record)
            if bgg_match:
                self.repository.link_item_to_store_item(candidate_id, record, bgg_match)
                return

            self.repository.mark_item_candidate_match_not_found(candidate_id, ["no match above threshold"])
        except Exception as exc:  # pragma: no cover - exercised through integration paths.
            self.repository.mark_item_candidate_processing_error(candidate_id, str(exc))

    def _best_local_match(self, record: DiscoveryItemCandidateRecord) -> CandidateOfferMatch | None:
        scored_matches: list[CandidateOfferMatch] = []
        for item in self.repository.find_local_item_matches(record.title):
            score = score_local_item(record, item)
            if score.score >= self.threshold:
                scored_matches.append(
                    CandidateOfferMatch(
                        item_id=item.item_id,
                        source="LOCAL",
                        matched_name=item.name,
                        score=score.score,
                        reasons=score.reasons,
                        bgg_id=item.bgg_id,
                        payload={"item": _local_item_payload(item)},
                    )
                )
        return _best_match(scored_matches)

    def _best_bgg_match(self, record: DiscoveryItemCandidateRecord) -> CandidateOfferMatch | None:
        search_results = self.repository.get_bgg_search_cache(record.title)
        if search_results is None:
            search_results = self.bgg_client.search(record.title)
            self.repository.upsert_bgg_search_cache(record.title, search_results)

        scored_matches: list[CandidateOfferMatch] = []
        seen_bgg_ids: set[int] = set()
        for result in search_results:
            if result.bgg_id in seen_bgg_ids:
                continue
            seen_bgg_ids.add(result.bgg_id)
            if len(seen_bgg_ids) > self.max_bgg_results:
                break

            fetched = self.bgg_client.fetch_thing(result.bgg_id)
            if not fetched:
                continue
            thing, raw_xml = fetched
            score = score_bgg_thing(record, thing)
            if score.score < self.threshold:
                continue

            item_id = self.bgg_importer.import_thing(thing, raw_xml)
            scored_matches.append(
                CandidateOfferMatch(
                    item_id=item_id,
                    source="BGG",
                    matched_name=thing.name,
                    score=score.score,
                    reasons=score.reasons,
                    bgg_id=thing.bgg_id,
                    payload={"search_result": _search_result_payload(result), "bgg_item": _bgg_thing_payload(thing)},
                )
            )

        return _best_match(scored_matches)


def score_local_item(record: DiscoveryItemCandidateRecord, item: LocalItemMatch) -> MatchScore:
    reasons: list[str] = []
    candidate_title = normalize_title(record.title)
    canonical_name = normalize_title(item.name or item.normalized_name)
    normalized_item_name = normalize_title(item.normalized_name)
    aliases = [normalize_title(alias) for alias in item.aliases]
    score = 0.2

    if candidate_title and (candidate_title == canonical_name or candidate_title == normalized_item_name):
        score = 0.94
        reasons.append("exact local item name match")
    elif candidate_title in aliases:
        score = 0.94
        reasons.append("exact local alias match")
    elif _has_title_overlap(candidate_title, canonical_name):
        score = 0.55
        reasons.append("substring title overlap only")
        reasons.extend(_meaningful_extra_token_reasons(candidate_title, canonical_name))
    else:
        reasons.append("no exact local name match")

    if _item_type_conflicts(record.item_type, item.item_type):
        score -= 0.25
        reasons.append("item type conflict")

    return MatchScore(score=_clamp_score(score), reasons=reasons)


def score_bgg_thing(record: DiscoveryItemCandidateRecord, thing: BggThing) -> MatchScore:
    reasons: list[str] = []
    candidate_title = normalize_title(record.title)
    names = [("primary", thing.name), *[("alternate", name) for name in thing.alternate_names]]
    exact_name = next((entry for entry in names if normalize_title(entry[1]) == candidate_title), None)
    score = 0.2

    if exact_name:
        score = 0.9
        reasons.append(f"exact BGG {exact_name[0]} name match")
    else:
        best_name = next((entry for entry in names if _has_title_overlap(candidate_title, normalize_title(entry[1]))), None)
        if best_name:
            score = 0.55
            reasons.append("substring title overlap only")
            reasons.extend(_meaningful_extra_token_reasons(candidate_title, normalize_title(best_name[1])))
        else:
            reasons.append("no exact BGG name match")

    if _item_type_conflicts(record.item_type, _bgg_type_to_item_type(thing.item_type)):
        score -= 0.25
        reasons.append("item type conflict")

    if _publisher_overlaps(record.publisher, thing):
        score += 0.03
        reasons.append("publisher overlap")

    if record.min_players and thing.min_players and record.min_players == thing.min_players:
        score += 0.02
        reasons.append("minimum players match")

    if record.max_players and thing.max_players and record.max_players == thing.max_players:
        score += 0.02
        reasons.append("maximum players match")

    return MatchScore(score=_clamp_score(score), reasons=reasons)


def _best_match(matches: list[CandidateOfferMatch]) -> CandidateOfferMatch | None:
    return sorted(matches, key=lambda match: match.score, reverse=True)[0] if matches else None


def _has_title_overlap(candidate_title: str, matched_title: str) -> bool:
    return bool(candidate_title and matched_title and (candidate_title in matched_title or matched_title in candidate_title))


def _meaningful_extra_token_reasons(candidate_title: str, matched_title: str) -> list[str]:
    matched_tokens = set(matched_title.split())
    return [
        f"meaningful extra title token: {token}"
        for token in candidate_title.split()
        if token and token not in matched_tokens and token in MEANINGFUL_EXTRA_TOKENS
    ]


def _item_type_conflicts(candidate_type: str | None, matched_type: str | None) -> bool:
    if not candidate_type or candidate_type == "unknown" or not matched_type or matched_type == "unknown":
        return False
    return candidate_type != matched_type


def _bgg_type_to_item_type(value: str) -> str:
    if value == "boardgameexpansion":
        return "expansion"
    if value == "boardgame":
        return "base_game"
    return "unknown"


def _publisher_overlaps(candidate_publisher: str, thing: BggThing) -> bool:
    normalized_publisher = normalize_title(candidate_publisher)
    return bool(normalized_publisher and any(normalize_title(publisher.name) == normalized_publisher for publisher in thing.publishers))


def _clamp_score(value: float) -> float:
    return max(0.0, min(0.99, round(value, 4)))


def _local_item_payload(item: LocalItemMatch) -> dict[str, object]:
    return {
        "id": item.item_id,
        "name": item.name,
        "normalized_name": item.normalized_name,
        "item_type": item.item_type,
        "bgg_id": item.bgg_id,
        "aliases": item.aliases,
    }


def _search_result_payload(result: Any) -> dict[str, object]:
    return {
        "bgg_id": result.bgg_id,
        "name": result.name,
        "item_type": result.item_type,
        "year_published": result.year_published,
    }


def _bgg_thing_payload(thing: BggThing) -> dict[str, object]:
    return {
        "bgg_id": thing.bgg_id,
        "item_type": thing.item_type,
        "name": thing.name,
        "alternate_names": thing.alternate_names,
        "year_published": thing.year_published,
    }
