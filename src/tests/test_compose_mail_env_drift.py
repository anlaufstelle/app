"""Keep deployment Compose mail variables aligned with production settings."""

import ast
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

ROOT = Path(__file__).parents[2]
KNOWN_MAIL_VARIABLES = {
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "EMAIL_USE_TLS",
    "EMAIL_USE_SSL",
    "DEFAULT_FROM_EMAIL",
}


def _mail_variables_from_production_settings() -> set[str]:
    source = (ROOT / "src/anlaufstelle/settings/prod.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    variables: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        else:
            function_name = None
        if function_name not in {"get", "env_bool", "env_bool_strict", "env_int"}:
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            if argument.value.startswith("EMAIL_") or argument.value == "DEFAULT_FROM_EMAIL":
                variables.add(argument.value)
    return variables


def _compose_environment(path: Path) -> dict[str, object]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return document["services"]["web"]["environment"]


def _deployment_compose_paths() -> tuple[Path, ...]:
    staging_name = "docker-compose." + "staging.yml"
    return tuple(ROOT / name for name in ("docker-compose.prod.yml", "docker-compose.dev.yml", staging_name))


def test_production_mail_variable_discovery_is_not_empty() -> None:
    assert _mail_variables_from_production_settings() == KNOWN_MAIL_VARIABLES


@pytest.mark.parametrize("compose_path", _deployment_compose_paths(), ids=lambda path: path.name)
def test_web_environment_contains_production_mail_variables(compose_path: Path) -> None:
    if not compose_path.exists():
        pytest.skip(f"{compose_path.name} fehlt im öffentlichen Release-Baum")
    missing = _mail_variables_from_production_settings() - set(_compose_environment(compose_path))
    assert not missing, f"{compose_path.name}: fehlende web-Mailvariablen: {sorted(missing)}"


@pytest.mark.parametrize("compose_path", _deployment_compose_paths(), ids=lambda path: path.name)
@pytest.mark.parametrize("name", ["EMAIL_USE_TLS", "EMAIL_USE_SSL"])
def test_compose_mail_boolean_values_are_accepted_by_settings_parser(compose_path: Path, name: str) -> None:
    if not compose_path.exists():
        pytest.skip(f"{compose_path.name} fehlt im öffentlichen Release-Baum")
    value = _compose_environment(compose_path)[name]
    assert value in {"true", "false", "", "${EMAIL_USE_TLS:-true}", "${EMAIL_USE_SSL-}"}, (
        f"{compose_path.name}: {name} has unsupported value form {value!r}"
    )
