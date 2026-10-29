"""Pins for monotone projection of document-type datasets.

Source: mutation delta of the release-0.99.9 checkpoint, 9 mutants.
Groups: record suppression (4), total suppression (3), absent type tables (2).
"""

from datetime import date
from types import SimpleNamespace

import pytest

from core.reporting.base import DatasetResult
from core.services.dashboard import shared_disclosure

START, END = date(2026, 1, 1), date(2026, 1, 31)


def dataset(records, total):
    return DatasetResult(
        mode="aggregate",
        records=records,
        summary={"total": total},
        completeness=[],
        disclosure_context=(START, END, "live"),
    )


def test_records_preserve_local_masks_and_apply_shared_masks(monkeypatch):
    """Keep safe counts and both suppression sources: x_project_shared_type_dataset__mutmut_27,
    x_project_shared_type_dataset__mutmut_28, x_project_shared_type_dataset__mutmut_33,
    x_project_shared_type_dataset__mutmut_51.
    """
    general = {
        "total_contacts": 21,
        "by_document_type": [
            {"name": "visible", "count": 7},
            {"name": "local-hidden", "count": 7},
            {"name": "shared-hidden", "count": None},
        ],
    }
    monkeypatch.setattr(
        shared_disclosure, "build_live_release", lambda *args, **kwargs: SimpleNamespace(general=general)
    )
    original = dataset(
        [
            {"document_type": "visible", "count": 7},
            {"document_type": "local-hidden", "count": None},
            {"document_type": "shared-hidden", "count": 6},
            {"document_type": "removed", "count": 9},
        ],
        30,
    )

    projected = shared_disclosure.project_shared_type_dataset(SimpleNamespace(pk=37), original)

    assert projected.records == [
        {"document_type": "visible", "count": 7},
        {"document_type": "local-hidden", "count": None},
        {"document_type": "shared-hidden", "count": None},
    ]
    assert original.records[0]["count"] == 7
    assert original.records[-1]["document_type"] == "removed"


@pytest.mark.parametrize("local_total, expected", [(21, 21), (None, None)])
def test_total_preserves_local_suppression_and_uses_the_shared_value(monkeypatch, local_total, expected):
    """Preserve total availability: x_project_shared_type_dataset__mutmut_40,
    x_project_shared_type_dataset__mutmut_41, x_project_shared_type_dataset__mutmut_46.
    """
    general = {"total_contacts": 21, "by_document_type": []}
    monkeypatch.setattr(
        shared_disclosure, "build_live_release", lambda *args, **kwargs: SimpleNamespace(general=general)
    )
    projected = shared_disclosure.project_shared_type_dataset(SimpleNamespace(pk=37), dataset([], local_total))
    assert projected.summary["total"] == expected


def test_absent_shared_type_table_removes_records_without_crashing(monkeypatch):
    """Handle a fully withheld table: x_project_shared_type_dataset__mutmut_19,
    x_project_shared_type_dataset__mutmut_21.
    """
    general = {"total_contacts": None}
    monkeypatch.setattr(
        shared_disclosure, "build_live_release", lambda *args, **kwargs: SimpleNamespace(general=general)
    )
    original = dataset([{"document_type": "removed", "count": 7}], 7)
    projected = shared_disclosure.project_shared_type_dataset(SimpleNamespace(pk=37), original)
    assert projected.records == []
    assert projected.summary["total"] is None
