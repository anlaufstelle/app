"""Unit-Tests fuer ``IdempotentCreateMixin`` und ``is_idempotency_conflict``.

Refs #1831: die 3x kopierte Guard-Sequenz (normalize → fingerprint →
Cache-Lookup → Hash-Vergleich → Mismatch-422) und der IntegrityError-
Replay-Fang mit dem Magic-String laufen im Mixin bzw. Prädikat zusammen.
Der Constraint-Name ``idem_key_per_user_uniq`` (Substring von
``event_idem_key_per_user_uniq``/``workitem_idem_key_per_user_uniq``)
existiert damit genau einmal — im Prädikat.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.db import IntegrityError
from django.test import RequestFactory

from core.models import Event
from core.services.events.idempotency import (
    FINGERPRINT_SCHEME,
    get_idempotent_result,
    payload_fingerprint,
    remember_idempotent_result,
)
from core.views._json_contracts import _idempotency_mismatch_response
from core.views.mixins import IdempotentCreateMixin, is_idempotency_conflict

CORE_DIR = Path(__file__).resolve().parents[1] / "core"

CONSTRAINT_SUBSTRING = "idem_key_per_user_uniq"

# Ein String-Literal, das den Constraint-Namen enthaelt. Beide
# Anfuehrungszeichen-Varianten (der Vorgaenger-Guard sah nur ``"`` und haette
# damit ausgerechnet die einfach gequoteten Migrations-Literale uebersehen)
# und bewusst zeilengebunden (``\n`` in der negierten Klasse) — sonst laeuft
# die Klasse ueber Zeilengrenzen zwischen zwei weit entfernten Quotes
# zusammen und verschluckt Prosa-Erwaehnungen in Docstrings.
_LITERAL_RE = re.compile(rf"""(['"])[^'"\n]*{CONSTRAINT_SUBSTRING}[^'"\n]*\1""")

# Dasselbe Literal, direkt gegen ``str(...)`` geprueft — der Rueckfall, den
# das Praedikat ``is_idempotency_conflict`` ueberfluessig macht.
_PREDICATE_CHECK_RE = re.compile(_LITERAL_RE.pattern + r"\s+in\s+str\(")

# Die EINE legitime Stelle: hier lebt der Name als Konstante, hier wird gegen
# ``str(exc)`` geprueft.
PREDICATE_HOME = "views/mixins.py"

# Zaehlergenaue Ausnahmeliste: WO unter ``src/core/`` der Constraint-Name als
# String-Literal stehen darf und WIE OFT. Eine neue Fundstelle (oder eine
# zusaetzliche in einer bekannten Datei) muss hier bewusst eingetragen werden.
ALLOWED_CONSTRAINT_LITERALS = {
    # Praedikat-Heimat (s.o.).
    PREDICATE_HOME: 1,
    # Constraint-DEFINITIONEN: hier entsteht der Name, geprueft wird nichts.
    "models/event.py": 1,
    "models/workitem.py": 1,
    "migrations/0098_event_idempotency_key_workitem_idempotency_key_and_more.py": 2,
}


class _RecordingView(IdempotentCreateMixin):
    """Minimaler Träger: Scope fix, Hit-Hook zeichnet auf."""

    idempotency_scope = "event_create"

    def __init__(self):
        self.hits = []

    def on_idempotent_hit(self, request, hit):
        self.hits.append(hit)
        return SimpleNamespace(status_code=200, hit=hit)


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def rf_post():
    def _make(**meta):
        rf = RequestFactory()
        request = rf.post("/x/", {"title": "t"}, **meta)
        request.user = SimpleNamespace(pk=7)
        return request

    return _make


class TestIsIdempotencyConflict:
    def test_event_constraint_matches(self):
        exc = IntegrityError('duplicate key value violates unique constraint "event_idem_key_per_user_uniq"')
        assert is_idempotency_conflict(exc) is True

    def test_workitem_constraint_matches(self):
        exc = IntegrityError('duplicate key value violates unique constraint "workitem_idem_key_per_user_uniq"')
        assert is_idempotency_conflict(exc) is True

    def test_foreign_constraint_does_not_match(self):
        exc = IntegrityError('duplicate key value violates unique constraint "core_client_pseudonym_uniq"')
        assert is_idempotency_conflict(exc) is False

    def test_non_integrity_error_does_not_match(self):
        assert is_idempotency_conflict(ValueError("idem_key_per_user_uniq")) is False

    def test_constraint_string_checked_only_in_predicate(self):
        """DoD: der Magic-String wird nur im Prädikat gegen ``str(exc)``
        geprüft — keine andere Stelle hält ihn als Literal-Prüfung.

        Gehärtet (DoD-Audit Refs #1831): der Scan lief nur über
        ``src/core/views/`` und nur auf doppelte Anführungszeichen. Ein
        Rückfall in einem Service (``'…uniq' in str(exc)``) war damit doppelt
        unsichtbar — falscher Ordner UND falsches Anführungszeichen. Jetzt
        deckt er ``src/core/**`` mit beiden Quote-Varianten ab; ausgenommen
        ist einzig die Prädikat-Heimat ``views/mixins.py``.
        """
        offenders = []
        for path in sorted(CORE_DIR.rglob("*.py")):
            rel = path.relative_to(CORE_DIR).as_posix()
            if rel == PREDICATE_HOME:
                continue
            text = path.read_text(encoding="utf-8")
            for match in _PREDICATE_CHECK_RE.finditer(text):
                line = text[: match.start()].count("\n") + 1
                offenders.append(f"{rel}:{line}")
        assert offenders == [], f"Direkte Constraint-String-Pruefung in: {offenders}"

    def test_constraint_literal_sites_are_allowlisted_exactly(self):
        """Zweite Linie: der Constraint-Name darf unter ``src/core/`` nur an
        den begründeten Stellen als String-Literal stehen — zählergenau.

        Der Prüf-Scan oben greift erst, wenn jemand das Literal auch schon
        gegen ``str(...)`` hält. Diese Inventur schlägt eine Stufe früher an:
        taucht der Name irgendwo neu auf, muss die Ausnahme hier bewusst
        eingetragen (und damit begründet) werden. Erlaubt sind heute die
        Prädikat-Konstante und die Constraint-DEFINITIONEN in Model und
        Migration — Prosa-Erwähnungen in Docstrings/Kommentaren zählen nicht
        mit, weil sie keine Anführungszeichen tragen.
        """
        found = {}
        for path in sorted(CORE_DIR.rglob("*.py")):
            count = len(_LITERAL_RE.findall(path.read_text(encoding="utf-8")))
            if count:
                found[path.relative_to(CORE_DIR).as_posix()] = count
        assert found == ALLOWED_CONSTRAINT_LITERALS, (
            f"Constraint-Literale weichen von der begruendeten Ausnahmeliste ab: {found}"
        )


class TestIdempotentCreateMixinReadIdempotency:
    def test_header_present_yields_key_and_hash(self, rf_post):
        request = rf_post(HTTP_X_IDEMPOTENCY_KEY="abc-123")
        view = _RecordingView()
        idem_key, idem_payload_hash = view.read_idempotency(request)
        assert idem_key == "abc-123"
        # Refs #1942: Der Fingerprint traegt seither das Schema-Praefix des
        # gepfefferten HMAC (vorher: nackter SHA-256-Hex, exakt 64 Zeichen) und
        # muss weiterhin in ``Event.idempotency_payload_hash`` passen.
        assert isinstance(idem_payload_hash, str)
        assert idem_payload_hash.startswith(f"{FINGERPRINT_SCHEME}:")
        assert len(idem_payload_hash) <= Event._meta.get_field("idempotency_payload_hash").max_length

    def test_no_header_yields_none_pair(self, rf_post):
        view = _RecordingView()
        assert view.read_idempotency(rf_post()) == (None, None)

    def test_invalid_header_degrades_to_none(self, rf_post):
        request = rf_post(HTTP_X_IDEMPOTENCY_KEY="ungueltig mit leerzeichen")
        view = _RecordingView()
        assert view.read_idempotency(request) == (None, None)


class TestIdempotentCreateMixinCheckReplay:
    def test_without_key_returns_none(self, rf_post):
        view = _RecordingView()
        assert view.check_replay(rf_post(), None) is None

    def test_without_cache_entry_returns_none(self, rf_post):
        request = rf_post(HTTP_X_IDEMPOTENCY_KEY="k1")
        view = _RecordingView()
        assert view.check_replay(request, "k1") is None

    def test_cache_hit_calls_hook(self, rf_post):
        # Fingerprint DES Replays — sonst greift der Payload-Vergleich.
        remember_idempotent_result("event_create", 7, "k1", "evt-pk", payload_hash=payload_fingerprint(rf_post()))
        request = rf_post(HTTP_X_IDEMPOTENCY_KEY="k1")
        view = _RecordingView()
        response = view.check_replay(request, "k1")
        assert view.hits and view.hits[0].pk == "evt-pk"
        assert response is not None and response.status_code == 200

    def test_payload_mismatch_returns_422(self, rf_post):
        # Refs #1942: gemerkt wird der Fingerprint eines ANDEREN Payloads —
        # der Replay bringt denselben Key, aber einen abweichenden Body. Zwei
        # Phantasie-Strings taugten dafuer nicht mehr: ohne Schema-Praefix
        # gelten sie als Legacy-Werte und damit bewusst NICHT als Kollision.
        anderer_body = payload_fingerprint(RequestFactory().post("/x/", {"title": "anders"}))
        assert anderer_body != payload_fingerprint(rf_post())
        remember_idempotent_result("event_create", 7, "k1", "evt-pk", payload_hash=anderer_body)
        request = rf_post(HTTP_X_IDEMPOTENCY_KEY="k1")
        view = _RecordingView()
        response = view.check_replay(request, "k1")
        assert response.status_code == 422
        assert not view.hits

    def test_legacy_hit_without_hash_passes_through(self, rf_post):
        # Legacy-Cache-Wert (nackter PK-String, vor N14): Treffer OHNE
        # Hash-Pruefung (Abwaertskompatibilitaet).
        cache.set("idem:event_create:7:k1", "legacy-pk", 60)
        request = rf_post(HTTP_X_IDEMPOTENCY_KEY="k1")
        view = _RecordingView()
        response = view.check_replay(request, "k1")
        assert view.hits and view.hits[0].pk == "legacy-pk" and view.hits[0].payload_hash is None
        assert response.status_code == 200


class TestIdempotentCreateMixinRemember:
    def test_remember_writes_cache_entry(self, rf_post):
        view = _RecordingView()
        view.remember_idempotency(rf_post(), "k9", "h9", "pk-9")
        hit = get_idempotent_result("event_create", 7, "k9")
        assert hit is not None and hit.pk == "pk-9" and hit.payload_hash == "h9"

    def test_remember_without_key_is_noop(self, rf_post):
        view = _RecordingView()
        view.remember_idempotency(rf_post(), None, None, "pk-x")
        assert get_idempotent_result("event_create", 7, None) is None


class TestMismatchResponseContract:
    def test_mismatch_response_is_422(self):
        assert _idempotency_mismatch_response().status_code == 422
