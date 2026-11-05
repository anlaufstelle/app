"""E2E: the server starts with a field-encryption key ring and honors its order (Refs #2567).

The key ring (``ENCRYPTION_KEYS``) is the only key source. The first entry
encrypts, every entry decrypts. These specs start their own gunicorn processes
with an explicit ring against the shared E2E database:

* a ring of ``new,old`` reads data written under ``old`` in the browser and
  writes new values under ``new`` only (the stored token opens with ``new``
  alone and no longer with ``old``);
* blanks and empty entries around the keys change nothing;
* a malformed ring, a legacy single-key variable, or an empty ring under
  ``REQUIRE_ENCRYPTION`` stops the start with the documented message, never
  prints a key value, and never opens the port.
"""

import http.client
import socket
import subprocess
import time
import uuid

import pytest
from cryptography.fernet import Fernet, InvalidToken

from ._helpers import SEED_PASSWORD, django_shell, python_bin

pytestmark = pytest.mark.e2e

NOTE_FIELD = "client-notes"

_CREATE_CLIENT = (
    "from core.models import Client, DocumentType, Facility, User; "
    "f = Facility.objects.get(name='Hauptstelle'); "
    "dt = DocumentType.objects.for_facility(f).get(slug='standard-client', entity_type='client'); "
    "c = Client.objects.create(facility=f, pseudonym={pseudonym!r}, contact_stage='identified', "
    "age_cluster='unknown', created_by=User.objects.get(username='admin'), document_type=dt, "
    "data_json={{{field!r}: {note!r}}}); "
    "print('PK|' + str(c.pk))"
)
_READ_TOKEN = (
    "from core.models import Client; print('TOKEN|' + Client.objects.get(pk={pk!r}).data_json[{field!r}]['value'])"
)
_DELETE_CLIENT = "from core.models import Client; Client.objects.filter(pk={pk!r}).delete(); print('DELETED|ok')"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _gunicorn_command(port: int, workers: int = 1) -> list[str]:
    return [
        python_bin(),
        "-m",
        "gunicorn",
        "anlaufstelle.wsgi:application",
        "--bind",
        f"127.0.0.1:{port}",
        "--workers",
        str(workers),
        "--threads",
        "2",
        "--chdir",
        "src",
        "--timeout",
        "120",
    ]


def _wait_until_serving(port: int, process, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        assert process.poll() is None, "server exited before it served requests"
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        try:
            connection.request("GET", "/login/")
            connection.getresponse().read()
            return
        except (OSError, http.client.HTTPException):
            time.sleep(0.3)
        finally:
            connection.close()
    raise AssertionError(f"port {port} did not serve within {timeout_s}s")


@pytest.fixture
def serve_with_ring(e2e_env):
    """Start a gunicorn with the given ``ENCRYPTION_KEYS`` value; stop it after the test."""
    started = []

    def _serve(ring: str) -> str:
        port = _free_port()
        process = subprocess.Popen(
            _gunicorn_command(port),
            env={**e2e_env, "ENCRYPTION_KEYS": ring},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started.append(process)
        _wait_until_serving(port, process)
        return f"http://127.0.0.1:{port}"

    yield _serve
    for process in started:
        process.terminate()
        process.wait(timeout=15)


@pytest.fixture
def old_key_client(e2e_env):
    """A person whose note was stored under a ring of the old key only; yields ``(pk, old, new, pseudonym)``."""
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    pseudonym = f"Ring-Probe-{uuid.uuid4().hex[:8]}"
    created = django_shell(
        e2e_env | {"ENCRYPTION_KEYS": old_key},
        _CREATE_CLIENT.format(pseudonym=pseudonym, field=NOTE_FIELD, note="Notiz unter dem alten Schluessel"),
    )
    pk = next(line for line in created if line.startswith("PK|"))[3:]
    yield pk, old_key, new_key, pseudonym
    django_shell(e2e_env, _DELETE_CLIENT.format(pk=pk))


def _stored_token(e2e_env, pk: str, ring: str) -> bytes:
    lines = django_shell(e2e_env | {"ENCRYPTION_KEYS": ring}, _READ_TOKEN.format(pk=pk, field=NOTE_FIELD))
    return next(line for line in lines if line.startswith("TOKEN|"))[6:].encode()


def _sign_in_as_admin(browser, base_url):
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", "admin")
    page.fill("#id_password", SEED_PASSWORD)
    page.click('#login-form button[type="submit"]')
    page.wait_for_url(lambda url: "/login/" not in url)
    return page, context


@pytest.mark.parametrize(
    "spelling",
    [
        pytest.param("{new},{old}", id="plain"),
        pytest.param("  {new} ,\t{old}\n", id="blanks-around-entries"),
        pytest.param(",{new},, {old} ,", id="empty-entries"),
    ],
)
def test_ring_with_new_key_first_reads_old_data_and_writes_with_the_new_key(
    spelling, browser, e2e_env, serve_with_ring, old_key_client
):
    pk, old_key, new_key, pseudonym = old_key_client
    ring = spelling.format(new=new_key, old=old_key)
    old_token = _stored_token(e2e_env, pk, old_key)
    assert Fernet(old_key).decrypt(old_token) == b"Notiz unter dem alten Schluessel"

    base_url = serve_with_ring(ring)
    page, context = _sign_in_as_admin(browser, base_url)
    try:
        # Data written under the old key is readable through the ring.
        page.goto(f"{base_url}/clients/{pk}/", wait_until="domcontentloaded")
        assert pseudonym in page.locator("body").inner_text()
        assert "Notiz unter dem alten Schluessel" in page.locator("body").inner_text()

        # A new value goes out under the first entry.
        page.goto(f"{base_url}/clients/{pk}/edit/", wait_until="domcontentloaded")
        page.fill(f"textarea[name='{NOTE_FIELD}']", "Notiz unter dem neuen Schluessel")
        page.locator("button[type='submit']:has-text('Speichern')").first.click()
        page.wait_for_url(f"**/clients/{pk}/")
        assert "Notiz unter dem neuen Schluessel" in page.locator("body").inner_text()
    finally:
        context.close()

    new_token = _stored_token(e2e_env, pk, ring)
    assert new_token != old_token
    assert Fernet(new_key).decrypt(new_token) == b"Notiz unter dem neuen Schluessel"
    with pytest.raises(InvalidToken):
        Fernet(old_key).decrypt(new_token)


def _start_must_fail(e2e_env, extra_env: dict[str, str]) -> subprocess.CompletedProcess:
    port = _free_port()
    result = subprocess.run(
        _gunicorn_command(port),
        env={**e2e_env, **extra_env},
        capture_output=True,
        text=True,
        timeout=90,
    )
    # The process ended on its own: nothing was left listening.
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0
    return result


@pytest.mark.parametrize(
    ("env_template", "message"),
    [
        pytest.param(
            {"ENCRYPTION_KEYS": "{good},{bad}"},
            "ENCRYPTION_KEYS entry 2 is not a valid Fernet key.",
            id="second-entry-invalid",
        ),
        pytest.param(
            {"ENCRYPTION_KEYS": " , {bad} ,{good}"},
            "ENCRYPTION_KEYS entry 1 is not a valid Fernet key.",
            id="empty-entry-does-not-shift-the-position",
        ),
        pytest.param(
            {"ENCRYPTION_KEYS": "{good}", "ENCRYPTION_KEY": "{good}"},
            "ENCRYPTION_KEY is no longer supported",
            id="legacy-single-key-next-to-a-ring",
        ),
        pytest.param(
            {"ENCRYPTION_KEYS": " , ,\n", "REQUIRE_ENCRYPTION": "1"},
            "REQUIRE_ENCRYPTION is set but ENCRYPTION_KEYS is empty",
            id="ring-without-keys-under-require-encryption",
        ),
    ],
)
def test_start_with_a_broken_key_configuration_stops_with_the_documented_message(env_template, message, e2e_env):
    good, bad = Fernet.generate_key().decode(), "not-a-fernet-key"
    extra_env = {name: value.format(good=good, bad=bad) for name, value in env_template.items()}
    result = _start_must_fail(e2e_env, extra_env)

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert message in output, output
    assert good not in output
