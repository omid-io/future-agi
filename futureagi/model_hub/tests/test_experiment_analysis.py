import pytest

from model_hub.services.experiment_analysis import (
    DUPLICATE,
    IMPROVED,
    INVALID,
    MISSING,
    NOT_ASSESSED,
    REGRESSED,
    TIE,
    UNKNOWN,
    AnalysisFilter,
    RunItemRecord,
    compare_run_items,
    summarize_run_items,
)


def test_summarize_applies_split_metadata_and_keeps_unassessed_explicit():
    items = [
        RunItemRecord("a", score=0.8, split_names=("test",), metadata={"kind": "easy"}),
        RunItemRecord("b", score=0.2, split_names=("test",), metadata={"kind": "hard"}),
        RunItemRecord(
            "c",
            scores={"quality": {"status": NOT_ASSESSED}},
            split_names=("train",),
            metadata={"kind": "easy"},
        ),
    ]

    result = summarize_run_items(
        items,
        filters=AnalysisFilter(split_name="test", metadata_equals={"kind": "easy"}),
        passing_score=0.5,
    )

    assert result.item_count == 1
    assert result.score_count == 1
    assert result.score_average == 0.8
    assert result.pass_count == 1
    assert result.pass_rate == 1.0
    assert result.status_counts == {"completed": 1}

    unassessed = summarize_run_items(items, metric="quality")
    assert unassessed.score_count == 0
    assert unassessed.score_average is None


def test_compare_aligns_cases_and_reports_explicit_statuses():
    baseline = [
        RunItemRecord("improve", score=0.2),
        RunItemRecord("regress", score=0.8),
        RunItemRecord("tie", score=0.5),
        RunItemRecord("missing_candidate", score=0.4),
        RunItemRecord("unknown", scores={"quality": {"status": UNKNOWN}}),
        RunItemRecord("duplicate", score=0.1),
        RunItemRecord("duplicate", score=0.2),
        RunItemRecord("invalid", score=float("nan")),
    ]
    candidate = [
        RunItemRecord("improve", score=0.7),
        RunItemRecord("regress", score=0.3),
        RunItemRecord("tie", score=0.5),
        RunItemRecord("missing_baseline", score=0.9),
        RunItemRecord("unknown", scores={"quality": {"status": UNKNOWN}}),
        RunItemRecord("duplicate", score=0.9),
        RunItemRecord("invalid", score=0.4),
    ]

    result = compare_run_items(baseline, candidate, metric="quality")
    # The quality metric is absent on the numeric cases, so they are explicit
    # not_assessed.  The status matrix is still aligned by stable example ID.
    assert result.cases[0].example_id == "duplicate"
    assert result.status_counts[DUPLICATE] == 1
    assert result.status_counts[MISSING] == 2
    assert result.status_counts[NOT_ASSESSED] >= 4
    assert result.status_counts[UNKNOWN] == 1

    result = compare_run_items(baseline, candidate)
    assert result.status_counts[IMPROVED] == 1
    assert result.status_counts[REGRESSED] == 1
    assert result.status_counts[TIE] == 1
    assert result.status_counts[DUPLICATE] == 1
    assert result.status_counts[MISSING] == 2
    assert result.status_counts[INVALID] == 1
    assert result.comparable_count == 3
    by_id = {case.example_id: case for case in result.cases}
    assert by_id["improve"].score_delta == pytest.approx(0.5)
    assert by_id["missing_candidate"].missing_side == "candidate"
    assert by_id["missing_baseline"].missing_side == "baseline"
