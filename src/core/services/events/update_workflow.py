"""Event update with attachments as one workflow (Refs #2569).

The update choreography — preparing the files, the lock order, the atomic
write of event and attachments and the cleanup of prepared files — lives here
instead of in the HTTP view. The view keeps building the form, filtering
sensitive fields, translating service errors into responses and confirming an
offline replay.

Guarantees:

* **Scan and encryption before the write transaction** (Refs #1345, #1956).
  Every replacement and added file is scanned and encrypted to disk BEFORE the
  ``transaction.atomic`` block below opens. The audit row of
  :func:`~core.services.events.crud.update_event` takes the facility's chain
  advisory lock until commit; a scan inside the block would hold that lock for
  up to ``CLAMAV_TIMEOUT`` per file.
* **Lock order person -> event -> attachment rows -> first audit write**
  (Refs #2260, #2359). :func:`lock_event_for_edit` locks the freshly read
  person and the event first and aligns the instance with that binding, so a
  person merge that landed after the view loaded the event cannot put a
  person lock behind the event. The attachment rows of the batch follow before
  ``update_event`` writes its audit row.
* **Event and attachments together or not at all** (Refs #584, #587, #622).
* **Every outcome without commit removes the prepared files**; files of
  persisted attachments stay (Refs #662, #1345). A failing scan removes the
  files of the same submit itself (``stage_attachment_uploads``).
* **Quota and security audit rows stay durable** (Refs #2178, #524). The quota
  boundary wraps the transaction, so the ``ATTACHMENT_QUOTA_EXCEEDED`` row is
  written after the rollback; a security rejection is audited while preparing,
  outside the transaction.

The caller must not hold an open transaction: only then is the block below the
outermost one, its end a real commit, and the cleanup decision final.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from core.services.attachments import (
    lock_attachment_batch_before_audit,
    quota_backstop_audit_boundary,
    stage_attachment_uploads,
)
from core.services.events.crud import apply_attachment_changes, lock_event_for_edit, update_event


@dataclass(frozen=True)
class EventUpdateInput:
    """The explicit payload of one event update — no request object.

    ``data_json`` is the future content including restricted values and the
    existing file markers (``merge_update_payload``); the same value decides
    which files are prepared and which rows are locked, so both sides resolve
    replacements identically.

    ``file_fields`` holds the added uploads per FILE slug (the cleaned form
    data), ``uploads`` the submitted files by form key — replacements sit under
    ``replace_upload_key``. ``removals`` maps ``<slug>__remove`` to the
    comma-separated entry ids to remove.

    ``expected_updated_at``/``expected_form_state`` and
    ``require_version_token`` are handed to ``update_event`` unchanged.
    """

    data_json: dict
    file_fields: Mapping[str, Any] = field(default_factory=dict)
    uploads: Mapping[str, Any] = field(default_factory=dict)
    removals: Mapping[str, str] = field(default_factory=dict)
    expected_updated_at: str | None = None
    expected_form_state: str | None = None
    require_version_token: bool = False


def removal_actions(form_data: Mapping[str, Any]) -> dict[str, str]:
    """The ``<slug>__remove`` entries of a submitted form — the only form keys the batch reads."""
    return {key: form_data.get(key) or "" for key in form_data if key.endswith("__remove")}


def update_event_with_attachments(event, user, update: EventUpdateInput):
    """Save the event and its attachment changes atomically; returns ``event``.

    Raises the ``ValidationError`` of the version check, the person and event
    gates, the file policy or the quota backstop unchanged — translating them
    is the caller's job. Any exception leaves neither database changes nor
    prepared files behind.
    """
    document_type = event.document_type
    committed = False
    staged_all: list = []
    try:
        staged_by_key, staged_all = stage_attachment_uploads(
            event,
            user,
            update.uploads,
            update.file_fields,
            document_type,
            data_json=update.data_json,
        )
        # Refs #2178: the boundary sits AROUND the transaction — a quota
        # backstop rejected at commit rolls the whole update back, and the
        # audit row is written only after that rollback.
        with quota_backstop_audit_boundary():
            with transaction.atomic():
                # Refs #2359: person -> event with the freshly read binding is
                # the FIRST lock; the attachment pre-lock below then finds both
                # rows held.
                lock_event_for_edit(event)
                # Refs #2260: the rows of this batch are locked before the
                # first audit write takes the facility's chain lock.
                lock_attachment_batch_before_audit(
                    event,
                    user,
                    update.removals,
                    staged_by_key,
                    document_type,
                    data_json=update.data_json,
                )
                update_event(
                    event,
                    user,
                    update.data_json,
                    expected_updated_at=update.expected_updated_at,
                    expected_form_state=update.expected_form_state,
                    require_version_token=update.require_version_token,
                )
                apply_attachment_changes(
                    event,
                    user,
                    update.removals,
                    update.uploads,
                    update.file_fields,
                    document_type,
                    staged_by_key=staged_by_key,
                )
            committed = True
    finally:
        # Refs #1345/#1956: without a commit the prepared files are orphans.
        if not committed:
            for staged in staged_all:
                staged.output_path.unlink(missing_ok=True)
    return event
