"""Freigabe-Übersicht herausgegebener Berichtszeiträume (K4a, Refs #1770).

K4a ist **keine Lücke im Verfahren, sondern seine Definitionsgrenze**: die
k-Anonymitäts-Zusicherung gilt je Artefakt und je Zeitraum. Aus verschachtelten
Freigaben (Monatsberichte plus Jahresbericht) lässt sich jede unterdrückte Zelle
exakt zurückrechnen — auch dann, wenn in keinem der beteiligten Berichte etwas
unterdrückt wurde. Artefakt-lokal gibt es dagegen nichts zu unterdrücken.

Die Übersicht verhindert das nicht, sie macht es sichtbar. Getestet werden die
drei Auflagen der Analyse: Empfänger-/Zweckfeld am ``EXPORT``-Detail,
Überlappungswarnung, und dass die Historie nicht von
``auditlog_retention_months`` gekappt wird.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog
from core.services.dashboard.release_log import (
    RELEASE_FIELD_MAX_LENGTH,
    build_release_overview,
    release_detail,
)


def _export(facility, user, date_from, date_to, **detail):
    return AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.EXPORT,
        target_type="PDF",
        detail={"format": "PDF", "date_from": date_from, "date_to": date_to, **detail},
    )


class TestEmpfaengerUndZweckfeld:
    """Auflage 1: ohne sie erzeugt jede Überschneidung einen Alarm ohne Aussage."""

    def test_leere_angaben_erzeugen_kein_feld(self):
        """Ein ``{"recipient": ""}`` behauptete eine Angabe, die es nicht gibt —
        und liesse sich später nicht von „Feld gab es damals noch nicht"
        unterscheiden."""
        assert release_detail({"recipient": "  ", "purpose": ""}) == {}

    def test_angaben_werden_uebernommen_und_getrimmt(self):
        assert release_detail({"recipient": " Stadt Musterstadt ", "purpose": "Nachweis"}) == {
            "recipient": "Stadt Musterstadt",
            "purpose": "Nachweis",
        }

    def test_freitext_wird_gekappt(self):
        """Der Audit-Log ist append-only und überlebt seit K4a die Löschfristen —
        eine unbedachte Eingabe ist nachträglich nicht redigierbar."""
        detail = release_detail({"recipient": "x" * 500})

        assert len(detail["recipient"]) == RELEASE_FIELD_MAX_LENGTH


@pytest.mark.django_db
class TestExportViewsSchreibenEmpfaengerUndZweck:
    """Alle EXPORT-auditierenden Berichts-Views reichen die Felder durch."""

    def _letzter_export(self, facility):
        return (
            AuditLog.objects.for_facility(facility).filter(action=AuditLog.Action.EXPORT).order_by("-timestamp").first()
        )

    @pytest.mark.parametrize(
        ("url_name", "params"),
        [
            ("core:statistics_csv_export", {}),
            ("core:statistics_external_report", {"period": "custom"}),
            ("core:statistics_report_export", {"backend": "sample-report-1", "format": "csv"}),
        ],
    )
    def test_export_schreibt_die_freigabe_angaben_ins_audit_detail(self, client, lead_user, facility, url_name, params):
        client.force_login(lead_user)

        response = client.get(
            reverse(url_name),
            {
                "date_from": "2026-01-01",
                "date_to": "2026-06-30",
                "recipient": "Stadt Musterstadt",
                "purpose": "Verwendungsnachweis 2026",
                **params,
            },
        )

        assert response.status_code == 200
        detail = self._letzter_export(facility).detail
        assert detail["recipient"] == "Stadt Musterstadt"
        assert detail["purpose"] == "Verwendungsnachweis 2026"

    def test_ohne_angabe_bleibt_das_detail_unveraendert(self, client, lead_user, facility):
        """Die Felder sind optional — ein Pflichtfeld erzwänge eine Angabe auch
        dort, wo der Export gar keine Herausgabe ist (Probelauf)."""
        client.force_login(lead_user)

        client.get(reverse("core:statistics_csv_export"), {"date_from": "2026-01-01", "date_to": "2026-06-30"})

        detail = self._letzter_export(facility).detail
        assert "recipient" not in detail and "purpose" not in detail


@pytest.mark.django_db
class TestUeberlappungswarnung:
    """Auflage 2: der eigentliche Zweck der Übersicht."""

    def test_verschachtelte_zeitraeume_werden_gemeldet(self, facility, lead_user):
        """Der Regelfall des externen Berichtswesens: Monatsbericht plus
        Jahresbericht. Aus der Jahressumme minus den elf sichtbaren
        Monatszellen ergibt sich die im zwölften unterdrückte Zelle."""
        _export(facility, lead_user, "2026-01-01", "2026-12-31", recipient="Träger")
        _export(facility, lead_user, "2026-03-01", "2026-03-31", recipient="Träger")

        releases = build_release_overview(facility)

        assert all(release.overlaps for release in releases)

    def test_identische_zeitraeume_sind_kein_befund(self, facility, lead_user):
        """Aus zwei Berichten über denselben Zeitraum lässt sich keine Differenz
        bilden — der zweite ist typischerweise ein erneuter Download. Ohne diese
        Einschränkung produzierte die Übersicht Alarme ohne Aussage."""
        _export(facility, lead_user, "2026-01-01", "2026-06-30")
        _export(facility, lead_user, "2026-01-01", "2026-06-30")

        releases = build_release_overview(facility)

        assert not any(release.overlaps for release in releases)

    def test_disjunkte_zeitraeume_sind_kein_befund(self, facility, lead_user):
        _export(facility, lead_user, "2026-01-01", "2026-06-30")
        _export(facility, lead_user, "2026-07-01", "2026-12-31")

        releases = build_release_overview(facility)

        assert not any(release.overlaps for release in releases)

    def test_teilueberlappung_wird_gemeldet(self, facility, lead_user):
        _export(facility, lead_user, "2026-01-01", "2026-06-30")
        _export(facility, lead_user, "2026-05-01", "2026-09-30")

        releases = build_release_overview(facility)

        assert all(release.overlaps for release in releases)

    def test_eintraege_ohne_zeitraum_werden_nicht_gemeldet(self, facility, lead_user):
        """Ältere EXPORT-Einträge (z. B. Klienten-Datenauskunft) tragen keinen
        Berichtszeitraum — sie sind keine Freigabe im Sinne von K4a."""
        AuditLog.objects.create(
            facility=facility, user=lead_user, action=AuditLog.Action.EXPORT, target_type="Client", detail={}
        )
        _export(facility, lead_user, "2026-01-01", "2026-12-31")

        releases = build_release_overview(facility)

        assert not any(release.overlaps for release in releases)

    def test_uebersicht_ist_facility_gescopt(self, facility, second_facility, lead_user, second_facility_user):
        _export(facility, lead_user, "2026-01-01", "2026-12-31")
        _export(second_facility, second_facility_user, "2026-03-01", "2026-03-31")

        releases = build_release_overview(facility)

        assert len(releases) == 1
        assert not releases[0].overlaps, "eine fremde Einrichtung darf nie in die Warnung eingehen"


@pytest.mark.django_db
class TestFreigabeUebersichtView:
    def test_seite_zeigt_die_herausgaben(self, client, lead_user, facility):
        _export(facility, lead_user, "2026-01-01", "2026-12-31", recipient="Stadt Musterstadt")
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_releases"))

        assert response.status_code == 200
        assert "Stadt Musterstadt" in response.content.decode()

    def test_seite_warnt_bei_ueberschneidung(self, client, lead_user, facility):
        _export(facility, lead_user, "2026-01-01", "2026-12-31")
        _export(facility, lead_user, "2026-03-01", "2026-03-31")
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_releases"))

        assert "release-overlap" in response.content.decode()

    def test_ohne_herausgabe_bleibt_die_seite_ruhig(self, client, lead_user):
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_releases"))

        content = response.content.decode()
        assert "release-overlap" not in content
        assert "Noch keine Herausgabe protokolliert." in content

    def test_staff_darf_nicht(self, client, staff_user):
        """Rollen-Guard wie das Berichts-Panel selbst."""
        client.force_login(staff_user)

        response = client.get(reverse("core:statistics_report_releases"))

        assert response.status_code == 403


@pytest.mark.django_db(transaction=True)
class TestExportUeberlebtDieAuditRetention:
    """Auflage 3: sonst täuscht die Übersicht Vollständigkeit vor."""

    def test_export_ist_prune_exempt(self, facility, settings_obj, lead_user):
        from django.db import connection

        from core.retention.audit_pruning import PRUNE_EXEMPT_ACTIONS, prune_auditlog

        if connection.vendor != "postgresql":
            pytest.skip("prune_auditlog erfordert PostgreSQL")

        assert AuditLog.Action.EXPORT in PRUNE_EXEMPT_ACTIONS

        settings_obj.auditlog_retention_months = 24
        settings_obj.save()
        now = timezone.now()
        alt = _export(facility, lead_user, "2020-01-01", "2020-12-31")
        generisch = AuditLog.objects.create(facility=facility, user=lead_user, action=AuditLog.Action.LOGIN)
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable")
            cur.execute(
                "UPDATE core_auditlog SET timestamp = %s WHERE id IN (%s, %s)",
                [now - timedelta(days=24 * 31), str(alt.pk), str(generisch.pk)],
            )
            cur.execute("ALTER TABLE core_auditlog ENABLE ALWAYS TRIGGER auditlog_immutable")

        result = prune_auditlog(facility, settings_obj, now=now, dry_run=False)

        assert result["count"] == 1, "nur der generische LOGIN-Eintrag darf fallen"
        assert AuditLog.objects.filter(pk=alt.pk).exists(), (
            "Ein gekappter EXPORT-Eintrag laesst die Ueberlappungswarnung stumm bleiben — "
            "ein stiller falscher Negativ, schlechter als laengere Aufbewahrung."
        )

    def test_die_uebersicht_findet_den_alten_eintrag_noch(self, facility, lead_user):
        _export(facility, lead_user, "2020-01-01", "2020-12-31")

        releases = build_release_overview(facility)

        assert releases[0].date_from == date(2020, 1, 1)
