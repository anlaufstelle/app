"""E2E-Test: Upload mit Virenfund zeigt sichtbares Ablehnungs-Feedback (Refs #2026).

Service-seitig ist der Pfad exzellent abgedeckt (``src/tests/test_file_vault.py``
``TestVirusScanIntegration``) — aber dort wird ``scan_file`` gemockt. Was die
Nutzerin im Browser SIEHT, wenn eine Datei am Virenscanner haengenbleibt, hat
bisher kein Test geprueft.

**Warum dieser Test einen eigenen Server startet.** Im Standard-E2E-Server ist
``CLAMAV_ENABLED`` der ``base.py``-Default ``False`` — ``scan_file`` gibt dann
ohne jeden Daemon-Kontakt „clean" zurueck (Bypass). Weder ``settings/e2e.py``
noch ``conftest.py`` kennen einen Test-Scanner, und ein echter ``clamd`` laeuft
im E2E-Harness nicht (nur optional per Docker-Profil). Statt Produktionscode
oder ``conftest.py`` anzufassen, bringt dieses Modul beides selbst mit:

* :class:`_FakeClamd` — ein TCP-Server im Testprozess, der genau die zwei
  ``pyclamd``-Kommandos beantwortet, die ``virus_scan.scan_file`` benutzt
  (``PING`` und ``INSTREAM``). Verdikt nach Inhalt: enthaelt der Stream den
  EICAR-String, antwortet er ``stream: Eicar-Test-Signature FOUND``, sonst
  ``stream: OK``. Ein zusaetzlicher Modus ``down`` beantwortet nichts und
  loest damit den fail-closed-Zweig aus.
* ``clamav_base_url`` — ein zweiter gunicorn auf der GLEICHEN E2E-Datenbank mit
  ``CLAMAV_ENABLED=1`` und ``CLAMAV_HOST/PORT`` auf den Fake-Daemon; Muster und
  Lebenszyklus wie ``enforced_base_url`` in ``conftest.py``, nur modul-scoped.

Der Standard-Server (``base_url``) bleibt dadurch unveraendert scan-frei — kein
anderer Test sieht etwas von diesem Aufbau.
"""

import os
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import uuid
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

# Die drei Helfer stammen aus dem bestehenden Upload-E2E — sie tragen die
# Anti-Stale-DOM-Mechanik fuer den HTMX-Feldwechsel (Refs #1797) und das
# ``expect_file_chooser``-Muster. Neu implementieren hiesse, dieselben Fallen
# ein zweites Mal zu umgehen.
from .conftest import _kill_port, _wait_for_server, login_as
from .test_file_vault import (
    _VALID_PDF_BYTES,
    _select_qualified_client,
    select_doc_type_and_wait,
    set_files_on_swapped_input,
)

pytestmark = pytest.mark.e2e

# EICAR-Test-String (wortgleich zu ``src/tests/test_virus_scan.py``) — harmloses
# Standard-Artefakt, das jeder AV-Scanner als Testvirus meldet. Hier ist er die
# Nutzlast, an der der Fake-Daemon sein Verdikt festmacht.
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

#: Gueltiges PDF (libmagic-tauglich) mit dem EICAR-String im Body. Die
#: Reihenfolge in ``prepare_encrypted_upload`` ist Extension → Groesse →
#: Virenscan → Magic-Bytes; das PDF-Geruest sorgt dafuer, dass der Upload nicht
#: schon vorher am Formular- oder Typ-Check scheitert und der Scan wirklich der
#: ablehnende Schritt ist.
_INFECTED_PDF_BYTES = _VALID_PDF_BYTES.replace(b"trailer", b"% " + EICAR + b"\ntrailer")

_SIGNATUR = "Eicar-Test-Signature"


class _Rahmenleser:
    """Gepufferter Leser fuer eine clamd-Verbindung.

    Noetig, weil ``pyclamd`` Kommando und Datenrahmen in getrennten ``send``-
    Aufrufen schickt: TCP darf sie zu einem einzigen Segment zusammenfassen, ein
    naives ``recv`` beim Kommando verschluckt dann den Anfang des ersten Rahmens.
    """

    def __init__(self, conn):
        self._conn = conn
        self._puffer = bytearray()

    def _fuellen(self, mindestens: int) -> bool:
        while len(self._puffer) < mindestens:
            haeppchen = self._conn.recv(65536)
            if not haeppchen:
                return False
            self._puffer.extend(haeppchen)
        return True

    def zeile(self) -> str:
        """Naechste ``\n``-terminierte Zeile; ``""`` bei geschlossener Verbindung."""
        while b"\n" not in self._puffer:
            haeppchen = self._conn.recv(65536)
            if not haeppchen:
                break
            self._puffer.extend(haeppchen)
        trenner = self._puffer.find(b"\n")
        if trenner < 0:
            rest, self._puffer = bytes(self._puffer), bytearray()
            return rest.decode("utf-8", "replace").strip()
        zeile = bytes(self._puffer[:trenner])
        del self._puffer[: trenner + 1]
        return zeile.decode("utf-8", "replace").strip()

    def exakt(self, anzahl: int) -> bytes:
        if not self._fuellen(anzahl):
            rest, self._puffer = bytes(self._puffer), bytearray()
            return rest
        block = bytes(self._puffer[:anzahl])
        del self._puffer[:anzahl]
        return block


class _FakeClamd:
    """Minimaler ``clamd``-Ersatz im Testprozess (nur ``PING`` + ``INSTREAM``).

    ``pyclamd`` praefixt jedes Kommando mit ``n`` und terminiert es mit ``\n``
    (``_send_command``); die Antwort liest es zeilenweise, bis der Server die
    Verbindung schliesst (``_recv_response`` liefert dann ``""``). Genau dieses
    Minimalprotokoll wird hier bedient — pro Kommando eine Verbindung, Antwort,
    Close.

    ``mode``:
    * ``"up"`` — normaler Betrieb, Verdikt nach Stream-Inhalt.
    * ``"down"`` — jede Verbindung wird ohne Antwort geschlossen. ``client.ping()``
      wirft dann einen ``ConnectionError``, ``scan_file`` macht daraus
      ``VirusScannerUnavailableError`` (fail-closed).
    """

    def __init__(self):
        self.mode = "up"
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(32)
        self.port = self._server.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, name="fake-clamd", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        # ``close()`` bricht das blockierende ``accept()`` im Thread ab.
        self._server.close()
        self._thread.join(timeout=5)

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        # Endliche Wartezeit statt unendlichem Blockieren: ein haengender
        # Handler duerfte den Testlauf nie aufhalten (Standardblock 22).
        conn.settimeout(20)
        try:
            if self.mode == "down":
                return
            leser = _Rahmenleser(conn)
            befehl = leser.zeile()
            if not befehl:
                # Der ``ClamdNetworkSocket``-Konstruktor testet die Erreichbarkeit
                # mit einem Connect/Close ohne Kommando.
                return
            if befehl.endswith("PING"):
                conn.sendall(b"PONG\n")
                return
            if befehl.endswith("INSTREAM"):
                daten = self._read_instream(leser)
                treffer = EICAR in daten
                conn.sendall(f"stream: {_SIGNATUR} FOUND\n".encode() if treffer else b"stream: OK\n")
                return
            conn.sendall(b"UNKNOWN COMMAND\n")
        except OSError:
            pass
        finally:
            _close_quietly(conn)

    @staticmethod
    def _read_instream(leser: _Rahmenleser) -> bytes:
        """INSTREAM-Rahmen lesen: je ``<uint32 Laenge><Nutzlast>``, Ende bei Laenge 0."""
        daten = bytearray()
        while True:
            kopf = leser.exakt(4)
            if len(kopf) < 4:
                break
            (laenge,) = struct.unpack("!L", kopf)
            if laenge == 0:
                break
            block = leser.exakt(laenge)
            daten.extend(block)
            if len(block) < laenge:
                break
        return bytes(daten)


def _close_quietly(conn):
    """Verbindung schliessen und dabei jede IO-Nachwehe schlucken."""
    try:
        conn.close()
    except OSError:
        pass


def _stop_process(proc):
    """gunicorn garantiert beenden: SIGTERM → wait(10) → SIGKILL → wait().

    Review Fix-Runde 1: Der Teardown schickte nur ``SIGTERM`` und wartete
    ohne Fallback. Endet der Prozess nicht in zehn Sekunden, scheiterte der
    Teardown und der Server konnte auf Port ``base_url+200`` weiterlaufen; im
    Startfehler-Pfad blieb er ausserdem unreaped. Beides ist Shared-State-Muell
    fuer den seriellen Voll-Lauf.
    """
    if proc.poll() is not None:
        proc.wait()
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture(scope="module")
def fake_clamd():
    """Fake-``clamd`` fuer die Dauer dieser Testdatei."""
    daemon = _FakeClamd().start()
    yield daemon
    daemon.stop()


@pytest.fixture(scope="module")
def clamav_base_url(base_url, e2e_env, fake_clamd):
    """Zweiter E2E-Server mit scharfem Virenscan gegen den Fake-Daemon.

    Teilt Datenbank und Seed mit ``base_url`` (dort bereits migriert/geseedet)
    und startet nur einen zusaetzlichen gunicorn — analog ``enforced_base_url``
    (``conftest.py``).

    Portwahl: ``base_url``-Port + 200. Das Briefing schlug ``8848 + worker_num``
    vor; das kollidiert ab vier xdist-Workern mit den ``base_url``-Ports
    (8844+n). Der Abstand +200 ist per Konstruktion kollisionsfrei und folgt der
    bestehenden Konvention (``enforced_base_url`` = +100).
    """
    port = urlparse(base_url).port + 200
    env = {
        **e2e_env,
        "CLAMAV_ENABLED": "1",
        "CLAMAV_HOST": "127.0.0.1",
        "CLAMAV_PORT": str(fake_clamd.port),
        # Deutlich unter dem 120-s-Test-Timeout: ein haengender Scan soll
        # fail-closed werden, nicht den Testlauf blockieren.
        "CLAMAV_TIMEOUT": "10",
    }
    python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable

    _kill_port(port)
    proc = subprocess.Popen(
        [
            python,
            "-m",
            "gunicorn",
            "anlaufstelle.wsgi:application",
            "--bind",
            f"127.0.0.1:{port}",
            "--workers",
            "2",
            "--threads",
            "2",
            "--chdir",
            "src",
            "--timeout",
            "120",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(url)
        yield url
    finally:
        _stop_process(proc)


@pytest.fixture
def lead_on_clamav_server(browser, clamav_base_url):
    """Als Leitung (emma) am scan-scharfen Server angemeldet.

    Kein Storage-State: der gehoert zum Standard-Server und traegt dessen
    Session-Cookie. Die Datenbank ist dieselbe, der Prozess nicht.
    """
    page, context = login_as(browser, clamav_base_url, "emma")
    yield page
    context.close()


def _zaehle(e2e_env, script) -> int:
    zeilen = [z.removeprefix("E2E_COUNT=") for z in django_shell(e2e_env, script) if z.startswith("E2E_COUNT=")]
    assert len(zeilen) == 1, f"Zaehl-Skript lieferte kein Ergebnis: {zeilen}"
    return int(zeilen[0])


def _event_count(e2e_env) -> int:
    """Alle Ereignisse zaehlen.

    Bewusst kein Filter auf das eingegebene Thema: dynamische Feldwerte liegen
    verschluesselt in ``data_json``/``search_text`` und sind per ORM nicht
    durchsuchbar. Der serielle Voll-Lauf macht die Gesamtzahl zu einer
    belastbaren Vorher/Nachher-Groesse (nichts anderes schreibt gleichzeitig).
    """
    return _zaehle(
        e2e_env,
        "from core.models import Event\nprint('E2E_COUNT=' + str(Event.objects.count()))\n",
    )


def _anhaenge_des_events(e2e_env, event_pk: str) -> int:
    """Lebende Anhaenge dieses Ereignisses zaehlen.

    Auf den Dateinamen kann nicht gefiltert werden — er liegt als
    ``original_filename_encrypted`` verschluesselt in der Zeile. Den sichtbaren
    Namen prueft dafuer die UI-Assertion.
    """
    return _zaehle(
        e2e_env,
        "from core.models import Attachment\n"
        f'qs = Attachment.objects.filter(event_id="{event_pk}", deleted_at__isnull=True)\n'
        "print('E2E_COUNT=' + str(qs.count()))\n",
    )


def _attachment_count(e2e_env) -> int:
    return _zaehle(
        e2e_env,
        "from core.models import Attachment\nprint('E2E_COUNT=' + str(Attachment.objects.count()))\n",
    )


def _security_violations(e2e_env, reason: str) -> int:
    return _zaehle(
        e2e_env,
        "from core.models import AuditLog\n"
        "qs = AuditLog.objects.filter(action=AuditLog.Action.SECURITY_VIOLATION, "
        f'detail__reason="{reason}")\n'
        'print("E2E_COUNT=" + str(qs.count()))\n',
    )


def _upload_starten(page, base_url, e2e_env, thema, datei):
    """Ereignisformular mit Datei-Upload ausfuellen (ohne Absenden)."""
    page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
    select_doc_type_and_wait(page, "Beratungsgespräch", "text=Scan/Bescheid")
    assert _select_qualified_client(page, base_url, e2e_env), "Kein qualifizierter Klient im Seed gefunden"
    page.fill('input[name="thema"]', thema)
    set_files_on_swapped_input(page, 'input[name="scan-bescheid"]', str(datei))


class TestVirusUploadFeedback:
    """Was die Nutzerin sieht, wenn der Scanner eine Datei abweist."""

    def test_infected_upload_shows_error_and_keeps_form(
        self, lead_on_clamav_server, clamav_base_url, e2e_env, tmp_path
    ):
        """EICAR-PDF: sichtbare Fehlermeldung, Formular erhalten, nichts gespeichert."""
        page = lead_on_clamav_server
        thema = f"E2E-VIRUS-{uuid.uuid4().hex[:8]}"
        datei = tmp_path / "eicar-bescheid.pdf"
        datei.write_bytes(_INFECTED_PDF_BYTES)

        verstoesse_vorher = _security_violations(e2e_env, "virus_detected")
        anhaenge_vorher = _attachment_count(e2e_env)
        ereignisse_vorher = _event_count(e2e_env)

        _upload_starten(page, clamav_base_url, e2e_env, thema, datei)
        page.click('button:has-text("Speichern")')

        # 1. Sichtbares Feedback in der Non-Field-Fehlerbox des Formulars.
        expect(page.locator("#event-create-form")).to_contain_text(
            f"Datei wurde von Virenscanner abgewiesen: {_SIGNATUR}", timeout=20000
        )

        # 2. Formular bleibt erhalten — kein Redirect, keine leere Maske.
        assert page.url.endswith("/events/new/"), f"Erwartet Verbleib auf /events/new/, ist: {page.url}"
        expect(page.locator('select[name="document_type"] option:checked')).to_have_text(
            re.compile("Beratungsgespräch")
        )
        expect(page.locator('input[name="thema"]')).to_have_value(thema)

        # 3. Nichts gespeichert, aber protokolliert.
        assert _event_count(e2e_env) == ereignisse_vorher, "Abgewiesener Upload darf kein Ereignis hinterlassen"
        assert _attachment_count(e2e_env) == anhaenge_vorher, "Abgewiesener Upload darf keinen Anhang hinterlassen"
        assert _security_violations(e2e_env, "virus_detected") == verstoesse_vorher + 1, (
            "Virenfund muss eine SECURITY_VIOLATION schreiben"
        )

    def test_clean_upload_still_succeeds_on_the_same_server(
        self, lead_on_clamav_server, clamav_base_url, e2e_env, tmp_path
    ):
        """Gegenprobe: dieselbe Strecke mit sauberer Datei bleibt gruen."""
        page = lead_on_clamav_server
        thema = f"E2E-CLEAN-{uuid.uuid4().hex[:8]}"
        datei = tmp_path / "sauberer-bescheid.pdf"
        datei.write_bytes(_VALID_PDF_BYTES)

        _upload_starten(page, clamav_base_url, e2e_env, thema, datei)
        page.click('button:has-text("Speichern")')
        page.wait_for_url(lambda url: "/events/" in url and "/new/" not in url, timeout=20000)

        expect(page.locator("#main-content")).to_contain_text("sauberer-bescheid.pdf", timeout=10000)
        treffer = re.search(r"/events/([0-9a-f-]{36})/", page.url)
        assert treffer, f"Ereignis-Detail-URL nicht erkannt: {page.url}"
        assert _anhaenge_des_events(e2e_env, treffer.group(1)) == 1, (
            "Sauberer Upload muss genau einen Anhang am neuen Ereignis erzeugen"
        )

    def test_unreachable_scanner_fails_closed_with_visible_message(
        self, lead_on_clamav_server, clamav_base_url, e2e_env, fake_clamd, tmp_path
    ):
        """Fail-closed: schweigt der Daemon, wird der Upload sichtbar abgelehnt."""
        page = lead_on_clamav_server
        thema = f"E2E-FAILCLOSED-{uuid.uuid4().hex[:8]}"
        datei = tmp_path / "sauberer-bescheid.pdf"
        datei.write_bytes(_VALID_PDF_BYTES)

        verstoesse_vorher = _security_violations(e2e_env, "virus_scanner_unavailable")
        ereignisse_vorher = _event_count(e2e_env)
        _upload_starten(page, clamav_base_url, e2e_env, thema, datei)

        fake_clamd.mode = "down"
        try:
            page.click('button:has-text("Speichern")')
            expect(page.locator("#event-create-form")).to_contain_text(
                "Virenscanner ist nicht erreichbar", timeout=30000
            )
        finally:
            fake_clamd.mode = "up"

        assert page.url.endswith("/events/new/"), f"Erwartet Verbleib auf /events/new/, ist: {page.url}"
        assert _event_count(e2e_env) == ereignisse_vorher, "Fail-closed darf kein Ereignis hinterlassen"
        assert _security_violations(e2e_env, "virus_scanner_unavailable") == verstoesse_vorher + 1, (
            "Scanner-Ausfall muss eine SECURITY_VIOLATION schreiben"
        )
