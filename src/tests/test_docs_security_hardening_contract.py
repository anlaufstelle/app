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


@pytest.mark.parametrize(
    ("path", "resolver_label"),
    [
        ("docs/security-notes.md", "URL-Resolver"),
        ("docs/threat-model.md", "URL-Resolver"),
        ("docs/en/security-notes.md", "URL resolver"),
        ("docs/en/threat-model.md", "URL resolver"),
    ],
)
def test_mutation_guard_claims_name_the_runtime_proof_and_exact_exemptions(path, resolver_label):
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    for requirement in (
        "fail-closed",
        resolver_label,
        "core.utils.ratelimit",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "`logout`",
        "`core:client_rename_warning`",
        "`admin:logout`",
    ):
        assert requirement in text, f"{path}: missing guard boundary {requirement!r}"
