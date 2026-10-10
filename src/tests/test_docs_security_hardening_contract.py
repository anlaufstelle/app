"""Security documentation must not retain superseded recovery and release claims."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("path", "obsolete_claim"),
    [
        ("docs/security-notes.md", "nur** deren SHA-256-Hex-Digest"),
        ("docs/admin-guide.md", "Für ein Konto mit bestätigtem TOTP-Gerät bietet die Oberfläche keinen Schritt"),
        ("docs/en/admin-guide.md", "the interface offers no step to set up a new device"),
        ("docs/faq.md", "Danach zeigt der Browser einen Fehler (HTTP 405)"),
        ("docs/admin-guide.md", "sie wertet nur die 500 jüngsten Herausgaben aus"),
        ("docs/en/admin-guide.md", "it evaluates only the 500 most recent releases"),
        ("docs/threat-model.md", "src/core/views/*.py` (nicht rekursiv)"),
        ("docs/adr/025-csp-unsafe-eval-admin.md", "Die gevendorten Unfold-Templates prüft keiner von ihnen"),
    ],
)
def test_docs_remove_superseded_security_claims(path, obsolete_claim):
    assert obsolete_claim not in (REPO_ROOT / path).read_text(encoding="utf-8")
