"""Contract of the snapshot carry-over for deleted events vs. reporting-plugins docs.

A missing ``_SUMMARY_MERGERS`` entry does not fail the first carry-over of a
slug (the summary is stored unchanged); only merging into an existing
aggregate looks the merger up. The docs must not promise a fail-closed check.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.services.dashboard import snapshot

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = {
    "de": (
        REPO_ROOT / "docs/reporting-plugins.md",
        "Fehlt eines von beidem",
        "wird aber nicht beim ersten Übertrag geprüft",
    ),
    "en": (REPO_ROOT / "docs/en/reporting-plugins.md", "If either is missing", "not checked at the first carry-over"),
}


def _patch_backend(monkeypatch):
    backend = SimpleNamespace(slug="no_merger", snapshot_enabled=True, summarize_events=lambda events: {"total": 1})
    monkeypatch.setattr(snapshot, "get_backends", lambda: [backend])
    monkeypatch.setattr(snapshot, "get_statistics_for_events", lambda events: {})
    monkeypatch.setattr(snapshot, "_merge_stats", lambda parts: {})
    monkeypatch.setattr(snapshot, "reporting_statement_timeout", contextlib.nullcontext)
    monkeypatch.setattr(snapshot, "_SUMMARY_MERGERS", {})


def test_missing_merger_fails_only_on_second_carry(monkeypatch):
    _patch_backend(monkeypatch)
    first = snapshot._add_deleted_events({}, object())
    assert first["no_merger"] == {"total": 1}
    with pytest.raises(KeyError, match="no_merger"):
        snapshot._add_deleted_events(first, object())


@pytest.mark.parametrize("lang", sorted(DOCS))
def test_docs_state_merger_contract_precisely(lang):
    path, old_claim, new_claim = DOCS[lang]
    text = path.read_text(encoding="utf-8")
    assert old_claim not in text, f"{path.name}: promises a fail-closed merger check"
    assert new_claim in text
