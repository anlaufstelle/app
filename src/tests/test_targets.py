"""Vertrag für typgekoppelte UUID-Zielfilter (Refs #2156)."""

import re
import uuid
from collections import Counter
from pathlib import Path

import pytest
from django.db.models import Q

from core.services.targets import TARGET_ACTIVITY, TARGET_CASE, TARGET_CLIENT, TARGET_EVENT, target_q

SERVICES_ROOT = Path(__file__).resolve().parents[1] / "core" / "services"
_TARGET_PAIR_RE = re.compile(
    r"target_type\s*=\s*(?P<target>[^,\n]+),(?:(?!target_id\s*=)[\s\S]){0,180}?target_id__in\s*="
)


def _handwritten_target_filters(root: Path) -> Counter[tuple[str, str]]:
    """Grep-artiges Inventar gekoppelter Zielfilter außerhalb des Helfers."""
    matches: Counter[tuple[str, str]] = Counter()
    for path in sorted(root.rglob("*.py")):
        if path == root / "targets.py":
            continue
        text = path.read_text(encoding="utf-8")
        for match in _TARGET_PAIR_RE.finditer(text):
            matches[(str(path.relative_to(root)), match.group("target").strip())] += 1
    return matches


@pytest.mark.parametrize("target_type", [TARGET_ACTIVITY, TARGET_CASE, TARGET_CLIENT, TARGET_EVENT])
def test_target_q_builds_a_coupled_type_and_uuid_filter(target_type):
    target_id = uuid.uuid4()

    assert target_q(target_type, (item for item in [target_id])) == Q(
        target_type=target_type,
        target_id__in=(target_id,),
    )


def test_target_q_rejects_unknown_target_type():
    with pytest.raises(ValueError, match="Unzulässiger Zieltyp: Unknown"):
        target_q("Unknown", [uuid.uuid4()])


@pytest.mark.parametrize("target_ids", ["not-an-iterable-of-uuids", ["not-a-uuid"], [uuid.uuid4(), None]])
def test_target_q_rejects_non_uuid_target_ids(target_ids):
    with pytest.raises(ValueError, match="target_ids muss ausschließlich UUID-Werte enthalten"):
        target_q(TARGET_EVENT, target_ids)


@pytest.mark.architecture  # scannt src/core/**/*.py — in mutmut-Runs deselektiert (Mutanten-Varianten im Quelltext)
def test_handwritten_target_filter_inventory_has_no_new_entries():
    """Neue CharField-Zielfilter müssen Typ und IDs über ``target_q`` koppeln."""
    assert _handwritten_target_filters(SERVICES_ROOT) == Counter(
        {
            ("case/deletion.py", "DeletionRequest.TargetType.CASE"): 1,
            ("client/main.py", '"Event"'): 1,
            ("client/main.py", "DeletionRequest.TargetType.CASE"): 1,
            # Einziger erlaubter Fall im bearbeiteten Helfer: Activity nutzt
            # einen echten ContentType-FK, nicht das freie CharField-Triple.
            ("client/main.py", "ContentType.objects.get_for_model(Event)"): 1,
            ("client/main.py", "DeletionRequest.TargetType.CLIENT"): 2,
            # Bestehende zentrale Zielmengen-Builder; eigener Althelfer für
            # Art.-15/Art.-17-Parität, nicht die hier gehärteten Modellfilter.
            ("client/targets.py", '"Event"'): 1,
            ("client/targets.py", "ContentType.objects.get_for_model(Event)"): 1,
            ("client/targets.py", "ContentType.objects.get_for_model(WorkItem)"): 1,
            ("client/targets.py", "ContentType.objects.get_for_model(Case)"): 1,
            ("client/targets.py", '"Case"'): 1,
            ("client/targets.py", '"Activity"'): 1,
        }
    )


def test_handwritten_target_filter_scanner_uses_injected_root(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(
        'rows.filter(target_type="Event", target_id__in=target_ids)\n',
        encoding="utf-8",
    )

    assert _handwritten_target_filters(tmp_path) == Counter({("probe.py", '"Event"'): 1})
