"""Client data export for DSGVO Art. 15 (right of access) and Art. 20 (data portability)."""

import weasyprint
from django.contrib.contenttypes.models import ContentType
from django.db.models import Prefetch, Q
from django.template.loader import render_to_string
from django.utils import timezone

from core.models import (
    Activity,
    Attachment,
    AuditLog,
    Client,
    DeletionRequest,
    Episode,
    Event,
    EventHistory,
    LegalHold,
    Milestone,
    OutcomeGoal,
    RecentClientVisit,
    RectificationRequest,
    RetentionProposal,
    WorkItem,
)
from core.models import Case as CaseModel
from core.models.attachment import owner_field_name
from core.services.facility import resolve_facility_display_name
from core.services.file_vault import safe_decrypt
from core.services.system.export import restricted_url_fetcher
from core.utils.formatting import isoformat_or_none as _isoformat


def _username(user):
    """Username eines optionalen User-FKs (``None`` bei ``SET_NULL``-Waisen)."""
    return user.username if user else None


def _soft_delete_meta(obj):
    """Papierkorb-Metadaten eines :class:`SoftDeletableModel` (Refs #1752).

    ``is_deleted``/``deleted_at``/``deleted_by`` sind selbst gespeicherte
    personenbezogene Daten: sie beantworten, ob und seit wann ein Datensatz im
    Papierkorb liegt — bis zum Hard-Purge existiert er weiter. Die Auskunft
    muss das ausweisen, sonst wirkt ein Datensatz „geloescht", der es nicht ist.
    """
    return {
        "is_deleted": obj.is_deleted,
        "deleted_at": _isoformat(obj.deleted_at),
        "deleted_by": _username(obj.deleted_by),
    }


# Refs #1738 (M24, ADR-039): FILE-Felder speichern in ``data_json`` nur einen
# Verweis auf die Attachment-Zeilen (``__file__``/``__files__``-Marker). Das rohe
# Marker-Dict waere in einem Auskunftsdokument technischer Ausschuss samt
# Attachment-UUIDs; die lesbaren Angaben stehen im jeweiligen Anhang-Abschnitt.
ATTACHMENT_FIELD_PLACEHOLDER = "[Dateianhang – Angaben unter „Dateianhänge“]"


def _ordered_attachments(queryset):
    """Anhang-Zeilen in Auskunfts-Reihenfolge, mit den Joins der Serialisierung.

    Eine Stelle fuer Sortierung und ``select_related`` — die Einzel-Abfrage
    (:func:`_gather_attachments` ohne Index) und der Sammel-Aufbau
    (:func:`_build_attachment_index`) muessen dieselbe Reihenfolge liefern,
    sonst haengt das Auskunftsdokument vom Eintrittspunkt ab.
    """
    return queryset.select_related("field_template", "created_by").order_by(
        "field_template__name",
        "sort_order",
        "created_at",
        "pk",
    )


def _build_attachment_index(client, user):
    """Anhaenge der Person, ihrer Faelle und ihrer Ereignisse in **drei** Queries (Refs #1738).

    Rueckgabe: ``{(owner_field, owner_pk): [Attachment, ...]}`` — der Schluessel
    ist derselbe Owner-Dispatch wie im Service-Layer
    (:func:`~core.models.attachment.owner_field_name`).

    Vorher lief pro Fall und pro Ereignis eine eigene ``owner.attachments``-
    Abfrage; bei einer Person mit langer Historie skalierte die Auskunft linear
    mit der Zahl ihrer Datensaetze (G4-Befund). Die Ausgabe bleibt bitgleich:
    sortiert wird zentral in :func:`_ordered_attachments`, die Gruppierung
    erhaelt die Reihenfolge je Owner.

    Der Ereignis-Zweig traegt die **Sensitivity-Filterung** in der Query
    (``visible_to``) — ohne sie laege Anhang-Metadatum eines fuer diesen
    Nutzer unsichtbaren Ereignisses im Speicher, auch wenn es die
    Serialisierung nie erreicht. Der Zugriff bleibt damit deckungsgleich mit
    :func:`_gather_events`; ``is_deleted`` wird bewusst nicht gefiltert, weil
    die Auskunft Live-Bestand **und** Papierkorb ausweist.
    """
    index = {}
    sources = (
        ("client", Attachment.objects.filter(client=client)),
        ("case", Attachment.objects.filter(case__client=client)),
        ("event", Attachment.objects.filter(event__in=Event.objects.visible_to(user).filter(client=client))),
    )
    for owner_field, queryset in sources:
        for attachment in _ordered_attachments(queryset):
            index.setdefault((owner_field, getattr(attachment, f"{owner_field}_id")), []).append(attachment)
    return index


def _gather_attachments(owner, attachment_index=None):
    """Anhang-**Metadaten** eines Owners als ``[{field, filename, ...}, ...]`` (Refs #1738).

    Owner-generisch: ``owner`` ist ein ``Client``, ``Case`` oder ``Event``.
    Mit ``attachment_index`` (aus :func:`_build_attachment_index`) kommen die
    Zeilen aus der gebuendelten Sammel-Abfrage, sonst aus dem geteilten
    Reverse-Accessor ``owner.attachments`` — beide Wege sind owner-gescopt
    (ADR-039 §1) und liefern dieselbe Reihenfolge. Der indexlose Weg bleibt fuer
    Einzelaufrufe erhalten (ein Owner = eine Query, kein N+1).

    **Keine Binaerinhalte** (Produktentscheid M24, konsistent mit
    M4/#1060): die
    Auskunft benennt, welche Datei wann von wem hinterlegt wurde — die Datei
    selbst wird ueber den regulaeren Download herausgegeben. Der Originalname
    wird **entschluesselt** ausgewiesen; ein Fernet-Token waere keine Auskunft
    (dieselbe Begruendung wie in :func:`_gather_entity_dynamic_data`).

    Entschluesselt wird bewusst mit dem Modul-Standard
    ``safe_decrypt(..., default="[verschlüsselt]")`` statt mit
    :func:`~core.services.file_vault.get_original_filename` (Refs #1738, G4):
    dessen Fallback ``"download"`` ist fuer den Content-Disposition-Header
    richtig, in einer Auskunft aber eine Falschaussage — er waere von einer
    echten Datei namens „download" nicht zu unterscheiden und behauptete einen
    Dateinamen, den es nie gab. Bei unlesbarem Token muss die Auskunft sagen,
    dass der Name nicht entschluesselbar ist.

    Die **vollstaendige Versionskette** wird ausgewiesen, inklusive ersetzter
    (``is_current=False`` + ``superseded_at``) und vom Nutzer entfernter Glieder
    (``removed_at``). Soft-deletete Zeilen sind bis zum physischen Loeschen
    (Owner-Lebenszyklus, ADR-039) weiterhin gespeicherte Daten der betroffenen
    Person — sie zu verschweigen waere eine unvollstaendige Auskunft; sie
    unmarkiert zu listen ebenso irrefuehrend. Muster wie ``_soft_delete_meta``
    bei den uebrigen Kategorien.

    **Nach einem ersatzlosen Loeschen (Refs #1874) steht hier nichts mehr** —
    und das ist richtig: ``purge_attachment_chain`` entfernt Datei UND Zeile,
    es gibt keine gespeicherten Daten mehr, ueber die Auskunft zu erteilen
    waere. Eine Grabstein-Zeile waere obendrein kontraproduktiv, weil ihr
    Dateiname (das einzige, was sie noch tragen koennte) haeufig genau die PII
    ist, deren Vernichtung der Vorgang bezweckt. Der Vorgang selbst faellt
    trotzdem nicht aus der Auskunft: ``ATTACHMENT_DELETE`` schreibt auf
    ``target_type="Attachment"`` mit der **Owner**-PK als ``target_id``
    (ADR-039 §5) und wird damit von :func:`_gather_audit_trail` ueber die
    bestehenden Personen-/Fall-/Ereignis-Zweige mitgeliefert — als „Anhang
    geloescht" mit Zeitpunkt, ohne Dateinamen.

    ``mime_type`` liefert den **verifizierten** Typ (libmagic, Refs #1274) und
    faellt nur fuer Bestandszeilen ohne Erkennung auf den browser-gemeldeten
    zurueck — dieselbe Rangfolge wie beim Download.

    Speicherinterna (``storage_filename``, Attachment-PK, verschluesselter
    Namens-Blob) bleiben draussen: sie sind keine Auskunft ueber die Person,
    sondern Ablage-Technik.
    """
    if attachment_index is None:
        attachments = _ordered_attachments(owner.attachments.all())
    else:
        attachments = attachment_index.get((owner_field_name(owner), owner.pk), ())
    return [
        {
            "field": attachment.field_template.name,
            "field_slug": attachment.field_template.slug,
            "filename": safe_decrypt(attachment.original_filename_encrypted),
            "file_size": attachment.file_size,
            "mime_type": attachment.detected_mime or attachment.mime_type,
            "uploaded_at": attachment.created_at.isoformat(),
            "uploaded_by": _username(attachment.created_by),
            "is_current": attachment.is_current,
            "superseded_at": _isoformat(attachment.superseded_at),
            "removed_at": _isoformat(attachment.deleted_at),
        }
        for attachment in attachments
    ]


def _gather_entity_dynamic_data(entity):
    """Dynamische Zusatzfelder als ``[{slug, label, value}, ...]`` (M2, Refs #1058, #1730).

    Entity-neutraler Helfer für **jedes** Modell mit ``document_type`` +
    ``data_json`` (``DynamicDataEncryptionMixin``: ``Client``, ``Case``, …). Löst
    die zuvor wörtlich duplizierten ``_gather_client_dynamic_data`` /
    ``_gather_case_dynamic_data`` ab (Refs #1730, Punkt 4) — Duck-Typing über
    ``document_type_id``/``document_type``/``data_json``.

    Ohne ``document_type`` gibt es keine Feld-Definitionen und damit nichts zu
    exportieren -> ``None`` (der Aufrufer laesst die Keys dann ganz weg, der
    Export bleibt bitgleich zum Stand vor M2).

    Werte werden **entschluesselt** ausgegeben: der Art.-15-Auskunftsanspruch
    zielt auf den Inhalt, ein Fernet-Token waere keine Auskunft. Das Label kommt
    aus dem ``FieldTemplate`` — der nackte Slug ist fuer die betroffene Person
    nicht lesbar. Aus demselben Grund laeuft der Wert durch
    :func:`~core.services.dynamic_data.format_dynamic_display_value`:
    ``SELECT``-/``MULTI_SELECT``-Slugs werden zu ihren Labels, Booleans zu
    ``Ja``/``Nein`` — ein Auskunftsdokument, das ``["wohnungslos"]`` statt
    „Wohnungslos" ausweist, ist fuer die betroffene Person genauso wenig lesbar
    wie der Slug im Schluessel. Sortiert wird nach der konfigurierten
    Feldreihenfolge des Typs (``jsonb`` speichert Schluessel normalisiert, nicht
    in Eingabefolge).

    Nur die dem **aktuellen** Typ zugeordneten Slugs erscheinen hier — verwaiste
    Werte (Slug in ``data_json``, aber nicht mehr am Typ) liefert separat
    :func:`_gather_entity_orphan_data` (Refs #1725), damit dieses Bestandsformat
    stabil bleibt.

    **Datei-Felder** tragen hier nur einen Platzhalter
    (:data:`ATTACHMENT_FIELD_PLACEHOLDER`): ihr Wert in ``data_json`` ist ein
    ``__file__``/``__files__``-Marker, also ein Verweis auf Attachment-Zeilen,
    kein Feldwert. Die lesbaren Angaben liefert :func:`_gather_attachments`.
    Der Marker-Check laeuft **vor** :func:`safe_decrypt` — Marker sind ebenfalls
    ``dict``, aber keine Krypto-Huelle (Muster :func:`_history_value`). Bis M24
    (Refs #1738) war der Zweig entbehrlich, weil FILE an Personen-/Fall-Typen
    gesperrt war (ADR-037 §9 „E8"); mit dem Fall dieser Sperre waere das rohe
    Marker-Dict samt Attachment-UUIDs in der Auskunft gelandet.

    Keine Sensitivity-Filterung: der Export haengt an ``LeadOrAdminRequiredMixin``
    + SudoMode, und Leitung/Admin duerfen ohnehin jede Sensitivitaetsstufe
    sehen (``ROLE_MAX_SENSITIVITY``). Eine Filterung waere hier eine
    Scheinsicherheit, die eine unvollstaendige Auskunft erzeugen koennte.
    """
    from core.services.dynamic_data import build_field_template_lookup, format_dynamic_display_value, is_file_marker

    if entity.document_type_id is None:
        return None
    values = entity.data_json or {}
    field_templates = build_field_template_lookup(entity.document_type, ordered=True)
    entries = []
    for slug, ft in field_templates.items():
        if slug not in values:
            continue
        raw = values[slug]
        if is_file_marker(raw):
            entries.append({"slug": slug, "label": ft.name, "value": ATTACHMENT_FIELD_PLACEHOLDER})
            continue
        decrypted = safe_decrypt(raw) if isinstance(raw, dict) else raw
        entries.append({"slug": slug, "label": ft.name, "value": format_dynamic_display_value(decrypted, ft)})
    return entries


def _gather_entity_orphan_data(entity):
    """Verwaiste dynamische Felder als ``[{slug, label, value}, ...]`` (Refs #1725(a)).

    Verwaist = Slug in ``data_json``, aber nicht (mehr) am aktuellen Typ. Diese
    Werte gehören zur Art.-15-Auskunft (sie liegen weiter gespeichert), dürfen
    aber das stabile ``dynamic_data``-Bestandsformat nicht aufweichen — daher ein
    **separater** Export-Key ``dynamic_data_orphaned``.

    ``None`` ohne ``document_type`` (der Aufrufer lässt den Key dann ganz weg),
    sonst die nach Slug sortierten Waisen — facility-weit über das
    ``FieldTemplate`` (unique je (facility, slug)) mit Label + lesbarer
    Formatierung aufgelöst; fehlt ein Template, ist das Label der Slug.

    **Ohne Feld-Gating**, konsistent zu :func:`_gather_entity_dynamic_data`: der
    Export hängt an ``LeadOrAdminRequiredMixin`` + SudoMode, Leitung/Admin dürfen
    ohnehin jede Sensitivitätsstufe sehen — eine Filterung wäre hier eine
    Scheinsicherheit, die eine unvollständige Auskunft erzeugen könnte.
    """
    from core.models import FieldTemplate
    from core.services.dynamic_data import build_field_template_lookup, format_dynamic_display_value, is_file_marker

    if entity.document_type_id is None:
        return None
    values = entity.data_json or {}
    field_templates = build_field_template_lookup(entity.document_type)
    orphan_slugs = sorted(set(values.keys()) - set(field_templates.keys()))
    if not orphan_slugs:
        return []
    resolved = {
        ft.slug: ft
        for ft in FieldTemplate.objects.filter(
            facility_id=entity.document_type.facility_id,
            slug__in=orphan_slugs,
        )
    }
    entries = []
    for slug in orphan_slugs:
        ft = resolved.get(slug)
        raw = values[slug]
        label = ft.name if ft is not None else slug
        # Refs #1738: Auch verwaiste Werte koennen Datei-Marker sein — FILE-
        # Waisen entstehen regulaer, weil ``purge_orphan_field_data`` sie
        # bewusst ueberspringt (ADR-039 §7). Ohne denselben Zweig wie oben
        # laendete das rohe Marker-Dict hier doch noch in der Auskunft.
        if is_file_marker(raw):
            entries.append({"slug": slug, "label": label, "value": ATTACHMENT_FIELD_PLACEHOLDER})
            continue
        decrypted = safe_decrypt(raw) if isinstance(raw, dict) else raw
        entries.append({"slug": slug, "label": label, "value": format_dynamic_display_value(decrypted, ft)})
    return entries


def _gather_client_fields(client):
    """Serialize the client's master data."""
    fields = {
        "pseudonym": client.pseudonym,
        "contact_stage": client.get_contact_stage_display(),
        "age_cluster": client.get_age_cluster_display(),
        # Refs #1752: ``notes`` ist der gewichtigste bislang fehlende Freitext
        # (docs/security-notes.md stuft ihn selbst als „hoch (Klarname-Risiko)"
        # ein) — ohne ihn war die Auskunft substanziell unvollstaendig.
        "notes": client.notes,
        "is_active": client.is_active,
        # Refs #1752: Anonymisierungs-Status gehoert zur Auskunft — er erklaert,
        # warum Felder generalisiert erscheinen.
        "k_anonymized": client.k_anonymized,
        "created_at": client.created_at.isoformat(),
        "updated_at": client.updated_at.isoformat(),
        "created_by": _username(client.created_by),
        **_soft_delete_meta(client),
    }
    # M2 P7 (Refs #1058): dynamische Felder gehoeren in die Auskunft, sobald der
    # Datensatz einen Typ traegt (P4b-Befund). Ohne Typ bleibt das Dict exakt so
    # wie vor M2 — Bestandstests pinnen genau diesen Schluesselsatz.
    dynamic_data = _gather_entity_dynamic_data(client)
    if dynamic_data is not None:
        fields["document_type"] = client.document_type.name
        fields["dynamic_data"] = dynamic_data
    # Refs #1725(a): verwaiste Werte separat, nur wenn vorhanden — so bleibt das
    # ``dynamic_data``-Bestandsformat bitgleich, wenn es keine Waisen gibt.
    orphaned = _gather_entity_orphan_data(client)
    if orphaned:
        fields["dynamic_data_orphaned"] = orphaned
    return fields


def _serialize_event(event, attachment_index=None):
    """Serialize a single Event instance, decrypting encrypted data_json values.

    Refs #1738 (M24): ``attachments`` kommt hinzu — **nur wenn nicht leer**,
    damit das Bestandsformat fuer Ereignisse ohne Dateien bitgleich bleibt
    (Muster ``dynamic_data_orphaned``). Damit schliesst sich die Bestandsluecke,
    dass der Export bislang keinerlei Anhang-Daten enthielt.

    **Datei-Felder** tragen — wie in :func:`_gather_entity_dynamic_data` — nur
    :data:`ATTACHMENT_FIELD_PLACEHOLDER`; der Marker-Check laeuft **vor**
    :func:`safe_decrypt`, weil Marker ebenfalls ``dict`` sind, aber keine
    Krypto-Huelle. Anders als bei Person und Fall ist das hier **kein**
    Neuland-Fall: Ereignisse tragen seit der E8-Aera (ADR-037 §9) FILE-Marker,
    der gesamte Bestand laeuft also durch diese Schleife. Ohne den Zweig landete
    das rohe Marker-Dict samt Attachment-UUIDs in ``data[slug]`` — im JSON wie
    in der PDF-Spalte „Daten" (G4-Blocker, Refs #1738).

    Diese Schleife arbeitet bewusst **ohne** ``FieldTemplate``-Lookup (anders als
    :func:`_gather_entity_dynamic_data`): das Ereignis-Format ist Bestand,
    ``data_json`` wird slug-treu ausgegeben. Der Marker-Check haengt deshalb am
    Wert, nicht an der Feld-Definition — und traegt damit auch fuer Slugs, die
    inzwischen nicht mehr am Typ haengen.
    """
    from core.services.dynamic_data import is_file_marker

    decrypted_data = {}
    if event.data_json:
        for key, value in event.data_json.items():
            if is_file_marker(value):
                decrypted_data[key] = ATTACHMENT_FIELD_PLACEHOLDER
            elif isinstance(value, dict):
                decrypted_data[key] = safe_decrypt(value)
            else:
                decrypted_data[key] = value
    entry = {
        "document_type": event.document_type.name,
        "occurred_at": event.occurred_at.isoformat(),
        "created_by": _username(event.created_by),
        "data": decrypted_data,
        # Refs #1752: Kleinfelder, die bisher fehlten. ``case``/``episode``
        # ordnen das Ereignis fuer die betroffene Person nachvollziehbar ein,
        # ``is_deleted`` unterscheidet Live-Datensatz von Papierkorb-Eintrag.
        "case": event.case.title if event.case else None,
        "episode": event.episode.title if event.episode else None,
        "is_anonymous": event.is_anonymous,
        "is_deleted": event.is_deleted,
        "created_at": event.created_at.isoformat(),
        "updated_at": event.updated_at.isoformat(),
    }
    attachments = _gather_attachments(event, attachment_index)
    if attachments:
        entry["attachments"] = attachments
    return entry


def _gather_events(client, user, attachment_index=None, *, is_deleted=False):
    """Return (events_data, event_ids) for events visible to ``user``.

    Refs #734: ``user`` ist Pflicht, damit ``visible_to(user)`` greift.
    Ohne den Filter wuerden HIGH-Sensitivity-Events auch bei einem
    Staff-Triggered-Export landen (heute durch Lead/Admin-Mixin auf der
    View entschaerft, aber Service-Layer muss sich nicht auf den Caller
    verlassen).

    ``is_deleted`` waehlt zwischen Live-Bestand und Papierkorb (Refs #1752).
    Soft-deletete Ereignisse sind bis zum Hard-Purge weiterhin gespeicherte
    Daten und gehoeren in die Auskunft — nur eben als eigener Abschnitt, damit
    die betroffene Person Live-Datensatz und Papierkorb unterscheiden kann. Ihr
    ``data_json`` ist beim Soft-Delete geleert; die Auskunft weist deshalb die
    Existenz plus die (redigierte) Historie aus, nicht die Inhalte.
    """
    events = (
        Event.objects.visible_to(user)
        .filter(client=client, is_deleted=is_deleted)
        .select_related("document_type", "created_by", "case", "episode")
        .order_by("-occurred_at", "pk")
    )
    events_data = []
    event_ids = []
    for event in events:
        event_ids.append(event.pk)
        events_data.append(_serialize_event(event, attachment_index))
    return events_data, event_ids


def _gather_cases(client, attachment_index=None):
    """Serialize all cases linked to the client."""
    cases = (
        CaseModel.objects.filter(client=client)
        .select_related("created_by", "lead_user", "document_type", "deleted_by")
        .order_by("-created_at", "pk")
    )
    entries = []
    for case in cases:
        entry = {
            "title": case.title,
            "description": case.description,
            "status": case.get_status_display(),
            "created_at": case.created_at.isoformat(),
            # Refs #1752: ``updated_at`` + Papierkorb-Metadaten fehlten.
            "updated_at": case.updated_at.isoformat(),
            "closed_at": _isoformat(case.closed_at),
            "lead_user": _username(case.lead_user),
            "created_by": _username(case.created_by),
            **_soft_delete_meta(case),
        }
        # M2 P8 (Refs #1058): dynamische Fall-Felder gehoeren in die Auskunft,
        # sobald der Fall einen Typ traegt. Ohne Typ bleibt das Dict exakt so
        # wie vor M2 — Bestandstests pinnen genau diesen Schluesselsatz.
        dynamic_data = _gather_entity_dynamic_data(case)
        if dynamic_data is not None:
            entry["document_type"] = case.document_type.name
            entry["dynamic_data"] = dynamic_data
        # Refs #1725(a): verwaiste Fall-Werte separat, nur wenn vorhanden.
        orphaned = _gather_entity_orphan_data(case)
        if orphaned:
            entry["dynamic_data_orphaned"] = orphaned
        # Refs #1738 (M24): fall-eigene Anhaenge, Key nur wenn nicht leer
        # (Bestandsformat bleibt bitgleich fuer Faelle ohne Dateien).
        attachments = _gather_attachments(case, attachment_index)
        if attachments:
            entry["attachments"] = attachments
        entries.append(entry)
    return entries


# Refs #1752: Anhang-Marker (``__file__``/``__files__``) sind Metadaten der
# Datei-Ablage, keine Feldwerte — in der Historie steht daher nur ein
# Platzhalter. Refs #1738 (M24): Attachment-Metadaten sind inzwischen Teil der
# Auskunft, aber ueber :func:`_gather_attachments` — aus der lebenden Zeile mit
# Feldname, Dateiname und Zeitstempeln. Der Historien-Snapshot bleibt bewusst
# beim Platzhalter: er traegt nur rohe Attachment-IDs, die als Zahlenkolonne
# keine Auskunft waeren und den Bezug zur Datei nicht herstellen.
HISTORY_ATTACHMENT_PLACEHOLDER = "[Anhang]"


def _history_value(raw):
    """Klartext-Wert eines Historien-Snapshot-Felds (Refs #1752).

    Verschluesselte Werte werden entschluesselt — dieselbe Begruendung wie bei
    :func:`_gather_entity_dynamic_data`: ein Fernet-Token waere keine Auskunft.
    Der Marker-Check laeuft **vor** :func:`safe_decrypt`, weil Datei-Marker
    ebenfalls ``dict`` sind, aber keine Krypto-Huelle.
    """
    if isinstance(raw, dict):
        if raw.get("__file__") or raw.get("__files__"):
            return HISTORY_ATTACHMENT_PLACEHOLDER
        return safe_decrypt(raw)
    return raw


def _serialize_history_payload(payload, field_metadata):
    """``data_before``/``data_after`` als ``[{slug, label, value}, ...]``.

    ``field_metadata`` ist der beim Schreiben eingefrorene Label-Snapshot
    (revisionsfeste Darstellung, Refs #824); fehlt ein Slug darin, bleibt der
    Slug selbst das Label. Sortiert nach Slug, weil ``jsonb`` die Schluessel
    normalisiert speichert und die Auskunft reproduzierbar sein soll.

    Redigierte Loeschungen (``build_redacted_delete_history``) speichern nur die
    Feldnamen — dann steht ``value=None``: das Feld existierte, sein Wert wurde
    bereits geloescht.
    """
    if not payload:
        return []

    def label(slug):
        return (field_metadata or {}).get(slug, {}).get("name") or slug

    if payload.get("_redacted"):
        return [{"slug": slug, "label": label(slug), "value": None} for slug in sorted(payload.get("fields", []))]
    return [{"slug": slug, "label": label(slug), "value": _history_value(payload[slug])} for slug in sorted(payload)]


def _gather_event_history(event_ids):
    """Serialize EventHistory entries for the given event IDs.

    Refs #1752: bisher standen hier nur Aktion/Zeitpunkt/User — die eigentlich
    auskunftspflichtigen **Inhalte** (``data_before``/``data_after``) fehlten,
    obwohl sie gespeichert sind und aelteren Stand der eigenen Daten belegen.
    Zusaetzlich benennt jeder Eintrag jetzt sein Ereignis, sonst laesst sich
    eine Aenderung bei mehreren Ereignissen nicht zuordnen.
    """
    history_entries = (
        EventHistory.objects.filter(event_id__in=event_ids)
        .select_related("changed_by", "event", "event__document_type")
        .order_by("-changed_at", "pk")
    )
    return [
        {
            "action": entry.get_action_display(),
            "changed_at": entry.changed_at.isoformat(),
            "changed_by": _username(entry.changed_by),
            "event": {
                "document_type": entry.event.document_type.name,
                "occurred_at": entry.event.occurred_at.isoformat(),
                "is_deleted": entry.event.is_deleted,
            },
            "redacted": bool((entry.data_before or {}).get("_redacted")),
            "data_before": _serialize_history_payload(entry.data_before, entry.field_metadata),
            "data_after": _serialize_history_payload(entry.data_after, entry.field_metadata),
        }
        for entry in history_entries
    ]


def _gather_deletion_requests(facility, client, event_ids):
    """Serialize DeletionRequest entries for the client and the given event IDs.

    Refs #1752: der Filter war hart auf ``target_type="Event"`` verdrahtet,
    obwohl ``request_client_deletion`` (``core.services.client.main``) Antraege
    mit ``target_type="Client"`` schreibt. Der Vier-Augen-Antrag auf Loeschung
    der eigenen Person fehlte damit ausgerechnet in der Auskunft, waehrend der
    PDF-Abschnitt „Loeschantraege" Vollstaendigkeit suggerierte.

    Facility-gescopt ueber den ``FacilityScopedManager`` (Defense-in-Depth
    zusaetzlich zu RLS) — ``target_id`` allein ist zwar UUID-eindeutig, das
    explizite Scoping macht die Absicht aber lesbar. (Andere Quertabellen wie
    Episoden/Ziele/Merklisten scopen stattdessen ueber den Client-/Case-Join
    plus RLS.)
    """
    deletion_requests = (
        DeletionRequest.objects.for_facility(facility)
        .filter(
            Q(target_type=DeletionRequest.TargetType.EVENT, target_id__in=event_ids)
            | Q(target_type=DeletionRequest.TargetType.CLIENT, target_id=client.pk)
        )
        .select_related("requested_by", "reviewed_by")
        .order_by("-created_at", "pk")
    )
    return [
        {
            "target_type": dr.get_target_type_display(),
            "status": dr.get_status_display(),
            "reason": dr.reason,
            "requested_by": _username(dr.requested_by),
            "reviewed_by": _username(dr.reviewed_by),
            "created_at": dr.created_at.isoformat(),
            "reviewed_at": _isoformat(dr.reviewed_at),
        }
        for dr in deletion_requests
    ]


def _rectification_field_label(field_path, field_templates):
    """Lesbares Feld-Label zu einem ``field_path`` eines Berichtigungsantrags (Refs #1796).

    Stammdatenfelder loesen ueber das Modell auf (``verbose_name``, dieselbe
    SSOT wie :func:`~core.services.client.rectification._core_field_label`),
    dynamische Pfade ueber die ``FieldTemplate``-Vorlage des Typs. Bleibt beides
    ergebnislos — etwa weil das Feld nach dem Antrag vom Typ entfernt wurde
    (Waise, Muster Refs #1725) —, steht der rohe Pfad da: er ist unschoen, aber
    wahr, und die Auskunft darf an ihm nicht scheitern.

    ``str()`` ist Pflicht, nicht Kosmetik: ``verbose_name`` ist ein Lazy-Proxy,
    und der JSON-Download serialisiert das Dict.
    """
    from core.services.client.rectification import CORE_FIELDS, DATA_JSON_PREFIX, _core_field_label

    if field_path in CORE_FIELDS:
        return str(_core_field_label(field_path))
    if field_path.startswith(DATA_JSON_PREFIX):
        ft = field_templates.get(field_path[len(DATA_JSON_PREFIX) :])
        if ft is not None:
            return ft.name
    return field_path


def _rectification_value(field_path, stored, field_templates):
    """Lesbarer Klartext-Wert einer ``old_value``/``new_value``-Spalte (Refs #1796).

    Entschluesselt wird ueber :func:`~core.services.client.rectification.decode_rectification_value`
    (Fernet + ``json.loads``); ``None`` bleibt ``None`` — genau so liegen die
    Spalten nach der Anonymisierung (Refs #1792), und eine redigierte Zeile muss
    die Auskunft ohne Absturz passieren.

    Anschliessend dieselbe Lesbarkeits-Regel wie in
    :func:`_gather_entity_dynamic_data`: ein gespeicherter Code waere keine
    Auskunft. ``age_cluster`` loest auf sein Choices-Label auf, dynamische Werte
    laufen durch :func:`~core.services.dynamic_data.format_dynamic_display_value`
    (SELECT-Slug -> Label, Bool -> Ja/Nein). Unbekannte Codes und ein fehlendes
    Template fallen auf den Rohwert zurueck — inklusive des
    ``safe_decrypt``-Platzhalters bei korruptem Token.
    """
    from core.services.client.rectification import DATA_JSON_PREFIX, decode_rectification_value
    from core.services.dynamic_data import format_dynamic_display_value

    value = decode_rectification_value(stored)
    if value is None:
        return None
    if field_path == "age_cluster":
        try:
            return Client.AgeCluster(value).label
        except ValueError:
            return value
    if field_path.startswith(DATA_JSON_PREFIX):
        return format_dynamic_display_value(value, field_templates.get(field_path[len(DATA_JSON_PREFIX) :]))
    return value


def _gather_rectification_requests(facility, client):
    """Serialize Art.-16-Berichtigungsantraege der Person (Refs #1796, ADR-041).

    Der Antrag ist selbst gespeicherte Information ueber die betroffene Person:
    er benennt das strittige Feld, den damaligen und den gewuenschten Wert, die
    fachliche Begruendung und den Pruefvermerk. Ohne ihn wies die Auskunft zwar
    Art.-17-Loeschantraege aus, den Art.-16-Vorgang aber gar nicht.

    **Alle Status** (offen/genehmigt/abgelehnt), wie bei
    :func:`_gather_deletion_requests` — gerade ein abgelehnter Antrag ist fuer
    die betroffene Person die aussagekraeftigste Zeile.

    Facility-gescopt ueber den ``FacilityScopedManager`` (Defense-in-Depth
    zusaetzlich zu RLS), Muster :func:`_gather_deletion_requests`.

    Die Feld-Vorlagen werden nur geladen, wenn ueberhaupt ein dynamischer Pfad
    vorkommt — ohne den Zweig kostete jede Auskunft eine Query mehr. Sie laufen
    bewusst **ohne** Sensitivity-Gate (kein ``rectifiable_fields``, das nach der
    Sicht des aufrufenden Nutzers filtert): dieselbe Begruendung wie in
    :func:`_gather_entity_dynamic_data` — der Export haengt an
    ``LeadOrAdminRequiredMixin`` + SudoMode, und eine Filterung erzeugte hier
    nur eine unvollstaendige Auskunft.
    """
    from core.services.client.rectification import DATA_JSON_PREFIX, decode_rectification_text
    from core.services.dynamic_data import build_field_template_lookup

    requests = list(
        RectificationRequest.objects.for_facility(facility)
        .filter(target_client=client)
        .select_related("requested_by", "reviewed_by")
        .order_by("-created_at", "pk")
    )
    field_templates = {}
    if client.document_type_id and any(rr.field_path.startswith(DATA_JSON_PREFIX) for rr in requests):
        field_templates = build_field_template_lookup(client.document_type)
    return [
        {
            "field_path": rr.field_path,
            "field_label": _rectification_field_label(rr.field_path, field_templates),
            "status": rr.get_status_display(),
            "old_value": _rectification_value(rr.field_path, rr.old_value, field_templates),
            "new_value": _rectification_value(rr.field_path, rr.new_value, field_templates),
            # ``None`` (nach Anonymisierung redigiert, Refs #1792) bleibt
            # ``None`` — ein echter leerer Freitext bleibt ``""``. Die
            # Auskunft muss "redigiert" von "leer eingereicht" unterscheiden
            # koennen (Review-Befund #1796).
            "reason": decode_rectification_text(rr.reason) if rr.reason is not None else None,
            "review_note": decode_rectification_text(rr.review_note) if rr.review_note is not None else None,
            "requested_by": _username(rr.requested_by),
            "reviewed_by": _username(rr.reviewed_by),
            "created_at": _isoformat(rr.created_at),
            "reviewed_at": _isoformat(rr.reviewed_at),
        }
        for rr in requests
    ]


def _gather_workitems(client):
    """Serialize all work items linked to the client."""
    workitems = (
        WorkItem.objects.filter(client=client)
        .select_related("created_by", "assigned_to", "deleted_by")
        .order_by("-created_at", "pk")
    )
    return [
        {
            "title": wi.title,
            "description": wi.description,
            # Refs #1752: bisher fehlende Kleinfelder — Typ, handelnde
            # Personen, Fristen und Papierkorb-Status.
            "item_type": wi.get_item_type_display(),
            "status": wi.get_status_display(),
            "priority": wi.get_priority_display(),
            "created_by": _username(wi.created_by),
            "assigned_to": _username(wi.assigned_to),
            "created_at": wi.created_at.isoformat(),
            "updated_at": wi.updated_at.isoformat(),
            "completed_at": _isoformat(wi.completed_at),
            "due_date": _isoformat(wi.due_date),
            "remind_at": _isoformat(wi.remind_at),
            "recurrence": wi.get_recurrence_display(),
            **_soft_delete_meta(wi),
        }
        for wi in workitems
    ]


def _gather_episodes(client):
    """Serialize all episodes of the client's cases (Refs #1752).

    Episoden waren komplett aus der Auskunft gefallen, obwohl sie Freitext
    (``title``/``description``) zur Person tragen. Der Zugriff laeuft ueber den
    ``case``-Join — ``Episode`` haengt nicht direkt an der Facility, RLS greift
    hier als JOIN-Policy.
    """
    episodes = (
        Episode.objects.filter(case__client=client)
        .select_related("case", "created_by", "deleted_by")
        .order_by("-started_at", "pk")
    )
    return [
        {
            "case": episode.case.title,
            "title": episode.title,
            "description": episode.description,
            "started_at": episode.started_at.isoformat(),
            "ended_at": _isoformat(episode.ended_at),
            "created_at": episode.created_at.isoformat(),
            "created_by": _username(episode.created_by),
            **_soft_delete_meta(episode),
        }
        for episode in episodes
    ]


def _gather_outcome_goals(client):
    """Serialize outcome goals + milestones of the client's cases (Refs #1752).

    Wirkungsziele beschreiben die Arbeit *an* der Person und tragen Freitext;
    die Meilensteine haengen daran und werden mitgeliefert (ein Ziel ohne
    seine Schritte waere eine halbe Auskunft). ``Prefetch`` mit explizitem
    ``order_by`` haelt die Reihenfolge deterministisch und vermeidet N+1.
    """
    goals = (
        OutcomeGoal.objects.filter(case__client=client)
        .select_related("case", "created_by")
        .prefetch_related(Prefetch("milestones", queryset=Milestone.objects.order_by("sort_order", "pk")))
        .order_by("-created_at", "pk")
    )
    return [
        {
            "case": goal.case.title,
            "title": goal.title,
            "description": goal.description,
            "is_achieved": goal.is_achieved,
            "achieved_at": _isoformat(goal.achieved_at),
            "created_at": goal.created_at.isoformat(),
            "created_by": _username(goal.created_by),
            "milestones": [
                {
                    "title": milestone.title,
                    "is_completed": milestone.is_completed,
                    "completed_at": _isoformat(milestone.completed_at),
                    "sort_order": milestone.sort_order,
                    "created_at": milestone.created_at.isoformat(),
                }
                for milestone in goal.milestones.all()
            ],
        }
        for goal in goals
    ]


def _gather_activities(facility, client, event_ids):
    """Serialize the activity feed entries about the client (Refs #1752).

    Der Zeitstrom traegt Freitext-``summary`` zur Person („… für <pseudonym>",
    „Aufgabe: <titel>"). Die Ziel-Menge deckt sich mit der Redaktions-Logik in
    :func:`core.services.client.main._redact_activities` (Client-, Event-,
    WorkItem- und seit #1751 auch Case-Targets).

    ``actor`` bleibt hier **enthalten** — anders als beim Audit-Auszug
    (:func:`_gather_audit_trail`). Der Zeitstrom dokumentiert eine einzelne
    fachliche Handlung am Datensatz, genau wie das bereits exportierte
    ``created_by``/``changed_by``; erst das vollstaendige Zugriffsprotokoll
    kippt in ein Bewegungsprofil der Beschaeftigten.
    """
    targets = Q(target_type=ContentType.objects.get_for_model(client.__class__), target_id=client.pk)
    case_ids = list(client.cases.values_list("pk", flat=True))
    if case_ids:
        targets |= Q(target_type=ContentType.objects.get_for_model(CaseModel), target_id__in=case_ids)
    if event_ids:
        targets |= Q(target_type=ContentType.objects.get_for_model(Event), target_id__in=event_ids)
    workitem_ids = list(client.work_items.values_list("pk", flat=True))
    if workitem_ids:
        targets |= Q(target_type=ContentType.objects.get_for_model(WorkItem), target_id__in=workitem_ids)

    activities = (
        Activity.objects.for_facility(facility)
        .filter(targets)
        .select_related("actor", "target_type")
        .order_by("-occurred_at", "pk")
    )
    return [
        {
            "verb": activity.get_verb_display(),
            "summary": activity.summary,
            "target": activity.target_type.name,
            "actor": _username(activity.actor),
            "occurred_at": activity.occurred_at.isoformat(),
        }
        for activity in activities
    ]


def _gather_recent_visits(client):
    """Serialize recent-visit markers for the client (Refs #1752).

    ``RecentClientVisit`` ist gespeicherte Information *ueber die Person* („ihre
    Akte steht auf N Merklisten, zuletzt am …") und gehoert deshalb in die
    Auskunft.

    **Ohne ``user``** — bewusster Produktentscheid vom 2026-08-03: die Zeile
    beantwortet „wer hat die Personenakte aufgerufen" und ist damit zugleich
    ein Zugriffsprotokoll ueber Beschaeftigte. Dieselbe Abwaegung wie beim
    Audit-Auszug (:func:`_gather_audit_trail`); Zeitpunkt und Merkliste-Flag
    bleiben, die Beschaeftigten-Identitaet nicht.
    """
    visits = RecentClientVisit.objects.filter(client=client).order_by("-visited_at", "pk")
    return [
        {
            "visited_at": visit.visited_at.isoformat(),
            "is_favorite": visit.is_favorite,
        }
        for visit in visits
    ]


def _gather_legal_holds(facility, client, event_ids):
    """Serialize legal holds on the client or her events (Refs #1752).

    Art.-18-Relevanz: ein Legal Hold erklaert der betroffenen Person, warum ihre
    Daten trotz abgelaufener Aufbewahrungsfrist noch existieren. Ohne diese
    Kategorie wirkt die Aufbewahrung willkuerlich.
    """
    holds = (
        LegalHold.objects.for_facility(facility)
        .filter(Q(target_type="Client", target_id=client.pk) | Q(target_type="Event", target_id__in=event_ids))
        .select_related("created_by", "dismissed_by")
        .order_by("-created_at", "pk")
    )
    return [
        {
            "target_type": hold.target_type,
            "reason": hold.reason,
            "is_active": hold.is_active,
            "expires_at": _isoformat(hold.expires_at),
            "created_at": hold.created_at.isoformat(),
            "created_by": _username(hold.created_by),
            "dismissed_at": _isoformat(hold.dismissed_at),
            "dismissed_by": _username(hold.dismissed_by),
        }
        for hold in holds
    ]


def _gather_retention_proposals(facility, client, event_ids):
    """Serialize pending retention proposals for the client's records (Refs #1752).

    Gegenstueck zum Legal Hold: der Loeschvorschlag benennt die anstehende
    Loeschung samt Frist und Kategorie. ``details`` traegt ausschliesslich Daten
    der betroffenen Person selbst (Pseudonym, Kontaktstufe, Dokumentationstyp,
    Zeitpunkt) und wird deshalb unveraendert ausgewiesen.

    ``RetentionProposal.TargetType`` kennt heute nur ``Event``; der
    ``Client``-Zweig ist mitgefiltert, damit eine spaetere Erweiterung der
    Werteliste die Auskunft nicht erneut stillschweigend unvollstaendig macht.
    """
    proposals = (
        RetentionProposal.objects.for_facility(facility)
        .filter(Q(target_type="Client", target_id=client.pk) | Q(target_type="Event", target_id__in=event_ids))
        .order_by("deletion_due_at", "pk")
    )
    return [
        {
            "target_type": proposal.target_type,
            "status": proposal.get_status_display(),
            "retention_category": proposal.retention_category,
            "proposed_at": proposal.proposed_at.isoformat(),
            "deletion_due_at": proposal.deletion_due_at.isoformat(),
            "deferred_until": _isoformat(proposal.deferred_until),
            "defer_count": proposal.defer_count,
            "details": proposal.details,
        }
        for proposal in proposals
    ]


# Refs #1752: Lesbare Kategorie je Audit-Zeile. Der rohe ``target_type``
# („Client-JSON", „Attachment") ist interne Technik-Sprache; die Auskunft
# soll sagen, *worauf* sich der Zugriff bezog.
AUDIT_CATEGORY_OTHER = "Sonstiges"
# ADR-039 §5: Die Anhang-Domaene schreibt seit M24 (Refs #1738)
# ``target_type="Attachment"``; Alt-Zeilen tragen unmigrierbar (HMAC-Kette)
# weiter ``"EventAttachment"`` — die Zuordnung muss BEIDE Strings kennen.
# Die Reihenfolge ist ordnungssicher — "EventAttachment".startswith("Attachment")
# ist False, der Alt-String kann also nie in den neuen Praefix rutschen; beide
# Eintraege muenden ohnehin im selben Label.
#
# Refs #1796: ``RectificationRequest`` (ADR-041) kollidiert mit keinem der
# uebrigen Praefixe — die Reihenfolge bleibt damit unerheblich, der Eintrag
# steht am Ende.
AUDIT_CATEGORIES = (
    ("EventAttachment", "Anhang"),
    ("Attachment", "Anhang"),
    ("Event", "Ereignis"),
    ("Client", "Person"),
    ("RectificationRequest", "Berichtigung"),
)


def _audit_category(target_type):
    """Kategorie-Label zu einem Audit-``target_type`` (laengstes Praefix gewinnt)."""
    for prefix, label in AUDIT_CATEGORIES:
        if target_type.startswith(prefix):
            return label
    return AUDIT_CATEGORY_OTHER


def _gather_audit_trail(facility, client, event_ids):
    """Serialize the audit entries about the client — without actor identity.

    Refs #1752 (Produktentscheid 2026-08-03): Der Audit-Auszug fehlte komplett;
    ``docs/admin-guide.md`` setzte stillschweigend voraus, dass eine
    Anwendungsbetreuung ``/audit/`` haendisch durchsucht und Treffer ergaenzt.
    Er gehoert in die Auskunft — VIEW_QUALIFIED, EXPORT, DOWNLOAD, CLIENT_*
    und alles Weitere, dessen Ziel die Person oder eines ihrer Ereignisse ist.

    **Bewusst ohne Beschaeftigten-Identitaet.** ``user``, ``actor``,
    ``ip_address`` und ``detail`` bleiben draussen:

    * „Wer griff wann zu" ist zugleich ein Bewegungs-/Leistungsprofil der
      Beschaeftigten — Art. 15 Abs. 4 schuetzt die Rechte Dritter. Anders als
      beim einzelnen ``created_by`` eines Datensatzes entsteht hier ein
      vollstaendiges Zugriffsprotokoll ueber Personen, die selbst nicht die
      Auskunftsberechtigten sind.
    * ``detail`` traegt je nach Aktion Anhang-Dateinamen
      (``audit_security_violation``) und Roh-IDs — es bleibt als **Feld**
      draussen. Die Anhang-Angaben liefert seit M24 (Refs #1738) statt dessen
      :func:`_gather_attachments` aus der lebenden Zeile; ``detail`` dient hier
      nur noch der Zuordnung (s. u.), nicht der Ausgabe.

    Aktion, Zeitpunkt und Kategorie bleiben erhalten: die betroffene Person
    erfaehrt, *dass* und *wann* auf welche Art auf ihre Daten zugegriffen wurde.

    Direkt ueber ``target_id`` zugeordnet werden Zeilen auf **alle drei**
    Owner-Sorten: Person, Fall und Ereignis. Der Fall-Zweig gehoert seit M24
    dazu, weil die Anhang-Domaene ``target_id`` auf die Owner-PK setzt
    (ADR-039 §5) — ein ``SECURITY_VIOLATION`` an einem fall-eigenen Upload
    traegt also die Fall-PK. Aus demselben Grund kommen seit Refs #1796 die
    **Berichtigungsantraege** dazu: ``RECTIFICATION_REQUESTED/APPROVED/REJECTED``
    zeigen auf die Antrags-PK (ADR-041), die vorher keinen Zweig traf.

    Download-Zeilen zeigen auf den Anhang, nicht auf seinen Owner; sie werden
    ueber ``detail`` zugeordnet. Das kommt ohne Lesen der Attachment-Tabelle aus
    und traegt auch dann noch, wenn die Anhaenge beim Loeschen des Owners
    laengst hart entfernt wurden. **Zwei Schemata** (ADR-039 §5): neue Zeilen
    tragen ``owner_type``/``owner_id`` — der Owner kann eine Person, ein Fall
    oder ein Ereignis sein —, Alt-Zeilen weiterhin ``event_id``. Alt-Zeilen sind
    HMAC-verkettet und append-only, also nie migrierbar; beide Zweige muessen
    daher dauerhaft nebeneinander stehen. Ohne den Owner-Zweig fiele der
    Download einer personen- oder falleigenen Datei komplett aus dem
    Zugriffsprotokoll der betroffenen Person heraus.
    """
    event_id_strings = [str(pk) for pk in event_ids]
    case_id_strings = [str(pk) for pk in client.cases.values_list("pk", flat=True)]
    owner_id_strings = [str(client.pk), *case_id_strings, *event_id_strings]
    targets = Q(target_id=str(client.pk))
    # Refs #1796: Der Art.-16-Workflow (ADR-041) setzt ``target_id`` auf die
    # ANTRAGS-PK, nicht auf die Person — genau wie die Anhang-Domaene auf die
    # Owner-PK zeigt. Ohne diesen Zweig fiel der komplette Berichtigungsvorgang
    # (beantragt/genehmigt/abgelehnt) aus dem Zugriffsprotokoll der betroffenen
    # Person heraus. Ueber-Matching ist ausgeschlossen, weil die IDs ueber
    # ``target_client`` gezogen werden (plus ``for_facility`` + RLS).
    rectification_id_strings = [
        str(pk)
        for pk in RectificationRequest.objects.for_facility(facility)
        .filter(target_client=client)
        .values_list("pk", flat=True)
    ]
    if rectification_id_strings:
        targets |= Q(target_id__in=rectification_id_strings)
    if case_id_strings:
        # Refs #1738 (G4): Die Anhang-Domaene setzt ``target_id`` auf die
        # **Owner**-PK (ADR-039 §5, AP2) — bei fall-eigenen Uploads also auf den
        # Fall. Ohne diesen Zweig fiel jede fall-gezielte Zeile (etwa
        # ``SECURITY_VIOLATION`` bei abgelehnter Endung) aus dem Protokoll: die
        # Fall-IDs wurden bis dahin ausschliesslich im DOWNLOAD/``owner_id``-
        # Zweig verwendet. Ueber-Matching ist ausgeschlossen, weil die IDs aus
        # ``client.cases`` stammen (plus ``for_facility`` + RLS).
        targets |= Q(target_id__in=case_id_strings)
    if event_id_strings:
        targets |= Q(target_id__in=event_id_strings)
        # Alt-Schema (vor ADR-039 §5) — unmigrierbare Bestandszeilen.
        targets |= Q(action=AuditLog.Action.DOWNLOAD, detail__event_id__in=event_id_strings)
    targets |= Q(action=AuditLog.Action.DOWNLOAD, detail__owner_id__in=owner_id_strings)
    entries = AuditLog.objects.for_facility(facility).filter(targets).order_by("-timestamp", "pk")
    return [
        {
            "action": entry.get_action_display(),
            "category": _audit_category(entry.target_type),
            "target_type": entry.target_type,
            "timestamp": entry.timestamp.isoformat(),
        }
        for entry in entries
    ]


def _build_export_meta(facility):
    """Return export metadata (timestamp + resolved facility name)."""
    return {
        "timestamp": timezone.now().isoformat(),
        "facility_name": resolve_facility_display_name(facility),
    }


def export_client_data(client, facility, user):
    """Collect all personal data for a client visible to ``user``.

    Refs #734: ``user`` ist Pflicht-Parameter — die Sensitivity-
    Filterung greift via ``Event.objects.visible_to(user)`` in
    ``_gather_events``. Lead/Admin sehen alles, Staff sieht
    NORMAL+ELEVATED, Assistant nur NORMAL.

    Refs #1752: Ereignisse im Papierkorb kommen als eigener Abschnitt dazu;
    alle ereignisbezogenen Kategorien (Historie, Loeschantraege, Legal Holds,
    Loeschvorschlaege, Zeitstrom) arbeiten anschliessend auf **beiden** Mengen
    — sonst fiele die Historie eines geloeschten Ereignisses stillschweigend
    weg, obwohl sie bis zum Hard-Purge gespeichert bleibt.
    """
    # Refs #1738 (G4): Anhaenge einmal gebuendelt holen (drei Queries), statt je
    # Fall und je Ereignis eine eigene — der Index wird an alle drei
    # Serialisierungs-Pfade durchgereicht.
    attachment_index = _build_attachment_index(client, user)
    events_data, event_ids = _gather_events(client, user, attachment_index)
    deleted_events_data, deleted_event_ids = _gather_events(client, user, attachment_index, is_deleted=True)
    all_event_ids = [*event_ids, *deleted_event_ids]
    return {
        "client": _gather_client_fields(client),
        "events": events_data,
        "deleted_events": deleted_events_data,
        # Refs #1738 (M24): personen-eigene Anhaenge als EIGENE Top-Level-
        # Kategorie — nicht als Unterfeld von ``client``. Der abgeleitete
        # PDF-Guard (``test_every_category_has_its_own_pdf_section``) iteriert
        # ueber Top-Level-Keys; als Unterfeld liefe der Block an ihm vorbei.
        # Fall- und ereignis-eigene Anhaenge haengen dagegen sinnvollerweise am
        # jeweiligen Datensatz (``cases[i]``/``events[i]``) und haben dort
        # eigene PDF-Nadeln.
        "attachments": _gather_attachments(client, attachment_index),
        "cases": _gather_cases(client, attachment_index),
        "episodes": _gather_episodes(client),
        "outcome_goals": _gather_outcome_goals(client),
        "event_history": _gather_event_history(all_event_ids),
        "deletion_requests": _gather_deletion_requests(facility, client, all_event_ids),
        # Refs #1796: Art.-16-Antraege neben den Art.-17-Antraegen — beides sind
        # Vorgaenge ueber die Person, beide mit Vier-Augen-Nachweis.
        "rectification_requests": _gather_rectification_requests(facility, client),
        "legal_holds": _gather_legal_holds(facility, client, all_event_ids),
        "retention_proposals": _gather_retention_proposals(facility, client, all_event_ids),
        "work_items": _gather_workitems(client),
        "activities": _gather_activities(facility, client, all_event_ids),
        "recent_visits": _gather_recent_visits(client),
        "audit_trail": _gather_audit_trail(facility, client, all_event_ids),
        "export_meta": _build_export_meta(facility),
    }


def export_client_data_pdf(client, facility, user):
    """Generate PDF from client data. Returns bytes.

    Refs #1752: Der Kontext wird aus :func:`export_client_data` **abgeleitet**
    statt Key fuer Key aufgezaehlt. Vorher konnte eine neue JSON-Kategorie
    still am PDF vorbeilaufen — JSON und PDF muessen denselben Umfang abdecken
    (das PDF darf zusammenfassen, aber keine Kategorie auslassen).
    """
    data = export_client_data(client, facility, user)
    context = {key: value for key, value in data.items() if key != "export_meta"}
    context["facility_name"] = data["export_meta"]["facility_name"]
    context["generated_at"] = timezone.now()
    html = render_to_string("core/export/client_data_pdf.html", context)
    return weasyprint.HTML(string=html, url_fetcher=restricted_url_fetcher).write_pdf()
