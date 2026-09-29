from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

GUIDES = (Path("docs/admin-guide.md"), Path("docs/en/admin-guide.md"))
RELEASES_URL = "https://github.com/anlaufstelle/app/releases"
FEED_URL = "https://github.com/anlaufstelle/app/releases.atom"


def _updates_section(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("## 4. "))
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


@pytest.mark.parametrize("guide", GUIDES)
def test_updates_section_links_release_page_and_atom_feed(guide: Path) -> None:
    section = _updates_section(guide)

    assert f"]({RELEASES_URL})" in section
    assert f"]({FEED_URL})" in section
