"""Service layer for Episode updates (Refs #1959).

Bis hierher hatte die Episode gar keinen Service: :class:`EpisodeUpdateView`
rief ``form.save()`` direkt, und eine Bearbeitung hinterliess nirgends eine
Spur — weder im AuditLog noch in einer ``EventHistory`` (die gibt es nur fuer
Ereignisse) noch im Aktivitaets-Feed. Das Verarbeitungsverzeichnis musste das
als TOM ausdruecklich einraeumen (``services/compliance/vvt.py``, ADR-043).

**Ein Service ist hier zwingend, nicht nur Konvention.** ``EpisodeForm`` ist
ein ``ModelForm(instance=…)``; ``_post_clean()`` hat die Instanz bereits
mutiert, wenn die View sie zu Gesicht bekommt. Ein Diff *nach* ``form.save()``
saehe ``neu == neu`` und schriebe nie eine Zeile (Threat-Model TB6,
Repudiation). Der Diff laeuft deshalb ueber :func:`changed_fields_vs_db` gegen
den DB-Stand — dasselbe Muster wie ``update_case``/``update_client``.

Anders als ``update_case`` gibt es hier **keinen** Versionskonflikt-Check:
:class:`~core.models.episode.Episode` fuehrt kein ``updated_at``, es gibt also
keinen Vergleichsstempel fuer Optimistic Locking.
"""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import AuditLog, Episode
from core.services.audit import audit_event
from core.services.audit.diff import changed_fields_vs_db
from core.services.case.state_contract import CASE_WRITE_GONE_MESSAGE, lock_case_for_write

logger = logging.getLogger(__name__)

#: Allowlist (Muster ``update_case``, Refs #734/#1786): verhindert
#: Mass-Assignment durch versehentlich durchgereichte Felder — insbesondere
#: ``case`` (ein Umhaengen der Episode in einen fremden Fall) und die
#: Soft-Delete-Marker.
_UPDATABLE_FIELDS = frozenset({"title", "description", "started_at", "ended_at"})


def _episode_facility_id(episode):
    """Einrichtung der Akte einer Episode — ungesperrt gelesen.

    Die Einrichtung einer Akte ist unveraenderlich; der Vergleich mit der
    Einrichtung der handelnden Person braucht also keinen frischen Stand und
    sperrt nichts ueber die Mandantengrenze hinweg. ``None`` (Zeile nicht
    mehr vorhanden) ueberlaesst die Absage dem Zustandsvertrag.
    """
    return Episode.objects.filter(pk=episode.pk).values_list("case__facility_id", flat=True).first()


def _lock_episode_for_write(episode) -> Episode:
    """Die Episode fuer einen Schreibvorgang — Person -> Fall -> Episode, frisch und gesperrt.

    Person und Fall laufen durch den Zustandsvertrag
    (:func:`~core.services.case.state_contract.lock_case_for_write`): darin
    das Art.-18-Gate, der Widerruf der Person und Papierkorb/Tilgung des
    Falls. Danach die Episodenzeile, der Fallbezug steht IN der Bedingung.
    Fehlt sie oder liegt sie im Papierkorb, ist sie nicht mehr die Episode,
    auf die sich der Aufruf bezieht — Absage wie bei einer verschwundenen
    Fallzeile.
    """
    lock_case_for_write(episode.case)
    locked = Episode.objects.filter(pk=episode.pk, case_id=episode.case_id).select_for_update().first()
    if locked is None or locked.is_deleted:
        raise ValidationError(CASE_WRITE_GONE_MESSAGE)
    return locked


@transaction.atomic
def update_episode(episode, user, **fields):
    """Update mutable fields on an episode + AuditLog (nur bei echtem Diff).

    Art.-18-Gate (Refs #2045): Episoden-Freitexte sind Inhalt der Fallakte —
    ihre Bearbeitung ist fachliche Nutzung, nicht Speicherung.

    Nach der Allowlist die Einrichtungsgrenze der handelnden Person (vor
    jeder Sperre, fail-closed), dann der Zustandsvertrag Person -> Fall ->
    Episode (:func:`_lock_episode_for_write`, das Art.-18-Gate eingeschlossen).
    Die Instanz stammt aus einem Lesevorgang vor der Sperre; ohne den Vertrag
    schriebe die Bearbeitung Freitext an eine Akte im Papierkorb, an eine
    getilgte Akte oder an eine geloeschte Episode.

    Gibt die Episode zurueck. Ohne ``fields`` ist der Aufruf ein No-Op; ein
    Aufruf, der nur bereits gespeicherte Werte setzt, schreibt keine
    Audit-Zeile (sonst waere "einfach immer auditieren" der billige Fix und
    das Log verloere seine Aussagekraft).
    """
    # Laeuft ZUERST — die Mass-Assignment-Pruefung darf nicht vom Diff
    # abhaengen (Muster ``update_case``/``update_client``).
    for key in fields:
        if key not in _UPDATABLE_FIELDS:
            raise ValueError(f"Feld '{key}' darf nicht aktualisiert werden.")
    from core.services.case.goals import _assert_actor_facility

    _assert_actor_facility(user, _episode_facility_id(episode))
    _lock_episode_for_write(episode)
    if not fields:
        return episode

    changed_fields = changed_fields_vs_db(episode, fields)
    for key, value in fields.items():
        setattr(episode, key, value)
    # Nur die uebergebenen Spalten schreiben (Refs #1789-Folge): ein voller
    # Save wuerde die parallel laufende Redaktions-Kaskade (Queryset-Update)
    # mit dem stalen Klartext ueberschreiben.
    episode.save(update_fields=list(fields.keys()))

    if changed_fields:
        audit_event(
            AuditLog.Action.EPISODE_UPDATE,
            user=user,
            facility=episode.case.facility,
            target_obj=episode,
            # Refs #1751 (Punkt 5): kein Titel, keine Beschreibung ins detail.
            # Beides ist klientenbezogener Freitext, den die Anonymisierung an
            # der Episode tilgt — im append-only AuditLog ueberlebte er sie.
            # Die Zuordnung bleibt ueber target_id + case_id vollstaendig.
            detail={"case_id": str(episode.case_id), "changed_fields": changed_fields},
        )
    return episode


@transaction.atomic
def create_episode(case, user, *, title, description="", started_at=None):
    """Episode anlegen — Service-Naht ueber ``Case.create_episode`` (Refs #2045).

    Das Anlegen lag bisher als reine Modell-Methode vor und wurde von der View
    direkt gerufen; damit gab es keinen Ort fuer das Art.-18-Gate ausser der
    View selbst — und ein View-Check ist genau das, was der Re-Review an den
    Loeschpfaden beanstandet hat. Diese duenne Naht gibt dem Gate den Platz im
    Service, ohne die Modell-Methode (fremdes Modul) anzufassen: Status-Guard und
    Feldsemantik bleiben unveraendert dort.

    Zuerst die Einrichtungsgrenze der handelnden Person (vor jeder Sperre,
    fail-closed), dann der Zustandsvertrag (Person -> Fall, frisch und
    gesperrt; :func:`~core.services.case.state_contract.lock_case_for_write`)
    — darin das Art.-18-Gate, der Widerruf der Person und Papierkorb/Tilgung
    des Falls. Die Instanz des Aufrufers stammt aus einem Lesevorgang vor der
    Sperre; angelegt wird deshalb an der FRISCH gesperrten Fallzeile, und auch
    die Statuspruefung der Modell-Methode sieht so den aktuellen Stand statt
    eines inzwischen geschlossenen Falls.
    """
    from core.services.case.goals import _assert_actor_facility

    _assert_actor_facility(user, case.facility_id)
    locked_case = lock_case_for_write(case)
    return locked_case.create_episode(user=user, title=title, description=description, started_at=started_at)


@transaction.atomic
def close_episode(episode, user):
    """Episode abschliessen — Service-Naht ueber ``Episode.close`` (Refs #2045).

    Spiegel von :func:`create_episode`. ``user`` wird durchgereicht, obwohl die
    Modell-Methode ihn nicht braucht: der Aufrufer soll den Vorgang wie jede
    andere Mutation als Service-Aufruf mit Akteur schreiben, nicht als
    stillen ORM-Zugriff.
    """
    from core.services.client.restriction import assert_processing_allowed

    assert_processing_allowed(episode.case.client_id, lock=True)
    return episode.close()
