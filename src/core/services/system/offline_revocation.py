"""Schreib-/Lese-/Prune-Pfad der Offline-Revoke-Liste (Refs #1760).

Gegenstueck zu :mod:`core.services.system.offline` (Bundle-Erzeugung): dieses
Modul fuehrt die personen-genaue **Ent**wertung schon ausgelieferter Bundles.
Hintergrund und Datenmodell: :mod:`core.models.offline_revocation`.

Zusammenspiel mit den beiden bestehenden Invalidierungskanaelen:

1. :func:`core.signals.offline_invalidation.revoke_offline_access` — Achse
   *Nutzer:in*: Rollenwechsel/Deaktivierung/Lockout entwerten den gesamten
   Offline-Bestand DIESES Accounts (Salt-Rotation + Session-Flush).
2. ``revalidateCachedClient`` (``offline-store.js``) — Achse *Existenz*: ein
   401/404/410 des Bundle-Endpoints purgt den lokalen Bestand der Person.
3. **Neu, Achse Person:** diese Revoke-Liste. Sie greift genau dort, wo (2)
   blind ist — die anonymisierte Person existiert weiter (200) und ihr lokaler
   Klartext wird bei offenen ungesyncten Aenderungen sogar bewusst NICHT
   ueberschrieben (``"skipped"``).

Der Client teilt sich mit (2) die Loesch-Primitive
(``removeOfflineClient(pk, {force: true})``) — kein zweiter Purge-Pfad.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.models import OfflineClientRevocation
from core.services.system.offline import BUNDLE_TTL_SECONDS

logger = logging.getLogger(__name__)

# Wie lange ein Revoke-Eintrag vorgehalten wird: das doppelte Bundle-TTL.
#
# Begruendung: ein Bundle ist hoechstens ``BUNDLE_TTL_SECONDS`` lang gueltig.
# Ein Geraet, das laenger als diese Frist offline war, rendert den Bestand
# ohnehin nicht mehr (TTL-Purge, ``purgeExpired``) — der Revoke-Eintrag hat
# fuer es keinen Wert mehr. Die einfache TTL waere trotzdem zu knapp: das
# Geraet kann exakt am Fristende wieder online gehen und muss den Eintrag dann
# noch sehen. Der doppelte Abstand deckt diese Ueberlappung plus Uhren-Drift
# zwischen Server und Geraet ab, ohne die Liste unbegrenzt wachsen zu lassen.
# Danach bleibt die 48-h-TTL die letzte Verteidigungslinie fuer ein dauerhaft
# offline gebliebenes Geraet (dokumentiert in ``docs/security-notes.md``).
OFFLINE_REVOCATION_RETENTION_SECONDS = 2 * BUNDLE_TTL_SECONDS

# Obergrenze der pro Abruf ausgelieferten Eintraege. Der Poll laeuft bei jedem
# Sync; eine unbegrenzte Antwort waere im Massen-Anonymisierungsfall (naechtlicher
# Retention-Lauf) eine unnoetige Bandbreiten-Spitze. Sortiert wird nach
# ``-revoked_at``, der Deckel schneidet also die AELTESTEN ab — die haben fuer
# ein Geraet im TTL-Fenster den geringsten Wert (ihr Bundle ist bereits naeher
# am eigenen TTL-Ablauf). Wird der Deckel erreicht, wird laut geloggt.
OFFLINE_REVOCATION_MAX_ENTRIES = 5000


def _cutoff(now=None):
    now = now or timezone.now()
    return now - timedelta(seconds=OFFLINE_REVOCATION_RETENTION_SECONDS)


@transaction.atomic
def revoke_offline_client_bundles(client) -> None:
    """Widerrufe alle ausgelieferten Offline-Bundles zu *client*.

    Wird aus dem Anonymisierungs-Service heraus aufgerufen (beide Einstiege:
    Hard-Pfad und K-Anon-Bridge) und laeuft damit in DERSELBEN Transaktion wie
    die Redaktions-Kaskade — ein Rollback der Anonymisierung nimmt den Widerruf
    mit, und umgekehrt kann kein anonymisierter Datensatz ohne Widerruf
    entstehen.

    Upsert statt Insert: eine zweite Anonymisierung derselben Person (etwa
    Hard-Pfad nach vorherigem K-Anon-Lauf) frischt nur den Zeitstempel auf. Das
    verlaengert das Auslieferungsfenster fuer Geraete, die den ersten Eintrag
    verpasst haben — fail-safe in die richtige Richtung.

    **Kein eigenes Audit-Event.** Die Aktion, die den Widerruf ausloest, ist
    bereits vollstaendig protokolliert (``CLIENT_ANONYMIZED`` mit ``trigger``
    bzw. die ``client_anonymized``/``client_k_anonymized``-Retention-Entscheidung).
    Ein zweiter Eintrag traege dieselbe Ziel-ID und keinen zusaetzlichen
    Informationsgehalt — und das Audit-Log ist append-only, also der teuerste
    Ort fuer Redundanz.
    """
    OfflineClientRevocation.objects.update_or_create(
        facility_id=client.facility_id,
        client_pk=client.pk,
        defaults={"revoked_at": timezone.now()},
    )


def list_offline_client_revocations(facility, now=None) -> list[str]:
    """Die aktuell gueltigen Widerrufe einer Einrichtung als UUID-Strings.

    Gefiltert auf das Retention-Fenster — auch wenn der naechtliche Prune noch
    nicht lief, liefert der Endpoint nie Eintraege, die fuer kein Geraet mehr
    relevant sein koennen (Datensparsamkeit am Auslieferungspunkt).
    """
    qs = (
        OfflineClientRevocation.objects.for_facility(facility)
        .filter(revoked_at__gte=_cutoff(now))
        .order_by("-revoked_at")
        .values_list("client_pk", flat=True)
    )
    entries = [str(pk) for pk in qs[: OFFLINE_REVOCATION_MAX_ENTRIES + 1]]
    if len(entries) > OFFLINE_REVOCATION_MAX_ENTRIES:
        logger.error(
            "Offline-Revoke-Liste: Obergrenze (%d) fuer Facility %s erreicht — "
            "aelteste Eintraege nicht ausgeliefert (Bundle-TTL bleibt Backstop).",
            OFFLINE_REVOCATION_MAX_ENTRIES,
            facility.pk,
        )
        entries = entries[:OFFLINE_REVOCATION_MAX_ENTRIES]
    return entries


def prune_offline_client_revocations(facility, now=None) -> int:
    """Loesche abgelaufene Widerrufe einer Einrichtung; gibt die Anzahl zurueck.

    Aufruf aus dem naechtlichen ``enforce_retention``-Lauf. Der Eintrag selbst
    ist klartext-frei, aber er bleibt ein pseudonymer Bezug auf eine bereits
    anonymisierte Person — er darf nicht laenger liegen, als er einen Zweck hat
    (Art. 5 Abs. 1 lit. e).
    """
    deleted, _ = OfflineClientRevocation.objects.for_facility(facility).filter(revoked_at__lt=_cutoff(now)).delete()
    return deleted
