"""Refs #2321 (H-1): Logger-Name und Modul laufen im JSON-Formatter durch den Scrub.

Der äußerste Fallback (``_last_resort_json`` über ``_record_identity``) scrubbt
beide Felder bereits — der Normalpfad übernahm sie roh. Heute ist das nicht
ausnutzbar (der Produktivcode bildet keinen Logger-Namen dynamisch), aber es
ist die letzte Stelle des JSON-Formatters, an der Rohtext in den Sink käme:
``logging.getLogger(request.path)`` genügte. Der Textformatter hat das Problem
nicht, er scrubbt die fertige Zeile.
"""

import json
import logging

from core.logging import JsonFormatter


def _record(name="core.test", module="probe", message="nichts besonderes"):
    record = logging.LogRecord(
        name=name,
        level=logging.WARNING,
        pathname="/app/src/core/probe.py",
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    record.module = module
    return record


class TestJsonFormatterScrubsIdentityFields:
    def test_logger_name_with_path_is_masked(self):
        entry = json.loads(JsonFormatter().format(_record(name="/clients/8f14e45f-ceea-467a-9c8e-000000000001/")))
        assert "8f14e45f" not in entry["logger"], "Logger-Name geht ungescrubbt in den Sink (Refs #2321 H-1)"

    def test_module_with_path_is_masked(self):
        entry = json.loads(JsonFormatter().format(_record(module="/clients/8f14e45f-ceea-467a-9c8e-000000000001/")))
        assert "8f14e45f" not in entry["module"], "Modulname geht ungescrubbt in den Sink (Refs #2321 H-1)"

    def test_logger_name_with_credential_is_masked(self):
        entry = json.loads(JsonFormatter().format(_record(name="password=geheim1234")))
        assert "geheim1234" not in entry["logger"]

    def test_normal_path_matches_the_fallback_treatment(self):
        """Normalpfad und äußerster Fallback behandeln dieselben Felder gleich —
        sonst hinge der Schutz daran, ob der Formatter gerade gestolpert ist."""
        from core.logging import _record_identity

        record = _record(name="/clients/8f14e45f-ceea-467a-9c8e-000000000001/")
        _level, fallback_name = _record_identity(record)
        entry = json.loads(JsonFormatter().format(record))
        assert entry["logger"] == fallback_name

    def test_ordinary_names_are_unchanged(self):
        entry = json.loads(JsonFormatter().format(_record(name="core.services.client.main", module="main")))
        assert entry["logger"] == "core.services.client.main"
        assert entry["module"] == "main"
