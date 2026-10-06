"""Regression tests for the Locust authentication helpers."""

import sys
import types
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

try:
    import locustfile
except ModuleNotFoundError as error:
    if error.name != "locust":
        raise
    fake_locust = types.ModuleType("locust")

    class FakeHttpUser:
        pass

    fake_locust.HttpUser = FakeHttpUser
    fake_locust.between = lambda _minimum, _maximum: None

    def fake_task(*_args, **_kwargs):
        return lambda function: function

    fake_locust.task = fake_task
    sys.modules["locust"] = fake_locust
    import locustfile


pytestmark = pytest.mark.architecture


@dataclass
class FakeResponse:
    text: str
    status_code: int = 200
    url: str = "http://testserver/login/"
    headers: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {}


class FakeClient:
    def __init__(self, login_response: FakeResponse, login_page: FakeResponse | None = None) -> None:
        self.login_response = login_response
        self.login_page = login_page or FakeResponse('name="csrfmiddlewaretoken" value="token"')
        self.posts: list[dict[str, object]] = []

    def get(self, path: str, **kwargs: object) -> FakeResponse:
        assert path == "/login/"
        return self.login_page

    def post(self, path: str, **kwargs: object) -> FakeResponse:
        self.posts.append(kwargs)
        return self.login_response


def _user(client: FakeClient) -> SimpleNamespace:
    return SimpleNamespace(client=client, host="http://testserver")


def test_staff_login_rejects_a_failed_login_page() -> None:
    user = _user(FakeClient(FakeResponse('name="csrfmiddlewaretoken" value="token"')))

    with pytest.raises(RuntimeError, match="login"):
        locustfile.login_user(user, "miriam", "password")


def test_login_rejects_missing_csrf_token() -> None:
    client = FakeClient(
        FakeResponse("welcome", status_code=302, url="http://testserver/"),
        login_page=FakeResponse("login"),
    )

    with pytest.raises(RuntimeError, match="CSRF"):
        locustfile.login_user(_user(client), "miriam", "password")


def test_login_accepts_a_redirect_away_from_login() -> None:
    client = FakeClient(FakeResponse("", status_code=302, url="http://testserver/"))

    locustfile.login_user(_user(client), "miriam", "password")

    assert client.posts[0]["data"] == {
        "csrfmiddlewaretoken": "token",
        "username": "miriam",
        "password": "password",
    }


def test_staff_user_uses_a_seeded_staff_account() -> None:
    assert locustfile.STAFF_USERNAME == "miriam"


def test_heavy_export_user_uses_a_seeded_lead_account() -> None:
    assert locustfile.LEAD_USERNAME == "emma"
