"""Tests fuer den Off-Site-Backup-State-File-Mechanismus (Refs #797).

Das Backup-Skript ``scripts/backup.sh`` schreibt bei Off-Site-Fehlern einen
Counter ins State-File ``$BACKUP_STATE_DIR/.offsite_state``. Erst beim
zweiten aufeinanderfolgenden Fehler endet das Skript mit Exit ≠ 0 — sonst
wuerde jeder transiente Netzwerk-Fehler den Cron-Lauf rot faerben.

Wir testen die State-Logik isoliert mit einem stripped-down Bash-Snippet,
das die produktive Logik 1:1 spiegelt (keine echten Backups noetig).
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path


def _run_offsite_logic(state_dir: Path, *, succeed: bool) -> tuple[int, Path]:
    """Fuehre das State-File-Snippet aus scripts/backup.sh isoliert aus.

    Returns (exit_code, state_file_path).
    """
    state_file = state_dir / ".offsite_state"
    script = textwrap.dedent(
        f"""
        set -euo pipefail
        BACKUP_STATE_DIR={state_dir}
        BACKUP_DIR={state_dir}
        OFFSITE_OK={"true" if succeed else "false"}
        OFFSITE_STATE_FILE="${{BACKUP_STATE_DIR:-$BACKUP_DIR}}/.offsite_state"
        mkdir -p "$(dirname "$OFFSITE_STATE_FILE")"
        PREV_FAIL_COUNT=0
        if [[ -f "$OFFSITE_STATE_FILE" ]]; then
            PREV_FAIL_COUNT=$(cat "$OFFSITE_STATE_FILE" 2>/dev/null || echo 0)
        fi
        if [[ "$OFFSITE_OK" == true ]]; then
            echo "Off-Site-Sync erfolgreich."
            echo 0 > "$OFFSITE_STATE_FILE"
        else
            FAIL_COUNT=$((PREV_FAIL_COUNT + 1))
            echo "$FAIL_COUNT" > "$OFFSITE_STATE_FILE"
            if (( FAIL_COUNT >= 2 )); then
                echo "FATAL: $FAIL_COUNT consecutive failures"
                exit 1
            fi
        fi
        """
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return result.returncode, state_file


class TestOffsiteState:
    def test_first_failure_does_not_exit_nonzero(self, tmp_path):
        rc, state_file = _run_offsite_logic(tmp_path, succeed=False)
        assert rc == 0, "Erster Fehler darf den Skript-Run NICHT roten — kein Cron-Alarm bei transienten Aussetzern."
        assert state_file.read_text().strip() == "1"

    def test_second_consecutive_failure_exits_nonzero(self, tmp_path):
        # Lauf 1: schlaegt fehl, Counter=1, Exit=0
        rc1, state_file = _run_offsite_logic(tmp_path, succeed=False)
        assert rc1 == 0
        assert state_file.read_text().strip() == "1"

        # Lauf 2: erneut Fehler, Counter=2 -> Exit != 0
        rc2, _ = _run_offsite_logic(tmp_path, succeed=False)
        assert rc2 != 0, "Zweiter aufeinanderfolgender Fehler MUSS Exit != 0 setzen (Refs #797)."
        assert state_file.read_text().strip() == "2"

    def test_success_resets_counter(self, tmp_path):
        # 1 Fehler, dann Erfolg → Counter zurueck auf 0
        rc1, state_file = _run_offsite_logic(tmp_path, succeed=False)
        assert rc1 == 0
        assert state_file.read_text().strip() == "1"

        rc2, _ = _run_offsite_logic(tmp_path, succeed=True)
        assert rc2 == 0
        assert state_file.read_text().strip() == "0"

        # Naechster Fehler ist wieder #1, kein Exit ≠ 0.
        rc3, _ = _run_offsite_logic(tmp_path, succeed=False)
        assert rc3 == 0
        assert state_file.read_text().strip() == "1"
