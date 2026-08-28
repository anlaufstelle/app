"""Admin fuer DocumentType, FieldTemplate, QuickTemplate, TimeFilter (Refs #785, #958)."""

import logging

from django.contrib import admin, messages
from django.db import transaction
from unfold.admin import ModelAdmin, TabularInline

from core.admin.mixins import FacilityScopedAdminMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import AuditLog, DocumentType, DocumentTypeField, FieldGroup, FieldTemplate, QuickTemplate, TimeFilter
from core.services.audit import log_audit_event
from core.services.audit.chain import advisory_lock_id

logger = logging.getLogger(__name__)


class DocumentTypeFieldInline(TabularInline):
    model = DocumentTypeField
    extra = 1
    autocomplete_fields = ("field_template",)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """``field_group``-Dropdown auf den Parent-DocumentType scopen (Refs #1711).

        Ohne dieses Scoping listet der Django-Admin ALLE FieldGroups (auch
        fremder Facilities/DocumentTypes) — irrefuehrende UX (das Modell-
        ``clean()`` verhindert das Speichern zwar serverseitig, s. dort).
        Pflege der Gruppen laeuft primaer ueber die Config-UI; der Admin
        bleibt Super-Admin-Fallback. ``DocumentTypeAdmin.get_form`` legt das
        Parent-Objekt vorher auf ``request._doctype_parent_obj`` ab — im
        Add-View (neuer, noch ungespeicherter DocumentType) ist das ``None``
        und das Dropdown bleibt entsprechend leer (es gibt noch keine
        Gruppen).
        """
        if db_field.name == "field_group":
            parent = getattr(request, "_doctype_parent_obj", None)
            kwargs["queryset"] = (
                FieldGroup.objects.filter(document_type=parent) if parent else FieldGroup.objects.none()
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(DocumentType, site=anlaufstelle_admin_site)
class DocumentTypeAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = (
        "name",
        "sensitivity",
        "system_type",
        "tally_enabled",
        "facility",
        "is_active",
        "sort_order",
    )
    list_filter = ("sensitivity", "is_active", "tally_enabled", "facility", "system_type")
    search_fields = ("name",)
    inlines = [DocumentTypeFieldInline]

    def get_readonly_fields(self, request, obj=None):
        readonly = self.readonly_fields
        if obj and obj.system_type:
            readonly = (*readonly, "system_type")
        if obj and obj.report_key:
            # E3 (Refs #1849), Entscheid E-K: gesperrt wird NUR ein bereits
            # gesetzter Berichtsschluessel — leer→gesetzt bleibt auch hier der
            # Reparaturpfad. Dieselbe Mechanik wie ``system_type``: Django
            # schliesst ``readonly_fields`` von ``modelform_factory`` aus, ein
            # POST kann den Guard also gar nicht mehr erreichen. Ohne diese
            # Zeile leerte JEDER Change-Speichervorgang im Admin das Feld (der
            # POST traegt es nicht) und liefe in den Guard — HTTP 500 auf einer
            # voellig unbeteiligten Aenderung.
            readonly = (*readonly, "report_key")
        if obj:
            # Fix-Runde 1 (Refs #1058, B-1): slug/entity_type sind nach dem
            # Anlegen unveraenderlich (Guard in DocumentType.save()). Ohne
            # readonly waeren beide im Change-ModelForm editierbar — Django
            # schliesst readonly_fields dagegen von modelform_factory aus
            # (dieselbe Mechanik wie system_type oben), also kann eine
            # Aenderung ueber die Admin-UI den Guard gar nicht mehr erreichen
            # und den vormals unabgefangenen ValidationError->HTTP-500 ausloesen.
            readonly = (*readonly, "slug", "entity_type")
        return readonly

    def save_model(self, request, obj, form, change):
        """Refs #1917: Banner-Revision auch am Admin-Fallback bumpen.

        Der Django-Admin ist der per #1637 bewusst erhaltene technische
        Fallback und laeuft an ``core.services.config.save_document_type``
        vorbei — ohne diesen Hook blieben ``is_active``-/``sensitivity``-
        Edits an einem Ban-Typ fuer offene Zeitstroeme unsichtbar (staler
        Banner bis zum Reload). Zusaetzlich abgedeckt: das Setzen von
        ``system_type`` leer→ban (der Reparaturpfad — ein GESETZTER Wert ist
        readonly), das saemtliche Alt-Events des Typs neu ins Banner hebt.
        Der Admin-Change-View laeuft in ``transaction.atomic`` — Bump und
        Save teilen sich denselben Commit.

        Refs #2023: Der Fallback bleibt bewusst am Service vorbei — #1637 hat
        den Django-Admin als **technische** Notfallebene erhalten, die gerade
        nicht an der Fach-Formularvalidierung und am Facility-Request-Scope des
        Config-UI haengen soll. Was daran fehlte, war nur die Rechenschaftszeile:
        eine Aenderung an Sensibilitaet, Aktiv-Status oder Berichtsschluessel
        wirkt auf Sichtbarkeit und Auswertung derselben Daten, egal ueber
        welchen der beiden Wege sie lief. Deshalb entsteht hier dieselbe
        ``CONFIG_CREATE``/``CONFIG_UPDATE``-Zeile wie in
        ``services/config/mutations.save_document_type`` — Vokabel und
        ``detail``-Form absichtlich identisch, sonst muesste eine Auswertung
        unter /audit/ zwei Schreibweisen desselben Vorgangs kennen.
        """
        previous_sensitivity = form.initial.get("sensitivity", "") if change else ""
        changed_fields = list(form.changed_data)
        super().save_model(request, obj, form, change)
        # Wie im Config-Service: die Zeile entsteht bei JEDEM Speichervorgang,
        # auch wenn ``changed_fields`` leer ist. Der Beleg ist hier "wer hat
        # diesen Typ wann ueber die Notfallebene angefasst", nicht nur "was hat
        # sich geaendert" — und die leere Liste sagt genau das aus.
        log_audit_event(
            request,
            AuditLog.Action.CONFIG_UPDATE if change else AuditLog.Action.CONFIG_CREATE,
            target_obj=obj,
            detail={"name": obj.name, "changed_fields": changed_fields},
            facility=obj.facility,
        )
        if not change:
            return
        from core.services.system.ban_revision import (
            bump_ban_revision,
            bump_ban_revision_for_document_type_change,
        )

        bump_ban_revision_for_document_type_change(
            obj,
            changed_fields,
            previous_sensitivity=previous_sensitivity,
        )
        if "system_type" in changed_fields and obj.system_type == DocumentType.SystemType.BAN:
            bump_ban_revision(obj.facility, [0])

    def get_form(self, request, obj=None, **kwargs):
        """Parent-DocumentType fuer den Inline-Scope auf dem Request merken.

        Refs #1711: ``DocumentTypeFieldInline.formfield_for_foreignkey`` liest
        ``request._doctype_parent_obj``, um das ``field_group``-Dropdown auf
        die Gruppen dieses DocumentType zu beschraenken. Django ruft
        ``get_form`` (Haupt-Formular) vor den Inline-Formsets auf — der Wert
        steht rechtzeitig zur Verfuegung.
        """
        request._doctype_parent_obj = obj
        form = super().get_form(request, obj, **kwargs)
        if obj is None:
            # Fix-Runde 1 (Refs #1058, B-1, Zweitwirkung): slug ist beim
            # Anlegen kein readonly-Feld (es existiert ja noch kein Wert), war
            # aber als normales (Pflicht-)Feld im Add-ModelForm vorhanden — ein
            # Admin haette ihn von Hand tippen muessen statt die Model-
            # Auto-Generierung (``DocumentType.save()``) greifen zu lassen.
            # Ganz aus dem Add-Formular nehmen: kein POST-Wert -> ``save()``
            # generiert ihn automatisch aus dem Namen (Muster FieldTemplate).
            form.base_fields.pop("slug", None)
        return form


@admin.register(FieldTemplate, site=anlaufstelle_admin_site)
class FieldTemplateAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = (
        "name",
        "slug",
        "field_type",
        "is_active",
        "is_required",
        "is_encrypted",
        "sensitivity",
        "facility",
    )
    list_filter = ("is_active", "field_type", "is_encrypted", "sensitivity", "is_required", "facility")
    search_fields = ("name",)
    fields = (
        "facility",
        "name",
        "slug",
        "field_type",
        "is_required",
        "is_encrypted",
        "sensitivity",
        "options_json",
        "default_value",
        "report_key",
        "help_text",
        "is_active",
    )
    actions = ("deactivate_selected", "activate_selected")

    def get_readonly_fields(self, request, obj=None):
        readonly = self.readonly_fields
        if obj:
            readonly = (*readonly, "slug")
        # E3 (Refs #1849), Entscheid E-K — dieselbe Regel wie am
        # Dokumentationstyp: gesperrt erst, wenn ein Wert dasteht.
        if obj and obj.report_key:
            readonly = (*readonly, "report_key")
        return readonly

    def delete_model(self, request, obj):
        """ProtectedError aus dem pre_delete-Signal abfangen und als Admin-Meldung anzeigen (Issue #356)."""
        from django.db.models import ProtectedError

        try:
            super().delete_model(request, obj)
        except ProtectedError as exc:
            messages.error(request, str(exc.args[0]))

    def delete_queryset(self, request, queryset):
        """Bulk-Delete: ProtectedError pro FieldTemplate abfangen und in der Admin-UI zeigen."""
        from django.db.models import ProtectedError

        for obj in queryset:
            try:
                obj.delete()
            except ProtectedError as exc:
                messages.error(request, str(exc.args[0]))

    def _bulk_set_active(self, request, queryset, *, is_active: bool) -> int:
        """``is_active`` sammelumstellen und JE tatsaechlich geaenderter Vorlage
        eine ``CONFIG_UPDATE``-Zeile schreiben (Refs #2023).

        Eine Feldvorlage zu deaktivieren nimmt sie aus **allen**
        Erfassungsformularen der Einrichtung — bis hierher geschah das per
        rohem ``QuerySet.update()`` und damit ohne jede Spur im append-only
        AuditLog, waehrend derselbe Schalter ueber die Config-UI eine
        ``CONFIG_UPDATE``-Zeile erzeugt (``services/config/mutations.py``).

        Das ``update()`` bleibt bewusst erhalten (Entscheid des Tickets:
        „Service-Layer **oder** eigene Audit-Zeilen"): ``FieldTemplate.save()``
        traegt Normalisierungs- und Validierungslogik (Zahlenbereiche,
        HIGH⇒verschluesselt), die eine reine Aktiv/Inaktiv-Umschaltung nicht
        braucht und die an Alt-Bestand scheitern koennte; am Modell haengt nur
        ein ``pre_delete``-Receiver, also geht durch das Bulk-Update kein
        Signal verloren.

        Vorgefiltert wird auf die Zeilen, die sich wirklich aendern — sonst
        erzeugte jeder Sammelklick eine Zeile je unveraenderter Vorlage und
        deckte die echten Aenderungen unter Rauschen zu. Die Erfolgsmeldung
        zaehlt damit ebenfalls die echten Aenderungen.

        Fix-Runde 1 (Cross-Review) — **Transaktionsklammer, Zeilensperren,
        Rueckgabezahl**: Vorher standen Snapshot, unbedingtes ``update()`` und
        Audit-Writes ungeschuetzt nebeneinander. Moeglich waren dadurch eine
        Umschaltung ohne Audit (Fehler beim Write), doppelte Auditzeilen bei
        parallelen gleichgerichteten Aktionen und Auditzeilen fuer
        zwischenzeitlich geaenderte Vorlagen. Jetzt sperrt der Vorfilter die
        Zeilen (``select_for_update``, ``order_by("pk")`` gegen Deadlocks —
        Muster #1022 B2), das ``update()`` bleibt auf den unveraenderten
        Zustand konditioniert, und seine Rueckgabezahl ist die gemeldete
        Wahrheit. Unter der Sperre muss sie mit dem Snapshot uebereinstimmen;
        weicht sie ab, wird das laut geloggt, statt es stillschweigend
        wegzuwerfen.

        Fix-Runde 2/3 (Re-Review) — **Ordnung nach dem tatsaechlichen
        Advisory-Lock-Key**: Jeder Audit-Insert nimmt in
        ``services/audit/chain.py`` einen **transaktionsweiten** Advisory-Lock
        PRO FACILITY, der bis zum Commit gehalten wird — in dieser Klammer
        sammeln sich also die Facility-Locks aller auditierten Vorlagen an. Ein
        super_admin sieht einrichtungsuebergreifend
        (``admin_site.scope_to_facility``), sein Lauf kann somit mehrere
        Facility-Locks halten; ohne feste Erwerbsreihenfolge deadlocken zwei
        parallele Laeufe selbst ueber disjunkte Vorlagenmengen.

        Nach ``facility_id`` zu ordnen reicht **nicht**: gesperrt wird
        ``hashtext(name)``, eine 32-Bit- und damit nicht injektive Abbildung.
        Bei ``h(A) = h(C) = X``, ``h(B) = Y`` mit ``A < B < C`` naehme ein Lauf
        ueber ``{A, B}`` die Keys ``X -> Y``, ein Lauf ueber ``{B, C}`` dagegen
        ``Y -> X``. Sortiert wird deshalb nach
        ``(advisory_lock_id, facility_id)`` — echter Lock-Key, facility_id als
        deterministischer Tie-Break bei Kollision.

        **Invariante:** ueber mehrere Facilities immer nach
        ``advisory_lock_id`` ordnen. Die SQL-Ordnung ``(facility_id, pk)``
        darunter bleibt fuer die ZEILENsperren zustaendig; Pythons stabile
        Sortierung erhaelt sie innerhalb einer Facility.
        """
        with transaction.atomic():
            betroffen = list(queryset.select_for_update().filter(is_active=not is_active).order_by("facility_id", "pk"))
            if not betroffen:
                return 0
            # Umsortieren auf die Lock-Key-Ordnung: je Facility EIN Lookup.
            # ``None`` (Nicht-PostgreSQL) faellt auf 0 — dort nimmt
            # ``assign_chain_fields`` gar keinen Lock.
            lock_ids = {fid: (advisory_lock_id(fid) or 0) for fid in {obj.facility_id for obj in betroffen}}
            betroffen.sort(key=lambda obj: (lock_ids[obj.facility_id], str(obj.facility_id)))
            geaendert = FieldTemplate.objects.filter(
                pk__in=[obj.pk for obj in betroffen],
                # Konditioniert statt unbedingt: das UPDATE fasst nur an, was
                # sich wirklich noch im Ausgangszustand befindet.
                is_active=not is_active,
            ).update(is_active=is_active)
            if geaendert != len(betroffen):
                logger.error(
                    "FieldTemplate-Bulk (%s): %d gesperrte Vorlagen, aber %d geaenderte Zeilen — "
                    "Audit und Zustand koennten auseinanderlaufen.",
                    "aktivieren" if is_active else "deaktivieren",
                    len(betroffen),
                    geaendert,
                )
            for obj in betroffen:
                log_audit_event(
                    request,
                    AuditLog.Action.CONFIG_UPDATE,
                    target_obj=obj,
                    # Dieselbe Form wie ``save_field_template`` (Config-UI), plus
                    # ``bulk`` als Kennzeichen der Sammelaenderung. ``name`` ist
                    # Einrichtungs-Konfiguration, keine Klienten-PII (Allowlist
                    # ``AUDIT_DETAIL_KEYS``, Refs #1944).
                    detail={"name": obj.name, "changed_fields": ["is_active"], "bulk": True},
                    facility=obj.facility,
                )
        return geaendert

    @admin.action(description="Ausgewählte Feldvorlagen deaktivieren")
    def deactivate_selected(self, request, queryset):
        updated = self._bulk_set_active(request, queryset, is_active=False)
        messages.success(request, f"{updated} Feldvorlage(n) deaktiviert.")

    @admin.action(description="Ausgewählte Feldvorlagen aktivieren")
    def activate_selected(self, request, queryset):
        updated = self._bulk_set_active(request, queryset, is_active=True)
        messages.success(request, f"{updated} Feldvorlage(n) aktiviert.")


@admin.register(QuickTemplate, site=anlaufstelle_admin_site)
class QuickTemplateAdmin(FacilityScopedAdminMixin, ModelAdmin):
    """Admin für Quick-Templates (vorbefüllte Event-Vorlagen).

    ``prefilled_data`` wird beim Speichern über den Service auf NORMAL-Felder
    gefiltert (Whitelist). So kann der Admin-User Werte pflegen, ohne die
    Sensitivitäts-Regeln zu umgehen.
    """

    list_display = ("name", "document_type", "facility", "is_active", "sort_order")
    list_filter = ("is_active", "facility", "document_type")
    search_fields = ("name",)
    autocomplete_fields = ("document_type",)
    readonly_fields = ("created_at",)

    def save_model(self, request, obj, form, change):
        from core.services.dashboard import filter_prefilled_data

        obj.prefilled_data = filter_prefilled_data(obj.document_type, obj.prefilled_data or {})
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(TimeFilter, site=anlaufstelle_admin_site)
class TimeFilterAdmin(FacilityScopedAdminMixin, ModelAdmin):
    list_display = ("label", "start_time", "end_time", "is_active", "facility")
    list_filter = ("is_active", "facility")
