"""Mutations-Pins zum Meilenstein release-0.94 — Modul-Batch ``credentials``.

Quelle: ``src/core/services/security/credentials.py`` (Funktionen
:func:`revoke_credentials`, :func:`_write_audit`, :func:`flush_user_sessions`);
Mutantenbaum ``mutants/src/core/services/security/credentials.py``. Herkunft
der Triage: die Survivor-Triage release-0.94 (Ledger des Meilensteins) (20 Survivors aus dem
0.93-Delta, adversarial gegengeprueft — die als „nur Kosmetik" abgetanen
Log-Text-Mutanten wurden in der Verify-Runde widerlegt und sind hier gepinnt).

Vier Themen:

* **Duck-Typing-Toleranz der Service-Grenze.** ``revoke_credentials`` und
  ``_write_audit`` lesen ``must_change_password`` bzw. ``facility`` per
  ``getattr`` MIT Default. Der Default ist fuer echte ``User``-Instanzen nie
  sichtbar (Django installiert fuer jedes konkrete Feld einen Deskriptor) —
  genau deshalb ueberlebt jede Mutation daran die ganze Suite. Gepinnt wird
  der Fall, fuer den der Default geschrieben wurde: ein user-artiges Objekt
  ohne das Attribut (``AnonymousUser``/``SimpleLazyObject``, Service-/CLI-Stub).
* **Die Verengung des Teil-Saves.** ``user.save(update_fields=[...])`` schuetzt
  vor Lost Updates; bisher pruefte nur der Endwert.
* **Die Kostenzusage R18 (Refs #1476/#1666).** Der Session-Scan ist
  gedeckelt (Setting mit Modul-Default), gestreamt (``chunk_size``) und
  spaltenbeschraenkt (``.only``). Alle drei Eigenschaften waren unverifiziert.
* **Die beiden Log-Zeilen von :func:`flush_user_sessions`.** Der Flush schreibt
  BEWUSST keine Audit-Zeile (``REVOCATION_EFFECTS[...].write_audit=False`` fuer
  Lockout/Rechteentzug) — die INFO-Zeile ist damit die einzige Spur eines
  erfolgten Flushs, die ERROR-Zeile das einzige Betriebssignal dafuer, dass der
  DoS-Deckel gegriffen hat und ein Session-Rest ungeflusht blieb.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` /
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten) — die
Log-Assertions vergleichen deshalb die vollstaendig gerenderte Meldung;
Sprachkontext wird per ``translation.override("de")`` festgenagelt; Fixtures
kommen aus ``src/tests/conftest.py`` (echte Model-Objekte, keine Fakes); KEINE
Imports aus ``test_rls_functional``. Jeder Test nennt seine Mutanten in der
Docstring samt dem Wert, der beim Mutanten abweicht.
"""

from __future__ import annotations

import logging
from unittest.mock import Mock, patch

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import connection
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext
from django.utils import translation

from core.services.security import credentials
from core.services.security.credentials import (
    RevocationReason,
    flush_user_sessions,
    revoke_credentials,
)

#: Der Logger, dessen Meldungen hier gepinnt werden. Andere Logger (Django,
#: Security-Middleware) duerfen im selben Lauf feuern — sie werden ausgefiltert.
LOGGER_NAME = "core.services.security.credentials"

#: Name des Deckel-Settings. Steht in ``settings/base.py`` IMMER (env_int mit
#: 10000) — genau deshalb ist der Modul-Default toter Code, solange kein Test
#: das Setting entfernt.
CAP_SETTING = "OFFLINE_REVOCATION_SESSION_SCAN_LIMIT"


def _db_session_for(user) -> str:
    """Echte DB-Session, die wie eine Login-Session auf den User zeigt."""
    store = SessionStore()
    store["_auth_user_id"] = str(user.pk)
    store["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    store.save()
    return store.session_key


def _anonymous_db_session() -> str:
    """Fremde (nicht angemeldete) DB-Session — Scan-Ballast, wird nie geflusht."""
    store = SessionStore()
    store["irgendein_schluessel"] = "wert"
    store.save()
    return store.session_key


def _messages_of(caplog) -> list[str]:
    """Vollstaendig gerenderte Meldungen NUR aus dem Credential-Logger."""
    return [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME]


# ============================================================================
# _write_audit — die Facility-Ermittlung an der Service-Grenze (1 Mutant)
# ============================================================================


class TestWriteAuditFacilityFallback:
    """``facility = getattr(user, "facility", None)`` — der Default ist Vertrag."""

    def test_audit_facility_is_none_for_user_object_without_facility_attribute(self, monkeypatch):
        """Pin: ``x__write_audit__mutmut_6`` — ``getattr(user, "facility", None)``
        → ``getattr(user, "facility")`` (Default entfernt).

        ``AnonymousUser`` ist ein echtes Django-Objekt OHNE ``facility``-Attribut
        und steht hier stellvertretend fuer jedes user-artige Objekt, das der
        untypisierte Service-Parameter zulaesst (``SimpleLazyObject``,
        Service-/CLI-Stub, haengender FK — Djangos
        ``RelatedObjectDoesNotExist`` erbt von ``AttributeError`` und wird vom
        Default ebenfalls geschluckt).

        Original: der Aufruf laeuft durch und reicht ``facility=None`` an den
        Audit-Schreiber. Mutant 6: ``getattr`` ohne Default wirft
        ``AttributeError: 'AnonymousUser' object has no attribute 'facility'``,
        der Aufruf endet mit Fehler statt mit einem Audit-Write.

        Der Audit-Schreiber selbst ist ersetzt, weil ``AnonymousUser`` als
        ``AuditLog.user``-FK nicht speicherbar waere — geprueft wird hier
        ausschliesslich der Wert, der die Attribut-Ermittlung verlaesst.
        """
        senke = Mock()
        monkeypatch.setattr(credentials, "audit_event", senke)

        credentials._write_audit(AnonymousUser(), RevocationReason.PASSWORD_CHANGE, None)

        assert senke.call_count == 1
        assert senke.call_args.kwargs["facility"] is None
        assert senke.call_args.kwargs["detail"] == {"reason": "password_change"}


# ============================================================================
# revoke_credentials — Zwangswechsel-Zweig (3 Mutanten)
# ============================================================================


class TestRevokeCredentialsForcedChangeDuckTyping:
    """``getattr(user, "must_change_password", False)`` — Default = still ueberspringen."""

    def test_user_object_without_forced_change_attribute_is_left_untouched(self, monkeypatch):
        """Pin: ``x_revoke_credentials__mutmut_15`` (Default entfernt) und
        ``x_revoke_credentials__mutmut_18`` (Default ``False`` → ``True``).

        ``AnonymousUser`` traegt kein ``must_change_password``. Original: der
        Zweig wird still uebersprungen — es wird nichts gesetzt und nicht
        gespeichert.

        Mutant 15: ``getattr`` ohne Default wirft ``AttributeError`` — der
        Widerruf bricht ab, statt den Zweig zu ueberspringen.
        Mutant 18: der Default ``True`` fuehrt IN den Zweig; das Objekt bekommt
        ``must_change_password = False`` angeheftet und ``save`` wird gerufen
        (``save.call_count == 1`` statt ``0``) — ein Zwangswechsel wuerde auf
        einem Nicht-Model-Objekt still aufgeloest.

        Salt-Rotation und Audit-Zeile sind ersetzt: sie erwarten ein echtes
        ``User``-Model und sind nicht Gegenstand dieses Pins.
        """
        user = AnonymousUser()
        save_spy = Mock()
        user.save = save_spy
        monkeypatch.setattr(credentials, "rotate_offline_salt", Mock())
        monkeypatch.setattr(credentials, "_write_audit", Mock())

        revoke_credentials(user, reason=RevocationReason.PASSWORD_CHANGE)

        assert save_spy.call_count == 0
        assert "must_change_password" not in user.__dict__

    @pytest.mark.django_db
    def test_forced_change_save_is_narrowed_to_the_single_column(self, staff_user):
        """Pin: ``x_revoke_credentials__mutmut_21`` —
        ``user.save(update_fields=["must_change_password"])`` →
        ``user.save(update_fields=None)``.

        Original: der Teil-Save schreibt GENAU eine Spalte; der Spy sieht
        ``call(update_fields=["must_change_password"])``. Mutant 21: derselbe
        Spy sieht ``call(update_fields=None)`` — ein Full-Save, der alle
        Spalten der (moeglicherweise veralteten) Instanz zurueckschreibt
        (Lost Update auf ``role``/``is_active``/``password``/
        ``offline_key_salt``) und den ``post_save``-Receivern ein anderes
        ``update_fields`` reicht.

        Zusaetzlich der DB-Nachweis derselben Eigenschaft: eine parallel per
        ``QuerySet.update()`` deaktivierte Zeile bleibt deaktiviert, weil der
        Teil-Save ``is_active`` nicht anfasst. Beim Mutanten schreibt der
        Full-Save das alte ``is_active=True`` zurueck.
        """
        staff_user.must_change_password = True
        staff_user.save(update_fields=["must_change_password"])

        with patch.object(staff_user, "save") as save_spy:
            revoke_credentials(staff_user, reason=RevocationReason.PASSWORD_CHANGE)
        save_spy.assert_called_once_with(update_fields=["must_change_password"])

        # Zweiter, unabhaengiger Nachweis ueber die tatsaechlich geschriebenen
        # Spalten: die Instanz ist jetzt veraltet (is_active=True im Speicher).
        credentials.User.objects.filter(pk=staff_user.pk).update(is_active=False)
        staff_user.must_change_password = True

        revoke_credentials(staff_user, reason=RevocationReason.PASSWORD_RESET)

        frisch = credentials.User.objects.get(pk=staff_user.pk)
        assert frisch.is_active is False
        assert frisch.must_change_password is False


# ============================================================================
# flush_user_sessions — der gedeckelte Scan (R18, Refs #1476/#1666)
# ============================================================================


@pytest.mark.django_db
class TestFlushUserSessionsScanCap:
    """Deckel-Ermittlung und Kappungs-Flag."""

    def test_scan_falls_back_to_module_default_when_setting_is_absent(self, staff_user, monkeypatch):
        """Pin: ``x_flush_user_sessions__mutmut_6`` (Default
        ``_SESSION_SCAN_CAP_DEFAULT`` → ``None``) und
        ``x_flush_user_sessions__mutmut_9`` (Default ganz entfernt).

        Das Setting steht in ``settings/base.py`` immer, also ist der
        Modul-Default sonst unerreichbar. Hier wird es fuer die Dauer des Tests
        entfernt (aus dem ``Settings``-Objekt UND aus dem Attribut-Cache der
        ``LazySettings``).

        Original: ``cap`` faellt auf ``_SESSION_SCAN_CAP_DEFAULT`` (10000)
        zurueck, der Scan laeuft und flusht die Session.
        Mutant 6: ``cap is None`` → ``scanned >= None`` wirft ``TypeError:
        '>=' not supported between instances of 'int' and 'NoneType'``.
        Mutant 9: ``getattr(settings, "...")`` ohne Default wirft
        ``AttributeError``. In beiden Faellen bleibt die Session bestehen,
        weil die Funktion vorher abbricht.
        """
        monkeypatch.delitem(django_settings.__dict__, CAP_SETTING, raising=False)
        monkeypatch.delattr(django_settings._wrapped, CAP_SETTING, raising=False)
        assert not hasattr(django_settings._wrapped, CAP_SETTING)

        key = _db_session_for(staff_user)

        flush_user_sessions(staff_user)

        assert Session.objects.filter(session_key=key).exists() is False
        assert credentials._SESSION_SCAN_CAP_DEFAULT == 10000

    def test_normal_flush_does_not_raise_the_cap_alarm(self, staff_user, caplog):
        """Pin: ``x_flush_user_sessions__mutmut_14`` — ``capped = False`` →
        ``capped = True``.

        Original: der Deckel (Default 10000) wird von einer einzigen Session
        nie erreicht, es gibt KEINE ERROR-Meldung —
        ``[r.getMessage() for r in caplog.records if r.name == LOGGER_NAME
        and r.levelno == logging.ERROR] == []``.
        Mutant 14: die Vorbelegung ``True`` laesst den Kappungs-Alarm bei JEDEM
        Flush feuern; dieselbe Liste enthaelt dann die Meldung
        ``"Offline-Invalidierung: Session-Scan-Obergrenze (10000) für User
        <pk> erreicht — ..."``. Der dokumentierte Ops-Indikator waere ein
        Dauer-Falschalarm.

        Der einzige bestehende ``caplog``-Test der Funktion prueft nur die
        ANWESENHEIT der Meldung bei ``cap=0``, nie ihre Abwesenheit.
        """
        key = _db_session_for(staff_user)

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            flush_user_sessions(staff_user)

        assert Session.objects.filter(session_key=key).exists() is False
        fehler = [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME and r.levelno == logging.ERROR]
        assert fehler == []


@pytest.mark.django_db
class TestFlushUserSessionsScanShape:
    """Die Kostenzusage R18: gestreamt (``chunk_size``) und spaltenbeschraenkt (``.only``)."""

    def test_scan_streams_in_fixed_chunks(self, staff_user, monkeypatch):
        """Pin: ``x_flush_user_sessions__mutmut_16`` —
        ``.iterator(chunk_size=_SESSION_SCAN_CHUNK)`` →
        ``.iterator(chunk_size=None)``.

        Weisskasten-Pin, weil der Parameter das Ergebnis nicht veraendert, nur
        die Kosten: Django reicht ``chunk_size`` bis ``cursor.fetchmany``
        durch; ``None`` faellt in psycopg auf ``arraysize`` (1) zurueck — statt
        in 2000er-Bloecken wird zeilenweise geholt.

        Original: die aufgezeichneten Keyword-Argumente sind
        ``{"chunk_size": 2000}``. Mutant 16: ``{"chunk_size": None}``.
        """
        aufgezeichnet: list[dict] = []
        original_iterator = QuerySet.iterator

        def spy(self, *args, **kwargs):
            aufgezeichnet.append(dict(kwargs))
            return original_iterator(self, *args, **kwargs)

        monkeypatch.setattr(QuerySet, "iterator", spy)
        _db_session_for(staff_user)

        flush_user_sessions(staff_user)

        assert aufgezeichnet == [{"chunk_size": credentials._SESSION_SCAN_CHUNK}]
        assert credentials._SESSION_SCAN_CHUNK == 2000

    def test_scan_cost_is_independent_of_the_number_of_scanned_sessions(self, staff_user, admin_user):
        """Pin: ``x_flush_user_sessions__mutmut_20`` —
        ``.only("session_key", "session_data")`` → ``.only("session_key")``.

        Ohne ``session_data`` ist die Spalte deferred und ``get_decoded()``
        laedt sie je gescannter Session einzeln nach: aus 1 Scan-Query werden
        1+N. Gemessen wird deshalb nicht eine absolute Query-Zahl, sondern die
        EIGENSCHAFT, die R18 zusichert — die Query-Zahl haengt nicht an der
        Zahl der gescannten Sessions.

        Original: beide Durchlaeufe (2 bzw. 10 gescannte Sessions, je genau
        eine geflushte) brauchen dieselbe Query-Zahl.
        Mutant 20: der zweite Durchlauf braucht 8 Queries mehr als der erste.
        """
        _anonymous_db_session()
        key_a = _db_session_for(staff_user)
        assert Session.objects.filter(expire_date__gte=credentials.timezone.now()).count() == 2

        with CaptureQueriesContext(connection) as wenige:
            flush_user_sessions(staff_user)
        assert Session.objects.filter(session_key=key_a).exists() is False

        for _ in range(8):
            _anonymous_db_session()
        key_b = _db_session_for(admin_user)
        assert Session.objects.filter(expire_date__gte=credentials.timezone.now()).count() == 10

        with CaptureQueriesContext(connection) as viele:
            flush_user_sessions(admin_user)
        assert Session.objects.filter(session_key=key_b).exists() is False

        assert len(viele.captured_queries) == len(wenige.captured_queries)


@pytest.mark.django_db
class TestFlushUserSessionsLogLines:
    """Die beiden Log-Zeilen sind die einzige Spur — es gibt hier keine Audit-Zeile."""

    def test_info_line_names_count_and_user(self, staff_user, caplog):
        """Pin: ``x_flush_user_sessions__mutmut_39`` (``user.pk`` → ``None``),
        ``x_flush_user_sessions__mutmut_43`` (XX-Sentinels um den Text) und
        ``x_flush_user_sessions__mutmut_44`` (Text komplett kleingeschrieben).

        Original (gerendert): ``"Offline-Invalidierung: 1 Session(s) für User
        <pk> geflusht."``.
        Mutant 39: ``"... für User None geflusht."`` — der Flush ist keiner
        Person mehr zuzuordnen (``REVOCATION_EFFECTS`` schreibt fuer Lockout
        und Rechteentzug bewusst KEINE Audit-Zeile, diese INFO-Zeile ist die
        einzige Spur).
        Mutant 43: ``"XXOffline-Invalidierung: 1 Session(s) für User <pk>
        geflusht.XX"``.
        Mutant 44: ``"offline-invalidierung: 1 session(s) für user <pk>
        geflusht."``.

        Verglichen wird deshalb die vollstaendige Meldung per ``==``, nicht per
        Teilstring.
        """
        key = _db_session_for(staff_user)

        with translation.override("de"), caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            flush_user_sessions(staff_user)

        assert Session.objects.filter(session_key=key).exists() is False
        assert _messages_of(caplog) == [f"Offline-Invalidierung: 1 Session(s) für User {staff_user.pk} geflusht."]

    def test_cap_error_line_is_complete_and_names_cap_and_user(self, staff_user, settings, caplog):
        """Pin: ``x_flush_user_sessions__mutmut_48`` (``user.pk`` → ``None``)
        sowie die Textmutanten ``52`` (XX um Fragment 1), ``55`` (XX um
        Fragment 2), ``56`` (Fragment 2 klein), ``57`` (Fragment 2 GROSS),
        ``58`` (XX um Fragment 3), ``59`` (Fragment 3 klein) und ``60``
        (Fragment 3 GROSS).

        Diese ERROR-Zeile ist das EINZIGE Betriebssignal dafuer, dass der
        DoS-Deckel gegriffen hat und ein Session-Rest ungeflusht blieb (samt
        der fachlichen Entwarnung: Salt rotiert, Rest laeuft ab, Refs
        #1858/#1868).

        Original (gerendert, dreiteilig implizit konkateniert):
        ``"Offline-Invalidierung: Session-Scan-Obergrenze (0) für User <pk>
        erreicht — Rest nicht geflusht (Salt ist rotiert; Rest läuft bei
        Inaktivität ab, spätestens am absoluten Sitzungs-Maximum, Refs
        #1858/#1868)."``

        Beim Mutanten 48 steht ``User None`` statt der PK; bei 52/55/58 haengen
        ``XX``-Sentinels an den Fragmentraendern; bei 56/57/59/60 kippt die
        Gross-/Kleinschreibung des jeweiligen Fragments (z.B. ``"REST NICHT
        GEFLUSHT (SALT IST ROTIERT; ..."``). Der bestehende Test prueft nur den
        Teilstring ``"Session-Scan-Obergrenze"`` und laesst all das durch —
        hier wird die volle Meldung mit ``==`` verglichen.
        """
        settings.OFFLINE_REVOCATION_SESSION_SCAN_LIMIT = 0
        key = _db_session_for(staff_user)

        with translation.override("de"), caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            flush_user_sessions(staff_user)

        # cap=0 bricht vor der ersten Dekodierung ab: nichts geflusht, keine INFO-Zeile.
        assert Session.objects.filter(session_key=key).exists() is True
        erwartet = (
            f"Offline-Invalidierung: Session-Scan-Obergrenze (0) für User {staff_user.pk} "
            "erreicht — Rest nicht geflusht (Salt ist rotiert; Rest läuft bei Inaktivität ab, "
            "spätestens am absoluten Sitzungs-Maximum, Refs #1858/#1868)."
        )
        assert _messages_of(caplog) == [erwartet]
