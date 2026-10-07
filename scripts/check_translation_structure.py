#!/usr/bin/env python3
"""Compare DE/EN Markdown structure; this does not prove semantic parity."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
METRICS = ("headings", "code_blocks", "tables", "table_columns", "commands", "env_variables", "make_targets")
# The source explicitly labels this entire document as a summary, not a 1:1 translation.
ALLOWLIST = {
    "docs/en/domain-concept-summary.md": dict.fromkeys(
        METRICS, "Explicitly shortened domain concept summary; see its opening notice."
    ),
    "docs/en/README.md": dict.fromkeys(
        ("headings", "tables", "table_columns"),
        "The English index lists translated documents and groups German-only ones; sections differ.",
    ),
    "docs/en/glossary.md": {
        "table_columns": "Bilingual glossary adds an English column to the German term/definition table."
    },
}
# Command pairs whose only difference is a translated example value, keyed by the EN file as
# (DE command, EN command). Any other command difference in these files is still reported.
TRANSLATED_COMMANDS = {
    "CONTRIBUTING.en.md": {
        ("git checkout -b feature/kurze-beschreibung", "git checkout -b feature/short-description"),
        ("git checkout -b fix/was-repariert-wird", "git checkout -b fix/what-is-being-fixed"),
        (
            "python src/manage.py load_package --file paket.yaml --facility <placeholder> --dry-run",
            "python src/manage.py load_package --file package.yaml --facility <placeholder> --dry-run",
        ),
    },
    "docs/en/admin-guide.md": {
        (
            "docker compose -f docker-compose.prod.yml run --rm maintenance "
            'create_statistics_snapshots --facility "Meine Einrichtung"',
            "docker compose -f docker-compose.prod.yml run --rm maintenance "
            'create_statistics_snapshots --facility "My Facility"',
        ),
    },
    "docs/en/ops-runbook.md": {
        (
            'git commit -m "chore: bump <placeholder> auf <placeholder>"',
            'git commit -m "chore: bump <placeholder> to <placeholder>"',
        ),
        (
            "python manage.py sendtestemail pruefadresse@example.org",
            "python manage.py sendtestemail test-address@example.org",
        ),
    },
}
SOURCE = re.compile(r"<!--\s*translation-source:\s*(\S+)[^\n]*?-->")
COMMAND = re.compile(
    r"^(?:\$\s+)?(?:[A-Z_][A-Z_0-9]*=\S+\s+)*(?:sudo\s+)?"
    r"(?:make|docker|python[\d.]*|pip[\d.]*|pytest|git|curl|flock|uv|npm|npx|systemctl|bash|sh)\b.*"
)


def structure(text: str) -> dict[str, object]:
    """Parse Markdown containers rather than counting apparent markers in literals."""
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    headings = [token.tag for token in tokens if token.type == "heading_open"]
    blocks = [token for token in tokens if token.type in {"fence", "code_block"}]
    tables: list[list[int]] = []
    row_cells = 0
    for token in tokens:
        if token.type == "table_open":
            tables.append([])
        elif token.type == "tr_open":
            row_cells = 0
        elif token.type in {"th_open", "td_open"}:
            row_cells += 1
        elif token.type == "tr_close":
            tables[-1].append(row_cells)
    commands: set[str] = set()
    env: set[str] = set()
    targets: set[str] = set()
    for block in blocks:
        for line in block.content.splitlines():
            line = re.sub(r"<[^<>\n]+>", "<placeholder>", line).strip()
            if not line or line.startswith("#"):
                continue
            # Comments may be translated; literal command arguments remain significant.
            line = re.sub(r"\s+#.*$", "", line)
            if COMMAND.match(line):
                commands.add(line)
            env.update(re.findall(r"\$\{?([A-Z_][A-Z_0-9]+)", line))
            env.update(re.findall(r"\b([A-Z_][A-Z_0-9]+)=", line))
            for match in re.finditer(r"\bmake\s+([^;&|]+)", line):
                targets.update(
                    word for word in match[1].split() if not word.startswith(("-", "\\")) and "=" not in word
                )
    return {
        "headings": headings,
        "code_blocks": [block.info.split()[0] if block.info.strip() else "" for block in blocks],
        "tables": [len(table) for table in tables],
        "table_columns": [sorted(set(table)) for table in tables],
        "commands": sorted(commands),
        "env_variables": sorted(env),
        "make_targets": sorted(targets),
    }


def translation_paths(root: Path) -> list[Path]:
    """Include root mirrors, sibling EN files and the docs/en translation tree."""
    docs = root / "docs"
    return sorted(set(root.glob("*.en.md")) | set(docs.rglob("*.en.md")) | set((docs / "en").rglob("*.md")))


def findings(root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    exceptions: list[str] = []
    for en in translation_paths(root):
        relative = en.relative_to(root).as_posix()
        text = en.read_text()
        match = SOURCE.search(text)
        if match is None:
            errors.append(f"{relative}: missing translation-source")
            continue
        source = (root / match[1]).resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            errors.append(f"{relative}: missing source {match[1]}")
            continue
        de_structure, en_structure = structure(source.read_text()), structure(text)
        for metric in METRICS:
            if de_structure[metric] == en_structure[metric]:
                continue
            reason = ALLOWLIST.get(relative, {}).get(metric)
            if metric == "commands" and not reason:
                pairs = TRANSLATED_COMMANDS.get(relative, set())
                de_only = set(de_structure[metric]) - set(en_structure[metric])
                en_only = set(en_structure[metric]) - set(de_structure[metric])
                for de_command, en_command in pairs:
                    if de_command in de_only and en_command in en_only:
                        de_only.discard(de_command)
                        en_only.discard(en_command)
                if not de_only and not en_only:
                    reason = "Example values are translated; the commands are otherwise identical."
            description = f"{relative} ↔ {match[1]}: {metric} DE={de_structure[metric]!r} EN={en_structure[metric]!r}"
            if reason:
                exceptions.append(f"ALLOW {relative}: {metric}: {reason}")
            else:
                errors.append(description)
    return errors, exceptions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    errors, exceptions = findings(args.root)
    for line in [*exceptions, *errors]:
        print(line)
    print(
        f"Translation structure: {len(translation_paths(args.root))} pairs, "
        f"{len(errors)} findings, {len(exceptions)} intentional differences"
    )
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
