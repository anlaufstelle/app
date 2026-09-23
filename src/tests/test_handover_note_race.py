"""Renn-Nachweis der erstmaligen Schichtnotiz-Anlage (Refs #2042, E-05).

Eigene Datei, weil der Nachweis ``django_db(transaction=True)`` braucht: zwei
echte Verbindungen sehen einander nur, wenn wirklich committet wird — im
Transaktions-Rollback der uebrigen Tests gaebe es das Rennen nicht.

**Beobachtend gebaut**: zwei Faeden, eine Barriere
direkt nach den beiden leeren Lookups, endliche Wartezeit, Auswertung NACH dem
Rennen — kein ``assert`` im gesperrten Fenster.

Der Befund: ``select_for_update().first()`` sperrt keine Zeile, die es noch
nicht gibt. Beide Faeden lesen ``None`` und legen an; der zweite lief anfangs
in einen ungefangenen ``IntegrityError`` (HTTP 500). Erwartet wird
jetzt: genau EINE Zeile, und der unterlegene Aufruf endet fachlich — bei
gleichem Text still erfolgreich, bei abweichendem Text mit einer
``ValidationError``, die den Konflikt benennt.
"""

import threading

import pytest
from django.core.exceptions import ValidationError
from django.db import connections
from django.utils import timezone

from core.models import Organization, Settings, ShiftHandoverNote, User
from core.services.case import handover_note as handover_note_mod
from core.services.case.handover_note import Shift, decrypt_note, save_note

_JOIN_TIMEOUT = 30


def _committed_bestand():
    """Committeter Aufbau — die Faeden bringen eigene Verbindungen mit."""
    org = Organization.objects.create(name="Notiz-Renn-Org")
    facility = org.facilities.create(name="Notiz-Renn-Stelle")
    Settings.objects.get_or_create(facility=facility)
    user_a = User.objects.create_user(
        username="renn-staff-a", password="x" * 40, facility=facility, role=User.Role.STAFF
    )
    user_b = User.objects.create_user(
        username="renn-staff-b", password="x" * 40, facility=facility, role=User.Role.STAFF
    )
    return facility, user_a, user_b


@pytest.fixture
def bestand(transactional_db):
    """Committeter Aufbau; das Aufraeumen macht der Flush von pytest-django.

    Bewusst KEIN eigenes ``delete()`` im Teardown: die Organisation kaskadiert
    ins AuditLog, und dessen Zeilen sind per Trigger unveraenderlich
    (``prevent_auditlog_mutation``). Der Flush arbeitet mit ``TRUNCATE`` und
    laeuft am Zeilentrigger vorbei.
    """
    yield _committed_bestand()
    connections.close_all()


@pytest.mark.django_db(transaction=True)
class TestErstanlageUnterNebenlaeufigkeit:
    def _rennen(self, facility, user_a, user_b, text_a, text_b):
        """Beide Faeden lesen die leere Schicht, dann legen beide an."""
        # Echte Uhr: der Aufbau hat keine Zeitfilter, die laufende Schicht ist
        # also der heutige Kalendertag und damit sicher im Editierfenster.
        jetzt = timezone.localtime()
        schicht = Shift(jetzt.date(), None)
        beide_haben_gelesen = threading.Barrier(2, timeout=_JOIN_TIMEOUT)
        ergebnis: dict[str, str] = {}
        original = handover_note_mod._locked_existing_note

        schon_gewartet = threading.local()

        def _mit_barriere(facility_arg, shift_arg):
            zeile = original(facility_arg, shift_arg)
            # Genau hier klafft das Fenster: beide Faeden haben ``None``
            # gelesen und keiner haelt eine Sperre. Nur der ERSTE Lookup je
            # Faden wartet — der Verlierer liest nach dem Integritaetsfehler
            # ein zweites Mal, und dort steht kein Partner mehr an der
            # Barriere (sonst verklemmt der Test statt zu messen).
            if not getattr(schon_gewartet, "ja", False):
                schon_gewartet.ja = True
                beide_haben_gelesen.wait()
            return zeile

        handover_note_mod._locked_existing_note = _mit_barriere

        def _lauf(name, user, text):
            try:
                save_note(facility, schicht, user, text, now=jetzt)
                ergebnis[name] = "gespeichert"
            except ValidationError:
                ergebnis[name] = "konflikt"
            except Exception as exc:  # noqa: BLE001
                ergebnis[name] = f"{type(exc).__name__}"
            finally:
                connections.close_all()

        faden_a = threading.Thread(target=_lauf, args=("a", user_a, text_a), name="notiz-a")
        faden_b = threading.Thread(target=_lauf, args=("b", user_b, text_b), name="notiz-b")
        try:
            faden_a.start()
            faden_b.start()
            faden_a.join(timeout=_JOIN_TIMEOUT)
            faden_b.join(timeout=_JOIN_TIMEOUT)
        finally:
            handover_note_mod._locked_existing_note = original
        assert not faden_a.is_alive() and not faden_b.is_alive(), "Verklemmung bei der Erstanlage"
        return schicht, ergebnis

    def test_gleicher_text_endet_fuer_beide_erfolgreich(self, bestand):
        facility, user_a, user_b = bestand
        schicht, ergebnis = self._rennen(facility, user_a, user_b, "Gleicher Text", "Gleicher Text")

        assert set(ergebnis.values()) == {"gespeichert"}, f"Bei identischem Text darf kein Aufruf scheitern: {ergebnis}"
        assert ShiftHandoverNote.objects.filter(facility=facility).count() == 1
        assert decrypt_note(handover_note_mod.get_note(facility, schicht)) == "Gleicher Text"

    def test_abweichender_text_meldet_einen_fachlichen_konflikt(self, bestand):
        facility, user_a, user_b = bestand
        schicht, ergebnis = self._rennen(facility, user_a, user_b, "Fassung A", "Fassung B")

        assert sorted(ergebnis.values()) == ["gespeichert", "konflikt"], (
            f"Erwartet genau ein Erfolg und ein fachlicher Konflikt, nie ein IntegrityError: {ergebnis}"
        )
        assert ShiftHandoverNote.objects.filter(facility=facility).count() == 1
        assert decrypt_note(handover_note_mod.get_note(facility, schicht)) in {"Fassung A", "Fassung B"}
