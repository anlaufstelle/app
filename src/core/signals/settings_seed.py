"""Post-migrate-Signal: jede Facility besitzt mindestens eine ``Settings``-Row.

Refs #771 — Defense-in-Depth zur fail-closed-Whitelist im File-Vault. Die
Service-Schicht greift inzwischen auf ``DEFAULT_ALLOWED_FILE_TYPES`` zurueck,
wenn keine ``Settings``-Row existiert. Diese Lueckenschliessung ist dennoch
sichtbar wertvoll, weil sie Operator-Tooling (z.B. Whitelist-Editing in der
Admin-UI) konsistent ueber bestehende Mandanten anwendbar macht.

Der Receiver laeuft nach jeder ``migrate``-Run der ``core``-App und legt
fuer jede Facility ohne ``Settings`` eine Default-Row an. Idempotent.
"""

from __future__ import annotations

import logging

from django.apps import apps as django_apps
from django.db.models.signals import post_migrate
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(post_migrate)
def ensure_facility_settings(sender, app_config, **kwargs):
    """Lege fuer jede Facility ohne ``Settings``-Row eine Default-Row an."""
    if app_config is None or app_config.name != "core":
        return
    try:
        Facility = django_apps.get_model("core", "Facility")
        Settings = django_apps.get_model("core", "Settings")
    except LookupError:
        return

    facility_ids_with_settings = set(Settings.objects.values_list("facility_id", flat=True))
    missing = Facility.objects.exclude(pk__in=facility_ids_with_settings)
    created = 0
    for facility in missing:
        Settings.objects.create(facility=facility)
        created += 1
    if created:
        logger.info("ensure_facility_settings: %d Default-Settings angelegt.", created)
