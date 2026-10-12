import uuid

from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.serializers.experiments import (
    ExperimentRunItemSerializer,
    ExperimentRunSerializer,
)


def test_experiment_run_contract_serializes_dataset_version_relation():
    dataset_version_id = uuid.uuid4()
    dataset_version = DatasetVersion(id=dataset_version_id)
    run = ExperimentRun(
        dataset_version=dataset_version,
        task_kind=ExperimentRun.TaskKind.EXTERNAL,
        metadata={"source": "ci"},
        evaluator_versions=[{"id": "eval-v1"}],
    )

    data = ExperimentRunSerializer(run).data

    assert data["dataset_version_id"] == dataset_version_id
    assert data["task_kind"] == "external"
    assert data["status"] == ExperimentRun.Status.DRAFT
    assert data["metadata"] == {"source": "ci"}
    assert data["evaluator_versions"] == [{"id": "eval-v1"}]


def test_experiment_run_item_contract_preserves_trace_and_trial_fields():
    run = ExperimentRun(dataset_version=DatasetVersion(id=uuid.uuid4()))
    item = ExperimentRunItem(
        run=run,
        example_id=uuid.uuid4(),
        trial_number=2,
        status=ExperimentRunItem.Status.COMPLETED,
        output={"answer": "ok"},
        trace_id="trace-123",
        root_span_id="span-456",
        score_refs=[{"evaluator_version_id": "eval-v1", "score": 1.0}],
    )

    data = ExperimentRunItemSerializer(item).data

    # DRF's relational primary-key field keeps UUID values typed until the
    # renderer serializes the response body.
    assert data["run_id"] == run.id
    assert data["example_id"] == str(item.example_id)
    assert data["trial_number"] == 2
    assert data["status"] == "completed"
    assert data["trace_id"] == "trace-123"
    assert data["root_span_id"] == "span-456"
    assert data["score_refs"] == [
        {"evaluator_version_id": "eval-v1", "score": 1.0}
    ]


def test_experiment_run_item_has_one_result_per_example_and_trial():
    constraints = {
        tuple(constraint.fields)
        for constraint in ExperimentRunItem._meta.constraints
    }

    assert ("run", "example_id", "trial_number") in constraints
