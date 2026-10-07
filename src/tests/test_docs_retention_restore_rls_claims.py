"""Guards: retention, restore and RLS docs describe what the code does.

Each forbidden claim below promised more than the implementation delivers:

- the retention soft delete already empties ``data_json`` and ``search_text``
  and removes the attachments (``core.retention.enforcement._enforce``); the
  contents do not wait for the anonymization of the person;
- the qualified period counts from the closing of the case
  (``core.retention.enforcement`` ``closed_at__lt``), while ``prune_auditlog``
  counts from each audit row's own timestamp, so an audit period equal to the
  longest domain period does not cover the lifetime of the domain data;
- ``scripts/ops/restore.sh`` checks the bound audit anchor only when
  ``DJANGO_AUDIT_HWM_PATH`` is set on the target host, otherwise it only warns;
- the RLS policies let rows through for ``app.is_super_admin = 'true'`` even
  with an empty facility scope (migration ``0085_rls_superadmin_bypass``), and
  ``FacilityScopeMiddleware`` empties the scope of an archived facility only
  for blocked requests (``_is_archived_blocked``);
- the history sweep after a soft delete is not unconditional: rows whose person
  or event lock is taken (``_lock_rows`` uses ``skip_locked``) or whose event
  moved to another person meanwhile are skipped, as are held ones, and only a
  later run redacts them (``core.retention.eventhistory``). A sentence about the
  history step that promises "immediately"/"same run" must name that deferral.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = (
    Path("docs/admin-guide.md"),
    Path("docs/en/admin-guide.md"),
    Path("docs/adr/021-retention-modell.md"),
)

FORBIDDEN_CLAIMS = (
    # Soft-deleted event contents kept until the person is anonymized.
    re.compile(r"Inhalte fallen mit der Anonymisierung der Person"),
    re.compile(r"contents go with the anonymization of the person"),
    # Qualified period counted like an age limit of the event.
    re.compile(r"bleiben dagegen bis zu 3650 Tage"),
    re.compile(r"remain for up to 3650 days"),
    re.compile(r"das Ereignis selbst fällt erst nach zehn"),
    # Audit period equal to the longest domain period as lifetime coverage.
    re.compile(r"Lebensdauer der Fachdaten braucht, setzt"),
    re.compile(r"entire lifetime of the domain data sets"),
    # Unconditional audit-anchor preflight on restore.
    re.compile(r"Medien-Hash samt vorhandenem Sidecar, Audit-Anker\)"),
    re.compile(r"media hash including an existing sidecar, audit anchor\)"),
    # Empty facility scope as an unconditional "no rows".
    re.compile(r"Fail-closed:\*\* Ist die Variable leer oder nicht gesetzt"),
    re.compile(r"Fail-closed:\*\* if the variable is empty or unset"),
    # Archived facility always runs with an empty scope.
    re.compile(r"für Personen einer archivierten Einrichtung\) sind beide leer"),
    re.compile(r"for people of an archived facility\) both are empty"),
)


HISTORY_STEP_SENTENCE = re.compile(r"[^.]*(?:Historien-Schritt|history step)[^.]*\.")
IMMEDIACY = re.compile(r"sofort|im selben Lauf|right away|immediately|same run")
DEFERRAL = re.compile(r"spätere[nr]? Lauf|later run")


def _unqualified_history_immediacy(*, root: Path) -> list[str]:
    findings = []
    for relative_path in DOCUMENTS:
        text = (root / relative_path).read_text(encoding="utf-8")
        for sentence in HISTORY_STEP_SENTENCE.findall(text):
            if IMMEDIACY.search(sentence) and not DEFERRAL.search(sentence):
                findings.append(f"{relative_path}: {sentence.strip()[:80]}")
    return findings


def _overpromises(*, root: Path) -> list[str]:
    findings = []
    for relative_path in DOCUMENTS:
        text = (root / relative_path).read_text(encoding="utf-8")
        for pattern in FORBIDDEN_CLAIMS:
            if pattern.search(text):
                findings.append(f"{relative_path}: {pattern.pattern}")
    return findings


def _write_tree(root: Path, first_document_text: str) -> None:
    for relative_path in DOCUMENTS:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Ist-Stand", encoding="utf-8")
    (root / DOCUMENTS[0]).write_text(first_document_text, encoding="utf-8")


@pytest.mark.parametrize("pattern", FORBIDDEN_CLAIMS, ids=lambda pattern: pattern.pattern[:40])
def test_scanner_detects_each_overpromise(tmp_path: Path, pattern: re.Pattern) -> None:
    _write_tree(tmp_path, pattern.pattern.replace("\\", ""))

    assert _overpromises(root=tmp_path) == [f"{DOCUMENTS[0]}: {pattern.pattern}"]


def test_scanner_accepts_the_conditional_phrasings(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        "bis zu 3650 Tage nach Fallabschluss; die Inhalte fallen schon beim Soft-Delete; "
        "Audit-Anker nur mit gesetztem `DJANGO_AUDIT_HWM_PATH`",
    )

    assert _overpromises(root=tmp_path) == []


def test_shipped_docs_do_not_overpromise_retention_restore_and_rls() -> None:
    assert _overpromises(root=REPO_ROOT) == []


@pytest.mark.parametrize(
    "sentence",
    (
        "Die Fassungen redigiert derselbe Lauf im Historien-Schritt sofort (unter Legal Hold zurückgestellt).",
        "The versions are redacted in the subsequent history step right away (deferred under a legal hold).",
    ),
)
def test_history_scanner_rejects_immediacy_without_deferral(tmp_path: Path, sentence: str) -> None:
    _write_tree(tmp_path, sentence)

    assert len(_unqualified_history_immediacy(root=tmp_path)) == 1


def test_history_scanner_accepts_immediacy_with_later_run(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        "Im Historien-Schritt sofort; gesperrte Zeilen holt ein späterer Lauf nach. "
        "In the history step right away; a later run catches up.",
    )

    assert _unqualified_history_immediacy(root=tmp_path) == []


def test_shipped_docs_name_the_history_step_deferrals() -> None:
    assert _unqualified_history_immediacy(root=REPO_ROOT) == []
