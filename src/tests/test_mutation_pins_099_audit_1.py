"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.audit`` (Batch ``audit-1``).

Refs https://github.com/anlaufstelle/app/issues/2174.
Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Vollmutations-Lauf auf main@1886bb2a (frischer Mutantenbaum), Batch
``audit-1`` (60 Survivor-Mutanten in drei Modulen: ``core.services.audit.hash``
24 — ``_get_audit_hash_key`` 21, ``hmac_hash_value`` 1, ``hmac_hash_email`` 2 —,
``core.services.audit.helpers`` 20 — ``log_audit_event`` 15, ``audit_event`` 1,
``audit_system_view`` 4 —, ``core.services.audit.diff`` 16 —
``changed_fields_vs_db`` 5, ``_vergleichswert`` 2, ``changed_data_json_slugs``
9). Andere Funktionen/Module des ``audit``-Subpackages (``chain.py``,
``detail_policy.py``, ``filters.py``, ``hwm.py``) gehören anderen Batches und
werden hier nicht angefasst.

Fünf wiederkehrende Mutationsachsen in diesem Batch:

* **Settings-Schlüssel-Auflösung** (``_get_audit_hash_key``): ``raw``/``secret``
  werden ignoriert (``raw = None``, ``... and ""``, ``getattr(None, ...)``),
  unter falschem Schlüsselnamen gelesen (``"XXAUDIT_HASH_KEYXX"``,
  ``"audit_hash_key"``, ``"XXSECRET_KEYXX"``, ``"secret_key"`` — ECHTE
  Attributnamen, kein i18n-Wortlaut, deshalb ``test`` und nicht ``skip``) oder
  der leere Fallback wird durch einen statischen Vier-Zeichen-„Schlüssel"
  ``"XXXX"`` ersetzt. Alle Prio 1 — genau die Schlüsselableitung, die laut
  Docstring einen SECRET_KEY-Leak nicht rückwirkend auf Audit-Hashes
  durchschlagen lassen soll. Default-Wert-Mutationen auf denselben
  ``getattr``-Aufrufen (``None``/weggelassen/``"XXXX"`` als DRITTES Argument)
  sind dagegen ``equivalent``: ``AUDIT_HASH_KEY`` und ``SECRET_KEY`` sind in
  JEDEM Settings-Profil dieses Repos (``base.py``) immer als Attribut
  vorhanden — der ``getattr``-Default ist toter Code, verifiziert per
  Diff + Testlauf unter der Mutante (Abschnitt 3 im Bericht). Der
  ``or "XXXX"``-Fallback auf der ``secret``-Zeile
  (``x__get_audit_hash_key__mutmut_27``) ist ebenfalls ``equivalent``: Django
  weist jeden Zugriff auf ein leeres ``SECRET_KEY`` mit
  ``ImproperlyConfigured`` zurueck (``LazySettings.__getattr__``-Spezialfall,
  auch ueber ``getattr(..., default)``) — der Fallback ist unerreichbar.
* **has_override-Kombinatorik** (``log_audit_event``): die Bedingung
  ``user is not _UNSET or facility is not _UNSET`` steuert, ob die
  RLS-Session-GUCs für Pre-Auth-Flows (Login-Lockout, Passwort-Reset)
  nachgezogen werden (Security R3). ``or``→``and`` sowie die beiden
  invertierten Einzelbedingungen verkennen einen ECHTEN Einzel-Override
  (nur ``facility=`` ODER nur ``user=``, das reale Muster in
  ``views/mfa.py``) als „kein Override" — der folgende ``AuditLog``-INSERT
  verletzt dann die WITH-CHECK-Policy (Migration 0085). Die zweite
  Bedingung (``not is_authenticated``) hat ein Geschwister-Bündel: ``and``→
  ``or`` sowie zwei Wege, die Authentifizierungsprüfung zu entkernen
  (``getattr(None, ...)``, falscher Attributname) lassen den Sync-Zweig
  auch für AUTHENTIFIZIERTE Requests mit Override laufen und setzen dabei
  ``app.is_super_admin`` mitten im Request unbeabsichtigt zurück — alle
  Prio 1. Default-Wert-Mutationen auf ``getattr(request.user,
  "is_authenticated", …)`` sind wieder ``equivalent``: jedes Django-User-
  bzw. ``AnonymousUser``-Objekt trägt ``is_authenticated`` immer.
  ``bool(getattr(user, "is_super_admin", False))`` bleibt bei
  ``None``-statt-``False``-Default ebenfalls ``equivalent`` — ``bool(None)
  == bool(False)``, der Wrapper neutralisiert die Mutante unabhängig davon,
  ob das Attribut existiert.
* **Meldungs-Quelle in ``validate_audit_detail``-Aufrufen** (``log_audit_event``,
  ``audit_event``): ``source=f"…({action})"`` → ``source=None``. Ein nicht
  deklarierter ``detail``-Schlüssel löst unter ``AUDIT_DETAIL_STRICT=True``
  (Testprofil) eine Exception aus, deren Text die Aufrufstelle nennt — die
  Mutante verfälscht diesen Text auf ``"aus None:"``. Meldungs-Verlust,
  Prio 3, exakte Assertion auf den vollen Nachrichtentext.
* **``lock``-Default UND ``.pk``-Auflösung bei FKs** (``changed_fields_vs_db``,
  ``changed_data_json_slugs``): ``lock=True``→``lock=False`` nimmt der
  Standard-Aufrufform (kein Caller übergibt ``lock=`` explizit) die
  ``SELECT … FOR UPDATE``-Sperre — TOCTOU zwischen Diff und ``save()``,
  Prio 1. Die invertierte/entkernte ``is_relation``-Bedingung in
  ``changed_fields_vs_db`` (``value is None`` statt ``is not None``,
  ``hasattr(None, "pk")``, falscher Attributname ``"XXpkXX"``/``"PK"``)
  lässt ein als Objekt übergebenes FK-Feld NIE auf ``.pk`` auflösen — jede
  unveränderte FK-Aktualisierung meldet sich fälschlich als geändert
  (fachliches Verhalten, Prio 2).
* **``_ABWESEND``-Sentinel, Datei-Marker-Kurzschluss und Schleifenabbruch**
  (``changed_data_json_slugs``): ``_ABWESEND`` wird durch ``None`` ersetzt
  (explizit oder implizit über den 2-Arg-``dict.get``) und macht einen
  echten Zu-/Abgang eines ``data_json``-Slugs mit leerem Wert ununterscheidbar
  von „unverändert" — Prio 1 (Diff verliert Felder). Der Datei-Marker-Filter
  (``is_file_marker(alt) or is_file_marker(neu)``) wird zu ``and`` bzw. verliert
  eine Seite auf ``None`` — ein Marker-Text-Übergang wird dann fälschlich als
  Inhaltsänderung gemeldet statt übersprungen (fachliches Verhalten, Prio 2).
  ``continue``→``break`` in derselben Schleife ist dagegen Prio 1: ein
  übersprungener Marker-Slug bricht die GESAMTE Schleife ab und verschluckt
  jede nachfolgende echte Änderung. ``_vergleichswert`` selbst verliert mit
  ``default=None`` bzw. dem impliziten Platzhalter-Default von
  ``safe_decrypt`` (2-Arg-Aufruf) die Fallback-Garantie, dass zwei
  VERSCHIEDENE korrupte Verschlüsselungs-Marker unterscheidbar bleiben
  (Docstring-Vertrag, Prio 1).

Konventionen wie ``test_mutation_pins_099_deletion_a.py``: eine Testklasse je
Bruchpunkt, jeder Test nennt seine Mutanten samt abweichendem Verhalten in der
Docstring, Zusicherungen vergleichen EXAKT (``==``), nie per Teilstring
(mutmut erzeugt ``"XX<text>XX"``-Varianten); ``pytest.mark.django_db``
modulweit, keine Fakes — echte Model-/Request-Objekte; kein Import aus
``tests.test_rls_functional``. RLS-/GUC-Nachweise laufen direkt über
``django.db.connection``-Cursor (``current_setting``/``set_config``) bzw. über
den realen ``AuditLog``-INSERT gegen die WITH-CHECK-Policy — nicht über
Mock-Spies auf interne Helfer.
"""

from __future__ import annotations

import hashlib
import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import AuditLog
from core.services.audit import (
    UnknownAuditDetailKeyError,
    audit_event,
    audit_system_view,
    changed_data_json_slugs,
    changed_fields_vs_db,
    hmac_hash_email,
    hmac_hash_value,
    log_audit_event,
)
from core.services.audit.diff import _vergleichswert
from core.services.audit.hash import _get_audit_hash_key

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


#: ``FOR UPDATE``-Statements verraten die Zeilensperre; Muster wie
#: ``test_mutation_pins_099_deletion_a.py`` (nicht importiert — eigene Kopie,
#: Pin-Dateien bleiben bewusst voneinander unabhaengig).
_LOCK_RE = re.compile(r'FROM "(?P<table>core_\w+)"[^;]*FOR UPDATE', re.IGNORECASE | re.DOTALL)


def _locking_statements(captured, table):
    hits = []
    for query in captured.captured_queries:
        match = _LOCK_RE.search(query["sql"])
        if match and match.group("table") == table:
            hits.append(query["sql"])
    return hits


def _anon_request(rf):
    """Request wie ihn die Middleware-Kette für einen Pre-Auth-Flow liefert:
    Session + anonymer ``user``."""
    request = rf.post("/")
    request.session = SessionStore()
    request.user = AnonymousUser()
    return request


def _authenticated_request(rf, user):
    request = rf.post("/")
    request.session = SessionStore()
    request.user = user
    return request


#: Datei-Marker (Stufe A, ``is_file_marker``) für die ``changed_data_json_slugs``-Tests.
_MARKER = {"__file__": True, "attachment_id": "11111111-1111-1111-1111-111111111111"}


# ---------------------------------------------------------------------------
# core.services.audit.hash — _get_audit_hash_key / hmac_hash_email
# ---------------------------------------------------------------------------


class TestGetAuditHashKeyGoldenSchluesselableitung:
    """``_get_audit_hash_key``: bevorzugt ``AUDIT_HASH_KEY``, sonst
    ``SHA256(SECRET_KEY)``. Golden-Rekonstruktion unabhängig vom
    Produktionscode."""

    def test_bevorzugt_konfigurierten_audit_hash_key(self, settings):
        """Pins ``x__get_audit_hash_key__mutmut_1`` (``raw = None``), ``_2``
        (``... and ""`` — ``raw`` wird immer leer), ``_3``
        (``getattr(None, "AUDIT_HASH_KEY", "")`` statt ``getattr(settings, ...)``),
        ``_9`` (Schlüsselname ``"XXAUDIT_HASH_KEYXX"``), ``_10`` (Schlüsselname
        ``"audit_hash_key"``) — alle vier ignorieren ein GESETZTES
        ``AUDIT_HASH_KEY`` und fallen fälschlich auf den SECRET_KEY-Fallback
        zurück. UND ``_13`` (``raw.encode(None)`` — ``TypeError``), ``_14``
        (``raw.encode("XXutf-8XX")`` — ungültiger Codec-Name, ``LookupError``)
        auf demselben (dann erreichten) ``raw``-Zweig."""
        settings.AUDIT_HASH_KEY = "pin-w9-audit-schluessel"
        assert _get_audit_hash_key() == b"pin-w9-audit-schluessel"

    def test_faellt_ohne_audit_hash_key_auf_sha256_des_echten_secret_key_zurueck(self, settings):
        """Pins ``x__get_audit_hash_key__mutmut_12`` (``raw = getattr(...) or
        "XXXX"`` statt ``or ""`` — bei leerem ``AUDIT_HASH_KEY`` würde ein
        statischer Vier-Zeichen-„Schlüssel" ``b"XXXX"`` sofort zurückkommen,
        statt auf den SECRET_KEY-Fallback weiterzufallen), ``_17`` (``secret =
        getattr(...) and ""`` — bei GESETZTEM ``SECRET_KEY`` wird ``secret``
        trotzdem leer), ``_18`` (``getattr(None, "SECRET_KEY", "")``), ``_24``
        (Schlüsselname ``"XXSECRET_KEYXX"``), ``_25`` (Schlüsselname
        ``"secret_key"``) — alle fünf ignorieren den ECHTEN ``SECRET_KEY``
        bzw. den korrekten Fallback-Pfad dorthin.

        ``x__get_audit_hash_key__mutmut_27`` (``secret = getattr(...) or
        "XXXX"`` statt ``or ""``) ist dagegen ``equivalent``: Django weist
        laut ``django.conf.LazySettings.__getattr__`` JEDEN Zugriff auf ein
        leeres ``SECRET_KEY`` mit ``ImproperlyConfigured`` zurück (Spezialfall
        ``name == "SECRET_KEY" and not val``) — auch über ``getattr(settings,
        "SECRET_KEY", default)``, der ``default`` faengt das nicht ab (nur
        ``AttributeError`` wird von ``getattr`` abgefangen). ``secret`` kann
        also nie leer UND erreichbar zugleich sein; der ``or "XXXX"``-Zweig
        ist unerreichbarer Code (empirisch verifiziert: ``settings.SECRET_KEY
        = ""`` lässt bereits das UNMUTIERTE Original mit
        ``ImproperlyConfigured`` scheitern, nicht erst die Mutante)."""
        settings.AUDIT_HASH_KEY = ""
        erwarteter_schluessel = settings.SECRET_KEY
        assert erwarteter_schluessel, "Testprofil setzt SECRET_KEY nicht leer — Testannahme verletzt."
        assert _get_audit_hash_key() == hashlib.sha256(erwarteter_schluessel.encode("utf-8")).digest()


class TestHmacHashEmailGoldenNormalisierung:
    """``hmac_hash_email``: stabile Kleinschreibungs-Normalisierung vor dem Hash."""

    def test_email_normalisierung_bleibt_kleinschreibung(self):
        """Pin ``x_hmac_hash_email__mutmut_4``: ``.strip().lower()`` →
        ``.strip().upper()``. Der Forensik-Vertrag (Docstring: „gleiche
        E-Mail -> gleicher Hash") verlangt STABIL kleingeschriebene
        Normalisierung — sonst driften neu geschriebene Hashes von allen
        vorhandenen (mit ``.lower()`` geschriebenen) Audit-Zeilen derselben
        Adresse ab und der Lookup einer bekannten E-Mail bricht."""
        assert hmac_hash_email("Pin-Envelope@Beispiel.ORG") == hmac_hash_value("pin-envelope@beispiel.org")

    def test_email_wert_fliesst_tatsaechlich_in_den_hash_ein(self):
        """Pin ``x_hmac_hash_email__mutmut_5``: ``str(email)`` → ``str(None)``.
        Der Hash MUSS von der tatsächlichen Eingabe abhängen — sonst
        kollabieren alle E-Mail-Adressen auf denselben Hash (totaler
        Korrelationsverlust im Audit-Trail)."""
        assert hmac_hash_email("erste@beispiel.org") != hmac_hash_email("zweite@beispiel.org")


# ---------------------------------------------------------------------------
# core.services.audit.helpers — log_audit_event / audit_event / audit_system_view
# ---------------------------------------------------------------------------


class TestLogAuditEventHasOverrideEinzelUeberschreibung:
    """``has_override = user is not _UNSET or facility is not _UNSET``: ein
    ECHTER Einzel-Override (nur EINE der beiden Kwargs) muss die
    RLS-Session-GUC ``app.current_facility_id`` für den anschließenden
    ``AuditLog``-INSERT syncen (Security R3) — sonst verletzt der INSERT unter
    der NOBYPASSRLS-App-Rolle die WITH-CHECK-Policy von ``core_auditlog``
    (Migration 0085) für eine nicht-NULL ``facility``. Nachweis direkt über
    die GUC (nicht über INSERT-Erfolg/-Fehlschlag): die Standard-Testverbindung
    läuft unter einer Rolle, die den WITH-CHECK-Branch selbst bei
    nicht-synchronisierter GUC nicht sichtbar verletzt (verifiziert per
    Probe-Skript) — die GUC-Introspektion bleibt der einzige rollen-
    unabhängige, direkte Nachweis dieses Zweigs."""

    def test_nur_facility_override_synct_die_session_guc(self, rf, facility):
        """Pins ``x_log_audit_event__mutmut_8`` (``or``→``and``: mit NUR
        ``facility=`` überschrieben liefert ``and`` ``False``) und ``_10``
        (``facility is not _UNSET`` → ``facility is _UNSET`` — dieselbe
        Wirkung bei reinem Facility-Override). Realer Aufrufer desselben
        Musters: ``views/mfa.py`` (``facility=`` ohne ``user=``)."""
        request = _anon_request(rf)
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.current_facility_id', '', false)")
        log_audit_event(
            request,
            AuditLog.Action.LOGIN_FAILED,
            facility=facility,
            target_type="Facility",
            target_id=str(facility.pk),
        )
        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('app.current_facility_id', true)")
            (value,) = cur.fetchone()
        assert value == str(facility.pk)

    def test_nur_user_override_synct_die_session_guc(self, rf, facility, staff_user):
        """Pins ``x_log_audit_event__mutmut_8`` (``or``→``and``, hier mit NUR
        ``user=`` überschrieben) und ``_9`` (``user is not _UNSET`` →
        ``user is _UNSET`` — dieselbe Wirkung bei reinem User-Override).
        ``facility`` bleibt ``_UNSET`` und fällt auf ``request.current_facility``
        zurück (wie die Middleware es für einen echten Request setzen würde)."""
        request = _anon_request(rf)
        request.current_facility = facility
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.current_facility_id', '', false)")
        log_audit_event(
            request,
            AuditLog.Action.LOGIN_FAILED,
            user=staff_user,
            target_type="User",
            target_id=str(staff_user.pk),
        )
        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('app.current_facility_id', true)")
            (value,) = cur.fetchone()
        assert value == str(facility.pk)


class TestLogAuditEventAuthentifizierterRequestKeinErneutesSyncen:
    """Der zweite Zweig der Sync-Bedingung (``not is_authenticated``) darf NUR
    für anonyme Requests greifen. Bricht er, resettet ein Override auf einem
    AUTHENTIFIZIERTEN Request die von der Middleware bereits gesetzte
    ``app.is_super_admin``-GUC mitten im Request."""

    def test_authentifizierter_override_synct_die_guc_nicht_erneut(self, rf, facility, staff_user):
        """Pins ``x_log_audit_event__mutmut_31`` (``and``→``or`` auf
        ``has_override and not is_authenticated``), ``_33``
        (``getattr(None, "is_authenticated", False)`` statt
        ``getattr(request.user, ...)`` — entkernt die Prüfung IMMER auf
        ``False``), ``_39`` (Attributname ``"XXis_authenticatedXX"``), ``_40``
        (Attributname ``"IS_AUTHENTICATED"``) — alle vier lassen den
        Sync-Zweig auch bei einem authentifizierten Request mit Override
        laufen und setzen ``is_super_admin`` dabei auf den (falschen) Wert
        von ``staff_user`` zurück."""
        request = _authenticated_request(rf, staff_user)
        with connection.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.current_facility_id', %s, false), "
                "       set_config('app.is_super_admin', %s, false)",
                [str(facility.pk), "true"],
            )
        log_audit_event(
            request,
            AuditLog.Action.LOGIN,
            facility=facility,
            target_type="Facility",
            target_id=str(facility.pk),
        )
        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('app.is_super_admin', true)")
            (value,) = cur.fetchone()
        assert value == "true", "app.is_super_admin wurde unerwartet zurueckgesetzt (staff_user ist kein super_admin)."


class TestValidateAuditDetailQuellenAngabe:
    """``log_audit_event``/``audit_event`` benennen ihre eigene Aufrufstelle
    als ``source`` für die Nicht-deklariert-Meldung von
    ``validate_audit_detail`` (``AUDIT_DETAIL_STRICT=True`` im Testprofil)."""

    def test_log_audit_event_nennt_die_eigene_aufrufstelle(self, rf, staff_user):
        """Pin ``x_log_audit_event__mutmut_76``: ``source=f"log_audit_event({action})"``
        → ``source=None``. Meldungs-Verlust, exakte Assertion auf den vollen
        Text."""
        request = _authenticated_request(rf, staff_user)
        unbekannt = "pin_audit_1_nicht_deklarierter_schluessel_a"
        erwartete_meldung = (
            f"Nicht deklarierte AuditLog-detail-Schluessel aus log_audit_event({AuditLog.Action.LOGIN}): "
            f"{[unbekannt]}. "
            "Jeder detail-Schluessel muss in core.services.audit.detail_policy.AUDIT_DETAIL_KEYS "
            "mit Begruendung stehen (Datenminimierung im append-only Log, Refs #1944). "
            "Klienten-Freitext gehoert nicht hinein — statt Klartext einen HMAC "
            "(hmac_hash_value) oder die target_id verwenden."
        )
        with pytest.raises(UnknownAuditDetailKeyError) as exc:
            log_audit_event(request, AuditLog.Action.LOGIN, detail={unbekannt: "wert"})
        assert str(exc.value) == erwartete_meldung

    def test_audit_event_nennt_die_eigene_aufrufstelle(self, facility, staff_user):
        """Pin ``x_audit_event__mutmut_30``: identischer Bug in der
        Service-/Cron-Variante ``audit_event`` (``source=f"audit_event({action})"``
        → ``source=None``)."""
        unbekannt = "pin_audit_1_nicht_deklarierter_schluessel_b"
        erwartete_meldung = (
            f"Nicht deklarierte AuditLog-detail-Schluessel aus audit_event({AuditLog.Action.LOGIN}): "
            f"{[unbekannt]}. "
            "Jeder detail-Schluessel muss in core.services.audit.detail_policy.AUDIT_DETAIL_KEYS "
            "mit Begruendung stehen (Datenminimierung im append-only Log, Refs #1944). "
            "Klienten-Freitext gehoert nicht hinein — statt Klartext einen HMAC "
            "(hmac_hash_value) oder die target_id verwenden."
        )
        with pytest.raises(UnknownAuditDetailKeyError) as exc:
            audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility, detail={unbekannt: "wert"})
        assert str(exc.value) == erwartete_meldung


class TestAuditSystemViewTargetTypeDefault:
    """``audit_system_view``: ``target_type``-Default bleibt der leere String,
    wenn der Aufrufer ihn (gültig laut Signatur) weglässt."""

    def test_target_type_default_bleibt_leerstring(self, rf, staff_user):
        """Pin ``x_audit_system_view__mutmut_1``: ``target_type: str = ""`` →
        ``target_type: str = "XXXX"``. Ohne explizites ``target_type`` würde
        die persistierte ``AuditLog``-Zeile ``target_type="XXXX"`` statt
        ``""`` tragen — ein verfälschtes Audit-Feld."""
        request = _authenticated_request(rf, staff_user)
        entry = audit_system_view(request, AuditLog.Action.SYSTEM_VIEW)
        assert entry.target_type == ""


# ---------------------------------------------------------------------------
# core.services.audit.diff — changed_fields_vs_db / _vergleichswert /
# changed_data_json_slugs
# ---------------------------------------------------------------------------


class TestDiffFunktionenLockDefault:
    """``lock=True`` ist der Default BEIDER Diff-Funktionen — kein Aufrufer im
    Repo übergibt ``lock=`` explizit. Ohne die ``SELECT … FOR UPDATE``-Sperre
    entsteht ein TOCTOU-Fenster zwischen Diff und dem ``save()`` des Callers."""

    def test_changed_fields_vs_db_sperrt_ohne_explizites_lock_argument(self, case_open):
        """Pin ``x_changed_fields_vs_db__mutmut_1``: ``lock=True`` → ``lock=False``."""
        with CaptureQueriesContext(connection) as captured:
            changed_fields_vs_db(case_open, {"title": case_open.title})
        hits = _locking_statements(captured, "core_case")
        assert hits, f"Kein FOR UPDATE auf core_case gefunden: {[q['sql'] for q in captured.captured_queries]}"

    def test_changed_data_json_slugs_sperrt_ohne_explizites_lock_argument(self, case_open):
        """Pin ``x_changed_data_json_slugs__mutmut_1``: identischer
        ``lock``-Default-Bug in der Geschwisterfunktion für dynamische
        Formularfelder."""
        with CaptureQueriesContext(connection) as captured:
            changed_data_json_slugs(case_open, {})
        hits = _locking_statements(captured, "core_case")
        assert hits, f"Kein FOR UPDATE auf core_case gefunden: {[q['sql'] for q in captured.captured_queries]}"


class TestChangedFieldsVsDbFkAlsObjekt:
    """Ein als volles Objekt übergebenes FK-Feld (ModelForm-Normalfall) muss
    über ``attname``/``.pk`` verglichen werden."""

    def test_unveraenderter_fk_als_objekt_bleibt_ungeaendert(self, case_open):
        """Pins ``x_changed_fields_vs_db__mutmut_19`` (``value is not None`` →
        ``value is None`` — die Bedingung ist danach nie mehr erfüllbar),
        ``_20`` (``hasattr(value, "pk")`` → ``hasattr(None, "pk")``), ``_24``
        (Attributname ``"XXpkXX"``), ``_25`` (Attributname ``"PK"``) — alle
        vier lösen ein FK-Objekt nie mehr auf ``.pk`` auf, wodurch JEDE
        unveränderte FK-Aktualisierung fälschlich als Änderung gemeldet wird
        (``row[attname]`` [roher PK] ist nie gleich dem vollen Objekt)."""
        changed = changed_fields_vs_db(case_open, {"client": case_open.client})
        assert changed == []


class TestVergleichswertGoldenFallback:
    """``_vergleichswert``: ein NICHT entschlüsselbares Token fällt auf den
    Rohwert zurück — zwei verschiedene korrupte Marker müssen unterscheidbar
    bleiben (Docstring-Vertrag)."""

    def test_unentschluesselbare_marker_bleiben_ueber_default_unterscheidbar(self):
        """Pins ``x__vergleichswert__mutmut_2`` (``safe_decrypt(value,
        default=value)`` → ``default=None`` — zwei verschiedene korrupte
        Marker würden beide zu ``None`` und wären ununterscheidbar) und
        ``_4`` (``safe_decrypt(value, )`` — 2-Arg-Aufruf, fällt auf
        ``safe_decrypt``s EIGENEN Default ``"[verschlüsselt]"`` zurück,
        derselbe Kollaps auf einen gemeinsamen Platzhalter)."""
        marker_a = {"__encrypted__": True, "value": "pin-audit-1-kaputt-a"}
        marker_b = {"__encrypted__": True, "value": "pin-audit-1-kaputt-b"}
        assert _vergleichswert(marker_a) != _vergleichswert(marker_b)


class TestChangedDataJsonSlugsSentinelZuUndAbgang:
    """``_ABWESEND`` unterscheidet „Slug fehlt komplett" von „Slug ist
    vorhanden mit Wert ``None``" — ein Default von ``None`` statt
    ``_ABWESEND`` macht Zu-/Abgänge mit leerem Wert unsichtbar."""

    def test_zugang_eines_slugs_mit_leerem_neuwert_wird_erkannt(self, case_open):
        """Pins ``x_changed_data_json_slugs__mutmut_26`` (``alt.get(slug,
        _ABWESEND)`` → ``alt.get(slug, None)``) und ``_28`` (``alt.get(slug,
        )`` — 2-Arg-Aufruf, impliziter ``None``-Default). Ein Slug, der NEU
        in ``new_data_json`` auftaucht und dort ``None`` trägt, muss trotzdem
        als Zugang erkannt werden."""
        assert case_open.data_json == {}
        changed = changed_data_json_slugs(case_open, {"neues_feld": None})
        assert changed == ["neues_feld"]

    def test_abgang_eines_slugs_mit_leerem_altwert_wird_erkannt(self, case_open):
        """Pins ``x_changed_data_json_slugs__mutmut_31`` (``neu.get(slug,
        _ABWESEND)`` → ``neu.get(slug, None)``) und ``_33`` (``neu.get(slug,
        )`` — 2-Arg-Aufruf). Ein Slug, der aus ``new_data_json`` fällt
        (Abgang), aber in der DB den Wert ``None`` trug, muss ebenfalls
        erkannt werden."""
        case_open.data_json = {"altes_feld": None}
        case_open.save(update_fields=["data_json"])
        changed = changed_data_json_slugs(case_open, {})
        assert changed == ["altes_feld"]


class TestChangedDataJsonSlugsFileMarkerUebergang:
    """``is_file_marker(alt) or is_file_marker(neu)`` überspringt einen Slug,
    sobald EINE Seite ein Datei-Marker ist — Anhangs-Änderungen haben ihren
    eigenen Audit-Pfad (Docstring)."""

    def test_marker_zu_text_uebergang_bleibt_ausserhalb_des_diffs(self, case_open):
        """Pins ``x_changed_data_json_slugs__mutmut_34`` (``or``→``and``) und
        ``_35`` (``is_file_marker(alt_wert)`` → ``is_file_marker(None)`` —
        immer ``False``). ALT ein Datei-Marker, NEU Klartext: das Original
        überspringt den Slug, beide Mutanten melden ihn fälschlich als
        Änderung."""
        case_open.data_json = {"anhang_feld": _MARKER}
        case_open.save(update_fields=["data_json"])
        changed = changed_data_json_slugs(case_open, {"anhang_feld": "jetzt Klartext"})
        assert changed == []

    def test_text_zu_marker_uebergang_bleibt_ausserhalb_des_diffs(self, case_open):
        """Pins ``x_changed_data_json_slugs__mutmut_34`` (``or``→``and``,
        Gegenrichtung) und ``_36`` (``is_file_marker(neu_wert)`` →
        ``is_file_marker(None)`` — immer ``False``). ALT Klartext, NEU ein
        Datei-Marker."""
        case_open.data_json = {"anhang_feld": "alter Klartext"}
        case_open.save(update_fields=["data_json"])
        changed = changed_data_json_slugs(case_open, {"anhang_feld": _MARKER})
        assert changed == []


class TestChangedDataJsonSlugsBreakVerlust:
    """``continue``→``break`` im Datei-Marker-Zweig: ein übersprungener
    Marker-Slug darf die Schleife NICHT vorzeitig beenden."""

    def test_marker_slug_stoppt_die_schleife_nicht(self, case_open):
        """Pin ``x_changed_data_json_slugs__mutmut_37``: ein Marker-Slug an
        erster Position (sortierte Reihenfolge) wird übersprungen — mit
        ``break`` statt ``continue`` geht dabei die nachfolgende ECHTE
        Änderung eines zweiten Slugs spurlos verloren."""
        case_open.data_json = {"a_anhang": _MARKER, "b_text": "alter Wert"}
        case_open.save(update_fields=["data_json"])
        changed = changed_data_json_slugs(case_open, {"a_anhang": _MARKER, "b_text": "neuer Wert"})
        assert changed == ["b_text"]
