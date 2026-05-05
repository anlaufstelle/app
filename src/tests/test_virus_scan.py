"""Tests für den ClamAV-Virenscan-Service (Issue #524)."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.services.virus_scan import (
    ScanResult,
    VirusScannerUnavailableError,
    ping,
    scan_file,
)

# EICAR-Test-String — harmloses Standard-Artefakt, das von jedem AV-Scanner
# als Testvirus erkannt wird. Wir mocken ClamAV, daher reicht eine konstante
# Kennzeichnung dieser Bytes als „infiziert".
EICAR = (
    rb"X5O!P%@AP[4\PZX54(P^)7CC)7}"
    rb"$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


class TestScanBypass:
    """Deaktivierter Scanner darf ohne ClamAV-Kontakt als clean zurückgeben."""

    def test_returns_clean_when_disabled(self, settings):
        settings.CLAMAV_ENABLED = False
        uploaded = SimpleUploadedFile("foo.pdf", b"harmless", content_type="application/pdf")

        with patch("core.services.virus_scan._build_client") as build:
            result = scan_file(uploaded)

        assert result == ScanResult(clean=True, infected=False)
        # Kein Client-Aufbau, wenn Scan deaktiviert.
        build.assert_not_called()


class TestScanResults:
    """Mock pyclamd and verify ScanResult mapping."""

    def _mock_client(self, scan_stream_return):
        client = MagicMock()
        client.ping.return_value = True
        client.scan_stream.return_value = scan_stream_return
        return client

    def test_clean_file_is_reported_clean(self, settings):
        settings.CLAMAV_ENABLED = True
        uploaded = SimpleUploadedFile("ok.pdf", b"PDF bytes", content_type="application/pdf")

        with patch(
            "core.services.virus_scan._build_client",
            return_value=self._mock_client(scan_stream_return=None),
        ):
            result = scan_file(uploaded)

        assert result.clean is True
        assert result.infected is False
        assert result.signature is None

    def test_eicar_is_reported_infected(self, settings):
        settings.CLAMAV_ENABLED = True
        uploaded = SimpleUploadedFile("eicar.com", EICAR, content_type="application/octet-stream")

        mock_response = {"stream": ("FOUND", "Eicar-Signature")}
        with patch(
            "core.services.virus_scan._build_client",
            return_value=self._mock_client(scan_stream_return=mock_response),
        ):
            result = scan_file(uploaded)

        assert result.clean is False
        assert result.infected is True
        assert result.signature == "Eicar-Signature"

    def test_legacy_string_response_is_parsed(self, settings):
        """Historische pyclamd-Versionen lieferten 'NAME FOUND' als String."""
        settings.CLAMAV_ENABLED = True
        uploaded = SimpleUploadedFile("x.bin", b"data", content_type="application/octet-stream")

        mock_response = {"stream": "Some-Signature FOUND"}
        with patch(
            "core.services.virus_scan._build_client",
            return_value=self._mock_client(scan_stream_return=mock_response),
        ):
            result = scan_file(uploaded)

        assert result.infected is True
        assert result.signature == "Some-Signature"

    def test_file_pointer_is_reset_after_scan(self, settings):
        """Nach dem Scan muss der Upload-Stream für die Verschlüsselung wieder
        an Position 0 stehen — sonst wird eine leere Datei verschlüsselt."""
        settings.CLAMAV_ENABLED = True
        payload = b"PDF content for encryption"
        uploaded = SimpleUploadedFile("ok.pdf", payload, content_type="application/pdf")

        with patch(
            "core.services.virus_scan._build_client",
            return_value=self._mock_client(scan_stream_return=None),
        ):
            scan_file(uploaded)

        assert uploaded.read() == payload


class TestScannerErrors:
    """Verbindungsfehler müssen als VirusScannerUnavailableError propagieren."""

    def test_ping_failure_raises_unavailable(self, settings):
        settings.CLAMAV_ENABLED = True
        uploaded = SimpleUploadedFile("ok.pdf", b"x", content_type="application/pdf")

        client = MagicMock()
        client.ping.return_value = False

        with patch("core.services.virus_scan._build_client", return_value=client):
            with pytest.raises(VirusScannerUnavailableError):
                scan_file(uploaded)
        # scan_stream darf bei nicht erreichbarem Daemon nicht aufgerufen werden.
        client.scan_stream.assert_not_called()

    def test_connection_error_during_scan_is_wrapped(self, settings):
        settings.CLAMAV_ENABLED = True
        uploaded = SimpleUploadedFile("ok.pdf", b"x", content_type="application/pdf")

        client = MagicMock()
        client.ping.return_value = True
        client.scan_stream.side_effect = ConnectionError("refused")

        with patch("core.services.virus_scan._build_client", return_value=client):
            with pytest.raises(VirusScannerUnavailableError):
                scan_file(uploaded)

    def test_build_client_failure_is_wrapped(self, settings):
        settings.CLAMAV_ENABLED = True
        uploaded = SimpleUploadedFile("ok.pdf", b"x", content_type="application/pdf")

        with (
            patch(
                "core.services.virus_scan._build_client",
                side_effect=OSError("cannot resolve clamav"),
            ),
            pytest.raises(VirusScannerUnavailableError),
        ):
            scan_file(uploaded)


class TestStreamlikeObjects:
    """scan_file muss auch mit BytesIO-Objekten klarkommen (z.B. Tests)."""

    def test_bytesio_is_scanned(self, settings):
        settings.CLAMAV_ENABLED = True
        buf = BytesIO(b"some bytes")

        client = MagicMock()
        client.ping.return_value = True
        client.scan_stream.return_value = None

        with patch("core.services.virus_scan._build_client", return_value=client):
            result = scan_file(buf)

        assert result.clean is True
        # scan_stream wurde mit den Bytes aufgerufen.
        assert client.scan_stream.call_args[0][0] == b"some bytes"


class TestScannerNetworkFailures:
    """Fail-closed-Verhalten bei Netzwerk-Ausfällen (WP5 Gap-Analyse).

    Das Upload-Contract sagt: bei aktivem CLAMAV_ENABLED ist der Scanner
    Pflicht — ist er unerreichbar, muss der Upload mit
    ``VirusScannerUnavailableError`` abgelehnt werden, *bevor* irgendein
    ``scan_stream``-Aufruf passiert. Kein „silent allow".
    """

    def test_socket_timeout_on_ping_fails_closed(self, settings):
        """``socket.timeout`` während ``ping()`` muss als unavailable propagieren."""
        settings.CLAMAV_ENABLED = True
        uploaded = SimpleUploadedFile("x.pdf", b"payload", content_type="application/pdf")

        client = MagicMock()
        client.ping.side_effect = TimeoutError("clamav ping timeout")

        with patch("core.services.virus_scan._build_client", return_value=client):
            with pytest.raises(VirusScannerUnavailableError) as exc_info:
                scan_file(uploaded)
        assert "nicht erreichbar" in str(exc_info.value) or "ping" in str(exc_info.value).lower()
        client.scan_stream.assert_not_called()

    def test_socket_timeout_during_scan_stream_is_wrapped(self, settings):
        """Timeout erst beim ``scan_stream``-Aufruf (Daemon hängt mitten im Transfer)."""
        settings.CLAMAV_ENABLED = True
        uploaded = SimpleUploadedFile("x.pdf", b"payload", content_type="application/pdf")

        client = MagicMock()
        client.ping.return_value = True
        client.scan_stream.side_effect = TimeoutError("clamav scan timeout")

        with patch("core.services.virus_scan._build_client", return_value=client):
            with pytest.raises(VirusScannerUnavailableError):
                scan_file(uploaded)

    def test_connection_refused_on_build_fails_closed(self, settings):
        """``ConnectionRefusedError`` bereits beim Clientaufbau → unavailable."""
        settings.CLAMAV_ENABLED = True
        uploaded = SimpleUploadedFile("x.pdf", b"payload", content_type="application/pdf")

        with (
            patch(
                "core.services.virus_scan._build_client",
                side_effect=ConnectionRefusedError("clamd: connection refused"),
            ),
            pytest.raises(VirusScannerUnavailableError) as exc_info,
        ):
            scan_file(uploaded)
        assert "refused" in str(exc_info.value).lower() or "initialis" in str(exc_info.value)

    def test_connection_refused_during_scan_stream_is_wrapped(self, settings):
        """``ConnectionRefusedError`` erst beim ``scan_stream``-Aufruf."""
        settings.CLAMAV_ENABLED = True
        uploaded = SimpleUploadedFile("x.pdf", b"payload", content_type="application/pdf")

        client = MagicMock()
        client.ping.return_value = True
        client.scan_stream.side_effect = ConnectionRefusedError("clamd closed during scan")

        with patch("core.services.virus_scan._build_client", return_value=client):
            with pytest.raises(VirusScannerUnavailableError):
                scan_file(uploaded)

    def test_unavailable_scanner_produces_clean_exception_not_scanresult(self, settings):
        """Regression: bei unavailable darf ``scan_file`` kein ScanResult(clean=True)
        zurückgeben — sonst würde der Aufrufer den Upload fälschlich akzeptieren.
        """
        settings.CLAMAV_ENABLED = True
        uploaded = SimpleUploadedFile("x.pdf", b"payload", content_type="application/pdf")

        with (
            patch(
                "core.services.virus_scan._build_client",
                side_effect=TimeoutError("clamav socket timeout"),
            ),
            pytest.raises(VirusScannerUnavailableError),
        ):
            result = scan_file(uploaded)
            # Darf nicht erreicht werden.
            assert False, f"Expected exception, got {result!r}"


class TestPing:
    """Healthcheck-Ping darf nie werfen."""

    def test_returns_false_when_disabled(self, settings):
        settings.CLAMAV_ENABLED = False
        assert ping() is False

    def test_returns_true_when_enabled_and_reachable(self, settings):
        settings.CLAMAV_ENABLED = True
        client = MagicMock()
        client.ping.return_value = True
        with patch("core.services.virus_scan._build_client", return_value=client):
            assert ping() is True

    def test_returns_false_on_error(self, settings):
        settings.CLAMAV_ENABLED = True
        with patch(
            "core.services.virus_scan._build_client",
            side_effect=ConnectionError("unreachable"),
        ):
            assert ping() is False
