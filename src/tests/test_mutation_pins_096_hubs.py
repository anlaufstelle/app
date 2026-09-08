"""Pin-Tests der Prio-1-Survivor-Triage zu ``core.services.hubs`` (v0.96.0).

Quelle: ``src/core/services/hubs.py`` (Kennzahlen der Bereichs-Übersichten,
Refs #1813, Konzept #1801 §4.2). Gepinnt wird das Prio-1-Set der
Checkpoint-Survivor-Triage v0.96.0: zwei bestätigte Facility-Scoping-Lücken.

* ``datenschutz_overview`` — die DSGVO-Paket-Zeile ("zuletzt heruntergeladen")
  fragt ``AuditLog`` mit ``facility=facility``. Fällt die Klausel weg, sucht
  die Query über ALLE Einrichtungen nach dem jüngsten EXPORT/DSGVO-Eintrag —
  eine Leitung/Anwendungsbetreuung könnte das Download-Datum einer FREMDEN
  Einrichtung angezeigt bekommen. ``test_hub_services.py`` deckt das nicht ab,
  weil dort immer nur eine einzige Facility existiert.
* ``verwaltung_overview`` — reicht ``facility`` an ``verwaltung_metrics``
  durch. Fällt das auf ``None``, werden alle vier Kacheln
  (Dokumentationstypen, Feldvorlagen, Datenimport, Speicher) mit
  ``facility=None`` berechnet: ``for_facility(None)``/``Settings.objects.get
  (facility=None)`` liefern durchweg 0/kein Limit statt der echten
  Einrichtungswerte (Refs #2129) — kein Crash, deshalb überlebt der Mutant.
  ``test_overview_reuses_the_facility_admin_dashboard_context`` prüft nur die
  Schlüsselliste, nicht die Werte, die bei ``facility=None`` unverändert
  bleiben würde.

Konventionen wie ``test_hub_services.py``: ``pytestmark = pytest.mark.django_db``,
echte Model-Objekte statt Fixture-Faken, Zusicherungen exakt (``==``).
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import AuditLog, DocumentType
from core.services.hubs import datenschutz_overview, verwaltung_overview

pytestmark = pytest.mark.django_db


def _metric(metrics, key):
    found = [m for m in metrics if m.key == key]
    assert found, f"Kennzahl {key!r} fehlt (vorhanden: {[m.key for m in metrics]})"
    return found[0]


class TestDatenschutzOverviewFacilityScoping:
    def test_dsgvo_row_ignores_a_newer_download_of_another_facility(
        self, facility, admin_user, second_facility, foreign_staff
    ):
        """Pin: ``x_datenschutz_overview__mutmut_167`` — ``facility=facility,``
        entfällt im ``AuditLog.objects.filter(...)`` der DSGVO-Paket-Zeile.

        Aufbau: die EIGENE Einrichtung hat einen ÄLTEREN DSGVO-EXPORT-Eintrag,
        die ``second_facility`` (IDOR-Fixture aus ``conftest.py``) einen
        JÜNGEREN. ``datenschutz_overview`` wird für die EIGENE Einrichtung
        aufgerufen — die Zeile darf nur das eigene, ältere Datum nennen.

        Abweichung beim Mutanten: ohne die Facility-Klausel gewinnt
        ``order_by("-timestamp").first()`` den JÜNGEREN, fremden Eintrag —
        ``row.detail`` nennt dessen Datum statt des eigenen (älteren).
        """
        eigener = AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            target_type="DSGVO-Dokument",
            target_id="vvt",
            detail={"document": "vvt"},
            timestamp=timezone.now() - timezone.timedelta(days=2),
        )
        fremder = AuditLog.objects.create(
            facility=second_facility,
            user=foreign_staff,
            action=AuditLog.Action.EXPORT,
            target_type="DSGVO-Dokument",
            target_id="vvt",
            detail={"document": "vvt"},
            timestamp=timezone.now(),
        )
        eigenes_datum = timezone.localtime(eigener.timestamp).strftime("%d.%m.%Y")
        fremdes_datum = timezone.localtime(fremder.timestamp).strftime("%d.%m.%Y")
        assert eigenes_datum != fremdes_datum, "Sanity: die Daten muessen unterscheidbar sein."

        row = _metric(datenschutz_overview(facility, admin_user), "dsgvo")

        assert eigenes_datum in row.detail
        assert fremdes_datum not in row.detail


class TestVerwaltungOverviewFacilityScoping:
    def test_overview_metrics_use_the_own_facility_not_none(self, facility, admin_user, field_template, import_job):
        """Pin: ``x_verwaltung_overview__mutmut_3`` — ``verwaltung_metrics(facility)``
        -> ``verwaltung_metrics(None)``.

        Aufbau wie ``test_metrics_name_doctypes_fieldtemplates_imports_and_storage``
        in ``test_hub_services.py``: zwei Dokumentationstypen (1 aktiv, 1
        inaktiv), eine Feldvorlage, ein offener Importlauf.

        Abweichung beim Mutanten: ``for_facility(None)`` liefert für JEDE
        Kachel 0 statt der echten Zahl — ``config``/``config-fieldtemplates``/
        ``import`` wären alle ``0`` statt ``2``/``1``/``1``.
        """
        DocumentType.objects.create(facility=facility, name="Hub-Typ aktiv 096")
        DocumentType.objects.create(facility=facility, name="Hub-Typ inaktiv 096", is_active=False)

        context = verwaltung_overview(facility, admin_user)

        assert _metric(context["metrics"], "config").value == 2
        assert _metric(context["metrics"], "config-fieldtemplates").value == 1
        assert _metric(context["metrics"], "import").value == 1
