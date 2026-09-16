"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Batch ``events-1``
(Vollmutation-Survivor-Triage), Modul ``core.services.events.history_diff``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-events-1.json`` (Triage
``triage-099-events-1.json``). Alle 63 Survivor-Mutanten dieses Batches
sitzen im selben Modul: die Diff-/Maskierungs-Logik fuer
``EventHistory``-Eintraege (Vorrang-Regel #1961: restricted > Datei-Marker >
berechtigt ? Klartext : encrypted > Roh-/Formatierungswert).

Die Tests rufen die privaten Funktionen des Moduls ueberwiegend DIREKT auf
(``_display_value``, ``_resolve_label``, ``_diff_create``,
``_diff_update``, ``_diff_update_redacted``, ``_comparison_value`` u. a.)
statt ueber ``compute_event_diff``/echte ``EventHistory``-Zeilen zu gehen —
die Funktionen nehmen ``slug_info`` als reines Dict entgegen, brauchen also
keine DB-Objekte ausser einem echten Rollen-User fuer die
Sichtbarkeitspruefung. Das haelt die Suite schnell und die Assertions exakt
auf die mutierte Zeile fokussiert.

``_encryption_key`` liegt modulweit an (Lehre 112), auch wenn nicht jeder
Test hier tatsaechlich verschluesselt.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import DocumentType
from core.services.events.history_diff import (
    ENCRYPTED_PLACEHOLDER,
    REDACTED_VALUE_TEXT,
    RESTRICTED_PLACEHOLDER,
    _build_slug_info,
    _build_slug_info_from_metadata,
    _comparison_value,
    _diff_create,
    _diff_update,
    _diff_update_redacted,
    _display_value,
    _is_encrypted,
    _is_restricted,
    _marker_slugs,
    _resolve_label,
    compute_event_diff,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# _build_slug_info_from_metadata — 6 Mutanten (8, 10, 13, 17, 19, 22)
# ---------------------------------------------------------------------------


def test_build_slug_info_from_metadata_defaults_for_incomplete_legacy_entries():
    """Kills ``x__build_slug_info_from_metadata__mutmut_8/_10/_13`` (Default
    von ``is_encrypted`` ``False`` -> ``None``/entfernt/``True``) und
    ``_17/_19/_22`` (Default von ``sensitivity`` ``""`` -> ``None``/entfernt/
    ``"XXXX"``): ein ``field_metadata``-Eintrag ohne ``is_encrypted``/
    ``sensitivity`` (aeltere oder unvollstaendige Snapshots) bekommt exakt
    ``False`` bzw. ``""`` als Fallback, kein anderer Wert — ``""`` ist die
    Vererbungs-Semantik „von ``doc_type_sensitivity`` erben" in
    :func:`core.services.compliance.sensitivity.effective_sensitivity`."""
    result = _build_slug_info_from_metadata({"s": {"name": "Feld"}})
    assert result == {"s": {"name": "Feld", "is_encrypted": False, "sensitivity": ""}}


# ---------------------------------------------------------------------------
# _build_slug_info — 4 Mutanten (2, 6, 11, 12)
# ---------------------------------------------------------------------------


def test_build_slug_info_reads_live_fields_in_a_single_query(doc_type_crisis):
    """Kills ``x__build_slug_info__mutmut_2`` (``select_related("field_template")``
    -> ``select_related(None)``, Abfragezahl N+1 statt 1), ``_6`` (Werte-Dict
    -> ``None``) sowie ``_11``/``_12`` (Schluessel ``sensitivity`` ->
    ``XXsensitivityXX``/``SENSITIVITY``): der Live-Fallback (kein
    ``field_metadata``-Snapshot) muss den exakten
    ``{name, is_encrypted, sensitivity}``-Eintrag mit EINER Query liefern."""
    ft = doc_type_crisis.fields.get().field_template
    fake_event = SimpleNamespace(document_type=doc_type_crisis)

    with CaptureQueriesContext(connection) as ctx:
        result = _build_slug_info(fake_event)
    assert len(ctx.captured_queries) == 1

    assert result == {
        ft.slug: {"name": "Notiz (Krise)", "is_encrypted": True, "sensitivity": "high"},
    }


# ---------------------------------------------------------------------------
# _display_value — 12 Mutanten (1, 2, 11-20)
# ---------------------------------------------------------------------------


def test_display_value_defaults_keep_restriction_and_encryption_closed(monkeypatch):
    """Kills ``x__display_value__mutmut_1`` (Default ``restricted=False`` ->
    ``True``) und ``_2`` (Default ``may_decrypt=False`` -> ``True``): ohne
    explizite Kwargs bleibt ein Wert unmaskiert sichtbar und ein
    verschluesselter Wert maskiert — ``safe_decrypt`` darf ohne
    ``may_decrypt=True`` NIE aufgerufen werden."""
    calls = []
    monkeypatch.setattr(
        "core.services.events.history_diff.safe_decrypt",
        lambda value, default=None: calls.append(value) or "DECRYPTED",
    )

    assert _display_value("Text", False) == "Text"
    assert _display_value("token", True) == ENCRYPTED_PLACEHOLDER
    assert calls == []


def test_display_value_scalar_placeholders_are_exact_german_text():
    """Kills ``x__display_value__mutmut_11`` (``"–"`` -> ``"XX–XX"``),
    ``_12/_13/_14`` (``"Ja"`` -> ``"XXJaXX"``/``"ja"``/``"JA"``) und
    ``_15/_16/_17`` (``"Nein"`` -> ``"XXNeinXX"``/``"nein"``/``"NEIN"``)."""
    assert _display_value(None, False) == "–"
    assert _display_value(True, False) == "Ja"
    assert _display_value(False, False) == "Nein"


def test_display_value_list_values_join_with_comma_space():
    """Kills ``x__display_value__mutmut_18`` (``", ".join(...)`` ->
    ``", ".join(None)``, TypeError), ``_19`` (Trenner ``", "`` ->
    ``"XX, XX"``) und ``_20`` (jedes Element -> ``str(None)`` statt
    ``str(v)``, echte Listenwerte verschwinden aus der Anzeige)."""
    assert _display_value(["a", "b"], False) == "a, b"


# ---------------------------------------------------------------------------
# _resolve_label — 8 Mutanten (1, 2, 5-10)
# ---------------------------------------------------------------------------


def test_resolve_label_uses_the_stored_name_when_slug_is_known():
    """Kills ``x__resolve_label__mutmut_1`` (``info = slug_info.get(slug)`` ->
    ``info = None``, IMMER Fallback-Formatierung statt gespeichertem Namen)
    und ``_2`` (``.get(slug)`` -> ``.get(None)``, findet den echten Slug
    nie)."""
    slug_info = {"foo": {"name": "Vorname"}}
    assert _resolve_label("foo", slug_info) == "Vorname"


def test_resolve_label_fallback_formats_unknown_slug_dash_as_space_titlecase():
    """Kills ``x__resolve_label__mutmut_5/_6/_7/_8`` (``.replace("-", " ")``
    verliert je ein Argument -> ``TypeError``) sowie ``_9`` (Suchstring
    ``"-"`` -> ``"XX-XX"``, Bindestrich bleibt stehen) und ``_10``
    (Ersatzstring ``" "`` -> ``"XX XX"``): ein unbekannter Slug (Feld
    umbenannt/geloescht, Legacy-Eintrag ohne Metadaten) faellt auf
    ``slug.replace("-", " ").title()`` zurueck, nicht auf einen Absturz oder
    eine andere Formatierung."""
    assert _resolve_label("foo-bar", {}) == "Foo Bar"


# ---------------------------------------------------------------------------
# _is_encrypted / _is_restricted — je 1 Mutant
# ---------------------------------------------------------------------------


def test_is_encrypted_default_for_unknown_slug_is_false():
    """Kills ``x__is_encrypted__mutmut_5`` (Default ``False`` -> ``True``):
    ein Slug ohne Eintrag in ``slug_info`` gilt NICHT als verschluesselt."""
    assert _is_encrypted("unbekannt", {}) is False


def test_is_restricted_default_sensitivity_for_unknown_slug_inherits_doc_type(staff_user):
    """Kills ``x__is_restricted__mutmut_8`` (Default ``""`` -> ``"XXXX"``):
    ein unbekannter Slug erbt ueber den LEEREN Default die
    Dokumentationstyp-Sensitivity (hier ``normal``, fuer Fachkraft sichtbar)
    statt ueber einen unbekannten Sensitivity-Wert fail-closed IMMER
    restricted zu werden (:func:`effective_sensitivity`/
    ``UNKNOWN_SENSITIVITY_RANK``)."""
    assert _is_restricted("unbekannt", {}, staff_user, DocumentType.Sensitivity.NORMAL) is False


# ---------------------------------------------------------------------------
# _marker_slugs — 2 Mutanten (3, 5)
# ---------------------------------------------------------------------------


def test_marker_slugs_default_for_missing_fields_key_is_empty_list():
    """Kills ``x__marker_slugs__mutmut_3`` (Default ``[]`` -> ``None``) und
    ``_5`` (Default entfernt, ebenfalls ``None``): ein Marker ohne
    ``"fields"`` — genau das Format der Art.-17-Anonymisierung
    (``build_anonymization_marker``: ``{"_redacted": True, "anonymized":
    True}``, ECHTER Produktionspfad) — darf NICHT mit ``TypeError``
    abstuerzen (``list(None)``); die Feldliste ist dann leer."""
    assert _marker_slugs({"_redacted": True, "anonymized": True}) == []


# ---------------------------------------------------------------------------
# _diff_create — 4 Mutanten (10, 13, 15, 16)
# ---------------------------------------------------------------------------


def test_diff_create_redacted_value_uses_the_exact_redacted_text(staff_user):
    """Kills ``x__diff_create__mutmut_10`` (``REDACTED_VALUE_TEXT`` ->
    ``None``) und ``_16`` (``doc_type_sensitivity`` -> ``None`` in der
    ``_field_masking``-Weitergabe, faellt fail-closed IMMER auf restricted):
    ein redigiertes CREATE-Feld eines normalen, sichtbaren Slugs zeigt EXAKT
    ``REDACTED_VALUE_TEXT``, nicht ``None`` und nicht faelschlich
    ``RESTRICTED_PLACEHOLDER``."""
    slug_info = {"s": {"name": "Feld", "is_encrypted": False, "sensitivity": ""}}
    result = _diff_create(
        {"_redacted": True, "fields": ["s"]},
        slug_info,
        staff_user,
        DocumentType.Sensitivity.NORMAL,
    )
    assert result == [{"label": "Feld", "value": REDACTED_VALUE_TEXT}]


def test_diff_create_redacted_restricted_field_stays_hidden(staff_user):
    """Kills ``x__diff_create__mutmut_13`` (``slug`` -> ``None`` in der
    ``_field_masking``-Weitergabe) und ``_15`` (``user`` -> ``None``
    ebenda): beide lassen die Restriction-Pruefung ins Leere laufen (Lookup
    auf Schluessel ``None`` bzw. ``_is_restricted`` mit ``user=None``
    liefert sofort ``False``) — ein High-Sensitivity-Feld eines
    redigierten CREATE-Eintrags muss fuer eine Fachkraft
    ``RESTRICTED_PLACEHOLDER`` bleiben, nicht auf den generischen
    Redaktionstext zurueckfallen (Vorrang-Regel #1961: restricted geht vor
    der Redaktions-Meldung)."""
    slug_info = {"geheim": {"name": "Geheim", "is_encrypted": False, "sensitivity": "high"}}
    result = _diff_create(
        {"_redacted": True, "fields": ["geheim"]},
        slug_info,
        staff_user,
        DocumentType.Sensitivity.NORMAL,
    )
    assert result == [{"label": "Geheim", "value": RESTRICTED_PLACEHOLDER}]


# ---------------------------------------------------------------------------
# _comparison_value — 4 Mutanten (1, 2, 4, 6)
# ---------------------------------------------------------------------------


def test_comparison_value_only_decrypts_when_encrypted_and_may_decrypt_and_no_file_marker(monkeypatch):
    """Kills ``x__comparison_value__mutmut_1``/``_2`` (``and`` -> ``or``
    zwischen den ersten beiden bzw. den ersten und dem dritten Operanden —
    ``safe_decrypt`` wuerde dann auf JEDEM Nicht-Datei-Wert bzw. jedem
    verschluesselten Wert unabhaengig von ``may_decrypt`` aufgerufen),
    ``_4`` (``is_file_marker(value)`` -> ``is_file_marker(None)``, ein
    echter Datei-Marker wuerde faelschlich zum Entschluesseln geschickt)
    und ``_6`` (``default=ENCRYPTED_PLACEHOLDER`` -> ``default=None``, der
    Fallback bei kaputtem Ciphertext waere ``None`` statt der Meldung)."""
    calls = []
    monkeypatch.setattr(
        "core.services.events.history_diff.safe_decrypt",
        lambda value, default=None: calls.append((value, default)) or "DECRYPTED",
    )

    assert _comparison_value("raw", False, False) == "raw"
    assert calls == []

    assert _comparison_value("raw", True, False) == "raw"
    assert calls == []

    marker = {"__file__": True, "attachment_id": "a1"}
    assert _comparison_value(marker, True, True) == marker
    assert calls == []

    assert _comparison_value("raw", True, True) == "DECRYPTED"
    assert calls == [("raw", ENCRYPTED_PLACEHOLDER)]


# ---------------------------------------------------------------------------
# _diff_update_redacted — 11 Mutanten (15, 17, 18, 23, 24, 25, 32, 33, 34, 37, 38)
# ---------------------------------------------------------------------------


def test_diff_update_redacted_field_masking_uses_the_real_slug_and_user(staff_user):
    """Kills ``x__diff_update_redacted__mutmut_15`` (``slug`` -> ``None``
    in der ``_field_masking``-Weitergabe) und ``_17`` (``user`` -> ``None``
    ebenda): ein High-Sensitivity-Feld muss auf BEIDEN Seiten
    ``RESTRICTED_PLACEHOLDER`` bleiben, nicht faelschlich offen werden."""
    slug_info = {"geheim": {"name": "Geheim", "is_encrypted": False, "sensitivity": "high"}}
    result = _diff_update_redacted(
        {"geheim": "Wert-alt"},
        {"_redacted": True, "fields": ["geheim"]},
        slug_info,
        staff_user,
        DocumentType.Sensitivity.NORMAL,
    )
    assert result[0]["old_value"] == RESTRICTED_PLACEHOLDER
    assert result[0]["new_value"] == RESTRICTED_PLACEHOLDER


def test_diff_update_redacted_field_masking_uses_the_real_doc_type_sensitivity(staff_user):
    """Kills ``x__diff_update_redacted__mutmut_18`` (``doc_type_sensitivity``
    -> ``None`` in der ``_field_masking``-Weitergabe, faellt fail-closed
    IMMER auf restricted): ein normales, fuer die Fachkraft sichtbares Feld
    darf NICHT ploetzlich ``RESTRICTED_PLACEHOLDER`` zeigen."""
    slug_info = {"normal": {"name": "Normal", "is_encrypted": False, "sensitivity": ""}}
    result = _diff_update_redacted(
        {"normal": "Wert-alt"},
        {"_redacted": True, "fields": ["normal"]},
        slug_info,
        staff_user,
        DocumentType.Sensitivity.NORMAL,
    )
    assert result[0]["old_value"] == "Wert-alt"
    assert result[0]["new_value"] == REDACTED_VALUE_TEXT


def test_diff_update_redacted_redacted_side_shows_encrypted_placeholder_not_generic_text():
    """Kills ``x__diff_update_redacted__mutmut_23`` (``encrypted`` -> ``None``
    im ``_redacted_display_value``-Aufruf): ein verschluesseltes Feld ohne
    Entschluesselungs-Zugriff (kein User) muss auf seiner redigierten Seite
    ``ENCRYPTED_PLACEHOLDER`` zeigen, nicht den generischen Redaktionstext —
    sonst verwischt der Unterschied zwischen „verschluesselt, kein
    Zugriff" und „echt redigiert"."""
    slug_info = {"s": {"name": "Feld", "is_encrypted": True, "sensitivity": ""}}
    result = _diff_update_redacted(
        {"_redacted": True, "fields": ["s"]},
        {"s": "Wert-neu"},
        slug_info,
        None,
        DocumentType.Sensitivity.NORMAL,
    )
    assert result[0]["old_value"] == ENCRYPTED_PLACEHOLDER


def test_diff_update_redacted_redacted_side_hides_restricted_fields_entirely(staff_user):
    """Kills ``x__diff_update_redacted__mutmut_24`` (``restricted`` -> ``None``
    im ``_redacted_display_value``-Aufruf): die redigierte Seite eines
    restricted Feldes muss ``RESTRICTED_PLACEHOLDER`` zeigen — niemals den
    Encrypted- oder generischen Redaktionstext (Vorrang-Regel #1961: „wer
    das Feld ohnehin nicht sehen duerfte, erfaehrt ueber seinen Zustand auch
    nichts")."""
    slug_info = {"s": {"name": "Feld", "is_encrypted": False, "sensitivity": "high"}}
    result = _diff_update_redacted(
        {"_redacted": True, "fields": ["s"]},
        {"s": "Wert-neu"},
        slug_info,
        staff_user,
        DocumentType.Sensitivity.NORMAL,
    )
    assert result[0]["old_value"] == RESTRICTED_PLACEHOLDER


def test_diff_update_redacted_redacted_side_shows_generic_text_when_authorized(staff_user):
    """Kills ``x__diff_update_redacted__mutmut_25`` (``may_decrypt`` ->
    ``None`` im ``_redacted_display_value``-Aufruf): ein autorisierter User
    sieht auf der redigierten Seite eines entschluesselbaren Feldes den
    generischen Redaktionstext, nicht faelschlich den
    Encrypted-nicht-zugreifbar-Platzhalter."""
    slug_info = {"s": {"name": "Feld", "is_encrypted": True, "sensitivity": ""}}
    result = _diff_update_redacted(
        {"_redacted": True, "fields": ["s"]},
        {"s": "Wert-neu"},
        slug_info,
        staff_user,
        DocumentType.Sensitivity.NORMAL,
    )
    assert result[0]["old_value"] == REDACTED_VALUE_TEXT


def test_diff_update_redacted_unredacted_side_keeps_its_full_masking(staff_user, monkeypatch):
    """Kills ``x__diff_update_redacted__mutmut_32`` (``is_encrypted`` ->
    ``None`` im ``_display_value``-Aufruf der nicht-redigierten Seite —
    zeigt sonst die rohe Ciphertext-Struktur statt des
    Encrypted-Platzhalters), ``_33``/``_37`` (``restricted`` -> ``None``
    bzw. ganz entfernt — ein restricted Feld faellt offen) und
    ``_34``/``_38`` (``may_decrypt`` -> ``None`` bzw. ganz entfernt — ein
    autorisierter User bekaeme faelschlich den Encrypted-Platzhalter statt
    des entschluesselten Werts)."""
    monkeypatch.setattr(
        "core.services.events.history_diff.safe_decrypt",
        lambda value, default=None: "PLAINTEXT-XYZ",
    )

    # (32) is_encrypted muss ankommen: ohne Entschluesselungs-Zugriff zeigt
    # die nicht-redigierte Seite den Encrypted-Platzhalter, nicht die rohe
    # Ciphertext-Dict-Struktur.
    slug_info_enc = {"s": {"name": "Feld", "is_encrypted": True, "sensitivity": ""}}
    result = _diff_update_redacted(
        {"s": {"__encrypted__": True, "value": "irrelevant"}},
        {"_redacted": True, "fields": ["s"]},
        slug_info_enc,
        None,
        DocumentType.Sensitivity.NORMAL,
    )
    assert result[0]["old_value"] == ENCRYPTED_PLACEHOLDER

    # (33/37) restricted muss ankommen: ein High-Sensitivity-Feld bleibt
    # [Eingeschränkt], auch auf der nicht-redigierten Seite.
    slug_info_restr = {"s": {"name": "Feld", "is_encrypted": False, "sensitivity": "high"}}
    result = _diff_update_redacted(
        {"s": "Geheimtext"},
        {"_redacted": True, "fields": ["s"]},
        slug_info_restr,
        staff_user,
        DocumentType.Sensitivity.NORMAL,
    )
    assert result[0]["old_value"] == RESTRICTED_PLACEHOLDER

    # (34/38) may_decrypt muss ankommen: ein autorisierter User sieht den
    # entschluesselten Wert, nicht den Encrypted-Platzhalter.
    result = _diff_update_redacted(
        {"s": {"__encrypted__": True, "value": "irrelevant"}},
        {"_redacted": True, "fields": ["s"]},
        slug_info_enc,
        staff_user,
        DocumentType.Sensitivity.NORMAL,
    )
    assert result[0]["old_value"] == "PLAINTEXT-XYZ"


# ---------------------------------------------------------------------------
# _diff_update — 9 Mutanten, davon 3 gepinnt (7, 8, 49) und 6 equivalent
# (54, 59, 72, 76, 84, 88 — siehe Bericht)
# ---------------------------------------------------------------------------


def test_diff_update_delegates_the_real_user_to_the_redacted_path(staff_user):
    """Kills ``x__diff_update__mutmut_7`` (``user`` -> ``None`` bei der
    Weitergabe an ``_diff_update_redacted``): sobald eine Seite ein
    Redaktions-Marker ist, muss der ECHTE User weitergereicht werden — ein
    High-Sensitivity-Feld faellt sonst offen, weil ``_is_restricted`` bei
    ``user=None`` sofort ``False`` liefert."""
    slug_info = {"s": {"name": "Feld", "is_encrypted": False, "sensitivity": "high"}}
    result = _diff_update(
        {"_redacted": True, "fields": ["s"]},
        {"s": "Wert-neu"},
        slug_info,
        staff_user,
        DocumentType.Sensitivity.NORMAL,
    )
    assert result[0]["old_value"] == RESTRICTED_PLACEHOLDER


def test_diff_update_delegates_the_real_doc_type_sensitivity_to_the_redacted_path(staff_user):
    """Kills ``x__diff_update__mutmut_8`` (``doc_type_sensitivity`` ->
    ``None`` bei der Weitergabe an ``_diff_update_redacted``, faellt
    fail-closed IMMER auf restricted): ein normales, sichtbares Feld darf
    nicht ploetzlich [Eingeschränkt] werden."""
    slug_info = {"s": {"name": "Feld", "is_encrypted": False, "sensitivity": ""}}
    result = _diff_update(
        {"_redacted": True, "fields": ["s"]},
        {"s": "Wert-neu"},
        slug_info,
        staff_user,
        DocumentType.Sensitivity.NORMAL,
    )
    assert result[0]["old_value"] == REDACTED_VALUE_TEXT


def test_diff_update_continues_past_unchanged_fields_instead_of_stopping():
    """Kills ``x__diff_update__mutmut_49`` (``continue`` -> ``break``): ein
    unveraendertes Feld darf nur UEBERSPRUNGEN werden, nicht die gesamte
    Diff-Schleife abbrechen — sonst verschwindet ein spaeter tatsaechlich
    geaendertes Feld komplett aus dem Audit-Diff (Integritaets-Luecke: eine
    echte Aenderung waere im UPDATE-Verlauf unsichtbar)."""
    slug_info = {
        "a": {"name": "A", "is_encrypted": False, "sensitivity": ""},
        "b": {"name": "B", "is_encrypted": False, "sensitivity": ""},
    }
    result = _diff_update(
        {"a": "1", "b": "1"},
        {"a": "1", "b": "2"},
        slug_info,
        None,
        DocumentType.Sensitivity.NORMAL,
    )
    assert [f["label"] for f in result] == ["B"]


# ---------------------------------------------------------------------------
# compute_event_diff — 1 Mutant (47)
# ---------------------------------------------------------------------------


def test_compute_event_diff_unknown_action_returns_no_fields_not_none():
    """Kills ``x_compute_event_diff__mutmut_47`` (else-Zweig ``fields = []``
    -> ``fields = None``): fuer eine unbekannte ``action``
    (Verteidigungszweig — echte ``EventHistory.Action``-Werte sind nur
    create/update/delete) muss ``fields`` eine leere Liste bleiben, kein
    ``None`` — ein Template, das ueber ``result["fields"]`` iteriert, wuerde
    sonst mit ``TypeError`` abstuerzen. Ueber ein ``SimpleNamespace`` statt
    einer echten ``EventHistory``-Zeile konstruiert, damit der
    Live-Fallback-Pfad (``_build_slug_info``) keine DB-Query braucht."""
    entry = SimpleNamespace(
        field_metadata=None,
        _slug_info={"irrelevant": {"name": "X", "is_encrypted": False, "sensitivity": ""}},
        event=SimpleNamespace(document_type=SimpleNamespace(sensitivity=DocumentType.Sensitivity.NORMAL)),
        action="unbekannt",
        data_before=None,
        data_after=None,
    )
    result = compute_event_diff(entry)
    assert result == {"action": "unbekannt", "fields": []}
