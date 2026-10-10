"""Guard: every offline recovery promise uses an approved wording (Refs #2426).

Locked offline ciphertext only becomes readable again while both key
derivation inputs are unchanged (``deriveSessionKey(password, saltB64Url)``
in ``src/static/js/crypto.js``). Instead of parsing the wording, every
sentence of the checked documents that mentions readability or
decryptability must equal one entry of ``APPROVED_RECOVERY_SENTENCES``
after whitespace and Markdown emphasis are normalised. Any other sentence
with a trigger word fails with its ``file:line``; a new wording may only be
added to the allowlist after checking it against the code.
"""

import re
from pathlib import Path

import pytest

from core.services.client import render_document

REPO_ROOT = Path(__file__).resolve().parents[2]
TOMS = REPO_ROOT / "src" / "core" / "dsgvo_templates" / "toms.md"
DSFA = REPO_ROOT / "src" / "core" / "dsgvo_templates" / "dsfa.md"
VVT = REPO_ROOT / "src" / "core" / "services" / "compliance" / "vvt.py"
DOMAIN_CONCEPT_DE = REPO_ROOT / "docs" / "fachkonzept-anlaufstelle.md"
DOMAIN_SUMMARY_EN = REPO_ROOT / "docs" / "en" / "domain-concept-summary.md"
RECOVERY_CLAIM_FILES = (TOMS, DSFA, VVT, DOMAIN_CONCEPT_DE, DOMAIN_SUMMARY_EN)

#: Deliberately broad: any readability or decryptability wording counts as a
#: recovery claim. Compound words ("maschinenlesbar", "machine-readable",
#: "unlesbar") are excluded by the look-behind.
RECOVERY_TRIGGER_RE = re.compile(
    r"(?<![\w-])(?:lesbar|readable)|entschlüsselbar|decryptable"
    r"|wieder (?:zugänglich|verfügbar|entschlüssel)|(?:accessible|available|decrypted) again",
    re.IGNORECASE,
)

#: Paragraph breaks, Markdown block starts and sentence ends.
_SEGMENT_BOUNDARY_RE = re.compile(r"\n\s*\n|\n(?=\s*(?:[-*+] |#|\||\d+\. |>))|(?<=[.!?])\s+")
_EMPHASIS_RE = re.compile(r"\*+|__")

#: The shipped recovery sentences, each checked against ``crypto.js``: both
#: derivation inputs unchanged, the salt rotation triggers, and nothing that
#: promises readability under any other condition.
APPROVED_RECOVERY_SENTENCES = frozenset(
    {
        # TOM (src/core/dsgvo_templates/toms.md)
        "Nach Ablauf der Leerlaufzeit löscht die Anwendung beim nächsten Ausführen den Schlüssel; ungesendete "
        "Arbeit bleibt verschlüsselt erhalten und wird nach erneuter Anmeldung nur wieder lesbar, wenn Passwort "
        "und Offline-Salt des Kontos unverändert sind; den Salt erneuert der Server bei Rollenwechsel, "
        "Deaktivierung und einer Anmeldung mit korrektem Passwort an einem gesperrten Konto, eine Passwortänderung "
        "(auch das Zurücksetzen) ändert die Schlüsselableitung ohnehin — danach ist der alte Bestand nicht mehr "
        "entschlüsselbar (zwischengespeicherte Daten werden verworfen, ungesendete Einträge als nicht zustellbar "
        "angezeigt).",
        # DSFA (src/core/dsgvo_templates/dsfa.md)
        "Nach Ablauf der Leerlaufzeit (Sitzungsdauer, Default 1800 s, einrichtungskonfigurierbar) greift die "
        "Leerlaufprüfung — aber nur, solange oder sobald eine Seite der Anwendung im Browser läuft (beim Laden, "
        "im 60-Sekunden-Intervall, bei Rückkehr in den Tab, tab-übergreifend): Liegt ungesendete Arbeit vor, wird "
        "nur der Schlüssel gelöscht (Sperre); der verschlüsselte Bestand bleibt erhalten und wird nach erneuter "
        "Anmeldung nur wieder lesbar, wenn Passwort und Offline-Salt des Kontos unverändert sind.",
        # Absolute offline key lifetime (toms.md and dsfa.md; crypto.js derives the same key from the
        # unchanged password and salt, otherwise decryption fails on the GCM tag)
        "Am Ablauf entzieht sie den Schlüssel und zeigt „Offline-Schlüssel abgelaufen — bitte online anmelden“; "
        "ungesendete Arbeit bleibt verschlüsselt erhalten und wird nach erneutem Online-Login mit unverändertem "
        "Passwort und Salt wieder lesbar.",
        # Domain concept (docs/fachkonzept-anlaufstelle.md)
        "Ist die Leerlaufzeit überschritten und liegt ungesendete Arbeit vor, verwirft sie nur den Schlüssel "
        "(Sperre): der verschlüsselte Bestand bleibt erhalten und wird nach erneutem Login nur wieder lesbar, wenn "
        "Passwort und Offline-Salt des Kontos unverändert sind.",
        # English summary (docs/en/domain-concept-summary.md)
        "With unsynced work it removes only the key and keeps the ciphertext, which becomes readable again after "
        "the next login only if password and offline salt are unchanged -- the server rotates the salt on role "
        "change, on deactivation and on a correct-password login against a locked account, after which cached rows "
        "are discarded and unsynced entries show as undeliverable.",
    }
)


def _normalise(text: str) -> str:
    return " ".join(_EMPHASIS_RE.sub("", text).split())


def _recovery_claim_sentences(text: str) -> list[tuple[int, str]]:
    """``(line, normalised sentence)`` for every segment with a trigger word."""
    claims = []
    start = 0
    for boundary in [*_SEGMENT_BOUNDARY_RE.finditer(text), None]:
        end = boundary.start() if boundary else len(text)
        sentence = _normalise(text[start:end])
        if RECOVERY_TRIGGER_RE.search(sentence):
            claims.append((text.count("\n", 0, start) + 1, sentence))
        if boundary:
            start = boundary.end()
    return claims


def _unapproved_recovery_claims(label: str, text: str) -> list[str]:
    return [
        f"{label}:{line}: recovery claim with unapproved wording; add a new wording to "
        f"APPROVED_RECOVERY_SENTENCES only after checking it against crypto.js: {sentence}"
        for line, sentence in _recovery_claim_sentences(text)
        if sentence not in APPROVED_RECOVERY_SENTENCES
    ]


def _label(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


@pytest.mark.architecture
class TestShippedRecoveryClaims:
    def test_every_recovery_claim_in_the_sources_is_approved(self):
        violations = [
            violation
            for path in RECOVERY_CLAIM_FILES
            for violation in _unapproved_recovery_claims(_label(path), path.read_text(encoding="utf-8"))
        ]
        assert not violations, "\n".join(violations)

    def test_every_approved_sentence_is_found_by_the_trigger(self):
        found = {
            sentence
            for path in RECOVERY_CLAIM_FILES
            for _, sentence in _recovery_claim_sentences(path.read_text(encoding="utf-8"))
        }
        missing = APPROVED_RECOVERY_SENTENCES - found
        assert not missing, missing

    @pytest.mark.parametrize("path", [TOMS, DSFA, DOMAIN_CONCEPT_DE, DOMAIN_SUMMARY_EN], ids=_label)
    def test_each_document_carries_a_recovery_claim(self, path):
        assert _recovery_claim_sentences(path.read_text(encoding="utf-8")), _label(path)


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["toms", "dsfa"])
def test_rendered_templates_carry_only_approved_recovery_claims(facility, slug):
    rendered = render_document(slug, facility)[0]
    assert _recovery_claim_sentences(rendered), slug
    violations = _unapproved_recovery_claims(f"{slug} (rendered)", rendered)
    assert not violations, "\n".join(violations)


CONDITION_DE = "wenn Passwort und Offline-Salt des Kontos unverändert sind"
CONDITION_EN = "only if password and offline salt are unchanged"

#: ``(file, original, replacement)`` for every rejected variant of the three
#: review rounds, applied to the shipped text.
FALSE_VARIANTS = {
    "en-salt-only": (DOMAIN_SUMMARY_EN, CONDITION_EN, "only if offline salt is unchanged"),
    "en-login-password-before-condition": (
        DOMAIN_SUMMARY_EN,
        "after the next login " + CONDITION_EN,
        "after the next correct-password login only if offline salt is unchanged",
    ),
    "en-alternative-login": (DOMAIN_SUMMARY_EN, CONDITION_EN, CONDITION_EN + " or the next login succeeds"),
    "en-second-promise": (
        DOMAIN_SUMMARY_EN,
        CONDITION_EN,
        CONDITION_EN + ", and after a password change it also becomes readable again",
    ),
    "en-unrelated-condition": (
        DOMAIN_SUMMARY_EN,
        CONDITION_EN,
        "and the app displays a notice only if password and offline salt are unchanged",
    ),
    "en-second-promise-in-next-sentence": (
        DOMAIN_SUMMARY_EN,
        "show as undeliverable.",
        "show as undeliverable. After a password change they become readable again.",
    ),
    **{
        f"{path.stem}-{case}": (path, CONDITION_DE, replacement)
        for path in (TOMS, DSFA, DOMAIN_CONCEPT_DE)
        for case, replacement in {
            "alternative-login": CONDITION_DE + " oder eine erneute Anmeldung erfolgreich ist",
            "second-promise": CONDITION_DE + ", und nach einer Passwortänderung wird er ebenfalls wieder lesbar",
            "unrelated-condition": "und die Anwendung zeigt einen Hinweis, " + CONDITION_DE,
            "salt-only": "wenn der Offline-Salt unverändert ist",
            "negated-condition": "wenn Passwort und Offline-Salt des Kontos nicht unverändert sind",
        }.items()
    },
}


@pytest.mark.architecture
@pytest.mark.parametrize("case", sorted(FALSE_VARIANTS))
def test_false_variant_of_a_shipped_claim_is_rejected(case):
    path, original, replacement = FALSE_VARIANTS[case]
    text = path.read_text(encoding="utf-8")
    assert text.count(original) == 1, (case, original)
    altered = text.replace(original, replacement)

    violations = _unapproved_recovery_claims(_label(path), altered)

    line = text.count("\n", 0, text.index(original)) + 1
    assert any(violation.startswith(f"{_label(path)}:{line}: ") for violation in violations), (case, violations)


@pytest.mark.parametrize(
    "sentence",
    [
        "Der Bestand wird nach erneuter Anmeldung nur wieder lesbar, wenn der Offline-Salt unverändert ist.",
        "Der Bestand wird nach erneuter Anmeldung nur wieder lesbar, wenn das Passwort unverändert ist.",
        "Der Bestand wird mit Passwort und Offline-Salt wieder lesbar.",
        "Er wird nach einer Anmeldung mit korrektem Passwort nur wieder lesbar, wenn der Offline-Salt unverändert ist.",
        "Er wird nur wieder lesbar, wenn Passwort oder Offline-Salt unverändert ist.",
        "Er wird nur wieder lesbar, wenn das Passwort unverändert ist und der Offline-Salt neu erzeugt wurde.",
        "Er wird wieder lesbar. Das gilt, wenn Passwort und Offline-Salt unverändert sind.",
        "Er wird wieder lesbar, außer wenn Passwort und Offline-Salt unverändert sind.",
        "Er wird **wieder lesbar**, wenn Passwort und Offline-Salt des Kontos unverändert sind.",
        "Nach erneuter Anmeldung ist der Bestand wieder zugänglich.",
        "Der alte Bestand bleibt entschlüsselbar.",
        "The ciphertext becomes readable again only if password is unchanged.",
        "The ciphertext becomes readable again unless password and offline salt are unchanged.",
        "The ciphertext becomes readable again -- logout keeps it only if password and offline salt are unchanged.",
        "Cached rows stay decryptable after a password change.",
    ],
)
def test_any_other_recovery_wording_is_rejected(sentence):
    assert _unapproved_recovery_claims("probe", sentence), sentence


@pytest.mark.parametrize("sentence", sorted(APPROVED_RECOVERY_SENTENCES))
def test_approved_sentence_passes_with_line_breaks_and_emphasis(sentence):
    wrapped = sentence.replace(" nur wieder lesbar", "\nnur **wieder lesbar**").replace(
        " readable again", "\nreadable *again*"
    )
    assert _recovery_claim_sentences(wrapped)
    assert not _unapproved_recovery_claims("probe", wrapped)


def test_violation_names_file_and_line():
    text = "# Titel\n\nEin Satz.\nDer Bestand wird wieder lesbar.\n"
    assert _unapproved_recovery_claims("docs/x.md", text) == [
        "docs/x.md:4: recovery claim with unapproved wording; add a new wording to "
        "APPROVED_RECOVERY_SENTENCES only after checking it against crypto.js: Der Bestand wird wieder lesbar."
    ]
