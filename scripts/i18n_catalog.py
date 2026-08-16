#!/usr/bin/env python3
"""Update Django PO files without importing makemessages' global ordering.

The Django command is still used for extraction.  Its output is generated in
a temporary copy of the repository and then merged into the checked-in
catalogue while retaining the order of existing PO blocks.  This keeps a
small string change reviewable even after a periodic full extraction.

Refs #1915.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ("de", "en")
PO_RELATIVE = Path("src/locale")
PO_NAME = "LC_MESSAGES/django.po"
COPY_EXCLUDE_NAMES = {
    ".git",
    ".venv",
    ".venv-py313-bak",
    ".worktrees",
    "node_modules",
    "mutants",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "certs",
    "secrets",
}
COPY_EXCLUDE_PATHS = {"src/media", "src/staticfiles"}


@dataclass(frozen=True)
class PoBlock:
    text: str
    key: tuple[str, str, str] | None


def _blocks(text: str) -> list[PoBlock]:
    """Split a PO into blank-line separated blocks and extract stable keys."""

    blocks: list[PoBlock] = []
    for raw in text.split("\n\n"):
        block = raw.strip("\n")
        if not block:
            continue
        values: dict[str, str] = {}
        current: str | None = None
        for line in block.splitlines():
            field = next((name for name in ("msgctxt", "msgid_plural", "msgid") if line.startswith(f"{name} ")), None)
            if field:
                current = field
                values[field] = _po_string(line[len(field) + 1 :])
            elif line.startswith('"') and current:
                values[current] += _po_string(line)
            else:
                current = None
        msgid = values.get("msgid", "")
        key = None if not msgid else (values.get("msgctxt", ""), msgid, values.get("msgid_plural", ""))
        blocks.append(PoBlock(block, key))
    return blocks


def _values(block: PoBlock) -> dict[str, str]:
    """Return all PO fields from one block, including plural translations."""

    values: dict[str, str] = {}
    current: str | None = None
    for line in block.text.splitlines():
        field = re.match(r"(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?) ", line)
        if field:
            current = field.group(1)
            values[current] = _po_string(line[len(current) + 1 :])
        elif line.startswith('"') and current:
            values[current] += _po_string(line)
        else:
            current = None
    return values


def _po_string(value: str) -> str:
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value.strip('"')
    return parsed if isinstance(parsed, str) else str(parsed)


def merge_preserving_order(existing: str, generated: str) -> str:
    """Return generated content with existing entries in their old order.

    Existing entries missing from the generated catalogue are intentionally
    omitted (the caller can report them as removed). New entries follow the
    generated order at the end. The generated block itself remains intact, so
    references, flags, plural forms and translations stay authoritative.
    """

    old = _blocks(existing)
    new = _blocks(generated)
    by_key = {block.key: block for block in new if block.key is not None}
    ordered: list[PoBlock] = [block for block in new if block.key is None]
    seen: set[tuple[str, str, str]] = set()
    for block in old:
        if block.key is not None and block.key in by_key:
            generated_block = by_key[block.key]
            # References are intentionally kept from the checked-in block.
            # They are positional review noise; ``check`` reports their drift
            # separately and a maintainer can refresh them in a focused pass.
            if _semantic_signature(block) == _semantic_signature(generated_block):
                ordered.append(block)
            else:
                ordered.append(generated_block)
            seen.add(block.key)
    ordered.extend(block for block in new if block.key is not None and block.key not in seen)
    return "\n\n".join(block.text for block in ordered) + "\n"


def _semantic_signature(block: PoBlock) -> tuple[tuple[str, str], ...]:
    """Normalize fields/comments while ignoring source locations and wrapping."""

    values: dict[str, str] = {}
    current: str | None = None
    comments: list[str] = []
    for line in block.text.splitlines():
        if line.startswith("#:"):
            continue
        if line.startswith("#"):
            if not line.startswith("# POT-Creation-Date:"):
                comments.append(line)
            continue
        field = re.match(r"(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?) ", line)
        if field:
            current = field.group(1)
            values[current] = _po_string(line[len(current) + 1 :])
        elif line.startswith('"') and current:
            values[current] += _po_string(line)
        else:
            current = None
    return tuple(sorted((*values.items(), *(("comment", comment) for comment in comments))))


def catalogue_keys(text: str) -> set[tuple[str, str, str]]:
    return {block.key for block in _blocks(text) if block.key is not None}


def _ignore_copy(directory: str, names: list[str]) -> set[str]:
    relative_directory = Path(directory).resolve().relative_to(ROOT)
    ignored: set[str] = set()
    for name in names:
        relative_path = (relative_directory / name).as_posix()
        if name in COPY_EXCLUDE_NAMES or relative_path in COPY_EXCLUDE_PATHS or name.startswith(".env"):
            ignored.add(name)
    return ignored


def _generated_catalogues() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temp = tempfile.TemporaryDirectory(prefix="anlaufstelle-i18n-")
    mirror = Path(temp.name) / "repo"
    shutil.copytree(ROOT, mirror, ignore=_ignore_copy)
    command = [
        sys.executable,
        "src/manage.py",
        "makemessages",
        "-l",
        "de",
        "-l",
        "en",
        "--no-obsolete",
        "--no-wrap",
    ]
    result = subprocess.run(command, cwd=mirror, capture_output=True, text=True, check=False)
    if result.returncode:
        temp.cleanup()
        raise RuntimeError(result.stderr or result.stdout or "makemessages failed")
    return temp, mirror


def _paths(root: Path, language: str) -> tuple[Path, Path]:
    po = root / PO_RELATIVE / language / PO_NAME
    return po, po.with_suffix(".mo")


def _compile(po: Path, mo: Path) -> None:
    result = subprocess.run(
        ["msgfmt", "--check-format", "-o", str(mo), str(po)], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout or f"msgfmt failed for {po}")


def _validate_catalogue(text: str, language: str) -> None:
    """Reject catalogues that would violate the committed i18n quality gates."""

    fuzzy: list[str] = []
    untranslated: list[str] = []
    non_identity_de: list[str] = []
    non_identity_de_plural: list[str] = []
    for block in _blocks(text):
        if block.key is None:
            continue
        values = _values(block)
        msgid = values["msgid"]
        if any(line.startswith("#,") and "fuzzy" in line for line in block.text.splitlines()):
            fuzzy.append(msgid)
        translations = [value for field, value in values.items() if field == "msgstr" or field.startswith("msgstr[")]
        if not translations or any(not value for value in translations):
            untranslated.append(msgid)
        if language == "de":
            if "msgid_plural" in values:
                if values.get("msgstr[0]") != msgid:
                    non_identity_de_plural.append(f"{msgid} (plural form 0)")
                if values.get("msgstr[1]") != values["msgid_plural"]:
                    non_identity_de_plural.append(f"{msgid} (plural form 1)")
            elif values.get("msgstr") != msgid:
                non_identity_de.append(msgid)

    problems: list[str] = []
    if fuzzy:
        problems.append(f"{len(fuzzy)} fuzzy ({', '.join(fuzzy[:3])})")
    if untranslated:
        problems.append(f"{len(untranslated)} untranslated ({', '.join(untranslated[:3])})")
    if non_identity_de:
        problems.append(f"{len(non_identity_de)} non-identity German entries ({', '.join(non_identity_de[:3])})")
    if non_identity_de_plural:
        problems.append(
            f"{len(non_identity_de_plural)} non-identity German plural entries "
            f"({', '.join(non_identity_de_plural[:3])})"
        )
    if problems:
        raise RuntimeError(f"{language} catalogue fails i18n quality gates: {'; '.join(problems)}")


def _report(existing: str, generated: str, language: str) -> bool:
    old_keys = catalogue_keys(existing)
    new_keys = catalogue_keys(generated)
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    old_by_key = {block.key: block for block in _blocks(existing) if block.key is not None}
    new_by_key = {block.key: block for block in _blocks(generated) if block.key is not None}
    ref_changes = sum(1 for key in old_keys & new_keys if _locations(old_by_key[key]) != _locations(new_by_key[key]))
    semantic_changes = sorted(
        key
        for key in old_keys & new_keys
        if _semantic_signature(old_by_key[key]) != _semantic_signature(new_by_key[key])
    )
    print(
        f"{language}: {len(added)} neue, {len(removed)} entfernte, "
        f"{len(semantic_changes)} semantisch geänderte, {ref_changes} Referenzänderung(en); "
        "bestehende Reihenfolge bleibt erhalten"
    )
    for key in added:
        print(f"  + {key[1]}")
    for key in removed:
        print(f"  - {key[1]}")
    for key in semantic_changes:
        print(f"  ~ {key[1]}")
    return bool(added or removed or semantic_changes)


def _locations(block: PoBlock) -> tuple[str, ...]:
    return tuple(line for line in block.text.splitlines() if line.startswith("#:"))


def _stage_catalogues(merged: dict[str, str], temporary_root: Path) -> dict[str, tuple[Path, Path]]:
    """Validate and compile every candidate before modifying the live tree."""

    staged_root = temporary_root / "staged"
    staged: dict[str, tuple[Path, Path]] = {}
    for language, content in merged.items():
        po, mo = _paths(staged_root, language)
        po.parent.mkdir(parents=True, exist_ok=True)
        po.write_text(content, encoding="utf-8")
        _validate_catalogue(content, language)
        _compile(po, mo)
        staged[language] = (po, mo)
    return staged


def _atomic_copy(source: Path, destination: Path) -> None:
    """Replace one live catalogue file using a same-directory temporary file."""

    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
        with source.open("rb") as source_handle:
            shutil.copyfileobj(source_handle, handle)
    try:
        os.chmod(temporary, source.stat().st_mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _commit_staged_catalogues(staged: dict[str, tuple[Path, Path]]) -> None:
    """Commit staged PO/MO pairs, restoring every replaced file on failure."""

    live_files = [file for language in LOCALES for file in _paths(ROOT, language)]
    originals = {file: file.read_bytes() for file in live_files}
    replaced: list[Path] = []
    try:
        for language in LOCALES:
            staged_po, staged_mo = staged[language]
            live_po, live_mo = _paths(ROOT, language)
            for source, destination in ((staged_po, live_po), (staged_mo, live_mo)):
                _atomic_copy(source, destination)
                replaced.append(destination)
    except OSError as error:
        rollback_errors: list[OSError] = []
        for destination in reversed(replaced):
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(originals[destination])
            try:
                os.replace(temporary, destination)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
            finally:
                temporary.unlink(missing_ok=True)
        if rollback_errors:
            raise RuntimeError("i18n update failed and rollback was incomplete") from error
        raise RuntimeError("i18n update failed; replaced catalogue files were rolled back") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "update"))
    args = parser.parse_args(argv)
    temp, mirror = _generated_catalogues()
    try:
        merged: dict[str, str] = {}
        has_semantic_drift = False
        for language in LOCALES:
            existing_path, _ = _paths(ROOT, language)
            generated_path, _ = _paths(mirror, language)
            existing = existing_path.read_text(encoding="utf-8")
            generated = generated_path.read_text(encoding="utf-8")
            has_semantic_drift = _report(existing, generated, language) or has_semantic_drift
            merged[language] = merge_preserving_order(existing, generated)
        if args.command == "check":
            return int(has_semantic_drift)
        staged = _stage_catalogues(merged, Path(temp.name))
        _commit_staged_catalogues(staged)
        print("PO-Dateien aktualisiert und beide .mo-Dateien nach vollständiger Validierung kompiliert.")
    finally:
        temp.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
