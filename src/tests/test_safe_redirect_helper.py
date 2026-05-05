"""RF-T01: Charakterisierungstests für ``safe_redirect_path`` (Refs #770).

Open-Redirect-Helper: nur same-origin Pfade akzeptieren. ``startswith("/")``
allein ist unzureichend, weil der Browser ``//evil.example/`` als
protokoll-relative URL interpretiert (Phishing-Vektor).

Quelle: Sprint 1 — Sicherheits-Quick-Wins, Goldene Regel „Erst Tests, dann Fix".
"""

from __future__ import annotations

import pytest

from core.views.utils import safe_redirect_path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Akzeptiert: same-origin Pfad.
        ("/", "/"),
        ("/x", "/x"),
        ("/clients/", "/clients/"),
        ("/clients/?next=foo", "/clients/?next=foo"),
        ("/x/../../y", "/x/../../y"),  # Pfad-Traversal ist same-origin → erlaubt
        # Abgelehnt: protokoll-relativ (Browser interpretiert als externe Origin).
        ("//evil.example/login", "/"),
        ("///evil", "/"),
        ("////a", "/"),
        # Abgelehnt: explizite Schemes.
        ("http://evil.example/", "/"),
        ("https://evil.example/", "/"),
        ("javascript:alert(1)", "/"),
        ("data:text/html,<script>", "/"),
        ("ftp://x", "/"),
        ("file:///etc/passwd", "/"),
        # Abgelehnt: leere oder None Eingabe.
        ("", "/"),
        (None, "/"),
        # Abgelehnt: relative Pfade ohne führenden Slash.
        ("evil", "/"),
        ("clients/", "/"),
        # Abgelehnt: Whitespace-Tricks (führender Whitespace bricht startswith).
        (" /clients/", "/"),
    ],
)
def test_safe_redirect_path(raw, expected):
    assert safe_redirect_path(raw) == expected
