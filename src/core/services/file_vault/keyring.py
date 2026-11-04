"""Runtime access to the shared key-ring parser.

The implementation lives in ``anlaufstelle.settings._keyring`` because the
settings start gate needs it before Django's app registry is ready.
"""

from __future__ import annotations

from anlaufstelle.settings._keyring import split_key_ring

__all__ = ["split_key_ring"]
