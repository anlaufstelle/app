"""Pin-Tests zum Meilenstein release-0.96 — Prio-1-Survivors in ``core.services.dashboard.main``.

Quelle: Mutations-Delta v0.96.0, Survivor-Triage des Moduls
``src/core/services/dashboard/main.py``. Gepinnt wird die Prio-1-Menge der
Funktion ``facility_admin_dashboard_context``:

* der **Facility-Scope der Nutzer-Query**: ohne ihn zaehlen ``users_total``
  und ``users_without_mfa`` alle aktiven Nutzer ALLER Einrichtungen — ein
  Scoping-Leak in einer Kachel, die jede Anwendungsbetreuung auf
  ``/verwaltung/`` sieht. Der Bestandstest
  ``test_mfa_zaehler_skaliert_nicht_mit_der_nutzerzahl`` legt nur Nutzer der
  eigenen Einrichtung an und bemerkt das nicht;
* die **Richtung der K-Anonymisierungs-Warnung**: sie gehoert in den
  riskanten Zustand (K-Anon deaktiviert ⇒ Loeschlauf entfernt hart). Der
  Bestandstest ``test_returns_settings_warnings_for_disabled_mfa`` setzt
  ``retention_use_k_anonymization=False``, prueft aber nur die MFA-Warnung.

Konventionen wie ``test_mutation_pins_094_export_1.py``: exakte Vergleiche
statt Teilstring-Heuristik, wo mutmut ``"XX<text>XX"``-Varianten erzeugt;
Sprachkontext per ``translation.override("de")`` (der de-Katalogeintrag des
Warntexts ist identisch zur msgid); Fixtures aus ``src/tests/conftest.py``.
"""

from __future__ import annotations

import pytest
from django.utils import translation

from core.models import Settings, User
from core.services.dashboard import facility_admin_dashboard_context

#: Wortlaut der K-Anon-Warnung (``core/services/dashboard/main.py``). Exakt
#: verglichen, damit ``"XX…XX"``-Textmutanten nicht durch eine
#: Teilstring-Pruefung schluepfen.
K_ANON_WARNUNG = (
    "K-Anonymisierung ist deaktiviert — Löschlauf entfernt Datensätze hart. "
    "Falls Statistik-Erhalt gewünscht, in Settings aktivieren."
)


@pytest.mark.django_db
class TestFacilityAdminDashboardScope:
    """Die Nutzer-Kacheln zaehlen nur die eigene Einrichtung."""

    def test_nutzerzahl_bleibt_auf_die_eigene_einrichtung_beschraenkt(self, facility, admin_user, second_facility):
        """Mutante ``x_facility_admin_dashboard_context__mutmut_5``
        (``filter(facility=facility, is_active=True)`` → ``filter(is_active=True)``).

        Die zweite Einrichtung bringt drei eigene aktive Nutzer mit. Original:
        ``users_total == 2`` / ``users_without_mfa == 2`` (nur die eigenen).
        Mutant: 5 — die Kachel der Anwendungsbetreuung zaehlt fremde
        Einrichtungen mit.
        """
        Settings.objects.create(facility=facility)
        User.objects.create_user(
            username="eigene-fachkraft",
            password="x",
            facility=facility,
            role=User.Role.STAFF,
        )
        for i in range(3):
            User.objects.create_user(
                username=f"fremde-fachkraft-{i}",
                password="x",
                facility=second_facility,
                role=User.Role.STAFF,
            )

        ctx = facility_admin_dashboard_context(admin_user, facility)

        assert ctx["users_total"] == 2
        assert ctx["users_without_mfa"] == 2

    def test_inaktive_nutzer_der_eigenen_einrichtung_zaehlen_nicht(self, facility, admin_user):
        """Gegenprobe zum Scope-Pin: ``is_active=True`` bleibt Teil derselben Query.

        Ohne diese Zusicherung koennte der Scope-Pin durch einen Mutanten
        erfuellt bleiben, der stattdessen den Aktiv-Filter fallen laesst.
        """
        Settings.objects.create(facility=facility)
        User.objects.create_user(
            username="ausgeschiedene-fachkraft",
            password="x",
            facility=facility,
            role=User.Role.STAFF,
            is_active=False,
        )

        ctx = facility_admin_dashboard_context(admin_user, facility)

        assert ctx["users_total"] == 1


@pytest.mark.django_db
class TestFacilityAdminDashboardWarnungen:
    """Die K-Anon-Warnung steht im riskanten Zustand, nicht im sicheren."""

    def test_warnung_erscheint_wenn_k_anonymisierung_deaktiviert_ist(self, facility, admin_user):
        """Mutante ``x_facility_admin_dashboard_context__mutmut_35``
        (``if not settings_obj.retention_use_k_anonymization:`` → ohne ``not``).

        Deaktivierte K-Anonymisierung ist der riskante Zustand (harte
        Loeschung ohne Anonymisierungs-Fallback). Der Mutant liefert hier
        KEINE Warnung.
        """
        Settings.objects.create(
            facility=facility,
            mfa_enforced_facility_wide=True,
            retention_use_k_anonymization=False,
        )

        with translation.override("de"):
            warnungen = [str(w) for w in facility_admin_dashboard_context(admin_user, facility)["settings_warnings"]]

        assert warnungen == [K_ANON_WARNUNG]

    def test_keine_warnung_wenn_k_anonymisierung_aktiv_ist(self, facility, admin_user):
        """Gegenrichtung derselben Mutante ``…__mutmut_35``.

        Bei aktiver K-Anonymisierung ist die Warnung sachlich falsch; der
        Mutant gibt sie genau dann aus.
        """
        Settings.objects.create(
            facility=facility,
            mfa_enforced_facility_wide=True,
            retention_use_k_anonymization=True,
        )

        with translation.override("de"):
            warnungen = [str(w) for w in facility_admin_dashboard_context(admin_user, facility)["settings_warnings"]]

        assert warnungen == []
