"""Contract of the snapshot carry-over for deleted events vs. reporting-plugins docs.

Without a ``_SUMMARY_MERGERS`` entry any carry-over may fail, including the
first one into an empty monthly row (the row is recomputed in full and live
and carried summaries are merged). The docs must make the merger a duty of the
plugin and must not promise a carry-over that succeeds without one.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.services.dashboard import snapshot

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = {
    "de": (
        REPO_ROOT / "docs/reporting-plugins.md",
        ("muss auch einen Merger registrieren", "kann der Übertrag scheitern", "versucht es erneut"),
        ("gelingt auch ohne Merger", "nicht beim ersten Übertrag geprüft", "erst der zweite", "Fehlt eines von beidem"),
    ),
    "en": (
        REPO_ROOT / "docs/en/reporting-plugins.md",
        ("must also register a merger", "the carry-over can fail", "retries"),
        (
            "succeeds even without a merger",
            "not checked at the first carry-over",
            "only the second",
            "If either is missing",
        ),
    ),
}


def _patch_backend(monkeypatch):
    backend = SimpleNamespace(
        slug="no_merger",
        snapshot_enabled=True,
        summarize_events=lambda events: {"total": 1},
        build_dataset=lambda *args: SimpleNamespace(summary={"total": 0}),
    )
    monkeypatch.setattr(snapshot, "get_backends", lambda: [backend])
    monkeypatch.setattr(snapshot, "get_statistics_for_events", lambda events: {})
    monkeypatch.setattr(snapshot, "get_statistics", lambda *args: {})
    monkeypatch.setattr(snapshot, "_merge_stats", lambda parts: {})
    monkeypatch.setattr(snapshot, "reporting_statement_timeout", contextlib.nullcontext)
    monkeypatch.setattr(snapshot, "_SUMMARY_MERGERS", {})
    events = SimpleNamespace(filter=lambda **kwargs: object())
    monkeypatch.setattr(snapshot, "Event", SimpleNamespace(objects=SimpleNamespace(for_facility=lambda f: events)))


def _carry(monkeypatch, data):
    saves = []
    row = SimpleNamespace(year=2026, month=9, data=data, backend_data={}, save=lambda **kw: saves.append(kw))
    _patch_backend(monkeypatch)
    monkeypatch.setattr(snapshot, "_lock_snapshot_row", lambda *args: row)
    event = SimpleNamespace(pk=1, occurred_at=datetime(2026, 9, 1, tzinfo=UTC))
    snapshot.carry_deleted_event(SimpleNamespace(name="Facility"), event)
    return row, saves


def test_first_carry_into_empty_monthly_row_needs_merger(monkeypatch):
    with pytest.raises(snapshot.SnapshotCarryError) as failure:
        _carry(monkeypatch, {})
    assert failure.value.__cause__.args == ("no_merger",)


def test_carry_into_populated_row_without_prior_aggregate_needs_no_merger(monkeypatch):
    row, saves = _carry(monkeypatch, {"total": 2})
    assert row.backend_data[snapshot.SNAPSHOT_DELETED_KEY]["no_merger"] == {"total": 1}
    assert len(saves) == 1


def test_missing_merger_fails_on_second_carry(monkeypatch):
    _patch_backend(monkeypatch)
    first = snapshot._add_deleted_events({}, object())
    with pytest.raises(KeyError, match="no_merger"):
        snapshot._add_deleted_events(first, object())


@pytest.mark.parametrize("lang", sorted(DOCS))
def test_docs_make_merger_a_plugin_duty_without_success_promise(lang):
    path, required, forbidden = DOCS[lang]
    text = path.read_text(encoding="utf-8")
    for phrase in forbidden:
        assert phrase not in text, f"{path.name}: promises a carry-over without merger: {phrase!r}"
    for phrase in required:
        assert phrase in text, f"{path.name}: missing {phrase!r}"
