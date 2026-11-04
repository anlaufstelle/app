"""Characterize how the settings modules split ``ENCRYPTION_KEYS`` into entries.

``validate_encryption_key_ring`` numbers entries after dropping empty and
whitespace-only elements, and ``settings/dev.py`` only falls back to
``LOCAL_ENCRYPTION_KEY_FILE`` when the configured ring has no entry. These tests
pin the observable start behaviour (exit code, exact error/warning text,
chosen key source) for both paths. No output may contain a key value.
"""

from __future__ import annotations

import pytest

from tests.test_encryption_ring_only import KEY, OTHER_KEY, _run_settings

INVALID = "private-invalid-key"
REQUIRE_MESSAGE = (
    "django.core.exceptions.ImproperlyConfigured: REQUIRE_ENCRYPTION is set but ENCRYPTION_KEYS is empty — dev "
    "settings refuse to start instead of storing fields in plaintext. Set ENCRYPTION_KEYS (comma-separated Fernet "
    "key ring) or remove REQUIRE_ENCRYPTION for purely local development with throwaway data."
)
PLAINTEXT_MARKER = "PLAINTEXT MODE"


def _invalid_entry(position: int) -> str:
    return f"django.core.exceptions.ImproperlyConfigured: ENCRYPTION_KEYS entry {position} is not a valid Fernet key."


def _duplicate_entry(position: int) -> str:
    return f"RuntimeWarning: ENCRYPTION_KEYS entry {position} duplicates an earlier entry."


def _assert_no_key_material(result) -> None:
    for secret in (KEY, OTHER_KEY, INVALID):
        assert secret not in result.stderr
        assert secret not in result.stdout


class TestValidationNumbersEntriesAfterDroppingEmptyOnes:
    @pytest.mark.parametrize(
        ("ring", "position"),
        [
            (f"{INVALID}, , {KEY}", 1),
            (f" , ,{KEY},  ,{INVALID}", 2),
            (f"{KEY},\t,{OTHER_KEY},,{INVALID}", 3),
            (f",,{INVALID},{KEY}", 1),
        ],
        ids=["first", "after-empty-entries", "after-tab-entry", "leading-separators"],
    )
    def test_invalid_entry_reports_its_position(self, ring, position):
        result = _run_settings("base", {"ENCRYPTION_KEYS": ring})

        assert result.returncode == 1
        assert result.stderr.strip().splitlines()[-1] == _invalid_entry(position)
        _assert_no_key_material(result)

    @pytest.mark.parametrize(
        ("ring", "positions"),
        [
            (f" , {KEY}, ,{OTHER_KEY},  {KEY} ", [3]),
            (f"{KEY},{KEY},,{KEY}", [2, 3]),
            (f"{OTHER_KEY},{KEY}, ,{OTHER_KEY},{KEY}", [3, 4]),
        ],
        ids=["after-empty-entries", "repeated", "two-keys-repeated"],
    )
    def test_duplicates_warn_with_their_position(self, ring, positions):
        result = _run_settings("base", {"ENCRYPTION_KEYS": ring})

        assert result.returncode == 0, result.stderr
        warnings = [line for line in result.stderr.splitlines() if "duplicates an earlier entry" in line]
        assert [line.split(": ", 1)[1] for line in warnings] == [_duplicate_entry(p) for p in positions]
        _assert_no_key_material(result)

    def test_duplicate_warning_comes_before_the_invalid_entry_error(self):
        result = _run_settings("base", {"ENCRYPTION_KEYS": f"{KEY}, ,{KEY},{INVALID}"})

        assert result.returncode == 1
        lines = result.stderr.strip().splitlines()
        assert lines[-1] == _invalid_entry(3)
        assert any(line.endswith(_duplicate_entry(2)) for line in lines)
        _assert_no_key_material(result)


def _dev_key_source(env: dict[str, str], ring: str) -> str:
    probe = (
        "import anlaufstelle.settings.dev as s\n"
        f"print('file' if s.ENCRYPTION_KEYS == {KEY!r} else 'environment' if s.ENCRYPTION_KEYS == {ring!r} "
        "else 'other')\n"
    )
    result = _run_settings("dev", env, expr=probe)
    assert result.returncode == 0, result.stderr
    _assert_no_key_material(result)
    return result.stdout.strip()


class TestDevLocalKeyFileFallback:
    @pytest.mark.parametrize(
        "ring",
        ["", "   ", ",", " , ,, ", "\t,\n"],
        ids=["empty", "whitespace", "separator", "separators", "tab-newline"],
    )
    def test_ring_without_entries_reads_the_file(self, ring, tmp_path):
        key_file = tmp_path / "encryption.key"
        key_file.write_text(f"{KEY}\n", encoding="utf-8")

        env = {"ENCRYPTION_KEYS": ring, "LOCAL_ENCRYPTION_KEY_FILE": str(key_file)}

        assert _dev_key_source(env, ring) == "file"

    @pytest.mark.parametrize(
        "ring",
        [OTHER_KEY, f" , {OTHER_KEY} ,", f",,{OTHER_KEY}"],
        ids=["single", "padded", "leading-separators"],
    )
    def test_ring_with_an_entry_keeps_the_environment(self, ring, tmp_path):
        key_file = tmp_path / "encryption.key"
        key_file.write_text(f"{KEY}\n", encoding="utf-8")

        env = {"ENCRYPTION_KEYS": ring, "LOCAL_ENCRYPTION_KEY_FILE": str(key_file)}

        assert _dev_key_source(env, ring) == "environment"

    def test_invalid_file_entry_reports_its_position_in_the_file(self, tmp_path):
        key_file = tmp_path / "encryption.key"
        key_file.write_text(f" , {KEY}, ,{INVALID}\n", encoding="utf-8")

        result = _run_settings("dev", {"ENCRYPTION_KEYS": " , ", "LOCAL_ENCRYPTION_KEY_FILE": str(key_file)})

        assert result.returncode == 1
        assert result.stderr.strip().splitlines()[-1] == _invalid_entry(2)
        _assert_no_key_material(result)

    @pytest.mark.parametrize("file_content", [" , ,\n", "\n"], ids=["separators", "empty"])
    def test_file_without_entries_falls_back_to_plaintext_banner(self, file_content, tmp_path):
        key_file = tmp_path / "encryption.key"
        key_file.write_text(file_content, encoding="utf-8")

        result = _run_settings("dev", {"LOCAL_ENCRYPTION_KEY_FILE": str(key_file)})

        assert result.returncode == 0, result.stderr
        assert PLAINTEXT_MARKER in result.stderr

    @pytest.mark.parametrize("ring", ["   ", " , ,, "], ids=["whitespace", "separators"])
    def test_ring_without_entries_and_no_file_shows_plaintext_banner(self, ring):
        result = _run_settings("dev", {"ENCRYPTION_KEYS": ring})

        assert result.returncode == 0, result.stderr
        assert PLAINTEXT_MARKER in result.stderr

    @pytest.mark.parametrize("ring", ["", "   ", " , ,, "], ids=["empty", "whitespace", "separators"])
    def test_ring_without_entries_refuses_under_require_encryption(self, ring):
        result = _run_settings("dev", {"ENCRYPTION_KEYS": ring, "REQUIRE_ENCRYPTION": "1"})

        assert result.returncode == 1
        assert result.stderr.strip().splitlines()[-1] == REQUIRE_MESSAGE

    def test_ring_with_an_entry_starts_under_require_encryption_without_banner(self):
        result = _run_settings("dev", {"ENCRYPTION_KEYS": f" , {KEY} ,", "REQUIRE_ENCRYPTION": "1"})

        assert result.returncode == 0, result.stderr
        assert PLAINTEXT_MARKER not in result.stderr
        _assert_no_key_material(result)
