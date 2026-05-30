from unittest.mock import patch

import pytest

from core.signals.settings_seed import ensure_facility_settings


class TestEnsureFacilitySettings:
    def test_returns_early_when_app_config_is_none(self):
        ensure_facility_settings(sender=None, app_config=None)

    def test_returns_early_for_other_app(self):
        class FakeApp:
            name = "django.contrib.auth"

        ensure_facility_settings(sender=None, app_config=FakeApp())

    def test_returns_early_on_lookup_error(self):
        class FakeApp:
            name = "core"

        with patch("core.signals.settings_seed.django_apps.get_model", side_effect=LookupError):
            ensure_facility_settings(sender=None, app_config=FakeApp())

    @pytest.mark.django_db
    def test_creates_settings_for_facility_without_one(self, organization):
        import logging

        from core.models import Facility, Settings
        from core.signals import settings_seed as settings_seed_module

        new_facility = Facility.objects.create(
            organization=organization,
            name="Test-Facility ohne Settings",
        )
        Settings.objects.filter(facility=new_facility).delete()
        assert not Settings.objects.filter(facility=new_facility).exists()

        class FakeApp:
            name = "core"

        # Der "core"-Logger hat propagate=False (siehe settings/base.py LOGGING),
        # daher haengen wir einen eigenen Handler direkt an den Modul-Logger
        # statt auf caplog (root-basiert) zu vertrauen.
        records: list[logging.LogRecord] = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = ListHandler(level=logging.INFO)
        settings_seed_module.logger.addHandler(handler)
        try:
            ensure_facility_settings(sender=None, app_config=FakeApp())
        finally:
            settings_seed_module.logger.removeHandler(handler)

        assert Settings.objects.filter(facility=new_facility).exists()
        assert any("ensure_facility_settings" in r.getMessage() for r in records)

    @pytest.mark.django_db
    def test_idempotent_when_all_facilities_have_settings(self, facility, settings_obj):
        from core.models import Settings

        before = Settings.objects.count()

        class FakeApp:
            name = "core"

        ensure_facility_settings(sender=None, app_config=FakeApp())

        assert Settings.objects.count() == before
