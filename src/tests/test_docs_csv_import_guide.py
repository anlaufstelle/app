from pathlib import Path

import pytest
from django.template.base import Lexer, TokenType
from django.utils.text import unescape_string_literal
from django.utils.translation import gettext, override

pytestmark = pytest.mark.architecture

STEPPER_TEMPLATE = Path("src/templates/core/import/partials/_stepper.html")
ADMIN_GUIDES = (
    (Path("docs/admin-guide.md"), "### 2.12 ", "de"),
    (Path("docs/en/admin-guide.md"), "### 2.12 ", "en"),
)
REFERENCES = (
    (Path("docs/user-guide.md"), "admin-guide.md#212-import-assistent"),
    (Path("docs/en/user-guide.md"), "admin-guide.md#212-import-wizard"),
    (Path("docs/faq.md"), "admin-guide.md#212-import-assistent"),
)


def _section(path: Path, heading_prefix: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(heading_prefix))
    level = len(lines[start]) - len(lines[start].lstrip("#"))
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("#") and len(lines[index]) - len(lines[index].lstrip("#")) <= level
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _step_labels() -> tuple[str, ...]:
    labels: list[str] = []
    in_step = False
    found_label = False

    for token in Lexer(STEPPER_TEMPLATE.read_text(encoding="utf-8")).tokenize():
        if token.token_type is TokenType.TEXT:
            if "<li " in token.contents and 'data-testid="import-step-' in token.contents:
                in_step = True
                found_label = False
            if in_step and "</li>" in token.contents:
                in_step = False
        elif token.token_type is TokenType.BLOCK and in_step and not found_label:
            bits = token.split_contents()
            if bits and bits[0] == "trans" and len(bits) >= 2:
                labels.append(unescape_string_literal(bits[1]))
                found_label = True

    return tuple(labels)


def test_import_step_labels_are_discovered_from_template() -> None:
    assert _step_labels()


@pytest.mark.parametrize(("guide", "heading_prefix", "language"), ADMIN_GUIDES)
def test_admin_guide_documents_import_route_and_visible_steps(guide: Path, heading_prefix: str, language: str) -> None:
    section = _section(guide, heading_prefix)

    assert "/import/" in section
    with override(language):
        for label in _step_labels():
            assert gettext(label) in section


@pytest.mark.parametrize(("guide", "target"), REFERENCES)
def test_related_guides_link_to_import_assistant(guide: Path, target: str) -> None:
    assert f"]({target})" in guide.read_text(encoding="utf-8")
