"""Pin-Tests der Welle K (0.98) — Prio-2-Testluecken in ``core.services.hubs``.

Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Batch ``hubs-dashboard`` der Survivor-Triage (``triage-096-hubs.json``).
Gepinnt werden vier Mutanten-Familien der Statuslisten-Kennzahlen aus
``datenschutz_overview``/``verwaltung_metrics`` (Konzept #1801 §4.2), die der
Bestand (``test_hub_services.py``) nicht abdeckt:

* **``label``/``detail`` als ``None``**: Django-Templates rendern ``None`` als
  leeren String — die betroffene Statuszeile bliebe sichtbar, aber ohne
  Zeilennamen bzw. Beisatz. Der Bestand prueft nur ``value``, nie den
  woertlichen Text.
* **``is_actionable``-Schwelle**: ``> 0`` wird zu ``>= 0``/``> 1`` verschoben,
  auf ``None`` gesetzt oder das Keyword faellt ganz weg (Dataclass-Default
  ``False``). Alle vier Varianten trennt ein Zwei-Punkte-Test (0 offene
  Vorgaenge ⇒ ``False``, genau 1 offener Vorgang ⇒ ``True``).
* **Facility-/Status-Filter der Berichtigungs-Zeile**: ``status=PENDING``,
  ``target_client__is_deleted=False`` und ``target_client__is_active=True``
  koennen je einzeln entfallen oder invertiert werden, ohne dass der Bestand
  (zufaellig 1 aktive + 1 inaktive Person) es bemerkt.
* **Aktiv-Zaehlung der Dokumentationstypen-Kachel**: ``Q(is_active=True)`` zu
  ``Q(is_active=False)`` gekippt — der Bestand nutzt zufaellig 1 aktiven + 1
  inaktiven Typ, wo beide Filterrichtungen dieselbe Zahl liefern.

``_date_line`` (die Datums-Zeile von Audit-/DSGVO-Kachel) bekommt einen
direkten Funktionstest: ``timezone.localtime(moment)`` -> ``timezone.
localtime(None)`` liefert das AKTUELLE Datum statt des von ``moment`` — am
Objekt selbst eindeutiger zu zeigen als ueber die Kachel.

Konventionen wie ``test_hub_services.py``/``test_mutation_pins_096_hubs.py``:
echte Model-Objekte statt Fakes, exakte Vergleiche, Fixtures aus
``src/tests/conftest.py``. ``_encryption_key`` liegt modulweit an (Lehre 112):
der Mutantenbaum hat keine ``.env``. Sprachkontext explizit per
``translation.override("de")`` bei woertlichen Textvergleichen — die
UI-Strings existieren auch als EN-Katalogeintrag (``src/locale/en/...``), ein
unspezifiziertes aktives Test-Locale waere sonst nicht deterministisch.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone, translation

from core.models import AuditLog, Client, DocumentType, ImportJob, RetentionProposal
from core.models.workitem import DeletionRequest
from core.services import hubs as hubs_module
from core.services.hubs import datenschutz_overview, verwaltung_metrics

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _metric(metrics, key):
    found = [m for m in metrics if m.key == key]
    assert found, f"Kennzahl {key!r} fehlt (vorhanden: {[m.key for m in metrics]})"
    return found[0]


# ---------------------------------------------------------------------------
# _date_line: eine Zeitzonen-Funktion, direkt getestet
# ---------------------------------------------------------------------------


def test_date_line_formats_the_given_moment_not_today():
    """Pins x__date_line__mutmut_9 (``timezone.localtime(moment)`` ->
    ``timezone.localtime(None)``).

    ``timezone.localtime(None)`` loest laut Django-Doku NICHT auf ``moment``
    auf, sondern auf das AKTUELLE lokale Datum (Default-Verhalten bei
    fehlendem Argument) — bei einem ``moment`` von vor 10 Tagen ist das
    eindeutig ein anderes Datum als heute.
    """
    from django.utils.formats import date_format

    from core.services.hubs import _date_line

    moment = timezone.now() - timedelta(days=10)
    expected = date_format(timezone.localtime(moment), "SHORT_DATE_FORMAT")
    today = date_format(timezone.localtime(timezone.now()), "SHORT_DATE_FORMAT")
    assert expected != today, "Sanity: die Testdaten muessen sich vom heutigen Datum unterscheiden."

    result = _date_line(moment, "am %(date)s", "nie")

    assert expected in result
    assert today not in result


# ---------------------------------------------------------------------------
# datenschutz_overview: label/detail-Wortlaut
# ---------------------------------------------------------------------------


def test_datenschutz_overview_labels_and_details_use_the_expected_wording(facility, admin_user):
    """Pins x_datenschutz_overview__mutmut_7/9/48/50/81/83/124/180 (``label=…``/
    ``detail=…`` -> ``None`` je Statuszeile).

    Ein Aufruf mit Admin-Rolle deckt alle fuenf Zeilen (drei Lead- + zwei
    Admin-only) in einem Rutsch ab; die Zaehlerstaende bleiben 0/``None``,
    hier zaehlt nur der woertliche Text.
    """
    with translation.override("de"):
        metrics = datenschutz_overview(facility, admin_user)

    assert _metric(metrics, "deletion-requests").label == "Löschanträge"
    assert _metric(metrics, "deletion-requests").detail == "warten auf Bestätigung"
    assert _metric(metrics, "rectification-requests").label == "Berichtigungen"
    assert _metric(metrics, "rectification-requests").detail == "offen"
    assert _metric(metrics, "retention").label == "Löschfristen"
    assert _metric(metrics, "retention").detail == "Löschvorschläge offen"
    assert _metric(metrics, "audit").label == "Audit-Log"
    assert _metric(metrics, "dsgvo").label == "DSGVO-Paket"


# ---------------------------------------------------------------------------
# datenschutz_overview: is_actionable-Schwelle (drei Zeilen)
# ---------------------------------------------------------------------------


class TestDatenschutzIsActionableThresholds:
    """``is_actionable`` steuert nur ``{% if metric.is_actionable %}`` — die
    Schwelle ist ``> 0`` und muss bei 0 ``False``, bei 1 ``True`` sein. Das
    trennt alle vier Mutationsarten je Zeile: ``None`` und das entfernte
    Keyword (Dataclass-Default ``False``) scheitern schon am ``is False``-Fall
    (0 Vorgaenge), ``>= 0`` scheitert dort ebenfalls (liefert ``True`` statt
    ``False``); ``> 1`` scheitert erst am ``is True``-Fall (1 Vorgang).
    """

    def test_deletion_requests_is_actionable_flips_at_the_first_open_request(self, facility, admin_user, staff_user):
        """Pins x_datenschutz_overview__mutmut_12/19/34/35."""
        assert _metric(datenschutz_overview(facility, admin_user), "deletion-requests").is_actionable is False

        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=uuid.uuid4(),
            reason="Pin-098",
            requested_by=staff_user,
            status=DeletionRequest.Status.PENDING,
        )

        assert _metric(datenschutz_overview(facility, admin_user), "deletion-requests").is_actionable is True

    def test_rectification_requests_is_actionable_flips_at_the_first_open_request(
        self, facility, admin_user, staff_user, client_identified
    ):
        """Pins x_datenschutz_overview__mutmut_53/60/74/75."""
        from core.services.client.rectification import request_rectification

        assert _metric(datenschutz_overview(facility, admin_user), "rectification-requests").is_actionable is False

        request_rectification(client_identified, staff_user, "notes", "Pin-098-Wert", "Pin-098-Grund")

        assert _metric(datenschutz_overview(facility, admin_user), "rectification-requests").is_actionable is True

    def test_retention_is_actionable_flips_at_the_first_open_proposal(self, facility, admin_user):
        """Pins x_datenschutz_overview__mutmut_86/93/108/109."""
        assert _metric(datenschutz_overview(facility, admin_user), "retention").is_actionable is False

        RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=uuid.uuid4(),
            deletion_due_at=timezone.localdate(),
            retention_category="anonymous",
            status=RetentionProposal.Status.PENDING,
        )

        assert _metric(datenschutz_overview(facility, admin_user), "retention").is_actionable is True


# ---------------------------------------------------------------------------
# datenschutz_overview: Berichtigungs-Zeile — drei unabhaengige Filterklauseln
# ---------------------------------------------------------------------------


def test_rectifications_count_applies_status_deletion_and_active_filters_independently(
    facility, admin_user, staff_user
):
    """Pins x_datenschutz_overview__mutmut_40/41/45 (``status=PENDING``
    entfernt, ``target_client__is_deleted=False`` entfernt,
    ``target_client__is_active=True`` -> ``False``).

    Vier Klienten, drei Koeder: ein ENTSCHIEDENER Antrag (faengt einen
    fehlenden Status-Filter), ein offener Antrag einer GELOESCHTEN Person
    (faengt einen fehlenden ``is_deleted``-Filter) und ein offener Antrag
    einer INAKTIVEN, aber nicht geloeschten Person (faengt den geflippten
    ``is_active``-Filter) — jeder Koeder aendert die Zielzahl (2) in eine
    ANDERE falsche Zahl, sodass jede der drei Mutationen einzeln auffliegt.
    """
    from core.services.client.rectification import request_rectification

    def _make_client(pseudonym: str) -> Client:
        return Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym=pseudonym,
            created_by=staff_user,
        )

    # Die Wahrheit: zwei offene Antraege aktiver, nicht geloeschter Personen.
    for i in range(2):
        wahr = _make_client(f"PIN098-WAHR-{i}")
        request_rectification(wahr, staff_user, "notes", "Pin-098-Wert", "Pin-098-Grund")

    # Koeder fuer mutmut_40: ein ENTSCHIEDENER Antrag einer aktiven Person.
    entschieden = _make_client("PIN098-ENTSCHIEDEN")
    request_rectification(entschieden, staff_user, "notes", "Pin-098-Wert", "Pin-098-Grund")
    from core.models import RectificationRequest

    RectificationRequest.objects.filter(target_client=entschieden).update(status=RectificationRequest.Status.APPROVED)

    # Koeder fuer mutmut_41: ein offener Antrag einer GELOESCHTEN Person.
    geloescht = _make_client("PIN098-GELOESCHT")
    request_rectification(geloescht, staff_user, "notes", "Pin-098-Wert", "Pin-098-Grund")
    Client.objects.filter(pk=geloescht.pk).update(is_deleted=True)

    # Koeder fuer mutmut_45: ein offener Antrag einer INAKTIVEN Person.
    inaktiv = _make_client("PIN098-INAKTIV")
    request_rectification(inaktiv, staff_user, "notes", "Pin-098-Wert", "Pin-098-Grund")
    Client.objects.filter(pk=inaktiv.pk).update(is_active=False)

    assert _metric(datenschutz_overview(facility, admin_user), "rectification-requests").value == 2


# ---------------------------------------------------------------------------
# datenschutz_overview: DSGVO-Zeile — Aktionsfilter
# ---------------------------------------------------------------------------


def test_dsgvo_row_ignores_non_export_actions_on_the_matching_target_type(facility, admin_user):
    """Pins x_datenschutz_overview__mutmut_168 (``action=AuditLog.Action.
    EXPORT,`` entfaellt aus dem Query-Filter).

    Gegenstueck zum Bestandstest ``test_dsgvo_row_ignores_other_exports``
    (der den ``target_type``-Filter deckt): hier passt der ``target_type``
    exakt, aber die Aktion ist kein EXPORT — der Mutant wuerde sie trotzdem
    als Download werten.
    """
    AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
        target_type="DSGVO-Dokument",
        target_id="vvt",
    )

    row = _metric(datenschutz_overview(facility, admin_user), "dsgvo")

    assert row.value is None
    assert not any(char.isdigit() for char in row.detail), (
        f"Eine Nicht-EXPORT-Aktion darf kein Download-Datum vortaeuschen: {row.detail!r}"
    )


# ---------------------------------------------------------------------------
# verwaltung_metrics: label/detail-Wortlaut
# ---------------------------------------------------------------------------


def test_verwaltung_metrics_labels_and_details_use_the_expected_wording(facility):
    """Pins x_verwaltung_metrics__mutmut_26/58/60/83/85/113 (``label=…``/
    ``detail=…`` -> ``None`` je Statuszeile)."""
    with translation.override("de"):
        metrics = verwaltung_metrics(facility)

    assert _metric(metrics, "config").label == "Dokumentationstypen"
    assert _metric(metrics, "config-fieldtemplates").label == "Feldvorlagen"
    assert _metric(metrics, "config-fieldtemplates").detail == "angelegt"
    assert _metric(metrics, "import").label == "Datenimport"
    assert _metric(metrics, "import").detail == "Importläufe offen"
    assert _metric(metrics, "storage").label == "Speicher"


# ---------------------------------------------------------------------------
# verwaltung_metrics: Dokumentationstypen — Aktiv-Zaehlung
# ---------------------------------------------------------------------------


def test_config_active_count_counts_active_not_inactive_doctypes(facility):
    """Pins x_verwaltung_metrics__mutmut_17 (``Q(is_active=True)`` ->
    ``Q(is_active=False)``).

    Zwei aktive und ein inaktiver Dokumentationstyp: der Bestandstest
    (``test_hub_services.py``) verwendet zufaellig 1 aktiv + 1 inaktiv, wo
    beide Filterrichtungen dieselbe Zahl (1) liefern.
    """
    DocumentType.objects.create(facility=facility, name="Pin-098 aktiv 1")
    DocumentType.objects.create(facility=facility, name="Pin-098 aktiv 2")
    DocumentType.objects.create(facility=facility, name="Pin-098 inaktiv", is_active=False)

    with translation.override("de"):
        metric = _metric(verwaltung_metrics(facility), "config")

    assert metric.value == 3
    assert metric.detail == "davon 2 aktiv"


# ---------------------------------------------------------------------------
# verwaltung_metrics: is_actionable (Import- und Speicher-Zeile)
# ---------------------------------------------------------------------------


def test_import_is_actionable_flips_at_the_first_open_run(facility, admin_user):
    """Pins x_verwaltung_metrics__mutmut_88/95/110/111 (``is_actionable``:
    ``None``, entferntes Keyword (Default ``False``), ``>= 0``, ``> 1``)."""
    assert _metric(verwaltung_metrics(facility), "import").is_actionable is False

    ImportJob.objects.create(facility=facility, created_by=admin_user, target_type=ImportJob.TargetType.CLIENT)

    assert _metric(verwaltung_metrics(facility), "import").is_actionable is True


def test_storage_is_actionable_reflects_the_near_limit_flag(facility, monkeypatch):
    """Pins x_verwaltung_metrics__mutmut_118/125 (``is_actionable=storage.
    is_near_limit`` -> ``None`` bzw. entferntes Keyword (Default ``False``)).

    ``facility_storage_usage`` echt ueber Attachments zu fuellen waere ein
    Upload-Umweg fuer eine reine Kennzahlen-Frage — die Funktion wird im
    ``hubs``-Modulnamespace durch einen festen, ueber der Warnschwelle
    liegenden ``StorageUsage`` ersetzt (Muster wie in anderen Pin-Dateien:
    ``monkeypatch.setattr`` auf den importierten Namen).
    """
    from core.services.file_vault.quota import StorageUsage

    near_limit = StorageUsage(used_bytes=95, trash_bytes=0, limit_bytes=100)
    monkeypatch.setattr(hubs_module, "facility_storage_usage", lambda facility: near_limit)
    assert near_limit.is_near_limit is True, "Sanity: der Fixture-Wert muss ueber der Warnschwelle liegen."

    assert _metric(verwaltung_metrics(facility), "storage").is_actionable is True
