"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — Vollmutation-Rerun,
Batch ``neu-1``: 47 NEUE Survivor aus dem Kill-Nachweis-Rerun auf dem
Endbaum (main ``a608ba0e``) in zehn Funktionen ueber sieben Module, die seit
dem Pruefstand ``25685845`` durch parallele Aenderungen geaendert und deshalb neu
mutiert wurden (Mutantennummern haben sich gegenueber den alten Batches
verschoben — jede Mutante wurde am Diff gegen ``__mutmut_orig`` IM AKTUELLEN
Baum gelesen).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-neu-1.json`` (47 Survivor),
Triage ``welle-l-0.99/reports/m-scratch/pins-neu-1/triage-099-neu-1.json``.

Module/Funktionen dieser Datei:

* ``core.services.attachments.crud``: ``_batch_entry_ids`` (7 Mutanten),
  ``lock_batch_attachment_rows`` (4 Mutanten, ALLE ``equivalent``).
* ``core.services.client.export``: ``_gather_event_history`` (1),
  ``_gather_events`` (1) — je ein verlorenes Sortierkriterium.
* ``core.services.conditions``: ``build_conditions_payload`` (1,
  ``continue``->``break`` in der Drop-Regel-Schleife).
* ``core.services.file_vault.cleanup``: ``cleanup_orphan_storage_files``
  (1, ``continue``->``break`` im Waisen-Scan).
* ``core.services.file_vault.quota``: ``facility_storage_overrun`` (6),
  ``lock_facility_storage`` (5, davon 1 ``equivalent``).
* ``core.services.file_vault.storage``: ``_commit_staged_upload`` (2, davon
  1 ``equivalent``), ``_lock_superseded_row`` (4, ALLE ``equivalent``).
* ``core.services.user_access``: ``resend_invitation_for_any_user`` (15,
  davon 3 ``equivalent`` und 1 ``skip``).

**Wiederkehrendes Aequivalenz-Muster (``lock_batch_attachment_rows``,
``_lock_superseded_row``):** beide Funktionen materialisieren eine
``select_for_update().values_list(...)``-Abfrage vor allem fuer ihren
Sperr-Seiteneffekt. ``lock_batch_attachment_rows`` verwirft das Ergebnis
vollstaendig (``-> None``, kein ``return``-Statement);
``_lock_superseded_row`` gibt es seit der Zugehoerigkeits-Pruefung des
Vorgaengers (Refs #2307) zurueck, sein einziger Aufrufer wertet daran aber
ausschliesslich ``is None`` aus. Mutationen an ``flat=`` (``None``/
weggelassen/``False``) aendern lediglich die Python-Form des Ergebnisses
(Tupel statt Skalar) — beide Formen sind gleichermassen ``None`` bzw. nicht
``None`` — ``equivalent`` (maskierter interner Wert). Mutationen, die das
POSITIONS-Argument ``"pk"`` entfernen (``values_list(flat=True)`` OHNE
Feldname), crashen entgegen der urspruenglichen Vermutung NICHT mit
``TypeError``: Django faellt bei
``flat=True`` ohne Feldliste laut Quelltext (``django/db/models/query.py``,
``QuerySet.values_list``: ``elif not fields: fields =
[self.model._meta.concrete_fields[0].attname]``) automatisch auf das ERSTE
konkrete Modellfeld zurueck — bei ``Attachment`` ist das exakt ``id``, die
PK (identisch mit ``"pk"``). Beide Fassungen waehlen also GENAU dieselbe
Spalte; zusammen mit der allein auf ``is None`` geprueften Rueckgabe ist
auch das ``equivalent`` (empirisch verifiziert: ein direkt dagegen
geschriebener Pin blieb im Kill-Nachweis GRUEN unter der Mutante, siehe
``killmap.tsv`` — kein Pin in dieser Datei).

**Aequivalenz-Muster (``resend_invitation_for_any_user``, drei
``getattr(actor, <flag>, False)``-Defaults auf ``None`` statt ``False``):**
der Rueckgabewert wird ausschliesslich ueber ``not(...)`` konsumiert;
``not None == not False == True`` fuer JEDEN Eingabewert -- kein Aufrufer
kann die beiden Defaults unterscheiden.

**Aequivalenz (``_commit_staged_upload``, ``is_current=True``-Kwarg
weggelassen):** ``Attachment.is_current`` ist als
``models.BooleanField(default=True, ...)`` deklariert -- ein weggelassenes
Kwarg erzeugt exakt denselben Wert wie das explizite ``True``.

**Aequivalenz (``lock_facility_storage``, SQL-Keyword ``select`` statt
``SELECT``):** PostgreSQL ist bei SQL-Schluesselwoertern case-insensitiv.

**``skip`` (``x_resend_invitation_for_any_user__mutmut_42``):** XX-Marker um
den GESAMTEN, per ``_()`` uebersetzbaren ``ValidationError``-Text des
E-Mail-/``must_change_password``-Guards -- reiner i18n-UI-String, kein Pin
in dieser Datei ruft diesen Zweig auf (am Ende gegen die fertige Pin-Datei
unter ``MUTANT_UNDER_TEST`` verifiziert, ``killmap.tsv``).

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum
(``/work/anlaufstelle/mutants``) hat keine ``.env`` und damit keinen
``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import os
import time
import types
from unittest import mock
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.db.models.query import QuerySet
from django.utils import timezone

from core.models import Client, DocumentType, Event, FieldCondition, FieldTemplate, User
from core.services.attachments.crud import _batch_entry_ids
from core.services.client.export import _gather_event_history, _gather_events
from core.services.conditions import build_conditions_payload
from core.services.file_vault import cleanup as cleanup_module
from core.services.file_vault import cleanup_orphan_storage_files, prepare_encrypted_upload, store_encrypted_file
from core.services.file_vault import quota as quota_module
from core.services.file_vault import storage as storage_module
from core.services.file_vault.quota import facility_storage_overrun, lock_facility_storage
from core.services.file_vault.storage import _commit_staged_upload
from core.services.user_access import resend_invitation_for_any_user

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

# Echte PDF-Magic-Bytes (Refs #610): store_encrypted_file lehnt Uploads ab,
# deren Inhalt nicht zum deklarierten Typ passt.
PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _make_event(facility):
    """Minimale Event-Owner-Instanz (Pflichtfeld ``document_type``)."""
    dt = DocumentType.objects.create(facility=facility, name="Doku")
    return Event.objects.create(facility=facility, document_type=dt, occurred_at=timezone.now())


def _pending_target(facility, username="pending-einladung"):
    """Noch nicht eingerichteter Zugang MIT nutzbarem Passwort (analog
    ``_legacy_pending_user`` in ``test_mutation_pins_099_services_user_access.py``):
    ``resend_invitation_for_any_user``s Entwertungs-Zweig braucht ein
    nutzbares Passwort, um ausgeloest zu werden."""
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.org",
        role=User.Role.STAFF,
        facility=facility,
        must_change_password=True,
    )
    user.set_password("legacy-initial-pw")
    user.save()
    return user


def _capture_order_by(call) -> list[tuple[str, tuple]]:
    """Erfasst jeden ``QuerySet.order_by``-Aufruf waehrend ``call()`` laeuft,
    als ``(Modellname, args)`` in Aufrufreihenfolge.

    Planunabhaengig: prueft die tatsaechlich am Python-Aufruf uebergebenen
    Sortierkriterien, NICHT die von Postgres zurueckgelieferte
    Zeilenreihenfolge (Muster aus ``test_mutation_pins_099_facility_export_b.py``).
    """
    calls: list[tuple[str, tuple]] = []
    original = QuerySet.order_by

    def _spy(self, *args, **kwargs):
        calls.append((self.model.__name__, args))
        return original(self, *args, **kwargs)

    with mock.patch.object(QuerySet, "order_by", _spy):
        call()
    return calls


def _last_order_by(calls: list[tuple[str, tuple]], model_name: str) -> tuple | None:
    """Letztes ``order_by``-Argument-Tupel fuer ``model_name`` aus ``calls``."""
    matches = [args for name, args in calls if name == model_name]
    return matches[-1] if matches else None


# ---------------------------------------------------------------------------
# core.services.attachments.crud — _batch_entry_ids
# ---------------------------------------------------------------------------


class TestBatchEntryIds:
    """``_batch_entry_ids`` bestimmt die Sperrmenge eines Anhangs-Batches aus
    der hidden-CSV ``<slug>__remove`` und den Staging-Schluesseln."""

    def test_remove_key_is_read_via_the_field_specific_slug(self):
        """Toetet ``mutmut_3`` (``request_post.get(None, "")`` statt
        ``request_post.get(f"{slug}__remove", "")`` — der echte
        Request-Schluessel wird NIE mehr gelesen)."""
        att = types.SimpleNamespace(entry_id="abc")
        state = ("dateien", None, [{"id": "1"}], {"1": att})

        ids = _batch_entry_ids([state], {"dateien__remove": "abc"}, set())

        assert ids == {"abc"}

    def test_missing_remove_key_defaults_to_an_empty_csv_not_a_placeholder(self):
        """Toetet ``mutmut_7`` (Default ``""`` -> ``"XXXX"``): der
        Fallback-Vertrag bei komplett fehlendem ``<slug>__remove``-Feld ist
        eine LEERE CSV, kein Platzhalter-Token — ``"XXXX"`` ist hier kein
        i18n-/Log-String, sondern ein funktionaler Default, deshalb keine
        Skip-Ausnahme."""
        att = types.SimpleNamespace(entry_id="XXXX")
        state = ("dateien", None, [{"id": "1"}], {"1": att})

        ids = _batch_entry_ids([state], {}, set())

        assert ids == set()

    def test_remove_csv_is_split_on_every_comma(self):
        """Toetet ``mutmut_9`` (``split(",")`` -> ``split(None)``,
        Whitespace-Split) und ``mutmut_10`` (``split(",")`` ->
        ``split("XX,XX")``, falsches Trennzeichen — funktionales
        Trennzeichen-Literal, keine i18n-/Log-Zeichenkette, keine
        Skip-Ausnahme): eine kommaseparierte Liste OHNE Leerzeichen muss an
        JEDEM Komma zerlegt werden."""
        att_a = types.SimpleNamespace(entry_id="a")
        att_b = types.SimpleNamespace(entry_id="b")
        state = ("dateien", None, [{"id": "1"}, {"id": "2"}], {"1": att_a, "2": att_b})

        ids = _batch_entry_ids([state], {"dateien__remove": "a,b"}, set())

        assert ids == {"a", "b"}

    def test_a_missing_attachment_row_does_not_abort_the_remaining_scan(self):
        """Toetet ``mutmut_17`` (``continue`` -> ``break``): eine bereits
        geloeschte/veraltete Zeile (kein Treffer in ``attachments_by_pk``)
        VOR einer gueltigen REMOVE-Zeile darf die restliche Liste nicht
        ungeprueft lassen."""
        att = types.SimpleNamespace(entry_id="valide")
        state = ("dateien", None, [{"id": "geloescht"}, {"id": "1"}], {"1": att})

        ids = _batch_entry_ids([state], {"dateien__remove": "valide"}, set())

        assert ids == {"valide"}

    def test_removal_match_compares_the_entrys_own_id_not_a_constant(self):
        """Toetet ``mutmut_19`` (``str(att.entry_id) in remove_ids`` ->
        ``str(None) in remove_ids`` — vergleicht IMMER die Konstante
        ``"None"`` statt der echten ``entry_id``)."""
        att = types.SimpleNamespace(entry_id="7")
        state = ("dateien", None, [{"id": "1"}], {"1": att})

        ids = _batch_entry_ids([state], {"dateien__remove": "7"}, set())

        assert ids == {"7"}

    def test_an_entry_absent_from_the_remove_csv_and_not_staged_is_left_alone(self):
        """Toetet ``mutmut_20`` (``str(att.entry_id) in remove_ids`` ->
        ``not in``): negiert die erste ODER-Bedingung — praktisch jede
        NICHT als REMOVE markierte Zeile wuerde faelschlich in die
        Sperr-/Schreibmenge aufgenommen."""
        att = types.SimpleNamespace(entry_id="unberuehrt")
        state = ("dateien", None, [{"id": "1"}], {"1": att})

        ids = _batch_entry_ids([state], {"dateien__remove": "andere-id"}, set())

        assert ids == set()


# ---------------------------------------------------------------------------
# core.services.attachments.crud — lock_batch_attachment_rows
#
# Alle vier Batch-Mutanten (mutmut_8/_9/_10/_17) sind ``equivalent`` (siehe
# Moduldocstring) — kein Pin in dieser Datei.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# core.services.client.export — _gather_event_history / _gather_events
# ---------------------------------------------------------------------------


class TestGatherEventHistoryOrdering:
    def test_orders_by_changed_at_desc_then_pk(self):
        """Toetet ``mutmut_4`` (``.order_by("-changed_at", "pk")`` ->
        ``.order_by("pk")``): die chronologische Reihenfolge (neueste
        Aenderung zuerst) der Auskunfts-Historie (Art. 15 DSGVO Export)
        ist Teil des dokumentierten Vertrags. Planunabhaengige
        Introspektion (eine beobachtete Zeilenreihenfolge ist kein
        Aequivalenzbeweis)."""
        calls = _capture_order_by(lambda: _gather_event_history([1, 2]))

        assert _last_order_by(calls, "EventHistory") == ("-changed_at", "pk")


class TestGatherEventsOrdering:
    def test_orders_by_occurred_at_desc_then_pk(self, facility, staff_user):
        """Toetet ``mutmut_6`` (``.order_by("-occurred_at", "pk")`` ->
        ``.order_by("-occurred_at", )``, verliert den ``pk``-Tiebreak):
        bei identischem ``occurred_at`` ist die Export-Reihenfolge sonst
        nicht mehr deterministisch."""
        client_obj = Client.objects.create(facility=facility, pseudonym="Pin-Ordering")

        calls = _capture_order_by(lambda: _gather_events(client_obj, staff_user))

        assert _last_order_by(calls, "Event") == ("-occurred_at", "pk")


# ---------------------------------------------------------------------------
# core.services.conditions — build_conditions_payload
# ---------------------------------------------------------------------------


class TestBuildConditionsPayloadDropRule:
    def test_a_dropped_condition_does_not_abort_the_remaining_scan(self, facility, doc_type_contact):
        """Toetet ``mutmut_31`` (``continue`` -> ``break`` in der
        Drop-Regel-Schleife ueber ``conditions``, #774): eine zu
        verwerfende Bedingung VOR einer gueltigen darf die Verarbeitung
        nicht abbrechen. Reihenfolge LEGAL ERZWUNGEN ueber den
        ``conditions``-Parameter (explizite Python-Liste statt
        ungeordnetem Queryset, keine Dateisystem-/DB-Ordnungsannahme)."""
        dauer_dtf = doc_type_contact.fields.get(field_template__slug="dauer")
        notiz_dtf = doc_type_contact.fields.get(field_template__slug="notiz")
        fremd_ft = FieldTemplate.objects.create(
            facility=facility, name="Fremdesfeld", field_type=FieldTemplate.FieldType.TEXT
        )
        fremd_dtf = doc_type_contact.fields.create(field_template=fremd_ft, sort_order=2)

        dropped = FieldCondition.objects.create(
            target_field=fremd_dtf,
            depends_on_field=dauer_dtf,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=5,
            group_index=0,
        )
        kept = FieldCondition.objects.create(
            target_field=notiz_dtf,
            depends_on_field=dauer_dtf,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=5,
            group_index=0,
        )

        payload = build_conditions_payload(doc_type_contact, {"dauer", "notiz"}, conditions=[dropped, kept])

        assert payload is not None
        assert payload["conditions"] == [
            {"target": "notiz", "source": "dauer", "operator": "greater_than", "value": 5, "group": 0}
        ]


# ---------------------------------------------------------------------------
# core.services.file_vault.cleanup — cleanup_orphan_storage_files
# ---------------------------------------------------------------------------


class TestCleanupOrphanStorageFilesContinuesPastKnownEntries:
    def test_continues_scanning_after_a_known_entry(self, monkeypatch, settings, tmp_path, facility, staff_user):
        """Toetet ``mutmut_35`` (``continue`` -> ``break`` beim Scan ueber
        ``.enc``-Dateien): ein bekannter (referenzierter) Dateiname VOR
        einer echten Waise in der Scan-Reihenfolge darf den periodischen
        Cron-Sweep nicht komplett abbrechen — sonst bleiben verwaiste
        personenbezogene Dateien liegen (Datenschutz/Speicherminimierung,
        #662). Reihenfolge LEGAL ERZWUNGEN per Traversierungs-Spion
        (eine beobachtete Dateisystem-Reihenfolge ist kein
        Aequivalenzbeweis)."""
        settings.MEDIA_ROOT = str(tmp_path)
        event = _make_event(facility)
        ft = FieldTemplate.objects.create(facility=facility, name="Scan", field_type=FieldTemplate.FieldType.FILE)
        uploaded = SimpleUploadedFile("real.pdf", PDF_HEADER, content_type="application/pdf")
        known_attachment = store_encrypted_file(facility, uploaded, ft, event, staff_user, audit=False)
        known_matches = list(tmp_path.rglob(known_attachment.storage_filename))
        assert len(known_matches) == 1, "die frisch gespeicherte Attachment-Datei muss unter MEDIA_ROOT liegen"
        known_path = known_matches[0]
        old_mtime = time.time() - 7200
        os.utime(known_path, (old_mtime, old_mtime))
        orphan_path = tmp_path / "waise-nach-bekannter-datei.enc"
        orphan_path.write_bytes(b"ciphertext")
        os.utime(orphan_path, (old_mtime, old_mtime))

        monkeypatch.setattr(
            cleanup_module,
            "_iter_encrypted_files",
            lambda root: iter(
                [
                    (known_path.relative_to(root).as_posix(), known_path),
                    (orphan_path.relative_to(root).as_posix(), orphan_path),
                ]
            ),
        )

        deleted = cleanup_orphan_storage_files()

        assert deleted == 1
        assert known_path.exists()
        assert not orphan_path.exists()


# ---------------------------------------------------------------------------
# core.services.file_vault.quota — facility_storage_overrun
# ---------------------------------------------------------------------------


class TestFacilityStorageOverrunLockArgument:
    """``facility_storage_overrun`` sperrt VOR dem Summen-Lesen ueber
    ``lock_facility_storage(getattr(facility, "pk", facility))`` — der
    Sperr-Aufruf muss exakt mit ``facility.pk`` erfolgen (Mandanten-Scope
    der Sperre, #2178)."""

    def test_locks_with_the_facilitys_own_pk(self, monkeypatch, facility):
        """Toetet ``mutmut_4`` (``lock_facility_storage(None)``),
        ``mutmut_5`` (``getattr(None, "pk", facility)`` liefert das
        FACILITY-OBJEKT statt dessen ``pk``), ``mutmut_11`` (Attributname
        ``"pk"`` -> funktionaler String ``"XXpkXX"``, kein i18n-String) und
        ``mutmut_12`` (Attributname ``"pk"`` -> ``"PK"``, case-sensitiv) —
        alle vier lassen bei einem ECHTEN Facility-Objekt den Default (das
        ganze Objekt) statt des echten ``pk`` greifen bzw. ersetzen ihn
        durch ``None``."""
        calls = []
        monkeypatch.setattr(quota_module, "lock_facility_storage", lambda facility_id: calls.append(facility_id))
        monkeypatch.setattr(quota_module, "_configured_limit_bytes", lambda f: 1000)
        monkeypatch.setattr(quota_module, "_facility_totals", lambda f: (0, 0))

        facility_storage_overrun(facility, 10)

        assert calls == [facility.pk]

    def test_falls_back_to_the_object_itself_when_pk_is_truly_absent(self, monkeypatch):
        """Toetet ``mutmut_7`` (Default ``facility`` -> ``None``) und
        ``mutmut_10`` (3-Arg-Default entfaellt, ``AttributeError`` statt
        Fallback): fehlt einem uebergebenen Objekt ausnahmsweise ``pk``,
        muss der Fallback laut Original das Objekt SELBST sein."""
        facility_stub = types.SimpleNamespace()  # bewusst OHNE "pk"-Attribut
        calls = []
        monkeypatch.setattr(quota_module, "lock_facility_storage", lambda facility_id: calls.append(facility_id))
        monkeypatch.setattr(quota_module, "_configured_limit_bytes", lambda f: 1000)
        # Seit Refs #2259 liest ``facility_storage_overrun`` die Summe ueber den
        # gemeinsamen Verbrauchsrechner (Anhaenge + offene Importdateien); der
        # Stub ohne ``pk`` ist fuer dessen ImportJob-Abfrage kein gueltiger
        # Mandant. Gepinnt wird hier weiterhin ausschliesslich das
        # Sperr-Argument, nicht die Summenbildung.
        monkeypatch.setattr(quota_module, "_enforcement_totals", lambda f: (0, 0))

        facility_storage_overrun(facility_stub, 10)

        assert calls == [facility_stub]


# ---------------------------------------------------------------------------
# core.services.file_vault.quota — lock_facility_storage
# ---------------------------------------------------------------------------


class TestLockFacilityStorage:
    def test_fail_loud_message_is_exact_outside_a_transaction(self, monkeypatch):
        """Toetet ``mutmut_6``/``_9`` (XX-Marker in der ersten/zweiten
        Haelfte der Fail-loud-``RuntimeError``-Meldung) und ``mutmut_10``
        (zweite Haelfte in GROSSBUCHSTABEN): nicht uebersetzter
        Ausnahmetext (kein ``_()``), den reale Aufrufer im Log/Traceback
        sehen -> volle exakte Zusicherung statt ``match=``."""
        monkeypatch.setattr(connection, "in_atomic_block", False)

        with pytest.raises(RuntimeError) as excinfo:
            lock_facility_storage(1)

        assert str(excinfo.value) == (
            "lock_facility_storage() ausserhalb einer Transaktion — die Xact-Sperre "
            "waere sofort wieder frei und wuerde nichts serialisieren."
        )

    def test_derives_the_lock_name_from_the_given_facility_id(self, monkeypatch):
        """Toetet ``mutmut_18`` (``storage_lock_name(None)`` statt
        ``storage_lock_name(facility_id)``): OHNE den echten
        Mandanten-Bezug wuerden ALLE Einrichtungen unter EINEM
        Advisory-Lock-Schluessel serialisieren statt je Einrichtung
        eigenstaendig (Sperr-Mutant, #2178). ``mutmut_16`` (SQL-Keyword
        ``select`` statt ``SELECT``) ist ``equivalent`` (PostgreSQL ist
        keyword-case-insensitiv) und nicht hier gepinnt."""
        calls = []
        monkeypatch.setattr(
            quota_module, "storage_lock_name", lambda facility_id: calls.append(facility_id) or "irrelevant"
        )

        with transaction.atomic():
            lock_facility_storage(4242)

        assert calls == [4242]


# ---------------------------------------------------------------------------
# core.services.file_vault.storage — _commit_staged_upload
# ---------------------------------------------------------------------------


class TestCommitStagedUploadQuotaBackstopAttribution:
    def test_passes_the_acting_user_to_the_quota_backstop(self, monkeypatch, settings, tmp_path, facility, staff_user):
        """Toetet ``mutmut_10`` (``check_storage_quota_at_commit(owner,
        None, staged.file_size)`` statt ``..., user, ...``): laut Docstring
        von ``check_storage_quota_at_commit`` fliesst ``user`` NUR in die
        Audit-Attribution der ``StorageQuotaBackstopError``-Ausnahme im
        Ablehnungsfall — mit ``None`` geht die Zuordnung, WER den
        abgelehnten Upload ausgeloest hat, verloren (Audit-Mutant).
        ``mutmut_50`` (``is_current=True``-Kwarg weggelassen) ist
        ``equivalent`` (Modellfeld-Default ist ebenfalls ``True``, siehe
        Moduldocstring) und nicht hier gepinnt."""
        settings.MEDIA_ROOT = str(tmp_path)
        event = _make_event(facility)
        ft = FieldTemplate.objects.create(facility=facility, name="Scan", field_type=FieldTemplate.FieldType.FILE)
        uploaded = SimpleUploadedFile("real.pdf", PDF_HEADER, content_type="application/pdf")
        staged = prepare_encrypted_upload(facility, uploaded, staff_user, owner=event)
        calls = []
        monkeypatch.setattr(storage_module, "check_storage_quota_at_commit", lambda owner, user, n: calls.append(user))

        _commit_staged_upload(event, ft, staged, staff_user, audit=False)

        assert calls == [staff_user]


# ---------------------------------------------------------------------------
# core.services.file_vault.storage — _lock_superseded_row
#
# Alle vier Batch-Mutanten (mutmut_2/_3/_4/_8) sind ``equivalent`` (siehe
# Moduldocstring) — kein Pin in dieser Datei.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# core.services.user_access — resend_invitation_for_any_user
# ---------------------------------------------------------------------------


class TestResendInvitationForAnyUserGegenFremdeAkteursobjekte:
    """Die drei Berechtigungs-Flags ``is_authenticated``/``is_active``/
    ``is_super_admin`` werden per ``getattr(actor, <flag>, False)``
    fail-closed gelesen. Fehlt einem Akteur-Stand-in das jeweilige
    Attribut, MUSS das Original mit ``PermissionDenied`` ablehnen. Zwei
    Mutantenklassen je Flag brechen das: der 3-Arg-Default faellt weg
    (crasht mit ``AttributeError``) oder der Default kippt auf ``True``
    (Rechte-Eskalation) — je Flag ein Stand-in, der die BEIDEN anderen
    Flags explizit auf ``True`` setzt, damit ausschliesslich das
    untersuchte Flag ueber Ablehnung/Durchlass entscheidet."""

    def test_missing_is_authenticated_denies_conservatively(self):
        """Toetet ``mutmut_9`` (2-Arg-``getattr`` fuer
        ``is_authenticated``) und ``mutmut_12`` (Default ``True`` statt
        ``False``). ``mutmut_6`` (Default ``None`` statt ``False``) ist
        ``equivalent`` (siehe Moduldocstring) und nicht hier gepinnt."""
        actor = types.SimpleNamespace(is_active=True, is_super_admin=True)

        with pytest.raises(PermissionDenied):
            resend_invitation_for_any_user(types.SimpleNamespace(), actor=actor, ip_address=None)

    def test_missing_is_active_denies_conservatively(self):
        """Toetet ``mutmut_19`` (2-Arg-``getattr`` fuer ``is_active``) und
        ``mutmut_22`` (Default ``True`` statt ``False``). ``mutmut_16``
        ist ``equivalent`` und nicht hier gepinnt."""
        actor = types.SimpleNamespace(is_authenticated=True, is_super_admin=True)

        with pytest.raises(PermissionDenied):
            resend_invitation_for_any_user(types.SimpleNamespace(), actor=actor, ip_address=None)

    def test_missing_is_super_admin_denies_conservatively(self):
        """Toetet ``mutmut_29`` (2-Arg-``getattr`` fuer
        ``is_super_admin``) und ``mutmut_32`` (Default ``True`` statt
        ``False`` — schwerwiegende Rechte-Eskalation beim
        installationsweiten Einladungsversand). ``mutmut_26`` ist
        ``equivalent`` und nicht hier gepinnt."""
        actor = types.SimpleNamespace(is_authenticated=True, is_active=True)

        with pytest.raises(PermissionDenied):
            resend_invitation_for_any_user(types.SimpleNamespace(), actor=actor, ip_address=None)


class TestResendInvitationForAnyUserMeldungUndSchreibScope:
    def test_permission_denied_message_is_exact(self, staff_user):
        """Toetet ``mutmut_33`` (Meldung -> ``None``), ``mutmut_34``
        (XX-Marker um den GESAMTEN Text — nicht per ``_()`` uebersetzt,
        keine Skip-Ausnahme) und ``mutmut_35``/``_36``
        (Klein-/Grossschreibung, ebenfalls kein i18n-String)."""
        with pytest.raises(PermissionDenied) as excinfo:
            resend_invitation_for_any_user(types.SimpleNamespace(), actor=staff_user, ip_address=None)

        assert str(excinfo.value) == "Der installationsweite Einladungsversand ist nur für super_admin erlaubt."

    def test_password_entwertung_speichert_ausschliesslich_das_passwort_feld(self, facility, super_admin_user):
        """Toetet ``mutmut_45`` (``target.save(update_fields=None)`` statt
        ``update_fields=["password"]``): Schreib-Scope-Regel (a) —
        ``target`` ist beim Save weder per ``select_for_update`` gesperrt
        noch unmittelbar zuvor frisch geladen, ein voller Save waere ein
        Lost-Update jedes anderen, gleichzeitig geaenderten In-Memory-
        Feldes."""
        target = _pending_target(facility)
        assert target.has_usable_password()
        captured_update_fields = []
        real_save = User.save

        def spy_save(self, *args, **kwargs):
            if self.pk == target.pk:
                captured_update_fields.append(kwargs.get("update_fields"))
            return real_save(self, *args, **kwargs)

        with (
            patch.object(User, "save", spy_save),
            patch("core.services.user_access.send_invite_email", return_value=True),
        ):
            resend_invitation_for_any_user(target, actor=super_admin_user, ip_address=None)

        assert captured_update_fields == [["password"]]
