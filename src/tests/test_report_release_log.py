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
    EXTERNAL_REPORT_TARGET,
    RELEASE_FIELD_MAX_LENGTH,
    build_release_overview,
    release_detail,
)


def _export(facility, user, date_from, date_to, **detail):
    """Ein **herausgegebenes Artefakt** (Download) — die Freigabe-Menge."""
    from core.services.dashboard.release_log import RELEASE_ARTIFACT_KEY

    return AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.EXPORT,
        target_type="PDF",
        detail={
            "format": "PDF",
            RELEASE_ARTIFACT_KEY: True,
            "date_from": date_from,
            "date_to": date_to,
            **detail,
        },
    )


def _alt_zeile_vor_marker_einfuehrung(facility, user, date_from, date_to):
    """Eine Download-Zeile aus der Zeit **vor** dem Artefakt-Marker.

    Bewusst roh ueber ``AuditLog.objects.create`` konstruiert, NICHT ueber
    ``_export`` — genau so sehen die Bestandszeilen nach dem Deploy aus:
    Berichtszeitraum ja, Download-Format ja, Marker nein (den gab es damals
    nicht). Nachtraeglich markieren laesst sich das nicht (append-only +
    HMAC-Kette, siehe Modul-Docstring von ``release_log``), also muss das
    Verhalten fail-safe und **bekannt** sein.
    """
    return AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.EXPORT,
        target_type="PDF",
        detail={"format": "PDF", "date_from": date_from, "date_to": date_to},
    )


def _bildschirm_aufruf(facility, user, date_from, date_to, fmt="html"):
    """Ein On-Screen-Aufruf des externen Berichts — **keine** Herausgabe.

    Dieselbe Aktion, derselbe Zeitraum im ``detail``, aber kein Artefakt hat das
    Haus verlassen. Jeder Reload erzeugt so eine Zeile. ``fmt="json"`` ist
    dieselbe Ansicht in maschinenlesbar (Refs #1772).
    """
    return AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.EXPORT,
        target_type=EXTERNAL_REPORT_TARGET,
        detail={"format": fmt, "date_from": date_from, "date_to": date_to},
    )


def _json_ansicht_vor_der_abwertung(facility, user, date_from, date_to):
    """Eine ``?format=json``-Zeile aus der Zeit **vor** der Abwertung (#1772).

    Sie traegt den Freigabe-Marker, weil die View ihn damals gesetzt hat.
    Nachtraeglich entfernen laesst er sich nicht (append-only + HMAC-Kette) —
    die Abwertung muss also am **Lesepfad** greifen.
    """
    from core.services.dashboard.release_log import RELEASE_ARTIFACT_KEY

    return AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.EXPORT,
        target_type=EXTERNAL_REPORT_TARGET,
        detail={"format": "json", RELEASE_ARTIFACT_KEY: True, "date_from": date_from, "date_to": date_to},
    )


class TestEmpfaengerUndZweckfeld:
    """Auflage 1: ohne sie erzeugt jede Überschneidung einen Alarm ohne Aussage."""

    def test_leere_angaben_erzeugen_kein_feld(self):
        """Ein ``{"recipient": ""}`` behauptete eine Angabe, die es nicht gibt —
        und liesse sich später nicht von „Feld gab es damals noch nicht"
        unterscheiden."""
        assert release_detail({"recipient": "  ", "purpose": ""}, artifact_released=False) == {}

    def test_angaben_werden_uebernommen_und_getrimmt(self):
        assert release_detail({"recipient": " Stadt Musterstadt ", "purpose": "Nachweis"}, artifact_released=False) == {
            "recipient": "Stadt Musterstadt",
            "purpose": "Nachweis",
        }

    def test_freitext_wird_gekappt(self):
        """Der Audit-Log ist append-only und überlebt seit K4a die Löschfristen —
        eine unbedachte Eingabe ist nachträglich nicht redigierbar."""
        detail = release_detail({"recipient": "x" * 500}, artifact_released=False)

        assert len(detail["recipient"]) == RELEASE_FIELD_MAX_LENGTH

    def test_der_freigabe_marker_ist_ein_pflicht_keyword(self):
        """Ohne Default: jede EXPORT-auditierende Aufrufstelle muss ausdrücklich
        sagen, ob bei ihr ein Artefakt das Haus verlassen hat. Ein Default wäre
        in beide Richtungen falsch (stille Dauer-Aufbewahrung von Ansichten bzw.
        stiller Ausfall eines neuen Download-Pfads aus der Übersicht)."""
        with pytest.raises(TypeError):
            release_detail({})

    def test_nur_mit_marker_entsteht_der_freigabe_schluessel(self):
        from core.services.dashboard.release_log import RELEASE_ARTIFACT_KEY

        assert release_detail({}, artifact_released=True) == {RELEASE_ARTIFACT_KEY: True}


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

    @pytest.mark.parametrize(
        ("url_name", "params", "ist_freigabe"),
        [
            ("core:statistics_csv_export", {}, True),
            ("core:statistics_pdf_export", {}, True),
            ("core:statistics_sample_report_1_export", {}, True),
            ("core:statistics_report_export", {"backend": "sample-report-1", "format": "csv"}, True),
            # Refs #1772 (Punkt 2): der externe Bericht ist in BEIDEN Formaten
            # nur eine Ansicht — ``?format=json`` hat keine Empfaenger-/
            # Zweckfelder und keine Download-Geste, sondern ist die
            # maschinenlesbare Fassung derselben Bildschirmseite.
            ("core:statistics_external_report", {"period": "custom", "format": "json"}, False),
            ("core:statistics_external_report", {"period": "custom"}, False),
        ],
    )
    def test_nur_herausgegebene_artefakte_tragen_den_freigabe_marker(
        self, client, lead_user, facility, url_name, params, ist_freigabe
    ):
        """Design-Constraint: echte Artefakt-Downloads sind Freigaben,
        Bildschirm-Ansichten nicht — und zwar an EINER Definition
        (``RELEASE_EXPORT_Q``), die Retention und Uebersicht teilen."""
        from core.services.dashboard.release_log import RELEASE_ARTIFACT_KEY

        client.force_login(lead_user)

        client.get(reverse(url_name), {"date_from": "2026-01-01", "date_to": "2026-06-30", **params})

        detail = self._letzter_export(facility).detail
        assert detail.get(RELEASE_ARTIFACT_KEY, False) is ist_freigabe, detail

    @pytest.mark.parametrize("params", [{}, {"format": "json"}])
    def test_bildschirmaufruf_landet_nicht_in_der_uebersicht(self, client, lead_user, facility, params):
        """Beide Formate der externen Ansicht bleiben draussen (Refs #1772)."""
        client.force_login(lead_user)

        client.get(reverse("core:statistics_external_report"), {"period": "custom", **params})

        assert build_release_overview(facility) == []

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

    def test_eintraege_ohne_zeitraum_werden_gar_nicht_gelistet(self, facility, lead_user):
        """``EXPORT`` ist die **Sammelklasse**: darunter fallen auch die
        Art.-15-Datenauskunft einer Person und die DSGVO-Dokument-Downloads.
        Die Übersicht ist eine Freigabe-Liste für **Berichtszeiträume** — solche
        Einträge gehören dort nicht hin, und zwar nicht bloß „ohne Warnung",
        sondern gar nicht: sonst zeigte eine Seite mit LEAD_PLUS-Guard
        Audit-Metadaten, die `/audit/` nur der Einrichtungsverwaltung zeigt.
        """
        AuditLog.objects.create(
            facility=facility,
            user=lead_user,
            action=AuditLog.Action.EXPORT,
            target_type="Client",
            target_id="1f0d6b1e-0000-0000-0000-000000000000",
            detail={"format": "JSON"},
        )
        _export(facility, lead_user, "2026-01-01", "2026-12-31")

        releases = build_release_overview(facility)

        assert len(releases) == 1, "nur Herausgaben MIT Berichtszeitraum gehoeren in die Uebersicht"
        assert releases[0].has_period

    def test_uebersicht_nennt_keine_handelnde_person(self, facility, lead_user):
        """Datensparsamkeit: Für den Zweck („welche Zeiträume sind draußen?")
        ist die handelnde Person irrelevant; die Rechenschaft trägt der
        Audit-Log selbst. Sie hier zu zeigen, öffnete Leitungen eine
        Beschäftigten-Metadatensicht, die `/audit/` (ADMIN_ONLY) ihnen
        vorenthält."""
        import dataclasses

        _export(facility, lead_user, "2026-01-01", "2026-12-31")

        releases = build_release_overview(facility)

        felder = {field.name for field in dataclasses.fields(releases[0])}
        assert "user" not in felder and "actor" not in felder, felder

    def test_bildschirmaufruf_ist_keine_freigabe(self, facility, lead_user):
        """Der externe Bericht auditiert **jeden** HTML-GET — jeder Reload.

        Solche Zeilen als Herausgabe zu fuehren, waere in zwei Richtungen
        falsch: sie verdraengen bei ``RELEASE_LOG_LIMIT`` echte Downloads aus
        dem Auswertungsfenster (stiller falscher Negativ genau in der Warnung,
        die die Retention-Ausnahme absichern soll), und sie behaupten eine
        Herausgabe, die nicht stattgefunden hat.
        """
        _bildschirm_aufruf(facility, lead_user, "2026-01-01", "2026-12-31")
        _export(facility, lead_user, "2026-03-01", "2026-03-31")

        releases = build_release_overview(facility)

        assert len(releases) == 1
        assert releases[0].date_from == date(2026, 3, 1)
        assert not releases[0].overlaps, "ein Bildschirmaufruf darf keine Ueberlappung ausloesen"

    def test_json_ansicht_ist_keine_freigabe(self, facility, lead_user):
        """Refs #1772 (Punkt 2): ``?format=json`` ist Ansicht, nicht Herausgabe.

        Die Route hat weder Empfaenger-/Zweckfelder noch eine Download-Geste;
        sie ist die maschinenlesbare Fassung derselben Bildschirmseite und wird
        wie diese bei **jedem** Aufruf auditiert. Als Freigabe gefuehrt haette
        sie dieselben zwei Folgen wie die HTML-Ansicht: unbegrenztes,
        prune-exemptes Wachstum und Verdraengung echter Downloads aus dem
        ``RELEASE_LOG_LIMIT``-Fenster.
        """
        _bildschirm_aufruf(facility, lead_user, "2026-01-01", "2026-12-31", fmt="json")
        _export(facility, lead_user, "2026-03-01", "2026-03-31")

        releases = build_release_overview(facility)

        assert len(releases) == 1
        assert releases[0].date_from == date(2026, 3, 1)
        assert not releases[0].overlaps, "eine JSON-Ansicht darf keine Ueberlappung ausloesen"

    def test_json_altzeilen_mit_marker_sind_nicht_mehr_gelistet(self, facility, lead_user):
        """Die Uebersicht zeigt kuenftig **nur echte** Freigaben — auch rueckwirkend.

        Zeilen aus der Zeit vor der Abwertung tragen den Marker; entfernen
        laesst er sich nicht (append-only + HMAC-Kette). Die Abwertung greift
        deshalb am Lesepfad: ``RELEASE_EXPORT_Q`` schliesst die eindeutig
        erkennbare Gestalt dieser Zeilen aus (Ziel ``ExternalReport`` +
        ``format="json"``). Die Zeilen selbst bleiben im Log — als normale,
        befristete Audit-Zeilen.
        """
        _json_ansicht_vor_der_abwertung(facility, lead_user, "2026-01-01", "2026-12-31")
        _export(facility, lead_user, "2026-03-01", "2026-03-31")

        releases = build_release_overview(facility)

        assert len(releases) == 1
        assert releases[0].date_from == date(2026, 3, 1)

    def test_json_export_der_backend_pipeline_bleibt_eine_freigabe(self, facility, lead_user):
        """Abgrenzung: der generische Berichts-Download ist ein Artefakt.

        Er traegt dasselbe ``format="json"``, aber ein anderes Ziel — die
        Ausnahme haengt an BEIDEM, sonst nimmt sie echte Downloads mit.
        """
        AuditLog.objects.create(
            facility=facility,
            user=lead_user,
            action=AuditLog.Action.EXPORT,
            target_type="DatasetExport",
            detail={
                "backend": "sample-report-1",
                "format": "json",
                "artifact_released": True,
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
            },
        )

        releases = build_release_overview(facility)

        assert [release.date_from for release in releases] == [date(2026, 1, 1)]

    def test_altzeilen_vor_der_marker_einfuehrung_sind_nicht_gelistet(self, facility, lead_user):
        """**Rollout-Grenze, bewusst so und offengelegt.** Der Artefakt-Marker
        ist neu; keine vor dem Deploy geloggte Zeile traegt ihn, und
        nachtraeglich markieren laesst sich nichts (append-only + HMAC-Kette).

        Die Uebersicht zeigt deshalb Freigaben **ab Einfuehrung der Markierung**
        — die Seite sagt das ausdruecklich, statt Vollstaendigkeit zu
        behaupten. Fail-safe ist die Richtung richtig (lieber eine Freigabe zu
        wenig zeigen als eine erfinden); der Preis ist eine
        Ueberlappungswarnung, die gegen Alt-Freigaben nicht feuern kann.
        """
        _alt_zeile_vor_marker_einfuehrung(facility, lead_user, "2026-01-01", "2026-12-31")
        _export(facility, lead_user, "2026-03-01", "2026-03-31")

        releases = build_release_overview(facility)

        assert len(releases) == 1
        assert releases[0].date_from == date(2026, 3, 1)
        assert not releases[0].overlaps, "gegen eine Alt-Freigabe kann die Warnung nicht feuern"

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

    def test_seite_nennt_die_mengen_grenze_der_auswertung(self, client, lead_user, facility):
        """Refs #1772 (Punkt 3): dritter blinder Fleck, bisher unbenannt.

        Die Seite wies die Rollout-Grenze und die Weitergabe auf anderem Weg
        aus, nicht aber ``RELEASE_LOG_LIMIT``: aeltere Freigaben fallen aus dem
        Auswertungsfenster und koennen keine Ueberschneidung mehr ausloesen.
        Eine Grenze, die niemand kennt, ist keine ehrliche Grenze.
        """
        from core.services.dashboard.release_log import RELEASE_LOG_LIMIT

        _export(facility, lead_user, "2026-01-01", "2026-12-31")
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_releases"))

        inhalt = response.content.decode()
        assert response.context["release_limit"] == RELEASE_LOG_LIMIT
        assert str(RELEASE_LOG_LIMIT) in inhalt

    def test_staff_darf_nicht(self, client, staff_user):
        """Rollen-Guard wie das Berichts-Panel selbst."""
        client.force_login(staff_user)

        response = client.get(reverse("core:statistics_report_releases"))

        assert response.status_code == 403


@pytest.mark.django_db(transaction=True)
class TestExportUeberlebtDieAuditRetention:
    """Auflage 3: sonst täuscht die Übersicht Vollständigkeit vor."""

    def _veralten(self, now, *entries):
        from django.db import connection

        ids = [str(entry.pk) for entry in entries]
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable")
            cur.execute(
                f"UPDATE core_auditlog SET timestamp = %s WHERE id IN ({', '.join(['%s'] * len(ids))})",
                [now - timedelta(days=24 * 31), *ids],
            )
            cur.execute("ALTER TABLE core_auditlog ENABLE ALWAYS TRIGGER auditlog_immutable")

    def test_nur_berichts_freigaben_sind_prune_exempt(self, facility, settings_obj, lead_user):
        """Die Ausnahme ist **so eng wie ihre Begründung**.

        ``EXPORT`` ist die Sammelklasse: darunter laufen auch die
        Art.-15-Datenauskunft einer Person (``target_id`` = Klienten-UUID) und
        die DSGVO-Dokument-Downloads. Deren ``detail`` ist im PII-Sweep als
        personenbeziehbar klassifiziert — sie dauerhaft von der Frist zu
        befreien, wäre eine Speicherbegrenzungs-Regression und **nicht** von
        der K4a-Zweckbindung („Nachweis herausgegebener Statistiken") gedeckt.
        Ausgenommen ist deshalb nur, was einen **Berichtszeitraum** trägt.
        """
        from django.db import connection

        from core.retention.audit_pruning import prune_auditlog

        if connection.vendor != "postgresql":
            pytest.skip("prune_auditlog erfordert PostgreSQL")

        settings_obj.auditlog_retention_months = 24
        settings_obj.save()
        now = timezone.now()
        freigabe = _export(facility, lead_user, "2020-01-01", "2020-12-31")
        klientenexport = AuditLog.objects.create(
            facility=facility,
            user=lead_user,
            action=AuditLog.Action.EXPORT,
            target_type="Client",
            target_id="1f0d6b1e-0000-0000-0000-000000000000",
            detail={"format": "JSON"},
        )
        generisch = AuditLog.objects.create(facility=facility, user=lead_user, action=AuditLog.Action.LOGIN)
        self._veralten(now, freigabe, klientenexport, generisch)

        result = prune_auditlog(facility, settings_obj, now=now, dry_run=False)

        assert result["count"] == 2, "LOGIN und Klientenexport fallen, die Berichts-Freigabe nicht"
        assert AuditLog.objects.filter(pk=freigabe.pk).exists(), (
            "Ein gekappter Freigabe-Eintrag laesst die Ueberlappungswarnung stumm bleiben — "
            "ein stiller falscher Negativ, schlechter als laengere Aufbewahrung."
        )
        assert not AuditLog.objects.filter(pk=klientenexport.pk).exists(), (
            "Ein Art.-15-Klientenexport ist keine Berichts-Freigabe und bleibt der Frist unterworfen."
        )
        assert not AuditLog.objects.filter(pk=generisch.pk).exists()

    def test_bildschirmaufruf_ist_nicht_prune_exempt(self, facility, settings_obj, lead_user):
        """Sonst waechst der Log mit jedem Reload unbegrenzt weiter — inklusive
        Beschaeftigten-Username, dauerhaft und nicht redigierbar."""
        from django.db import connection

        from core.retention.audit_pruning import prune_auditlog

        if connection.vendor != "postgresql":
            pytest.skip("prune_auditlog erfordert PostgreSQL")

        settings_obj.auditlog_retention_months = 24
        settings_obj.save()
        now = timezone.now()
        ansicht = _bildschirm_aufruf(facility, lead_user, "2020-01-01", "2020-12-31")
        download = _export(facility, lead_user, "2020-01-01", "2020-12-31")
        self._veralten(now, ansicht, download)

        result = prune_auditlog(facility, settings_obj, now=now, dry_run=False)

        assert result["count"] == 1
        assert not AuditLog.objects.filter(pk=ansicht.pk).exists(), "Bildschirmaufrufe bleiben befristet"
        assert AuditLog.objects.filter(pk=download.pk).exists(), "das herausgegebene Artefakt bleibt"

    def test_json_ansicht_ist_nicht_prune_exempt(self, facility, settings_obj, lead_user):
        """Refs #1772: die Abwertung nimmt der JSON-Ansicht auch die Ausnahme.

        Die Alt-Zeile mit Marker faellt mit — sie ist keine Freigabe und war
        nie eine; die Dauer-Aufbewahrung war an eine Klassifikation gebunden,
        die es nicht mehr gibt.
        """
        from django.db import connection

        from core.retention.audit_pruning import prune_auditlog

        if connection.vendor != "postgresql":
            pytest.skip("prune_auditlog erfordert PostgreSQL")

        settings_obj.auditlog_retention_months = 24
        settings_obj.save()
        now = timezone.now()
        ansicht = _bildschirm_aufruf(facility, lead_user, "2020-01-01", "2020-12-31", fmt="json")
        altzeile = _json_ansicht_vor_der_abwertung(facility, lead_user, "2020-01-01", "2020-12-31")
        download = _export(facility, lead_user, "2020-01-01", "2020-12-31")
        self._veralten(now, ansicht, altzeile, download)

        result = prune_auditlog(facility, settings_obj, now=now, dry_run=False)

        assert result["count"] == 2
        assert not AuditLog.objects.filter(pk=ansicht.pk).exists()
        assert not AuditLog.objects.filter(pk=altzeile.pk).exists()
        assert AuditLog.objects.filter(pk=download.pk).exists(), "das herausgegebene Artefakt bleibt"

    def test_altzeilen_vor_der_marker_einfuehrung_bleiben_befristet(self, facility, settings_obj, lead_user):
        """Die Kehrseite derselben Rollout-Grenze: Bestands-Freigaben laufen
        nach der normalen Frist aus dem Log.

        Das ist **kein** zusaetzlicher Datenverlust — es ist genau das
        Verhalten, das vor dieser Welle galt. Ein Backfill waere der einzige
        Weg dagegen und ist ausgeschlossen: ``detail`` geht in den
        Ketten-Hash ein (``test_audit_chain.py::test_inplace_detail_mutation_breaks_chain``),
        eine nachtraegliche Markierung braeche also die Tamper-Evidenz.
        """
        from django.db import connection

        from core.retention.audit_pruning import prune_auditlog

        if connection.vendor != "postgresql":
            pytest.skip("prune_auditlog erfordert PostgreSQL")

        settings_obj.auditlog_retention_months = 24
        settings_obj.save()
        now = timezone.now()
        alt = _alt_zeile_vor_marker_einfuehrung(facility, lead_user, "2020-01-01", "2020-12-31")
        neu = _export(facility, lead_user, "2020-01-01", "2020-12-31")
        self._veralten(now, alt, neu)

        result = prune_auditlog(facility, settings_obj, now=now, dry_run=False)

        assert result["count"] == 1
        assert not AuditLog.objects.filter(pk=alt.pk).exists()
        assert AuditLog.objects.filter(pk=neu.pk).exists()

    def test_die_uebersicht_findet_den_alten_eintrag_noch(self, facility, lead_user):
        _export(facility, lead_user, "2020-01-01", "2020-12-31")

        releases = build_release_overview(facility)

        assert releases[0].date_from == date(2020, 1, 1)
