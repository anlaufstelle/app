"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.file_vault.audit`` und ``core.services.file_vault.quota``
(Batch ``file_vault-2``).

Refs #2174.
Refs #2193.

Quelle: Vollmutations-Lauf auf main@5216f473 (frischer Mutantenbaum), Batch
``file_vault-2`` (23 Survivor-Mutanten in zwei Modulen: ``audit.py``
``_extension`` 4, ``_owner_reference`` 3, ``audit_attachment_upload`` 3,
``audit_attachment_quota_exceeded`` 6; ``quota.py`` ``storage_warning`` 3,
``_configured_limit_bytes`` 4). Batch ``file_vault-1`` (dieselbe Dateifamilie,
andere Module) gehoert einem anderen Batch-Agenten und wird hier NICHT
angefasst.

Vier Mutationsachsen in diesem Batch:

* **Defensive ``getattr``-Defaults entfernt** in ``_owner_reference``
  (``getattr(obj, name, None)`` -> ``getattr(obj, name, )``): laut Docstring
  ist die Funktion bewusst defensiv, damit ein ``AttributeError`` beim
  Ermitteln der Owner-Metadaten nicht die forensische Spur eines
  abgewiesenen Uploads zerstoert. Gepinnt mit minimalen Stub-Objekten, denen
  genau das erwartete Attribut fehlt — Original liefert ``(None, None)``/
  Teil-``None``, die Mutante wirft.
* **Audit-Inhalt exakt falsch** in ``audit_attachment_upload`` (MIME-
  Fallback per ``or``/``and``-Praezedenz bzw. Text-/Gross-Klein-Variante) und
  in ``audit_attachment_quota_exceeded`` (Byte-MB-Divisor bzw. Rundungs-
  Nachkommastellen): die Klassifikationsregel "Audit-Inhalt/-Scope und
  Grenzwerte exakt pinnen" — Klasse ``test``, Prio 1, exakte Assertion auf
  den geschriebenen ``detail``-Wert.
* **Meldungs-Wortlaut** in ``storage_warning`` (XX-Marker-/Gross-Klein-
  Varianten desselben Satzes): die Klassifikationsregel fuer Meldungen
  — Klasse ``test``, Prio 3, exakter String-Vergleich des vollstaendigen
  Warnsatzes toetet alle drei Varianten mit einer Assertion.
* **Inline-Default eines Settings-Lesers** in ``_configured_limit_bytes``
  (``getattr(django_settings, "FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB", 0)``):
  Regel 4 der bindenden Klassifikation — Klasse ``test``, Prio 3, gepinnt
  per ``monkeypatch.delattr(django_settings._wrapped, name, raising=False)``
  (Muster ``test_mutation_pins_099_breach_detection_a.py::_delete_setting``).

Vier weitere Survivor sind ``equivalent`` (Diff + Begruendung in
``triage-099-file_vault-2.json``, gegen die fertige Pin-Datei unter
``MUTANT_UNDER_TEST`` nachgefahren, siehe ``killmap.tsv``): zwei
``_extension``-``rsplit``-Varianten (Index ``[-1]`` liefert bei mindestens
einem Punkt unabhaengig von ``maxsplit``/Index-Vorzeichen denselben Rest
nach dem letzten Punkt) und ein Ersatzwert-Mutant derselben Funktion (der
Ersatzwert wird nur auf Punkt-Enthaltensein geprueft), sowie zwei
``_configured_limit_bytes``-Defaults, die durch die nachgelagerte
``... or None``-Verarbeitung bzw. die falsy-Filterung in der
Kandidatenliste beobachtungsneutral bleiben.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.conf import settings as django_settings

from core.services.file_vault.audit import (
    _owner_reference,
    audit_attachment_quota_exceeded,
    audit_attachment_upload,
)
from core.services.file_vault.quota import StorageUsage, _configured_limit_bytes, storage_warning

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _delete_setting(monkeypatch, name):
    """Entfernt ``name`` vollstaendig aus den Django-Settings (Muster aus
    ``test_mutation_pins_099_breach_detection_a.py::_delete_setting``):
    sowohl aus dem Instanz-``__dict__`` der ``LazySettings`` als auch aus dem
    ``Settings``-Objekt dahinter (``_wrapped``) — erst dann greift
    ``getattr(settings, name, default)`` wirklich den ``default``-Zweig, weil
    ``settings/base.py`` die Konstante sonst IMMER setzt."""
    monkeypatch.delitem(django_settings.__dict__, name, raising=False)
    monkeypatch.delattr(django_settings._wrapped, name, raising=False)


class _OwnerStub:
    """Minimaler Owner-Stand-in mit ``_meta.model_name``/``pk``/``facility``
    (Muster aus ``test_mutation_followup_file_vault_audit.py::_OwnerStub``)."""

    class _Meta:
        model_name = "event"

    _meta = _Meta()

    def __init__(self, pk, facility):
        self.pk = pk
        self.facility = facility


# ============================================================================
# _owner_reference — defensive getattr-Defaults auf drei Ebenen
# ============================================================================


class TestOwnerReferenceDefensiveGetattr:
    def test_missing_meta_attribute_returns_none_pair_instead_of_raising(self):
        """Killt ``x__owner_reference__mutmut_7``:
        ``getattr(owner, "_meta", None)`` -> ``getattr(owner, "_meta", )``.

        Ein Owner ganz ohne ``_meta``-Attribut (kein Django-Model) ist genau
        der Fall, vor dem der Docstring warnt: das Original faengt ihn
        defensiv ab und liefert ``(None, None)``, die Mutante wirft
        ``AttributeError`` und wuerde den Audit-Write des Fehlerpfads mit
        sich reissen.
        """
        owner = object()

        assert _owner_reference(owner) == (None, None)

    def test_meta_without_model_name_returns_none_owner_type(self):
        """Killt ``x__owner_reference__mutmut_15``:
        ``getattr(meta, "model_name", None)`` -> ``getattr(meta, "model_name", )``.

        ``_meta`` ist vorhanden, traegt aber kein ``model_name`` (kein echtes
        Django-Model). Original liefert ``owner_type=None`` und liest den
        ``pk`` regulaer weiter; die Mutante wirft.
        """
        owner = SimpleNamespace(_meta=SimpleNamespace(), pk=99)

        assert _owner_reference(owner) == (None, "99")

    def test_owner_without_pk_returns_none_owner_id(self):
        """Killt ``x__owner_reference__mutmut_24``:
        ``getattr(owner, "pk", None)`` -> ``getattr(owner, "pk", )``.

        ``_meta.model_name`` ist vorhanden, ``pk`` fehlt komplett. Original
        liefert ``owner_id=None``, die Mutante wirft.
        """
        owner = SimpleNamespace(_meta=SimpleNamespace(model_name="event"))

        assert _owner_reference(owner) == ("event", None)


# ============================================================================
# audit_attachment_upload — MIME-Fallback exakt
# ============================================================================


class TestAuditAttachmentUploadMimeFallback:
    def test_fallback_mime_used_exact_lowercase_string_when_both_hints_missing(self, facility, staff_user):
        """Killt drei Survivor auf einmal:

        - ``x_audit_attachment_upload__mutmut_38``: ``staged.content_type or
          "application/octet-stream"`` -> ``staged.content_type and
          "application/octet-stream"`` (Operator-Praezedenz — der
          Fallback-String wird bei fehlendem ``content_type`` NIE
          geschrieben, ``mime`` bleibt leer).
        - ``x_audit_attachment_upload__mutmut_40``: XX-Marker-Variante des
          Fallback-Strings.
        - ``x_audit_attachment_upload__mutmut_41``: Gross-Schreibungs-
          Variante des Fallback-Strings.

        Fehlen ``detected_mime`` UND ``content_type`` (leere Datei-Metadaten,
        Pre-Encrypt-Pipeline liefert im Fehlerfall leere Strings), MUSS die
        Audit-Zeile den dokumentierten Fallback exakt kleingeschrieben
        tragen — der MIME-Typ ist Teil der forensischen Spur.
        """
        owner = _OwnerStub(pk=101, facility=facility)
        field_template = SimpleNamespace(slug="scan")
        attachment = SimpleNamespace(pk=uuid.uuid4(), entry_id=uuid.uuid4())
        staged = SimpleNamespace(
            original_name="dokument.pdf",
            file_size=4096,
            detected_mime="",
            content_type="",
        )

        entry = audit_attachment_upload(owner, field_template, attachment, staged, staff_user, mode="add")

        assert entry.detail["mime"] == "application/octet-stream"


# ============================================================================
# audit_attachment_quota_exceeded — Byte-MB-Divisor und Rundungs-Praezision
# ============================================================================


class TestAuditAttachmentQuotaExceededExactMbValues:
    def test_used_mb_uses_declared_bytes_per_mb_divisor_and_two_decimal_precision(self, facility, staff_user):
        """Killt drei Survivor auf einmal (``used_mb``):

        - ``x_audit_attachment_quota_exceeded__mutmut_42``:
          ``usage.used_bytes / (1024 * 1024)`` -> ``/ (1025 * 1024)``.
        - ``x_audit_attachment_quota_exceeded__mutmut_43``:
          ``/ (1024 * 1024)`` -> ``/ (1024 * 1025)``.
        - ``x_audit_attachment_quota_exceeded__mutmut_44``:
          ``round(..., 2)`` -> ``round(..., 3)``.

        ``used_bytes=12345678`` liefert bei korrektem Divisor und zwei
        Nachkommastellen exakt ``11.77`` — jede der drei Verzerrungen
        (falscher Divisor ODER dritte Nachkommastelle) aendert diesen Wert
        sichtbar.
        """
        usage = StorageUsage(used_bytes=12345678, trash_bytes=0, limit_bytes=5000 * 1024 * 1024)

        entry = audit_attachment_quota_exceeded(
            facility,
            staff_user,
            None,
            usage=usage,
            requested_bytes=1024 * 1024,
        )

        assert entry.detail["used_mb"] == 11.77

    def test_requested_mb_uses_declared_bytes_per_mb_divisor_and_two_decimal_precision(self, facility, staff_user):
        """Killt drei Survivor auf einmal (``requested_mb``):

        - ``x_audit_attachment_quota_exceeded__mutmut_55``:
          ``requested_bytes / (1024 * 1024)`` -> ``/ (1025 * 1024)``.
        - ``x_audit_attachment_quota_exceeded__mutmut_56``:
          ``/ (1024 * 1024)`` -> ``/ (1024 * 1025)``.
        - ``x_audit_attachment_quota_exceeded__mutmut_57``:
          ``round(..., 2)`` -> ``round(..., 3)``.

        ``requested_bytes=3221357472`` liefert bei korrektem Divisor und
        zwei Nachkommastellen exakt ``3072.13``.
        """
        usage = StorageUsage(used_bytes=0, trash_bytes=0, limit_bytes=5000 * 1024 * 1024)

        entry = audit_attachment_quota_exceeded(
            facility,
            staff_user,
            None,
            usage=usage,
            requested_bytes=3221357472,
        )

        assert entry.detail["requested_mb"] == 3072.13


# ============================================================================
# storage_warning — exakter Wortlaut
# ============================================================================


class TestStorageWarningExactWording:
    def test_returns_exact_wording_with_all_four_values(self):
        """Killt drei Survivor auf einmal (XX-Marker-/Gross-Klein-Varianten
        desselben Wortlauts):

        - ``x_storage_warning__mutmut_4``: XX-Marker im ersten Teilstring.
        - ``x_storage_warning__mutmut_7``: XX-Marker im zweiten Teilstring.
        - ``x_storage_warning__mutmut_8``: Kleinschreibung des zweiten
          Teilstrings.

        Derselbe Warntext erscheint unveraendert im Verwaltungs-Hub UND im
        Leitungs-Dashboard (dort war der Warnzustand vorher nur
        farbcodiert, WCAG 1.4.1) — ein exakter Vergleich des vollstaendigen
        Satzes deckt jede Teilstring-Verstuemmelung ab.
        """
        usage = StorageUsage(
            used_bytes=950 * 1024 * 1024,
            trash_bytes=100 * 1024 * 1024,
            limit_bytes=1000 * 1024 * 1024,
        )
        assert usage.is_near_limit

        message = storage_warning(usage)

        assert message == (
            "Speicherkontingent zu 95 % belegt (950 von 1000 MB, "
            "davon 100 MB im Papierkorb) — Papierkorb leeren oder Kontingent erhöhen."
        )


# ============================================================================
# _configured_limit_bytes — Inline-Default des Settings-Lesers
# ============================================================================


class TestConfiguredLimitBytesBackstopDefaultContract:
    def test_missing_backstop_setting_falls_back_to_no_backstop(self, facility, monkeypatch):
        """Killt zwei Survivor auf einmal (Inline-Default des Settings-
        Lesers, Regel 4 der bindenden Klassifikation):

        - ``x__configured_limit_bytes__mutmut_12``: ``getattr(django_settings,
          "FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB", 0)`` -> Default entfernt
          (wirft ``AttributeError`` statt auf den dokumentierten Fallback
          "kein Backstop" zurueckzufallen).
        - ``x__configured_limit_bytes__mutmut_15``: derselbe Default
          ``0`` -> ``1`` (liefert still einen erfundenen 1-MB-Backstop statt
          "kein Backstop").

        Ohne Einrichtungs-``Settings``-Row (``facility`` ohne
        ``max_storage_mb``) UND ohne den Betreiber-Backstop ist das
        dokumentierte Ergebnis ``None`` (unbegrenzt an dieser Stelle).
        """
        _delete_setting(monkeypatch, "FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB")

        assert _configured_limit_bytes(facility) is None
