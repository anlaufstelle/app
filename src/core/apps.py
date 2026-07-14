from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Anlaufstelle Core"

    def ready(self):
        import core.checks  # noqa: F401  # System-Checks registrieren (L8, Refs #1375)
        import core.signals.audit  # noqa: F401
        import core.signals.event_search  # noqa: F401
        import core.signals.facility_protection  # noqa: F401
        import core.signals.field_template  # noqa: F401
        import core.signals.offline_invalidation  # noqa: F401
        import core.signals.settings_seed  # noqa: F401
