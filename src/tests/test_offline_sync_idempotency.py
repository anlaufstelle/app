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
"""

from __future__ import annotations

import uuid

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from core.models import Event


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
