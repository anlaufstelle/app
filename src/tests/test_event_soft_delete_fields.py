"""Guard + Verhalten: Ereignisse tragen Löschzeitpunkt UND Auslöser (Refs #2157).

Die Art.-15-Auskunft weist bei Fällen und Personen „gelöscht am/von" aus, bei
Ereignissen bisher nicht — ``Event`` trug nur ``is_deleted``. Mit #2157 stehen
``deleted_at``/``deleted_by`` auch am Ereignis.

Der **Scanner-Guard** hält die Zusage über alle künftigen Schreibpfade: wer
``is_deleted = True`` setzt, muss im selben Gültigkeitsbereich auch
``deleted_at`` und ``deleted_by`` setzen — sonst entstünde erneut ein
Papierkorb-Eintrag ohne Zeitpunkt und ohne Auslöser. Bulk-Pfade
(``.update(is_deleted=True)``) sind dieselbe Lücke in Massenform und deshalb
mit erfasst; heute existiert kein solcher Pfad, der Guard hält das fest.

Der Scanner ist über ``root=`` parametrisiert und legt **keine** Proben im
echten Baum ab: die Negativfälle laufen gegen ``tmp_path``.
"""

import ast
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Attachment, AuditLog, Event, LegalHold
from core.services.events.crud import LEGAL_HOLD_BLOCKS_DELETE_MESSAGE, soft_delete_event
from core.services.file_vault.storage import get_attachment_path
from tests.conftest import _make_attachment

CORE_ROOT = Path(__file__).resolve().parents[1] / "core"

#: Felder, die ein Soft-Delete-Schreibpfad zusammen mit ``is_deleted`` setzen muss.
REQUIRED_COMPANION_FIELDS = ("deleted_at", "deleted_by")


def _is_true(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _attribute_writes(scope: ast.AST) -> tuple[dict[str, int], dict[str, set[str]]]:
    """(``is_deleted = True``-Stellen, gesetzte Begleitfelder je Basisausdruck).

    Verschachtelte Funktionsdefinitionen bleiben Teil ihres äußeren Scopes —
    ein Helfer, der den Zeitstempel setzt, zählt damit für den Aufrufer mit.
    Das ist bewusst großzügig: der Guard soll fehlende Felder finden, nicht
    Stilfragen entscheiden.
    """
    deletions: dict[str, int] = {}
    companions: dict[str, set[str]] = {}
    for node in ast.walk(scope):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Attribute):
                continue
            base = ast.unparse(target.value)
            if target.attr == "is_deleted" and _is_true(node.value):
                deletions.setdefault(base, node.lineno)
            elif target.attr in REQUIRED_COMPANION_FIELDS:
                companions.setdefault(base, set()).add(target.attr)
    return deletions, companions


def _scopes(tree: ast.Module):
    """Modul plus jede Funktions-/Methodendefinition als eigener Gültigkeitsbereich."""
    yield tree
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _bulk_update_violations(tree: ast.Module, rel: str) -> list[str]:
    """``.update(is_deleted=True, ...)`` ohne ``deleted_at``/``deleted_by``."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "update":
            continue
        keywords = {kw.arg for kw in node.keywords if kw.arg}
        if not any(kw.arg == "is_deleted" and _is_true(kw.value) for kw in node.keywords):
            continue
        missing = [field for field in REQUIRED_COMPANION_FIELDS if field not in keywords]
        if missing:
            violations.append(f"{rel}:{node.lineno}: .update(is_deleted=True) ohne {', '.join(missing)}")
    return violations


def find_soft_deletes_without_metadata(root: Path) -> list[str]:
    """Soft-Delete-Schreibpfade unter ``root`` ohne ``deleted_at``/``deleted_by``.

    ``root`` ist injizierbar, damit der Gegenstands-Test gegen ``tmp_path``
    laufen kann; im Guard selbst ist ``src/core`` die SSOT.
    """
    violations: list[str] = []
    for py_file in sorted(root.rglob("*.py")):
        rel = py_file.relative_to(root).as_posix()
        tree = ast.parse(py_file.read_text())
        seen: set[tuple[str, int]] = set()
        for scope in _scopes(tree):
            deletions, companions = _attribute_writes(scope)
            for base, lineno in deletions.items():
                missing = [field for field in REQUIRED_COMPANION_FIELDS if field not in companions.get(base, set())]
                if not missing or (base, lineno) in seen:
                    continue
                seen.add((base, lineno))
                violations.append(f"{rel}:{lineno}: {base}.is_deleted = True ohne {', '.join(missing)}")
        violations.extend(_bulk_update_violations(tree, rel))
    return sorted(set(violations))


class TestSoftDeleteMetadataGuard:
    """Refs #2157 — kein Papierkorb-Eintrag ohne Zeitpunkt und Auslöser."""

    def test_core_has_no_soft_delete_without_metadata(self):
        violations = find_soft_deletes_without_metadata(CORE_ROOT)
        assert violations == [], (
            "Soft-Delete-Schreibpfade ohne Papierkorb-Metadaten: "
            + "; ".join(violations)
            + ". Die Art.-15-Auskunft weist Loeschzeitpunkt und Ausloeser aus — ein Pfad, der "
            "nur ``is_deleted`` setzt, hinterlässt einen Eintrag ohne beides."
        )

    def test_guard_flags_a_write_without_metadata(self, tmp_path):
        (tmp_path / "probe.py").write_text("def wipe(event):\n    event.is_deleted = True\n    event.save()\n")
        violations = find_soft_deletes_without_metadata(tmp_path)
        assert violations == ["probe.py:2: event.is_deleted = True ohne deleted_at, deleted_by"]

    def test_guard_accepts_a_complete_write(self, tmp_path):
        (tmp_path / "probe.py").write_text(
            "def wipe(event, user):\n"
            "    event.is_deleted = True\n"
            "    event.deleted_at = now()\n"
            "    event.deleted_by = user\n"
            "    event.save()\n"
        )
        assert find_soft_deletes_without_metadata(tmp_path) == []

    def test_guard_flags_a_partial_write(self, tmp_path):
        (tmp_path / "probe.py").write_text(
            "def wipe(event):\n    event.is_deleted = True\n    event.deleted_at = now()\n    event.save()\n"
        )
        assert find_soft_deletes_without_metadata(tmp_path) == ["probe.py:2: event.is_deleted = True ohne deleted_by"]

    def test_guard_flags_bulk_updates(self, tmp_path):
        (tmp_path / "probe.py").write_text("def wipe(qs):\n    qs.update(is_deleted=True)\n")
        violations = find_soft_deletes_without_metadata(tmp_path)
        assert violations == ["probe.py:2: .update(is_deleted=True) ohne deleted_at, deleted_by"]

    def test_guard_accepts_complete_bulk_updates(self, tmp_path):
        (tmp_path / "probe.py").write_text(
            "def wipe(qs, user):\n    qs.update(is_deleted=True, deleted_at=now(), deleted_by=user)\n"
        )
        assert find_soft_deletes_without_metadata(tmp_path) == []

    def test_guard_ignores_restore_and_filters(self, tmp_path):
        (tmp_path / "probe.py").write_text(
            "def restore(event):\n"
            "    event.is_deleted = False\n"
            "    event.save()\n"
            "\n"
            "def read(qs):\n"
            "    return qs.filter(is_deleted=True)\n"
        )
        assert find_soft_deletes_without_metadata(tmp_path) == []


@pytest.mark.django_db
class TestEventSoftDeleteFields:
    """Refs #2157 — der manuelle Löschpfad stempelt Zeitpunkt und Auslöser.

    Der Retention-Sweep (``deleted_by=None``, System-Löschung) ist in
    ``test_retention.py`` gepinnt, wo sein Kommando ohnehin läuft.
    """

    def test_fresh_event_carries_no_deletion_metadata(self, sample_event):
        assert sample_event.deleted_at is None
        assert sample_event.deleted_by is None

    def test_soft_delete_event_stamps_time_and_actor(self, sample_event, staff_user):
        before = timezone.now()
        soft_delete_event(sample_event, staff_user)

        sample_event.refresh_from_db()
        assert sample_event.is_deleted is True
        assert sample_event.deleted_at is not None
        assert sample_event.deleted_at >= before
        assert sample_event.deleted_by == staff_user

    def test_metadata_survives_the_update_fields_write(self, sample_event, staff_user):
        """``soft_delete_event`` schreibt bewusst mit ``update_fields`` (Merge-Race).

        Fehlten die beiden neuen Namen in der Liste, bliebe die Instanz im
        Speicher korrekt, die Zeile in der Datenbank aber leer — genau die
        Lücke, die #2157 schließt. Deshalb wird hier frisch aus der Datenbank
        gelesen statt die mutierte Instanz zu prüfen.
        """
        soft_delete_event(sample_event, staff_user)

        stored = Event.objects.get(pk=sample_event.pk)
        assert stored.deleted_at is not None
        assert stored.deleted_by_id == staff_user.pk


@pytest.mark.django_db
class TestCaseHoldBlocksTheManualDeletePath:
    """Refs #2197 (a): ein Hold auf den FALL hält auch den manuellen Klick an.

    ``_lock_event_and_recheck_hold`` (``core/retention/enforcement.py``) prüft
    den Fall-Zweig seit Refs #2122 unter der Sperre — der manuelle Pfad kannte
    nur Ereignis- und Personen-Holds. Damit durfte ein Klick mehr als der Cron:
    der Aufbewahrungslauf stellte das Ereignis eines gehaltenen Falls zurück,
    ``soft_delete_event`` leerte es und löschte seine Anhänge **physisch**
    (nicht rollback-bar) — die Hintertür um einen bewusst gesetzten
    Beweismittel-Hold (Spoliation, Muster Refs #1066).
    """

    def _hold_on(self, facility, admin_user, target_type, target_id):
        return LegalHold.objects.create(
            facility=facility,
            target_type=target_type,
            target_id=target_id,
            reason="Ermittlungsverfahren",
            created_by=admin_user,
        )

    def test_event_of_a_held_case_keeps_payload_audit_and_file(
        self, facility, case_open, case_event, staff_user, admin_user
    ):
        """Die drei Wirkungen, die ``soft_delete_event`` sonst entfaltet.

        Der Anhang wird eigens geprüft, weil er der einzige unumkehrbare Teil
        ist: ``delete_attachments_for`` entfernt die verschlüsselte Datei von
        der Platte, kein Papierkorb hält sie vor.
        """
        attachment = _make_attachment(facility, case_event, staff_user, ft_name="Anhang (Fall-Hold)")
        path = get_attachment_path(attachment)
        assert path.exists(), "Vorbedingung: die verschlüsselte Datei liegt auf der Platte"
        self._hold_on(facility, admin_user, "Case", case_open.pk)

        with pytest.raises(ValidationError) as exc_info:
            soft_delete_event(case_event, staff_user)

        assert exc_info.value.messages == [str(LEGAL_HOLD_BLOCKS_DELETE_MESSAGE)]
        case_event.refresh_from_db()
        assert case_event.is_deleted is False
        assert case_event.deleted_at is None
        assert case_event.data_json == {"dauer": 15, "notiz": "Testnotiz"}
        assert Attachment.objects.filter(pk=attachment.pk).exists()
        assert path.exists(), "der Anhang darf physisch nicht verschwinden"
        assert not AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_id=str(case_event.pk)).exists()

    def test_a_case_assigned_after_the_instance_was_read_still_blocks(
        self, facility, case_open, case_event, staff_user, admin_user
    ):
        """Cross-Review Runde 1, Befund 1: der Guard darf die Fallzuordnung nicht
        aus der übergebenen Instanz nehmen.

        ``_lock_event_row`` sperrte nur, ohne die Zeile zu lesen, und
        ``_assert_current_client_binding`` revalidierte allein den
        Personenbezug. Ein Ereignis, das zwischen dem Laden der Instanz und dem
        Löschen einem gehaltenen Fall **derselben Person** zugeordnet wurde,
        passierte damit beide Prüfungen — über den regulären Service
        ``assign_event_to_case``, ohne Bypass. Der Löschvorgang leerte die
        Nutzdaten und entfernte die Anhänge physisch; in der Datenbank blieb
        das geleerte Ereignis sogar am gehaltenen Fall hängen, weil
        ``update_fields`` die Zuordnung nicht zurückschreibt.
        """
        from core.services.case.cases import assign_event_to_case, remove_event_from_case

        remove_event_from_case(Event.objects.get(pk=case_event.pk), staff_user)
        case_event.refresh_from_db()
        assert case_event.case_id is None, "Vorbedingung: die gelesene Instanz kennt keinen Fall"
        # Ab hier ist ``case_event`` bewusst veraltet: in der Datenbank hängt das
        # Ereignis wieder am Fall, im Speicher nicht.
        assign_event_to_case(case_open, Event.objects.get(pk=case_event.pk), staff_user)
        attachment = _make_attachment(facility, case_event, staff_user, ft_name="Anhang (Stale-Fall)")
        path = get_attachment_path(attachment)
        self._hold_on(facility, admin_user, "Case", case_open.pk)

        with pytest.raises(ValidationError) as exc_info:
            soft_delete_event(case_event, staff_user)

        assert exc_info.value.messages == [str(LEGAL_HOLD_BLOCKS_DELETE_MESSAGE)]
        stored = Event.objects.get(pk=case_event.pk)
        assert stored.is_deleted is False
        assert stored.data_json == {"dauer": 15, "notiz": "Testnotiz"}
        assert stored.case_id == case_open.pk
        assert Attachment.objects.filter(pk=attachment.pk).exists()
        assert path.exists()
        assert not AuditLog.objects.filter(action=AuditLog.Action.DELETE, target_id=str(case_event.pk)).exists()

    def test_a_case_assigned_after_the_instance_was_read_does_not_block_without_a_hold(
        self, facility, case_open, case_closed, case_event, staff_user, admin_user
    ):
        """Gegenprobe zum Vorigen: die Lesung unter der Sperre darf nur den Hold
        durchsetzen, nicht jede zwischenzeitliche Umhängung abweisen — anders
        als beim Personenbezug, dessen Wechsel den Vorgang bewusst abbricht
        (dort war die falsche Personenzeile gesperrt)."""
        from core.services.case.cases import assign_event_to_case, remove_event_from_case, reopen_case

        remove_event_from_case(Event.objects.get(pk=case_event.pk), staff_user)
        case_event.refresh_from_db()
        reopen_case(case_closed, staff_user)
        assign_event_to_case(case_closed, Event.objects.get(pk=case_event.pk), staff_user)
        self._hold_on(facility, admin_user, "Case", case_open.pk)

        soft_delete_event(case_event, staff_user)

        stored = Event.objects.get(pk=case_event.pk)
        assert stored.is_deleted is True
        assert stored.case_id == case_closed.pk

    def test_hold_on_another_case_leaves_the_delete_path_open(
        self, facility, case_open, case_closed, case_event, staff_user, admin_user
    ):
        """Gegenprobe: der Guard darf nur den gehaltenen Fall anhalten."""
        self._hold_on(facility, admin_user, "Case", case_closed.pk)

        soft_delete_event(case_event, staff_user)

        case_event.refresh_from_db()
        assert case_event.is_deleted is True

    def test_event_without_a_case_is_unaffected(self, facility, sample_event, staff_user, admin_user):
        """Der Zweig fragt ``event.case_id`` zuerst — ein fallloses Ereignis
        darf nicht über einen zufällig gleich lautenden Hold stolpern."""
        self._hold_on(facility, admin_user, "Case", sample_event.pk)

        soft_delete_event(sample_event, staff_user)

        sample_event.refresh_from_db()
        assert sample_event.is_deleted is True
