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

import uuid

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from core.models import Event, WorkItem
from core.services.events.idempotency import IDEMPOTENCY_TTL_SECONDS
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
