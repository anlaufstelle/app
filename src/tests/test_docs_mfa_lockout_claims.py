"""Guards: the lockout, 2FA and factor-reset docs describe what the code does.

Each forbidden claim below promised more than the implementation delivers:

- the lock does not end at a fixed time after the last failure, it ends once
  fewer than ten counted failures remain in the sliding window
  (``core.services.security.login_lockout.is_locked``);
- a rate-limited request (HTTP 429) never reaches the form, so it is neither
  counted nor audited (``core.views.auth.CustomLoginView.post``);
- a sign-in against a lock ends only the sessions the bounded scan finds
  (``core.services.security.credentials.flush_user_sessions``);
- not every 2FA event writes an audit entry (a successful TOTP verification
  does not, ``core.views.mfa.MFAVerifyView.post``);
- an existing TOTP device cannot be swapped through the UI
  (``core.views.mfa.MFASetupView.get`` redirects to the settings page);
- the reset audit detail carries account identifiers besides the counters
  (``core.services.user_access.reset_user_mfa``);
- the factor reset does not depend on the session scan limit
  (``core.models.user.User.revoke_sessions``).
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = (
    Path("docs/admin-guide.md"),
    Path("docs/en/admin-guide.md"),
    Path("docs/user-guide.md"),
    Path("docs/en/user-guide.md"),
    Path("docs/faq.md"),
    Path("docs/glossar.md"),
    Path("docs/en/glossary.md"),
    Path("docs/adr/015-mfa-totp.md"),
)
AUDIT_TRAIL_SECTIONS = (
    (Path("docs/admin-guide.md"), "#### Audit-Spur"),
    (Path("docs/en/admin-guide.md"), "#### Audit Trail"),
)
AUDITED_2FA_PREFIXES = ("MFA_", "BACKUP_CODES_", "WEBAUTHN_", "SUDO_MODE_")

FORBIDDEN_CLAIMS = (
    # Lock end as a fixed deadline after the last failure.
    re.compile(r"(?:endet|also)(?: daher)?(?: von selbst)?(?:\*\*)? 15 Minuten nach dem letzten Fehlversuch"),
    re.compile(r"(?:ends|that is,)(?: by itself)? 15 minutes after the last failed attempt"),
    # Every attempt extends the lock (rate-limited attempts are not counted).
    re.compile(r"(?:jeder|Weitere) (?:weitere )?Versuche?\b[^.]{0,60}verlänger"),
    re.compile(r"(?:every|Further) (?:further )?attempts?\b[^.]{0,60}extend", re.IGNORECASE),
    # The lock's form error appears only after the hourly rate window.
    re.compile(r"erst nach Ablauf des Stundenfensters"),
    re.compile(r"only after the hourly window"),
    # A sign-in against the lock ends all running sessions.
    re.compile(r"laufende Sitzungen des Kontos werden beendet"),
    re.compile(r"running sessions of the account are ended"),
    # Blanket completeness of the audit trail.
    re.compile(r"Alle 2FA-Vorgänge werden"),
    re.compile(r"All 2FA events are recorded"),
    re.compile(r"alle Versuche während der Sperrphase"),
    re.compile(r"any attempts during the lock window"),
    # Swapping an existing TOTP device through the UI.
    re.compile(r"ein neues TOTP-Gerät und neue Backup-Codes ein"),
    re.compile(r"Der Wechsel des TOTP-Geräts ist ein eigener Schritt"),
    re.compile(r"landet danach wieder auf `/mfa/setup/`"),
    re.compile(r"then returns to `/mfa/setup/`"),
    # Reset audit detail without the account identifiers.
    re.compile(r"nur die Anzahl der entfernten"),
    re.compile(r"only the number of removed"),
    # Factor reset listed as a user of the session scan.
    re.compile(r"Zurücksetzen des zweiten Faktors\) durchsucht"),
    re.compile(r"second-factor reset\); exceeding"),
)


def _overpromises(*, root: Path) -> list[str]:
    findings = []
    for relative_path in DOCUMENTS:
        text = (root / relative_path).read_text(encoding="utf-8")
        for pattern in FORBIDDEN_CLAIMS:
            if pattern.search(text):
                findings.append(f"{relative_path}: {pattern.pattern}")
    return findings


def _section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    end = text.find("\n#", start)
    return text[start:] if end == -1 else text[start:end]


def _documented_audit_actions(*, root: Path, relative_path: Path, heading: str) -> set[str]:
    section = _section((root / relative_path).read_text(encoding="utf-8"), heading)
    return {name for name in re.findall(r"`([A-Z][A-Z_]+)`", section) if name.startswith(AUDITED_2FA_PREFIXES)}


@pytest.mark.parametrize("pattern", FORBIDDEN_CLAIMS, ids=lambda pattern: pattern.pattern[:40])
def test_scanner_detects_each_overpromise(tmp_path: Path, pattern: re.Pattern) -> None:
    samples = {
        r"(?:endet|also)(?: daher)?(?: von selbst)?(?:\*\*)? 15 Minuten nach dem letzten Fehlversuch": (
            "endet von selbst** 15 Minuten nach dem letzten Fehlversuch"
        ),
        r"(?:ends|that is,)(?: by itself)? 15 minutes after the last failed attempt": (
            "ends by itself 15 minutes after the last failed attempt"
        ),
        r"(?:jeder|Weitere) (?:weitere )?Versuche?\b[^.]{0,60}verlänger": "jeder weitere Versuch verlängert sie",
        r"(?:every|Further) (?:further )?attempts?\b[^.]{0,60}extend": "every further attempt extends it",
        r"Zurücksetzen des zweiten Faktors\) durchsucht": "Zurücksetzen des zweiten Faktors) durchsucht",
        r"second-factor reset\); exceeding": "second-factor reset); exceeding",
    }
    for relative_path in DOCUMENTS:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Ist-Stand", encoding="utf-8")
    (tmp_path / DOCUMENTS[0]).write_text(samples.get(pattern.pattern, pattern.pattern), encoding="utf-8")

    assert _overpromises(root=tmp_path) == [f"{DOCUMENTS[0]}: {pattern.pattern}"]


def test_scanner_accepts_the_bounded_lock_end(tmp_path: Path) -> None:
    for relative_path in DOCUMENTS:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "endet spätestens 15 Minuten nach dem letzten Fehlversuch, früher als 15 Minuten nach dem letzten "
            "Fehlversuch / ends at the latest 15 minutes after the last failed attempt",
            encoding="utf-8",
        )

    assert _overpromises(root=tmp_path) == []


def test_shipped_docs_do_not_overpromise_lockout_and_2fa_behaviour() -> None:
    assert _overpromises(root=REPO_ROOT) == []


@pytest.mark.parametrize(("relative_path", "heading"), AUDIT_TRAIL_SECTIONS, ids=lambda value: str(value))
def test_audit_trail_section_lists_exactly_the_2fa_and_sudo_actions(relative_path: Path, heading: str) -> None:
    from core.models import AuditLog

    expected = {name for name in AuditLog.Action.names if name.startswith(AUDITED_2FA_PREFIXES)}

    assert _documented_audit_actions(root=REPO_ROOT, relative_path=relative_path, heading=heading) == expected
