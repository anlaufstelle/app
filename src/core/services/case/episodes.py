"""Episode writes with signed baselines, state guards and attributed audits."""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import AuditLog, Episode
from core.services.audit import audit_event
from core.services.case.state_contract import CASE_WRITE_GONE_MESSAGE, lock_case_for_write
from core.services.form_state import require_form_state, sign_form_state

logger = logging.getLogger(__name__)

# Wortlaut wie die Vorpruefung der View und die Statuspruefung am Modell.
EPISODE_CASE_NOT_OPEN_MESSAGE = _("Episoden können nur für offene Fälle erstellt werden.")

#: Allowlist (Muster ``update_case``, Refs #734/#1786): verhindert
#: Mass-Assignment durch versehentlich durchgereichte Felder — insbesondere
#: ``case`` (ein Umhaengen der Episode in einen fremden Fall) und die
#: Soft-Delete-Marker.
_UPDATABLE_FIELDS = frozenset({"title", "description", "started_at", "ended_at"})


def validate_episode_dates(started_at, ended_at):
    """Reject reversed dates without imposing a limit on future dates."""
    if started_at is not None and ended_at is not None and ended_at < started_at:
        raise ValidationError({"ended_at": _("Das Ende darf nicht vor dem Beginn liegen.")})


_EPISODE_STATE_SALT = "core.episode.form_state"


def episode_basis_token(episode):
    """Sign all editable episode fields, bound to episode and facility."""
    return sign_form_state(
        episode, fields=_UPDATABLE_FIELDS, facility_id=episode.case.facility_id, salt=_EPISODE_STATE_SALT
    )


def _episode_facility_id(episode):
    """Einrichtung der Akte einer Episode — ungesperrt gelesen.

    Die Einrichtung einer Akte ist unveraenderlich; der Vergleich mit der
    Einrichtung der handelnden Person braucht also keinen frischen Stand und
    sperrt nichts ueber die Mandantengrenze hinweg.

    Fehlt die Episodenzeile, endet der Aufruf HIER — vor jeder Sperre und
    fuer jede handelnde Person. Ohne Zeile gibt es keine Einrichtung zum
    Vergleichen; liefe der Aufruf weiter, naehme auch eine Fachkraft aus
    einer anderen Einrichtung erst die Personen- und Fallsperre der Akte,
    bevor der Zustandsvertrag die fehlende Episode bemerkt (Muster
    :func:`~core.services.case.state_contract.lock_goal_for_write`). Eine
    Episode im Papierkorb bleibt Sache der gesperrten Neulesung — der Marker
    kann sich bis dahin noch aendern.
    """
    facility_id = Episode.objects.filter(pk=episode.pk).values_list("case__facility_id", flat=True).first()
    if facility_id is None:
        raise ValidationError(CASE_WRITE_GONE_MESSAGE)
    return facility_id


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
def update_episode(episode, user, *, basis_token=None, **fields):
    """Apply reviewed fields to the fresh locked row after the state contract."""
    for key in fields:
        if key not in _UPDATABLE_FIELDS:
            raise ValueError(f"Field '{key}' cannot be updated.")
    from core.services.case.goals import _assert_actor_facility

    _assert_actor_facility(user, _episode_facility_id(episode))
    locked = _lock_episode_for_write(episode)
    require_form_state(
        basis_token,
        locked,
        fields=_UPDATABLE_FIELDS,
        facility_id=locked.case.facility_id,
        salt=_EPISODE_STATE_SALT,
    )
    validate_episode_dates(fields.get("started_at", locked.started_at), fields.get("ended_at", locked.ended_at))
    changed_fields = [key for key, value in fields.items() if getattr(locked, key) != value]
    if not changed_fields:
        return locked
    for key in changed_fields:
        setattr(locked, key, fields[key])
    locked.save(update_fields=changed_fields)
    audit_event(
        AuditLog.Action.EPISODE_UPDATE,
        user=user,
        facility=locked.case.facility,
        target_obj=locked,
        # Episode plaintext must not survive redaction in the append-only audit.
        detail={"case_id": str(locked.case_id), "changed_fields": changed_fields},
    )
    return locked


@transaction.atomic
def create_episode(case, user, *, title, description="", started_at=None, ended_at=None):
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
    Sperre; angelegt wird deshalb an der FRISCH gesperrten Fallzeile.

    Ist der Fall dort nicht (mehr) offen, lehnt der Dienst mit
    :data:`EPISODE_CASE_NOT_OPEN_MESSAGE` als ``ValidationError`` ab — am
    gesperrten Stand, vor der Modell-Methode. Die Absage traegt ihre Ursache
    so selbst: der Aufrufer muss sie nicht nachtraeglich aus einem erneuten,
    ungesperrten Blick auf den Fall erraten (der Fall kann inzwischen wieder
    offen sein), und die ``ValueError`` der Modell-Methode bleibt ein
    Programmfehler.
    """
    from core.services.case.goals import _assert_actor_facility

    _assert_actor_facility(user, case.facility_id)
    locked_case = lock_case_for_write(case)
    if locked_case.status != locked_case.Status.OPEN:
        raise ValidationError(EPISODE_CASE_NOT_OPEN_MESSAGE)
    started_at = started_at or timezone.localdate()
    validate_episode_dates(started_at, ended_at)
    return locked_case.create_episode(
        user=user, title=title, description=description, started_at=started_at, ended_at=ended_at
    )


@transaction.atomic
def close_episode(episode, user):
    """Episode abschliessen — Service-Naht ueber ``Episode.close`` (Refs #2045).

    Spiegel von :func:`create_episode`. ``user`` wird durchgereicht, obwohl die
    Modell-Methode ihn nicht braucht: der Aufrufer soll den Vorgang wie jede
    andere Mutation als Service-Aufruf mit Akteur schreiben, nicht als
    stillen ORM-Zugriff.

    Einrichtungsgrenze und Zustandsvertrag wie bei :func:`update_episode`.
    Abgeschlossen wird die FRISCH gesperrte Zeile: die uebergebene Instanz
    stammt aus einem Lesevorgang vor der Sperre, ihr ``ended_at`` sagt nicht,
    ob die Episode INZWISCHEN abgeschlossen ist — ein parallel gesetztes Ende
    bliebe sonst nicht stehen. Die Instanz des Aufrufers bekommt am Ende den
    neuen Stand und bleibt das zurueckgegebene Objekt (Muster ``close_case``).
    """
    from core.services.case.goals import _assert_actor_facility

    _assert_actor_facility(user, _episode_facility_id(episode))
    _lock_episode_for_write(episode).close()
    episode.refresh_from_db()
    return episode
