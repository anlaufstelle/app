"""Tests für den Idempotenz-Schutz beim Offline-Queue-Replay (F-09, Refs #1109).

Hintergrund (Security-Review 2026-06-14, Abschnitt 6.3):

Geht ein Server-Write durch, bricht aber die Verbindung *vor* Empfang der
Response ab, bleibt die Queue-Zeile in IndexedDB und wird beim nächsten
``online``-Event erneut gespielt. Für nicht-idempotente POSTs (Event-Create)
führt das zu einem **Doppel-Submit** — dasselbe Ereignis landet zweimal in
der Datenbank.

Gegenmaßnahme:

* **Client** (``offline-queue.js``): pro Queue-Eintrag eine UUID
  (``crypto.randomUUID()``), gespeichert mit dem Eintrag, gesendet als Header
  ``X-Idempotency-Key``. (Hier nicht getestet — kein JS-Runner; abgesichert
  durch Code-Review.)
* **Server** (``EventCreateView.post``): Dedup-Guard über Djangos
  Cache-Framework. Beim ersten Erfolg wird die erzeugte Event-PK unter dem
  Key gecacht (kurze TTL); ein Replay mit demselben Key liefert das *vorige*
  Ergebnis zurück, ohne ein zweites Event zu erzeugen.

Die Tests fixieren das **server-seitige** Verhalten — den Teil, der ohne den
Client-Header wirkungslos bliebe, aber mit ihm den Doppel-Submit verhindert.

Erweiterung (Refs #1329): ``WorkItemCreateView.post`` wertete den Header
bislang gar nicht aus (Doppel-Anlage bei Replay einer WorkItem-Erstellung),
und die Dedup-TTL war mit 24 h kürzer als die Offline-Bundle-Lease (48 h,
``BUNDLE_TTL_SECONDS``) — ein Replay kurz vor Lease-Ablauf hätte noch
dedupliziert werden müssen, aber die TTL war da bereits abgelaufen. Beide
Lücken deckt der Rest dieser Datei ab: WorkItem-Dedup
(``TestWorkItemCreateIdempotency``, 1:1 aus ``TestEventCreateIdempotency``
übertragen) und die TTL-Kopplungs-Invariante (``TestIdempotencyTtlCoupling``).
"""

from __future__ import annotations

import threading
import uuid

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, close_old_connections, connection
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.models import Event, WorkItem
from core.services.events.idempotency import IDEMPOTENCY_TTL_SECONDS, _cache_key
from core.services.system.offline import BUNDLE_TTL_SECONDS


def _create_payload(doc_type, client_obj):
    return {
        "document_type": str(doc_type.pk),
        "client": str(client_obj.pk),
        "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
        "dauer": "15",
        "notiz": "offline-create",
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    """Idempotenz-Guard lebt im Cache — vor und nach jedem Test leeren, damit
    Keys aus anderen Tests nicht durchschlagen."""
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
class TestEventCreateIdempotency:
    """Server-Dedup-Contract von :class:`EventCreateView.post` (F-09)."""

    def _url(self):
        return reverse("core:event_create")

    def test_replay_with_same_key_creates_only_one_event(self, client, staff_user, doc_type_contact, client_identified):
        """Zwei identische POSTs mit demselben ``X-Idempotency-Key`` dürfen nur
        EIN Event erzeugen — der zweite ist der Replay nach Verbindungsabbruch.
        """
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        payload = _create_payload(doc_type_contact, client_identified)

        r1 = client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r1.status_code == 302
        count_after_first = Event.objects.filter(document_type=doc_type_contact).count()
        assert count_after_first == 1

        # Replay derselben Queue-Zeile (gleicher Key) → KEIN zweites Event.
        r2 = client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r2.status_code == 302
        assert Event.objects.filter(document_type=doc_type_contact).count() == 1, (
            "Replay mit gleichem Idempotenz-Schlüssel darf kein Doppel-Event erzeugen"
        )

    def test_replay_redirects_to_same_event(self, client, staff_user, doc_type_contact, client_identified):
        """Der Replay muss auf dasselbe Event zeigen wie der Erst-Request, damit
        der Client ein konsistentes Ergebnis sieht."""
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        payload = _create_payload(doc_type_contact, client_identified)

        r1 = client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        r2 = client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r1["Location"] == r2["Location"]

    def test_different_key_creates_separate_events(self, client, staff_user, doc_type_contact, client_identified):
        """Unterschiedliche Idempotenz-Schlüssel sind unterschiedliche
        Aktionen → zwei Events (kein versehentliches Dedup)."""
        client.force_login(staff_user)
        payload = _create_payload(doc_type_contact, client_identified)

        client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        assert Event.objects.filter(document_type=doc_type_contact).count() == 2

    def test_no_key_keeps_legacy_behaviour(self, client, staff_user, doc_type_contact, client_identified):
        """Ohne Header bleibt das bestehende Verhalten: jeder POST erzeugt ein
        Event (Online-Direkt-Submit ohne Offline-Queue)."""
        client.force_login(staff_user)
        payload = _create_payload(doc_type_contact, client_identified)

        client.post(self._url(), payload)
        client.post(self._url(), payload)
        assert Event.objects.filter(document_type=doc_type_contact).count() == 2

    def test_failed_create_does_not_poison_key(self, client, staff_user, doc_type_contact, client_identified):
        """Schlägt der erste Versuch fehl (Validierungsfehler, kein Event), darf
        der Key NICHT als 'erledigt' gecacht werden — ein korrigierter Retry
        mit demselben Key muss das Event dann anlegen können."""
        client.force_login(staff_user)
        key = str(uuid.uuid4())

        # Invalider POST: occurred_at fehlt → Form ungültig, kein Event.
        bad = {
            "document_type": str(doc_type_contact.pk),
            "client": str(client_identified.pk),
            "dauer": "15",
            "notiz": "kaputt",
        }
        client.post(self._url(), bad, HTTP_X_IDEMPOTENCY_KEY=key)
        assert Event.objects.filter(document_type=doc_type_contact).count() == 0

        # Korrigierter Retry mit demselben Key muss durchgehen.
        good = _create_payload(doc_type_contact, client_identified)
        r = client.post(self._url(), good, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r.status_code == 302
        assert Event.objects.filter(document_type=doc_type_contact).count() == 1

    def test_same_key_different_body_returns_422_and_no_second_event(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        """N14 (#1424): derselbe Key mit ABWEICHENDEM Body ist ein
        Key-Kollisions-Kurzschluss — statt den gecachten Erfolg (Redirect
        aufs Original) zurueckzugeben, MUSS der Server 422 liefern (sichtbares
        Dead-Letter), und es darf KEIN zweites Event entstehen."""
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        payload = _create_payload(doc_type_contact, client_identified)

        r1 = client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r1.status_code == 302
        assert Event.objects.filter(document_type=doc_type_contact).count() == 1

        divergent = {**payload, "notiz": "GEAENDERTER-INHALT"}
        r2 = client.post(self._url(), divergent, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r2.status_code == 422, "Key-Treffer mit abweichendem Body muss 422 sein, kein gecachter Erfolg"
        assert "__all__" in r2.json()["errors"]
        assert Event.objects.filter(document_type=doc_type_contact).count() == 1

    def test_same_key_same_body_different_csrf_stays_idempotent(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        """Stale-CSRF-Retry (#1331/#1333): der CSRF-Token wird beim legitimen
        403-Retry erneuert und darf den Fingerprint NICHT beeinflussen — sonst
        braeche der konforme Replay als False-Positive-422."""
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        payload = _create_payload(doc_type_contact, client_identified)

        r1 = client.post(self._url(), {**payload, "csrfmiddlewaretoken": "tok-A"}, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r1.status_code == 302
        r2 = client.post(self._url(), {**payload, "csrfmiddlewaretoken": "tok-B"}, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r2.status_code == 302
        assert r1["Location"] == r2["Location"]
        assert Event.objects.filter(document_type=doc_type_contact).count() == 1

    def test_legacy_cache_value_hits_without_payload_check(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        """Abwaertskompatibilitaet (N14): ein alter Cache-Eintrag (nackter
        PK-String vor dem Payload-Hash, TTL bis 72 h nach Deploy) muss als
        Treffer OHNE Hash-Pruefung durchgehen — kein 500, kein False-Positive-
        422, auch bei abweichendem Body."""
        client.force_login(staff_user)
        payload = _create_payload(doc_type_contact, client_identified)
        r0 = client.post(self._url(), payload)  # ohne Key -> reales Ziel-Event
        assert r0.status_code == 302
        event = Event.objects.get()

        legacy_key = str(uuid.uuid4())
        cache.set(_cache_key("event_create", staff_user.pk, legacy_key), str(event.pk), IDEMPOTENCY_TTL_SECONDS)

        divergent = {**payload, "notiz": "voellig-anders"}
        r = client.post(self._url(), divergent, HTTP_X_IDEMPOTENCY_KEY=legacy_key)
        assert r.status_code == 302
        assert r["Location"] == reverse("core:event_detail", kwargs={"pk": event.pk})
        assert Event.objects.filter(document_type=doc_type_contact).count() == 1


@pytest.mark.django_db
class TestWorkItemCreateIdempotency:
    """Server-Dedup-Contract von :class:`WorkItemCreateView.post` (Refs #1329).

    1:1 aus :class:`TestEventCreateIdempotency` übertragen: ``WorkItemCreateView.post``
    las den ``X-Idempotency-Key`` bislang nicht — ein Offline-Replay einer
    WorkItem-Anlage (Verbindungsabbruch vor Empfang der Response) erzeugte ein
    Doppel-WorkItem. Reihenfolge und Assertions spiegeln die Event-Tests oben,
    nur das Ziel-Modell wechselt.
    """

    def _url(self):
        return reverse("core:workitem_create")

    def _payload(self, title="Offline-Aufgabe"):
        return {
            "item_type": "task",
            "title": title,
            "priority": "normal",
        }

    def test_replay_with_same_key_creates_only_one_workitem(self, client, staff_user):
        """Zwei identische POSTs mit demselben ``X-Idempotency-Key`` dürfen nur
        EIN WorkItem erzeugen — der zweite ist der Replay nach Verbindungsabbruch.
        """
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        payload = self._payload()

        r1 = client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r1.status_code == 302
        assert WorkItem.objects.filter(title=payload["title"]).count() == 1

        # Replay derselben Queue-Zeile (gleicher Key) → KEIN zweites WorkItem.
        r2 = client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r2.status_code == 302
        assert WorkItem.objects.filter(title=payload["title"]).count() == 1, (
            "Replay mit gleichem Idempotenz-Schlüssel darf kein Doppel-WorkItem erzeugen"
        )

    def test_replay_redirects_to_same_target(self, client, staff_user):
        """Der Replay muss auf dasselbe Ziel zeigen wie der Erst-Request, damit
        der Client ein konsistentes Ergebnis sieht — heutiger Success-Redirect
        der View (bei WorkItem die feste Inbox-URL statt eines Detail-Links,
        da ``WorkItemCreateView.post`` schon vor #1329 nicht pk-spezifisch
        umleitete)."""
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        payload = self._payload()

        r1 = client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        r2 = client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r1["Location"] == r2["Location"]

    def test_different_key_creates_separate_workitems(self, client, staff_user):
        """Unterschiedliche Idempotenz-Schlüssel sind unterschiedliche
        Aktionen → zwei WorkItems (kein versehentliches Dedup)."""
        client.force_login(staff_user)
        payload = self._payload()

        client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        client.post(self._url(), payload, HTTP_X_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        assert WorkItem.objects.filter(title=payload["title"]).count() == 2

    def test_no_key_keeps_legacy_behaviour(self, client, staff_user):
        """Ohne Header bleibt das bestehende Verhalten: jeder POST erzeugt ein
        WorkItem (Online-Direkt-Submit ohne Offline-Queue)."""
        client.force_login(staff_user)
        payload = self._payload()

        client.post(self._url(), payload)
        client.post(self._url(), payload)
        assert WorkItem.objects.filter(title=payload["title"]).count() == 2

    def test_failed_create_does_not_poison_key(self, client, staff_user):
        """Schlägt der erste Versuch fehl (Validierungsfehler, kein WorkItem),
        darf der Key NICHT als 'erledigt' gecacht werden — ein korrigierter
        Retry mit demselben Key muss das WorkItem dann anlegen können."""
        client.force_login(staff_user)
        key = str(uuid.uuid4())

        # Invalider POST: item_type fehlt → Form ungültig, kein WorkItem.
        bad = {"title": "kaputt", "priority": "normal"}
        client.post(self._url(), bad, HTTP_X_IDEMPOTENCY_KEY=key)
        assert WorkItem.objects.filter(title="kaputt").count() == 0

        # Korrigierter Retry mit demselben Key muss durchgehen.
        good = self._payload(title="kaputt")
        r = client.post(self._url(), good, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r.status_code == 302
        assert WorkItem.objects.filter(title="kaputt").count() == 1

    def test_same_key_different_body_returns_422_and_no_second_workitem(self, client, staff_user):
        """N14 (#1424): analog zum Event-Pfad — Key-Treffer mit abweichendem
        Body -> 422 (Dead-Letter), KEIN zweites WorkItem."""
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        r1 = client.post(self._url(), self._payload(title="Original"), HTTP_X_IDEMPOTENCY_KEY=key)
        assert r1.status_code == 302
        assert WorkItem.objects.count() == 1

        r2 = client.post(self._url(), self._payload(title="Abweichend"), HTTP_X_IDEMPOTENCY_KEY=key)
        assert r2.status_code == 422, "Key-Treffer mit abweichendem Body muss 422 sein, kein gecachter Erfolg"
        assert "__all__" in r2.json()["errors"]
        assert WorkItem.objects.count() == 1

    def test_same_key_same_body_different_csrf_stays_idempotent(self, client, staff_user):
        """Stale-CSRF-Retry (#1331/#1333): CSRF-Token darf den Fingerprint nicht
        beeinflussen — konformer Replay bleibt idempotent (Redirect)."""
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        payload = self._payload()
        r1 = client.post(self._url(), {**payload, "csrfmiddlewaretoken": "tok-A"}, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r1.status_code == 302
        r2 = client.post(self._url(), {**payload, "csrfmiddlewaretoken": "tok-B"}, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r2.status_code == 302
        assert r1["Location"] == r2["Location"]
        assert WorkItem.objects.count() == 1

    def test_legacy_cache_value_hits_without_payload_check(self, client, staff_user):
        """Abwaertskompatibilitaet (N14): alter nackter PK-String im Cache ->
        Treffer ohne Hash-Pruefung (kein 500, kein False-Positive-422)."""
        client.force_login(staff_user)
        r0 = client.post(self._url(), self._payload(title="Legacy-Basis"))
        assert r0.status_code == 302
        workitem = WorkItem.objects.get()

        legacy_key = str(uuid.uuid4())
        cache.set(_cache_key("workitem_create", staff_user.pk, legacy_key), str(workitem.pk), IDEMPOTENCY_TTL_SECONDS)

        r = client.post(self._url(), self._payload(title="voellig-anders"), HTTP_X_IDEMPOTENCY_KEY=legacy_key)
        assert r.status_code == 302
        assert r["Location"] == reverse("core:workitem_inbox")
        assert WorkItem.objects.count() == 1


class TestPayloadFingerprint:
    """Unit-Contract von :func:`payload_fingerprint` (N14, Refs #1424):
    reihenfolge-unabhaengig, CSRF-agnostisch, datei-sensitiv."""

    def _req(self, data, files=None):
        return RequestFactory().post("/x", {**data, **(files or {})})

    def test_order_independent(self):
        from core.services.events.idempotency import payload_fingerprint

        a = payload_fingerprint(self._req({"a": "1", "b": "2"}))
        b = payload_fingerprint(self._req({"b": "2", "a": "1"}))
        assert a == b

    def test_csrf_token_excluded(self):
        from core.services.events.idempotency import payload_fingerprint

        a = payload_fingerprint(self._req({"a": "1", "csrfmiddlewaretoken": "AAA"}))
        b = payload_fingerprint(self._req({"a": "1", "csrfmiddlewaretoken": "BBB"}))
        c = payload_fingerprint(self._req({"a": "1"}))
        assert a == b == c

    def test_body_change_changes_hash(self):
        from core.services.events.idempotency import payload_fingerprint

        assert payload_fingerprint(self._req({"a": "1"})) != payload_fingerprint(self._req({"a": "2"}))

    def test_file_sensitive(self):
        from core.services.events.idempotency import payload_fingerprint

        no_file = payload_fingerprint(self._req({"a": "1"}))
        with_file = payload_fingerprint(self._req({"a": "1"}, {"anhang": SimpleUploadedFile("f.txt", b"data")}))
        other_name = payload_fingerprint(self._req({"a": "1"}, {"anhang": SimpleUploadedFile("g.txt", b"data")}))
        assert no_file != with_file
        assert with_file != other_name


class TestIdempotencyTtlCoupling:
    """TTL-Kopplungs-Invariante (Refs #1329).

    Der Dedup-Cache muss mindestens so lange leben wie die Offline-Bundle-
    Lease — sonst dedupliziert ein Replay kurz vor Lease-Ablauf nicht mehr,
    weil der Idempotenz-Key schon aus dem Cache gefallen ist, bevor der
    Client seinen letzten Retry-Versuch unternimmt. Reiner Konstanten-
    Vergleich, keine DB nötig.
    """

    def test_ttl_is_72_hours_and_covers_bundle_lease(self):
        assert IDEMPOTENCY_TTL_SECONDS == 72 * 3600
        assert IDEMPOTENCY_TTL_SECONDS > BUNDLE_TTL_SECONDS


class TestNormalizeIdempotencyKey:
    """R6/N14-Vorgriff: nur ^[A-Za-z0-9_-]{1,64}$ wird akzeptiert, sonst None."""

    @pytest.mark.parametrize(
        "raw,expected_kept",
        [
            (str(uuid.UUID(int=1)), True),
            ("a" * 64, True),
            ("A-b_0", True),
            (None, False),
            ("", False),
            ("a" * 65, False),
            ("key with spaces", False),
            ("=cmd|injection", False),
            ("umlaut-ä", False),
        ],
    )
    def test_normalize(self, raw, expected_kept):
        from core.services.events.idempotency import normalize_idempotency_key

        result = normalize_idempotency_key(raw)
        assert (result == raw) if expected_kept else (result is None)


@pytest.mark.django_db
class TestIdempotencyDbBackstop:
    """R5/R6: Cache-Verlust (Eviction/Worker-Neustart) darf den Dedup nicht
    aufheben — der DB-Unique-Constraint ist der persistente Backstop."""

    def test_event_replay_after_cache_loss_creates_no_duplicate(
        self, client, staff_user, doc_type_contact, client_identified
    ):
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        payload = _create_payload(doc_type_contact, client_identified)
        r1 = client.post(reverse("core:event_create"), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r1.status_code == 302
        cache.clear()  # simuliert Eviction / Prozess-Neustart (R5)
        r2 = client.post(reverse("core:event_create"), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r2.status_code == 302
        assert Event.objects.count() == 1
        assert r1["Location"] == r2["Location"]

    def test_workitem_replay_after_cache_loss_creates_no_duplicate(self, client, staff_user):
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        payload = {"item_type": "task", "title": "Backstop", "priority": "normal"}
        client.post(reverse("core:workitem_create"), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        cache.clear()
        client.post(reverse("core:workitem_create"), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert WorkItem.objects.count() == 1

    def test_db_constraint_blocks_concurrent_duplicate(self, staff_user, doc_type_contact, facility):
        """R6: zwei Replays im get-then-set-Fenster passieren beide den
        Cache-Check — das zweite INSERT muss am Unique-Constraint scheitern."""
        from django.db import IntegrityError
        from django.utils import timezone

        key = str(uuid.uuid4())
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
            idempotency_key=key,
        )
        with pytest.raises(IntegrityError):
            Event.objects.create(
                facility=facility,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={},
                is_anonymous=True,
                created_by=staff_user,
                idempotency_key=key,
            )

    def test_malformed_key_is_ignored_not_500(self, client, staff_user, doc_type_contact, client_identified):
        """Ueberlange/binaere Keys degradieren zum Legacy-Verhalten (kein Dedup, kein 500)."""
        client.force_login(staff_user)
        bad_key = "x" * 200
        payload = _create_payload(doc_type_contact, client_identified)
        r1 = client.post(reverse("core:event_create"), payload, HTTP_X_IDEMPOTENCY_KEY=bad_key)
        r2 = client.post(reverse("core:event_create"), payload, HTTP_X_IDEMPOTENCY_KEY=bad_key)
        assert r1.status_code == 302 and r2.status_code == 302
        assert Event.objects.count() == 2

    def test_same_key_different_users_both_succeed(self, staff_user, lead_user, doc_type_contact, facility):
        """T1: Der Unique-Constraint ist bewusst auf (created_by, idempotency_key)
        skopiert, nicht auf idempotency_key allein. Verwenden zwei VERSCHIEDENE
        Nutzer zufaellig denselben X-Idempotency-Key (z.B. Client-seitige UUID-
        Kollision oder zwei unabhaengige Offline-Queues), darf der Key des einen
        Nutzers den Request des anderen weder blockieren (IntegrityError) noch
        dessen Ergebnis leaken (Redirect/Event des fremden Nutzers zurueckgeben).
        Direkt auf ORM-Ebene geprueft: beide Zeilen muessen mit demselben Key,
        aber unterschiedlichem created_by, unabhaengig voneinander existieren.
        """
        shared_key = str(uuid.uuid4())

        event_staff = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
            idempotency_key=shared_key,
        )
        event_lead = Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=lead_user,
            idempotency_key=shared_key,
        )

        assert Event.objects.count() == 2
        assert event_staff.pk != event_lead.pk
        assert Event.objects.filter(created_by=staff_user, idempotency_key=shared_key).count() == 1
        assert Event.objects.filter(created_by=lead_user, idempotency_key=shared_key).count() == 1


@pytest.mark.django_db(transaction=True)
class TestIntegrityErrorScopedToConstraint:
    """R6-Haertung (Refs #1443): der ``IntegrityError``-Fang in den Create-Views
    darf NUR den Idempotenz-Unique-Constraint (``idem_key_per_user_uniq``, deckt
    ``event_…`` wie ``workitem_…``) als Duplikat behandeln. Ein FACHFREMDER
    IntegrityError im selben Replay-Request wuerde sonst als Duplikat maskiert
    (stiller 302 statt 500), sobald zufaellig eine passende (user, key)-Zeile
    existiert — der eigentliche Fehler bliebe unsichtbar.

    Deterministisch nachgestellt: der Pre-Check laeuft leer, dann committet ein
    konkurrierender Insert (eigener Thread + eigene DB-Connection) die
    (user, key)-Zeile im get-then-set-Fenster, waehrend der eigene Insert (hier:
    gepatchtes ``create_event``/``create_workitem``) einen IntegrityError wirft.
    Traegt dessen Meldung den Constraint-Namen -> Duplikat-Redirect; ist sie
    fachfremd -> Propagation.
    """

    def setup_method(self):
        # Der Race braucht eine zweite, echt committende Connection — nur unter
        # PostgreSQL (SQLite/Autocommit-in-memory verhaelt sich anders).
        if connection.vendor != "postgresql":
            pytest.skip("Race-Test erfordert PostgreSQL (eigene Thread-Connection)")

    @staticmethod
    def _insert_event_in_thread(*, facility, doc_type, user, key, sink=None):
        def _worker():
            try:
                ev = Event.objects.create(
                    facility=facility,
                    document_type=doc_type,
                    occurred_at=timezone.now(),
                    data_json={},
                    is_anonymous=True,
                    created_by=user,
                    idempotency_key=key,
                )
                if sink is not None:
                    sink["pk"] = ev.pk
            finally:
                close_old_connections()

        t = threading.Thread(target=_worker)
        t.start()
        t.join(timeout=10)

    @staticmethod
    def _insert_workitem_in_thread(*, facility, user, title, key, sink=None):
        def _worker():
            try:
                wi = WorkItem.objects.create(
                    facility=facility,
                    created_by=user,
                    item_type=WorkItem.ItemType.TASK,
                    status=WorkItem.Status.OPEN,
                    priority=WorkItem.Priority.NORMAL,
                    title=title,
                    idempotency_key=key,
                )
                if sink is not None:
                    sink["pk"] = wi.pk
            finally:
                close_old_connections()

        t = threading.Thread(target=_worker)
        t.start()
        t.join(timeout=10)

    def test_event_foreign_integrity_error_propagates_not_masked(
        self, client, staff_user, doc_type_contact, client_identified, facility, monkeypatch
    ):
        """Fachfremder IntegrityError im Event-Create -> Propagation, kein 302."""
        import core.views.events as events_view

        client.force_login(staff_user)
        key = str(uuid.uuid4())

        def _raise_foreign(*args, **kwargs):
            self._insert_event_in_thread(facility=facility, doc_type=doc_type_contact, user=staff_user, key=key)
            raise IntegrityError("FOREIGN KEY constraint violated (fachfremd)")

        monkeypatch.setattr(events_view, "create_event", _raise_foreign)

        payload = _create_payload(doc_type_contact, client_identified)
        with pytest.raises(IntegrityError):
            client.post(reverse("core:event_create"), payload, HTTP_X_IDEMPOTENCY_KEY=key)

    def test_event_constraint_integrity_error_still_redirects(
        self, client, staff_user, doc_type_contact, client_identified, facility, monkeypatch
    ):
        """Echter Idempotenz-Constraint (Name in der Meldung) -> weiterhin
        Duplikat-Redirect auf das im Race angelegte Event (Regressions-Guard)."""
        import core.views.events as events_view

        client.force_login(staff_user)
        key = str(uuid.uuid4())
        sink: dict = {}

        def _raise_constraint(*args, **kwargs):
            self._insert_event_in_thread(
                facility=facility, doc_type=doc_type_contact, user=staff_user, key=key, sink=sink
            )
            raise IntegrityError('duplicate key value violates unique constraint "event_idem_key_per_user_uniq"')

        monkeypatch.setattr(events_view, "create_event", _raise_constraint)

        payload = _create_payload(doc_type_contact, client_identified)
        r = client.post(reverse("core:event_create"), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r.status_code == 302
        assert r["Location"] == reverse("core:event_detail", kwargs={"pk": sink["pk"]})
        assert Event.objects.count() == 1

    def test_workitem_foreign_integrity_error_propagates_not_masked(self, client, staff_user, facility, monkeypatch):
        """Fachfremder IntegrityError im WorkItem-Create -> Propagation, kein 302."""
        import core.views.workitem_actions as wi_view

        client.force_login(staff_user)
        key = str(uuid.uuid4())
        title = "Race-Aufgabe"

        def _raise_foreign(*args, **kwargs):
            self._insert_workitem_in_thread(facility=facility, user=staff_user, title=title, key=key)
            raise IntegrityError("FOREIGN KEY constraint violated (fachfremd)")

        monkeypatch.setattr(wi_view, "create_workitem", _raise_foreign)

        payload = {"item_type": "task", "title": title, "priority": "normal"}
        with pytest.raises(IntegrityError):
            client.post(reverse("core:workitem_create"), payload, HTTP_X_IDEMPOTENCY_KEY=key)

    def test_workitem_constraint_integrity_error_still_redirects(self, client, staff_user, facility, monkeypatch):
        """Echter Idempotenz-Constraint -> weiterhin Duplikat-Redirect (Regressions-Guard)."""
        import core.views.workitem_actions as wi_view

        client.force_login(staff_user)
        key = str(uuid.uuid4())
        title = "Race-Aufgabe"

        def _raise_constraint(*args, **kwargs):
            self._insert_workitem_in_thread(facility=facility, user=staff_user, title=title, key=key)
            raise IntegrityError('duplicate key value violates unique constraint "workitem_idem_key_per_user_uniq"')

        monkeypatch.setattr(wi_view, "create_workitem", _raise_constraint)

        payload = {"item_type": "task", "title": title, "priority": "normal"}
        r = client.post(reverse("core:workitem_create"), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r.status_code == 302
        assert r["Location"] == reverse("core:workitem_inbox")
        assert WorkItem.objects.count() == 1


@pytest.mark.django_db
class TestWorkItemStatusIdempotency:
    """Server-Dedup-Contract von :class:`WorkItemStatusUpdateView.post` (Refs #1419).

    Der Status-Replay der generischen Offline-Queue braucht denselben
    ``X-Idempotency-Key``-Schutz wie Create (Scope ``workitem_status``):
    Bricht die Verbindung nach erfolgreichem Server-Write, aber vor Empfang
    der Response ab, spielt der Client dieselbe Queue-Zeile erneut. Ohne
    Dedup würde der zweite Replay einen ZWISCHENZEITLICHEN Statuswechsel
    (z.B. Kollegin hat wiedereröffnet) still überschreiben — der
    Versions-Token hilft dann nicht, weil der Erst-Replay selbst das
    ``updated_at`` fortgeschrieben hat, das der Zweit-Replay als 409 sähe;
    schlimmer noch: OHNE Cache-Hit landet der Zweit-Replay als
    Konflikt in der M8-Liste, obwohl die Aktion längst angewendet ist.
    Der Cache-Hit liefert stattdessen die Erfolgsform des Originals.
    """

    def _url(self, workitem):
        return reverse("core:workitem_status_update", kwargs={"pk": workitem.pk})

    def _fresh_token(self, workitem):
        workitem.refresh_from_db()
        return workitem.updated_at.isoformat()

    def test_replay_with_same_key_applies_transition_only_once(self, client, staff_user, sample_workitem):
        """Der Zweit-Replay mit demselben Key wendet die Transition NICHT
        erneut an: ein zwischenzeitlicher Statuswechsel (Reopen durch
        Kollegin) bleibt erhalten (ROT vor #1419)."""
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        payload = {"status": "done", "expected_updated_at": self._fresh_token(sample_workitem)}

        r1 = client.post(self._url(sample_workitem), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r1.status_code == 302
        sample_workitem.refresh_from_db()
        assert sample_workitem.status == "done"

        # Kollegin eröffnet die Aufgabe wieder (anderer Kontext, kein Key).
        sample_workitem.status = "open"
        sample_workitem.completed_at = None
        sample_workitem.save()

        # Replay derselben Queue-Zeile (gleicher Key, gleicher Body).
        r2 = client.post(self._url(sample_workitem), payload, HTTP_X_IDEMPOTENCY_KEY=key)
        assert r2.status_code == 302
        sample_workitem.refresh_from_db()
        assert sample_workitem.status == "open", (
            "Replay mit gleichem Idempotenz-Schlüssel darf den zwischenzeitlichen Reopen nicht überschreiben"
        )

    def test_replay_hit_returns_htmx_success_shape(self, client, staff_user, sample_workitem):
        """Für einen HX-Record liefert der Cache-Hit dieselbe Erfolgsform wie
        das Original (200-Partial) — die generische Queue löscht die Zeile
        dann als synchronisiert (ROT vor #1419)."""
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        payload = {"status": "in_progress", "expected_updated_at": self._fresh_token(sample_workitem)}

        r1 = client.post(self._url(sample_workitem), payload, HTTP_X_IDEMPOTENCY_KEY=key, HTTP_HX_REQUEST="true")
        assert r1.status_code == 200
        r2 = client.post(self._url(sample_workitem), payload, HTTP_X_IDEMPOTENCY_KEY=key, HTTP_HX_REQUEST="true")
        assert r2.status_code == 200
        assert r2["Content-Type"].startswith("text/html")

    def test_replay_hit_with_hide_returns_empty_response(self, client, staff_user, sample_workitem):
        """``hide``-Records (Inbox-Karte ausblenden) bekommen auch beim
        Cache-Hit die leere 200-Antwort des Originals."""
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        payload = {
            "status": "done",
            "hide": "1",
            "expected_updated_at": self._fresh_token(sample_workitem),
        }
        r1 = client.post(self._url(sample_workitem), payload, HTTP_X_IDEMPOTENCY_KEY=key, HTTP_HX_REQUEST="true")
        assert r1.status_code == 200
        assert r1.content == b""
        r2 = client.post(self._url(sample_workitem), payload, HTTP_X_IDEMPOTENCY_KEY=key, HTTP_HX_REQUEST="true")
        assert r2.status_code == 200
        assert r2.content == b""

    def test_same_key_with_different_payload_returns_422(self, client, staff_user, sample_workitem):
        """N14-Grenze (analog Create): derselbe Key mit ABWEICHENDEM Body ist
        eine Key-Kollision → 422 (Dead-Letter), kein gecachter Erfolg und
        keine zweite Transition."""
        client.force_login(staff_user)
        key = str(uuid.uuid4())
        token = self._fresh_token(sample_workitem)

        r1 = client.post(
            self._url(sample_workitem),
            {"status": "done", "expected_updated_at": token},
            HTTP_X_IDEMPOTENCY_KEY=key,
        )
        assert r1.status_code == 302
        r2 = client.post(
            self._url(sample_workitem),
            {"status": "dismissed", "expected_updated_at": token},
            HTTP_X_IDEMPOTENCY_KEY=key,
        )
        assert r2.status_code == 422
        assert r2.json()["error"] == "invalid"
        sample_workitem.refresh_from_db()
        assert sample_workitem.status == "done"

    def test_without_key_each_post_applies(self, client, staff_user, sample_workitem):
        """Ohne Key bleibt das bisherige Verhalten: jeder POST ist eine
        eigenständige Aktion (Online-Pfad, kein Replay)."""
        client.force_login(staff_user)
        r1 = client.post(self._url(sample_workitem), {"status": "done"})
        assert r1.status_code == 302
        r2 = client.post(self._url(sample_workitem), {"status": "open"})
        assert r2.status_code == 302
        sample_workitem.refresh_from_db()
        assert sample_workitem.status == "open"
