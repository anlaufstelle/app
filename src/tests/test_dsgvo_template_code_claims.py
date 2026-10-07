"""Guards: GDPR templates and the domain concept describe what the code does.

Three claim families are pinned against the implementation:

* Offline key lifecycle (Refs #2426): the AES-GCM ``CryptoKey`` is persisted
  in IndexedDB (``src/static/js/crypto.js``), closing a tab or restarting the
  device is not a wipe, the idle check only locks the key while unsynced work
  exists, and index metadata stays in clear text next to the envelope.
  Locked ciphertext only becomes readable again while password and offline
  salt are unchanged; role change, deactivation and a correct-password login
  against a locked account rotate the salt.
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
from typing import NamedTuple

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


_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.;!?]) | -- ")


def _sentences(text: str) -> list[str]:
    return [sentence for sentence in _SENTENCE_BOUNDARY_RE.split(text) if sentence]


class RecoveryGrammar(NamedTuple):
    """How a language attaches the "both inputs unchanged" condition to a promise.

    ``opener`` finds where the promise's own condition (or an exception
    such as "außer wenn"/"unless") begins; ``condition``
    must match right there and spell out a conjunction of both inputs followed
    directly by the unchanged predicate, so negation ("nicht", "not"), a
    disjunction or an input qualifying something else (a "correct-password
    login") cannot satisfy it.
    """

    opener: re.Pattern
    condition: re.Pattern
    inputs: frozenset[str]


#: Both key derivation inputs must stay unchanged for locked ciphertext to
#: become readable again (``deriveSessionKey(password, saltB64Url)`` in ``crypto.js``); a
#: recovery promise naming only one of them overstates what survives.
RECOVERY_CONDITIONS_DE = RecoveryGrammar(
    opener=re.compile(r"\b(?:außer wenn|es sei denn|wenn|sofern|solange)\b"),
    condition=re.compile(
        r"(?:wenn|sofern|solange) (?:das |der )?(Passwort|Offline-Salt) und (?:das |der )?(Passwort|Offline-Salt)"
        r"(?: des Kontos)? unverändert (?:sind|bleiben)\b"
    ),
    inputs=frozenset({"Passwort", "Offline-Salt"}),
)
RECOVERY_CONDITIONS_EN = RecoveryGrammar(
    opener=re.compile(r"\b(?:except if|except when|unless|only if|if|as long as|while)\b"),
    condition=re.compile(
        r"(?:only if|if|as long as|while) (?:the )?(password|offline salt) and (?:the )?(password|offline salt)"
        r" (?:are|remain|stay) unchanged\b"
    ),
    inputs=frozenset({"password", "offline salt"}),
)


def _assert_recovery_claims_name_both_inputs(name: str, text: str, phrase: str, grammar: RecoveryGrammar) -> None:
    """Every promise of renewed readability carries its own "both inputs unchanged" condition.

    Within the promise's sentence, the first condition opener after the
    promise must start a condition that names both inputs, joined by "and",
    directly followed by the unchanged predicate. A mention elsewhere in the
    sentence (e.g. the salt rotation's "correct-password login", before or
    after the promise) cannot stand in for a dropped input.
    """
    hits = [sentence for sentence in _sentences(text) if phrase in sentence]
    assert hits, (name, phrase)
    for sentence in hits:
        after_promise = sentence[sentence.index(phrase) + len(phrase) :]
        opener = grammar.opener.search(after_promise)
        assert opener, (name, "no condition after promise", sentence)
        condition = grammar.condition.match(after_promise, opener.start())
        assert condition, (name, "condition does not keep both inputs unchanged", sentence)
        assert set(condition.groups()) == grammar.inputs, (name, condition.group(0), sentence)


class TestRecoveryClaimHelper:
    """The helper itself rejects a promise that drops one derivation input."""

    @pytest.mark.parametrize(
        "sentence",
        [
            "Der Bestand wird nach erneuter Anmeldung nur wieder lesbar, wenn der Offline-Salt unverändert ist.",
            "Der Bestand wird nach erneuter Anmeldung nur wieder lesbar, wenn das Passwort unverändert ist.",
            "Der Bestand wird mit Passwort und Offline-Salt wieder lesbar.",
        ],
        ids=["salt-only", "password-only", "no-unchanged-condition"],
    )
    def test_incomplete_recovery_promise_is_rejected(self, sentence):
        with pytest.raises(AssertionError):
            _assert_recovery_claims_name_both_inputs("probe", sentence, "wieder lesbar", RECOVERY_CONDITIONS_DE)

    @pytest.mark.parametrize(
        "sentence",
        [
            "Er wird nach einer Anmeldung mit korrektem Passwort nur wieder lesbar, "
            "wenn der Offline-Salt unverändert ist.",
            "Er wird nur wieder lesbar, wenn Passwort und Offline-Salt nicht unverändert sind.",
            "Er wird nur wieder lesbar, wenn Passwort oder Offline-Salt unverändert ist.",
            "Er wird nur wieder lesbar, wenn das Passwort unverändert ist und der Offline-Salt neu erzeugt wurde.",
            "Er wird nur wieder lesbar, wenn der Offline-Salt unverändert ist, "
            "und die Sitzung endet, wenn Passwort und Offline-Salt unverändert sind.",
            "Er wird wieder lesbar. Das gilt, wenn Passwort und Offline-Salt unverändert sind.",
            "Er wird wieder lesbar, außer wenn Passwort und Offline-Salt unverändert sind.",
        ],
        ids=[
            "login-password-before-condition",
            "negated-condition",
            "disjunction",
            "password-only-with-salt-changed",
            "later-condition-in-same-sentence",
            "condition-in-next-sentence",
            "exception-instead-of-condition",
        ],
    )
    def test_recovery_promise_without_its_own_unchanged_condition_is_rejected_de(self, sentence):
        with pytest.raises(AssertionError):
            _assert_recovery_claims_name_both_inputs("probe", sentence, "wieder lesbar", RECOVERY_CONDITIONS_DE)

    @pytest.mark.parametrize(
        "sentence",
        [
            "The ciphertext becomes readable again after the next correct-password login "
            "only if offline salt is unchanged.",
            "The ciphertext becomes readable again only if offline salt is unchanged.",
            "The ciphertext becomes readable again only if password is unchanged.",
            "The ciphertext becomes readable again only if password and offline salt are not unchanged.",
            "The ciphertext becomes readable again only if password or offline salt is unchanged.",
            "The ciphertext becomes readable again unless password and offline salt are unchanged.",
            "The ciphertext becomes readable again -- logout keeps it only if password and offline salt are unchanged.",
        ],
        ids=[
            "login-password-before-condition",
            "salt-only",
            "password-only",
            "negated-condition",
            "disjunction",
            "exception-instead-of-condition",
            "condition-after-clause-boundary",
        ],
    )
    def test_recovery_promise_without_its_own_unchanged_condition_is_rejected_en(self, sentence):
        with pytest.raises(AssertionError):
            _assert_recovery_claims_name_both_inputs("probe", sentence, "readable again", RECOVERY_CONDITIONS_EN)

    @pytest.mark.parametrize(
        "sentence",
        [
            "Er wird nur wieder lesbar, wenn Passwort und Offline-Salt des Kontos unverändert sind.",
            "Er wird nach einer Anmeldung mit korrektem Passwort nur wieder lesbar, "
            "sofern das Passwort und der Offline-Salt unverändert bleiben.",
            "Er wird nur wieder lesbar, wenn Offline-Salt und Passwort unverändert sind; der Salt rotiert sonst.",
        ],
        ids=["template-wording", "articles-and-remain", "reverse-order"],
    )
    def test_complete_recovery_promise_passes_de(self, sentence):
        _assert_recovery_claims_name_both_inputs("probe", sentence, "wieder lesbar", RECOVERY_CONDITIONS_DE)

    @pytest.mark.parametrize(
        "sentence",
        [
            "It becomes readable again after the next login only if password and offline salt are unchanged "
            "-- the server rotates the salt on a correct-password login against a locked account.",
            "It becomes readable again as long as the password and the offline salt remain unchanged.",
            "It becomes readable again only if offline salt and password are unchanged.",
        ],
        ids=["summary-wording", "as-long-as-remain", "reverse-order"],
    )
    def test_complete_recovery_promise_passes_en(self, sentence):
        _assert_recovery_claims_name_both_inputs("probe", sentence, "readable again", RECOVERY_CONDITIONS_EN)


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
            _assert_recovery_claims_name_both_inputs(name, text, "wieder lesbar", RECOVERY_CONDITIONS_DE)
            _assert_salt_rotation_triggers_named(name, text)


@pytest.mark.architecture
class TestOfflineRecoveryDocs:
    def test_domain_concept_ties_recovery_to_password_and_salt(self):
        text = _flat(DOMAIN_CONCEPT_DE.read_text(encoding="utf-8"))
        for claim in STALE_RECOVERY_CLAIMS:
            assert claim not in text, claim
        _assert_recovery_claims_name_both_inputs("domain concept", text, "wieder lesbar", RECOVERY_CONDITIONS_DE)
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
        _assert_recovery_claims_name_both_inputs("en summary", current, "readable again", RECOVERY_CONDITIONS_EN)

    def test_later_password_mention_does_not_stand_in_for_the_recovery_condition(self):
        current = _section(
            _flat(DOMAIN_SUMMARY_EN.read_text(encoding="utf-8")), "## Privacy Architecture", "## Role Model"
        )
        complete = "only if password and offline salt are unchanged"
        assert current.count(complete) == 1
        incomplete = current.replace(complete, "only if offline salt is unchanged")
        # The salt rotation explanation later in the same sentence still says
        # "correct-password login"; it must not satisfy the password condition.
        assert "correct-password login" in incomplete
        with pytest.raises(AssertionError):
            _assert_recovery_claims_name_both_inputs("en summary", incomplete, "readable again", RECOVERY_CONDITIONS_EN)

    def test_login_password_does_not_stand_in_for_the_unchanged_password(self):
        current = _section(
            _flat(DOMAIN_SUMMARY_EN.read_text(encoding="utf-8")), "## Privacy Architecture", "## Role Model"
        )
        complete = "after the next login only if password and offline salt are unchanged"
        assert current.count(complete) == 1
        # A password-qualified login right before "only if" says nothing about
        # the password staying unchanged since encryption.
        incomplete = current.replace(
            complete, "after the next correct-password login only if offline salt is unchanged"
        )
        with pytest.raises(AssertionError):
            _assert_recovery_claims_name_both_inputs("en summary", incomplete, "readable again", RECOVERY_CONDITIONS_EN)


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
