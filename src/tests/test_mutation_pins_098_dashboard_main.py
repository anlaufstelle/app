"""Pin-Tests der Welle K (0.98) — Prio-2-Testluecken in
``core.services.dashboard.main``.

Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Batch ``hubs-dashboard`` der Survivor-Triage (``triage-096-export-
dashboard.json``). Gepinnt werden fuenf Mutanten-Familien der vier
rollenspezifischen ``..._dashboard_context``-Funktionen, die der Bestand
(``test_dashboard_services.py``) nicht abdeckt:

* **``facility_admin_dashboard_context`` — TOTP-Subquery-Korrelation**: die
  ``Exists``-Subquery, die ein bestaetigtes TOTP-Geraet je Nutzer prueft, kann
  die ``OuterRef``-Korrelation verlieren, das ``confirmed=True``-Kriterium auf
  ``None``/``False`` drehen oder ganz entfernen — der Bestand
  (``test_mfa_zaehler_skaliert_nicht_mit_der_nutzerzahl``) legt nie ein
  BESTAETIGTES Geraet an und bemerkt keine dieser fuenf Varianten.
* **``facility_admin_dashboard_context`` — Settings-Warntext**: die Meldung
  "Keine Settings konfiguriert" faellt komplett weg (``None``), waehrend die
  Warnliste trotzdem nicht-leer bleibt.
* **``facility_admin_dashboard_context`` — Dict-Key ``storage_usage``**:
  Gross-/Falschschreibung des Schluessels.
* **``lead_dashboard_context`` — Snapshot-Auswahl**: ``last_snapshot`` faellt
  auf ``None``/eine falsche Facility zurueck oder die
  ``order_by("-year", "-month")``-Sortierung verliert Jahr oder Monat als
  Kriterium.
* **``staff_dashboard_context``/``super_admin_dashboard_context``**: fehlende
  Ober-/Statusgrenzen bei Ereignissen bzw. Aufgaben, verlorene
  Ueberfaellig-zuerst-Sortierung, fehlende Zeit-/Aktionsfilter der
  kritischen Audit-Zeile, invertierte Aktiv-Zaehlung der Nutzerzahl.

Konventionen wie ``test_dashboard_services.py``/
``test_mutation_pins_096_dashboard.py``: echte Model-Objekte, exakte
Vergleiche, Fixtures aus ``src/tests/conftest.py``. ``_encryption_key`` liegt
modulweit an (Lehre 112): der Mutantenbaum hat keine ``.env``.

Hinweis: ``x_facility_admin_dashboard_context__mutmut_6`` (``is_active=True``
entfaellt aus dem User-Filter der Nutzer-Query) ist BEREITS getoetet — der
Bestandstest ``test_mutation_pins_096_dashboard.py::
TestFacilityAdminDashboardScope::test_inaktive_nutzer_der_eigenen_
einrichtung_zaehlen_nicht`` legt einen inaktiven Nutzer an und prueft
``users_total == 1``; das faellt bereits ohne neuen Pin. Kein Test dafuer
in dieser Datei.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation

from core.models import (
    AuditLog,
    EncryptedTOTPDevice,
    Event,
    StatisticsSnapshot,
    User,
    WorkItem,
)
from core.services.dashboard import (
    facility_admin_dashboard_context,
    lead_dashboard_context,
    staff_dashboard_context,
    super_admin_dashboard_context,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# facility_admin_dashboard_context — TOTP-Subquery-Korrelation
# ---------------------------------------------------------------------------


def test_without_mfa_counts_only_users_lacking_a_confirmed_device_of_their_own(
    facility, admin_user, staff_user, lead_user
):
    """Pins x_facility_admin_dashboard_context__mutmut_9/10/11/12/16
    (Korrelations-/``confirmed``-Mutationen der TOTP-``Exists``-Subquery).

    Drei Nutzer: ``staff_user``/``lead_user`` mit je einem BESTAETIGTEN
    Geraet, ``admin_user`` mit einem NUR unbestaetigten Geraet. Original:
    ``users_without_mfa == 1`` (nur ``admin_user``). Zwei bestaetigte gegen
    einen nur-unbestaetigten Nutzer, damit ein glatter ``confirmed``-Flip
    (mutmut_16 tauscht die Rollen von ``admin_user``/den beiden anderen) NICHT
    zufaellig dieselbe Gesamtzahl liefert wie das Original (2 vs. 1).
    """
    EncryptedTOTPDevice.objects.create(user=staff_user, name="pin098-x1", confirmed=True)
    EncryptedTOTPDevice.objects.create(user=lead_user, name="pin098-x2", confirmed=True)
    EncryptedTOTPDevice.objects.create(user=admin_user, name="pin098-y", confirmed=False)

    ctx = facility_admin_dashboard_context(admin_user, facility)

    assert ctx["users_total"] == 3
    assert ctx["users_without_mfa"] == 1


# ---------------------------------------------------------------------------
# facility_admin_dashboard_context — Settings-Warntext
# ---------------------------------------------------------------------------


def test_missing_settings_warning_has_the_expected_wording(facility, admin_user):
    """Pins x_facility_admin_dashboard_context__mutmut_24
    (``warnings.append(_("Keine Settings konfiguriert…"))`` -> ``warnings.
    append(None)``)."""
    with translation.override("de"):
        ctx = facility_admin_dashboard_context(admin_user, facility)

    assert ctx["settings_warnings"] == ["Keine Settings konfiguriert — bitte initial einrichten."]


# ---------------------------------------------------------------------------
# facility_admin_dashboard_context — Dict-Key storage_usage
# ---------------------------------------------------------------------------


def test_context_uses_the_exact_storage_usage_key(facility, admin_user):
    """Pins x_facility_admin_dashboard_context__mutmut_55/56 (Dict-Key
    ``storage_usage`` -> ``XXstorage_usageXX``/``STORAGE_USAGE``)."""
    ctx = facility_admin_dashboard_context(admin_user, facility)

    assert "storage_usage" in ctx


# ---------------------------------------------------------------------------
# lead_dashboard_context — Snapshot-Auswahl
# ---------------------------------------------------------------------------


def test_last_snapshot_prefers_the_newer_year_over_the_higher_month(facility, lead_user):
    """Pins x_lead_dashboard_context__mutmut_7/10/12 (``last_snapshot`` fest
    auf ``None``, ``order_by("-month")`` ohne Jahr, ``for_facility(None)``).

    Ein Snapshot aus dem VORJAHR mit dem hoeheren Monat (Dezember) gegen
    einen aus dem AKTUELLEN Jahr mit niedrigerem Monat (Januar) — das Jahr hat
    Vorrang, der Januar-Snapshot ist der tatsaechlich juengste.
    """
    StatisticsSnapshot.objects.create(facility=facility, year=2025, month=12, data={})
    newest = StatisticsSnapshot.objects.create(facility=facility, year=2026, month=1, data={})

    ctx = lead_dashboard_context(lead_user, facility)

    assert ctx["last_snapshot"] == newest


def test_last_snapshot_query_keeps_month_as_a_sort_key(facility, lead_user):
    """Pins x_lead_dashboard_context__mutmut_11 (``order_by("-year",
    "-month")`` -> ``order_by("-year",)``, verliert den Monat als
    Tiebreaker).

    Das reine ``.first()``-Ergebnis trennt diese Mutante auf diesem Schema
    NICHT zuverlaessig: ``StatisticsSnapshot`` traegt einen Unique-Index auf
    ``(facility, year, month)`` (``unique_snapshot_per_month``), den Postgres
    fuer ``ORDER BY year DESC`` per Index-Scan nutzt — das liefert inzidentell
    auch eine nach Monat sortierte Reihenfolge, VOELLIG unabhaengig davon, ob
    ``"-month"`` ueberhaupt im ``order_by()``-Aufruf steht (empirisch
    verifiziert: zwei Snapshots desselben Jahres, unterschiedlicher
    Einfuegereihenfolge, liefern mit UND ohne Monat-Kriterium dasselbe
    Ergebnis). Der Test prueft deshalb direkt die tatsaechlich ausgefuehrte
    SQL-Order-Klausel auf ``core_statisticssnapshot``.
    """
    StatisticsSnapshot.objects.create(facility=facility, year=2026, month=2, data={})
    StatisticsSnapshot.objects.create(facility=facility, year=2026, month=9, data={})

    with CaptureQueriesContext(connection) as captured:
        lead_dashboard_context(lead_user, facility)

    snapshot_selects = [
        q["sql"]
        for q in captured.captured_queries
        if q["sql"].lstrip().upper().startswith("SELECT") and '"core_statisticssnapshot"' in q["sql"]
    ]
    assert snapshot_selects, "Kein SELECT gegen core_statisticssnapshot erfasst."
    match = re.search(r"ORDER BY (.+?)(?:\s+LIMIT|\Z)", snapshot_selects[0], re.IGNORECASE)
    assert match, f"Kein ORDER BY in der Snapshot-Query: {snapshot_selects[0]!r}"
    order_clause = match.group(1).lower()

    assert "year" in order_clause
    assert "month" in order_clause


def test_context_uses_the_exact_last_snapshot_key(facility, lead_user):
    """Pins x_lead_dashboard_context__mutmut_23/24 (Dict-Key
    ``last_snapshot`` -> ``XXlast_snapshotXX``/``LAST_SNAPSHOT``)."""
    ctx = lead_dashboard_context(lead_user, facility)

    assert "last_snapshot" in ctx


# ---------------------------------------------------------------------------
# staff_dashboard_context — heutige Ereignisse, offene Aufgaben
# ---------------------------------------------------------------------------


def test_today_events_count_ignores_events_dated_in_the_future(
    facility, staff_user, client_identified, doc_type_contact
):
    """Pins x_staff_dashboard_context__mutmut_17 (``occurred_at__lt=
    today_end`` entfaellt — keine Obergrenze mehr)."""
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now() + timedelta(days=2),
        data_json={"dauer": 1},
        created_by=staff_user,
    )

    ctx = staff_dashboard_context(staff_user, facility)

    assert ctx["today_events_count"] == 0


def test_today_events_count_ignores_soft_deleted_events(facility, staff_user, client_identified, doc_type_contact):
    """Pins x_staff_dashboard_context__mutmut_18 (``is_deleted=False``
    entfaellt)."""
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 1},
        created_by=staff_user,
        is_deleted=True,
    )

    ctx = staff_dashboard_context(staff_user, facility)

    assert ctx["today_events_count"] == 0


def test_my_open_workitems_count_excludes_done_items(facility, staff_user, client_identified):
    """Pins x_staff_dashboard_context__mutmut_26 (``status__in=[OPEN,
    IN_PROGRESS]`` entfaellt aus dem Filter von ``open_qs``)."""
    WorkItem.objects.create(
        facility=facility,
        title="Pin-098 erledigt",
        assigned_to=staff_user,
        client=client_identified,
        created_by=staff_user,
        status=WorkItem.Status.DONE,
    )

    ctx = staff_dashboard_context(staff_user, facility)

    assert ctx["my_open_workitems_count"] == 0


def test_overdue_item_stays_first_even_when_created_before_the_undated_item(facility, staff_user, client_identified):
    """Pins x_staff_dashboard_context__mutmut_33 (``order_by(F("due_date").
    asc(nulls_last=True), "-created_at")`` -> ``order_by("-created_at")``).

    Der Bestandstest ``test_surfaces_overdue_workitems_first`` legt das
    ueberfaellige Item ZULETZT an — dort liefert auch ``-created_at`` allein
    dieselbe Reihenfolge. Hier entsteht das ueberfaellige Item ZUERST
    (aelterer ``created_at``), das terminlose Item DANACH: nur die
    Faelligkeits-Sortierung bringt das Ueberfaellige trotzdem nach vorn.
    """
    overdue = WorkItem.objects.create(
        facility=facility,
        title="Pin-098 ueberfaellig",
        assigned_to=staff_user,
        client=client_identified,
        created_by=staff_user,
        due_date=timezone.localdate() - timedelta(days=3),
    )
    WorkItem.objects.create(
        facility=facility,
        title="Pin-098 ohne Termin",
        assigned_to=staff_user,
        client=client_identified,
        created_by=staff_user,
    )

    ctx = staff_dashboard_context(staff_user, facility)

    assert ctx["my_open_workitems"][0].pk == overdue.pk


# ---------------------------------------------------------------------------
# super_admin_dashboard_context — kritische Audit-Zeile, Nutzerzahl
# ---------------------------------------------------------------------------


def test_critical_recent_counts_only_critical_actions_within_24h(facility, super_admin_user, staff_user):
    """Pins x_super_admin_dashboard_context__mutmut_8/11/12
    (``critical_recent`` fest auf ``None``, ``timestamp__gte=cutoff``
    entfaellt, ``action__in=critical_actions`` entfaellt).

    Drei Koeder-/Wahr-Kombinationen: unkritisch+frisch (faengt einen
    fehlenden Aktionsfilter), kritisch+ueber 24h alt (faengt eine fehlende
    Zeitgrenze), kritisch+frisch (die Wahrheit, zaehlt allein).

    Reihenfolge bewusst: die Audit-Kette (``assign_chain_fields``, Refs
    #1070) erzwingt strikt monoton steigende Zeitstempel je Facility und hebt
    einen ruecksichtigen ``timestamp`` sonst auf den Nachfolgewert der Kette
    an — der alte Eintrag muss deshalb der ERSTE der Facility-Kette sein,
    damit sein rueckdatierter Zeitstempel erhalten bleibt.
    """
    AuditLog.objects.create(
        facility=facility,
        user=staff_user,
        action=AuditLog.Action.DELETE,
        target_type="Client",
        target_id="pin098-alt",
        timestamp=timezone.now() - timedelta(hours=30),
    )
    AuditLog.objects.create(
        facility=facility,
        user=staff_user,
        action=AuditLog.Action.LOGIN,
        target_type="User",
        target_id=str(staff_user.pk),
    )
    AuditLog.objects.create(
        facility=facility,
        user=staff_user,
        action=AuditLog.Action.DELETE,
        target_type="Client",
        target_id="pin098-frisch",
    )

    ctx = super_admin_dashboard_context(super_admin_user)

    assert ctx["critical_recent"] == 1


def test_users_total_counts_only_active_users_globally(facility, super_admin_user):
    """Pins x_super_admin_dashboard_context__mutmut_18 (``User.objects.
    filter(is_active=True)`` -> ``filter(is_active=False)``).

    Deltamessung statt Absolutzahl: die Funktion zaehlt GLOBAL (kein
    Facility-Scope), der Vorbestand an Nutzern ist deshalb unbekannt. Zwei
    NEUE aktive Nutzer heben die Original-Zahl um genau 2 an; die gekippte
    Zaehlung (nur Inaktive) bliebe unveraendert, weil keine neue inaktive
    Person hinzukommt.
    """
    baseline = super_admin_dashboard_context(super_admin_user)["users_total"]

    User.objects.create_user(username="pin098-aktiv-a", password="x", facility=facility, role=User.Role.STAFF)
    User.objects.create_user(username="pin098-aktiv-b", password="x", facility=facility, role=User.Role.STAFF)

    ctx = super_admin_dashboard_context(super_admin_user)

    assert ctx["users_total"] == baseline + 2
