"""Löschpfade und Lösch-Blocker der Formular-Konfiguration (Refs #1832)."""

from __future__ import annotations

import uuid

from django.db import transaction
from django.db.models import ProtectedError, Q
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from core.models import (
    Attachment,
    AuditLog,
    Case,
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldGroup,
    FieldTemplate,
    QuickTemplate,
)
from core.services.audit import log_audit_event
from core.services.config.block_order import iter_blocks_in_order


class ConfigDeleteBlocked(Exception):  # noqa: N818 — Plan-Vorgabe (#1056); UI-Semantik „blockiert", kein Error-Suffix
    """Löschung einer Konfiguration ist wegen referenzierender Daten blockiert.

    ``reasons`` bündelt alle UI-tauglichen, deutschen Begründungen — es können
    mehrere gleichzeitig zutreffen (z.B. Feld-Zuordnung *und* Anhänge *und*
    Event-Daten).
    """

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


def _append_trash_share(
    reason: str, trashed_count: int, *, of_attachments: bool = False, of_cases: bool = False
) -> str:
    """Ergänzt eine Blocker-Begründung um den Papierkorb-Anteil.

    Refs #1737: Die Blocker zählen über den Default-Manager, der ``is_deleted``
    bewusst nicht filtert (:class:`~core.models.managers.FacilityScopedManager`).
    Soft-gelöschte Personen blockieren also mit — die Meldung verlangte dafür
    aber einen unmöglichen Schritt („müsste zuerst geändert werden"), weil ein
    Datensatz im Papierkorb im Fach-UI gar nicht mehr erreichbar ist. Der
    Zusatz benennt den Anteil; den Ausweg (Papierkorb öffnen, wiederherstellen
    oder endgültig anonymisieren) zeigen die Lösch-Dialoge als Link.

    ``of_cases`` (Refs #1818, ADR-044): Seit dem Fall-Löschpfad gibt es einen
    **zweiten** Papierkorb, und er erzeugt dieselbe Sackgasse — ein Fall im
    Papierkorb hält seinen ``document_type`` per ``PROTECT`` und seine
    ``data_json``-Slugs fest, ist über die Fallliste aber nicht mehr
    erreichbar. Der Satz nennt dann Fälle statt Personen; der Ausweg ist der
    Fall-Papierkorb (Link im Hinweis-Partial).

    ``of_attachments`` (M24/AP4, Refs #1738): Der zweite Blocker-Grund zählt
    **Dateianhänge**, nicht Personen — „Davon liegt 1 Person im Papierkorb"
    bezöge sich dort auf die falsche Größe. Der Satz nennt deshalb die Anhänge;
    die Bezugsgröße Papierkorb (und damit der Ausweg) bleibt dieselbe. Die
    Personen-Formulierung ist unverändert — Bestandsübersetzungen bleiben
    gültig.
    """
    if not trashed_count:
        return reason
    if of_cases:
        share = ngettext(
            "Davon liegt %(t)d Fall im Papierkorb.",
            "Davon liegen %(t)d Fälle im Papierkorb.",
            trashed_count,
        ) % {"t": trashed_count}
    elif of_attachments:
        share = ngettext(
            "Davon gehört %(t)d Anhang zu einer Person im Papierkorb.",
            "Davon gehören %(t)d Anhänge zu Personen im Papierkorb.",
            trashed_count,
        ) % {"t": trashed_count}
    else:
        share = ngettext(
            "Davon liegt %(t)d Person im Papierkorb.",
            "Davon liegen %(t)d Personen im Papierkorb.",
            trashed_count,
        ) % {"t": trashed_count}
    return f"{reason} {share}"


def _trashed_client_ids_with_slug(field_template: FieldTemplate) -> set:
    """PKs soft-gelöschter Personen mit einem Wert unter diesem Slug."""
    if not field_template.slug:
        return set()
    return set(
        Client.objects.filter(
            facility_id=field_template.facility_id,
            data_json__has_key=field_template.slug,
            is_deleted=True,
        ).values_list("pk", flat=True)
    )


def _trashed_client_ids_with_attachment(field_template: FieldTemplate) -> set:
    """PKs soft-gelöschter Personen, deren **Anhänge** diese Vorlage blockieren.

    M24/AP4 (Refs #1738, ADR-039): ``Attachment.field_template`` ist ``PROTECT``
    — seit dem Owner-Umbau hängt ein solcher Anhang an der Person selbst, an
    einem ihrer Ereignisse oder an einem ihrer Fälle. Alle drei Wege führen auf
    dieselbe Person; ``set``-Vereinigung entfernt Doppelzählungen.
    """
    base = Attachment.objects.filter(field_template=field_template)
    ids = set(base.filter(client__is_deleted=True).values_list("client_id", flat=True))
    ids |= set(base.filter(event__client__is_deleted=True).values_list("event__client_id", flat=True))
    ids |= set(base.filter(case__client__is_deleted=True).values_list("case__client_id", flat=True))
    return ids


def _trashed_case_ids_with_slug(field_template: FieldTemplate) -> set:
    """PKs von Fällen im Papierkorb mit einem Wert unter diesem Slug (Refs #1818)."""
    if not field_template.slug:
        return set()
    return set(
        Case.objects.filter(
            facility_id=field_template.facility_id,
            is_deleted=True,
            data_json__has_key=field_template.slug,
        ).values_list("pk", flat=True)
    )


def _trashed_case_ids_with_attachment(field_template: FieldTemplate) -> set:
    """PKs von Fällen im Papierkorb, deren **Anhänge** diese Vorlage blockieren.

    Gegenstück zu :func:`_trashed_client_ids_with_attachment` auf der Fall-Achse:
    ``Attachment.field_template`` ist ``PROTECT``, und seit ADR-039 kann ein
    Anhang direkt am Fall hängen. Liegt dieser Fall im Papierkorb, ist er über
    die Fallliste nicht mehr erreichbar — genau die Sackgasse, für die #1737
    den Personen-Ausweg gebaut hat.
    """
    return set(
        Attachment.objects.filter(field_template=field_template, case__is_deleted=True).values_list(
            "case_id", flat=True
        )
    )


def field_template_trashed_case_count(field_template: FieldTemplate) -> int:
    """Fälle im Papierkorb, die das Löschen dieser Vorlage blockieren (Refs #1818).

    Speist den zweiten Ausweg-Hinweis („Fall-Papierkorb öffnen") der
    Lösch-Dialoge. Zwei Quellen wie beim Personen-Gegenstück: ein Wert unter dem
    Slug im ``data_json`` ODER ein Anhang, der die Vorlage per ``PROTECT``
    festhält. Distinct über beide — derselbe Fall zählt nicht doppelt.
    """
    return len(_trashed_case_ids_with_slug(field_template) | _trashed_case_ids_with_attachment(field_template))


def field_template_trashed_client_count(field_template: FieldTemplate) -> int:
    """Personen im Papierkorb, die das Löschen dieser Vorlage blockieren.

    Speist den Ausweg-Hinweis („Papierkorb öffnen") der Lösch-Dialoge.

    **Zwei Quellen** (M24/AP4, Refs #1738 — #1737 hatte die zweite bereits
    angekündigt): ein Wert unter dem Slug im ``data_json`` ODER ein
    ``Attachment``, das die Vorlage per ``PROTECT`` festhält. Ohne die zweite
    Quelle blieb eine reine Anhang-Blockade ohne sichtbaren Ausweg: der Dialog
    nannte die Anhänge, verschwieg aber, dass ihre Person im Papierkorb liegt
    und über die Personenliste nicht mehr erreichbar ist.

    Distinct über beide Quellen — dieselbe Person zählt nicht doppelt.
    """
    return len(_trashed_client_ids_with_slug(field_template) | _trashed_client_ids_with_attachment(field_template))


def field_template_trashed_attachment_count(field_template: FieldTemplate) -> int:
    """Blockierende **Anhänge**, die zu einer Person im Papierkorb gehören.

    Bezugsgröße des Anhang-Blocker-Satzes (M24/AP4, Refs #1738); zählt Anhänge,
    nicht Personen. Owner-generisch: Person, Ereignis der Person oder Fall der
    Person.
    """
    return (
        Attachment.objects.filter(field_template=field_template)
        .filter(Q(client__is_deleted=True) | Q(event__client__is_deleted=True) | Q(case__client__is_deleted=True))
        .count()
    )


def delete_document_type(request, doc_type: DocumentType) -> None:
    """Löscht einen DocumentType — blockiert bei referenzierenden Events/Personen/Fällen (PROTECT).

    ``Event.document_type``/``Client.document_type``/``Case.document_type`` sind
    alle ``PROTECT``; ein Delete mit Bestandsreferenzen würde eine
    ``ProtectedError`` werfen. Wir zählen **alle drei** Referenzarten vorab und
    melden sie gemeinsam als :class:`ConfigDeleteBlocked` (Muster
    :func:`field_template_delete_blockers`: alle zutreffenden Gründe, nicht nur
    der erste — ein Typ kann z.B. sowohl Events als auch Personen referenziert
    haben, wenn ``entity_type`` erst nachträglich sinnvoll genutzt wurde).

    Refs #1818: Personen- **und** Fall-Blocker tragen ihren Papierkorb-Anteil im
    Satz. Beide Zähler laufen über den Default-Manager, der ``is_deleted`` nicht
    filtert — ein Datensatz im Papierkorb blockiert also mit, ist im Fach-UI
    aber nicht mehr erreichbar. Ohne den Zusatz verlangt die Meldung einen
    Schritt, den niemand gehen kann.
    """
    reasons: list[str] = []

    event_count = Event.objects.filter(document_type=doc_type).count()
    if event_count:
        reasons.append(
            _("%(n)d Ereignis(se) verweisen auf diesen Dokumentationstyp und müssten zuerst entfernt werden.")
            % {"n": event_count}
        )

    client_count = Client.objects.filter(document_type=doc_type).count()
    if client_count:
        reasons.append(
            _append_trash_share(
                ngettext(
                    "%(n)d Person ist diesem Dokumentationstyp zugeordnet und müsste zuerst geändert werden.",
                    "%(n)d Personen sind diesem Dokumentationstyp zugeordnet und müssten zuerst geändert werden.",
                    client_count,
                )
                % {"n": client_count},
                Client.objects.filter(document_type=doc_type, is_deleted=True).count(),
            )
        )

    case_count = Case.objects.filter(document_type=doc_type).count()
    if case_count:
        reasons.append(
            _append_trash_share(
                ngettext(
                    "%(n)d Fall ist diesem Dokumentationstyp zugeordnet und müsste zuerst geändert werden.",
                    "%(n)d Fälle sind diesem Dokumentationstyp zugeordnet und müssten zuerst geändert werden.",
                    case_count,
                )
                % {"n": case_count},
                Case.objects.filter(document_type=doc_type, is_deleted=True).count(),
                of_cases=True,
            )
        )

    # Refs #2068 (b): ``QuickTemplate.document_type`` ist seit dem
    # Hygiene-Buendel PROTECT statt CASCADE. Vorher fielen die Schnellvorlagen
    # still mit — samt ihrer von Hand gepflegten ``prefilled_data``, die sich
    # aus dem Dokumentationstyp allein nicht rekonstruieren lassen. Ohne
    # diesen Zaehler liefe der Loeschversuch jetzt in die generische
    # ``ProtectedError``-Auffangzeile unten, die nur „N Objekte" nennt.
    quick_template_count = QuickTemplate.objects.filter(document_type=doc_type).count()
    if quick_template_count:
        reasons.append(
            ngettext(
                "%(n)d Schnellvorlage verweist auf diesen Dokumentationstyp und müsste zuerst gelöscht werden.",
                "%(n)d Schnellvorlagen verweisen auf diesen Dokumentationstyp und müssten zuerst gelöscht werden.",
                quick_template_count,
            )
            % {"n": quick_template_count}
        )

    if reasons:
        raise ConfigDeleteBlocked(reasons)

    name, target_type, target_id = doc_type.name, doc_type.__class__.__name__, str(doc_type.pk)
    try:
        doc_type.delete()
    except ProtectedError as exc:  # pragma: no cover - Sicherheitsnetz gegen weitere PROTECT-Refs
        blocked = _("Der Dokumentationstyp wird noch von %(n)d Objekt(en) referenziert.") % {
            "n": len(exc.protected_objects)
        }
        raise ConfigDeleteBlocked([blocked]) from exc

    log_audit_event(
        request,
        AuditLog.Action.CONFIG_DELETE,
        target_type=target_type,
        target_id=target_id,
        detail={"name": name},
    )


def field_template_delete_blockers(field_template: FieldTemplate) -> list[str]:
    """Liefert alle UI-tauglichen Lösch-Blocker-Gründe (leere Liste = löschbar).

    Read-only Vorberechnung für die Confirm-Seite (P5b) — die Gründe sind
    identisch zu denen, die :func:`delete_field_template` als
    :class:`ConfigDeleteBlocked` wirft. Drei Gründe können (auch gemeinsam)
    greifen:

    a) **DocumentTypeField-Zuordnungen** (``CASCADE``) — würden beim Delete
       still mitgelöscht, daher **vor** dem Delete prüfen.
    b) **Attachment-Referenzen** (``PROTECT``).
    c) **Daten unter dem Slug** in ``Event``, ``Client`` **und** ``Case`` — das
       ``pre_delete``-Signal (#356) würfe sonst eine ``ProtectedError``.
       Personen/Fälle tragen seit M2 (Refs #1058) dieselben — auch
       verschlüsselten — Werte unter demselben Slug; ohne diese Prüfung würden
       Art.-9-nahe Restdaten verwaisen (Final-Review B).
    """
    reasons: list[str] = []

    dtf_count = DocumentTypeField.objects.filter(field_template=field_template).count()
    if dtf_count:
        reasons.append(
            _("%(n)d Dokumentationstyp-Zuordnung(en) verwenden diese Feldvorlage — bitte zuerst dort entfernen.")
            % {"n": dtf_count}
        )

    attachment_count = Attachment.objects.filter(field_template=field_template).count()
    if attachment_count:
        # M24/AP4 (Refs #1738): Papierkorb-Anteil auch hier — seit ADR-039 ist
        # ``Attachment.field_template`` (PROTECT) die zweite Quelle einer
        # Blockade, deren Verursacher im Fach-UI nicht mehr erreichbar ist.
        reasons.append(
            _append_trash_share(
                _("%(n)d Dateianhang/-anhänge sind an diese Feldvorlage gebunden.") % {"n": attachment_count},
                field_template_trashed_attachment_count(field_template),
                of_attachments=True,
            )
        )

    if field_template.slug:
        data_event_count = Event.objects.filter(
            facility_id=field_template.facility_id,
            data_json__has_key=field_template.slug,
        ).count()
        if data_event_count:
            reasons.append(
                _("%(n)d Ereignis(se) enthalten Daten unter dem Slug „%(slug)s“.")
                % {"n": data_event_count, "slug": field_template.slug}
            )

        data_client_count = Client.objects.filter(
            facility_id=field_template.facility_id,
            data_json__has_key=field_template.slug,
        ).count()
        if data_client_count:
            reasons.append(
                _append_trash_share(
                    ngettext(
                        "%(n)d Person enthält Daten unter dem Slug „%(slug)s“.",
                        "%(n)d Personen enthalten Daten unter dem Slug „%(slug)s“.",
                        data_client_count,
                    )
                    % {"n": data_client_count, "slug": field_template.slug},
                    # Nur der Slug-Anteil: dieser Satz zaehlt Personen MIT
                    # Slug-Daten. Der (seit M24 breitere)
                    # ``field_template_trashed_client_count`` speist den
                    # Ausweg-Hinweis der Dialoge und schluesse hier Personen ein,
                    # die allein ueber einen Anhang blockieren (Refs #1738).
                    len(_trashed_client_ids_with_slug(field_template)),
                )
            )

        data_case_count = Case.objects.filter(
            facility_id=field_template.facility_id,
            data_json__has_key=field_template.slug,
        ).count()
        if data_case_count:
            reasons.append(
                _append_trash_share(
                    ngettext(
                        "%(n)d Fall enthält Daten unter dem Slug „%(slug)s“.",
                        "%(n)d Fälle enthalten Daten unter dem Slug „%(slug)s“.",
                        data_case_count,
                    )
                    % {"n": data_case_count, "slug": field_template.slug},
                    # Nur der Slug-Anteil, wie im Personen-Zweig darüber: dieser
                    # Satz zählt Fälle MIT Slug-Daten. Der breitere
                    # ``field_template_trashed_case_count`` speist den
                    # Ausweg-Hinweis der Dialoge (Refs #1818).
                    len(_trashed_case_ids_with_slug(field_template)),
                    of_cases=True,
                )
            )

    return reasons


def delete_field_template(request, field_template: FieldTemplate) -> None:
    """Löscht eine FieldTemplate — blockiert bei jeder referenzierenden Datenspur.

    Es werden **alle** zutreffenden Gründe gesammelt (siehe
    :func:`field_template_delete_blockers`), nicht nur der erste. Alternative
    bleibt das Deaktivieren (:func:`set_field_template_active`).
    """
    reasons = field_template_delete_blockers(field_template)
    if reasons:
        raise ConfigDeleteBlocked(reasons)

    name, target_type, target_id = field_template.name, field_template.__class__.__name__, str(field_template.pk)
    field_template.delete()
    log_audit_event(
        request,
        AuditLog.Action.CONFIG_DELETE,
        target_type=target_type,
        target_id=target_id,
        detail={"name": name},
    )


def delete_field_group(request, group: FieldGroup) -> None:
    """Löscht eine FieldGroup — ihre Felder werden freie Blöcke **an ihrer Stelle**.

    ``DocumentTypeField.field_group`` ist ``SET_NULL``; die Felder bleiben also
    erhalten. Unter der globalen Block-Sequenz (#1713) genügt SET_NULL aber
    NICHT: die freigewordenen Felder trügen ihre Innen-``sort_order`` als
    (falsche) Globalpositionen. Daher explizit umnummerieren: der Gruppenblock
    wird an seiner bisherigen Position durch seine Innen-Felder (in Innen-
    Reihenfolge) ersetzt, die übrigen Blöcke rücken kompakt nach — das Layout
    bleibt visuell erhalten.
    """
    doc_type = group.document_type
    inner_fields = list(DocumentTypeField.objects.filter(field_group=group).order_by("sort_order", "pk"))
    name, target_type, target_id = group.name, group.__class__.__name__, str(group.pk)
    with transaction.atomic():
        # Blockliste VOR dem Delete erfassen und den Gruppenblock durch seine
        # Innen-Felder (als freie Blöcke) an gleicher Stelle ersetzen.
        new_order: list[tuple[str, uuid.UUID]] = []
        for block in iter_blocks_in_order(doc_type):
            if block["kind"] == "group" and block["group"].pk == group.pk:
                new_order.extend(("field", dtf.pk) for dtf in inner_fields)
            elif block["kind"] == "group":
                new_order.append(("group", block["group"].pk))
            else:
                new_order.append(("field", block["field"].pk))
        group.delete()  # SET_NULL → die Innen-Felder werden freie Blöcke
        for position, (kind, block_id) in enumerate(new_order):
            if kind == "group":
                FieldGroup.objects.filter(pk=block_id).update(sort_order=position)
            else:
                DocumentTypeField.objects.filter(pk=block_id).update(sort_order=position)
    log_audit_event(
        request,
        AuditLog.Action.CONFIG_DELETE,
        target_type=target_type,
        target_id=target_id,
        detail={"name": name},
    )
