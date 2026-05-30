"""Architektur-Tests fuer AnlaufstelleAdminSite (Refs #785).

Sichert, dass:
- alle Anlaufstelle-Modelle an der Custom-Site registriert sind
- die Default-admin.site KEIN Anlaufstelle-Modell enthaelt (keine versehentliche
  Doppelregistrierung oder vergessenes site=-Argument)
"""

from __future__ import annotations

import pytest


@pytest.mark.django_db
def test_all_core_models_registered_with_custom_site():
    """Alle ModelAdmin-Klassen aus core/admin.py haengen am Custom-Site."""
    from core.admin_site import anlaufstelle_admin_site
    from core.models import (
        AuditLog,
        Case,
        Client,
        DeletionRequest,
        DocumentType,
        Event,
        EventHistory,
        Facility,
        FieldTemplate,
        Organization,
        QuickTemplate,
        Settings,
        StatisticsSnapshot,
        TimeFilter,
        User,
        WorkItem,
    )
    from core.models.attachment import EventAttachment

    expected_models = {
        User,
        Organization,
        Facility,
        Client,
        DocumentType,
        FieldTemplate,
        QuickTemplate,
        Event,
        EventHistory,
        EventAttachment,
        TimeFilter,
        WorkItem,
        DeletionRequest,
        Case,
        AuditLog,
        Settings,
        StatisticsSnapshot,
    }

    custom_registered = set(anlaufstelle_admin_site._registry.keys())
    missing = expected_models - custom_registered
    assert not missing, f"Diese Modelle sind nicht am Custom-Site registriert: {missing}"


@pytest.mark.django_db
def test_default_admin_site_has_no_anlaufstelle_models():
    """Die Default-admin.site darf KEIN Anlaufstelle-Modell registriert haben.

    Sichert gegen versehentliches Vergessen des ``site=``-Arguments im
    ``@admin.register(...)``-Decorator.
    """
    from django.contrib import admin as django_admin

    from core.models import User

    default_registered = set(django_admin.site._registry.keys())
    # User ist unser zentrales Modell — wenn das am Default-Site haengt, ist was kaputt.
    assert User not in default_registered, (
        "User ist am Default-admin.site registriert — Custom-Site-Migration unvollstaendig."
    )
