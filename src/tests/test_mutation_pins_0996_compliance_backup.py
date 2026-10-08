"""Pins for the demo backup and restore cards (``core.services.compliance.backup``).

Source: mutation delta of the release-0.99.6 checkpoint, 3 mutants.
Groups:

* ``_demo_card`` — the demo card carries the label its caller passes in.
* ``_backup_checks`` — under ``DEMO_MODE`` the backup card is titled
  ``Letztes Backup``.
* ``_restore_checks`` — under ``DEMO_MODE`` the restore card is titled
  ``Restore-Test``.
"""

from __future__ import annotations

from unittest.mock import patch

from core.services.compliance import backup


def _single(checks, key):
    cards = [c for c in checks if c.key == key]
    assert len(cards) == 1
    return cards[0]


class TestDemoCardLabels:
    def test_demo_backup_card_is_titled_last_backup(self, settings):
        """Under ``DEMO_MODE`` the backup card keeps the title ``Letztes Backup``.

        Kills ``x__backup_checks__mutmut_2`` (label ``None`` passed in) and
        ``x__demo_card__mutmut_2`` (label replaced by ``None``).
        """
        settings.DEMO_MODE = True
        with patch("core.services.system.health.last_backup_info", return_value=None):
            card = _single(backup._backup_checks(), "backup_age")

        assert card.label == "Letztes Backup"

    def test_demo_restore_card_is_titled_restore_test(self, settings):
        """Under ``DEMO_MODE`` the restore card keeps the title ``Restore-Test``.

        Kills ``x__restore_checks__mutmut_2`` (label ``None`` passed in) and
        ``x__demo_card__mutmut_2`` (label replaced by ``None``).
        """
        settings.DEMO_MODE = True

        card = _single(backup._restore_checks(), "restore_verified")

        assert card.label == "Restore-Test"
