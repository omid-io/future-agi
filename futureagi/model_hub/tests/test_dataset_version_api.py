import uuid

import pytest
from django.urls import reverse
from rest_framework import status

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset


@pytest.fixture
def version_api_dataset(organization, workspace, user):
    return Dataset.objects.create(
        name="Version API dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )


@pytest.mark.django_db
def test_dataset_version_list_is_scoped_and_reports_item_count(
    auth_client, version_api_dataset
):
    version = DatasetVersion.objects.create(
        dataset=version_api_dataset,
        description="Evaluation baseline",
        source_revision="git:abc123",
    )
    DatasetVersionItem.objects.create(
        dataset_version=version,
        input={"question": "2 + 2?"},
        expected={"answer": "4"},
    )
    DatasetVersionItem.objects.create(
        dataset_version=version,
        input={"question": "3 + 3?"},
        deleted=True,
    )

    response = auth_client.get(
        reverse("dataset-version-list", kwargs={"dataset_id": version_api_dataset.id})
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["dataset_id"] == str(version_api_dataset.id)
    assert payload["versions"][0]["id"] == str(version.id)
    assert payload["versions"][0]["item_count"] == 1
    assert payload["versions"][0]["source_revision"] == "git:abc123"


@pytest.mark.django_db
def test_dataset_version_detail_returns_provenance_and_bounded_items(
    auth_client, version_api_dataset
):
    version = DatasetVersion.objects.create(dataset=version_api_dataset)
    first = DatasetVersionItem.objects.create(
        dataset_version=version,
        example_id=uuid.uuid4(),
        external_id="case-1",
        input={"question": "hello"},
        expected={"answer": "world"},
        metadata={"split": "eval"},
        source_trace_id="trace-1",
        source_span_id="span-1",
    )
    DatasetVersionItem.objects.create(
        dataset_version=version,
        input={"question": "second"},
    )

    response = auth_client.get(
        reverse(
            "dataset-version-detail",
            kwargs={"dataset_id": version_api_dataset.id, "version_id": version.id},
        ),
        {"limit": 1},
    )

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()["result"]
    assert payload["version"]["id"] == str(version.id)
    assert payload["items"][0]["id"] == str(first.id)
    assert payload["items"][0]["source_trace_id"] == "trace-1"
    assert payload["items"][0]["source_span_id"] == "span-1"
    assert payload["items"][0]["content_hash"] == first.content_hash
    assert payload["has_more"] is True


@pytest.mark.django_db
def test_dataset_version_detail_rejects_invalid_page_parameters(
    auth_client, version_api_dataset
):
    version = DatasetVersion.objects.create(dataset=version_api_dataset)

    response = auth_client.get(
        reverse(
            "dataset-version-detail",
            kwargs={"dataset_id": version_api_dataset.id, "version_id": version.id},
        ),
        {"limit": 0},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
