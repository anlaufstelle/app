"""Architecture-Guards — Audit-Completeness, Settings-Allowlist, E2E-Selektor-Stabilität (Refs #929)."""

import ast
import re
from pathlib import Path
from typing import ClassVar

import pytest

pytestmark = pytest.mark.architecture


class TestSettingsAuditCompletenessGuard:
    """Refs #900: jedes Feld auf ``Settings`` muss entweder
    auditiert (``_AUDIT_FIELDS``) oder explizit ausgenommen sein
    (``_AUDIT_EXEMPT``). Verhindert, dass ein neues verhaltensrelevantes
    Setting unauditiert gemerged wird.
    """

    def _settings_fields(self):
        from core.models import Settings

        names = []
        for f in Settings._meta.get_fields():
            # Reverse-Relationen (related_name-Zugriffe) zaehlen nicht — sie
            # haben kein Datenbank-Spaltenpendant auf Settings.
            if not getattr(f, "concrete", False):
                continue
            # M2M-Through und virtuelle Felder ueberspringen — sie haben
            # entweder keine ``column`` oder sind reine Manager-Konstrukte.
            if getattr(f, "column", None) is None:
                continue
            names.append(f.name)
        return set(names)

    def test_no_overlap_between_audit_fields_and_exempt(self):
        from core.services.system import _AUDIT_EXEMPT, _AUDIT_FIELDS

        overlap = set(_AUDIT_FIELDS) & set(_AUDIT_EXEMPT)
        assert not overlap, (
            f"_AUDIT_FIELDS und _AUDIT_EXEMPT duerfen nicht ueberlappen — "
            f"sonst wird ein 'auditiertes' Feld gleichzeitig als 'irrelevant' "
            f"deklariert. Konflikt: {sorted(overlap)}"
        )

    def test_audit_fields_reference_existing_model_fields(self):
        from core.services.system import _AUDIT_FIELDS

        existing = self._settings_fields()
        missing = [name for name in _AUDIT_FIELDS if name not in existing]
        assert not missing, (
            f"_AUDIT_FIELDS verweist auf nicht-existierende Settings-Felder: "
            f"{missing}. Wahrscheinlich Tippfehler oder veralteter Eintrag — "
            f"existierende Felder: {sorted(existing)}"
        )

    def test_audit_exempt_references_existing_model_fields(self):
        from core.services.system import _AUDIT_EXEMPT

        existing = self._settings_fields()
        missing = [name for name in _AUDIT_EXEMPT if name not in existing]
        assert not missing, (
            f"_AUDIT_EXEMPT verweist auf nicht-existierende Settings-Felder: "
            f"{missing}. Existierende Felder: {sorted(existing)}"
        )

    def test_every_settings_field_is_classified(self):
        """Jedes konkrete Feld auf Settings muss explizit klassifiziert sein.

        Failt, wenn jemand ein neues Feld zu Settings hinzufuegt, aber
        weder in ``_AUDIT_FIELDS`` noch in ``_AUDIT_EXEMPT`` deklariert.
        Dann muss bewusst entschieden werden: auditieren oder begruendet
        ausnehmen — beides ist ok, aber stillschweigendes Uebergehen nicht.
        """
        from core.services.system import _AUDIT_EXEMPT, _AUDIT_FIELDS

        classified = set(_AUDIT_FIELDS) | set(_AUDIT_EXEMPT)
        existing = self._settings_fields()
        unclassified = sorted(existing - classified)
        assert not unclassified, (
            f"Unklassifizierte Settings-Felder: {unclassified}. "
            f"Bitte zu ``_AUDIT_FIELDS`` in src/core/services/settings.py "
            f"hinzufuegen (verhaltensrelevant — DSGVO/MFA/Retention/Suche/"
            f"Datei-Policy) oder zu ``_AUDIT_EXEMPT`` mit Kommentar "
            f"(z.B. PrimaryKey, auto_now). Refs #893."
        )


class TestAuditLogCreationAllowlist:
    """Refs #901: direkte ``AuditLog.objects.create(...)``-Aufrufe
    sind nur an dokumentierten Stellen erlaubt. Alles andere muss ueber die
    typed Helper aus ``core.services.audit`` laufen
    (``log_audit_event``, ``audit_event``, ``audit_client_event``,
    ``audit_retention_decision``, ``audit_security_violation``,
    ``audit_system_view``).

    Die Allowlist enthaelt aktuell drei Kategorien:

    1. **Helper-Bodies** in ``services/audit/helpers.py`` und ``services/settings.py``
       — die Helper selbst muessen am Ende ``.objects.create(...)`` rufen.
    2. **Signal-Handler** in ``signals/audit.py`` — laufen in Post-Save-
       Signalen ohne Request-Objekt und setzen RLS-Session-Variablen
       manuell. Migration auf Helper waere Schein-Aufwand.
    3. **Migrationsstandorte** waehrend des #901-Refactors — werden in
       atomaren Schritten in 8 Commits abgeraeumt. Endzustand siehe S9
       des Plans.
    """

    _CORE_DIR = Path("src/core")
    _PATTERN = re.compile(r"AuditLog\.objects\.create\(")

    # Allowlist: { relative-to-src/-path: { line_number: "Begruendung" } }.
    # Aenderungen am Code, die die Zeilennummern eines erlaubten Eintrags
    # verschieben, MUESSEN die Allowlist mit aktualisieren — sonst meldet
    # der Test wieder einen "Verstoss".
    _ALLOWED_DIRECT_CALLS: dict[str, dict[int, str]] = {
        # Helper-Bodies — by design.
        "core/services/audit/__init__.py": {
            27: "Docstring-Hinweis — kein echter Call (Refs #959)",
        },
        "core/services/audit/helpers.py": {
            4: "Docstring-Beispiel — kein echter Call",
            108: "log_audit_event body — central request helper",
            152: "audit_event body — generic service/cron helper",
        },
        "core/services/system/settings.py": {
            # Refs #2129 + #2136: +8 Zeilen durch ``max_storage_mb`` und +12 Zeilen
            # durch ``quick_exit_url``/``privacy_screen_on_blur`` in _AUDIT_FIELDS
            # und _AUDIT_VALUE_FIELDS (144 -> 164, 173 -> 193).
            # Refs #2042: +9 Zeilen durch ``retention_handover_notes_days`` in
            # denselben beiden Listen (164 -> 173, 193 -> 202). Reine Zeilen-
            # Nachfuehrung, keine neue Schreibstelle.
            # Refs #2525: +1 line for the protection gate before the version
            # check in ``update_settings`` (173 -> 174, 202 -> 203).
            # Refs #2524: two review settings in both audit field lists shift
            # the existing helpers by four lines; no new write site is allowed.
            # Refs #2524: the request period shifts the existing write sites by
            # two more lines; no new write site is allowed.
            180: "update_settings body — settings diff helper",
            209: "log_settings_change body — second create call",
        },
        # Signal-Handler: kein Request, RLS-Session-State explizit gesetzt.
        "core/signals/audit.py": {
            115: "on_user_logged_in — auth signal, kein Request",
            134: "on_user_logged_out — auth signal",
            167: "on_user_login_failed — no-facility branch",
            185: "on_user_login_failed — with-facility branch",
            269: "post_save User — request-driven creation (Refs #1871)",
            297: "post_save User — role change detection (actor attribution, Refs #1369)",
            316: "post_save User — deactivation detection (actor attribution, Refs #1369)",
            348: "post_save User — MFA requirement change (Refs #2172)",
            369: "post_save User — deletion approval permission change (Refs #1053/#1369)",
        },
    }

    def test_only_allowlisted_direct_auditlog_creates(self):
        if not self._CORE_DIR.exists():
            pytest.skip(f"{self._CORE_DIR} nicht vorhanden")

        violations: list[str] = []
        for py_file in self._CORE_DIR.rglob("*.py"):
            rel = str(py_file.relative_to("src"))
            allowed_for_file = self._ALLOWED_DIRECT_CALLS.get(rel, {})
            source = py_file.read_text(errors="ignore")
            for match in self._PATTERN.finditer(source):
                line = source[: match.start()].count("\n") + 1
                if line in allowed_for_file:
                    continue
                violations.append(f"{rel}:{line}")

        assert not violations, (
            "Direkte AuditLog.objects.create()-Calls ausserhalb der Allowlist. "
            "Bitte stattdessen einen typed Helper aus core.services.audit "
            "verwenden (audit_event, audit_client_event, "
            "audit_retention_decision, audit_security_violation, "
            "audit_system_view) oder die Stelle in "
            "_ALLOWED_DIRECT_CALLS mit Begruendung dokumentieren.\n"
            "Refs #901.\n"
            f"Verstoesse: {violations}"
        )

    def test_allowlist_lines_actually_have_creates(self):
        """Schutz vor stale Eintraegen: jede Allowlist-Zeile muss
        tatsaechlich auf eine ``AuditLog.objects.create``-Stelle zeigen.
        Sonst altert die Allowlist still — wenn der Code refactored wird,
        verschiebt sich die Zeile und der alte Eintrag verstummt."""
        stale: list[str] = []
        for rel, lines in self._ALLOWED_DIRECT_CALLS.items():
            path = Path("src") / rel
            if not path.exists():
                stale.append(f"{rel} (Datei existiert nicht)")
                continue
            src_lines = path.read_text(errors="ignore").splitlines()
            for line_no in lines:
                if line_no < 1 or line_no > len(src_lines):
                    stale.append(f"{rel}:{line_no} (Zeile ausserhalb der Datei)")
                    continue
                if "AuditLog.objects.create" not in src_lines[line_no - 1]:
                    stale.append(f"{rel}:{line_no} (kein 'AuditLog.objects.create' an dieser Zeile)")
        assert not stale, (
            "Allowlist-Eintraege zeigen ins Leere — wahrscheinlich verschoben "
            "sich Zeilennummern durch Refactoring. Bitte _ALLOWED_DIRECT_CALLS "
            "auf die aktuellen Zeilen aktualisieren.\n"
            f"Verstoesse: {stale}"
        )


class TestAuditDetailKeyInventory:
    """Refs #1944: statisches Gegenstueck zum Laufzeit-Guard
    ``core.services.audit.detail_policy.validate_audit_detail``.

    Der Laufzeit-Guard traegt die Last (er sieht auch dynamisch gebaute
    Payloads), faellt aber nur auf, wenn der Schreibpunkt **ausgefuehrt** wird.
    Dieser Guard liest die literalen ``detail={...}``-Dicts aller Audit-Helper-
    Aufrufe unter ``src/core`` und meldet einen nicht deklarierten Schluessel
    schon ohne Testabdeckung.

    Zweiter Teil: jeder Schreibpunkt, dessen ``detail`` **nicht** literal ist
    (``**kwargs``, Zaehler-Dict, ``**release_detail(...)``), muss in
    :data:`_DYNAMIC_DETAIL_SITES` mit Anzahl und Begruendung stehen. Dort greift
    ausschliesslich der Laufzeit-Guard — die Liste haelt sichtbar, wo das der
    Fall ist, und faellt auf, sobald eine neue solche Stelle dazukommt.
    """

    _CORE_DIR = Path("src/core")

    # Alle Helper aus ``core.services.audit``. ``audit_admin_view`` /
    # ``audit_system_view`` / ``audit_client_event`` /
    # ``audit_retention_decision`` / ``audit_security_violation`` nehmen ihr
    # ``detail`` als ``**kwargs`` entgegen — der Wert je Helper listet dessen
    # Meta-Keywords, die nicht als detail-Schluessel zaehlen.
    _KWARG_HELPERS: ClassVar[dict[str, set[str]]] = {
        "audit_client_event": {"client", "user", "action"},
        "audit_retention_decision": {"facility", "target_type", "action", "category", "target_id", "user"},
        "audit_security_violation": {"facility", "user", "target_type", "target_id", "reason"},
        "audit_system_view": {"request", "action", "target_type", "target_id"},
        "audit_admin_view": {"request", "target_obj", "target_type", "target_id"},
    }
    _DETAIL_HELPERS: ClassVar[set[str]] = {"log_audit_event", "audit_event"}

    # ``helpers.py`` ist die Heimat des Guards selbst (es reicht ``detail``
    # weiter, statt es zu bauen) — kein Schreibpunkt im Sinne dieses Tests.
    _SKIP_FILES: ClassVar[set[str]] = {"core/services/audit/helpers.py"}

    # Datei -> (Anzahl dynamischer Schreibpunkte, Begruendung).
    _DYNAMIC_DETAIL_SITES: ClassVar[dict[str, tuple[int, str]]] = {
        "core/admin/mixins.py": (1, "AdminReadAuditMixin reicht **detail durch (admin_view)"),
        "core/management/commands/export_facility.py": (1, "Zaehler-Dict aus export['entities']"),
        "core/management/commands/mark_restore_verified.py": (1, "detail-Variable (note) je nach CLI-Argument"),
        "core/retention/enforcement.py": (1, "**extra_detail des Retention-Laufs (document_type)"),
        "core/retention/legal_holds.py": (
            1,
            "**detail der LEGAL_HOLD-Zeile — ``history_already_redacted`` steht "
            "nur bei ``target_type='Event'`` darin (ADR-050 D2, Refs #2065). Alle "
            "moeglichen Schluessel (reason, expires_at, hold_id, "
            "history_already_redacted) sind in AUDIT_DETAIL_KEYS deklariert",
        ),
        "core/services/case/deletion.py": (
            1,
            "**detail von _move_case_to_trash — der gemeinsame Kern beider "
            "Fall-Loeschwege; die Aufrufer geben entweder ``trigger`` (direkt) "
            "oder ``deletion_request`` (genehmigt) mit (Refs #1818)",
        ),
        "core/services/client/merge.py": (
            1,
            "**extra der CLIENT_MERGED-Zeile — der direkte Merge-Weg schreibt "
            "ohne, der genehmigte mit ``deletion_request`` (Muster "
            "_move_case_to_trash, Refs #1876); alle uebrigen Schluessel stehen "
            "literal am Aufruf und sind in AUDIT_DETAIL_KEYS deklariert",
        ),
        "core/services/deletion.py": (
            1,
            "DELETION_WITHDRAWN kennt EINEN Schluessel, aber dessen Name haengt "
            "am Zieltyp: ``withdraw_deletion_request`` bedient alle drei "
            "(Ereignis/Person/Fall) und schlaegt ihn in ``_WITHDRAW_DETAIL_KEY`` "
            "nach — dieselben Namen, die die drei ``DELETION_REQUESTED``-"
            "Schreiber literal setzen. Moegliche Schluessel sind genau "
            "``target_event``/``target_client``/``target_case``, alle drei in "
            "AUDIT_DETAIL_KEYS deklariert (Refs #2040)",
        ),
        "core/services/config/conditions.py": (3, "**_condition_detail(...) der Feldbedingungen"),
        "core/services/events/crud.py": (1, "audit_detail-Variable mit optionalem source"),
        # ``log_attachment_violation`` bekommt sein ``**extra`` ueber die
        # Indirektion ``_reject(...)`` in ``services/file_vault/policy.py``.
        # Deren neun Aufrufstellen tragen 16 technische Schluessel (Groessen,
        # MIME-Typen, Virensignatur) — kein einziger davon steht als Literal an
        # einem Helper-Aufruf, dieser AST-Guard sieht sie also nicht. Genau
        # dafuer sitzt der eigentliche Guard zur Laufzeit.
        "core/services/file_vault/audit.py": (
            2,
            "**detail von log_attachment_violation (via policy._reject) sowie "
            "**filename_fingerprint beim ATTACHMENT_UPLOAD; die moeglichen "
            "Fingerprint-Schluessel filename_hash/filename_ext sind beide in "
            "AUDIT_DETAIL_KEYS deklariert (Refs #1960)",
        ),
        "core/views/attachments.py": (1, "self._audit_detail(...) der Anhang-Domaene"),
        "core/views/mixins.py": (1, "**extra + **release_detail(...) des Export-Helpers"),
    }

    def _iter_calls(self):
        """(rel, lineno, helper, node) fuer jeden Audit-Helper-Aufruf."""
        for py_file in sorted(self._CORE_DIR.rglob("*.py")):
            rel = str(py_file.relative_to("src"))
            if rel in self._SKIP_FILES:
                continue
            try:
                tree = ast.parse(py_file.read_text(errors="ignore"))
            except SyntaxError:  # pragma: no cover — defensiv
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name in self._KWARG_HELPERS or name in self._DETAIL_HELPERS:
                    yield rel, node.lineno, name, node

    def _literal_keys_and_dynamic_sites(self):
        keys: dict[str, str] = {}
        dynamic: dict[str, set[int]] = {}

        def mark_dynamic(rel, lineno):
            dynamic.setdefault(rel, set()).add(lineno)

        def scan_dict(dict_node, rel, lineno):
            for key in dict_node.keys:
                if key is None:  # ``**expr``-Expansion
                    mark_dynamic(rel, lineno)
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.setdefault(key.value, f"{rel}:{lineno}")
                else:
                    mark_dynamic(rel, lineno)

        for rel, lineno, name, node in self._iter_calls():
            if name in self._KWARG_HELPERS:
                meta = self._KWARG_HELPERS[name]
                for kw in node.keywords:
                    if kw.arg is None:
                        mark_dynamic(rel, lineno)
                    elif kw.arg not in meta:
                        keys.setdefault(kw.arg, f"{rel}:{lineno}")
                continue
            for kw in node.keywords:
                if kw.arg != "detail":
                    continue
                value = kw.value
                branches = [value.body, value.orelse] if isinstance(value, ast.IfExp) else [value]
                for branch in branches:
                    if isinstance(branch, ast.Dict):
                        scan_dict(branch, rel, lineno)
                    elif isinstance(branch, ast.Constant) and branch.value is None:
                        continue
                    else:
                        mark_dynamic(rel, lineno)
        return keys, dynamic

    def test_literale_detail_schluessel_sind_deklariert(self):
        if not self._CORE_DIR.exists():
            pytest.skip(f"{self._CORE_DIR} nicht vorhanden")
        from core.services.audit.detail_policy import AUDIT_DETAIL_KEYS

        keys, _dynamic = self._literal_keys_and_dynamic_sites()
        missing = sorted(f"{key} ({where})" for key, where in keys.items() if key not in AUDIT_DETAIL_KEYS)
        assert not missing, (
            "Nicht deklarierte AuditLog-detail-Schluessel. Jeder Schluessel muss in "
            "``core/services/audit/detail_policy.py`` (AUDIT_DETAIL_KEYS) mit Begruendung stehen, "
            "warum der Wert keine Klienten-PII traegt. Klartext-Freitext gehoert nicht in das "
            "append-only AuditLog — stattdessen ``hmac_hash_value`` oder die ``target_id`` nutzen.\n"
            "Refs #1944.\n"
            "Fehlend:\n  " + "\n  ".join(missing)
        )

    def test_dynamische_schreibpunkte_sind_dokumentiert(self):
        if not self._CORE_DIR.exists():
            pytest.skip(f"{self._CORE_DIR} nicht vorhanden")

        _keys, dynamic = self._literal_keys_and_dynamic_sites()
        ist = {rel: len(lines) for rel, lines in dynamic.items()}
        soll = {rel: count for rel, (count, _grund) in self._DYNAMIC_DETAIL_SITES.items()}

        abweichungen = []
        for rel in sorted(set(ist) | set(soll)):
            if ist.get(rel, 0) != soll.get(rel, 0):
                abweichungen.append(f"{rel}: deklariert {soll.get(rel, 0)}, gefunden {ist.get(rel, 0)}")
        assert not abweichungen, (
            "Schreibpunkte mit nicht-literalem ``detail`` weichen von der Deklaration ab. "
            "Dort greift nur der Laufzeit-Guard (validate_audit_detail) — die Liste "
            "_DYNAMIC_DETAIL_SITES haelt sichtbar, wo das der Fall ist. Neue Stelle: mit "
            "Begruendung eintragen und pruefen, dass alle dort moeglichen Schluessel in "
            "AUDIT_DETAIL_KEYS stehen.\n"
            "Refs #1944.\n"
            "Abweichungen:\n  " + "\n  ".join(abweichungen)
        )

    def test_jede_deklarierte_dynamikstelle_existiert_noch(self):
        fehlend = [rel for rel in self._DYNAMIC_DETAIL_SITES if not (Path("src") / rel).exists()]
        assert not fehlend, f"_DYNAMIC_DETAIL_SITES verweist auf nicht-existierende Dateien: {fehlend}"


class TestDomainAdminReadOnlyGuard:
    """Refs #1341 (AUTHZ-1): Fachobjekte mit reicher Service-Logik dürfen im
    Django-Admin nicht direkt schreibbar sein.

    Ein Admin-Save würde die Service-Invarianten (Feld-Verschlüsselung,
    EventHistory-Diff, Vier-Augen-Löschung, Legal-Hold/Retention) und das
    Domänen-AuditLog umgehen. Die betroffenen ModelAdmins müssen daher
    ``ReadOnlyDomainAdminMixin`` erben. Der Guard schlägt fehl, sobald ein
    neuer Fach-Model-Admin ohne den Mixin registriert wird — dann bewusst
    entscheiden: read-only stellen oder an das Domänen-Audit anbinden.
    """

    def test_domain_model_admins_are_read_only(self):
        from core.admin.mixins import ReadOnlyDomainAdminMixin
        from core.admin_site import anlaufstelle_admin_site
        from core.models import Case, Client, Event, WorkItem

        # Fachobjekte mit Service-Logik / Klientel-PII (Refs #1341). Neue
        # Modelle dieser Kategorie hier ergänzen.
        domain_models = [Client, Case, Event, WorkItem]

        offenders: list[str] = []
        for model in domain_models:
            model_admin = anlaufstelle_admin_site._registry.get(model)
            if model_admin is None:
                offenders.append(f"{model.__name__}: nicht in anlaufstelle_admin_site registriert")
                continue
            if not isinstance(model_admin, ReadOnlyDomainAdminMixin):
                offenders.append(f"{model.__name__}: {type(model_admin).__name__} erbt kein ReadOnlyDomainAdminMixin")

        assert not offenders, (
            "Fach-Model-Admins müssen read-only sein (ReadOnlyDomainAdminMixin), "
            "damit Admin-Saves nicht die Service-Invarianten/das Domänen-Audit "
            "umgehen.\nRefs #1341.\nVerstöße:\n  " + "\n  ".join(offenders)
        )


class TestE2ESelectorStabilityGuard:
    """Blockt brüchige Playwright-Selektoren in src/tests/e2e/.

    Refs #922 / #924: ``page.locator(...).first.click()`` und
    ``.nth(<int>).click()`` auf nicht-deterministischer Reihenfolge führen
    zu Flakes bei Seed-Drift und parallelisierten Runs. Stabile Alternativen
    sind ``data-testid``-basierte Selektoren plus die Helper in
    ``src/tests/e2e/_selectors.py``.

    Bestehende Sünden, die bewusst noch nicht migriert wurden,
    sind per File-zentrierter Whitelist mit max. erlaubten Vorkommen erfasst.
    Wenn ein File aufräumt, muss der Counter heruntergesetzt werden — das
    erzwingt Bewegung in die richtige Richtung. Wenn die Zahl überschritten
    wird, schlägt der Guard fehl.
    """

    _E2E_DIR = Path("src/tests/e2e")
    _PATTERN = re.compile(r"\.(first|nth\(\d+\))\.click\(\)")

    # File-Whitelist mit max. erlaubten Vorkommen. Beim Migrieren auf 0 setzen
    # und den Eintrag entfernen, sobald der Counter 0 ist.
    #
    # Follow-Up: Die neuen E2E-Files aus #928 haben einige
    # ``.first.click()``-Selektoren mitgebracht; Werte hier dokumentieren den
    # Ist-Stand, damit #929 den reinen Refactor ohne behavioural
    # change abschließen kann. Cleanup → eigenes Issue.
    _WHITELIST_MAX: ClassVar[dict[str, int]] = {
        # e320590 (A2.1-E2E): _role_option_values klickt den User-Link in der
        # Django/Unfold-Changelist, die kein data-testid rendert -> exact-name
        # role-Locator + ``.first``. Counter zieht den Ist-Stand nach;
        # Stabilisierung (Direkt-Navigation per pk) separat.
        "test_admin_site.py": 1,
        "test_button_permissions.py": 2,
        "test_cases.py": 3,
        # 1b52bea7 (#1787): Wegwerf-Seed-Umbau entfernte 3 Vorkommen.
        # #1813: Overflow-Umbau stabilisierte 2 weitere auf Testid-Klicks.
        "test_client_deletion_workflow.py": 1,
        "test_episodes.py": 1,
        # dbdaf3c: TestMultipleGoalsAndMilestones brachte 3 weitere
        # ``.first.click()`` (Milestone-Toggle + Goal-Toggle + Submit innerhalb
        # einer Schleife). Counter zieht den Ist-Stand nach; Stabilisierung
        # auf data-testid läuft separat.
        "test_goals_htmx.py": 5,
        "test_handover.py": 2,
        "test_i18n_locale.py": 2,
        # 1b52bea7 (#1787): Wegwerf-Seed-Umbau entfernte 4 Vorkommen;
        # #1813: Hub-/Subnav-Umbau entfernte das letzte.
        "test_workflow_complete.py": 1,
        "test_zeitstrom_events.py": 2,
    }

    def _count_in_file(self, path: Path) -> int:
        if not path.exists():
            return 0
        return len(self._PATTERN.findall(path.read_text(errors="ignore")))

    def test_no_new_unstable_selectors(self):
        """Neue Files dürfen kein ``.first.click()``/``.nth(N).click()``."""
        if not self._E2E_DIR.exists():
            pytest.skip(f"{self._E2E_DIR} nicht vorhanden")

        violations: list[str] = []
        for py_file in sorted(self._E2E_DIR.glob("test_*.py")):
            name = py_file.name
            count = self._count_in_file(py_file)
            allowed = self._WHITELIST_MAX.get(name, 0)
            if count > allowed:
                violations.append(f"{name}: {count} unstable Selektor(en), erlaubt sind {allowed}")

        assert not violations, (
            "Brüchige Playwright-Selektoren (.first.click() / .nth(N).click()) "
            "über der Whitelist-Schwelle. Bitte stabile data-testid-basierte "
            "Selektoren nutzen (siehe src/tests/e2e/_selectors.py) oder die "
            "Whitelist im TestE2ESelectorStabilityGuard anpassen, falls eine "
            "Migration den Counter reduziert hat.\n"
            "Verstöße:\n  " + "\n  ".join(violations)
        )

    def test_whitelist_entries_are_still_needed(self):
        """Schutz gegen veraltete Whitelist: wenn ein File migriert ist,
        muss der Eintrag entfernt oder der Counter gesenkt werden."""
        stale: list[str] = []
        for name, allowed in self._WHITELIST_MAX.items():
            path = self._E2E_DIR / name
            if not path.exists():
                stale.append(f"{name}: Datei existiert nicht mehr")
                continue
            count = self._count_in_file(path)
            if count < allowed:
                stale.append(f"{name}: Whitelist erlaubt {allowed}, aktuell nur {count} Vorkommen")
        assert not stale, (
            "Whitelist-Schwellen sind höher als der aktuelle Bestand — bitte "
            "_WHITELIST_MAX auf die tatsächliche Anzahl senken (Ziel: 0 und "
            "Eintrag entfernen).\n"
            "Veraltete Einträge:\n  " + "\n  ".join(stale)
        )


class TestE2ESeedFacilityGuard:
    """Blockt neue ``Facility.objects.first()``-Seeds in src/tests/e2e/.

    Refs #2166: Facility-PK ist eine UUID — ``Facility.objects.first()`` bzw.
    ``Facility.objects.order_by("pk").first()`` liefert dann eine physische,
    nicht garantierte Reihenfolge. Sobald ein früherer Spec (z. B.
    ``test_admin_site.py``) eine zweite Facility anlegt, würfelt die Auswahl —
    Fix-Muster 660dc07b band die Onboarding-/Retention-Journeys stattdessen an
    ``User.objects.get(username="admin").facility``; die übrigen 16 Seeds
    (#2166) an ``Facility.objects.get(name="Hauptstelle")``.

    Review: der ursprüngliche Scan traf nur unmittelbar
    unter ``src/tests/e2e/`` liegende ``test_*.py`` — ein neuer Seed in
    ``conftest.py``, ``_helpers.py`` oder einem künftigen Unterverzeichnis
    wäre unentdeckt geblieben. Der Scan läuft jetzt rekursiv über ALLE
    ``*.py``-Dateien unter ``src/tests/e2e/`` (``rglob``, kein Namensfilter).
    Keine Ausnahmen: aktuell enthält keine Nicht-Test-Datei im Baum das
    Muster, ein Ausschluss wäre unbegründet.

    Gleiche Mechanik wie ``TestE2ESelectorStabilityGuard``, aber mit
    Whitelist-Start bei 0 — der Fix bereinigt ALLE Stellen, keine Restmenge
    ist geduldet.
    """

    _E2E_DIR = Path("src/tests/e2e")
    _PATTERN = re.compile(r'Facility\.objects\.(?:order_by\(["\']pk["\']\)\.)?first\(\)')

    # Keine Ausnahmen: jeder neue Treffer ist ein Regressions-Fund.
    _WHITELIST_MAX: ClassVar[dict[str, int]] = {}

    def _count_in_file(self, path: Path) -> int:
        if not path.exists():
            return 0
        return len(self._PATTERN.findall(path.read_text(errors="ignore")))

    def test_no_new_facility_first_seeds(self):
        """Neue Files dürfen kein ``Facility.objects.first()``/``order_by("pk").first()``."""
        if not self._E2E_DIR.exists():
            pytest.skip(f"{self._E2E_DIR} nicht vorhanden")

        violations: list[str] = []
        for py_file in sorted(self._E2E_DIR.rglob("*.py")):
            name = py_file.relative_to(self._E2E_DIR).as_posix()
            count = self._count_in_file(py_file)
            allowed = self._WHITELIST_MAX.get(name, 0)
            if count > allowed:
                violations.append(f"{name}: {count} Facility.objects.first()-Seed(s), erlaubt sind {allowed}")

        assert not violations, (
            "E2E-Seeds dürfen die Ziel-Facility nicht über Facility.objects."
            'first()/order_by("pk").first() waehlen (UUID-PK, Reihenfolge '
            "nicht garantiert). Stattdessen Facility.objects.get(name="
            '"Hauptstelle") bzw. User.objects.get(username="admin").facility '
            "verwenden (Refs #2166).\n"
            "Verstöße:\n  " + "\n  ".join(violations)
        )
