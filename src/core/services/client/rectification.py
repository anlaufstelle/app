"""Art.-16-Berichtigungsworkflow (ADR-041, Refs #1060).

Der **interne** Antrags-/Review-Weg zu [ADR-013](../../../../docs/adr/013-dsgvo-art16-no-selfservice.md):
eine Fachkraft (``staff``+) beantragt die Korrektur eines Personenfelds, die
Leitung (``facility_lead``/``facility_admin``) genehmigt oder lehnt ab. Es gibt
keinen Klientel-Self-Service — Antragstellerin ist immer eine Fachkraft.

**Warum die Anwendung ausschliesslich ueber ``update_client`` laeuft:** dort
haengen Allowlist (Mass-Assignment-Schutz), ``data_json``-Whitelist gegen den
Dokumentationstyp, Feldverschluesselung im ``save()``, optimistisches Locking,
Activity-Feed und ``CLIENT_UPDATE``-Audit. Ein Direkt-``save()`` aus dem
Genehmigungspfad haette all das umgangen — die Genehmigung ist damit exakt so
sicher wie ein normales Bearbeiten, plus Vier-Augen-Nachweis.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import AuditLog, Client, FieldTemplate, RectificationRequest
from core.services.audit import audit_event
from core.services.compliance import user_can_see_field
from core.services.events.fields import _serialize_json_value
from core.services.file_vault import encrypt_field, safe_decrypt

#: Berichtigungsfaehige Stammdatenfelder (v1-Scope laut #1060).
#:
#: ``contact_stage`` fehlt bewusst (Entscheid E10): der Stufenwechsel hat einen
#: eigenen Workflow mit eigenen Gates (Herabstufung nur durch die Leitung, siehe
#: ``update_client``) und eigenem Audit (``STAGE_CHANGE``). Ihn hier
#: mitzufuehren wuerde diese Gates hinter dem Berichtigungspfad verstecken.
#: ``document_type`` fehlt ebenso — ein Typwechsel ist eine Konfigurations-,
#: keine Datenberichtigung, und verwirft nach E11-Merge-Policy fremde Slugs.
CORE_FIELDS: tuple[str, ...] = ("pseudonym", "age_cluster", "notes")

#: Praefix der dynamischen Feldpfade (``data_json.<slug>``).
DATA_JSON_PREFIX = "data_json."


def _core_field_label(name: str) -> Any:
    """Label eines Stammdatenfelds aus dem Modell (SSOT, schon uebersetzt).

    ``getattr`` mit Default statt direktem Attributzugriff: ``_meta.get_field``
    ist fuer mypy ``Field | ForeignObjectRel``, und nur ersteres traegt
    ``verbose_name``. Alle Namen in :data:`CORE_FIELDS` sind konkrete Felder —
    der Default ist nur die Typ-Beruhigung.
    """
    return getattr(Client._meta.get_field(name), "verbose_name", name)


def _dynamic_slug(field_path: str) -> str | None:
    """Slug eines ``data_json.<slug>``-Pfads, sonst ``None``."""
    if field_path.startswith(DATA_JSON_PREFIX):
        return field_path[len(DATA_JSON_PREFIX) :]
    return None


def rectifiable_fields(client: Client, user: Any) -> list[tuple[str, Any]]:
    """``(field_path, label)``-Paare, die *user* an *client* berichtigen lassen darf.

    Rueckgabe ist bewusst eine Liste von Paaren: sie geht unveraendert als
    ``choices`` ins Formular und dient gleichzeitig als Allowlist fuer
    :func:`request_rectification` und die Re-Validierung in
    :func:`approve_rectification`.

    Dynamische Ziele stammen aus dem ``DocumentType`` der Person. Ausgenommen
    sind FILE-Felder (Dateien werden ueber die Anhang-UI ersetzt, nicht ueber
    ein Wertpaar) und alle Felder, die *user* wegen seiner Rolle nicht sehen
    darf — wer einen Wert nicht sehen darf, darf ihn auch nicht zur Korrektur
    vorschlagen und in der Feldauswahl nicht einmal als Existenz erkennen.
    Ohne Dokumentationstyp gibt es keine Whitelist und damit keine dynamischen
    Ziele.
    """
    fields: list[tuple[str, Any]] = [(name, _core_field_label(name)) for name in CORE_FIELDS]
    document_type = client.document_type if client.document_type_id else None
    if document_type is None:
        return fields

    from core.services.dynamic_data import build_field_template_lookup

    doc_sensitivity = document_type.sensitivity
    for slug, ft in build_field_template_lookup(document_type, ordered=True).items():
        if ft.field_type == FieldTemplate.FieldType.FILE:
            continue
        if not user_can_see_field(user, doc_sensitivity, ft.sensitivity or ""):
            continue
        fields.append((f"{DATA_JSON_PREFIX}{slug}", ft.name))
    return fields


def _assert_rectifiable(client: Client, user: Any, field_path: str) -> None:
    if field_path not in {path for path, _label in rectifiable_fields(client, user)}:
        raise ValidationError(_("Dieses Feld kann nicht über den Berichtigungsworkflow geändert werden."))


def _validate_new_value(field_path: str, new_value: Any) -> Any:
    """Wertebereich des Wunschwerts pruefen — im SERVICE, nicht nur im Formular.

    ``update_client`` setzt Attribute per ``setattr`` und speichert ohne
    ``full_clean()`` (bewusst: der Diff- und Audit-Pfad haengt daran). Ein
    ungueltiger ``age_cluster`` oder ein zu langes Pseudonym faellt daher erst
    beim Save der GENEHMIGUNG auf — als DataError/500 bei der pruefenden
    Person, nicht als Formularfehler bei der antragstellenden. Deshalb hier,
    zum Zeitpunkt des Antrags.

    ``data_json``-Werte bleiben ungeprueft: ihre Whitelist (Slug + FILE-Skip)
    haelt ``validate_data_json`` beim Anwenden.
    """
    if _dynamic_slug(field_path) is not None:
        return new_value
    if field_path == "age_cluster":
        if new_value not in Client.AgeCluster.values:
            raise ValidationError(_("Ungültige Altersgruppe."))
        return new_value
    text = "" if new_value is None else str(new_value)
    if field_path == "pseudonym" and not text.strip():
        raise ValidationError(_("Das Pseudonym darf nicht leer sein."))
    max_length = getattr(Client._meta.get_field(field_path), "max_length", None)
    if max_length and len(text) > max_length:
        raise ValidationError(_("Der Wert ist zu lang für dieses Feld."))
    return text


def _current_value(client: Client, field_path: str) -> Any:
    """Aktueller Klartext-Wert des Feldpfads (entschluesselt).

    Fuer ``data_json``-Slugs wird der gespeicherte Wert durch
    :func:`~core.services.file_vault.safe_decrypt` gereicht — verschluesselte
    Felder liegen dort als Marker-Dict. Ein fehlender Slug ergibt ``None``
    (nicht ``KeyError``): ein leeres Feld ist ein legitimer Ausgangszustand.
    """
    slug = _dynamic_slug(field_path)
    if slug is None:
        return getattr(client, field_path)
    return safe_decrypt((client.data_json or {}).get(slug))


def _encode_value(value: Any) -> Any:
    """JSON-serialisieren, dann verschluesseln (siehe Modell-Docstring).

    ``date``/``time`` werden vorher auf ISO-Strings normalisiert — dieselbe
    SSOT-Konvention (:func:`~core.services.events.fields._serialize_json_value`,
    Refs #1073), die ``_clean_client_dynamic_data`` beim Anwenden erzwingt. Sie
    ist hier nicht Kosmetik, sondern Pflicht: das Antragsformular liefert fuer
    DATE-/TIME-Slugs echte ``datetime``-Objekte (``parse_default``), an denen
    ``json.dumps`` mit ``TypeError`` (Fehler 500 im Antragspfad) scheitert.
    Ausserdem haelt sie ``old_value`` und ``new_value`` typgleich — der
    Klartext-Konflikt-Guard vergliche sonst ein ``date`` mit dem ISO-String aus
    ``data_json`` und blockte jede Genehmigung eines Datumsfelds.
    """
    return encrypt_field(json.dumps(_serialize_json_value(value)))


def decode_rectification_value(stored: Any) -> Any:
    """Umkehrung von :func:`_encode_value` — entschluesseln, dann ``json.loads``.

    Faellt ``safe_decrypt`` wegen eines korrupten Tokens auf seinen Platzhalter
    zurueck, ist das Ergebnis kein gueltiges JSON. Der Platzhalter wird dann
    unveraendert durchgereicht, statt den Aufrufer (Template, Konflikt-Guard)
    mit einem ``JSONDecodeError`` zu reissen.
    """
    plain = safe_decrypt(stored)
    if not isinstance(plain, str):
        return plain
    try:
        return json.loads(plain)
    except ValueError:
        return plain


def decode_rectification_text(stored: Any) -> str:
    """Entschluesselter Freitext (``reason``/``review_note``), nie ``None``."""
    if not stored:
        return ""
    plain = safe_decrypt(stored)
    return plain if isinstance(plain, str) else str(plain)


def request_rectification(client: Client, user: Any, field_path: str, new_value: Any, reason: str):
    """Erfasst einen Berichtigungsantrag (Status ``PENDING``).

    Der alte Wert wird **jetzt** festgehalten, nicht erst bei der Genehmigung —
    er ist der Bezugspunkt des Konflikt-Guards und Teil des Art.-5(2)-Nachweises
    ("was stand da, als beantragt wurde").

    Ein bereits offener Antrag zum selben Feld ist ein ``ValidationError`` (der
    partielle Unique-Index ist die belastbare Grenze; hier wird er nur in eine
    Formularmeldung uebersetzt) — nicht still idempotent wie beim Loeschantrag:
    dort ist das Ziel identisch, hier truege ein zweiter Antrag einen anderen
    Wunschwert.
    """
    _assert_rectifiable(client, user, field_path)
    new_value = _validate_new_value(field_path, new_value)
    old_value = _current_value(client, field_path)
    try:
        with transaction.atomic():
            rr = RectificationRequest.objects.create(
                facility=client.facility,
                target_client=client,
                field_path=field_path,
                old_value=_encode_value(old_value),
                new_value=_encode_value(new_value),
                reason=encrypt_field(reason),
                requested_by=user,
            )
    except IntegrityError as exc:
        raise ValidationError(
            _("Für dieses Feld läuft bereits ein Berichtigungsantrag."),
        ) from exc

    audit_event(
        action=AuditLog.Action.RECTIFICATION_REQUESTED,
        user=user,
        facility=client.facility,
        target_type="RectificationRequest",
        target_id=str(rr.pk),
        # NIE Feldwerte oder Begruendung ins detail — beides lebt verschluesselt
        # am Antrag und ist ueber ``target_id`` erreichbar (Muster #1093).
        detail={"field_path": field_path, "target_client": str(client.pk)},
    )
    return rr


def _guard_reviewable(rr: RectificationRequest, reviewer: Any) -> None:
    """Gemeinsame Guards 1–3 von Genehmigung und Ablehnung, in fester Reihenfolge.

    1. Status ``PENDING`` — ein abgeschlossener Antrag ist kein Vorgang mehr.
    2. Selbst-Review verboten (Vier-Augen-Prinzip; der DB-CheckConstraint ist
       der Backstop, diese Meldung die verstaendliche Fassung).
    3. Rolle Leitung/Anwendungsbetreuung — der Guard sitzt im SERVICE, nicht nur
       in der View, damit auch Nicht-Formular-Aufrufer (Command, kuenftiger
       Replay) ihn passieren muessen.
    """
    if rr.status != RectificationRequest.Status.PENDING:
        raise ValidationError(_("Dieser Berichtigungsantrag ist bereits abgeschlossen."))
    if rr.requested_by_id is not None and rr.requested_by_id == reviewer.pk:
        raise ValidationError(_("Sie können Ihren eigenen Berichtigungsantrag nicht prüfen (Vier-Augen-Prinzip)."))
    if not reviewer.is_lead_or_admin:
        raise ValidationError(_("Nur die Leitung darf Berichtigungsanträge prüfen."))


@transaction.atomic
def approve_rectification(rr: RectificationRequest, reviewer: Any, review_note: str = ""):
    """Genehmigt einen Antrag und wendet die Korrektur ueber ``update_client`` an.

    Guard-Reihenfolge (jeder Schritt einzeln getestet):

    1.–3. :func:`_guard_reviewable` (Status, Selbst-Review, Rolle).
    3a. **Widerruf der Person.** Wurde sie zwischen Antrag und Pruefung in den
       Papierkorb gelegt (``is_deleted``) oder anonymisiert (``is_active=False``),
       schriebe die Genehmigung frischen Klartext in einen bereits redigierten
       Datensatz (Refs #1771, Muster ``resolve_writable_client``).
    4. **Pfad-Re-Validierung gegen den Reviewer.** Zwischen Antrag und Pruefung
       kann sich die Konfiguration geaendert haben (Feld entfernt, Typwechsel,
       Sensitivity angehoben) — der beim Antrag gepruefte Pfad ist deshalb kein
       Freibrief. Geprueft wird mit der Sicht des **Reviewers**.
    5. **Klartext-Konflikt-Guard.** Der aktuelle Wert wird ENTSCHLUESSELT mit dem
       ENTSCHLUESSELTEN ``old_value`` verglichen. Fernet-Tokens sind frisch
       gesalzen — ein Byte-Vergleich waere immer ungleich und machte die
       Genehmigung verschluesselter Felder unmoeglich (und bei unverschluesselten
       Feldern faelschlich "grün").

    Der Antrag wird innerhalb der Transaktion per ``select_for_update()`` neu
    geladen (TOCTOU-Muster aus ``approve_client_deletion``, Refs #1376), damit
    zwei zeitgleiche Genehmigungen nicht beide anwenden. **Die Client-Zeile wird
    dabei mitgesperrt:** Guard 3a und Guard 5 lesen sie, und ohne Lock beruhten
    beide auf einem stalen Snapshot — zwischen dem Lesen und dem Lock in
    ``update_client`` koennte eine parallele Transaktion die Person in den
    Papierkorb legen, anonymisieren oder den Wert aendern, ohne dass ein Guard
    das noch saehe. ``of=("self",)`` ist dabei Pflicht und nicht Kosmetik:
    ``document_type`` ist ``null=True``, ``select_related`` erzeugt also einen
    LEFT OUTER JOIN, und ein nacktes ``FOR UPDATE`` bricht unter Postgres an
    dessen nullable Seite. Die Sperrreihenfolge (Antrag → Person) ist dieselbe
    wie in ``approve_client_deletion`` — kein neues Deadlock-Risiko.
    """
    rr = RectificationRequest.objects.select_for_update().get(pk=rr.pk, facility=rr.facility)
    _guard_reviewable(rr, reviewer)

    client = (
        Client.objects.select_for_update(of=("self",))
        .select_related("document_type")
        .get(pk=rr.target_client_id, facility=rr.facility)
    )
    if client.is_deleted or not client.is_active:
        # Lazy-Import: ``main`` importiert dieses Modul nicht, aber der
        # Paket-``__init__`` bindet beide — der funktionslokale Import haelt die
        # Reihenfolge unabhaengig (Muster ``_apply_rectification``).
        from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE

        raise ValidationError(CLIENT_WRITE_REVOKED_MESSAGE)
    _assert_rectifiable(client, reviewer, rr.field_path)

    expected_old = decode_rectification_value(rr.old_value)
    if _current_value(client, rr.field_path) != expected_old:
        raise ValidationError(
            _("Der Wert wurde zwischenzeitlich geändert — bitte den Antrag neu stellen."),
        )

    new_value = decode_rectification_value(rr.new_value)
    _apply_rectification(client, reviewer, rr.field_path, new_value)

    rr.status = RectificationRequest.Status.APPROVED
    rr.reviewed_by = reviewer
    rr.reviewed_at = timezone.now()
    rr.review_note = encrypt_field(review_note)
    rr.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])

    audit_event(
        action=AuditLog.Action.RECTIFICATION_APPROVED,
        user=reviewer,
        facility=rr.facility,
        target_type="RectificationRequest",
        target_id=str(rr.pk),
        detail={"field_path": rr.field_path, "target_client": str(client.pk)},
    )
    return rr


def _apply_rectification(client: Client, reviewer: Any, field_path: str, new_value: Any) -> None:
    """Schreibt den korrigierten Wert — ausschliesslich ueber ``update_client``.

    Bei ``data_json.<slug>`` wird das GANZE Dict uebergeben: ``update_client``
    laesst es durch die Slug-Whitelist des aktuellen Typs laufen und ersetzt es
    komplett; ein Teil-Update gaebe es dort nicht. Bereits verschluesselte
    Fremd-Slugs bleiben dabei unangetastet (``_encrypt_sensitive_fields``
    ueberspringt Marker-Dicts), der neue Klartext-Wert wird beim Save
    verschluesselt, sofern das Feld ``is_encrypted`` traegt.

    ``expected_updated_at`` kommt vom frisch gelesenen Client — der Guard
    schuetzt gegen Schreiber, die zwischen unserem Lesen und dem Save
    dazwischenfunken.
    """
    from core.services.client.main import update_client

    slug = _dynamic_slug(field_path)
    if slug is None:
        fields: dict[str, Any] = {field_path: new_value}
    else:
        data = dict(client.data_json or {})
        data[slug] = new_value
        fields = {"data_json": data}

    try:
        with transaction.atomic():
            update_client(client, reviewer, expected_updated_at=client.updated_at, **fields)
    except IntegrityError as exc:
        # Haeufigster Fall: ``unique_facility_pseudonym`` — der Wunsch-Pseudonym
        # ist inzwischen vergeben. Als ValidationError an die View, kein 500.
        raise ValidationError(
            _("Die Berichtigung konnte nicht angewendet werden (Wert bereits vergeben)."),
        ) from exc


@transaction.atomic
def reject_rectification(rr: RectificationRequest, reviewer: Any, review_note: str):
    """Lehnt einen Antrag ab — mit Pflicht-Pruefvermerk.

    ADR-013 begruendet den organisatorischen Weg gerade damit, dass strittige
    Eintraege oft Wahrnehmungsfragen sind; die fachliche Abwaegung wird deshalb
    auch (und gerade) bei der Ablehnung festgehalten — verschluesselt, weil sie
    den strittigen Sachverhalt benennt.

    Widerruf-Guard wie bei der Genehmigung (Spiegel von Guard 3a): die
    Anonymisierung laesst offene Antraege im Status PENDING und leert nur ihre
    Wertspalten — eine Ablehnung schriebe sonst einen FRISCHEN verschluesselten
    Pruefvermerk auf die bereits redigierte Zeile und entwertete die
    ADR-041-Zusage rueckwirkend. Der Client wird dafuer mitgesperrt, damit eine
    parallel laufende Anonymisierung nicht zwischen Guard und ``save()`` faellt.
    """
    rr = RectificationRequest.objects.select_for_update().get(pk=rr.pk, facility=rr.facility)
    _guard_reviewable(rr, reviewer)

    client = Client.objects.select_for_update(of=("self",)).get(pk=rr.target_client_id, facility=rr.facility)
    if client.is_deleted or not client.is_active:
        from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE

        raise ValidationError(CLIENT_WRITE_REVOKED_MESSAGE)

    rr.status = RectificationRequest.Status.REJECTED
    rr.reviewed_by = reviewer
    rr.reviewed_at = timezone.now()
    rr.review_note = encrypt_field(review_note)
    rr.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])

    audit_event(
        action=AuditLog.Action.RECTIFICATION_REJECTED,
        user=reviewer,
        facility=rr.facility,
        target_type="RectificationRequest",
        target_id=str(rr.pk),
        detail={"field_path": rr.field_path, "target_client": str(rr.target_client_id)},
    )
    return rr
