"""Guards: GDPR templates and the domain concept describe what the code does.

Three claim families are pinned against the implementation:

* Offline key lifecycle (Refs #2426): the AES-GCM ``CryptoKey`` is persisted
  in IndexedDB (``src/static/js/crypto.js``), closing a tab or restarting the
  device is not a wipe, the idle check only locks the key while unsynced work
  exists, and index metadata stays in clear text next to the envelope.
* Encryption promises (Refs #2465): only individually configured fields are
  encrypted (``is_encrypted`` in the shipped packages), the database itself
  is not, and server-side field encryption is Fernet, not AES-GCM.
* Visibility (Refs #2466): what a role sees follows sensitivity (maximum of
  document type and field) times role (``ROLE_MAX_SENSITIVITY``), not the
  contact stage; the system administration reads across facilities through
  the admin interface and bypasses RLS.
"""

from pathlib import Path

import pytest
import yaml

from core.models import DocumentType, User
from core.services.client import render_document
from core.services.compliance.sensitivity import ROLE_MAX_SENSITIVITY, SENSITIVITY_RANK
from core.services.compliance.vvt import PROCESSING_ACTIVITIES

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = REPO_ROOT / "src" / "core" / "packages"
FACHKONZEPT = REPO_ROOT / "docs" / "fachkonzept-anlaufstelle.md"
DOMAIN_SUMMARY_EN = REPO_ROOT / "docs" / "en" / "domain-concept-summary.md"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _section(content: str, start: str, end: str) -> str:
    begin = content.index(start)
    return content[begin : content.index(end, begin + len(start))]


@pytest.fixture
def toms(facility):
    return _flat(render_document("toms", facility)[0])


@pytest.fixture
def dsfa(facility):
    return _flat(render_document("dsfa", facility)[0])


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
        text = _flat(FACHKONZEPT.read_text(encoding="utf-8"))
        assert "selbst mit DevTool-Zugriff" not in text
        assert "liefert nur Chiffretext" not in text

    def test_section_16_idle_check_distinguishes_lock_and_wipe(self):
        text = _flat(FACHKONZEPT.read_text(encoding="utf-8"))
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
        text = _flat(FACHKONZEPT.read_text(encoding="utf-8"))
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
