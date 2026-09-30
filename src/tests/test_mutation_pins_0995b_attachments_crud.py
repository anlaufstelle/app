"""Pins for the lock order of the foreign key pre-lock of an attachment batch (``core.services.attachments.crud``).

Source: mutation delta of the checkpoint release-0.99.5, 1 mutant.
Group:

* ``_new_row_fk_targets`` — the union of the targets of all new rows is
  sorted by table only; within one table (several field templates of one
  batch) the ``FOR KEY SHARE`` locks follow the iteration order of a set
  instead of the primary key.

Two batches with overlapping field templates must take the shared rows in the
same order (table, then primary key), otherwise they can block each other
crosswise. ``lock_fk_targets`` locks in the order it is handed and does not
sort itself, so the order of the target list is the lock order.

The iteration order of a set is unspecified; for the target tuples it even
changes with the string hash seed of the process. The pin therefore replaces
``set`` in the service module by a set that iterates its members in
descending order, so the input of the sort always holds the field templates
against key order, and checks as a precondition that the service collected
the targets through it.

The pin observes the executed ``FOR KEY SHARE`` statements on the field
template table (``tests._row_lock_helpers``: PostgreSQL plans the captured
statement; what counts is the locked table, the lock strength and the filter
values, not the literal form). A statement may lock one key or several; for
several keys the order in which the row lock receives them is read from the
plan (a sort or a btree index scan on the key column below the row lock). The
number of statements is not part of the contract: one statement per key,
one statement for all keys and redundant repeated locks are all accepted.
"""

import json
import re

import pytest
from django.db import connection, transaction

from core.models import FieldTemplate
from core.services.attachments import crud
from core.services.attachments.crud import lock_batch_attachment_rows
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

FIELD_TEMPLATE_TABLE = "core_fieldtemplate"
KEY_COLUMN = "id"
TEMPLATE_COUNT = 10

_IDENTIFIER = r'"(?:[^"]|"")+"|[^."\s()]+'
_SORT_KEY = re.compile(
    rf"^\(?(?P<alias>{_IDENTIFIER})\.(?P<column>{_IDENTIFIER})\)?(?P<desc>\s+DESC)?(?:\s+NULLS\s+(?:FIRST|LAST))?$"
)
_ORDERING_SCANS = ("Index Scan", "Index Only Scan")


@pytest.fixture
def file_templates(facility):
    return [
        FieldTemplate.objects.create(
            facility=facility,
            name=f"Attachment field {index}",
            field_type=FieldTemplate.FieldType.FILE,
        )
        for index in range(TEMPLATE_COUNT)
    ]


@pytest.fixture
def descending_sets(monkeypatch):
    """Replaces ``set`` in the service module by a set iterating in descending order.

    Returns the list of instances the service created while the fixture is active.
    """
    created = []

    class DescendingSet(set):
        def __init__(self, *args):
            super().__init__(*args)
            created.append(self)

        def __iter__(self):
            return iter(sorted(super().__iter__(), key=str, reverse=True))

    monkeypatch.setattr(crud, "set", DescendingSet, raising=False)
    return created


def _unquote(identifier):
    return identifier[1:-1].replace('""', '"') if identifier.startswith('"') else identifier


def _explain(sql, params):
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN (VERBOSE, FORMAT JSON) " + sql, params)
        (plan,) = cursor.fetchone()
    plan = json.loads(plan) if isinstance(plan, str) else plan
    return plan[0]["Plan"]


def _path_to_scan(node, table):
    """The plan nodes from ``node`` down to the scan of ``table``."""
    if node.get("Relation Name") == table:
        return [node]
    for child in node.get("Plans", ()):
        path = _path_to_scan(child, table)
        if path:
            return [node, *path]
    return []


def _btree_leading_column(index_name):
    """Leading column of a btree index and whether it is stored descending."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT a.attname, (i.indoption[0] & 1) = 1 FROM pg_index i"
            " JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_am am ON am.oid = c.relam"
            " JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0]"
            " WHERE i.indexrelid = to_regclass(quote_ident(%s)) AND am.amname = 'btree'",
            [index_name],
        )
        return cursor.fetchone()


def _key_order_below_lock(lock, table):
    """``"ASC"``/``"DESC"`` if the plan hands the rows of ``table`` to the row lock ordered by key, else ``None``."""
    path = _path_to_scan(_explain(lock.abfrage.sql, lock.abfrage.params), table)
    lock_nodes = [index for index, node in enumerate(path) if node.get("Node Type") == "LockRows"]
    if not lock_nodes:
        return None
    scan = path[-1]
    alias = scan.get("Alias", table)
    for node in path[lock_nodes[0] + 1 :]:
        if node.get("Node Type") in ("Sort", "Incremental Sort"):
            match = _SORT_KEY.match(node["Sort Key"][0])
            if match and _unquote(match["alias"]) == alias and _unquote(match["column"]) == KEY_COLUMN:
                return "DESC" if match["desc"] else "ASC"
            return None
        if node is scan:
            leading = _btree_leading_column(node["Index Name"]) if node.get("Node Type") in _ORDERING_SCANS else None
            if leading is None or leading[0] != KEY_COLUMN:
                return None
            return "DESC" if (node["Scan Direction"] == "Backward") != leading[1] else "ASC"
        if len(node.get("Plans", ())) > 1:
            return None
    return None


def _first_lock_order(locks, templates):
    """The templates in the order of their first observed lock.

    Each statement contributes the keys it filters on, in the order in which
    its row lock receives them. A statement for several keys without a key
    order below the row lock fails, as do keys outside ``templates``.
    """
    by_key = {template.pk.hex: template for template in templates}
    order = []
    for lock in locks:
        keys = lock.abfrage.bedingungen.get(lock.tabelle, {}).get(KEY_COLUMN, set())
        assert keys and keys <= set(by_key), lock
        if len(keys) > 1:
            direction = _key_order_below_lock(lock, lock.tabelle)
            assert direction is not None, f"multi-row lock without key order: {lock.abfrage.sql}"
            keys = sorted(keys, reverse=direction == "DESC")
        for key in keys:
            if by_key[key] not in order:
                order.append(by_key[key])
    return order


class TestNewRowFkTargets:
    """``_new_row_fk_targets`` as ``lock_batch_attachment_rows`` uses it for new rows."""

    def test_field_templates_are_locked_in_key_order(self, sample_event, staff_user, file_templates, descending_sets):
        """x__new_row_fk_targets__mutmut_20: the sort key loses the primary
        key, so the field template rows were locked in set iteration order
        instead of ascending key order."""
        handed = sorted(file_templates, key=lambda template: str(template.pk), reverse=True)
        with transaction.atomic(), mitschnitt() as queries:
            lock_batch_attachment_rows(sample_event, [], creates_rows=True, user=staff_user, field_templates=handed)
        template_targets = {(FIELD_TEMPLATE_TABLE, KEY_COLUMN, template.pk) for template in file_templates}
        collected = [targets for targets in descending_sets if template_targets <= targets]
        assert collected, "the service no longer collects the targets in a set; the counter order is not enforced"
        iterated = [entry[2] for entry in collected[0] if entry[0] == FIELD_TEMPLATE_TABLE]
        assert iterated == sorted(iterated, key=str, reverse=True)
        locks = queries.sperren(FIELD_TEMPLATE_TABLE)
        assert all(lock.sperrart == "KEY SHARE" for lock in locks), locks
        order = _first_lock_order(locks, file_templates)
        assert set(order) == set(file_templates)
        assert [str(t.pk) for t in order] == sorted(str(t.pk) for t in file_templates)


class TestLockOrderObservation:
    """The lock order observed for one statement that locks several field templates."""

    @pytest.mark.parametrize("planner_setting", [None, "enable_seqscan", "enable_sort"])
    @pytest.mark.parametrize("direction", ["ASC", "DESC"])
    def test_batch_lock_is_observed_in_the_order_it_locks(self, file_templates, planner_setting, direction):
        """A single ``IN`` statement with ``ORDER BY`` on the key is read in
        the order in which PostgreSQL locks and returns its rows, with a sort
        or an index scan below the row lock."""
        keys = [template.pk for template in file_templates]
        placeholders = ", ".join(["%s"] * len(keys))
        sql = (
            f'SELECT "{KEY_COLUMN}" FROM "{FIELD_TEMPLATE_TABLE}" WHERE "{KEY_COLUMN}" IN ({placeholders})'
            f' ORDER BY "{KEY_COLUMN}" {direction} FOR KEY SHARE'
        )
        with transaction.atomic(), connection.cursor() as cursor:
            if planner_setting:
                cursor.execute(f"SET LOCAL {planner_setting} = off")
            with mitschnitt() as queries:
                cursor.execute(sql, keys)
                returned = [row[0] for row in cursor.fetchall()]
            locks = queries.sperren(FIELD_TEMPLATE_TABLE)
            assert [lock.sperrart for lock in locks] == ["KEY SHARE"]
            order = _first_lock_order(locks, file_templates)
        assert [template.pk for template in order] == returned
        assert returned == sorted(keys, key=str, reverse=direction == "DESC")
