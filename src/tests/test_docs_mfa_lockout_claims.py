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

Besides these known wrong phrasings, the guard ties the numbers the
documented contract relies on to their single source in
``anlaufstelle/settings/base.py``: the defaults of ``SUDO_MODE_TTL_SECONDS``
and ``OFFLINE_REVOCATION_SESSION_SCAN_LIMIT`` in the admin-guide variable
tables, and the sudo window the recovery steps name ("second press within
the sudo window"). The step sequence itself is behaviour and is pinned over
HTTP by ``test_mfa_recovery_and_revocation_contract.py``; a free-text match
of the steps would only check wording, so the guard does not try.
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
    Path("docs/adr/032-webauthn-passkeys-2fa.md"),
)
ADMIN_GUIDES = (Path("docs/admin-guide.md"), Path("docs/en/admin-guide.md"))
SETTINGS_SOURCE = Path("src/anlaufstelle/settings/base.py")
DOCUMENTED_DEFAULTS = ("SUDO_MODE_TTL_SECONDS", "OFFLINE_REVOCATION_SESSION_SCAN_LIMIT")
SUDO_WINDOW_IN_STEPS = re.compile(r"`SUDO_MODE_TTL_SECONDS`, (?:Standard|default) (\d+) (?:Minuten|minutes)\)")
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


LOCKOUT_DOCUMENTS = (
    Path("docs/user-guide.md"),
    Path("docs/en/user-guide.md"),
    Path("docs/faq.md"),
    Path("docs/glossar.md"),
    Path("docs/en/glossary.md"),
    Path("docs/admin-guide.md"),
    Path("docs/en/admin-guide.md"),
    Path("docs/security-notes.md"),
    Path("docs/threat-model.md"),
    Path("docs/adr/015-mfa-totp.md"),
    Path("SECURITY.md"),
)
LOCKOUT_PROMISE = re.compile(
    r"(?:10[^.\n]{0,100}(?:fehlgeschlagen|failed)[^.\n]{0,180}(?:gesperrt|sperren|locked))"
    r"|(?:(?:Konto-Sperre|account lock)[^.\n]{0,80}10[^.\n]{0,60}(?:fehlgeschlagen|failed))"
    r"|(?:Account-Lockout|account lockout)[^.\n]{0,40}10/15"
    r"|Account-Lockout-Achse",
    re.IGNORECASE,
)
LOCKOUT_EXCEPTION = re.compile(r"ausgenommen|exempt|nie gesperrt|not (?:be )?locked", re.IGNORECASE)


def _lockout_promises_without_exception(*, root: Path) -> list[str]:
    """Require the super-admin exception next to each general lockout promise."""
    findings = []
    for relative_path in LOCKOUT_DOCUMENTS:
        text = (root / relative_path).read_text(encoding="utf-8")
        for paragraph in re.split(r"\n\s*\n", text):
            blocks = paragraph.splitlines() if paragraph.startswith("|") else [paragraph]
            for block in blocks:
                if LOCKOUT_PROMISE.search(block) and not ("super_admin" in block and LOCKOUT_EXCEPTION.search(block)):
                    findings.append(f"{relative_path}: lockout promise lacks the super_admin exception")
    return findings


@pytest.mark.parametrize(
    "claim",
    [
        "Nach 10 fehlgeschlagenen Anmeldungen innerhalb von 15 Minuten wird Ihr Konto gesperrt.",
        "After 10 failed sign-in attempts within 15 minutes your account is locked.",
        "Automatische Konto-Sperre nach 10 fehlgeschlagenen Login-Versuchen.",
        "Automatic account lock after 10 failed login attempts.",
    ],
)
def test_lockout_scanner_rejects_a_promise_without_the_role_exception(tmp_path: Path, claim: str) -> None:
    for relative_path in LOCKOUT_DOCUMENTS:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    target = LOCKOUT_DOCUMENTS[0]
    (tmp_path / target).write_text(claim, encoding="utf-8")

    assert _lockout_promises_without_exception(root=tmp_path) == [
        f"{target}: lockout promise lacks the super_admin exception"
    ]

    (tmp_path / target).write_text(claim + " `super_admin` is exempt.", encoding="utf-8")

    assert _lockout_promises_without_exception(root=tmp_path) == []


def test_shipped_lockout_promises_state_the_super_admin_exception() -> None:
    assert _lockout_promises_without_exception(root=REPO_ROOT) == []


def _settings_default(name: str, *, root: Path) -> int:
    source = (root / SETTINGS_SOURCE).read_text(encoding="utf-8")
    return int(re.search(rf'^{name} = env_int\("{name}", (\d+)\)', source, re.MULTILINE).group(1))


def _contract_value_mismatches(*, root: Path) -> list[str]:
    findings = []
    sudo_minutes = _settings_default("SUDO_MODE_TTL_SECONDS", root=root) // 60
    for relative_path in ADMIN_GUIDES:
        text = (root / relative_path).read_text(encoding="utf-8")
        for name in DOCUMENTED_DEFAULTS:
            row = re.search(rf"^\| `{name}` \| `(\d+)` \|", text, re.MULTILINE)
            expected = _settings_default(name, root=root)
            if row is None or int(row.group(1)) != expected:
                findings.append(f"{relative_path}: {name} default is not `{expected}`")
        windows = [int(minutes) for minutes in SUDO_WINDOW_IN_STEPS.findall(text)]
        if not windows or any(minutes != sudo_minutes for minutes in windows):
            findings.append(f"{relative_path}: recovery steps do not name the {sudo_minutes}-minute sudo window")
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


def _write_contract_tree(root: Path, *, ttl: int, scan_limit: int, documented_minutes: int | None) -> None:
    settings_path = root / SETTINGS_SOURCE
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        f'SUDO_MODE_TTL_SECONDS = env_int("SUDO_MODE_TTL_SECONDS", {ttl})\n'
        f'OFFLINE_REVOCATION_SESSION_SCAN_LIMIT = env_int("OFFLINE_REVOCATION_SESSION_SCAN_LIMIT", {scan_limit})\n',
        encoding="utf-8",
    )
    steps = "" if documented_minutes is None else f"(`SUDO_MODE_TTL_SECONDS`, Standard {documented_minutes} Minuten)"
    for relative_path in ADMIN_GUIDES:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "| `SUDO_MODE_TTL_SECONDS` | `900` | x |\n"
            "| `OFFLINE_REVOCATION_SESSION_SCAN_LIMIT` | `10000` | x |\n"
            f"{steps}\n",
            encoding="utf-8",
        )


def test_contract_values_accept_matching_tree(tmp_path: Path) -> None:
    _write_contract_tree(tmp_path, ttl=900, scan_limit=10000, documented_minutes=15)

    assert _contract_value_mismatches(root=tmp_path) == []


@pytest.mark.parametrize(
    ("ttl", "scan_limit", "documented_minutes", "expected_fragment"),
    [
        (600, 10000, 10, "SUDO_MODE_TTL_SECONDS default"),
        (900, 5000, 15, "OFFLINE_REVOCATION_SESSION_SCAN_LIMIT default"),
        (900, 10000, 20, "15-minute sudo window"),
        (900, 10000, None, "15-minute sudo window"),
    ],
)
def test_contract_values_detect_drift(
    tmp_path: Path, ttl: int, scan_limit: int, documented_minutes: int | None, expected_fragment: str
) -> None:
    _write_contract_tree(tmp_path, ttl=ttl, scan_limit=scan_limit, documented_minutes=documented_minutes)

    findings = _contract_value_mismatches(root=tmp_path)

    assert len(findings) == len(ADMIN_GUIDES)
    assert all(expected_fragment in finding for finding in findings)


def test_shipped_docs_state_the_contract_values_of_the_code() -> None:
    assert _contract_value_mismatches(root=REPO_ROOT) == []
