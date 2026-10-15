"""Pytest fixtures for Anlaufstelle test suite."""

import os

import pytest
from cryptography.fernet import Fernet
from django.conf import settings as django_settings
from django.utils import timezone, translation

# E2E-Tests nur collecten wenn playwright + requests installiert sind
try:
    import playwright  # noqa: F401
    import requests  # noqa: F401
except ImportError:
    collect_ignore_glob = ["e2e/*"]

from core.models import (
    AuditLog,
    Case,
    Client,
    DeletionRequest,
    DocumentType,
    DocumentTypeField,
    Episode,
    Event,
    Facility,
    FieldCondition,
    FieldGroup,
    FieldTemplate,
    ImportJob,
    LegalHold,
    Milestone,
    Organization,
    OutcomeGoal,
    RetentionProposal,
    Settings,
    User,
    WorkItem,
)

pytest_plugins = ["tests._skip_profile"]


def pytest_collection_modifyitems(items):
    """``@pytest.mark.postgres``-Items automatisch skippen, wenn die laufende
    DB kein PostgreSQL ist (Refs #1829).

    Ersetzt die vormals je Testklasse/-funktion wiederholte Inline-Guard
    (``if connection.vendor != "postgresql": pytest.skip(...)``) fuer
    RLS-/Trigger-/GUC-/``pg_``-Katalog-Tests — eine Stelle statt ~50 Kopien.
    ``connection.vendor`` liest nur das konfigurierte Backend (kein
    Verbindungsaufbau), daher zur Collection-Zeit sicher aufrufbar.
    """
    from django.db import connection

    if connection.vendor == "postgresql":
        return
    skip_postgres = pytest.mark.skip(
        reason="Benötigt PostgreSQL (RLS/Trigger/GUC) — connection.vendor != 'postgresql'."
    )
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip_postgres)


@pytest.fixture(autouse=True)
def _reset_active_language():
    """Deterministische Startsprache je Test (Refs #1058, P14).

    Sobald deutsche UI-Strings EN-Übersetzungen tragen (z. B. "Ja" → "Yes"),
    macht eine von Nachbartests geleakte aktive Sprache (``translation.activate``
    ohne Rückbau, LocaleMiddleware-Threadlocal) locale-sensitive Assertions
    ordnungsabhängig flaky — unter xdist zuerst als ``"Yes" != "Ja"`` in
    Direktaufruf-Tests sichtbar. Jeder Test startet mit der Default-Sprache;
    Sprachtests aktivieren selbst, was sie brauchen.
    """
    translation.activate(django_settings.LANGUAGE_CODE)
    yield


@pytest.fixture(autouse=True)
def _restore_pil_pixel_limit():
    """Restore Pillow's process-global decompression-bomb limit after each test.

    The upload policy writes ``FILE_VAULT_MAX_IMAGE_PIXELS`` into
    ``PIL.Image.MAX_IMAGE_PIXELS``. A test that lowers the setting would leave a
    tiny limit behind, and a later test that opens an image directly would fail
    with ``DecompressionBombError`` depending on test order.
    """
    from PIL import Image

    original = Image.MAX_IMAGE_PIXELS
    yield
    Image.MAX_IMAGE_PIXELS = original


def xdist_role_suffix() -> str:
    """Worker-Suffix fuer cluster-globale Postgres-Rollennamen unter ``-n auto``.

    Postgres-Rollen sind **cluster-global** — parallele pytest-xdist-Worker
    teilen sich den Rollen-Namespace, obwohl jeder Worker seine eigene Test-DB
    hat. Ohne Suffix kollidieren die ``CREATE ROLE``/``DROP ROLE`` zweier Worker
    (``role already exists`` / ``cannot drop role``). Alle ``transaction=True``-
    Tests, die statische Testrollen anlegen, haengen daher diesen Suffix an ihre
    Rollennamen. Generalisiert das Worker-Erkennungsmuster aus
    ``src/tests/e2e/conftest.py::_get_worker_info`` (Refs #1479, TST-03).

    Seriell (kein xdist, ``PYTEST_XDIST_WORKER`` leer oder ``master``): leerer
    Suffix, damit die Rollennamen unveraendert bleiben.

    Mutationslauf (Refs #2097): mutmut faehrt KEIN xdist (``-x -q -p
    no:randomly`` ohne ``-n``), forkt aber bis ``max_children`` pytest-
    Prozesse gleichzeitig. Seit #2097 hat jeder davon eine eigene Test-DB
    (``MUTMUT_DB_SLOT`` → ``test_<POSTGRES_DB>_mut<N>``) — getrennte
    DATENBANKEN loesen die Rollenkollision aber NICHT, weil Rollen
    clusterweit sind. Ohne Slot-Suffix liefen alle Forks in dasselbe
    ``CREATE ROLE``/``DROP ROLE`` und produzierten ``role already exists``
    bzw. ``cannot drop role`` — denselben Fremdfehler-Kanal und damit
    denselben Bias nach oben, den #2097 beseitigt. Darum haengt der Suffix
    hier auch an der Slot-Nummer.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    if worker and worker != "master":
        return f"_{worker}"
    try:
        slot = int(os.environ.get("MUTMUT_DB_SLOT", "").strip())
    except ValueError:
        return ""
    return f"_mut{slot}" if slot >= 0 else ""


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Testorg")


@pytest.fixture
def facility(organization):
    return Facility.objects.create(organization=organization, name="Teststelle")


@pytest.fixture
def admin_user(facility):
    user = User.objects.create_user(
        username="testadmin",
        role=User.Role.FACILITY_ADMIN,
        facility=facility,
        # Refs #1271: facility_admin ist kein Django-superuser (Least-Privilege);
        # spiegelt Seed/Prod. ``is_staff`` bleibt fuer den Admin-Site-Login.
        is_staff=True,
        # Refs #1053: spiegelt die Backfill-Migration — bestehende
        # Admins/Leitungen tragen das Vier-Augen-Genehmiger-Recht.
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def lead_user(facility):
    user = User.objects.create_user(
        username="testlead",
        role=User.Role.LEAD,
        facility=facility,
        is_staff=True,
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def staff_user(facility):
    user = User.objects.create_user(
        username="teststaff",
        role=User.Role.STAFF,
        facility=facility,
        is_staff=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def assistant_user(facility):
    user = User.objects.create_user(
        username="testassistant",
        role=User.Role.ASSISTANT,
        facility=facility,
        is_staff=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def super_admin_user(db):
    """Refs #867: SUPER_ADMIN ohne Facility-Bindung (Persona Jonas).

    Bewusst ohne ``facility``-Bindung — der super_admin bedient die gesamte
    Installation. ``is_superuser=False``, weil die Rolle nicht mit Djangos
    Auth-Superuser-Flag gleichgesetzt werden darf (RBAC-Property entscheidet).
    """
    user = User.objects.create_user(
        username="testsuperadmin",
        role=User.Role.SUPER_ADMIN,
        facility=None,
        is_staff=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def facility_less_admin(db):
    """Refs #2090: angemeldetes Konto OHNE Einrichtung, das nicht super_admin ist.

    Rolle ``facility_admin``, weil sie jedes Rollen-Gate ausser ``SUPER_ONLY``
    passiert — dieser Akteur erreicht also die tiefste Stelle jeder Route und
    ist damit die schaerfste Einzelsonde auf den Zustand „kein nutzbarer
    Einrichtungskontext".

    ``facility=None`` wird DIREKT gesetzt: ueber ``Facility.delete()`` und das
    ``on_delete=SET_NULL`` des FK ist der Zustand praktisch nicht erzeugbar —
    ein Facility-Hard-Delete laeuft in den ``auditlog_immutable``-Trigger und
    rollt vollstaendig zurueck (docs/admin-guide.md § 2.2c). Produktiv entsteht
    er ueber die Benutzerverwaltung, nicht ueber eine Loeschkaskade.
    """
    user = User.objects.create_user(
        username="testfacilityless",
        role=User.Role.FACILITY_ADMIN,
        facility=None,
        is_staff=True,
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def client_identified(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Test-ID-01",
        created_by=staff_user,
    )


@pytest.fixture
def client_qualified(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.QUALIFIED,
        pseudonym="Test-QU-01",
        created_by=staff_user,
    )


@pytest.fixture
def doc_type_contact(facility):
    doc_type = DocumentType.objects.create(
        facility=facility,
        name="Kontakt",
        # E3 (Refs #1849): Die Berichtszuordnung haengt seit der Umstellung am
        # ``report_key`` — vorher genuegte der Slug ``kontakt`` als
        # Zweitschluessel, den es nicht mehr gibt (Entscheid E-E). ``system_type``
        # bleibt bewusst leer: die beiden Achsen sind unabhaengig (E-D).
        report_key="contact",
        tally_enabled=True,
    )

    ft_dauer = FieldTemplate.objects.create(
        facility=facility,
        name="Dauer",
        field_type=FieldTemplate.FieldType.NUMBER,
        # E3 (Refs #1849): So sieht die Vorlage nach dem Backfill aus — der
        # Paket-Slug ``dauer`` traegt den Feld-Berichtsschluessel, ueber den
        # ``counseling-hours`` bindet; gelesen wird der Wert weiterhin unter dem
        # Slug.
        report_key="dauer_minuten",
    )
    ft_notiz = FieldTemplate.objects.create(
        facility=facility,
        name="Notiz",
        field_type=FieldTemplate.FieldType.TEXTAREA,
    )

    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_dauer, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_notiz, sort_order=1)

    return doc_type


@pytest.fixture
def doc_type_client_profile(facility):
    """Personen-Dokumentationstyp (M2 P4b, Refs #1058) mit je einem normalen und
    einem verschlüsselten Feld.

    Pendant zu ``doc_type_contact`` für ``entity_type=client``: ``Client.data_json``
    wird gegen genau diese Feldmenge whitelistet, ``wohnsituation`` bleibt Klartext
    (Residue-Proben), ``diagnose`` ist ``is_encrypted=True`` und muss nach
    ``Client.save()`` als Fernet-Token in der DB liegen.
    """
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CLIENT,
        name="Personenprofil",
    )

    ft_wohnsituation = FieldTemplate.objects.create(
        facility=facility,
        name="Wohnsituation",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    ft_diagnose = FieldTemplate.objects.create(
        facility=facility,
        name="Diagnose",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        is_encrypted=True,
    )

    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_wohnsituation, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_diagnose, sort_order=1)

    return doc_type


@pytest.fixture
def doc_type_case_form(facility):
    """Fall-Dokumentationstyp (M2 P5, Refs #1058) mit je einem normalen und
    einem verschlüsselten Feld.

    Pendant zu ``doc_type_client_profile`` für ``entity_type=case``:
    ``Case.data_json`` wird gegen genau diese Feldmenge whitelistet,
    ``anlass`` bleibt Klartext (Residue-Proben), ``verlauf`` ist
    ``is_encrypted=True`` und muss nach ``Case.save()`` als Fernet-Token in
    der DB liegen.
    """
    doc_type = DocumentType.objects.create(
        facility=facility,
        entity_type=DocumentType.EntityType.CASE,
        name="Fallformular",
    )

    ft_anlass = FieldTemplate.objects.create(
        facility=facility,
        name="Anlass",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    ft_verlauf = FieldTemplate.objects.create(
        facility=facility,
        name="Verlauf",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        is_encrypted=True,
    )

    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_anlass, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_verlauf, sort_order=1)

    return doc_type


@pytest.fixture
def doc_type_crisis(facility):
    doc_type = DocumentType.objects.create(
        facility=facility,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        name="Krisengespräch",
    )

    ft_notiz_krise = FieldTemplate.objects.create(
        facility=facility,
        name="Notiz (Krise)",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        is_encrypted=True,
        sensitivity="high",
    )

    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_notiz_krise, sort_order=0)

    return doc_type


@pytest.fixture
def sample_event(facility, client_identified, doc_type_contact, staff_user):
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 15, "notiz": "Testnotiz"},
        created_by=staff_user,
    )


@pytest.fixture
def sample_workitem(facility, client_identified, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        client=client_identified,
        created_by=staff_user,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        title="Test-Aufgabe",
    )


@pytest.fixture
def other_facility(organization):
    return Facility.objects.create(organization=organization, name="Andere Stelle")


@pytest.fixture
def second_facility(organization):
    return Facility.objects.create(organization=organization, name="Zweite Stelle")


@pytest.fixture
def second_facility_user(second_facility):
    user = User.objects.create_user(
        username="seconduser",
        role=User.Role.STAFF,
        facility=second_facility,
        is_staff=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def case_open(facility, client_identified, staff_user):
    return Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Offener Fall",
        status=Case.Status.OPEN,
        created_by=staff_user,
    )


@pytest.fixture
def case_closed(facility, client_identified, staff_user):
    return Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Geschlossener Fall",
        status=Case.Status.CLOSED,
        closed_at=timezone.now(),
        created_by=staff_user,
    )


@pytest.fixture
def episode(case_open, staff_user):
    return Episode.objects.create(
        case=case_open,
        title="Test-Episode",
        started_at=timezone.now().date(),
        created_by=staff_user,
    )


@pytest.fixture
def settings_obj(facility):
    return Settings.objects.create(
        facility=facility,
        retention_anonymous_days=90,
        retention_identified_days=365,
        retention_qualified_days=3650,
    )


@pytest.fixture
def outcome_goal(case_open, staff_user):
    return OutcomeGoal.objects.create(
        case=case_open,
        title="Stabile Wohnsituation",
        created_by=staff_user,
    )


@pytest.fixture
def milestone(outcome_goal):
    return Milestone.objects.create(
        goal=outcome_goal,
        title="Erstgespräch geführt",
    )


@pytest.fixture
def _encryption_key(settings):
    """Frischer Fernet-Key fuer Tests, die echte Verschluesselung brauchen
    (File-Vault, Attachments) — Refs #1829: identischer Zweizeiler stand in
    10 Testdateien einzeln (``@pytest.fixture`` + Erzeugung), jetzt einmal
    hier. Aufrufweg (``usefixtures("_encryption_key")`` bzw. als
    Funktionsparameter) bleibt an den Aufrufstellen unveraendert.
    """
    settings.ENCRYPTION_KEY = Fernet.generate_key().decode("utf-8")
    settings.ENCRYPTION_KEYS = ""


# ---- Rate-Limit-Fixtures (Refs #1900) -----------------------------------
# Die Testsuite laeuft mit ``RATELIMIT_ENABLE = False``; Tests, die das Limit
# wirklich ausloesen wollen, schalten es einzeln an. Der Zaehler steckt dabei
# in einem FESTEN Zeitfenster, nicht in einem gleitenden:
# ``django_ratelimit.core._get_window`` bildet den Cache-Schluessel aus
# ``ts - (ts % periode) + (crc32(key) % periode)``. Faellt diese Grenze
# mitten in die Zaehlschleife, beginnt ein frischer Eimer und der Request,
# der abgewiesen werden sollte, kommt mit 200 zurueck.
#
# Genau so fiel ``test_file_vault.test_download_rate_limited_after_120_requests``
# in der CI aus (``assert 200 == 429``), waehrend er lokal gruen blieb: die
# Trefferchance ist Schleifendauer / Periode, also lastabhaengig und bei
# ``30/m``-Limits sechzigmal hoeher als bei ``…/h``. Mehr Timeout oder ein
# Nachlauf haetten daran nichts geaendert.
#
# ``ratelimit_enabled`` nagelt deshalb die Zeitquelle von django-ratelimit
# fest — nur dessen Modul-Uhr, nicht ``time`` global und nicht
# ``timezone.now()``. Damit liegt die gesamte Schleife garantiert in EINEM
# Fenster und der Test beweist wieder das, was er behauptet.

#: Beliebiger, aber fester Zeitpunkt — nur die Konstanz zaehlt.
RATELIMIT_FIXED_TS = 1_800_000_000


class RatelimitClock:
    """Steuerbare Zeitquelle fuer ``django_ratelimit.core``.

    Traegt bewusst dieselbe Signatur wie das ``time``-Modul (``time()``), weil
    sie genau dessen Namen im Modul-Namensraum ersetzt.
    """

    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        """Uhr weiterstellen — ``advance(3600)`` wechselt bei jedem
        Stunden-Limit garantiert in ein neues Zaehlfenster, unabhaengig vom
        schluesselabhaengigen Versatz."""
        self.now += seconds


@pytest.fixture
def ratelimit_enabled(settings, monkeypatch):
    """Rate-Limit fuer einen Test scharf schalten, Zaehlfenster festnageln.

    Leert den Cache vorher und nachher (der LocMem-Cache ueberlebt sonst den
    Test und schleppt Zaehler weiter) und liefert die ``RatelimitClock``
    zurueck, damit ein Test den Fensterwechsel bei Bedarf gezielt herbeifuehren
    kann.

    Die Ablaufzeit des Cache-Eintrags (``Periode + 5 s``) laeuft weiter auf der
    echten Uhr des Cache-Backends. Das ist unkritisch, solange eine
    Zaehlschleife deutlich unter einer Minute bleibt.
    """
    from django.core.cache import cache

    settings.RATELIMIT_ENABLE = True
    cache.clear()
    clock = RatelimitClock(RATELIMIT_FIXED_TS)
    monkeypatch.setattr("django_ratelimit.core.time", clock)
    try:
        yield clock
    finally:
        cache.clear()


# ---- AuthZ-Matrix-Fixtures (Refs #1055) ---------------------------------
# Eigene Facility-1-Objekte für Matrix-Zellen + Spiegel-Objekte in
# second_facility für IDOR-Probes (erwartet 404, kein Existenz-Leak).

PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _make_attachment(fac, owner, user, *, ft_name="Anhang (AuthZ)"):
    """Anhang an einen beliebigen Owner (Ereignis/Person/Fall) — M24/AP4, Refs #1738.

    ``ft_name`` ist nur deshalb parametrisierbar, weil ``FieldTemplate.name``
    je Einrichtung eindeutig ist: mehrere Owner-Fixtures derselben Facility
    brauchen jeweils eine eigene Vorlage.
    """
    from django.core.files.uploadedfile import SimpleUploadedFile

    from core.services.file_vault import store_encrypted_file

    ft = FieldTemplate.objects.create(facility=fac, name=ft_name, field_type=FieldTemplate.FieldType.FILE)
    upload = SimpleUploadedFile("authz.pdf", PDF_MINIMAL, content_type="application/pdf")
    return store_encrypted_file(fac, upload, ft, owner, user)


@pytest.fixture
def case_trashed(facility, client_identified, staff_user):
    """Fall im Papierkorb (Refs #1818) — Gegenstueck zu ``client_trashed``.

    Traegt ``anonymized_at=None``: die Zeile ist wiederherstellbar und damit
    das Ziel von ``case_restore``/``case_anonymize``. Ein bereits getilgter
    Fall waere fuer beide Routen der Fehlerfall, nicht der Normalfall.
    """
    return Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Fall im Papierkorb",
        status=Case.Status.OPEN,
        created_by=staff_user,
        is_deleted=True,
        deleted_at=timezone.now(),
    )


@pytest.fixture
def client_trashed(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Trash-01",
        created_by=staff_user,
        is_deleted=True,
        deleted_at=timezone.now(),
    )


@pytest.fixture
def case_event(case_open, sample_event):
    """Hängt sample_event an case_open (mutiert sample_event in place)."""
    sample_event.case = case_open
    sample_event.save(update_fields=["case"])
    return sample_event


@pytest.fixture
def authz_attachment(facility, sample_event, staff_user):
    return _make_attachment(facility, sample_event, staff_user)


@pytest.fixture
def authz_client_attachment(facility, client_identified, doc_type_client_profile, staff_user):
    """Personen-eigener Anhang fuer AuthZ-Matrix/IDOR (M24/AP4, Refs #1738).

    Die Person bekommt dafuer einen Personen-Dokumentationstyp: ohne
    ``document_type`` antwortet das Download-Gate fail-closed mit 404
    (ADR-039 §12, Stufe 2). ``doc_type_client_profile`` ist NORMAL, damit auch
    die Assistenz (ASSISTANT_PLUS) den Download sehen darf.
    """
    client_identified.document_type = doc_type_client_profile
    client_identified.save(update_fields=["document_type", "data_json"])
    return _make_attachment(facility, client_identified, staff_user, ft_name="Anhang (AuthZ Person)")


@pytest.fixture
def authz_case_attachment(facility, case_open, doc_type_case_form, staff_user):
    """Fall-eigener Anhang fuer AuthZ-Matrix/IDOR (M24/AP4, Refs #1738).

    Rollen-Gefaelle (ADR-039 §12): der Fall-Download ist STAFF_PLUS — Faelle
    sind fuer die Assistenz unsichtbar.
    """
    case_open.document_type = doc_type_case_form
    case_open.save(update_fields=["document_type", "data_json"])
    return _make_attachment(facility, case_open, staff_user, ft_name="Anhang (AuthZ Fall)")


@pytest.fixture
def audit_entry(facility, staff_user, client_identified):
    return AuditLog.objects.create(
        facility=facility,
        user=staff_user,
        action=AuditLog.Action.EXPORT,
        target_type="Client",
        target_id=str(client_identified.pk),
        detail={},
    )


@pytest.fixture
def retention_proposal(facility, sample_event):
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=sample_event.pk,
        deletion_due_at=timezone.localdate(),
        retention_category="anonymous",
    )


@pytest.fixture
def legal_hold(facility, sample_event, lead_user):
    return LegalHold.objects.create(
        facility=facility,
        target_type="Event",
        target_id=sample_event.pk,
        reason="AuthZ-Matrix-Test",
        created_by=lead_user,
    )


@pytest.fixture
def deletion_request(facility, sample_event, staff_user):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type="Event",
        target_id=sample_event.pk,
        reason="AuthZ-Matrix-Test",
        requested_by=staff_user,
    )


# ---- Fremde Facility (IDOR-Ziele) ----------------------------------------


@pytest.fixture
def foreign_staff(second_facility):
    user = User.objects.create_user(
        username="authz_foreign_staff",
        role=User.Role.STAFF,
        facility=second_facility,
        is_staff=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def foreign_client(second_facility, foreign_staff):
    return Client.objects.create(
        facility=second_facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Fremd-01",
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_client_trashed(second_facility, foreign_staff):
    return Client.objects.create(
        facility=second_facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Fremd-Trash-01",
        created_by=foreign_staff,
        is_deleted=True,
        deleted_at=timezone.now(),
    )


@pytest.fixture
def foreign_case(second_facility, foreign_client, foreign_staff):
    return Case.objects.create(
        facility=second_facility,
        client=foreign_client,
        title="Fremder Fall",
        status=Case.Status.OPEN,
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_case_closed(second_facility, foreign_client, foreign_staff):
    return Case.objects.create(
        facility=second_facility,
        client=foreign_client,
        title="Fremder geschlossener Fall",
        status=Case.Status.CLOSED,
        closed_at=timezone.now(),
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_case_trashed(second_facility, foreign_client, foreign_staff):
    return Case.objects.create(
        facility=second_facility,
        client=foreign_client,
        title="Fremder Fall im Papierkorb",
        status=Case.Status.OPEN,
        created_by=foreign_staff,
        is_deleted=True,
        deleted_at=timezone.now(),
    )


@pytest.fixture
def foreign_doc_type(second_facility):
    return DocumentType.objects.create(
        facility=second_facility,
        name="Kontakt (fremd)",
    )


@pytest.fixture
def foreign_doc_type_client(second_facility):
    """Personen-Dokumentationstyp auf ``second_facility`` (M2 P4b, Refs #1058).

    Spiegel zu ``doc_type_client_profile`` für die Cross-Facility-Probe: der Typ
    ist inhaltlich gültig (``entity_type=client``), gehört aber zur falschen
    Einrichtung — der Service-Guard muss ihn ablehnen.
    """
    return DocumentType.objects.create(
        facility=second_facility,
        entity_type=DocumentType.EntityType.CLIENT,
        name="Personenprofil (fremd)",
    )


@pytest.fixture
def foreign_doc_type_case(second_facility):
    """Fall-Dokumentationstyp auf ``second_facility`` (M2 P5, Refs #1058).

    Spiegel zu ``doc_type_case_form`` für die Cross-Facility-Probe: der Typ
    ist inhaltlich gültig (``entity_type=case``), gehört aber zur falschen
    Einrichtung — der Service-Guard muss ihn ablehnen.
    """
    return DocumentType.objects.create(
        facility=second_facility,
        entity_type=DocumentType.EntityType.CASE,
        name="Fallformular (fremd)",
    )


@pytest.fixture
def foreign_event(second_facility, foreign_client, foreign_doc_type, foreign_staff):
    return Event.objects.create(
        facility=second_facility,
        client=foreign_client,
        document_type=foreign_doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_case_event(foreign_case, foreign_event):
    """Hängt foreign_event an foreign_case (mutiert foreign_event in place)."""
    foreign_event.case = foreign_case
    foreign_event.save(update_fields=["case"])
    return foreign_event


@pytest.fixture
def foreign_episode(foreign_case, foreign_staff):
    return Episode.objects.create(
        case=foreign_case,
        title="Fremde Episode",
        started_at=timezone.now().date(),
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_goal(foreign_case, foreign_staff):
    return OutcomeGoal.objects.create(
        case=foreign_case,
        title="Fremdes Ziel",
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_milestone(foreign_goal):
    return Milestone.objects.create(goal=foreign_goal, title="Fremder Meilenstein")


@pytest.fixture
def foreign_workitem(second_facility, foreign_client, foreign_staff):
    return WorkItem.objects.create(
        facility=second_facility,
        client=foreign_client,
        created_by=foreign_staff,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        title="Fremde Aufgabe",
    )


@pytest.fixture
def foreign_attachment(second_facility, foreign_event, foreign_staff):
    return _make_attachment(second_facility, foreign_event, foreign_staff)


@pytest.fixture
def foreign_client_attachment(second_facility, foreign_client, foreign_doc_type_client, foreign_staff):
    """IDOR-Spiegel zu ``authz_client_attachment`` in der zweiten Einrichtung."""
    foreign_client.document_type = foreign_doc_type_client
    foreign_client.save(update_fields=["document_type", "data_json"])
    return _make_attachment(second_facility, foreign_client, foreign_staff, ft_name="Anhang (AuthZ Person fremd)")


@pytest.fixture
def foreign_case_attachment(second_facility, foreign_case, foreign_doc_type_case, foreign_staff):
    """IDOR-Spiegel zu ``authz_case_attachment`` in der zweiten Einrichtung."""
    foreign_case.document_type = foreign_doc_type_case
    foreign_case.save(update_fields=["document_type", "data_json"])
    return _make_attachment(second_facility, foreign_case, foreign_staff, ft_name="Anhang (AuthZ Fall fremd)")


@pytest.fixture
def foreign_audit_entry(second_facility, foreign_staff, foreign_client):
    return AuditLog.objects.create(
        facility=second_facility,
        user=foreign_staff,
        action=AuditLog.Action.EXPORT,
        target_type="Client",
        target_id=str(foreign_client.pk),
        detail={},
    )


@pytest.fixture
def foreign_retention_proposal(second_facility, foreign_event):
    return RetentionProposal.objects.create(
        facility=second_facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=foreign_event.pk,
        deletion_due_at=timezone.localdate(),
        retention_category="anonymous",
    )


@pytest.fixture
def foreign_legal_hold(second_facility, foreign_event, foreign_staff):
    return LegalHold.objects.create(
        facility=second_facility,
        target_type="Event",
        target_id=foreign_event.pk,
        reason="AuthZ-IDOR-Test",
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_deletion_request(second_facility, foreign_event, foreign_staff):
    return DeletionRequest.objects.create(
        facility=second_facility,
        target_type="Event",
        target_id=foreign_event.pk,
        reason="AuthZ-IDOR-Test",
        requested_by=foreign_staff,
    )


# ---- Config-CRUD-Fixtures (M0, Refs #1056) -------------------------------
# FieldTemplate/FieldGroup/DocumentTypeField der Haupt-facility für die
# Config-CRUD-Matrix (P5) sowie die interaktive Feld-Zuordnung (P6/P7); die
# foreign_*-Spiegel auf second_facility/foreign_doc_type dienen den IDOR-Proben.


@pytest.fixture
def field_template(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Feldvorlage (Config)",
        field_type=FieldTemplate.FieldType.TEXT,
    )


@pytest.fixture
def doc_type_field(doc_type_contact, facility):
    """Eine dedizierte Feld-Zuordnung auf doc_type_contact (zusätzlich zu den
    beiden Fixture-Feldern), mit stabilem, eigenem FieldTemplate."""
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="Feld (Config-Zuordnung)",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    return DocumentTypeField.objects.create(
        document_type=doc_type_contact,
        field_template=ft,
        sort_order=2,
    )


@pytest.fixture
def field_group(doc_type_contact):
    return FieldGroup.objects.create(
        document_type=doc_type_contact,
        name="Feldgruppe (Config)",
    )


@pytest.fixture
def foreign_field_template(second_facility):
    return FieldTemplate.objects.create(
        facility=second_facility,
        name="Feldvorlage (fremd)",
        field_type=FieldTemplate.FieldType.TEXT,
    )


@pytest.fixture
def foreign_field_group(foreign_doc_type):
    return FieldGroup.objects.create(
        document_type=foreign_doc_type,
        name="Feldgruppe (fremd)",
    )


@pytest.fixture
def foreign_doc_type_field(foreign_doc_type, foreign_field_template):
    return DocumentTypeField.objects.create(
        document_type=foreign_doc_type,
        field_template=foreign_field_template,
        sort_order=0,
    )


# ---- FieldCondition-Fixtures (M1 P6, Refs #1057) --------------------------
# Referenz-Bedingungen für die AuthZ-/IDOR-Matrix (config_condition_update/
# _delete). Die Haupt-facility-Variante hängt sich an die beiden Bestandsfelder
# von ``doc_type_contact`` (dauer/number, notiz/textarea); die foreign-Variante
# legt eigenständig zwei Felder+DTFs an ``foreign_doc_type`` an, weil
# ``foreign_doc_type`` selbst keine Felder trägt und ``foreign_doc_type_field``
# nur EIN DTF liefert (eine Bedingung braucht zwei).


@pytest.fixture
def field_condition(doc_type_contact):
    dauer = doc_type_contact.fields.get(field_template__slug="dauer")
    notiz = doc_type_contact.fields.get(field_template__slug="notiz")
    return FieldCondition.objects.create(
        target_field=notiz,
        depends_on_field=dauer,
        operator=FieldCondition.Operator.GREATER_THAN,
        value=10,
        group_index=0,
    )


@pytest.fixture
def foreign_field_condition(foreign_doc_type, second_facility):
    ft_source = FieldTemplate.objects.create(
        facility=second_facility,
        name="Fremd-Quelle",
        field_type=FieldTemplate.FieldType.NUMBER,
    )
    ft_target = FieldTemplate.objects.create(
        facility=second_facility,
        name="Fremd-Ziel",
        field_type=FieldTemplate.FieldType.TEXTAREA,
    )
    source = DocumentTypeField.objects.create(
        document_type=foreign_doc_type,
        field_template=ft_source,
        sort_order=0,
    )
    target = DocumentTypeField.objects.create(
        document_type=foreign_doc_type,
        field_template=ft_target,
        sort_order=1,
    )
    return FieldCondition.objects.create(
        target_field=target,
        depends_on_field=source,
        operator=FieldCondition.Operator.GREATER_THAN,
        value=5,
        group_index=0,
    )


# --- M4 Import (Refs #1060) -------------------------------------------------
# Beide Laeufe stehen auf UPLOADED und tragen bewusst KEINE Quelldatei: fuer die
# URL-/IDOR-Proben der AuthZ-Matrix reicht die Zeile. Die Wizard-Views muessen
# den fehlenden Upload als Nutzerfehler behandeln (Redirect + Meldung), nie als
# 500er — genau das pinnt die Matrix damit mit ab.


@pytest.fixture
def import_job(facility, admin_user):
    return ImportJob.objects.create(
        facility=facility,
        created_by=admin_user,
        target_type=ImportJob.TargetType.CLIENT,
    )


@pytest.fixture
def foreign_import_job(second_facility, foreign_staff):
    return ImportJob.objects.create(
        facility=second_facility,
        created_by=foreign_staff,
        target_type=ImportJob.TargetType.CLIENT,
    )


# ---- Berichtigungsantraege (M4, Refs #1060) -------------------------------
# Referenz-Antrag der Haupt-facility fuer AuthZ-/IDOR-Matrix und View-Tests;
# der foreign_*-Spiegel liegt in der zweiten Einrichtung. Bewusst PENDING —
# nur offene Antraege sind ueber ``rectification_review`` erreichbar.


@pytest.fixture
def rectification_request(facility, client_identified, staff_user):
    from core.services.client.rectification import request_rectification

    return request_rectification(
        client_identified,
        staff_user,
        "notes",
        "AuthZ-Matrix-Wunschwert",
        "AuthZ-Matrix-Test",
    )


@pytest.fixture
def foreign_rectification_request(second_facility, foreign_client, foreign_staff):
    from core.services.client.rectification import request_rectification

    return request_rectification(
        foreign_client,
        foreign_staff,
        "notes",
        "AuthZ-IDOR-Wunschwert",
        "AuthZ-IDOR-Test",
    )


def isolate_reporting_registry(monkeypatch, *, empty: bool = False) -> None:
    """Kernlogik der Reporting-Registry-Isolation je Test (Refs #1829).

    14 Reporting-Testdateien definierten dieselbe ``_isolated_registry``-
    ``@pytest.fixture(autouse=True)`` byte-identisch (bis auf Docstring-Text):
    erst die echte Discovery ausloesen (``@register_backend`` ist ein
    Import-Seiteneffekt und laeuft je Modul nur beim *ersten* Import), dann
    mit einer **Kopie** von ``_REGISTRY`` isolieren, damit ad-hoc registrierte
    Test-Backends nicht in Folgetests leaken, echte Backends aber sichtbar
    bleiben. ``empty=True`` (nur ``test_reporting_registry.py::TestDiscovery``
    -Muster) isoliert stattdessen mit einem leeren Dict.

    Bewusst eine **Funktion**, keine Conftest-Fixture: die 14 Aufrufer
    binden sie ``autouse=True`` — als Conftest-Autouse-Fixture liefe sie fuer
    die GESAMTE Testsuite statt nur fuer Reporting-Tests (Performance- und
    Isolations-Ueberraschung fuer reporting-fremde Tests). Jede Datei behaelt
    deshalb ihre eigene, jetzt einzeilige ``autouse``-Fixture, die diese
    Funktion aufruft.
    """
    from core.reporting import registry

    registry._discover()
    monkeypatch.setattr(registry, "_REGISTRY", {} if empty else dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
