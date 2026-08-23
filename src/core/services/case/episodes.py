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

from django.db import transaction

from core.models import AuditLog
from core.services.audit import audit_event
from core.services.audit.diff import changed_fields_vs_db

logger = logging.getLogger(__name__)

#: Allowlist (Muster ``update_case``, Refs #734/#1786): verhindert
#: Mass-Assignment durch versehentlich durchgereichte Felder — insbesondere
#: ``case`` (ein Umhaengen der Episode in einen fremden Fall) und die
#: Soft-Delete-Marker.
_UPDATABLE_FIELDS = frozenset({"title", "description", "started_at", "ended_at"})


@transaction.atomic
def update_episode(episode, user, **fields):
    """Update mutable fields on an episode + AuditLog (nur bei echtem Diff).

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
