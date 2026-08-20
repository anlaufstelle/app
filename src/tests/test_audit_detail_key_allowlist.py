"""Laufzeit-Guard fuer die ``detail``-Schluessel des AuditLog (Refs #1944).

Datenminimierung im ``detail``-Payload war bis hierher reine Call-Site-
Konvention: ``TestAuditLogCreationAllowlist`` prueft nur, **wo** Audit-Zeilen
entstehen duerfen, nie **was** in ihnen steht. Ein neuer Service-Aufruf mit
``detail={"pseudonym": ...}`` oder einem beliebigen Freitextwert lief durch
kein Gate.

Diese Suite nagelt den Gegenentwurf fest: eine **Schluessel-Allowlist**
(``core.services.audit.detail_policy``), gegen die jeder Schreibvorgang zur
Laufzeit geprueft wird. Bewusst Keys statt Werte-Blacklist — eine Blacklist
aus ``pseudonym``/``notes``/``email`` faengt genau die Freitextwerte **nicht**,
die unter unverdaechtigen Schluesseln landen, und das ist der Fall, der wehtut.
"""

from __future__ import annotations

import logging

import pytest

from core.models import AuditLog
from core.services.audit import audit_event, log_audit_event
from core.services.audit.detail_policy import (
    AUDIT_DETAIL_KEYS,
    UnknownAuditDetailKeyError,
    validate_audit_detail,
)

pytestmark = pytest.mark.django_db


def _request(rf, user, facility):
    request = rf.get("/")
    request.user = user
    request.current_facility = facility
    request.META["REMOTE_ADDR"] = "127.0.0.1"
    return request


class TestUnbekannterSchluesselScheitertLaut:
    """Strikt-Modus (Tests/DEBUG): ein nicht deklarierter Schluessel bricht ab.

    Der Fehler soll dort auftreten, wo der Schreibpunkt entsteht — im
    Entwicklungs- und CI-Lauf, nicht erst im Betrieb.
    """

    def test_audit_event_lehnt_pseudonym_ab(self, facility, staff_user):
        with pytest.raises(UnknownAuditDetailKeyError) as exc:
            audit_event(
                AuditLog.Action.EXPORT,
                user=staff_user,
                facility=facility,
                target_type="Client",
                detail={"pseudonym": "SEC-RC-PII-PROBE-001"},
            )
        assert "pseudonym" in str(exc.value)

    def test_log_audit_event_lehnt_unbekannten_schluessel_ab(self, rf, facility, staff_user):
        with pytest.raises(UnknownAuditDetailKeyError) as exc:
            log_audit_event(
                _request(rf, staff_user, facility),
                AuditLog.Action.VIEW_QUALIFIED,
                detail={"klartext_notiz": "Herr M. war heute sehr aufgebracht"},
            )
        assert "klartext_notiz" in str(exc.value)

    def test_generischer_security_helper_lehnt_klartext_dateinamen_ab(self, facility, staff_user):
        """``log_attachment_violation`` minimiert seit #1751 auf ``filename_hash``.

        Der **generische** Helper darunter nahm ``filename`` bis hierher aber
        weiter entgegen — ein neuer Aufrufer haette am zentralen Hash vorbei
        den Klartextnamen in eine prune-exempte ``SECURITY_VIOLATION``-Zeile
        geschrieben.
        """
        from core.services.audit import audit_security_violation

        with pytest.raises(UnknownAuditDetailKeyError) as exc:
            audit_security_violation(
                facility,
                staff_user,
                target_type="Upload",
                target_id=None,
                reason="extension_blocked",
                filename="Bericht-Max-Mustermann.exe",
            )
        assert "filename" in str(exc.value)

    def test_meldung_nennt_nur_den_schluessel_nicht_den_wert(self, facility, staff_user):
        """Die Fehlermeldung darf die PII nicht selbst weitertragen."""
        with pytest.raises(UnknownAuditDetailKeyError) as exc:
            audit_event(
                AuditLog.Action.EXPORT,
                user=staff_user,
                facility=facility,
                detail={"klarname": "Erika Mustermann"},
            )
        assert "Erika Mustermann" not in str(exc.value)

    def test_kein_eintrag_bleibt_beim_abbruch_stehen(self, facility, staff_user):
        vorher = AuditLog.objects.count()
        with pytest.raises(UnknownAuditDetailKeyError):
            audit_event(AuditLog.Action.EXPORT, user=staff_user, facility=facility, detail={"klarname": "x"})
        assert AuditLog.objects.count() == vorher


class TestDeklarierteSchluesselPassieren:
    def test_bekannte_schluessel_schreiben_durch(self, facility, staff_user):
        entry = audit_event(
            AuditLog.Action.CLIENT_UPDATE,
            user=staff_user,
            facility=facility,
            target_type="Client",
            detail={"changed_fields": ["stage"], "old_stage": "identified", "new_stage": "qualified"},
        )
        entry.refresh_from_db()
        assert entry.detail["changed_fields"] == ["stage"]

    def test_leeres_detail_ist_erlaubt(self, facility, staff_user):
        entry = audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        assert entry.detail == {}

    def test_nur_die_oberste_ebene_wird_geprueft(self, facility, staff_user):
        """``changes`` (SETTINGS_CHANGE) traegt eine Feldnamen-Ebene darunter.

        Die Pruefung bleibt bewusst flach: die verschachtelten Schluessel sind
        Modellfeldnamen aus ``_AUDIT_VALUE_FIELDS``, die dort schon gegen eine
        eigene Allowlist laufen.
        """
        entry = audit_event(
            AuditLog.Action.SETTINGS_CHANGE,
            user=staff_user,
            facility=facility,
            target_type="Settings",
            detail={"changed_fields": ["retention_days"], "changes": {"retention_days": {"old": 30, "new": 60}}},
        )
        entry.refresh_from_db()
        assert entry.detail["changes"]["retention_days"]["new"] == 60


class TestBetriebVerschlucktKeinenEintrag:
    """Fail-Verhalten im Betrieb: melden, aber schreiben.

    Ein fehlender Audit-Eintrag ist schlimmer als ein zu breiter — das Log ist
    der Rechenschaftsnachweis (Art. 5 Abs. 2), und die schaerfsten Schreibpunkte
    (``SECURITY_VIOLATION``) laufen genau dann, wenn ohnehin etwas schiefgeht.
    """

    def test_unbekannter_schluessel_wird_geschrieben_und_gemeldet(self, settings, caplog, facility, staff_user):
        settings.AUDIT_DETAIL_STRICT = False
        with caplog.at_level(logging.ERROR, logger="core.services.audit.detail_policy"):
            entry = audit_event(
                AuditLog.Action.EXPORT,
                user=staff_user,
                facility=facility,
                detail={"klarname": "Erika Mustermann"},
            )
        entry.refresh_from_db()
        assert entry.pk is not None
        assert entry.detail == {"klarname": "Erika Mustermann"}
        assert any("klarname" in rec.getMessage() for rec in caplog.records)
        assert not any("Erika Mustermann" in rec.getMessage() for rec in caplog.records)


class TestAllowlistInhalt:
    def test_pseudonym_ist_nicht_deklariert(self):
        """Der Ausloeser des Tickets: das Klartext-Pseudonym bleibt draussen."""
        for key in ("pseudonym", "notes", "notiz", "email", "phone", "filename", "summary", "title"):
            assert key not in AUDIT_DETAIL_KEYS, f"{key!r} gehoert nicht in das AuditLog-detail"

    def test_jede_deklaration_traegt_eine_begruendung(self):
        leer = sorted(k for k, grund in AUDIT_DETAIL_KEYS.items() if not (grund or "").strip())
        assert not leer, f"Schluessel ohne Begruendung: {leer}"

    @pytest.mark.parametrize("key", ["name", "username", "actor_username", "note", "recipient", "purpose"])
    def test_legitime_freitextschluessel_sind_ausdruecklich_erlaubt(self, key):
        """Freitext, der bleiben muss — aber nur mit Begruendung im Code.

        ``name`` sind Konfigurationsbezeichner (Dokumentationstyp, Feldvorlage,
        DSGVO-Dokument), keine Klienten-PII; ``username``/``actor_username``
        sind Beschaeftigten-Kennungen und der einzige dauerhafte Nachweis einer
        Rechte-Eskalation bzw. eines Login-Versuchs.
        """
        assert key in AUDIT_DETAIL_KEYS
        assert len(AUDIT_DETAIL_KEYS[key].strip()) > 20, "Freitextschluessel brauchen eine echte Begruendung"


class TestValidatorDirekt:
    def test_gibt_das_detail_unveraendert_zurueck(self):
        detail = {"count": 3}
        assert validate_audit_detail(detail, source="test") is detail

    def test_none_ist_erlaubt(self):
        assert validate_audit_detail(None, source="test") is None

    def test_quelle_steht_in_der_meldung(self):
        with pytest.raises(UnknownAuditDetailKeyError) as exc:
            validate_audit_detail({"unbekannt": 1}, source="core.signals.audit.beispiel")
        assert "core.signals.audit.beispiel" in str(exc.value)
