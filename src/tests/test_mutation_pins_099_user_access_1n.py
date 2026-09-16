"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.user_access``
(Nacharbeits-Batch ``user_access-1n``).

Quelle: Vollmutation #2174, Batch ``user_access-1n``
(``welle-l-0.99/reports/m-scratch/batch-user_access-1n.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-user_access-1n/triage-099-user_access-1n.json``).
13 Survivor-Mutanten in zwei Funktionen: ``_deliver_invitation`` (9,
``mutmut_3``-``mutmut_11``) und ``resend_user_invitation`` (4, ``mutmut_13``,
``mutmut_15``, ``mutmut_16``, ``mutmut_17``).

``_deliver_invitation`` protokolliert einen Versandfehler im ``except``-Zweig
mit der Meldung ``"Einladungs-E-Mail konnte nicht versendet werden"`` und dem
Kontext ``extra={"user_pk": user.pk}``; das ist auf ``ERROR``-Level
beobachtbar, unabhängig davon, ob der Aufruf über ``logger.exception`` (wie im
Mutantenbaum) oder ``logger.error(..., exc_info=False)`` erfolgt — beide Formen
erzeugen einen ``ERROR``-Record mit identischer Meldung und identischem
``extra``-Dict; nur die Anwesenheit des Tracebacks unterscheidet sich, was
hier bewusst nicht geprüft wird. ``resend_user_invitation`` verliert bei
fehlendem Setup-Zugang sonst den vollständigen Meldungstext der
``ValidationError`` — die Zusicherung prüft ``exc_info.value.messages`` exakt
gegen den vollständigen Originaltext.

Zusicherungen EXAKT (``==``/``is``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten, die eine ``in``-Prüfung bzw.
``pytest.raises(match=…)`` übersehen würde).
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from core.services.user_access import _deliver_invitation, resend_user_invitation

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestDeliverInvitationLogtVersandfehlerMitVollstaendigerMeldung:
    """``xǁ_deliver_invitationǁ__mutmut_3``-``mutmut_11`` — die Log-Zeile im
    ``except``-Zweig (Meldungstext, ``extra``-Dict, Schlüssel ``user_pk``)."""

    def test_email_delivery_failure_logs_exact_message_and_user_pk(self, caplog, staff_user, admin_user):
        """Tötet ``mutmut_3`` (Meldung → ``None``), ``mutmut_4`` (``extra`` →
        ``None``), ``mutmut_5`` (die Meldung entfällt als Positionsargument
        ganz — der Aufruf crasht mit ``TypeError``, weil ``logger.exception``
        ein Pflichtargument verliert), ``mutmut_6`` (``extra`` entfällt
        komplett — der Record hat dann kein ``user_pk``-Attribut mehr),
        ``mutmut_7``/``mutmut_8``/``mutmut_9`` (``XX``-Marker bzw. Klein-/
        Großschreibung derselben Meldung) und ``mutmut_10``/``mutmut_11``
        (Schlüssel ``user_pk`` → ``XXuser_pkXX`` bzw. ``USER_PK``): ein
        Versandfehler muss als EIN ``ERROR``-Record mit exakt dieser Meldung
        und exakt diesem Kontext-Schlüssel protokolliert werden, und
        ``_deliver_invitation`` muss ``False`` zurückgeben."""
        with (
            patch("core.services.user_access.send_invite_email", side_effect=Exception("smtp boom")),
            caplog.at_level(logging.ERROR, logger="core.services.user_access"),
        ):
            result = _deliver_invitation(staff_user, admin_user, "invite_create", None)

        records = [
            record
            for record in caplog.records
            if record.name == "core.services.user_access" and record.levelno == logging.ERROR
        ]
        assert len(records) == 1
        record = records[0]
        assert record.getMessage() == "Einladungs-E-Mail konnte nicht versendet werden"
        assert record.user_pk == staff_user.pk
        assert result is False


class TestResendUserInvitationMeldungBleibtVollstaendigErhalten:
    """``xǁresend_user_invitationǁ__mutmut_13/15/16/17`` — die
    ``ValidationError`` beim fehlenden Setup-Zugang."""

    def test_missing_setup_access_raises_validation_error_with_full_message(self, facility, staff_user, admin_user):
        """Tötet ``mutmut_13`` (``ValidationError(None)`` — Meldung
        verloren), ``mutmut_15`` (``XX…XX``-Marker), ``mutmut_16``
        (Kleinschreibung) und ``mutmut_17`` (Großschreibung) derselben
        Meldung: ``resend_user_invitation`` muss bei einem bereits
        eingerichteten oder noch mailfreien Zugang den vollständigen
        Originaltext werfen."""
        with pytest.raises(ValidationError) as exc_info:
            resend_user_invitation(staff_user, facility=facility, actor=admin_user)

        assert exc_info.value.messages == [
            "Ein Setup-Link kann nur für einen noch nicht eingerichteten Zugang versendet werden."
        ]
