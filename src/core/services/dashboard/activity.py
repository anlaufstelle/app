"""PII-free Activity writes and display-time summary rendering."""

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext as _

from core.models.activity import Activity

ACTIVITY_CONTEXT_SCHEMA = 1


def activity_target_prefetch():
    """Batch GenericFK targets and the Event relations used by the renderer."""
    from django.contrib.contenttypes.prefetch import GenericPrefetch

    from core.models.event import Event

    return GenericPrefetch(
        "target",
        [Event.objects.select_related("document_type", "client")],
    )


def _target(activity):
    """Resolve the GenericFK defensively; deleted targets legitimately return None."""
    try:
        return activity.target
    except (AttributeError, ObjectDoesNotExist):
        return None


def _target_model(activity) -> str:
    target_type = getattr(activity, "target_type", None)
    return getattr(target_type, "model", "") or ""


def _generic_summary(activity) -> str:
    """Readable fallback for migrated rows, unknown actions and missing targets."""
    labels = {
        "client": _("Person"),
        "event": _("Dokumentation"),
        "workitem": _("Aufgabe"),
        "case": _("Fall"),
        "milestone": _("Meilenstein"),
    }
    label = labels.get(_target_model(activity), _("Aktivität"))
    return _("%(target)s %(verb)s") % {"target": label, "verb": activity.get_verb_display()}


def render_activity_summary(activity) -> str:
    """Render the human sentence from a PII-free action and the live target.

    User-entered values are read from the referenced object only. They are
    never copied into ``Activity.context``. If that object no longer exists,
    the renderer deliberately falls back to a generic but readable sentence.
    """
    context = activity.context if isinstance(activity.context, dict) else {}
    raw_action = context.get("action")
    if not isinstance(raw_action, str):
        return _generic_summary(activity)
    try:
        action = Activity.Action(raw_action)
    except ValueError:
        return _generic_summary(activity)

    target = _target(activity)

    if action == Activity.Action.CLIENT_CREATED:
        if target is None:
            return _("Person angelegt")
        return _("Person %(pseudonym)s angelegt") % {"pseudonym": target.pseudonym}
    if action == Activity.Action.CLIENT_QUALIFIED:
        if target is None:
            return _("Person qualifiziert")
        return _("%(pseudonym)s qualifiziert") % {"pseudonym": target.pseudonym}
    if action == Activity.Action.CLIENT_UPDATED:
        if context.get("kind") == "renamed":
            if target is None:
                return _("Person umbenannt")
            return _("Person %(pseudonym)s umbenannt") % {"pseudonym": target.pseudonym}
        if target is None:
            return _("Person aktualisiert")
        return _("Person %(pseudonym)s aktualisiert") % {"pseudonym": target.pseudonym}
    if action == Activity.Action.CLIENT_TRASHED:
        if target is None:
            return _("Person in den Papierkorb verschoben")
        return _("Person %(pseudonym)s in den Papierkorb verschoben") % {"pseudonym": target.pseudonym}
    if action == Activity.Action.CLIENT_MERGED:
        if target is None:
            return _("Person mit einem Duplikat zusammengeführt")
        return _("Person %(pseudonym)s mit einem Duplikat zusammengeführt") % {"pseudonym": target.pseudonym}
    if action == Activity.Action.CLIENT_UNMERGED:
        return _("Zusammenführung mit einem Duplikat aufgehoben")

    if action == Activity.Action.EVENT_CREATED:
        if target is None:
            return _("Dokumentation angelegt")
        document_type = target.document_type.name
        if target.client is not None:
            return _("%(document_type)s für %(pseudonym)s") % {
                "document_type": document_type,
                "pseudonym": target.client.pseudonym,
            }
        return document_type
    if action == Activity.Action.EVENT_DELETED:
        if target is None:
            return _("Dokumentation gelöscht")
        return _("%(document_type)s gelöscht") % {"document_type": target.document_type.name}

    if action == Activity.Action.WORKITEM_CREATED:
        if target is None:
            return _("Aufgabe angelegt")
        return _("Aufgabe: %(title)s") % {"title": target.title}
    if action == Activity.Action.WORKITEM_UPDATED:
        if target is None:
            return _("Aufgabe aktualisiert")
        return _("Aufgabe aktualisiert: %(title)s") % {"title": target.title}
    if action == Activity.Action.WORKITEM_COMPLETED:
        if target is None:
            return _("Aufgabe erledigt")
        return _("Aufgabe erledigt: %(title)s") % {"title": target.title}
    if action == Activity.Action.WORKITEM_REOPENED:
        if target is None:
            return _("Aufgabe wiedereröffnet")
        return _("Aufgabe wiedereröffnet: %(title)s") % {"title": target.title}
    if action == Activity.Action.WORKITEM_RECURRENCE_CREATED:
        if target is None:
            return _("Wiederkehrende Folgeaufgabe angelegt")
        return _("Wiederkehrende Folgeaufgabe: %(title)s") % {"title": target.title}

    if action == Activity.Action.CASE_CREATED:
        if target is None:
            return _("Fall eröffnet")
        return _("Fall eröffnet: %(title)s") % {"title": target.title}
    if action == Activity.Action.CASE_CLOSED:
        if target is None:
            return _("Fall geschlossen")
        return _('Fall "%(title)s" geschlossen') % {"title": target.title}
    if action == Activity.Action.CASE_REOPENED:
        if target is None:
            return _("Fall wiedereröffnet")
        return _('Fall "%(title)s" wiedereröffnet') % {"title": target.title}
    if action == Activity.Action.CASE_TRASHED:
        return _("Fall in den Papierkorb verschoben")
    if action == Activity.Action.MILESTONE_DELETED:
        return _("Meilenstein gelöscht")

    return _generic_summary(activity)


def log_activity(*, facility, actor, verb, target, action, client_update_kind=None):
    """Create an Activity entry for a system action.

    ``action`` is validated against the closed, PII-free renderer vocabulary;
    no pre-rendered or user-entered string crosses this write boundary.
    """
    action = Activity.Action(action)
    if client_update_kind not in (None, "renamed"):
        raise ValueError("client_update_kind must be None or 'renamed'")
    if client_update_kind is not None and action != Activity.Action.CLIENT_UPDATED:
        raise ValueError("client_update_kind is only valid for CLIENT_UPDATED")
    context = {"schema": ACTIVITY_CONTEXT_SCHEMA, "action": action.value}
    if client_update_kind is not None:
        context["kind"] = client_update_kind
    return Activity.objects.create(
        facility=facility,
        actor=actor,
        verb=verb,
        target_type=ContentType.objects.get_for_model(target),
        target_id=target.pk,
        context=context,
    )
