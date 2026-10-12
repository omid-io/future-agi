"""Small, storage-free analysis helpers for dataset experiment runs.

The dataset/experiment platform RFC deliberately keeps analysis derived from
``ExperimentRunItem`` records.  This module is the first query contract for
that approach: callers provide run-item records (usually an adapter over ORM
objects), and receive deterministic aggregates plus baseline/candidate case
comparisons.  It does not create or mutate database records.

The functions intentionally cover only the first release surface:

* split and exact metadata filtering;
* item/status and score aggregates; and
* per-example baseline/candidate statuses.

Percentiles, significance tests, custom query languages, and trace-tree
analysis belong to later layers.  Keeping this module pure means it can be
used by API views, background jobs, SDK adapters, and contract tests without
requiring Django or a database.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from math import isfinite
from numbers import Real
from typing import Any

# Values are deliberately strings so the result can be emitted directly as a
# JSON API response and remain forward-compatible with persisted score status
# values in the RFC evidence envelope.
ASSESSED = "assessed"
UNKNOWN = "unknown"
NOT_ASSESSED = "not_assessed"
UNSUPPORTED = "unsupported"
ERROR = "error"

IMPROVED = "improved"
REGRESSED = "regressed"
TIE = "tie"
MISSING = "missing"
DUPLICATE = "duplicate"
INVALID = "invalid"


@dataclass(frozen=True)
class RunItemRecord:
    """The minimal read shape needed by analysis.

    An adapter can construct this from a persisted ``ExperimentRunItem``.
    ``scores`` accepts scalar values or evidence-envelope-like mappings, for
    example ``{"quality": {"value": 0.8, "status": "assessed"}}``.
    ``score`` is retained as a convenient default metric for callers that have
    only one score.
    """

    example_id: str
    status: str = "completed"
    score: float | int | None = None
    scores: Mapping[str, Any] = field(default_factory=dict)
    split_names: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    trial_number: int = 1


@dataclass(frozen=True)
class AnalysisFilter:
    """Simple, deterministic filters supported by the first analysis API."""

    split_name: str | None = None
    metadata_equals: Mapping[str, Any] = field(default_factory=dict)
    item_statuses: frozenset[str] | None = None


@dataclass(frozen=True)
class ScoreResult:
    value: float | None
    status: str


@dataclass(frozen=True)
class RunAggregate:
    """Aggregate values over the filtered run items."""

    item_count: int
    completed_count: int
    failed_count: int
    score_count: int
    score_average: float | None
    score_min: float | None
    score_max: float | None
    pass_count: int | None
    pass_rate: float | None
    status_counts: Mapping[str, int]


@dataclass(frozen=True)
class CaseComparison:
    """One baseline/candidate result aligned by stable ``example_id``."""

    example_id: str
    status: str
    baseline_status: str | None
    candidate_status: str | None
    baseline_score: float | None = None
    candidate_score: float | None = None
    score_delta: float | None = None
    missing_side: str | None = None


@dataclass(frozen=True)
class ComparisonSummary:
    baseline: RunAggregate
    candidate: RunAggregate
    cases: tuple[CaseComparison, ...]
    status_counts: Mapping[str, int]

    @property
    def comparable_count(self) -> int:
        """Number of cases with a numeric score on both sides."""

        return sum(
            1
            for case in self.cases
            if case.status in {IMPROVED, REGRESSED, TIE}
        )


def _item_value(item: RunItemRecord | Mapping[str, Any], name: str, default: Any = None) -> Any:
    """Read a field from a record or mapping for lightweight adapters."""

    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _normalise_item(item: RunItemRecord | Mapping[str, Any]) -> RunItemRecord:
    """Convert a mapping adapter result into the pure record shape."""

    if isinstance(item, RunItemRecord):
        return item

    split_names = _item_value(item, "split_names", ()) or ()
    if isinstance(split_names, str):
        split_names = (split_names,)
    metadata = _item_value(item, "metadata", {}) or {}
    scores = _item_value(item, "scores", {}) or {}
    return RunItemRecord(
        example_id=str(_item_value(item, "example_id", "")),
        status=str(_item_value(item, "status", "completed")),
        score=_item_value(item, "score"),
        scores=scores,
        split_names=tuple(str(value) for value in split_names),
        metadata=metadata,
        error=_item_value(item, "error"),
        trial_number=int(_item_value(item, "trial_number", 1) or 1),
    )


def _matches(item: RunItemRecord, filters: AnalysisFilter | None) -> bool:
    if filters is None:
        return True
    if filters.split_name is not None and filters.split_name not in item.split_names:
        return False
    if filters.item_statuses is not None and item.status not in filters.item_statuses:
        return False
    return all(item.metadata.get(key) == value for key, value in filters.metadata_equals.items())


def _filtered_items(
    items: Iterable[RunItemRecord | Mapping[str, Any]],
    filters: AnalysisFilter | None,
) -> list[RunItemRecord]:
    return [item for item in (_normalise_item(value) for value in items) if _matches(item, filters)]


def _score_result(item: RunItemRecord, metric: str) -> ScoreResult:
    """Extract one score while preserving evidence-envelope status."""

    if metric == "score":
        raw = item.score
    else:
        raw = item.scores.get(metric)

    if raw is None:
        # A failed item is an execution error; a completed item without a
        # score is deliberately not_assessed rather than an implicit zero.
        return ScoreResult(None, ERROR if item.status in {"failed", "error"} else NOT_ASSESSED)

    status = ASSESSED
    if isinstance(raw, Mapping):
        status = str(raw.get("status", ASSESSED if raw.get("value", raw.get("score")) is not None else NOT_ASSESSED))
        raw = raw.get("value", raw.get("score"))

    if status != ASSESSED:
        return ScoreResult(None, status)
    if isinstance(raw, bool) or not isinstance(raw, Real):
        return ScoreResult(None, INVALID)
    value = float(raw)
    if not isfinite(value):
        return ScoreResult(None, INVALID)
    return ScoreResult(value, ASSESSED)


def summarize_run_items(
    items: Iterable[RunItemRecord | Mapping[str, Any]],
    *,
    metric: str = "score",
    filters: AnalysisFilter | None = None,
    passing_score: float | None = None,
) -> RunAggregate:
    """Return simple score and execution aggregates for a filtered run.

    ``passing_score`` is optional.  If omitted, pass counts/rates stay
    ``None`` because the analysis layer must not invent a threshold.
    """

    filtered = _filtered_items(items, filters)
    statuses = Counter(item.status for item in filtered)
    values: list[float] = []
    pass_count = 0 if passing_score is not None else None
    for item in filtered:
        result = _score_result(item, metric)
        if result.value is None:
            continue
        values.append(result.value)
        if passing_score is not None and result.value >= passing_score:
            pass_count += 1

    score_count = len(values)
    average = sum(values) / score_count if values else None
    pass_rate = (pass_count / score_count) if passing_score is not None and score_count else None
    return RunAggregate(
        item_count=len(filtered),
        completed_count=sum(1 for item in filtered if item.status in {"completed", "success", "succeeded"}),
        failed_count=sum(1 for item in filtered if item.status in {"failed", "error"}),
        score_count=score_count,
        score_average=average,
        score_min=min(values) if values else None,
        score_max=max(values) if values else None,
        pass_count=pass_count,
        pass_rate=pass_rate,
        status_counts=dict(sorted(statuses.items())),
    )


def _by_example(items: Sequence[RunItemRecord]) -> tuple[dict[str, RunItemRecord], set[str]]:
    grouped: dict[str, list[RunItemRecord]] = {}
    for item in items:
        grouped.setdefault(item.example_id, []).append(item)
    duplicates = {example_id for example_id, values in grouped.items() if len(values) > 1}
    return {example_id: values[0] for example_id, values in grouped.items()}, duplicates


def compare_run_items(
    baseline_items: Iterable[RunItemRecord | Mapping[str, Any]],
    candidate_items: Iterable[RunItemRecord | Mapping[str, Any]],
    *,
    metric: str = "score",
    filters: AnalysisFilter | None = None,
    passing_score: float | None = None,
) -> ComparisonSummary:
    """Align two runs by stable example ID and classify each case.

    Missing examples, duplicate IDs, invalid scores, and unassessed scores
    remain explicit statuses.  Only numeric scores on both sides become
    ``improved``, ``regressed``, or ``tie``.
    """

    baseline = _filtered_items(baseline_items, filters)
    candidate = _filtered_items(candidate_items, filters)
    baseline_by_id, baseline_duplicates = _by_example(baseline)
    candidate_by_id, candidate_duplicates = _by_example(candidate)

    baseline_summary = summarize_run_items(
        baseline, metric=metric, passing_score=passing_score
    )
    candidate_summary = summarize_run_items(
        candidate, metric=metric, passing_score=passing_score
    )

    cases: list[CaseComparison] = []
    for example_id in sorted(set(baseline_by_id) | set(candidate_by_id)):
        baseline_item = baseline_by_id.get(example_id)
        candidate_item = candidate_by_id.get(example_id)
        if example_id in baseline_duplicates or example_id in candidate_duplicates:
            cases.append(
                CaseComparison(
                    example_id=example_id,
                    status=DUPLICATE,
                    baseline_status=baseline_item.status if baseline_item else None,
                    candidate_status=candidate_item.status if candidate_item else None,
                )
            )
            continue
        if baseline_item is None or candidate_item is None:
            cases.append(
                CaseComparison(
                    example_id=example_id,
                    status=MISSING,
                    baseline_status=baseline_item.status if baseline_item else None,
                    candidate_status=candidate_item.status if candidate_item else None,
                    missing_side="baseline" if baseline_item is None else "candidate",
                )
            )
            continue

        baseline_score = _score_result(baseline_item, metric)
        candidate_score = _score_result(candidate_item, metric)
        if baseline_score.status != ASSESSED or candidate_score.status != ASSESSED:
            status = (
                INVALID
                if INVALID in {baseline_score.status, candidate_score.status}
                else baseline_score.status
                if baseline_score.status != ASSESSED
                else candidate_score.status
            )
            cases.append(
                CaseComparison(
                    example_id=example_id,
                    status=status,
                    baseline_status=baseline_score.status,
                    candidate_status=candidate_score.status,
                    baseline_score=baseline_score.value,
                    candidate_score=candidate_score.value,
                )
            )
            continue

        assert baseline_score.value is not None and candidate_score.value is not None
        delta = candidate_score.value - baseline_score.value
        status = IMPROVED if delta > 0 else REGRESSED if delta < 0 else TIE
        cases.append(
            CaseComparison(
                example_id=example_id,
                status=status,
                baseline_status=baseline_score.status,
                candidate_status=candidate_score.status,
                baseline_score=baseline_score.value,
                candidate_score=candidate_score.value,
                score_delta=delta,
            )
        )

    return ComparisonSummary(
        baseline=baseline_summary,
        candidate=candidate_summary,
        cases=tuple(cases),
        status_counts=dict(sorted(Counter(case.status for case in cases).items())),
    )
