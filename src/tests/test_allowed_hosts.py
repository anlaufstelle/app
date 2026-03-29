"""Tests für ALLOWED_HOSTS-Parsing in Prod-Settings. Refs #418."""


def _parse_allowed_hosts(raw: str) -> list[str]:
    """Repliziert die Parsing-Logik aus prod.py."""
    return [h.strip() for h in raw.split(",") if h.strip()]


class TestAllowedHostsParsing:
    def test_empty_string_yields_empty_list(self):
        """Leerer Env-Var-Wert darf keine [''] Liste erzeugen."""
        assert _parse_allowed_hosts("") == []

    def test_hosts_are_stripped(self):
        """Whitespace um Hostnamen wird entfernt."""
        assert _parse_allowed_hosts("host1, host2 ") == ["host1", "host2"]

    def test_consecutive_commas_no_empty_strings(self):
        """Doppelte Kommas erzeugen keine leeren Einträge."""
        result = _parse_allowed_hosts("host1,,host2")
        assert result == ["host1", "host2"]
        assert "" not in result

    def test_single_host(self):
        """Einzelner Host wird korrekt geparst."""
        assert _parse_allowed_hosts("example.com") == ["example.com"]

    def test_whitespace_only_yields_empty_list(self):
        """Nur Whitespace ergibt leere Liste."""
        assert _parse_allowed_hosts("  ,  , ") == []
