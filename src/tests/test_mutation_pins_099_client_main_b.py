"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.client.main``,
Batch ``main-b`` (Funktionen ``restore_client``, ``anonymize_client_now``,
``reject_client_deletion``, ``create_or_get_client_deletion_request``,
``_generalize_event_timestamps``, ``_event_ids_for_client``, ``_redact_event_history``,
``create_client``, ``resolve_writable_client``, ``anonymize_eligible_soft_deleted_clients``).

Quelle: Vollmutation #2174, Batch ``main-b``
(``welle-l-0.99/reports/m-scratch/batch-main-b.json``,
Triage ``welle-l-0.99/reports/m-scratch/pins-main-b/triage-099-main-b.json``). Die Batch hat 55
Survivor-Mutanten; 50 sind Testlücken (``test``) und 5 ``equivalent``. Keine ``bug``-Urteile.

* **Meldungs-VERLUST/-varianten an der Service-API (37 Mutanten, neun Guard-Stellen, neun
  Tests).** ``raise ValidationError(_(text))`` wird zu ``raise ValidationError(None)`` (Exception
  traegt die Meldung ``"None"``) bzw. ``raise ValidationError(_(None))`` (``gettext_lazy(None)`` —
  jede Stringifizierung, auch ``exc.value.messages``, bricht mit ``AttributeError`` ab); beides ist
  an der Service-API beobachtbares Verhalten. Neun Tests rufen je die betroffene Guard-Stelle auf
  und vergleichen ``exc_info.value.messages`` exakt gegen den Original-Wortlaut (kein ``match=`` —
  Teilstring-Suche wuerde weder ``XX``-Marker noch den ``None``-Verlust zuverlaessig von einem
  aehnlichen Text unterscheiden). Ein Guard mit mehreren Survivor-Varianten wird von DEMSELBEN Test
  gemeinsam getoetet, da alle an derselben Stelle denselben Meldungsverlust erzeugen. Drei Guards
  ohne ``None``-Verlust in diesem Batch (nur ``XX``-Marker/Groß-Klein) sind trotzdem gepinnt, weil
  die Service-Funktion ohnehin fuer die begleitenden Prio-1/2-Mutanten vollstaendig ueber alle
  Zweige getestet werden muss.

* **Scope-Filter-Verlust bei ``select_for_update()`` (fünf Mutanten, Prio 1).**
  ``x_anonymize_client_now__mutmut_4`` und ``x_reject_client_deletion__mutmut_14`` entfernen den
  ``pk``-Teil eines ``.filter(pk=..., facility=...)``/``.get(pk=..., facility=...)`` — ohne ihn
  matcht die Abfrage JEDE Zeile der Einrichtung (kein ``ORDER BY``, siehe Regel „eine beobachtete
  Zeilenreihenfolge ist kein Äquivalenzbeweis"): ``anonymize_client_now`` würde eine ANDERE Person
  anonymisieren, ``reject_client_deletion`` wirft bei einem zweiten offenen Antrag
  ``MultipleObjectsReturned``. Die spiegelbildlichen ``facility``-Filter-Verlust-Mutanten
  (``x_anonymize_client_now__mutmut_5``, ``x_reject_client_deletion__mutmut_15``,
  ``x_create_or_get_client_deletion_request__mutmut_5``) sind KEINE Äquivalente, sondern derselbe
  Mandanten-Scope-Verlust am anderen Filterteil: der ``facility``-Filter beim gesperrten Reload ist
  die App-seitige Mandantengrenze (RLS wird in der Testsuite nicht durchgesetzt) und schützt gegen
  eine vom Aufrufer im Speicher gehaltene, von der DB abweichende Facility-Annahme — exakt das
  Muster von ``x_approve_client_deletion__mutmut_15``/``…mutmut_31`` in Batch ``main-a``
  (``src/tests/test_mutation_pins_099_client_main_a.py``): ``client.facility``/
  ``deletion_request.facility`` wird NACH dem Laden manuell auf eine ANDERE Einrichtung gesetzt
  (nicht gespeichert); das Original verwirft diese Annahme beim Reload (``DoesNotExist`` bzw.
  ``locked is None``), die Mutante ignoriert sie und verarbeitet die echte Zeile trotzdem.

* **Audit-Detail-/Attributionsverlust (fünf Mutanten, Prio 1).** ``restore_client`` und
  ``create_client`` übergeben ``None`` statt der handelnden Person an
  ``audit_client_event``/``log_activity`` — der Nachweis, WER restauriert/angelegt hat, geht
  verloren. Drei Varianten von ``anonymize_client_now`` verlieren/verfälschen das
  ``deleted_at``-Feld im ``AuditLog.detail`` (Beginn der Aufbewahrungsfrist).
  ``reject_client_deletion_mutmut_30`` setzt ``reviewed_at`` auf ``None`` statt des
  Ablehnungszeitpunkts.

* **``anonymize_client``-Innenaufruf-Log (zwei Mutanten, Prio 2).**
  ``x_anonymize_client_now__mutmut_32/34`` entfernen ``user=user`` aus dem Aufruf von
  ``anonymize_client(locked, user=user)`` — ``user`` fließt dort NUR in den strukturierten Log
  ``"client_anonymized"`` (``extra={"user_id": ...}``) ein, NICHT in den persistierten
  ``AuditLog`` (der eigene ``audit_client_event``-Aufruf direkt danach trägt ``user`` unverändert)
  — daher Prio 2 (Ops-Log), nicht Prio 1.

* **``only()`` ohne ``"pk"`` (ein Mutant, equivalent).**
  ``x__generalize_event_timestamps__mutmut_4`` entfernt ``"pk"`` aus
  ``.only("pk", "occurred_at")`` — Django lädt die Primary-Key-Spalte bei ``only()`` IMMER
  zusätzlich, unabhängig von der übergebenen Feldliste (empirisch verifiziert, siehe
  ``reports/m-scratch/pins-main-b/only_pk_check.py``: ``str(Event.objects.only("occurred_at").query)``
  enthält weiterhin ``"core_event"."id"``).

* **``continue`` vs. ``break`` an einem unerreichbaren Zweig (ein Mutant, equivalent).**
  ``x__generalize_event_timestamps__mutmut_12`` ersetzt im ``if event.occurred_at is None:
  continue``-Zweig ``continue`` durch ``break``. ``Event.occurred_at`` ist seit der Ersterstellung
  (Migration ``0006_event_eventhistory``) OHNE ``null=True`` definiert — NOT NULL seit jeher, keine
  Nullable-Historie. Ein direkter Insert-Versuch mit ``occurred_at=None`` wurde verifiziert und
  scheitert mit ``IntegrityError: null value in column "occurred_at" ... violates not-null
  constraint``. Keine Zeile, aktuell oder historisch, kann dieses Feld je ``NULL`` tragen — der
  Zweig ist unerreichbar, kein Aufrufer kann ``continue`` von ``break`` unterscheiden.

* **``current_connection_owns()``-Großschreibung (ein Mutant, equivalent).**
  ``x__redact_event_history__mutmut_8`` ändert das Tabellenname-Literal auf Großschreibung — der
  Cast ``%s::regclass`` faltet einen unquotierten Bezeichner auf Kleinschreibung (empirisch per
  ``psql`` verifiziert: ``SELECT 'CORE_EVENTHISTORY'::regclass = 'core_eventhistory'::regclass``
  → ``t``).

* **Bereits-Default-Keyword (ein Mutant, equivalent).**
  ``x_anonymize_eligible_soft_deleted_clients__mutmut_45`` entfernt ``user=None`` aus
  ``anonymize_client(client, user=None)`` — die Zielfunktion hat ``user=None`` bereits als
  eigenen Default (``core/services/client/main.py:666``), byte-identisches Verhalten.

* **``values_list(flat=True)`` ohne Positionsfeld (ein Mutant, equivalent).**
  ``x__event_ids_for_client__mutmut_4`` entfernt das Positionsargument ``"pk"``. Django hat dafür
  einen dokumentierten Sonderfall (``QuerySet.values_list``, django/db/models/query.py): bei
  ``flat=True`` OHNE explizite Feldnamen setzt Django selbst
  ``fields = [model._meta.concrete_fields[0].attname]`` — für ``Event`` ist das ``"id"``, also
  genau das Feld, auf das auch ``"pk"`` auflöst (empirisch verifiziert:
  ``Event._meta.concrete_fields[0].attname == "id"``). ``values_list(flat=True)`` ist damit
  byte-identisch zu ``values_list("pk", flat=True)`` — kein ``TypeError``, keine andere
  Ergebnisliste; ein zunächst geschriebener Pin blieb unter dieser Mutante grün.

``_encryption_key`` liegt modulweit an: der Mutantenbaum (``mutants/`` im Checkout) hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``; ohne die Fixture wären die Pins dort aus dem
FALSCHEN Grund rot.
"""

from __future__ import annotations

import logging
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import AuditLog, Client
from core.models.activity import Activity
from core.models.workitem import DeletionRequest
from core.services.client.main import (
    anonymize_client_now,
    approve_client_deletion,
    create_client,
    create_or_get_client_deletion_request,
    reject_client_deletion,
    resolve_writable_client,
    restore_client,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# restore_client
# ---------------------------------------------------------------------------


class TestRestoreClientGuardMessages:
    """Pins die drei Guard-Meldungen von ``restore_client`` exakt.

    ``x_restore_client__mutmut_2/4/5/6/7/8/9`` (Merge-Tombstone-Guard),
    ``…mutmut_11/12/13/14`` (Papierkorb-Guard),
    ``…mutmut_18/19/20`` (terminaler anonymized_at-Guard).
    """

    def test_merged_tombstone_refused_with_exact_message(self, facility, staff_user):
        target = Client.objects.create(facility=facility, pseudonym="Ziel")
        tombstone = Client.objects.create(facility=facility, pseudonym="Quelle")
        tombstone.merged_into = target
        tombstone.save(update_fields=["merged_into"])
        tombstone.soft_delete(user=staff_user)

        with pytest.raises(ValidationError) as exc_info:
            restore_client(tombstone, staff_user)

        assert exc_info.value.messages == [
            "Diese Person wurde mit einer anderen zusammengeführt — "
            "Wiederherstellen nur über das Aufheben der Zusammenführung."
        ]

    def test_not_in_trash_refused_with_exact_message(self, client_identified, staff_user):
        with pytest.raises(ValidationError) as exc_info:
            restore_client(client_identified, staff_user)

        assert exc_info.value.messages == ["Person ist nicht im Papierkorb."]

    def test_terminally_anonymized_refused_with_exact_message(self, client_identified, staff_user):
        client_identified.soft_delete(user=staff_user)
        client_identified.anonymized_at = timezone.now()
        client_identified.save(update_fields=["anonymized_at"])

        with pytest.raises(ValidationError) as exc_info:
            restore_client(client_identified, staff_user)

        assert exc_info.value.messages == [
            "Diese Person wurde endgültig anonymisiert und kann nicht wiederhergestellt werden."
        ]

    def test_restore_audit_entry_records_the_acting_user(self, client_identified, staff_user, admin_user):
        """Pins ``x_restore_client__mutmut_22`` (``audit_client_event(client, user, ...)`` ->
        ``audit_client_event(client, None, ...)``): der CLIENT_RESTORED-Nachweis muss die
        handelnde Person tragen, nicht ``None``.
        """
        client_identified.soft_delete(user=staff_user)

        restore_client(client_identified, admin_user)

        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_RESTORED,
            target_id=str(client_identified.pk),
        ).first()
        assert entry is not None
        assert entry.user_id == admin_user.pk


# ---------------------------------------------------------------------------
# anonymize_client_now
# ---------------------------------------------------------------------------


class TestAnonymizeClientNowGuardMessages:
    """Pins die Guard-Meldungen UND den ``pk``/``facility``-Scope von ``anonymize_client_now``.

    ``x_anonymize_client_now__mutmut_4`` (pk-Scope-Verlust),
    ``…mutmut_5`` (facility-Scope-Verlust),
    ``…mutmut_7/8/9/10/11`` (locked-is-None-Guard),
    ``…mutmut_15`` (not-is_deleted-Guard, gleicher Wortlaut),
    ``…mutmut_21/22`` (anonymized_at-Terminal-Guard).
    """

    def test_missing_client_refused_and_other_clients_in_facility_untouched(self, facility, staff_user):
        """Ein Klient, dessen ``pk`` in dieser Einrichtung nicht existiert, muss abgelehnt
        werden — UND darf, selbst wenn er faelschlich matcht, keine ANDERE (echte) Person der
        Einrichtung anonymisieren (pins den entfernten ``pk``-Filter, mutmut_4).
        """
        real_trashed = Client.objects.create(facility=facility, pseudonym="Echte-Person")
        real_trashed.soft_delete(user=staff_user)
        phantom = Client(id=uuid.uuid4(), facility=facility)

        with pytest.raises(ValidationError) as exc_info:
            anonymize_client_now(phantom, staff_user)

        assert exc_info.value.messages == ["Nur Personen im Papierkorb können anonymisiert werden."]
        real_trashed.refresh_from_db()
        assert real_trashed.is_deleted is True
        assert real_trashed.anonymized_at is None

    def test_facility_reload_guard_discards_a_stale_in_memory_facility_assumption(
        self, second_facility, client_identified, staff_user, second_facility_user
    ):
        """Pin: ``x_anonymize_client_now__mutmut_5`` — ``select_for_update().filter(pk=
        client.pk, facility=client.facility_id)`` -> ``filter(pk=client.pk, )``
        (facility-Filter entfernt). Gleiches Muster wie
        ``x_approve_client_deletion__mutmut_15``/``…mutmut_31`` (Batch ``main-a``):
        ``client.facility`` wird NACH dem Laden manuell auf eine ANDERE Einrichtung
        gesetzt (nicht gespeichert) — eine im Speicher gehaltene, von der DB abweichende
        Annahme des Aufrufers. Original: der gesperrte Reload filtert nach dieser (jetzt
        falschen) Annahme und findet die Zeile nicht — lehnt ab. Mutant: der Reload
        ignoriert die Annahme und anonymisiert die echte Zeile trotzdem.

        Die handelnde Person gehoert zur ANGENOMMENEN Einrichtung: die
        Einrichtungsgrenze vor der Sperre vergleicht mit der Annahme und laesst
        den Aufruf bis zum gesperrten Reload durch, der die Annahme verwirft.
        """
        client_identified.soft_delete(user=staff_user)
        client_identified.facility = second_facility

        with pytest.raises(ValidationError) as exc_info:
            anonymize_client_now(client_identified, second_facility_user)

        assert exc_info.value.messages == ["Nur Personen im Papierkorb können anonymisiert werden."]
        real = Client.objects.get(pk=client_identified.pk)
        assert real.is_deleted is True
        assert real.anonymized_at is None

    def test_client_not_in_trash_refused_with_exact_message(self, client_identified, staff_user):
        with pytest.raises(ValidationError) as exc_info:
            anonymize_client_now(client_identified, staff_user)

        assert exc_info.value.messages == ["Nur Personen im Papierkorb können anonymisiert werden."]

    def test_already_anonymized_refused_with_exact_message(self, client_identified, staff_user):
        client_identified.soft_delete(user=staff_user)
        client_identified.anonymized_at = timezone.now()
        client_identified.save(update_fields=["anonymized_at"])

        with pytest.raises(ValidationError) as exc_info:
            anonymize_client_now(client_identified, staff_user)

        assert exc_info.value.messages == ["Diese Person wurde bereits endgültig anonymisiert."]

    def test_audit_entry_detail_records_the_real_deleted_at_timestamp(self, client_identified, staff_user):
        """Pins ``x_anonymize_client_now__mutmut_39/44/47`` (``deleted_at``-Detail verloren/
        verfälscht): der CLIENT_ANONYMIZED-Nachweis muss den echten ``deleted_at``-Zeitstempel
        tragen (Beginn der Aufbewahrungsfrist), nicht ``None``/das Literal ``"None"``/fehlend.
        """
        client_identified.soft_delete(user=staff_user)
        deleted_at_before = client_identified.deleted_at

        anonymize_client_now(client_identified, staff_user)

        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_ANONYMIZED,
            target_id=str(client_identified.pk),
        ).first()
        assert entry is not None
        assert entry.detail.get("deleted_at") == str(deleted_at_before)

    def test_inner_anonymize_client_log_carries_the_acting_user(self, caplog, client_identified, staff_user):
        """Pins ``x_anonymize_client_now__mutmut_32/34`` (``anonymize_client(locked, user=user)``
        verliert ``user``): der strukturierte Log ``client_anonymized`` muss die handelnde
        Person tragen — der einzige Ort, an dem ``anonymize_client`` den Parameter verwendet.
        """
        client_identified.soft_delete(user=staff_user)

        with caplog.at_level(logging.INFO, logger="core.services.client.main"):
            anonymize_client_now(client_identified, staff_user)

        matches = [record for record in caplog.records if record.getMessage() == "client_anonymized"]
        assert matches, "erwarteter Log-Eintrag 'client_anonymized' fehlt"
        assert matches[-1].user_id == staff_user.pk


# ---------------------------------------------------------------------------
# reject_client_deletion
# ---------------------------------------------------------------------------


class TestRejectClientDeletionMessages:
    """Pins die Guard-Meldungen, den ``pk``/``facility``-Scope und ``reviewed_at`` von
    ``reject_client_deletion``.

    ``x_reject_client_deletion__mutmut_2/3/4/5`` (Antragsteller-Guard),
    ``…mutmut_7/8/9/10`` (Bestätigungsrecht-Guard),
    ``…mutmut_14`` (pk-Scope-Verlust), ``…mutmut_15`` (facility-Scope-Verlust),
    ``…mutmut_17/18/19/20/21`` (Merge-Antrag-Guard),
    ``…mutmut_25/26`` (PENDING-Recheck-Guard), ``…mutmut_30`` (reviewed_at verloren).
    """

    def test_self_review_refused_with_exact_message(self, facility, staff_user):
        client = Client.objects.create(facility=facility, pseudonym="P-Selbst")
        dr, _created = create_or_get_client_deletion_request(client, staff_user, "Grund")

        with pytest.raises(ValidationError) as exc_info:
            reject_client_deletion(dr, staff_user)

        assert exc_info.value.messages == ["Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."]

    def test_reviewer_without_confirmation_right_refused_with_exact_message(self, facility, staff_user, assistant_user):
        """Pins ``x_reject_client_deletion__mutmut_7/8/9/10``: Verlust und
        Verfälschungen der Meldung des Bestätigungsrecht-Guards."""
        client = Client.objects.create(facility=facility, pseudonym="P-Kein-Recht")
        dr, _created = create_or_get_client_deletion_request(client, staff_user, "Grund")

        with pytest.raises(ValidationError) as exc_info:
            reject_client_deletion(dr, assistant_user)

        assert exc_info.value.messages == ["Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."]

    def test_pk_scope_filter_prevents_a_different_pending_request_from_being_touched(
        self, facility, staff_user, lead_user
    ):
        """Pins ``x_reject_client_deletion__mutmut_14`` (pk-Filter aus dem gesperrten Re-Fetch
        entfernt): bei einem ZWEITEN offenen Antrag derselben Einrichtung darf nur der
        adressierte Antrag betroffen sein — ohne pk-Filter matcht ``.get(facility=...)`` beide
        Zeilen und wirft ``MultipleObjectsReturned``.
        """
        client_a = Client.objects.create(facility=facility, pseudonym="P-A")
        client_b = Client.objects.create(facility=facility, pseudonym="P-B")
        dr_a, _ = create_or_get_client_deletion_request(client_a, staff_user, "Grund A")
        dr_b, _ = create_or_get_client_deletion_request(client_b, staff_user, "Grund B")

        reject_client_deletion(dr_a, lead_user)

        dr_a.refresh_from_db()
        dr_b.refresh_from_db()
        assert dr_a.status == DeletionRequest.Status.REJECTED
        assert dr_b.status == DeletionRequest.Status.PENDING

    def test_facility_reload_guard_discards_a_stale_in_memory_facility_assumption(
        self, facility, second_facility, staff_user
    ):
        """Pin: ``x_reject_client_deletion__mutmut_15`` — ``select_for_update().get(pk=
        deletion_request.pk, facility=deletion_request.facility)`` -> ``get(pk=
        deletion_request.pk, )`` (facility-Filter entfernt). Gleiches Muster wie
        ``x_approve_client_deletion__mutmut_15`` (Batch ``main-a``):
        ``deletion_request.facility`` wird NACH dem Laden manuell auf eine ANDERE
        Einrichtung gesetzt (nicht gespeichert) — eine im Speicher gehaltene, von der DB
        abweichende Annahme des Aufrufers. Original: der gesperrte Reload filtert nach
        dieser (jetzt falschen) Annahme und findet die Zeile nicht — ``DoesNotExist``.
        Mutant: der Reload ignoriert die Annahme und lehnt den Antrag trotzdem ab.

        Die pruefende Person gehoert zur ANGENOMMENEN Einrichtung: die
        Einrichtungsgrenze vor der Antragssperre vergleicht mit der Annahme und
        laesst den Aufruf bis zum gesperrten Reload durch, der die Annahme
        verwirft (Mutant: Reload gelingt, die Grenze unter der Sperre meldet
        die fremde Einrichtung statt ``DoesNotExist``).
        """
        from core.models import User

        reviewer = User.objects.create_user(
            username="pin099_leitung_angenommen_b",
            role=User.Role.LEAD,
            facility=second_facility,
            can_confirm_deletion=True,
        )
        client = Client.objects.create(facility=facility, pseudonym="P-FacilityGuard")
        dr, _created = create_or_get_client_deletion_request(client, staff_user, "Grund")
        dr.facility = second_facility

        with pytest.raises(DeletionRequest.DoesNotExist):
            reject_client_deletion(dr, reviewer)

        real = DeletionRequest.objects.get(pk=dr.pk)
        assert real.status == DeletionRequest.Status.PENDING

    def test_merge_request_refused_with_exact_message(self, facility, staff_user, lead_user):
        target = Client.objects.create(facility=facility, pseudonym="Merge-Ziel")
        source = Client.objects.create(facility=facility, pseudonym="Merge-Quelle")
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=source.pk,
            merge_into=target,
            reason="Zusammenführung",
            requested_by=staff_user,
        )

        with pytest.raises(ValidationError) as exc_info:
            reject_client_deletion(dr, lead_user)

        assert exc_info.value.messages == [
            "Dies ist ein Zusammenführungs-Antrag — er wird über den Zusammenführungs-Weg entschieden."
        ]

    def test_already_decided_request_refused_with_exact_message(self, facility, staff_user, lead_user, admin_user):
        client = Client.objects.create(facility=facility, pseudonym="P-Entschieden")
        dr, _created = create_or_get_client_deletion_request(client, staff_user, "Grund")
        approve_client_deletion(dr, lead_user)

        with pytest.raises(ValidationError) as exc_info:
            reject_client_deletion(dr, admin_user)

        assert exc_info.value.messages == ["Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."]

    def test_reviewed_at_timestamp_survives_rejection(self, facility, staff_user, lead_user):
        client = Client.objects.create(facility=facility, pseudonym="P-ReviewedAt")
        dr, _created = create_or_get_client_deletion_request(client, staff_user, "Grund")
        before = timezone.now()

        reject_client_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.reviewed_at is not None
        assert dr.reviewed_at >= before


# ---------------------------------------------------------------------------
# create_or_get_client_deletion_request
# ---------------------------------------------------------------------------


class TestCreateOrGetClientDeletionRequestMessages:
    """Pins die beiden Guard-Meldungen UND den ``facility``-Scope von
    ``create_or_get_client_deletion_request``.

    ``x_create_or_get_client_deletion_request__mutmut_5`` (facility-Scope-Verlust),
    ``…mutmut_9/10`` (merged_into-Guard), ``…mutmut_14`` (is_deleted-Guard).
    """

    def test_merged_tombstone_is_refused_with_exact_message(self, facility, staff_user):
        target = Client.objects.create(facility=facility, pseudonym="Ziel")
        tombstone = Client.objects.create(facility=facility, pseudonym="Quelle")
        tombstone.merged_into = target
        tombstone.save(update_fields=["merged_into"])
        tombstone.soft_delete(user=staff_user)

        with pytest.raises(ValidationError) as exc_info:
            create_or_get_client_deletion_request(tombstone, staff_user, "Grund")

        assert exc_info.value.messages == ["Diese Person ist bereits zusammengeführt."]

    def test_client_already_in_trash_is_refused_with_exact_message(self, facility, staff_user):
        client = Client.objects.create(facility=facility, pseudonym="P-Papierkorb")
        client.soft_delete(user=staff_user)

        with pytest.raises(ValidationError) as exc_info:
            create_or_get_client_deletion_request(client, staff_user, "Grund")

        assert exc_info.value.messages == ["Diese Person liegt bereits im Papierkorb."]

    def test_facility_reload_guard_discards_a_stale_in_memory_facility_assumption(
        self, facility, second_facility, second_facility_user
    ):
        """Pin: ``x_create_or_get_client_deletion_request__mutmut_5`` —
        ``select_for_update().get(pk=client.pk, facility=client.facility_id)`` -> ``get(
        pk=client.pk, )`` (facility-Filter entfernt). Gleiches Muster wie
        ``x_approve_client_deletion__mutmut_31`` (Batch ``main-a``): ``client.facility``
        wird NACH dem Laden manuell auf eine ANDERE Einrichtung gesetzt (nicht
        gespeichert) — eine im Speicher gehaltene, von der DB abweichende Annahme des
        Aufrufers. Original: der gesperrte Reload filtert nach dieser (jetzt falschen)
        Annahme und findet die Zeile nicht — ``DoesNotExist``. Mutant: der Reload
        ignoriert die Annahme und legt den Antrag trotzdem an.

        Die antragstellende Person gehoert zur ANGENOMMENEN Einrichtung: die
        Einrichtungsgrenze vor der Sperre vergleicht mit der Annahme und laesst
        den Aufruf bis zum gesperrten Reload durch, der die Annahme verwirft.
        """
        client = Client.objects.create(facility=facility, pseudonym="P-CreateFacilityGuard")
        client.facility = second_facility

        with pytest.raises(Client.DoesNotExist):
            create_or_get_client_deletion_request(client, second_facility_user, "Grund")

        assert not DeletionRequest.objects.filter(target_id=client.pk).exists()


# ---------------------------------------------------------------------------
# create_client
# ---------------------------------------------------------------------------


class TestCreateClientActivityActor:
    def test_activity_actor_is_the_creating_user_not_none(self, facility, staff_user):
        """Pins ``x_create_client__mutmut_26`` (``log_activity(..., actor=user, ...)`` ->
        ``actor=None``): die Activity-Zeile fuer CLIENT_CREATED muss die anlegende Person tragen.
        """
        client = create_client(facility, staff_user, pseudonym="Aktivitaets-Test")

        activity = Activity.objects.get(target_id=client.pk, verb=Activity.Verb.CREATED)
        assert activity.actor == staff_user


# ---------------------------------------------------------------------------
# resolve_writable_client
# ---------------------------------------------------------------------------


class TestResolveWritableClientGuardMessage:
    def test_missing_facility_raises_valueerror_with_exact_message(self):
        """Pins ``x_resolve_writable_client__mutmut_3`` (XX-Marker auf der ValueError-Meldung)."""
        with pytest.raises(ValueError) as exc_info:
            resolve_writable_client(None, uuid.uuid4(), missing_error="irrelevant")

        assert str(exc_info.value) == "Facility ist für die Personenauflösung erforderlich."
