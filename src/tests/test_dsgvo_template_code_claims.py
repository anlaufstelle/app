"""Guards: GDPR templates and the domain concept describe what the code does.

Three claim families are pinned against the implementation:

* Offline key lifecycle (Refs #2426): the AES-GCM ``CryptoKey`` is persisted
  in IndexedDB (``src/static/js/crypto.js``), closing a tab or restarting the
  device is not a wipe, the idle check only locks the key while unsynced work
  exists, and index metadata stays in clear text next to the envelope.
  Locked ciphertext only becomes readable again while password and offline
  salt are unchanged; role change, deactivation and a correct-password login
  against a locked account rotate the salt. The exact wording of that
  promise is pinned in ``test_dsgvo_recovery_claim_wording.py``.
* Encryption promises (Refs #2465): only individually configured fields are
  encrypted (``is_encrypted`` in the shipped packages), the database itself
  is not, and server-side field encryption is Fernet, not AES-GCM.
* Visibility (Refs #2466): what a role sees follows sensitivity (maximum of
  document type and field) times role (``ROLE_MAX_SENSITIVITY``), not the
  contact stage; the system administration reads across facilities through
  the admin interface and bypasses RLS. Only the list and detail views of
  the admin classes using ``AdminReadAuditMixin`` (plus every ``/system/``
  view) write ``SYSTEM_VIEW``; the documents must name that scope instead of
  promising that every administrative read is logged.
"""

import re
import time
import uuid
from pathlib import Path

import pytest
import yaml
from django.test import override_settings

from core.admin.mixins import AdminReadAuditMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import AuditLog, DeletionRequest, DocumentType, User
from core.services.client import render_document
from core.services.compliance.sensitivity import ROLE_MAX_SENSITIVITY, SENSITIVITY_RANK
from core.services.compliance.vvt import PROCESSING_ACTIVITIES
from core.services.security.credentials import REVOCATION_EFFECTS, RevocationReason

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = REPO_ROOT / "src" / "core" / "packages"
DOMAIN_CONCEPT_DE = REPO_ROOT / "docs" / "fachkonzept-anlaufstelle.md"
DOMAIN_SUMMARY_EN = REPO_ROOT / "docs" / "en" / "domain-concept-summary.md"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section(content: str, start: str, end: str) -> str:
    begin = content.index(start)
    return content[begin : content.index(end, begin + len(start))]


def _blocks(content: str) -> list[str]:
    """Paragraphs, list items and table rows of a rendered template, flattened."""
    return [_flat(block) for block in re.split(r"\n\s*\n|\n(?=\s*(?:[-|]|\d+\.) )", content) if block.strip()]


@pytest.fixture
def toms(facility):
    return _flat(render_document("toms", facility)[0])


@pytest.fixture
def dsfa(facility):
    return _flat(render_document("dsfa", facility)[0])


@pytest.fixture
def toms_raw(facility):
    return render_document("toms", facility)[0]


@pytest.fixture
def dsfa_raw(facility):
    return render_document("dsfa", facility)[0]


# ---------------------------------------------------------------------------
# Offline key lifecycle (Refs #2426)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOfflineKeyLifecycleTemplates:
    def test_toms_do_not_claim_memory_only_key(self, toms):
        assert "ausschließlich in-memory" not in toms
        assert "Schließen des Tabs machen alle offline gehaltenen Daten unlesbar" not in toms

    def test_toms_name_indexeddb_persistence_and_restart_is_no_wipe(self, toms):
        assert "IndexedDB" in toms
        assert "Geräteneustart" in toms
        assert "kein Löschweg" in toms

    def test_toms_separate_non_extractable_from_decryption(self, toms):
        assert "nicht das Entschlüsseln" in toms

    def test_toms_name_clear_text_metadata(self, toms):
        assert "liegen unverschlüsselt" in toms

    def test_dsfa_do_not_promise_ciphertext_only_or_unconditional_idle_wipe(self, dsfa):
        assert "ein gestohlenes Gerät ohne aktive Session liefert nur Chiffretext" not in dsfa
        assert "Schlüssel **und** verschlüsselter Bestand werden verworfen, sobald" not in dsfa

    def test_dsfa_explain_idle_lock_keeps_unsynced_ciphertext(self, dsfa):
        assert "ungesendete Arbeit" in dsfa
        assert "bleibt erhalten" in dsfa
        assert "**kein** Löschereignis" in dsfa
        assert "Entwicklerwerkzeuge" in dsfa

    def test_dsfa_metadata_list_matches_store_layout(self, dsfa):
        # ``occurredAt`` left the clear-text row (Dexie schema v5 in
        # offline-db.js); contact times are inside the encrypted envelope.
        assert "Kontaktzeitpunkte" not in dsfa
        assert "Ziel-Adressen der Sende-Warteschlange" in dsfa


@pytest.mark.architecture
class TestOfflineKeyLifecycleDomainConcept:
    def test_section_16_does_not_promise_devtools_protection(self):
        text = _flat(DOMAIN_CONCEPT_DE.read_text(encoding="utf-8"))
        assert "selbst mit DevTool-Zugriff" not in text
        assert "liefert nur Chiffretext" not in text

    def test_section_16_idle_check_distinguishes_lock_and_wipe(self):
        text = _flat(DOMAIN_CONCEPT_DE.read_text(encoding="utf-8"))
        assert "verwirft den Key und löscht den Offline-Store, sobald die Session abgelaufen ist" not in text
        assert "nur den Schlüssel" in text


# ---------------------------------------------------------------------------
# Encryption promises (Refs #2465)
# ---------------------------------------------------------------------------


def _shipped_field_encryption() -> dict[str, dict[str, bool]]:
    """``{package: {field slug: is_encrypted}}`` across all shipped packages."""
    result: dict[str, dict[str, bool]] = {}

    def walk(node, fields):
        if isinstance(node, dict):
            if "slug" in node and "is_encrypted" in node:
                fields[node["slug"]] = bool(node["is_encrypted"])
            for value in node.values():
                walk(value, fields)
        elif isinstance(node, list):
            for value in node:
                walk(value, fields)

    for path in sorted(PACKAGES_DIR.glob("*.yaml")):
        fields: dict[str, bool] = {}
        walk(yaml.safe_load(path.read_text(encoding="utf-8")), fields)
        result[path.stem] = fields
    return result


@pytest.mark.django_db
class TestEncryptionPromises:
    def test_general_note_field_is_not_encrypted_in_any_package(self):
        """Premise of the template wording: ``notiz`` is plain text everywhere."""
        packages = _shipped_field_encryption()
        assert packages
        for name, fields in packages.items():
            assert fields.get("notiz") is False, name

    def test_information_duties_do_not_promise_encrypted_contact_notes(self, facility):
        content = _flat(render_document("informationspflichten", facility)[0])
        assert "Dokumentation der Beratungsinhalte (verschlüsselt)" not in content
        assert "je nach Konfiguration" in content

    def test_dpa_does_not_promise_encrypted_database_or_notes(self, facility):
        content = _flat(render_document("av-vertrag", facility)[0])
        assert "Speicherung verschlüsselter Datenbankinhalte" not in content
        assert "Verschlüsselte Kontaktnotizen" not in content
        assert "je nach Konfiguration" in content

    def test_dpa_lists_missing_data_categories(self, facility):
        content = _flat(render_document("av-vertrag", facility)[0])
        section = _section(content, "§ 3 Art der personenbezogenen Daten", "§ 4 ")
        categories = ("Dateianhänge", "Fall", "Wirkungsziel", "Art. 9", "Protokolldaten", "IP-Adresse", "Sicherungen")
        for category in categories:
            assert category in section, category

    def test_vvt_names_fernet_for_server_side_fields(self):
        toms = [str(tom) for activity in PROCESSING_ACTIVITIES for tom in activity["toms"]]
        assert not [tom for tom in toms if "AES-GCM" in tom]
        assert [tom for tom in toms if "Fernet" in tom]


# ---------------------------------------------------------------------------
# Visibility = sensitivity x role (Refs #2466)
# ---------------------------------------------------------------------------


def _role_table_rows(content: str) -> dict[str, list[str]]:
    section = _section(content, "**Rollenkonzept:**", "**Technische Umsetzung:**")
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 6 and cells[0] not in ("Rolle",) and not set(cells[0]) <= {"-"}:
            rows[cells[0]] = cells[1:]
    return rows


@pytest.mark.django_db
class TestVisibilityTemplates:
    def test_premise_staff_sees_up_to_elevated_and_super_admin_has_no_rank(self):
        elevated = SENSITIVITY_RANK[DocumentType.Sensitivity.ELEVATED]
        assert ROLE_MAX_SENSITIVITY[User.Role.STAFF] == elevated
        assert ROLE_MAX_SENSITIVITY[User.Role.ASSISTANT] == SENSITIVITY_RANK[DocumentType.Sensitivity.NORMAL]
        assert User.Role.SUPER_ADMIN not in ROLE_MAX_SENSITIVITY

    def test_toms_role_table_lists_all_five_roles_by_ui_label(self, facility):
        rows = _role_table_rows(render_document("toms", facility)[0])
        assert sorted(rows) == sorted(str(label) for _value, label in User.Role.choices)

    def test_toms_role_table_reads_follow_sensitivity(self, facility):
        rows = _role_table_rows(render_document("toms", facility)[0])
        assert str(DocumentType.Sensitivity.ELEVATED.label) in rows["Fachkraft"][0]
        assert str(DocumentType.Sensitivity.NORMAL.label) in rows["Assistenz"][0]
        assert rows["Fachkraft"][1] != "Eigene"
        assert "SYSTEM_VIEW" in rows["Systemadministration"][0]

    def test_toms_and_dsfa_name_the_rls_exception(self, toms, dsfa):
        assert "app.is_super_admin" in toms
        assert "Auch bei Fehlern im ORM-Layer verhindert RLS einen Cross-Tenant-Zugriff" not in dsfa
        assert "app.is_super_admin" in dsfa

    def test_dsfa_and_vvt_template_count_five_roles(self, dsfa, facility):
        assert "Rollenbasierte Zugriffskontrolle (4 Stufen)" not in dsfa
        assert "fünf Rollen" in dsfa
        vvt = _flat(render_document("verarbeitungsverzeichnis", facility)[0])
        assert "Rolle (Admin, Leitung, Fachkraft, Assistenz)" not in vvt
        for _value, label in User.Role.choices:
            assert str(label) in vvt, label


@pytest.mark.architecture
class TestVisibilityDomainConcept:
    def test_domain_concept_does_not_tie_visibility_to_contact_stage(self):
        text = _flat(DOMAIN_CONCEPT_DE.read_text(encoding="utf-8"))
        for stale in (
            "Assistant sieht nur das Pseudonym und den letzten Kontakt",
            "Kontaktstufen als Zugriffssteuerung",
            "auch sie sieht nur den `/system/`-Bereich",
            "kein Zugriff auf qualifizierte Kontakt-Details",
            "Verschlüsselte Felder (z.B. Gesprächsinhalte) sind nur für berechtigte Rollen sichtbar",
        ):
            assert stale not in text, stale
        assert "/admin-mgmt/" in text

    def test_english_summary_role_model_matches_code(self):
        text = _flat(DOMAIN_SUMMARY_EN.read_text(encoding="utf-8"))
        role_model = _section(text, "## Role Model", "## Non-Functional Requirements")
        assert "Cannot view qualified contact details" not in role_model
        assert "system area (`/system/`) only" not in role_model
        assert "depends on the contact level of the person" not in role_model
        assert "/admin-mgmt/" in role_model


# ---------------------------------------------------------------------------
# Administrative read audit scope (Refs #2466)
# ---------------------------------------------------------------------------

# Admin models whose list/detail views write SYSTEM_VIEW for the system
# administration; the documents name exactly these as "Personen, Fälle,
# Ereignisse, Ereignis-Historie, Aufgaben und Anhänge".
AUDITED_ADMIN_MODELS = {"Client", "Case", "Event", "EventHistory", "WorkItem", "Attachment"}
AUDITED_ADMIN_SCOPE_DE = "Personen, Fällen, Ereignissen, Ereignis-Historie, Aufgaben und Anhängen"
AUDITED_ADMIN_SCOPE_EN = "persons, cases, events, event history, work items and attachments"

OVERBROAD_READ_AUDIT_CLAIMS = (
    "jeder Aufruf protokolliert",
    "jeder Lesezugriff der Systemadministration",
    "protokollieren diese Lesezugriffe als",
    "jeweils mit `SYSTEM_VIEW`-Protokoll",
    "jeder solche Aufruf wird im AuditLog",
    "jeweils protokolliert",
    "ohne dass dieser Zugriff im AuditLog (`SYSTEM_VIEW`) auftaucht",
    "und einrichtungsübergreifende Zugriffe;",
)


def _enter_sudo(client):
    from core.services.security import SUDO_SESSION_KEY

    session = client.session
    session[SUDO_SESSION_KEY] = int(time.time()) + 3600
    session.save()


def _system_view_count():
    return AuditLog.objects.filter(action=AuditLog.Action.SYSTEM_VIEW).count()


@pytest.mark.django_db
class TestAdminReadAuditScope:
    def test_premise_audited_admin_models(self):
        audited = {
            model.__name__
            for model, model_admin in anlaufstelle_admin_site._registry.items()
            if isinstance(model_admin, AdminReadAuditMixin)
        }
        assert audited == AUDITED_ADMIN_MODELS

    @override_settings(SUDO_MODE_ENABLED=True)
    def test_super_admin_changelists_of_audited_models_write_system_view(self, client, super_admin_user):
        client.force_login(super_admin_user)
        _enter_sudo(client)
        for model in anlaufstelle_admin_site._registry:
            if model.__name__ not in AUDITED_ADMIN_MODELS:
                continue
            before = _system_view_count()
            url = f"/admin-mgmt/{model._meta.app_label}/{model._meta.model_name}/"
            assert client.get(url).status_code == 200, url
            assert _system_view_count() == before + 1, url

    @override_settings(SUDO_MODE_ENABLED=True)
    def test_super_admin_deletion_request_read_writes_no_system_view(self, client, facility, super_admin_user):
        """The documented exception: deletion request reasons are read unlogged."""
        record = DeletionRequest.objects.create(
            facility=facility, target_type="Event", target_id=uuid.uuid4(), reason="Unlogged admin read"
        )
        client.force_login(super_admin_user)
        _enter_sudo(client)
        before = _system_view_count()
        response = client.get(f"/admin-mgmt/core/deletionrequest/{record.pk}/change/")
        assert response.status_code == 200
        assert b"Unlogged admin read" in response.content
        assert _system_view_count() == before

    def test_toms_and_dsfa_name_the_audited_scope_and_its_gaps(self, toms, dsfa):
        for name, text in (("toms", toms), ("dsfa", dsfa)):
            for claim in OVERBROAD_READ_AUDIT_CLAIMS:
                assert claim not in text, (name, claim)
            assert AUDITED_ADMIN_SCOPE_DE in text, name
            assert "Löschanträge" in text, name
            assert "schlägt die Protokollierung fehl" in text, name


@pytest.mark.architecture
class TestAdminReadAuditScopeDocs:
    def test_domain_concept_names_the_audited_scope_and_its_gaps(self):
        text = _flat(DOMAIN_CONCEPT_DE.read_text(encoding="utf-8"))
        for claim in OVERBROAD_READ_AUDIT_CLAIMS:
            assert claim not in text, claim
        assert AUDITED_ADMIN_SCOPE_DE in text
        assert "Löschanträge" in text
        assert "schlägt die Protokollierung fehl" in text

    def test_english_summary_names_the_audited_scope_and_its_gaps(self):
        text = _flat(DOMAIN_SUMMARY_EN.read_text(encoding="utf-8"))
        current = text[: text.index("## Version Additions")]
        assert "every such read is logged" not in current
        assert "and cross-facility access." not in current
        assert AUDITED_ADMIN_SCOPE_EN in current
        assert "deletion requests" in current
        assert "if logging fails" in current


# ---------------------------------------------------------------------------
# Offline recovery after re-login (Refs #2426)
# ---------------------------------------------------------------------------

STALE_RECOVERY_CLAIMS = (
    "sofern Passwort und Rolle unverändert sind",
    "nach erneutem Login mit unverändertem Passwort wieder lesbar",
)


def _assert_salt_rotation_triggers_named(name: str, text: str) -> None:
    for trigger in ("Rollenwechsel", "Deaktivierung", "gesperrten Konto"):
        assert trigger in text, (name, trigger)


@pytest.mark.django_db
class TestOfflineRecoveryPremise:
    def test_premise_salt_rotating_revocation_reasons(self):
        rotating = {reason for reason, effects in REVOCATION_EFFECTS.items() if effects.rotate_offline_salt}
        assert rotating == {
            RevocationReason.PASSWORD_CHANGE,
            RevocationReason.ACCOUNT_LOCKOUT,
            RevocationReason.PRIVILEGE_REVOKED,
        }

    def test_correct_password_login_against_locked_account_rotates_salt(self, client, staff_user):
        from core.services.security.login_lockout import LOCKOUT_THRESHOLD

        salt = staff_user.ensure_offline_key_salt()
        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=staff_user.facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN_FAILED,
                ip_address="127.0.0.1",
            )
        client.post("/login/", {"username": staff_user.username, "password": "testpass123"})
        staff_user.refresh_from_db()
        assert staff_user.offline_key_salt == ""
        assert staff_user.ensure_offline_key_salt() != salt

    def test_deactivation_rotates_salt_and_reactivation_keeps_it_new(self, staff_user):
        salt = staff_user.ensure_offline_key_salt()
        staff_user.is_active = False
        staff_user.save()
        staff_user.is_active = True
        staff_user.save()
        staff_user.refresh_from_db()
        assert staff_user.role == User.Role.STAFF
        assert staff_user.ensure_offline_key_salt() != salt


@pytest.mark.django_db
class TestOfflineRecoveryTemplates:
    def test_toms_and_dsfa_tie_recovery_to_password_and_salt(self, toms, dsfa):
        for name, text in (("toms", toms), ("dsfa", dsfa)):
            for claim in STALE_RECOVERY_CLAIMS:
                assert claim not in text, (name, claim)
            _assert_salt_rotation_triggers_named(name, text)


@pytest.mark.architecture
class TestOfflineRecoveryDocs:
    def test_domain_concept_ties_recovery_to_password_and_salt(self):
        text = _flat(DOMAIN_CONCEPT_DE.read_text(encoding="utf-8"))
        for claim in STALE_RECOVERY_CLAIMS:
            assert claim not in text, claim
        _assert_salt_rotation_triggers_named("domain concept", text)

    def test_english_summary_current_sections_describe_offline_and_rls_as_built(self):
        text = _flat(DOMAIN_SUMMARY_EN.read_text(encoding="utf-8"))
        # Only the privacy section itself: a paraphrase elsewhere (header,
        # release notes) must not satisfy the guard.
        current = _section(text, "## Privacy Architecture", "## Role Model")
        for required in (
            "IndexedDB",
            "does not prevent decryption",
            "not a wipe",
            "only the key",
            "locked account",
            "clear text",
            "app.is_super_admin",
            "the description in this section applies",
        ):
            assert required in current, required


# ---------------------------------------------------------------------------
# Deletion concept: cross references and audit log exemptions (Refs #2425)
# ---------------------------------------------------------------------------

_SECTION_REF_RE = re.compile(r"§ (\d+(?:\.\d+)*)")
_HEADING_RE = re.compile(r"^#{2,4} (\d+(?:\.\d+)*)\.? (.+)$", re.MULTILINE)


#: Wording every audit retention text uses for the class ``prune_auditlog``
#: never reaches (it deletes only ``facility=facility`` rows).
AUDIT_WITHOUT_FACILITY = "Einträge ohne Einrichtung"


@pytest.fixture
def deletion_concept(facility):
    return render_document("loeschkonzept", facility)[0]


def _headings(content: str) -> dict[str, str]:
    return {number: title for number, title in _HEADING_RE.findall(content)}


@pytest.mark.django_db
class TestDeletionConceptAuditLog:
    def test_internal_section_references_resolve(self, deletion_concept):
        headings = _headings(deletion_concept)
        ref_lines = [line for line in deletion_concept.splitlines() if "siehe §" in line]
        refs = {ref for line in ref_lines for ref in _SECTION_REF_RE.findall(line)}
        assert refs
        for ref in refs:
            assert ref in headings, ref

    def test_audit_log_row_points_to_audit_log_section(self, deletion_concept):
        headings = _headings(deletion_concept)
        row = next(line for line in deletion_concept.splitlines() if line.startswith("| Audit-Log |"))
        refs = _SECTION_REF_RE.findall(row)
        assert refs, row
        for ref in refs:
            assert "Audit-Log" in headings[ref], (ref, headings.get(ref))

    def test_audit_log_section_names_every_exemption_and_no_hold(self, deletion_concept):
        section = _flat(_section(deletion_concept, "### 3.3 Audit-Log", "### 3.4"))
        for exemption in (
            "Sicherheitsverstöße",
            "System-Marker des Aufbewahrungslaufs",
            "Ereignis-Historie",
            "herausgegebener Berichtszeiträume",
        ):
            assert exemption in section, exemption
        assert "Aufbewahrungssperren (Legal Hold) wirken nicht auf das Audit-Log" in section
        # ``prune_auditlog`` filters ``facility=facility``: entries without a
        # facility are never pruned, so the list of exemptions is not closed.
        assert "Weitere Ausnahmen gibt es nicht" not in section
        assert AUDIT_WITHOUT_FACILITY in section
        assert "unbefristet" in section

    def test_dsfa_and_vvt_template_name_history_proof_and_no_hold(self, facility):
        dsfa_row = next(
            line for line in render_document("dsfa", facility)[0].splitlines() if line.startswith("| 7 | Audit-Log |")
        )
        assert "Historien-Redaktion" in dsfa_row
        assert "Aufbewahrungssperren (Legal Hold) wirken nicht auf das Audit-Log" in dsfa_row
        assert AUDIT_WITHOUT_FACILITY in dsfa_row
        vvt = _flat(render_document("verarbeitungsverzeichnis", facility)[0])
        audit_deadlines = _section(vvt, "### 3.4 Löschfristen", "---")
        assert "Historien-Redaktion" in audit_deadlines
        assert "Aufbewahrungssperren (Legal Hold) wirken nicht auf das Audit-Log" in audit_deadlines
        assert "weitere Ausnahmen gibt es nicht" not in audit_deadlines
        assert AUDIT_WITHOUT_FACILITY in audit_deadlines

    def test_dsfa_residual_risk_and_employee_notice_name_entries_without_facility(self, facility):
        dsfa = _flat(render_document("dsfa", facility)[0])
        residual = _section(dsfa, "**Restrisiko:** Das Audit-Log", "Aufbewahrungs-Job")
        assert AUDIT_WITHOUT_FACILITY in residual
        employees = _flat(render_document("mitarbeiter-informationspflichten", facility)[0])
        assert AUDIT_WITHOUT_FACILITY in _section(employees, "Reguläre Audit-Log-Einträge", "Sitzungen enden")


# ---------------------------------------------------------------------------
# Domain concept: retention periods and RLS coverage (Refs #2425, #2445)
# ---------------------------------------------------------------------------


def _bullet(text: str, title: str) -> str:
    start = text.index(f"- **{title}**")
    end = text.find("\n- **", start + 1)
    return _flat(text[start : end if end != -1 else len(text)])


@pytest.mark.architecture
class TestDomainConceptRetentionAndRls:
    def test_premise_tables_outside_rls(self):
        from tests.test_rls import EXPECTED_TABLES, NOT_RLS_SCOPED

        assert set(NOT_RLS_SCOPED) == {"core_user"}
        for table in ("core_user", "core_facility", "core_organization"):
            assert table not in EXPECTED_TABLES, table

    def test_rls_bullet_derives_tables_and_names_its_limits(self):
        bullet = _bullet(DOMAIN_CONCEPT_DE.read_text(encoding="utf-8"), "Row Level Security (RLS).")
        assert re.search(r"derzeit \d+", bullet) is None
        assert "nicht zu Cross-Facility-Datenleaks führen" not in bullet
        for required in (
            "EXPECTED_TABLES",
            "core_user",
            "core_facility",
            "core_organization",
            "Materialized-View",
            "app.is_super_admin",
            "BYPASSRLS",
        ):
            assert required in bullet, required

    def test_english_rls_bullet_names_its_limits(self):
        privacy = _section(
            _flat(DOMAIN_SUMMARY_EN.read_text(encoding="utf-8")), "## Privacy Architecture", "## Role Model"
        )
        for required in ("EXPECTED_TABLES", "core_user", "core_facility", "core_organization", "BYPASSRLS"):
            assert required in privacy, required

    def test_retention_bullets_name_open_case_gap_and_hold_record(self):
        de = _bullet(DOMAIN_CONCEPT_DE.read_text(encoding="utf-8"), "Löschfristen.")
        for required in ("offenen Fall", "zuerst ablaufende", "ohne Freigabe", "Person oder Fall"):
            assert required in de, required
        en = _bullet(DOMAIN_SUMMARY_EN.read_text(encoding="utf-8"), "Retention periods.")
        for required in ("open case", "expires first", "without approval", "person or case"):
            assert required in en, required

    def test_audit_trail_glossary_row_names_entries_without_facility(self):
        row = next(
            line
            for line in DOMAIN_CONCEPT_DE.read_text(encoding="utf-8").splitlines()
            if line.startswith("| Audit-Trail |")
        )
        assert AUDIT_WITHOUT_FACILITY in row
        assert "unbefristet" in row

    def test_retention_bullets_name_the_monthly_aggregate_remainder(self):
        """``carry_deleted_event`` adds every deleted event to its month's snapshot."""
        de = _bullet(DOMAIN_CONCEPT_DE.read_text(encoding="utf-8"), "Löschfristen.")
        assert "nicht zu einer verbleibenden Zählung" not in de
        assert "Statistik-Aggregat ihres Monats" in de
        en = _bullet(DOMAIN_SUMMARY_EN.read_text(encoding="utf-8"), "Retention periods.")
        assert "not aggregation that preserves anonymous counts" not in en
        assert "monthly statistics aggregate" in en


# ---------------------------------------------------------------------------
# Refs #2492: retention inventory, four-eyes scope, audit logging, free-text
# inventory, third-country transfers and history redaction.
# ---------------------------------------------------------------------------

_NUMBER_WORDS = {7: "sieben", 8: "acht", 9: "neun", 10: "zehn", 11: "elf", 12: "zwölf"}
_DSFA_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*`([^`|]+)`\s*\|", re.MULTILINE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;:])\s+")


def _behaviour_test(module: str, qualname: str):
    """Resolve a behaviour test that backs a template claim.

    The template guards below only check wording; whether the code behaves as
    worded is proven by the referenced behaviour test. Resolving it here ties
    the claim to that test: if the test is removed, renamed or parked as
    skip/xfail (on the test, its class or its module), the claim guard fails
    instead of pinning a stale promise.
    """
    import importlib

    target = importlib.import_module(f"tests.{module}")
    marks = set()
    for part in ["", *qualname.split(".")]:
        if part:
            target = getattr(target, part)
        declared = getattr(target, "pytestmark", [])
        marks |= {mark.name for mark in (declared if isinstance(declared, list) else [declared])}
    assert not marks & {"skip", "skipif", "xfail"}, (module, qualname, marks)
    return target


def _probe_module(monkeypatch, *, module_mark=None, class_mark=None):
    """Register ``tests._behaviour_probe`` with one class holding one test."""
    import sys
    import types

    module = types.ModuleType("tests._behaviour_probe")

    class TestProbe:
        def test_probe(self):
            pass

    if class_mark is not None:
        TestProbe = class_mark(TestProbe)
    module.TestProbe = TestProbe
    if module_mark is not None:
        module.pytestmark = module_mark
    monkeypatch.setitem(sys.modules, "tests._behaviour_probe", module)


@pytest.mark.parametrize(
    "marks",
    [
        {"class_mark": pytest.mark.skip(reason="probe")},
        {"class_mark": pytest.mark.xfail(reason="probe")},
        {"module_mark": pytest.mark.skipif(True, reason="probe")},
        {"module_mark": [pytest.mark.xfail(reason="probe")]},
    ],
)
def test_behaviour_test_rejects_inherited_skip_marks(monkeypatch, marks):
    _probe_module(monkeypatch, **marks)
    with pytest.raises(AssertionError):
        _behaviour_test("_behaviour_probe", "TestProbe.test_probe")


def test_behaviour_test_accepts_an_unmarked_test(monkeypatch):
    _probe_module(monkeypatch)
    assert _behaviour_test("_behaviour_probe", "TestProbe.test_probe")


def _settings_retention_fields() -> list[str]:
    """Retention periods read from the ``Settings`` model, not from the hash.

    Every integer ``Settings`` field counted in days or months is a retention
    period; the hours/minutes fields are session limits and the remaining
    integers are counts or sizes. Deriving the list here keeps the inventory
    independent of ``_retention_values``, which the hash test below checks.
    """
    from django.db import models as dj_models

    from core.models import Settings

    return [
        field.name
        for field in Settings._meta.get_fields()
        if isinstance(field, dj_models.IntegerField) and field.name.endswith(("_days", "_months"))
    ]


def _retention_settings() -> list[str]:
    # The document type period is the only one that lives on another model
    # (row 4 of the DSFA table).
    document_type_periods = [
        f"DocumentType.{field.name}" for field in DocumentType._meta.get_fields() if field.name.startswith("retention_")
    ]
    return [*_settings_retention_fields(), *document_type_periods]


def _sentences(text: str) -> list[str]:
    return _SENTENCE_SPLIT_RE.split(text)


@pytest.fixture
def vvt_template(facility):
    return render_document("verarbeitungsverzeichnis", facility)[0]


def _vvt_texts(activity_id: str) -> list[str]:
    activity = next(a for a in PROCESSING_ACTIVITIES if a["id"] == activity_id)
    texts = []
    for value in activity.values():
        values = value if isinstance(value, list) else [value]
        texts.extend(str(v) for v in values)
    return texts


@pytest.mark.django_db
class TestRetentionInventory:
    def test_premise_handover_period_is_a_retention_setting(self):
        from core.models import Settings
        from core.services.client.dsgvo_package import _retention_values

        assert Settings._meta.get_field("retention_handover_notes_days").default == 90
        assert _retention_values(None)["retention_handover_notes_days"] == 90

    def test_model_inventory_is_complete(self):
        assert sorted(_retention_settings()) == sorted(
            [
                "retention_anonymous_days",
                "retention_identified_days",
                "retention_qualified_days",
                "retention_activities_days",
                "retention_handover_notes_days",
                "client_trash_days",
                "auditlog_retention_months",
                "eventhistory_detail_months",
                "eventhistory_chronicle_months",
                "DocumentType.retention_days",
            ]
        )

    def test_every_settings_period_changes_the_package_hash(self, facility):
        from core.models import Settings
        from core.services.client.dsgvo_package import _settings_hash

        settings_obj, _ = Settings.objects.get_or_create(facility=facility)
        baseline = _settings_hash(settings_obj)
        for name in _settings_retention_fields():
            original = getattr(settings_obj, name)
            setattr(settings_obj, name, original + 1)
            assert _settings_hash(settings_obj) != baseline, name
            setattr(settings_obj, name, original)

    def test_dsfa_table_lists_every_retention_setting_with_its_count(self, facility):
        content = render_document("dsfa", facility)[0]
        rows = {setting: (nr, label, value) for nr, label, value, setting in _DSFA_ROW_RE.findall(content)}
        expected = _retention_settings()

        assert sorted(rows) == sorted(expected)
        word = _NUMBER_WORDS[len(expected)]
        assert f"kennt {word} konfigurierbare Aufbewahrungsfristen. Alle {word} sind" in content
        assert f"greift **keine** der {word} Fristen" in content

    def test_deletion_concept_lists_every_retention_setting(self, deletion_concept):
        table = _section(deletion_concept, "## 2. Fristen und Löschwirkung", "## 3.")
        for setting in _retention_settings():
            assert f"| `{setting}` |" in table, setting

    @pytest.mark.parametrize("slug", ["dsfa", "loeschkonzept"])
    def test_handover_period_renders_configured_value_and_hard_deletion(self, facility, slug):
        from core.models import Settings

        Settings.objects.update_or_create(facility=facility, defaults={"retention_handover_notes_days": 60})
        facility.refresh_from_db()
        content = render_document(slug, facility)[0]

        row = next(line for line in content.splitlines() if "`retention_handover_notes_days`" in line)
        assert "| 60 Tage |" in row
        assert "endgültig gelöscht" in row
        assert "kein Legal Hold" in row

    def test_vvt_names_the_handover_period(self, vvt_template):
        assert "Übergabenotizen" in _section(vvt_template, "### 1.7 Löschfristen", "### 1.8")
        retention = str(next(a for a in PROCESSING_ACTIVITIES if a["id"] == "falldaten")["retention_period"])
        assert "Übergabenotizen" in retention
        assert "90 Tage" in retention


def _four_eyes_scope_ok(text: str) -> bool:
    """A four-eyes promise names the person and the stage limit for events and cases."""
    return "Person" in text and "Mit Zusatzangaben" in text


def _file_purge_names_both_locks(content: str) -> bool:
    """The detailed file purge block names the legal hold and the Art. 18 lock.

    Every block about the purge without a second person that names one lock
    must name the other, and at least one block (the one that sets the purge
    apart from the four-eyes deletion of persons) must name both.
    """
    blocks = [block for block in _blocks(content) if "Datei" in block and "ohne zweite Person" in block]

    def has_hold(block: str) -> bool:
        return "Legal Hold" in block or "Aufbewahrungssperre" in block

    def has_restriction(block: str) -> bool:
        return "Art. 18" in block

    if not blocks:
        return False
    if any(has_hold(block) != has_restriction(block) for block in blocks):
        return False
    return any(has_hold(block) and has_restriction(block) and "Augen" in block for block in blocks)


@pytest.mark.django_db
class TestFourEyesScope:
    def test_premise_file_purge_needs_no_second_person(self):
        from core.views.attachments import AttachmentPurgeView
        from core.views.mixins import DeletionConfirmerRequiredMixin, LeadOrAdminRequiredMixin

        assert issubclass(AttachmentPurgeView, LeadOrAdminRequiredMixin)
        assert not issubclass(AttachmentPurgeView, DeletionConfirmerRequiredMixin)

    @pytest.mark.parametrize("slug", ["dsfa", "toms", "loeschkonzept"])
    def test_templates_name_the_file_purge_without_second_person(self, facility, slug):
        content = _flat(render_document(slug, facility)[0])
        assert any("Datei" in s and "ohne zweite Person" in s for s in _sentences(content)), slug

    @pytest.mark.parametrize("slug", ["dsfa", "toms", "loeschkonzept"])
    def test_file_purge_names_both_locks(self, facility, slug):
        _behaviour_test(
            "test_mutation_pins_099_attachments_1",
            "TestPurgeAttachmentChainArticle18MessageIsExact.test_artikel_18_sperre_meldet_den_exakten_wortlaut",
        )
        _behaviour_test(
            "test_mutation_pins_099_attachments_1",
            "TestPurgeAttachmentChainLegalHoldMessageIsExact.test_legal_hold_meldet_den_exakten_wortlaut",
        )
        assert _file_purge_names_both_locks(render_document(slug, facility)[0]), slug

    @pytest.mark.parametrize(
        "text",
        [
            # The detailed block names the Art. 18 lock but drops the legal hold.
            "Dateien vernichten Leitung und Anwendungsbetreuung ohne zweite Person,"
            " außer bei einer Sperre nach Art. 18;"
            " die Löschung von Personen verlangt vier Augen.",
            # The legal hold is named, the Art. 18 lock is missing.
            "Dateien vernichten Leitung und Anwendungsbetreuung ohne zweite Person, außer bei einem Legal Hold;"
            " die Löschung von Personen verlangt vier Augen.",
        ],
    )
    def test_file_purge_guard_rejects_a_missing_lock(self, text):
        assert not _file_purge_names_both_locks(text)

    def test_dsfa_residual_risk_names_the_purge_path(self, dsfa):
        residual = _section(dsfa, "**Restrisiko:** Aus beidem folgt", "**Auskunftsrecht")
        assert "Leitung und Anwendungsbetreuung" in residual
        assert "ohne zweite Person" in residual

    def test_toms_role_table_does_not_generalise_four_eyes(self, facility):
        rows = _role_table_rows(render_document("toms", facility)[0])
        for role in ("Anwendungsbetreuung", "Leitung"):
            delete_cell = rows[role][2]
            assert delete_cell != "4-Augen", role
            assert "Dateien" in delete_cell, role

    def test_toms_role_table_names_what_staff_may_delete(self, facility):
        from core.views.case_deletion import CaseDeleteRequestView
        from core.views.client_deletion import ClientDeleteRequestView
        from core.views.events import EventDeleteView
        from core.views.mixins import LeadOrAdminRequiredMixin, StaffRequiredMixin

        # Staff open deletion of persons and cases they can see, but only
        # their own events (``EventDeleteView.dispatch`` checks ``created_by``).
        for view in (CaseDeleteRequestView, ClientDeleteRequestView, EventDeleteView):
            assert issubclass(view, StaffRequiredMixin), view
            assert not issubclass(view, LeadOrAdminRequiredMixin), view
        _behaviour_test("test_permissions", "TestStaffEventDeletion.test_staff_cannot_delete_others_event")
        _behaviour_test("test_permissions", "TestStaffEventDeletion.test_staff_can_delete_own_event")

        delete_cell = _role_table_rows(render_document("toms", facility)[0])["Fachkraft"][2]
        assert "identifiziert" not in delete_cell, delete_cell
        for part in ("Personen", "Antrag", "eigene Ereignisse", "Fälle", "Mit Zusatzangaben"):
            assert part in delete_cell, (part, delete_cell)

    def test_vvt_four_eyes_items_name_their_scope(self):
        for activity in PROCESSING_ACTIVITIES:
            if activity["id"] == "backup":
                continue  # restore recommendation, no deletion promise
            for text in _vvt_texts(activity["id"]):
                if "Vier-Augen" in text or "4-Augen" in text:
                    assert _four_eyes_scope_ok(text), (activity["id"], text)


# Upper-case identifiers in backticks inside the TOM section on input control
# are audit action codes; each must exist in ``AuditLog.Action``.
_BACKTICK_CODE_RE = re.compile(r"`([A-Z][A-Z_]{2,})`")


@pytest.mark.django_db
class TestAuditLoggingClaims:
    def test_premise_attachment_actions(self):
        names = set(AuditLog.Action.names)
        assert {"ATTACHMENT_UPLOAD", "ATTACHMENT_DELETE", "DOWNLOAD"} <= names
        assert not {"FILE_UPLOAD", "FILE_DOWNLOAD", "FILE_REPLACE"} & names

    def test_toms_input_control_names_only_existing_actions(self, facility):
        content = render_document("toms", facility)[0]
        section = _section(content, "### 2.2 Eingabekontrolle", "## 3.")
        codes = _BACKTICK_CODE_RE.findall(section)
        assert codes, "input control names no action codes"
        assert set(codes) <= set(AuditLog.Action.names), set(codes) - set(AuditLog.Action.names)

    def test_toms_event_history_is_not_called_append_only(self, toms):
        assert "Event-History (append-only)" not in toms
        bullet = next(s for s in toms.split("- [x] ") if s.startswith("Ereignis-Historie"))
        assert "redigiert" in bullet
        assert "löscht" in bullet

    def test_premise_audit_pruning_deletes_through_the_application(self):
        _behaviour_test("test_retention_auditlog", "TestPruneAuditlog.test_prune_deletes_entries_older_than_cutoff")

    def test_premise_chain_backfill_bypasses_the_trigger(self):
        import inspect

        from core.management.commands import backfill_audit_chain

        _behaviour_test("test_audit_chain", "TestBackfill.test_backfill_recomputes_correct_hashes")
        assert "bypass_replication_triggers()" in inspect.getsource(backfill_audit_chain)

    def test_vvt_audit_log_names_every_trigger_bypass(self):
        bypass = [text for text in _vvt_texts("auditlog") if "umgeh" in text]
        assert bypass
        for text in bypass:
            assert "nur der Aufbewahrungslauf" not in text, text
            assert "Aufbewahrungslauf" in text, text
            assert "Hash-Kette" in text, text

    def test_vvt_audit_log_entry_names_the_pruning_exception(self):
        for text in _vvt_texts("auditlog"):
            assert "kein UPDATE oder DELETE durch die Anwendung" not in text
            if "Append-Only" in text or "UPDATE/DELETE blockiert" in text:
                assert "Aufbewahrungslauf" in text, text

    def test_premise_lockout_is_derived_from_audit_entries(self):
        _behaviour_test("test_auth", "TestLoginLockoutService.test_locked_after_threshold")
        _behaviour_test("test_auth", "TestLoginLockoutService.test_not_locked_after_unlock")

    def test_vvt_does_not_promise_periods_the_code_lacks(self):
        auth = str(next(a for a in PROCESSING_ACTIVITIES if a["id"] == "auth_login")["retention_period"])
        assert "Lockouts: 90 Tage" not in auth
        assert "Audit-Frist" in auth
        requests = str(next(a for a in PROCESSING_ACTIVITIES if a["id"] == "dsgvo_requests")["retention_period"])
        assert "im AuditLog dauerhaft" not in requests
        assert "Audit-Frist" in requests


# Plain-text free-text fields outside the field encryption (TOM § 1.5).
FREE_TEXT_FIELDS = (
    ("Client", "notes"),
    ("Case", "title"),
    ("Case", "description"),
    ("Episode", "title"),
    ("Episode", "description"),
    ("WorkItem", "title"),
    ("WorkItem", "description"),
    ("OutcomeGoal", "title"),
    ("OutcomeGoal", "description"),
    ("Milestone", "title"),
    ("DeletionRequest", "reason"),
    ("LegalHold", "reason"),
    ("AuditLog", "detail"),
)


_BACKTICK_FIELD_RE = re.compile(r"`([A-Z][A-Za-z]+\.[a-z_]+)`")

# Free texts that never carry client data in the first place: the audit
# detail is minimised when written (Refs #1093), not redacted afterwards.
WRITE_TIME_MINIMISED = {("AuditLog", "detail")}


def _anonymisation_clears(model: str, field: str) -> bool:
    """True when both residue sweeps prove that anonymisation clears the field.

    ``test_no_residue_after_anonymize`` (manual) and
    ``test_no_residue_after_retention_k_anonymization`` (retention run, k-
    anonymising bridge) scan every ``pii`` column of every scoped table after
    anonymising a fully populated person; a table listed in their xfail maps
    is a known leak.
    """
    from django.apps import apps

    from tests import test_pii_residue_sweep as sweep
    from tests._residue_expectations import COLUMN_CLASSIFICATION

    _behaviour_test("test_pii_residue_sweep", "test_no_residue_after_anonymize")
    _behaviour_test("test_pii_residue_sweep", "test_no_residue_after_retention_k_anonymization")
    if (model, field) in WRITE_TIME_MINIMISED:
        return False
    table = apps.get_model("core", model)._meta.db_table
    kinds = {rule.kind for rule in COLUMN_CLASSIFICATION if (rule.table, rule.column) == (table, field)}
    return (
        kinds == {"pii"}
        and table in sweep.SCOPED_TABLES
        and table not in sweep.ANONYMIZE_XFAIL
        and table not in sweep.RETENTION_XFAIL
    )


@pytest.mark.django_db
class TestFreeTextInventory:
    def test_premise_fields_are_plain_columns(self):
        from django.apps import apps
        from django.db import models as dj_models

        for model_name, field_name in FREE_TEXT_FIELDS:
            field = apps.get_model("core", model_name)._meta.get_field(field_name)
            assert type(field) in (dj_models.CharField, dj_models.TextField, dj_models.JSONField), (
                model_name,
                field_name,
            )

    def test_toms_name_every_plain_free_text_field(self, facility):
        content = render_document("toms", facility)[0]
        section = _section(content, "### 1.5 Restrisiko", "## 2.")
        for model_name, field_name in FREE_TEXT_FIELDS:
            assert f"`{model_name}.{field_name}`" in section, f"{model_name}.{field_name}"

    def test_toms_name_what_anonymisation_cleans(self, facility):
        section = _flat(_section(render_document("toms", facility)[0], "### 1.5 Restrisiko", "## 2."))
        claim = next(s for s in _sentences(section) if s.startswith("Die Anonymisierung einer Person"))
        exceptions = set(_BACKTICK_FIELD_RE.findall(claim.split(":", 1)[0].split("außer", 1)[1]))
        expected = {f"{model}.{field}" for model, field in FREE_TEXT_FIELDS if not _anonymisation_clears(model, field)}
        assert exceptions == expected, claim
        for sentence in _sentences(section):
            if "nicht" in sentence and "Anonymisierung" in sentence:
                assert "Begründung" not in sentence, sentence
        assert "ohne Warnhinweis" in section

    def test_vvt_request_retention_follows_the_residue_sweep(self):
        retention = str(next(a for a in PROCESSING_ACTIVITIES if a["id"] == "dsgvo_requests")["retention_period"])
        assert "Begründungen von Löschanträgen bleiben" not in retention
        sentence = next(s for s in retention.split(". ") if "Begründungen" in s)
        redacted = _anonymisation_clears("DeletionRequest", "reason") and _anonymisation_clears("LegalHold", "reason")
        assert redacted
        assert "[Anonymisiert]" in sentence, sentence
        assert "Aufbewahrungssperren" in sentence, sentence


@pytest.mark.django_db
class TestThirdCountryTransfer:
    STALE = ("Eine Übermittlung in Drittländer findet nicht statt.",)

    def test_premise_error_tracking_is_optional(self):
        source = (REPO_ROOT / "src" / "anlaufstelle" / "settings" / "_sentry.py").read_text(encoding="utf-8")
        assert "SENTRY_DSN" in source

    @pytest.mark.parametrize("slug", ["informationspflichten", "verarbeitungsverzeichnis"])
    def test_templates_tie_transfers_to_facility_configuration(self, facility, slug):
        content = _flat(render_document(slug, facility)[0])
        for stale in self.STALE:
            assert stale not in content
        assert "### 1.6 Übermittlung in Drittländer Keine." not in content
        assert "nur, wenn die Einrichtung" in content
        assert "Fehlerüberwachung" in content


@pytest.mark.django_db
class TestHistoryRedactionClaims:
    def test_dsfa_and_deletion_concept_keep_the_version_and_the_newest_values(self, facility):
        for slug in ("dsfa", "loeschkonzept"):
            content = render_document(slug, facility)[0]
            row = next(line for line in content.splitlines() if "`eventhistory_detail_months`" in line)
            assert "die Fassung selbst" in row, slug
            assert "jüngste Fassung" in row, slug

    def test_toms_and_vvt_name_redaction_and_its_limits(self, toms):
        bullet = next(s for s in toms.split("- [x] ") if s.startswith("Ereignis-Historie"))
        assert "jüngste Fassung" in bullet
        assert "Sicherungsstände" in bullet
        retention = str(next(a for a in PROCESSING_ACTIVITIES if a["id"] == "falldaten")["retention_period"])
        assert "jüngste Fassung" in retention

    def test_vvt_names_the_anonymised_person_exception(self):
        _behaviour_test(
            "test_eventhistory_retention",
            "TestAnonymisierteFallenInDenSweep.test_historie_einer_anonymisierten_person_wird_erfasst",
        )
        retention = str(next(a for a in PROCESSING_ACTIVITIES if a["id"] == "falldaten")["retention_period"])
        sentence = next(s for s in retention.split(". ") if "jüngste Fassung" in s)
        assert "anonymisierten Person" in sentence, sentence
        assert "alle Fassungen" in sentence, sentence


_VERBATIM_CLAIM_RE = re.compile(r"\(wortgleich zur DSFA[^)]*\):\s*(.+)")


@pytest.mark.django_db
class TestVerbatimClaims:
    def test_verbatim_claims_are_verbatim(self, vvt_template, dsfa):
        for claim in _VERBATIM_CLAIM_RE.findall(vvt_template):
            assert _flat(claim) in dsfa, claim


# ---------------------------------------------------------------------------
# Redaction of the shipped documents (Refs #2492, #2477): no internal issue
# references, the target group is a facility entry, value lists follow the
# model choices, and the MFA/lockout promises follow the shipped defaults.
# ---------------------------------------------------------------------------

_TEMPLATE_SLUGS = (
    "av-vertrag",
    "dsfa",
    "informationspflichten",
    "loeschkonzept",
    "mitarbeiter-informationspflichten",
    "toms",
    "verarbeitungsverzeichnis",
)
_ISSUE_REF_RE = re.compile(r"\(Ref\.|#\d{2,}")
_STALE_MFA_CLAIMS = ("Optionale 2FA", "sensible Rollen", "Argon2", "(30 Min)", "zuvor ausschließlich optional")


_DSFA_LOCKOUT_EXEMPTION = (
    "; das Systemadministrationskonto ist von der Sperre ausgenommen und nur durch die Ratelimits gebremst"
)


def _all_vvt_texts() -> list[str]:
    return [text for activity in PROCESSING_ACTIVITIES for text in _vvt_texts(activity["id"])]


@pytest.mark.django_db
class TestDocumentRedaction:
    @pytest.mark.parametrize("slug", _TEMPLATE_SLUGS)
    def test_templates_carry_no_issue_references(self, facility, slug):
        content = render_document(slug, facility)[0]
        assert not _ISSUE_REF_RE.search(content), _ISSUE_REF_RE.search(content)

    def test_vvt_view_carries_no_issue_references(self):
        for text in _all_vvt_texts():
            assert not _ISSUE_REF_RE.search(text), text

    def test_target_group_is_a_facility_entry(self, facility):
        dsfa_scope = _section(render_document("dsfa", facility)[0], "### 1.1", "### 1.2")
        vvt_people = _section(render_document("verarbeitungsverzeichnis", facility)[0], "### 1.2", "### 1.3")
        for text in (dsfa_scope, vvt_people):
            assert "[Zielgruppe eintragen" in text, text
            assert "mit Jugendlichen und jungen Erwachsenen" not in text
            assert "Einrichtung (Jugendliche und junge Erwachsene)" not in text

    @pytest.mark.parametrize("slug", ["informationspflichten", "verarbeitungsverzeichnis"])
    def test_age_groups_follow_the_model_choices(self, facility, slug):
        from core.models import Client

        lines = render_document(slug, facility)[0].splitlines()
        row = next(line for line in lines if line.startswith("| Altersgruppe"))
        for label in Client.AgeCluster.labels:
            assert str(label) in row, (label, row)

    def test_vvt_staff_categories_match_the_employee_notice(self, facility):
        vvt_staff = _section(render_document("verarbeitungsverzeichnis", facility)[0], "### 2.2", "### 2.3")
        notice = _section(render_document("mitarbeiter-informationspflichten", facility)[0], "## 3.", "## 4.")
        for category in (
            "E-Mail-Adresse",
            "Einrichtungszuordnung",
            "Löschanträgen",
            "Telefonnummer",
            "Kontonotiz",
            "Passwort-Hash",
            "TOTP-Secrets",
            "Wiederherstellungscodes",
            "IP-Adresse",
        ):
            assert category in _flat(vvt_staff), category
            assert category in _flat(notice), category

    def test_premise_mfa_and_lockout_defaults(self):
        from django.conf import settings

        from core.constants import MFA_SETUP_GRACE_DAYS
        from core.services.security.login_lockout import LOCKOUT_THRESHOLD, LOCKOUT_WINDOW

        assert set(settings.MFA_ENFORCED_ROLES_IMMEDIATE) == {"super_admin", "facility_admin"}
        assert set(settings.MFA_ENFORCED_ROLES_GRACE) == {"lead", "staff", "assistant"}
        assert MFA_SETUP_GRACE_DAYS == 30
        assert LOCKOUT_THRESHOLD == 10
        assert LOCKOUT_WINDOW.total_seconds() == 15 * 60

    def test_templates_and_vvt_state_the_role_default(self, toms, dsfa):
        for name, text in (("toms", toms), ("dsfa", dsfa), ("vvt", " ".join(_all_vvt_texts()))):
            for stale in _STALE_MFA_CLAIMS:
                assert stale not in text, (name, stale)
            assert "Rollen-Default" in text, name
            assert "30 Tage" in text, name
        for name, text in (("toms", toms), ("dsfa", dsfa)):
            assert "innerhalb von 15 Minuten je Konto und Quell-IP" in text, name
            assert "ausgenommen" in text, name

    def test_every_lockout_claim_names_the_super_admin_exemption(self, toms_raw, dsfa_raw):
        """``login_lockout.is_locked`` never locks ``super_admin`` (behaviour:
        ``test_login_lockout_super_admin.py``), so each lockout promise must
        carry the exemption in the same statement, not only somewhere else.

        Template claims are taken from the unflattened rendering: the list item
        or paragraph holding the lockout promise is selected first and only then
        normalised, so an unrelated "ausgenommen" elsewhere cannot satisfy it.
        """
        claims = [("vvt", t) for t in _vvt_texts("auth_login") if "Kontosperre nach" in t]
        for name, text in (("toms", toms_raw), ("dsfa", dsfa_raw)):
            assert "\n" in text, f"{name}: the guard needs the unflattened rendering"
            claims += [(name, block) for block in _blocks(text) if "Kontosperrung nach" in block]
        summary = _read_flat(DOMAIN_SUMMARY_EN)
        claims += [("summary", _section(summary, "- **Account lockout.**", "- **"))]
        assert {name for name, _ in claims} == {"vvt", "toms", "dsfa", "summary"}
        for name, text in claims:
            assert "ausgenommen" in text or "exempt" in text, (name, text)
        assert "consecutive failed login attempts" not in summary

    def test_lockout_guard_rejects_dsfa_without_its_lockout_exemption(self):
        templates = REPO_ROOT / "src" / "core" / "dsgvo_templates"
        toms = _flat((templates / "toms.md").read_text(encoding="utf-8"))
        dsfa = (templates / "dsfa.md").read_text(encoding="utf-8")
        exemption = _DSFA_LOCKOUT_EXEMPTION
        assert dsfa.count(exemption) == 1
        dsfa = _flat(dsfa.replace(exemption, ""))
        assert "Systemadministrationskonto ist von der Sperre ausgenommen" not in dsfa
        assert "ausgenommen" in dsfa  # unrelated retention/Article 18 exceptions remain

        with pytest.raises(AssertionError):
            self.test_every_lockout_claim_names_the_super_admin_exemption(toms, dsfa)

    def test_lockout_guard_rejects_an_exemption_outside_the_lockout_item(self, toms_raw, dsfa_raw):
        exemption = _DSFA_LOCKOUT_EXEMPTION
        assert dsfa_raw.count(exemption) == 1
        dsfa = dsfa_raw.replace(exemption, "")
        assert "ausgenommen" in dsfa  # unrelated retention/Article 18 exceptions remain

        with pytest.raises(AssertionError, match="dsfa"):
            self.test_every_lockout_claim_names_the_super_admin_exemption(toms_raw, dsfa)


@pytest.mark.architecture
class TestAwarenessMfaClaims:
    def test_awareness_guide_states_the_role_default(self):
        text = _flat((REPO_ROOT / "docs" / "packages-awareness.md").read_text(encoding="utf-8"))
        assert "MFA organisatorisch verpflichtend" not in text
        assert "rollenbezogen für `lead` und `facility_admin`" not in text
        assert "per Rollen-Default für alle Rollen verpflichtend" in text
        assert "`MFA_ENFORCED_ROLES_GRACE`" in text


# ---------------------------------------------------------------------------
# Real-name promises (Refs #2430): pseudonymisation is the intended workflow
# and there is no real-name field, but nothing technically keeps names out of
# pseudonyms, notes, free-text fields or attachments, and an event without a
# person is not automatically anonymous by content.
# ---------------------------------------------------------------------------

_README_DE = REPO_ROOT / "README.md"
_README_EN = REPO_ROOT / "README.en.md"


def _read_flat(path: Path) -> str:
    return _flat(path.read_text(encoding="utf-8"))


@pytest.mark.django_db
def test_premise_no_real_name_field_and_personless_free_text():
    from core.models import Client

    assert not [f.name for f in Client._meta.get_fields() if "name" in f.name and f.name != "pseudonym"]
    package = yaml.safe_load((PACKAGES_DIR / "awareness.yaml").read_text(encoding="utf-8"))
    incident = next(t for t in package["document_types"] if t["slug"] == "gewaltvorfall")
    assert not incident.get("min_contact_stage")
    field_slugs = {ref["template_slug"] for ref in incident["field_refs"]}
    assert {"ort-im-veranstaltungsgelaende", "notiz-vorfall"} <= field_slugs


@pytest.mark.architecture
class TestRealNamePromises:
    def test_readmes_do_not_promise_technical_impossibility(self):
        de, en = _read_flat(_README_DE), _read_flat(_README_EN)
        assert "weder versehentlich noch absichtlich" not in de
        assert "neither accidentally nor intentionally" not in en
        assert "is still not anonymous by content" not in en
        for part in ("Pflichtfeld", "Pseudonymisierung", "Anhänge", "nicht automatisch inhaltlich anonym"):
            assert part in de, part
        for part in ("required field", "Pseudonymisation", "attachments", "not automatically anonymous by content"):
            assert part in en, part

    def test_domain_concept_separates_personless_from_anonymous_content(self):
        text = _section(_read_flat(DOMAIN_CONCEPT_DE), "**Stufe 1: Anonym.**", "**Stufe 3:")
        assert "Im rechtlichen Sinn werden keine personenbezogenen Daten verarbeitet" not in text
        assert "existiert nur im Wissen der Mitarbeitenden" not in text
        assert "nicht automatisch anonym" in text
        assert "Pflichtfeld" in text

    def test_english_summary_separates_personless_from_anonymous_content(self):
        text = _section(_read_flat(DOMAIN_SUMMARY_EN), "- **Anonymous** --", "- **Qualified**")
        assert "stores no personal data" not in text
        assert "No real name enters the database" not in text
        assert "does not make it anonymous by content" in text
        assert "required field" in text

    def test_dpa_does_not_promise_no_real_names(self):
        text = (REPO_ROOT / "src" / "core" / "dsgvo_templates" / "av-vertrag.md").read_text(encoding="utf-8")
        assert "(keine Klarnamen)" not in text
        assert "kein Pflichtfeld für Klarnamen" in _flat(text)


# ---------------------------------------------------------------------------
# Data protection overview (Refs #2492): file deletion paths and the staff
# references of handover notes.
# ---------------------------------------------------------------------------

_PRIVACY_DE = REPO_ROOT / "docs" / "datenschutz.md"
_PRIVACY_EN = REPO_ROOT / "docs" / "en" / "data-protection.md"


@pytest.mark.django_db
def test_premise_manual_case_purge_deletes_attachment_rows_and_files(
    case_open, authz_case_attachment, staff_user, admin_user
):
    """Behaviour premise for the case purge path in the privacy overview.

    The retention-driven purge is covered by
    ``test_case_deletion_workflow.py::TestTrashExpiryRedaction::test_attachments_and_markers_are_gone``;
    this covers the immediate purge by the application administration.
    """
    from core.models import Attachment
    from core.services.case import anonymize_case_now, soft_delete_case
    from core.services.file_vault import get_attachment_path

    path = get_attachment_path(authz_case_attachment)
    assert path.exists()
    soft_delete_case(case_open, staff_user)
    anonymize_case_now(case_open, admin_user)
    assert not Attachment.objects.filter(case=case_open).exists()
    assert not path.exists()


def test_premise_handover_notes_name_staff():
    from core.models import ShiftHandoverNote

    fields = {field.name for field in ShiftHandoverNote._meta.get_fields()}
    assert {"author", "updated_by"} <= fields


@pytest.mark.architecture
class TestPrivacyOverview:
    def test_file_deletion_paths_include_the_case_purge(self):
        de, en = _read_flat(_PRIVACY_DE), _read_flat(_PRIVACY_EN)
        assert "**drei** physische Löschpfade" not in de
        assert "**three** physical deletion paths" not in en
        for text in (de, en):
            assert "`anonymize_case`" in text
            assert "Art. 18" in text

    def test_handover_note_row_names_author_and_editor(self):
        for path, stale in (
            (_PRIVACY_DE, "nur Einrichtung, Schichtdatum und Zeitfenster"),
            (_PRIVACY_EN, "only facility, shift date and time window"),
        ):
            row = next(line for line in path.read_text(encoding="utf-8").splitlines() if "`ShiftHandoverNote`" in line)
            assert stale not in row, path
            assert "`author`" in row and "`updated_by`" in row, path
