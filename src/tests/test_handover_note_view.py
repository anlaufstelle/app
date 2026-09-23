"""HTTP-Ebene der Schichtnotiz (Refs #2042).

Eigene Datei, weil diese Faelle den Django-Test-Client brauchen und mit der
**echten** Uhr laufen: die View zieht ``now`` aus ``timezone.localtime()`` und
nimmt keinen Parameter entgegen. Um trotzdem deterministisch zu sein, richten
die Fixtures genau EIN Ganztags-Zeitfenster (00:00–23:59) ein — damit ist die
laufende Schicht immer ``(heute, tf)`` und die zuletzt abgeschlossene immer
``(gestern, tf)``, unabhaengig von der Uhrzeit des Testlaufs.

Abgedeckt:

* **E-04** — der POST darf den Anzeigekontext nicht auf die *bearbeitete*
  Schicht verengen; die Notiz der abgebenden Schicht muss im HTMX-Austausch
  erhalten bleiben.
* **E-06** — kaputte Schichtparameter duerfen keinen 500 erzeugen.
* **E-07** — eine Ganztags-Notiz neben konfigurierten Schichten wird abgelehnt.
"""

from datetime import date, time, timedelta
from html.parser import HTMLParser

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, ShiftHandoverNote, TimeFilter
from core.services.case.handover_note import Shift, current_shift, decrypt_note, get_note, save_note

HANDOVER_NOTE_URL = "core:handover_note"


@pytest.fixture
def ganztags_filter(facility):
    """Ein einziges Zeitfenster ueber den vollen Tag — macht die Uhr egal."""
    return TimeFilter.objects.create(
        facility=facility,
        label="Tagdienst",
        start_time=time(0, 0),
        end_time=time(23, 59),
        sort_order=0,
    )


@pytest.fixture
def heute():
    return timezone.localdate()


@pytest.fixture
def gestern(heute):
    return heute - timedelta(days=1)


def _post(client, **daten):
    return client.post(reverse(HANDOVER_NOTE_URL), daten)


@pytest.mark.django_db
class TestAnzeigekontextNachDemSpeichern:
    """E-04: der Austausch traegt weiterhin BEIDE Bloecke."""

    def test_speichern_der_laufenden_notiz_behaelt_die_vorherige(
        self, client, staff_user, facility, ganztags_filter, heute, gestern
    ):
        save_note(facility, Shift(gestern, ganztags_filter), staff_user, "Notiz der Vorschicht")
        client.force_login(staff_user)

        antwort = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            display_date=gestern.isoformat(),
            display_time_filter=str(ganztags_filter.pk),
            note="Notiz der laufenden Schicht",
        )

        assert antwort.status_code == 200
        html = antwort.content.decode()
        assert "handover-note-previous" in html
        assert "handover-note-current" in html
        assert "Notiz der Vorschicht" in html
        assert "Notiz der laufenden Schicht" in html

    def test_zweimal_hintereinander_speichern_uebernimmt_den_zweiten_text(
        self, client, staff_user, facility, ganztags_filter, heute, gestern
    ):
        """Regression zum E2E-Fall: der zweite Speichervorgang muss greifen."""
        client.force_login(staff_user)
        erst = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            display_date=gestern.isoformat(),
            display_time_filter=str(ganztags_filter.pk),
            note="Runde 1",
        )
        assert erst.status_code == 200
        zweit = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            display_date=gestern.isoformat(),
            display_time_filter=str(ganztags_filter.pk),
            note="Runde 2",
        )
        assert zweit.status_code == 200, zweit.content.decode()[:400]
        assert decrypt_note(get_note(facility, Shift(heute, ganztags_filter))) == "Runde 2"
        assert "Runde 2" in zweit.content.decode()

    def test_get_und_post_zeigen_dieselben_bloecke(self, client, staff_user, facility, ganztags_filter, heute, gestern):
        save_note(facility, Shift(gestern, ganztags_filter), staff_user, "Notiz der Vorschicht")
        client.force_login(staff_user)

        vorher = client.get(reverse("core:zeitstrom"), {"view": "uebergabe"})
        assert vorher.status_code == 200
        assert "Notiz der Vorschicht" in vorher.content.decode()

        antwort = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            display_date=gestern.isoformat(),
            display_time_filter=str(ganztags_filter.pk),
            note="Frisch geschrieben",
        )
        assert "Notiz der Vorschicht" in antwort.content.decode()

    def test_ohne_anzeigekoordinaten_faellt_auf_die_automatik_zurueck(
        self, client, staff_user, facility, ganztags_filter, heute, gestern
    ):
        save_note(facility, Shift(gestern, ganztags_filter), staff_user, "Notiz der Vorschicht")
        client.force_login(staff_user)

        antwort = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            note="Ohne Anzeigekoordinaten",
        )

        assert antwort.status_code == 200
        html = antwort.content.decode()
        assert "Notiz der Vorschicht" in html
        assert "handover-note-previous" in html


@pytest.mark.django_db
class TestKaputteSchichtparameter:
    """E-06: kontrollierte Antwort statt HTTP 500 — und inzwischen mit 400.

    Der Statuscode ist bewusst auf malformte Koordinaten beschraenkt; fachliche
    Ausgaenge bleiben bei 200 (Begruendung in ``HandoverNoteUpdateView._render``).
    """

    def test_kein_uuid_im_zeitfilter(self, client, staff_user, facility, ganztags_filter, heute):
        client.force_login(staff_user)
        antwort = _post(client, shift_date=heute.isoformat(), time_filter="not-a-uuid", note="Text")
        assert antwort.status_code == 400
        assert "Die Schicht konnte nicht bestimmt werden" in antwort.content.decode()
        assert ShiftHandoverNote.objects.count() == 0

    def test_datum_ausserhalb_des_bereichs(self, client, staff_user, facility, ganztags_filter):
        client.force_login(staff_user)
        antwort = _post(client, shift_date="9999-12-31", time_filter="", note="Text")
        assert antwort.status_code == 400
        assert "Die Schicht konnte nicht bestimmt werden" in antwort.content.decode()
        assert ShiftHandoverNote.objects.count() == 0

    def test_datum_vor_dem_unterstuetzten_bereich(self, client, staff_user, facility, ganztags_filter):
        client.force_login(staff_user)
        antwort = _post(client, shift_date="1899-01-01", time_filter="", note="Text")
        assert antwort.status_code == 400
        assert ShiftHandoverNote.objects.count() == 0

    def test_unsinniges_datum(self, client, staff_user, facility, ganztags_filter, heute):
        client.force_login(staff_user)
        antwort = _post(client, shift_date="kein-datum", time_filter="", note="Text")
        assert antwort.status_code == 400
        assert ShiftHandoverNote.objects.count() == 0

    def test_kaputte_anzeigekoordinaten_kippen_die_antwort_nicht(
        self, client, staff_user, facility, ganztags_filter, heute
    ):
        client.force_login(staff_user)
        antwort = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            display_date="9999-12-31",
            display_time_filter="not-a-uuid",
            note="Trotzdem speichern",
        )
        assert antwort.status_code == 200
        assert decrypt_note(get_note(facility, Shift(heute, ganztags_filter))) == "Trotzdem speichern"


@pytest.mark.django_db
class TestGanztagsnotizUeberHttp:
    """E-07: der POST darf die Schicht-Identität nicht umgehen."""

    def test_leerer_zeitfilter_bei_konfigurierten_schichten_abgelehnt(
        self, client, staff_user, facility, ganztags_filter, heute
    ):
        client.force_login(staff_user)
        antwort = _post(client, shift_date=heute.isoformat(), time_filter="", note="Neben der Schicht")
        assert antwort.status_code == 200
        assert not ShiftHandoverNote.objects.filter(time_filter__isnull=True).exists()

    def test_ohne_zeitfilter_geht_der_ganze_tag(self, client, staff_user, facility, heute):
        client.force_login(staff_user)
        antwort = _post(client, shift_date=heute.isoformat(), time_filter="", note="Ganzer Tag")
        assert antwort.status_code == 200
        assert decrypt_note(get_note(facility, Shift(heute, None))) == "Ganzer Tag"


@pytest.mark.django_db
class TestKalenderfallUeberHttp:
    """E-03 auf der HTTP-Ebene: Vortagsnotiz erscheint in der Übergabe."""

    def test_uebergabe_zeigt_die_notiz_des_vortags(self, client, staff_user, facility, gestern):
        save_note(facility, Shift(gestern, None), staff_user, "Vortagsnotiz ohne Zeitfilter")
        client.force_login(staff_user)
        antwort = client.get(reverse("core:zeitstrom"), {"view": "uebergabe"})
        assert antwort.status_code == 200
        html = antwort.content.decode()
        assert "Vortagsnotiz ohne Zeitfilter" in html
        assert "handover-note-previous" in html


@pytest.mark.django_db
class TestRollenGateUeberHttp:
    def test_assistenz_wird_abgewiesen(self, client, assistant_user, facility, ganztags_filter, heute):
        client.force_login(assistant_user)
        antwort = _post(client, shift_date=heute.isoformat(), time_filter=str(ganztags_filter.pk), note="Nein")
        assert antwort.status_code == 403
        assert ShiftHandoverNote.objects.count() == 0

    def test_zeitfilter_einer_fremden_einrichtung_wird_abgewiesen(
        self, client, staff_user, facility, second_facility, heute
    ):
        fremd = TimeFilter.objects.create(
            facility=second_facility, label="Fremd", start_time=time(0, 0), end_time=time(23, 59)
        )
        client.force_login(staff_user)
        antwort = _post(client, shift_date=heute.isoformat(), time_filter=str(fremd.pk), note="Fremd")
        # Ebenfalls 400: eine gueltige UUID aus einer FREMDEN
        # Einrichtung ist genauso wenig aus der eigenen Oberflaeche erreichbar
        # wie eine kaputte. Die Antwort unterscheidet die beiden Faelle nicht —
        # sie verraet damit auch nicht, ob es den Zeitfilter anderswo gibt.
        assert antwort.status_code == 400
        assert ShiftHandoverNote.objects.count() == 0


@pytest.mark.django_db
class TestDatumsbereichImService:
    """E-06, Service-Seite: der Bereich wird nicht erst in der View geprüft."""

    def test_service_lehnt_datum_weit_in_der_zukunft_ab(self, facility, staff_user):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            save_note(facility, Shift(date(9999, 12, 31), None), staff_user, "Text")


@pytest.mark.django_db
class TestAnlagekonfliktErhaeltBeideTexte:
    """E-05 (R-E-23): der Konflikt darf keine der beiden Fassungen verlieren.

    Aufbau nach dem Muster des Re-Reviews: in der DB steht bereits ``WINNER``,
    der POST sieht aber einen **veralteten leeren** Lookup und versucht deshalb
    anzulegen. Der echte Postgres-UniqueConstraint schlaegt zu, der echte
    Konfliktpfad des Service laeuft — und die Antwort muss danach BEIDE Texte
    tragen: den gespeicherten Bestand zum Lesen und die eingereichte Eingabe
    zum Weiterarbeiten.
    """

    @pytest.fixture
    def veralteter_leerer_lookup(self, monkeypatch):
        """Scharfstellen NACH dem Aufbau: nur der POST sieht den leeren Lookup.

        Wird die Attrappe schon vor dem Anlegen von ``WINNER`` gesetzt, faengt
        sie den Aufbau ab statt den Konflikt — und der POST liefe in den
        harmlosen Update-Pfad. Deshalb ein ausdrueckliches ``arm()``.
        """
        from core.services.case import handover_note as modul

        original = modul._locked_existing_note

        def arm():
            stand = {"erster": True}

            def _stale(facility, shift):
                if stand["erster"]:
                    stand["erster"] = False
                    return None
                return original(facility, shift)

            monkeypatch.setattr(modul, "_locked_existing_note", _stale)

        return arm

    def test_konflikt_zeigt_bestand_und_eingereichte_eingabe(
        self, client, staff_user, facility, ganztags_filter, heute, veralteter_leerer_lookup
    ):
        save_note(facility, Shift(heute, ganztags_filter), staff_user, "WINNER")
        client.force_login(staff_user)
        veralteter_leerer_lookup()

        antwort = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            note="LOSING-INPUT",
        )

        html = antwort.content.decode()
        assert antwort.status_code == 200
        assert "WINNER" in html, "Der gespeicherte Bestand muss lesbar bleiben"
        assert "LOSING-INPUT" in html, "Die eingereichte Eingabe darf nicht verloren gehen"
        assert decrypt_note(get_note(facility, Shift(heute, ganztags_filter))) == "WINNER"

    def test_konflikt_ueberschreibt_nicht_still(
        self, client, staff_user, facility, ganztags_filter, heute, veralteter_leerer_lookup
    ):
        save_note(facility, Shift(heute, ganztags_filter), staff_user, "WINNER")
        client.force_login(staff_user)
        veralteter_leerer_lookup()

        _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            note="LOSING-INPUT",
        )
        assert decrypt_note(get_note(facility, Shift(heute, ganztags_filter))) == "WINNER"

    def test_verwerfen_behaelt_den_gespeicherten_stand(
        self, client, staff_user, facility, ganztags_filter, heute, veralteter_leerer_lookup
    ):
        """Der zweite Ausgang des Abgleichs: nichts schreiben, Bestand zeigen."""
        save_note(facility, Shift(heute, ganztags_filter), staff_user, "WINNER")
        client.force_login(staff_user)
        veralteter_leerer_lookup()
        _post(client, shift_date=heute.isoformat(), time_filter=str(ganztags_filter.pk), note="LOSING-INPUT")

        antwort = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            note="LOSING-INPUT",
            discard="1",
        )
        assert antwort.status_code == 200
        assert decrypt_note(get_note(facility, Shift(heute, ganztags_filter))) == "WINNER"
        html = antwort.content.decode()
        assert "WINNER" in html
        assert "handover-note-conflict" not in html

    def test_nach_dem_abgleich_kann_bewusst_ueberschrieben_werden(
        self, client, staff_user, facility, ganztags_filter, heute, veralteter_leerer_lookup
    ):
        """Der zweite, bestaetigte Anlauf setzt den eigenen Text durch."""
        save_note(facility, Shift(heute, ganztags_filter), staff_user, "WINNER")
        client.force_login(staff_user)
        veralteter_leerer_lookup()
        _post(client, shift_date=heute.isoformat(), time_filter=str(ganztags_filter.pk), note="LOSING-INPUT")

        antwort = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            note="LOSING-INPUT",
        )
        assert antwort.status_code == 200
        assert decrypt_note(get_note(facility, Shift(heute, ganztags_filter))) == "LOSING-INPUT"


@pytest.mark.django_db
class TestKaputteParameterLiefern400:
    """E-06: malformte Schichtkoordinaten sind ein Client-Fehler, kein Ergebnis."""

    def test_kein_uuid_liefert_400(self, client, staff_user, facility, ganztags_filter, heute):
        client.force_login(staff_user)
        antwort = _post(client, shift_date=heute.isoformat(), time_filter="not-a-uuid", note="Text")
        assert antwort.status_code == 400
        assert "Die Schicht konnte nicht bestimmt werden" in antwort.content.decode()

    def test_datum_ausserhalb_des_bereichs_liefert_400(self, client, staff_user, facility, ganztags_filter):
        client.force_login(staff_user)
        antwort = _post(client, shift_date="9999-12-31", time_filter="", note="Text")
        assert antwort.status_code == 400

    def test_unsinniges_datum_liefert_400(self, client, staff_user, facility, ganztags_filter):
        client.force_login(staff_user)
        antwort = _post(client, shift_date="kein-datum", time_filter="", note="Text")
        assert antwort.status_code == 400

    def test_fachliche_fehler_bleiben_bei_200(self, client, staff_user, facility, ganztags_filter, heute):
        """Abgrenzung: ein geschlossenes Fenster ist ein Ergebnis, kein Client-Fehler."""
        client.force_login(staff_user)
        antwort = _post(
            client,
            shift_date=(heute - timedelta(days=400)).isoformat(),
            time_filter=str(ganztags_filter.pk),
            note="Zu spät",
        )
        assert antwort.status_code == 200
        assert ShiftHandoverNote.objects.count() == 0


class _HiddenFieldParser(HTMLParser):
    """Hidden-Felder des Konflikt-Formulars aus der Antwort lesen.

    Bewusst ``html.parser`` aus der Standardbibliothek — der Testlauf soll
    keine zusaetzliche Abhaengigkeit brauchen.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.felder: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attribute = dict(attrs)
        if attribute.get("type") == "hidden" and attribute.get("name"):
            self.felder[attribute["name"]] = attribute.get("value", "")


def _hidden_fields(html: str) -> dict[str, str]:
    parser = _HiddenFieldParser()
    parser.feed(html)
    return parser.felder


@pytest.mark.django_db
class TestUebernehmenPruefetDieAngezeigteFassung:
    """E-15 (R-E-25): der Abgleich gilt genau der Fassung, die gezeigt wurde.

    Reproduktion des Befundes: Waehrend die erste Person die beiden
    Fassungen vergleicht, schreibt eine zweite ``NEWER-WINNER``. Das
    unveraenderte Konfliktformular abzusenden darf diesen Text NICHT
    ungesehen ueberschreiben — die Zeilensperre serialisiert zwar, sagt aber
    nichts darueber, welche Fassung die Person gesehen hat.
    """

    @pytest.fixture
    def veralteter_leerer_lookup(self, monkeypatch):
        from core.services.case import handover_note as modul

        original = modul._locked_existing_note

        def arm():
            stand = {"erster": True}

            def _stale(facility, shift):
                if stand["erster"]:
                    stand["erster"] = False
                    return None
                return original(facility, shift)

            monkeypatch.setattr(modul, "_locked_existing_note", _stale)

        return arm

    @staticmethod
    def _erstkonflikt(client, facility, staff_user, ganztags_filter, heute, arm):
        """Erzeugt den ersten Konflikt und liefert dessen Formularfelder."""
        save_note(facility, Shift(heute, ganztags_filter), staff_user, "WINNER")
        client.force_login(staff_user)
        arm()
        antwort = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            note="LOSING-INPUT",
        )
        html = antwort.content.decode()
        assert "handover-note-conflict" in html
        return _hidden_fields(html)

    def test_zwischenzeitliche_aenderung_wird_nicht_ueberschrieben(
        self, client, staff_user, lead_user, facility, ganztags_filter, heute, veralteter_leerer_lookup
    ):
        felder = self._erstkonflikt(client, facility, staff_user, ganztags_filter, heute, veralteter_leerer_lookup)

        # Zweite berechtigte Person schreibt, waehrend verglichen wird.
        save_note(facility, Shift(heute, ganztags_filter), lead_user, "NEWER-WINNER")
        audit_vorher = AuditLog.objects.filter(target_type="ShiftHandoverNote").count()

        antwort = _post(client, **{**felder, "note": "LOSING-INPUT"})

        html = antwort.content.decode()
        assert antwort.status_code == 200
        assert "handover-note-conflict" in html, "Es muss ein NEUER Abgleich erscheinen"
        assert "NEWER-WINNER" in html, "Die jetzt aktuelle Fassung muss zur Entscheidung stehen"
        assert "LOSING-INPUT" in html, "Die eigene Eingabe bleibt erhalten"
        assert decrypt_note(get_note(facility, Shift(heute, ganztags_filter))) == "NEWER-WINNER"
        assert AuditLog.objects.filter(target_type="ShiftHandoverNote").count() == audit_vorher, (
            "Ein abgewiesener Abgleich darf keine Audit-Zeile erzeugen"
        )

    def test_zweiter_abgleich_traegt_eine_frische_revision(
        self, client, staff_user, lead_user, facility, ganztags_filter, heute, veralteter_leerer_lookup
    ):
        felder = self._erstkonflikt(client, facility, staff_user, ganztags_filter, heute, veralteter_leerer_lookup)
        save_note(facility, Shift(heute, ganztags_filter), lead_user, "NEWER-WINNER")

        zweite_antwort = _post(client, **{**felder, "note": "LOSING-INPUT"})
        frische_felder = _hidden_fields(zweite_antwort.content.decode())
        assert frische_felder.get("base_revision") not in (None, felder.get("base_revision"))

        dritte_antwort = _post(client, **{**frische_felder, "note": "LOSING-INPUT"})
        assert dritte_antwort.status_code == 200
        assert decrypt_note(get_note(facility, Shift(heute, ganztags_filter))) == "LOSING-INPUT"
        assert "handover-note-conflict" not in dritte_antwort.content.decode()

    def test_unveraenderter_bestand_laesst_uebernehmen_zu(
        self, client, staff_user, facility, ganztags_filter, heute, veralteter_leerer_lookup
    ):
        felder = self._erstkonflikt(client, facility, staff_user, ganztags_filter, heute, veralteter_leerer_lookup)

        antwort = _post(client, **{**felder, "note": "LOSING-INPUT"})

        assert antwort.status_code == 200
        assert decrypt_note(get_note(facility, Shift(heute, ganztags_filter))) == "LOSING-INPUT"
        assert "handover-note-conflict" not in antwort.content.decode()

    def test_manipulierte_revision_schreibt_nicht(
        self, client, staff_user, facility, ganztags_filter, heute, veralteter_leerer_lookup
    ):
        felder = self._erstkonflikt(client, facility, staff_user, ganztags_filter, heute, veralteter_leerer_lookup)

        antwort = _post(client, **{**felder, "base_revision": "irgendwas", "note": "LOSING-INPUT"})

        assert antwort.status_code == 200
        assert "handover-note-conflict" in antwort.content.decode()
        assert decrypt_note(get_note(facility, Shift(heute, ganztags_filter))) == "WINNER"

    def test_leere_revision_schreibt_nicht(
        self, client, staff_user, facility, ganztags_filter, heute, veralteter_leerer_lookup
    ):
        felder = self._erstkonflikt(client, facility, staff_user, ganztags_filter, heute, veralteter_leerer_lookup)

        antwort = _post(client, **{**felder, "base_revision": "", "note": "LOSING-INPUT"})

        assert antwort.status_code == 200
        assert "handover-note-conflict" in antwort.content.decode()
        assert decrypt_note(get_note(facility, Shift(heute, ganztags_filter))) == "WINNER"

    def test_inzwischen_geloeschte_fassung_schreibt_nicht_sofort(
        self, client, staff_user, lead_user, facility, ganztags_filter, heute, veralteter_leerer_lookup
    ):
        felder = self._erstkonflikt(client, facility, staff_user, ganztags_filter, heute, veralteter_leerer_lookup)

        save_note(facility, Shift(heute, ganztags_filter), lead_user, "")
        assert get_note(facility, Shift(heute, ganztags_filter)) is None

        antwort = _post(client, **{**felder, "note": "LOSING-INPUT"})

        assert antwort.status_code == 200
        assert "handover-note-conflict" in antwort.content.decode()
        assert get_note(facility, Shift(heute, ganztags_filter)) is None, "Kein Write vor der erneuten Entscheidung"

    def test_normales_speichern_braucht_keine_revision(self, client, staff_user, facility, ganztags_filter, heute):
        """Abgrenzung: der gewoehnliche Bearbeitungsweg bleibt unberuehrt."""
        save_note(facility, Shift(heute, ganztags_filter), staff_user, "Erste Fassung")
        client.force_login(staff_user)

        antwort = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            note="Zweite Fassung",
        )
        assert antwort.status_code == 200
        assert decrypt_note(get_note(facility, Shift(heute, ganztags_filter))) == "Zweite Fassung"


@pytest.mark.django_db
class TestSchichtwechselUeberHttp:
    """E-16: derselbe Ablauf wie im Browsergate, aber ohne Playwright.

    Der Re-Review hat den E2E-Dienstplanwechsel als Django-HTTP-Sequenz
    nachgestellt und dabei gezeigt, dass er am Tagesrand kippt. Genau diese
    Sequenz steht hier als Unit-Test — sie laeuft in jedem Vollauf mit und
    haengt nicht am Browsergate.

    Die Schichtgrenzen liegen **relativ zu jetzt** und nutzen den
    Mitternachts-Ueberlauf, damit der Fall auch um 00:00:30 gilt.
    """

    @staticmethod
    def _minuten_vor_jetzt(minuten):
        return (timezone.localtime() - timedelta(minutes=minuten)).time().replace(second=0, microsecond=0)

    @pytest.fixture
    def eine_laufende_schicht(self, facility):
        """Ein Fenster, das ``jetzt`` sicher deckt (``start > end`` ⇒ Überlauf)."""
        return TimeFilter.objects.create(
            facility=facility,
            label="Schicht A",
            start_time=self._minuten_vor_jetzt(5),
            end_time=self._minuten_vor_jetzt(6),
            sort_order=0,
        )

    def _wechsel_vollziehen(self, facility, schicht_a):
        """A endet eine Minute vor jetzt, B übernimmt ab dort."""
        grenze = self._minuten_vor_jetzt(1)
        schicht_a.end_time = grenze
        schicht_a.save(update_fields=["end_time"])
        return TimeFilter.objects.create(
            facility=facility,
            label="Schicht B",
            start_time=grenze,
            end_time=self._minuten_vor_jetzt(2),
            sort_order=1,
        )

    def test_notiz_wandert_in_den_abgebenden_block(self, client, staff_user, facility, eine_laufende_schicht):
        client.force_login(staff_user)

        laufend = current_shift(facility)
        assert laufend.time_filter == eine_laufende_schicht, "Aufbau: A muss die laufende Schicht sein"
        antwort = _post(
            client,
            shift_date=laufend.shift_date.isoformat(),
            time_filter=str(eine_laufende_schicht.pk),
            note="Notiz der abgebenden Schicht",
        )
        assert antwort.status_code == 200
        assert "handover-note-current" in antwort.content.decode()

        self._wechsel_vollziehen(facility, eine_laufende_schicht)

        seite = client.get(reverse("core:zeitstrom"), {"view": "uebergabe"})
        html = seite.content.decode()
        assert seite.status_code == 200
        abgebend = html.split("handover-note-previous", 1)
        assert len(abgebend) == 2, "Es muss einen abgebenden Block geben"
        laufender_teil = abgebend[1].split("handover-note-current", 1)
        assert len(laufender_teil) == 2, "Und einen laufenden Block danach"
        assert "Notiz der abgebenden Schicht" in laufender_teil[0], (
            "Die geschriebene Notiz gehört in den ABGEBENDEN Block, nicht in den laufenden"
        )
        assert "Notiz der abgebenden Schicht" not in laufender_teil[1]
        assert "Schicht A" in laufender_teil[0]
        assert "Schicht B" in laufender_teil[1]

    def test_kalenderfall_zeigt_den_vortag_als_abgebende_schicht(self, client, staff_user, facility):
        """Ohne Zeitfenster ist der Vortag die abgebende Schicht (E-03/E-11)."""
        client.force_login(staff_user)
        gestern = timezone.localdate() - timedelta(days=1)
        save_note(facility, Shift(gestern, None), staff_user, "Kalendernotiz von gestern")

        seite = client.get(reverse("core:zeitstrom"), {"view": "uebergabe"})
        html = seite.content.decode()
        teile = html.split("handover-note-previous", 1)
        assert len(teile) == 2
        vor_laufend, nach_laufend = teile[1].split("handover-note-current", 1)
        assert "Kalendernotiz von gestern" in vor_laufend
        assert "Kalendernotiz von gestern" not in nach_laufend


class _FormularParser(HTMLParser):
    """Hidden-Feldnamen **je Formular** plus dessen Testids.

    Ein flaches Einsammeln über die ganze Antwort reicht für E-18 nicht: die
    Zusage lautet „Revision **nur** im Abgleich-Formular", und im gemischten
    Konflikt-Response stehen zwei Formulare nebeneinander.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.formulare: list[dict] = []
        self._offen: dict | None = None

    def handle_starttag(self, tag, attrs):
        attribute = dict(attrs)
        if tag == "form":
            self._offen = {"felder": set(), "testids": set()}
            self.formulare.append(self._offen)
            return
        if self._offen is None:
            return
        if tag == "input" and attribute.get("type") == "hidden" and attribute.get("name"):
            self._offen["felder"].add(attribute["name"])
        if attribute.get("data-testid"):
            self._offen["testids"].add(attribute["data-testid"])

    def handle_endtag(self, tag):
        if tag == "form":
            self._offen = None


def _formulare(html: str) -> list[dict]:
    parser = _FormularParser()
    parser.feed(html)
    return parser.formulare


@pytest.mark.django_db
class TestRevisionsfeldNurImAbgleich:
    """E-18: das normale Notizformular trägt **kein** ``base_revision``.

    Der bestehende Test schickt einen POST ohne das Feld und prüft, dass
    gespeichert wird — er liest aber kein Formular. Würde das Feld später
    versehentlich in den normalen Templatezweig rutschen, bliebe er grün, und
    aus dem gezielten Abgleich würde stillschweigend eine optimistische Sperre
    für jede Bearbeitung. Deshalb hier der Pin am gerenderten Formular.
    """

    @pytest.fixture
    def veralteter_leerer_lookup(self, monkeypatch):
        from core.services.case import handover_note as modul

        original = modul._locked_existing_note

        def arm():
            stand = {"erster": True}

            def _stale(facility, shift):
                if stand["erster"]:
                    stand["erster"] = False
                    return None
                return original(facility, shift)

            monkeypatch.setattr(modul, "_locked_existing_note", _stale)

        return arm

    def test_get_der_uebergabe_traegt_keine_revision(self, client, staff_user, facility, ganztags_filter):
        client.force_login(staff_user)
        seite = client.get(reverse("core:zeitstrom"), {"view": "uebergabe"})
        formulare = _formulare(seite.content.decode())

        assert formulare, "Der GET muss mindestens ein Notizformular rendern"
        assert all("base_revision" not in f["felder"] for f in formulare)
        assert any("shift_date" in f["felder"] for f in formulare), "Aufbau: es sind Notizformulare"

    def test_erfolgs_antwort_traegt_keine_revision(self, client, staff_user, facility, ganztags_filter, heute):
        client.force_login(staff_user)
        antwort = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            note="Ganz normal gespeichert",
        )
        formulare = _formulare(antwort.content.decode())

        assert formulare
        assert all("base_revision" not in f["felder"] for f in formulare)

    def test_konflikt_antwort_traegt_die_revision_genau_im_abgleich(
        self, client, staff_user, facility, ganztags_filter, heute, veralteter_leerer_lookup
    ):
        save_note(facility, Shift(heute, ganztags_filter), staff_user, "WINNER")
        client.force_login(staff_user)
        veralteter_leerer_lookup()
        antwort = _post(
            client,
            shift_date=heute.isoformat(),
            time_filter=str(ganztags_filter.pk),
            note="LOSING-INPUT",
        )
        formulare = _formulare(antwort.content.decode())

        mit_revision = [f for f in formulare if "base_revision" in f["felder"]]
        assert len(mit_revision) == 1, "Genau ein Formular trägt die Revision"
        assert "handover-note-conflict-overwrite" in mit_revision[0]["testids"], (
            "Und zwar das Abgleich-Formular, nicht irgendeines"
        )
        ohne_revision = [f for f in formulare if "base_revision" not in f["felder"]]
        assert all("handover-note-conflict-overwrite" not in f["testids"] for f in ohne_revision)
