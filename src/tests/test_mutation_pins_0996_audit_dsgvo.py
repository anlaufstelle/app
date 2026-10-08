"""Pins for the audit chain verdict and the decoding of the GDPR templates.

Source: mutation delta of the release-0.99.6 checkpoint, 2 mutants. Groups:

* ``core.services.audit.chain`` (1, Refs #2449) -- an unhashed row after the chain
  start yields a verdict whose ``ok`` is the boolean ``False``, not merely a falsy
  value (``x_verify_chain__mutmut_20``).
* ``core.services.client.dsgvo_package`` (1, Refs #235) -- the GDPR templates are
  decoded as UTF-8 whatever the process locale says, so umlauts survive
  (``x_render_document__mutmut_9``).
"""

from __future__ import annotations

import io

import pytest
from django.db import transaction

from core.models import AuditLog
from core.services.audit import audit_event
from core.services.audit.chain import verify_chain
from core.services.client import render_document
from core.services.client.dsgvo_package import DOCUMENTS, TEMPLATE_DIR
from core.services.system import bypass_replication_triggers

pytestmark = pytest.mark.django_db


def _tamper(pk, **fields):
    """Write an audit row directly, past the ``save()`` guard and the immutability trigger."""
    with transaction.atomic(), bypass_replication_triggers():
        AuditLog.objects.filter(pk=pk).update(**fields)


class TestUnhashedRowAfterChainStart:
    def test_verdict_is_the_boolean_false(self, facility, staff_user):
        """An unhashed row after the first hashed row breaks the chain; ``ok`` is ``False``.

        ``x_verify_chain__mutmut_20`` returns ``None`` and breaks the boolean contract of
        ``ChainResult.ok``.
        """
        audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
        trailing = audit_event(AuditLog.Action.LOGOUT, user=staff_user, facility=facility)
        _tamper(trailing.pk, prev_hash="", entry_hash="")

        result = verify_chain(facility)

        assert result.ok is False
        assert result.first_break_id == str(trailing.pk)


class TestTemplatesAreDecodedAsUtf8:
    def test_umlauts_survive_a_non_utf8_locale(self, facility, monkeypatch):
        """With a process locale that decodes text files as Latin-1, the rendered document still
        carries the template's umlauts.

        The test image runs with a UTF-8 locale, which hides a read without an explicit
        encoding; the locale default is switched to Latin-1 here.
        ``x_render_document__mutmut_9`` reads with ``encoding=None`` and decodes the template
        with the locale default.
        """
        text_encoding = io.text_encoding
        monkeypatch.setattr(
            io,
            "text_encoding",
            lambda encoding, stacklevel=2: "latin-1" if encoding is None else text_encoding(encoding),
        )
        template = TEMPLATE_DIR / DOCUMENTS["verarbeitungsverzeichnis"]["filename"]
        heading = template.read_bytes().decode("utf-8").splitlines()[0]
        assert "ä" in heading

        content, _filename = render_document("verarbeitungsverzeichnis", facility)

        assert heading in content
        assert "Ã" not in content
