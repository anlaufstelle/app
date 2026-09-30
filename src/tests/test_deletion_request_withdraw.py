"""Selbst-Rueckzug eigener Loeschantraege (Refs #2040).

Ein versehentlich gestellter Loeschantrag war bis hierher eine Sackgasse: die
Antragstellerin sah ihn zwar (Refs #1736 B), konnte ihn aber nur ueber
Leitung/Genehmiger-Pool wieder loswerden. Der Rueckzug ist deshalb ein eigener,
auditierter Endstatus (``ApprovalStatus.WITHDRAWN``) — kein stilles Loeschen
der Antragszeile, weil der Vier-Augen-Vorgang nach Art. 5 Abs. 2 DSGVO
nachweisbar bleiben muss.

Die Guards, die diese Datei pinnt:

* **fremd → 404** (nicht 403): die Existenz eines fremden Antrags darf nicht
  ueber den Statuscode leaken.
* **eigen, aber nicht mehr PENDING → Redirect + Meldung, KEIN Audit-Eintrag**
  (das Rennen gegen eine parallele Genehmigung).
* **``reviewed_by`` bleibt NULL** — der CHECK-Constraint
  ``deletion_request_different_reviewer`` risse bei ``reviewed_by == requested_by``.
* **doppelter POST = genau EIN Audit-Eintrag** (``select_for_update`` +
  Status-Recheck, Muster ``reject_deletion``).
* **nach dem Rueckzug ist ein neuer Antrag moeglich** — der partielle
  Unique-Index ``unique_pending_deletion_request`` greift bei WITHDRAWN nicht.
"""

from uuid import uuid4

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DeletionRequest, Event
from core.services.deletion import withdraw_deletion_request
from core.services.events import request_deletion

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_qualified(facility, client_qualified, doc_type_contact, staff_user):
    return Event.objects.create(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 20, "notiz": "Qualifiziert"},
        created_by=staff_user,
    )


@pytest.fixture
def own_request(event_qualified, staff_user):
    """PENDING-Antrag, gestellt von ``staff_user``."""
    return request_deletion(event_qualified, staff_user, "Versehentlich gestellt")


def _withdraw_audits(dr):
    return AuditLog.objects.filter(
        action=AuditLog.Action.DELETION_WITHDRAWN,
        target_type="DeletionRequest",
        target_id=str(dr.pk),
    )


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------------------
# 1. Service
# ---------------------------------------------------------------------------


class TestWithdrawService:
    def test_missing_request_is_refused_as_already_decided(self, own_request, staff_user):
        own_request.pk = uuid4()

        with pytest.raises(ValidationError) as excinfo:
            withdraw_deletion_request(own_request, staff_user)

        assert excinfo.value.messages == [
            "Dieser Löschantrag ist bereits entschieden und kann nicht mehr zurückgezogen werden."
        ]

    def test_sets_withdrawn_status_and_keeps_reviewer_null(self, own_request, staff_user):
        withdraw_deletion_request(own_request, staff_user)

        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.WITHDRAWN
        # Der CHECK-Constraint ``deletion_request_different_reviewer`` verbietet
        # ``reviewed_by == requested_by`` — der Zeitpunkt traegt die Information.
        assert own_request.reviewed_by is None
        assert own_request.reviewed_at is not None

    def test_writes_exactly_one_pii_free_audit_entry(self, own_request, staff_user, event_qualified):
        withdraw_deletion_request(own_request, staff_user)

        entries = list(_withdraw_audits(own_request))
        assert len(entries) == 1
        entry = entries[0]
        assert entry.user_id == staff_user.pk
        assert entry.facility_id == own_request.facility_id
        # Muster ``DELETION_REQUESTED``: nur die Ziel-UUID, kein Pseudonym und
        # keine Begruendung (Refs #1093 — ``reason`` lebt am DeletionRequest).
        assert entry.detail == {"target_event": str(event_qualified.pk)}

    def test_second_call_on_stale_object_raises_and_writes_no_second_audit(self, own_request, staff_user):
        """TOCTOU: der zweite Aufruf haelt eine veraltete PENDING-Kopie."""
        stale = DeletionRequest.objects.get(pk=own_request.pk)
        withdraw_deletion_request(own_request, staff_user)

        with pytest.raises(ValidationError):
            withdraw_deletion_request(stale, staff_user)

        assert _withdraw_audits(own_request).count() == 1

    def test_foreign_requester_is_rejected(self, own_request, lead_user):
        with pytest.raises(ValidationError):
            withdraw_deletion_request(own_request, lead_user)

        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.PENDING
        assert not _withdraw_audits(own_request).exists()

    def test_new_request_possible_after_withdraw(self, own_request, staff_user, event_qualified):
        withdraw_deletion_request(own_request, staff_user)

        neu = request_deletion(event_qualified, staff_user, "Doch loeschen")
        assert neu.pk != own_request.pk
        assert neu.status == DeletionRequest.Status.PENDING
        assert DeletionRequest.objects.filter(target_id=event_qualified.pk).count() == 2


# ---------------------------------------------------------------------------
# 2. View — Gate
# ---------------------------------------------------------------------------


class TestWithdrawViewGate:
    def test_foreign_request_is_404_not_403(self, client, own_request, lead_user):
        """403 wuerde die Existenz eines fremden Antrags verraten."""
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))
        assert response.status_code == 404

        response = client.post(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))
        assert response.status_code == 404
        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.PENDING

    def test_cross_facility_is_404(self, client, own_request, second_facility_user):
        client.force_login(second_facility_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))
        assert response.status_code == 404

    def test_unknown_pk_is_404(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": "00000000-0000-0000-0000-000000000000"}))
        assert response.status_code == 404

    def test_anonymous_is_redirected_to_login(self, client, own_request):
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))
        assert response.status_code == 302
        assert "/login" in response.url


# ---------------------------------------------------------------------------
# 3. View — GET zeigt nur den Dialog
# ---------------------------------------------------------------------------


class TestWithdrawViewGet:
    def test_get_renders_consequence_dialog(self, client, own_request, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))

        assert response.status_code == 200
        content = response.content.decode()
        assert "deletion-withdraw-confirm" in content
        # Konsequenztext: was passiert — und was NICHT passiert.
        assert "Vier-Augen" in content
        assert "Es wird nichts gelöscht" in content

    def test_get_changes_nothing(self, client, own_request, staff_user):
        client.force_login(staff_user)
        client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))

        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.PENDING
        assert not _withdraw_audits(own_request).exists()

    def test_get_on_already_decided_request_redirects_with_message(self, client, own_request, staff_user, lead_user):
        from core.services.events import reject_deletion

        reject_deletion(own_request, lead_user)
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}), follow=False)

        assert response.status_code == 302
        assert _messages(response)
        assert not _withdraw_audits(own_request).exists()


# ---------------------------------------------------------------------------
# 4. View — POST
# ---------------------------------------------------------------------------


class TestWithdrawViewPost:
    def test_post_withdraws_and_audits_once(self, client, own_request, staff_user):
        client.force_login(staff_user)
        response = client.post(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))

        assert response.status_code == 302
        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.WITHDRAWN
        assert own_request.reviewed_by is None
        assert _withdraw_audits(own_request).count() == 1

    def test_double_post_is_idempotent(self, client, own_request, staff_user):
        client.force_login(staff_user)
        url = reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk})
        client.post(url)
        response = client.post(url)

        assert response.status_code == 302
        assert _withdraw_audits(own_request).count() == 1
        assert _messages(response)

    def test_post_on_approved_request_redirects_without_audit(self, client, own_request, staff_user, lead_user):
        from core.services.events import approve_deletion

        approve_deletion(own_request, lead_user)
        client.force_login(staff_user)
        response = client.post(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))

        assert response.status_code == 302
        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.APPROVED
        assert not _withdraw_audits(own_request).exists()
        assert _messages(response)

    def test_requester_without_other_access_lands_on_a_reachable_page(self, client, own_request, staff_user):
        """Refs #2040: ``can_access_deletion_requests`` faellt mit dem letzten
        eigenen PENDING-Antrag weg — ein blinder Redirect auf die Liste waere
        fuer die Antragstellerin ein 403 direkt nach der eigenen Aktion."""
        assert staff_user.can_access_deletion_requests is True
        client.force_login(staff_user)
        response = client.post(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}), follow=True)

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 5. Liste + Pruefansicht
# ---------------------------------------------------------------------------


class TestWithdrawUi:
    def test_list_offers_withdraw_only_on_own_pending_request(self, client, own_request, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_request_list"))
        content = response.content.decode()

        assert "deletion-withdraw-link" in content
        assert reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}) in content

    def test_list_hides_withdraw_on_foreign_request(self, client, own_request, lead_user):
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_request_list"))

        assert "deletion-withdraw-link" not in response.content.decode()

    def test_confirmer_sees_withdrawn_end_status_in_list(self, client, own_request, staff_user, lead_user):
        """Der Nachweis haengt an der KARTE, nicht an Sektions-ID und Text.

        Sektionsrahmen und Ueberschrift rendern auch bei leerem Bucket — ein
        Test auf beides bliebe gruen, wenn die WITHDRAWN-Abfrage aus der View
        verschwaende und gar keine Antragskarte mehr erschiene (Cross-Review
        Runde 1, Befund 7).
        """
        withdraw_deletion_request(own_request, staff_user)
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_request_list"))
        content = response.content.decode()

        assert "deletion-withdrawn-section" in content
        assert "Zurückgezogen" in content
        # Die konkrete Karte steht in der WITHDRAWN-Sektion …
        withdrawn_section = content.split('data-testid="deletion-withdrawn-section"', 1)[1]
        assert f'data-dr-pk="{own_request.pk}"' in withdrawn_section
        # … und nicht mehr unter „Ausstehend".
        pending_section = content.split('data-testid="deletion-withdrawn-section"', 1)[0]
        assert f'data-dr-pk="{own_request.pk}"' not in pending_section

    def test_review_get_on_withdrawn_shows_end_status_without_buttons(self, client, own_request, staff_user, lead_user):
        withdraw_deletion_request(own_request, staff_user)
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_review", kwargs={"pk": own_request.pk}))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'value="approve"' not in content
        assert 'value="reject"' not in content
        assert "deletion-withdrawn-notice" in content

    def test_review_post_on_withdrawn_is_rejected_cleanly(self, client, own_request, staff_user, lead_user):
        withdraw_deletion_request(own_request, staff_user)
        client.force_login(lead_user)
        response = client.post(reverse("core:deletion_review", kwargs={"pk": own_request.pk}), {"action": "approve"})

        assert response.status_code == 302
        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.WITHDRAWN
        assert _messages(response)


# ---------------------------------------------------------------------------
# 6. Wertelisten-Scan: der geteilte Enum-Wert darf den Art.-16-Pfad nicht
#    veraendern (``ApprovalStatus`` ist seit Refs #1834 geteilt).
# ---------------------------------------------------------------------------


class TestSharedEnumDoesNotLeakIntoRectification:
    def test_rectification_list_keeps_exactly_three_buckets(self, client, lead_user):
        """Der geteilte Enum-Wert darf im Art.-16-Pfad keine vierte Sektion erzeugen."""
        client.force_login(lead_user)
        response = client.get(reverse("core:rectification_list"))

        assert response.status_code == 200
        # ``.keys()`` ist hier NICHT redundant: ``response.context`` ist eine
        # ``ContextList``; ein ``in``/``for`` darauf iteriert die Kontexte, nicht
        # deren Schlüssel.
        buckets = {key for key in set(response.context.keys()) if key.endswith("_requests")}
        assert buckets == {"pending_requests", "approved_requests", "rejected_requests"}
        assert "Zurückgezogen" not in response.content.decode()

    def test_no_service_sets_withdrawn_on_a_rectification_request(self):
        """Quelltext-Guard: ``WITHDRAWN`` bleibt auf den Löschantrag beschränkt.

        Ein ``RectificationRequest.Status.WITHDRAWN`` wäre kein Syntaxfehler,
        sondern ein stiller Zustand ohne UI, ohne Audit-Aktion und ohne
        Rückweg — die Art.-16-Prüfansicht kennt ihn nicht.
        """
        from pathlib import Path

        quelle = Path(__file__).resolve().parents[1] / "core"
        treffer = [
            f"{pfad.relative_to(quelle)}:{nr}"
            for pfad in quelle.rglob("*.py")
            if "migrations" not in pfad.parts
            for nr, zeile in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1)
            if "RectificationRequest.Status.WITHDRAWN" in zeile
        ]
        assert treffer == []


# ---------------------------------------------------------------------------
# 6. Cross-Review-Nachtrag Runde 1, Refs #2040)
# ---------------------------------------------------------------------------


class TestWithdrawWithoutFacility:
    """Ein facility-loser Account darf 404 bekommen, niemals 500.

    ``get_scoped_object`` wirft ``ValueError``, wenn ``request.current_facility``
    fehlt — bewusst fail-loud gegen ungescopte Lookups (Refs #1346). Der
    super_admin traegt ``facility=None`` (Refs #867), die Middleware leert
    ``current_facility`` ausserdem bei archivierter Einrichtung (Refs #2067).
    Beide erreichen die Withdraw-Route wie jede andere angemeldete Rolle; der
    Rueckzug ist ohne Facility schlicht gegenstandslos.
    """

    def test_get_without_facility_is_404_not_500(self, client, own_request, super_admin_user):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))

        assert response.status_code == 404

    def test_post_without_facility_is_404_and_leaves_request_untouched(self, client, own_request, super_admin_user):
        client.force_login(super_admin_user)
        response = client.post(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))

        assert response.status_code == 404
        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.PENDING
        assert not _withdraw_audits(own_request).exists()


class TestWithdrawDialogCancelTarget:
    """„Abbrechen" muss auch nach einer parallelen Entscheidung erreichbar sein.

    Fuer eine reine Antragstellerin traegt ``can_access_deletion_requests``
    ausschliesslich der eigene offene Antrag (Refs #1736 B). Ein fest auf die
    Liste verdrahteter Abbrechen-Link endet deshalb im 403, wenn zwischen
    Dialog-Aufruf und Klick jemand anders entscheidet. Das Ziel folgt daher
    dem rollenbasierten (zustandsunabhaengigen) Anteil des Gates.
    """

    def test_requester_only_cancels_to_a_gate_independent_page(self, client, own_request, staff_user):
        assert staff_user.sees_all_deletion_requests is False
        client.force_login(staff_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))
        content = response.content.decode()

        # Auf das konkrete ``href`` prüfen, nicht auf den nackten Pfad: die
        # Withdraw-URL selbst beginnt mit ``/deletion-requests/`` und stünde
        # als Formular-Action in jeder Substring-Suche im Weg.
        assert f'href="{reverse("core:dashboard")}"' in content
        assert f'href="{reverse("core:deletion_request_list")}"' not in content

    def test_confirmer_keeps_the_list_as_cancel_target(self, client, own_request, staff_user, lead_user):
        """Wer die Liste rollenbasiert sieht, behaelt sie als Rueckweg."""
        own_lead_request = own_request
        own_lead_request.requested_by = lead_user
        own_lead_request.save(update_fields=["requested_by"])
        assert lead_user.sees_all_deletion_requests is True
        client.force_login(lead_user)
        response = client.get(reverse("core:deletion_withdraw", kwargs={"pk": own_lead_request.pk}))

        assert reverse("core:deletion_request_list") in response.content.decode()


class TestWithdrawMergeRequestAudit:
    """Der Rueckzug eines Merge-Antrags nennt beide Seiten — wie der Antrag.

    ``request_client_merge`` schreibt ``{"target_client", "merge_into"}``
    (services/client/merge.py). Ohne ``merge_into`` benennt die Rueckzugszeile
    nicht, WELCHE Zusammenfuehrung beendet wurde — beide UUIDs sind PII-frei.
    """

    def test_merge_withdraw_audit_names_both_sides(self, facility, staff_user, client_qualified, client_identified):
        from core.services.client.merge import request_client_merge

        dr = request_client_merge(client_qualified, client_identified, staff_user, reason="Doppelt angelegt")
        withdraw_deletion_request(dr, staff_user)

        entries = list(_withdraw_audits(dr))
        assert len(entries) == 1
        assert entries[0].detail == {
            "target_client": str(client_qualified.pk),
            "merge_into": str(client_identified.pk),
        }


class TestWithdrawRepeatedPostMessage:
    """Ein wiederholter POST meldet den eigenen Rueckzug, nicht „entschieden".

    Geht die Erfolgsantwort verloren und der Browser wiederholt den POST, ist
    der Antrag bereits WITHDRAWN. „bereits entschieden" waere sachlich falsch —
    entschieden hat hier niemand, zurueckgezogen hat sie selbst.
    """

    def test_second_post_reports_already_withdrawn(self, client, own_request, staff_user):
        client.force_login(staff_user)
        url = reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk})
        client.post(url)
        response = client.post(url)

        texte = [str(m) for m in _messages(response)]
        assert any("zurückgezogen" in t for t in texte), texte
        assert not any("entschieden" in t for t in texte), texte

    def test_post_on_approved_still_reports_decided(self, client, own_request, staff_user, lead_user):
        from core.services.events import approve_deletion

        approve_deletion(own_request, lead_user)
        client.force_login(staff_user)
        response = client.post(reverse("core:deletion_withdraw", kwargs={"pk": own_request.pk}))

        texte = [str(m) for m in _messages(response)]
        assert any("entschieden" in t for t in texte), texte


@pytest.mark.django_db(transaction=True)
class TestTrulyConcurrentWithdraw:
    """Der Sperr-Kontrakt, deterministisch gepinnt (Cross-Review Befund 4).

    Die Idempotenz-Tests oben laufen strikt nacheinander: der erste Aufruf ist
    fertig, bevor der zweite beginnt. Sie blieben deshalb auch mit einem
    schlichten ``.get()`` statt ``select_for_update()`` gruen — der eigentliche
    Kontrakt (zwei echte Verbindungen, eine Zeile) war nicht gepinnt.

    **Warum hier keine ``threading.Barrier`` steht:** eine symmetrische
    Barriere IM gesperrten Abschnitt kann den Lock nicht beweisen, sondern
    widerlegt sich selbst — greift die Sperre, kommt der zweite Thread dort
    nie an, und die Barriere laeuft zwangslaeufig in ihren Timeout. Eine
    Barriere DAVOR (erste Fassung dieses Tests) beweist ebenfalls nichts:
    danach darf Thread A komplett fertig werden, bevor B ueberhaupt liest —
    B saehe dann den Endstatus und der Test bliebe auch ohne Lock gruen.

    Stattdessen ein einseitiges Gate: Thread A oeffnet eine eigene Transaktion,
    ruft den Service und **haelt die Transaktion offen**. Erst danach startet
    Thread B. Damit ist die Reihenfolge in beiden Varianten deterministisch:

    * **mit** ``select_for_update()``: B blockiert schon beim Lesen, sieht nach
      dem Commit von A den Endstatus und wird sauber mit ``ValidationError``
      abgewiesen — genau ein Audit-Eintrag.
    * **ohne** den Lock: B liest die noch nicht committete Zeile unter READ
      COMMITTED als ``PENDING``, laeuft durch und schreibt einen ZWEITEN
      Audit-Eintrag. Der Test faellt — zuverlaessig, nicht nur meistens.

    Verwandtes Muster (dort liegt der Treffpunkt legitim VOR der Sperre, weil
    der Service dort erst spaeter sperrt):
    ``test_deletion_request_race_idempotency.TestTrulyConcurrentClientDeletionRequest``.
    """

    #: Wie lange der haltende Thread laengstens auf seine Freigabe wartet.
    HOLD_TIMEOUT = 30
    #: Wie lange der Haupt-Thread wartet, bis A im gesperrten Abschnitt steht.
    ENTER_TIMEOUT = 20
    #: Kurze Gnadenfrist, in der B nachweislich blockiert bleiben muss.
    CONTENDER_GRACE = 1.0
    #: Endgueltiger Join nach der Freigabe.
    JOIN_TIMEOUT = 30

    def _race_against_held_lock(self, hold_fn, contender_fn):
        """Faehrt ``contender_fn`` gegen eine von ``hold_fn`` offen gehaltene Sperre.

        Gibt ``{"hold": (kind, payload), "contender": (kind, payload)}`` zurueck.
        ``kind`` ist ``"ok"`` oder ``"err"`` (fachliche ``ValidationError``);
        jede andere Ausnahme wird als ``"boom"`` gemeldet, damit ein
        Infrastrukturfehler (z. B. ``OperationalError``) den Test rot macht,
        statt als fachliche Abweisung durchzugehen.
        """
        import threading

        from django.db import close_old_connections, transaction

        a_inside = threading.Event()
        release_a = threading.Event()
        contender_started = threading.Event()
        results = {}

        def _record(slot, fn):
            try:
                results[slot] = ("ok", fn())
            except ValidationError as exc:
                results[slot] = ("err", type(exc).__name__)
            except Exception as exc:  # noqa: BLE001 — Infrastrukturfehler sichtbar machen
                results[slot] = ("boom", f"{type(exc).__name__}: {exc}")

        def _hold():
            try:
                with transaction.atomic():
                    try:
                        _record("hold", hold_fn)
                    finally:
                        # Erst signalisieren, wenn der Service durch ist: ab
                        # hier haelt DIESE Transaktion die Zeile gesperrt.
                        a_inside.set()
                    if not release_a.wait(timeout=self.HOLD_TIMEOUT):
                        raise AssertionError("Freigabe des haltenden Threads blieb aus")
            except AssertionError as exc:
                results.setdefault("hold", ("boom", str(exc)))
            finally:
                # Auch im Fehlerfall darf der Haupt-Thread nicht haengen.
                a_inside.set()
                close_old_connections()

        def _contend():
            try:
                contender_started.set()
                _record("contender", contender_fn)
            finally:
                close_old_connections()

        # daemon=True: bliebe ein Thread wider Erwarten auf der Sperre haengen,
        # blockiert er weder DB-Teardown noch Prozessende — ein haengender Lauf
        # haelt in diesem Repo den globalen CI-flock und damit alles Folgende.
        hold_thread = threading.Thread(target=_hold, daemon=True)
        contender_thread = threading.Thread(target=_contend, daemon=True)
        try:
            hold_thread.start()
            assert a_inside.wait(timeout=self.ENTER_TIMEOUT), "Haltender Thread erreichte den Sperrpunkt nicht"
            contender_thread.start()
            assert contender_started.wait(timeout=self.ENTER_TIMEOUT), "Zweiter Thread startete nicht"
            # B muss jetzt blockieren — mit Lock bereits beim Lesen, ohne Lock
            # spaetestens beim Schreiben auf die von A gehaltene Zeile.
            contender_thread.join(timeout=self.CONTENDER_GRACE)
            assert contender_thread.is_alive(), "Zweiter Zugriff lief an der gehaltenen Sperre vorbei"
        finally:
            # Egal was oben schiefgeht: die Transaktion von A wird freigegeben,
            # sonst bliebe die Zeile bis zum Timeout gesperrt.
            release_a.set()

        hold_thread.join(timeout=self.JOIN_TIMEOUT)
        contender_thread.join(timeout=self.JOIN_TIMEOUT)
        assert not hold_thread.is_alive(), "Haltender Thread kam nicht zurueck"
        assert not contender_thread.is_alive(), "Deadlock im parallelen Rueckzug"
        assert set(results) == {"hold", "contender"}, results
        assert not [k for k, _p in results.values() if k == "boom"], results
        return results

    def _pending_request(self, facility, client_qualified, doc_type_contact, staff_user, notiz):
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 20, "notiz": notiz},
            created_by=staff_user,
        )
        return request_deletion(event, staff_user, f"{notiz} zurueckziehen")

    def test_two_parallel_withdraws_produce_exactly_one_audit_entry(
        self, facility, client_qualified, doc_type_contact, staff_user
    ):
        dr = self._pending_request(facility, client_qualified, doc_type_contact, staff_user, "Parallel")

        def _withdraw():
            return withdraw_deletion_request(DeletionRequest.objects.get(pk=dr.pk), staff_user).status

        results = self._race_against_held_lock(_withdraw, _withdraw)

        ausgaenge = [kind for kind, _payload in results.values()]
        assert sorted(ausgaenge) == ["err", "ok"], results
        assert results["hold"][0] == "ok", results
        assert results["contender"] == ("err", "ValidationError"), results
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.WITHDRAWN
        # Der eigentliche Nachweis: ohne den Zeilenlock schriebe der zweite
        # Durchlauf hier einen zweiten Eintrag.
        assert _withdraw_audits(dr).count() == 1

    def test_approve_against_held_withdraw_lock_loses_cleanly(
        self, facility, client_qualified, doc_type_contact, staff_user, lead_user
    ):
        """Die Genehmigung laeuft gegen den gehaltenen Rueckzug — und verliert sauber.

        Was dieser Test pinnt, praezise: er haengt am ``select_for_update()``
        in ``approve_deletion`` (Refs #1376), nicht an dem des Rueckzugs —
        empirisch geprueft, er wird rot, sobald DIESER Lock faellt. Den Lock
        des Rueckzugs pinnt der Test darueber. Zusammen decken beide die
        Kreuzung ab: keiner der beiden Endstati kann den anderen ueberschreiben.
        """
        from core.services.events import approve_deletion

        dr = self._pending_request(facility, client_qualified, doc_type_contact, staff_user, "Rennen")

        def _withdraw():
            return withdraw_deletion_request(DeletionRequest.objects.get(pk=dr.pk), staff_user).status

        def _approve():
            return approve_deletion(DeletionRequest.objects.get(pk=dr.pk), lead_user)

        results = self._race_against_held_lock(_withdraw, _approve)

        assert results["hold"][0] == "ok", results
        assert results["contender"] == ("err", "ValidationError"), results
        dr.refresh_from_db()
        # Der Rueckzug haelt die Sperre, die Genehmigung sieht danach den
        # Endstatus: genau ein Endzustand, genau eine Auditzeile.
        assert dr.status == DeletionRequest.Status.WITHDRAWN
        assert _withdraw_audits(dr).count() == 1
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.DELETION_APPROVED,
            target_type="DeletionRequest",
            target_id=str(dr.pk),
        ).exists(), "Die Genehmigung hat trotz gehaltener Sperre geschrieben"
