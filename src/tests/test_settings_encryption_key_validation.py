"""Settings reject malformed key-ring entries without disclosing key material."""

import pytest

from tests.test_encryption_ring_only import ALL_MODULES, LOCAL_MODULES, _run_settings

KEY = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="
OTHER_KEY = "YmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmI="


@pytest.mark.parametrize("module", ALL_MODULES)
@pytest.mark.parametrize("position", [1, 2])
@pytest.mark.parametrize("invalid", ["private-invalid-key", "bm90LTMyLWJ5dGVz", "non-ascii-ä"])
def test_invalid_entry_stops_settings_import_without_disclosure(module, position, invalid):
    ring = f"{invalid},{KEY}" if position == 1 else f"{KEY},{invalid}"
    result = _run_settings(module, {"ENCRYPTION_KEYS": ring})

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert f"ENCRYPTION_KEYS entry {position}" in result.stderr
    assert "Fernet" in result.stderr
    for secret in (invalid, KEY):
        assert secret not in result.stderr
        assert secret not in result.stdout


@pytest.mark.parametrize("module", ALL_MODULES)
def test_valid_rotation_ring_starts_and_preserves_order(module):
    ring = f" , {KEY} , {OTHER_KEY} , "
    result = _run_settings(
        module,
        {"ENCRYPTION_KEYS": ring},
        expr=f"from anlaufstelle.settings.{module} import ENCRYPTION_KEYS; print(ENCRYPTION_KEYS)",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ring.strip()


@pytest.mark.parametrize("module", ALL_MODULES)
def test_duplicate_entries_warn_without_disclosure(module):
    result = _run_settings(module, {"ENCRYPTION_KEYS": f"{KEY}, {KEY}"})

    assert result.returncode == 0, result.stderr
    assert "ENCRYPTION_KEYS entry 2 duplicates an earlier entry" in result.stderr
    assert KEY not in result.stderr


@pytest.mark.parametrize("module", LOCAL_MODULES)
def test_local_file_ring_is_validated(module, tmp_path):
    key_file = tmp_path / "key-ring"
    key_file.write_text(f"{KEY},private-invalid-key", encoding="utf-8")
    result = _run_settings(module, {"LOCAL_ENCRYPTION_KEY_FILE": str(key_file)})

    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "ENCRYPTION_KEYS entry 2" in result.stderr
    assert "private-invalid-key" not in result.stderr
    assert KEY not in result.stderr
