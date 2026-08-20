"""Post-migrate-Signal: jede Facility besitzt mindestens eine ``Settings``-Row.

Refs #771 — Defense-in-Depth zur fail-closed-Whitelist im File-Vault. Die
Service-Schicht greift inzwischen auf ``DEFAULT_ALLOWED_FILE_TYPES`` zurueck,
wenn keine ``Settings``-Row existiert. Diese Lueckenschliessung ist dennoch
sichtbar wertvoll, weil sie Operator-Tooling (z.B. Whitelist-Editing in der
Admin-UI) konsistent ueber bestehende Mandanten anwendbar macht.

Der Receiver laeuft nach jeder ``migrate``-Run der ``core``-App und legt
fuer jede Facility ohne ``Settings`` eine Default-Row an. Idempotent.

Refs #1884 — dieser Pfad deckt nur den Deploy ab. Die Laufzeit-Anlage im
Django-Admin haengt ihren Hook in ``FacilityAdmin.save_model``; beide rufen
denselben Helfer ``core.services.facility.ensure_settings_for_facility``.
"""

from __future__ import annotations

import logging

from django.apps import apps as django_apps
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from core.services.facility import ensure_settings_for_facility

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
        # Refs #1884: gemeinsamer Helfer mit ``FacilityAdmin.save_model`` —
        # eine Stelle, an der die Default-Zeile entsteht.
        if ensure_settings_for_facility(facility):
            created += 1
    if created:
        logger.info("ensure_facility_settings: %d Default-Settings angelegt.", created)
