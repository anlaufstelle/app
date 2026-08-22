"""L8 (Refs #1375, #1963) — System-Checks für das Proxy-Vertrauen.

``TRUSTED_PROXY_HOPS`` steuert, welcher Eintrag aus ``X-Forwarded-For`` als echte
Client-IP gilt (``signals.audit.get_client_ip``). Mehr als ein Hop ist nur dann
belastbar, wenn der Edge-Proxy weiss, welche vorgelagerten Proxys er ueberhaupt
glauben darf: Caddy uebernimmt ein eingehendes ``X-Forwarded-For`` nur von einem
Peer aus ``trusted_proxies`` und ersetzt es sonst durch die Peer-IP. Ohne
konfiguriertes Proxy-Vertrauen kommt also stets genau EIN Eintrag an — jedes
``TRUSTED_PROXY_HOPS > 1`` greift dann daneben und faellt auf ``REMOTE_ADDR``
(die Proxy-IP) zurueck, womit Ratelimit-Eimer, Lockout-Achse, Audit-IP und
Maintenance-Allowlist auf einen einzigen Wert kollabieren.

``TRUSTED_PROXY_CIDRS`` ist die gemeinsame Quelle beider Seiten: derselbe Wert
steht in der Caddy-Direktive ``trusted_proxies static`` und in den Settings.
Die Checks koppeln die Hop-Zahl daran und lehnen unbrauchbare Bereiche ab,
bevor Caddy am Deploy daran scheitert.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import override_settings

from anlaufstelle.settings._env import env_tokens
from core.checks import (
    check_file_upload_temp_dir_configured,
    check_media_root_persistent,
    check_trusted_proxy_cidrs,
    check_trusted_proxy_hops,
)


def _ids(messages):
    return {m.id for m in messages}


_CDN_RANGES = ["203.0.113.0/24"]


class TestTrustedProxyHopsCheck:
    @override_settings(TRUSTED_PROXY_HOPS=1, TRUSTED_PROXY_CIDRS=[])
    def test_caddy_only_default_is_clean(self):
        assert check_trusted_proxy_hops(None) == []

    @override_settings(TRUSTED_PROXY_HOPS=0, TRUSTED_PROXY_CIDRS=[])
    def test_zero_direct_remote_addr_is_clean(self):
        assert check_trusted_proxy_hops(None) == []

    @override_settings(TRUSTED_PROXY_HOPS=2, TRUSTED_PROXY_CIDRS=[])
    def test_cdn_plus_caddy_without_proxy_trust_warns(self):
        # Geaenderte Anforderung (Refs #1963): hops=2 ohne deklariertes
        # Proxy-Vertrauen ist keine gueltige Topologie mehr. Caddy ersetzt ein
        # eingehendes X-Forwarded-For, solange der Peer nicht in
        # ``trusted_proxies`` steht — es kommt genau ein Eintrag an, und
        # get_client_ip faellt auf REMOTE_ADDR (die Proxy-IP) zurueck.
        # Eigene, NEUE ID (core.W003): core.W001 ist seit #1375 veroeffentlicht
        # und bedeutet weiterhin genau das, was in den Runbooks der Betreiber
        # steht — eine Umwidmung wuerde dort still etwas anderes anzeigen.
        messages = check_trusted_proxy_hops(None)
        assert "core.W003" in _ids(messages)

    @override_settings(TRUSTED_PROXY_HOPS=2, TRUSTED_PROXY_CIDRS=_CDN_RANGES)
    def test_cdn_plus_caddy_with_proxy_trust_is_clean(self):
        assert check_trusted_proxy_hops(None) == []

    @override_settings(TRUSTED_PROXY_HOPS=3, TRUSTED_PROXY_CIDRS=_CDN_RANGES)
    def test_high_value_warns_even_with_proxy_trust(self):
        # Unveraenderte Bedeutung von core.W001 seit #1375: die Heuristik
        # "mehr als CDN + Caddy ist selten echt".
        messages = check_trusted_proxy_hops(None)
        assert "core.W001" in _ids(messages)

    @override_settings(TRUSTED_PROXY_HOPS=3, TRUSTED_PROXY_CIDRS=[])
    def test_high_value_without_proxy_trust_reports_both(self):
        messages = check_trusted_proxy_hops(None)
        assert _ids(messages) == {"core.W001", "core.W003"}

    @override_settings(TRUSTED_PROXY_HOPS=-1, TRUSTED_PROXY_CIDRS=[])
    def test_negative_is_error(self):
        messages = check_trusted_proxy_hops(None)
        assert "core.E001" in _ids(messages)


class TestTrustedProxyCidrsCheck:
    """Refs #1963: ``TRUSTED_PROXY_CIDRS`` fuellt die Caddy-Direktive
    ``trusted_proxies static``. Ein unbrauchbarer Wert laesst Caddy den Start
    verweigern — und zwar erst NACH dem als healthy gemeldeten web-Container.
    Der Check zieht den Fehler in den Start der Anwendung vor.

    Er ist nur dann etwas wert, wenn er **genau das** ablehnt, was Caddy
    ablehnt. Beide Korpora unten sind am gepinnten Image nachgemessen
    (``caddy:2-alpine``, v2.11.4, Digest aus ``docker-compose.prod.yml``):
    jeder Wert einmal als ``TRUSTED_PROXY_CIDRS`` in den globalen
    ``servers``-Block gesetzt und mit ``caddy validate --config Caddyfile
    --adapter caddyfile`` geprueft. Caddy parst jeden Eintrag ueber Gos
    ``netip.ParsePrefix`` — strenger als Pythons ``ipaddress``, das
    Netzmasken und fuehrende Nullen in der Praefixlaenge durchgehen laesst.
    """

    _VON_CADDY_AKZEPTIERT = (
        "203.0.113.0/24",
        "2001:db8::/32",
        # Nackte Adressen ergaenzt Caddy selbst zu /32 bzw. /128.
        "198.51.100.7",
        "0.0.0.0/0",
        # Gesetzte Host-Bits sind erlaubt (Go maskiert beim Vergleich).
        "203.0.113.5/24",
        # Schluesselwort, das die Direktive selbst expandiert.
        "private_ranges",
    )

    _VON_CADDY_ABGELEHNT = (
        # Komma ist KEIN Trennzeichen: Caddy zerlegt Direktiven-Argumente nur
        # an Leerzeichen/Tabs, der ganze String kommt als EIN Token an.
        "203.0.113.0/24,198.51.100.0/24",
        # Netzmaskenschreibweise — fuer Python gueltig, fuer Go nicht.
        "192.168.0.0/255.255.255.0",
        # Fuehrende Null in der Praefixlaenge bzw. im Oktett.
        "2001:db8::/032",
        "010.0.0.1",
        "203.0.113.0/33",
        "203.0.113.0/99",
        "kein-netz",
    )

    @override_settings(TRUSTED_PROXY_CIDRS=[])
    def test_unset_is_clean(self):
        assert check_trusted_proxy_cidrs(None) == []

    @pytest.mark.parametrize("eintrag", _VON_CADDY_AKZEPTIERT)
    def test_values_caddy_accepts_are_clean(self, eintrag):
        with override_settings(TRUSTED_PROXY_CIDRS=[eintrag]):
            assert check_trusted_proxy_cidrs(None) == [], (
                f"{eintrag!r} startet Caddy nachweislich — der Check darf den Start nicht blockieren."
            )

    @pytest.mark.parametrize("eintrag", _VON_CADDY_ABGELEHNT)
    def test_values_caddy_rejects_are_errors(self, eintrag):
        with override_settings(TRUSTED_PROXY_CIDRS=[eintrag]):
            assert "core.E003" in _ids(check_trusted_proxy_cidrs(None)), (
                f"{eintrag!r} laesst Caddy den Start verweigern — der Check muss ihn vorher melden."
            )

    @override_settings(TRUSTED_PROXY_CIDRS=["203.0.113.0/24", "kein-netz"])
    def test_error_names_only_the_broken_entry(self):
        (meldung,) = check_trusted_proxy_cidrs(None)
        assert "kein-netz" in meldung.msg
        assert "203.0.113.0/24" not in meldung.msg

    @override_settings(TRUSTED_PROXY_CIDRS=["203.0.113.0/24,198.51.100.0/24"])
    def test_comma_hint_names_the_separator(self):
        # Der Hinweis muss die Ursache benennen, nicht nur "ungueltig":
        # die Doku hat das Komma bis #1963 ausdruecklich als Schreibweise
        # angeboten, der Fehler ist also erwartbar hausgemacht.
        (meldung,) = check_trusted_proxy_cidrs(None)
        assert "Komma" in meldung.hint
        assert "Leerzeichen" in meldung.hint


class TestEnvTokens:
    """Refs #1963: ``TRUSTED_PROXY_CIDRS`` wird **von Caddy** zerlegt — die
    Substitution ``{$TRUSTED_PROXY_CIDRS}`` fuellt eine Caddyfile-Direktive,
    deren Argumente der Caddyfile-Parser an Leerzeichen und Tabs trennt, an
    nichts sonst. Der Helfer muss genau diese Zerlegung nachbilden, sonst
    sieht die Anwendung eine Liste, die es im Reverse-Proxy nie gab: ein
    kommagetrennter Wert waere fuer die Checks zwei gueltige Bereiche, fuer
    Caddy ein einziger unbrauchbarer — die Anwendung gaebe Entwarnung,
    waehrend der Proxy den Start verweigert.
    """

    def test_whitespace_separated(self, monkeypatch):
        monkeypatch.setenv("TEST_TOKENS", "203.0.113.0/24 2001:db8::/32")
        assert env_tokens("TEST_TOKENS") == ["203.0.113.0/24", "2001:db8::/32"]

    def test_tab_separated(self, monkeypatch):
        # Am gepinnten Image geprueft: Tabs trennt der Caddyfile-Parser wie
        # Leerzeichen (Zeilenumbrueche dagegen beenden die Direktive und
        # lassen ``caddy adapt`` scheitern — kein Trennzeichen also).
        monkeypatch.setenv("TEST_TOKENS", "203.0.113.0/24\t198.51.100.0/24")
        assert env_tokens("TEST_TOKENS") == ["203.0.113.0/24", "198.51.100.0/24"]

    def test_comma_is_not_a_separator(self, monkeypatch):
        # Nachgestellt mit caddy 2.11.4: derselbe Wert laesst
        # ``caddy validate`` mit "parsing CIDR expression" abbrechen.
        monkeypatch.setenv("TEST_TOKENS", "203.0.113.0/24,2001:db8::/32")
        assert env_tokens("TEST_TOKENS") == ["203.0.113.0/24,2001:db8::/32"]

    def test_padding_is_stripped(self, monkeypatch):
        monkeypatch.setenv("TEST_TOKENS", "  203.0.113.0/24 \t 198.51.100.0/24  \n")
        assert env_tokens("TEST_TOKENS") == ["203.0.113.0/24", "198.51.100.0/24"]

    def test_unset_is_empty(self, monkeypatch):
        monkeypatch.delenv("TEST_TOKENS", raising=False)
        assert env_tokens("TEST_TOKENS") == []

    def test_blank_is_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_TOKENS", "   ")
        assert env_tokens("TEST_TOKENS") == []

    def test_comma_value_reaches_the_check_as_one_unusable_token(self, monkeypatch):
        # Der eigentliche Befund: Env-Parsing und Check zusammen muessen den
        # Wert abweisen, an dem Caddy stirbt.
        monkeypatch.setenv("TEST_TOKENS", "203.0.113.0/24,198.51.100.0/24")
        with override_settings(TRUSTED_PROXY_CIDRS=env_tokens("TEST_TOKENS")):
            assert "core.E003" in _ids(check_trusted_proxy_cidrs(None))


class TestMediaRootPersistentCheck:
    """#1453: MEDIA_ROOT darf in Produktion nicht unter BASE_DIR liegen — das
    ist weder ueber ``docker compose pull`` persistent noch unter ``read_only``
    beschreibbar. Ein still auf ``BASE_DIR/media`` fallender Prod-Deploy soll
    beim ``check --deploy`` laut abbrechen statt erst beim ersten Upload zu 500en.
    """

    @override_settings(DEBUG=True, BASE_DIR=Path("/app"), MEDIA_ROOT="/app/media")
    def test_debug_skips_check(self):
        # dev/tests nutzen bewusst BASE_DIR/media — der Guard darf sie nicht brechen.
        assert check_media_root_persistent(None) == []

    @override_settings(DEBUG=False, BASE_DIR=Path("/app"), MEDIA_ROOT="/data/media")
    def test_persistent_media_root_is_clean(self):
        assert check_media_root_persistent(None) == []

    @override_settings(DEBUG=False, BASE_DIR=Path("/app"), MEDIA_ROOT="/app/media")
    def test_media_root_under_base_dir_is_error(self):
        messages = check_media_root_persistent(None)
        assert "core.E002" in _ids(messages)

    @override_settings(DEBUG=False, BASE_DIR=Path("/app"), MEDIA_ROOT="media")
    def test_relative_media_root_is_error(self):
        # Ein relativer Pfad wird von Django relativ zum CWD (= BASE_DIR im
        # Container) aufgeloest und liegt damit ebenfalls unter BASE_DIR.
        messages = check_media_root_persistent(None)
        assert "core.E002" in _ids(messages)


class TestFileUploadTempDirCheck:
    """#1661: FILE_UPLOAD_TEMP_DIR sollte in Produktion explizit gesetzt sein,
    damit der Klartext-Zwischenpuffer fuer Uploads > FILE_UPLOAD_MAX_MEMORY_SIZE
    nachweislich auf einem tmpfs/verschluesselten Volume liegt, statt sich auf
    Pythons impliziten tempfile.gettempdir()-Fallback zu verlassen. Nur eine
    Warnung (kein Error) — anders als core.E002/MEDIA_ROOT bricht ein fehlendes
    FILE_UPLOAD_TEMP_DIR den Betrieb nicht, es ist ein Haertungshinweis."""

    @override_settings(DEBUG=True, FILE_UPLOAD_TEMP_DIR=None)
    def test_debug_skips_check(self):
        # dev/tests nutzen bewusst keinen expliziten Upload-Tmp-Pfad.
        assert check_file_upload_temp_dir_configured(None) == []

    @override_settings(DEBUG=False, FILE_UPLOAD_TEMP_DIR="/mnt/tmpfs-uploads")
    def test_configured_is_clean(self):
        assert check_file_upload_temp_dir_configured(None) == []

    @override_settings(DEBUG=False, FILE_UPLOAD_TEMP_DIR=None)
    def test_unset_warns(self):
        messages = check_file_upload_temp_dir_configured(None)
        assert "core.W002" in _ids(messages)

    @override_settings(DEBUG=False, FILE_UPLOAD_TEMP_DIR="")
    def test_blank_warns(self):
        messages = check_file_upload_temp_dir_configured(None)
        assert "core.W002" in _ids(messages)
