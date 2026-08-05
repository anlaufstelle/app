"""Import-Wizard: Hochladen → Zuordnen → Probelauf → Übernehmen (M4, Refs #1060).

Alle Views tragen ``FacilityAdminRequiredMixin``: ein Masseningest legt in
einem Schritt hunderte Klientendatensaetze an — konservativ bei der
Anwendungsbetreuung. Kein Sudo-Mode (Konvention: Sudo nur bei PII-Export und
Auth-Risiko), kein ``super_admin``-Zugriff (der hat keine Facility).

Zwei Muster ziehen sich durch alle pk-Views:

1. ``get_scoped_object(...)`` steht IMMER vor jeder Payload- oder
   Statuspruefung — ein fremder Lauf liefert damit deterministisch 404 und
   verraet seine Existenz nicht (IDOR).
2. Die Statusmaschine wird in der View durchgesetzt: ein Schritt, der zum
   aktuellen Status nicht passt, endet in ``messages.error`` + Redirect auf die
   Detailseite (Repo-Muster, nie 5xx).

Fehler der Engine (fehlende Quelldatei, kaputtes CSV, Statusverstoss) kommen
als ``ValidationError`` hoch und werden hier zu einer Nutzermeldung — die
Wizard-Seiten duerfen nie mit einem 500er antworten.
"""

import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from core.constants import RATELIMIT_BULK_ACTION, RATELIMIT_MUTATION
from core.forms.import_data import ImportUploadForm
from core.models import ImportJob
from core.services import import_engine
from core.services.scoping import get_scoped_object
from core.views.mixins import FacilityAdminRequiredMixin, PaginatedListMixin

logger = logging.getLogger(__name__)

#: Spalten-Selects im Zuordnungsschritt heissen ``column-<index>``; der Index
#: zeigt in die Kopfzeile. Ueber den Namen statt ueber die Spaltenueberschrift,
#: weil Ueberschriften Leer-/Sonderzeichen tragen duerfen.
COLUMN_FIELD_PREFIX = "column-"


def _error_redirect(request, job, message):
    messages.error(request, message)
    return redirect("core:import_detail", pk=job.pk)


class ImportListView(FacilityAdminRequiredMixin, PaginatedListMixin, View):
    """Alle Importläufe der eigenen Einrichtung, neueste zuerst."""

    def get(self, request):
        queryset = ImportJob.objects.for_facility(request.current_facility).select_related(
            "created_by", "document_type"
        )
        return render(
            request,
            "core/import/list.html",
            {"page_obj": self.paginate(queryset, request)},
        )


@method_decorator(
    # Bulk-Rate (30/h): jeder Upload parst bis zu 1000 Zeilen und verschluesselt
    # eine Datei — teurer als eine gewoehnliche Mutation.
    ratelimit(key="user", rate=RATELIMIT_BULK_ACTION, method="POST", block=True),
    name="post",
)
class ImportCreateView(FacilityAdminRequiredMixin, View):
    """Schritt 1: CSV hochladen und den Lauf anlegen."""

    def get(self, request):
        return render(
            request,
            "core/import/upload.html",
            {"form": ImportUploadForm(facility=request.current_facility)},
        )

    def post(self, request):
        facility = request.current_facility
        form = ImportUploadForm(request.POST, request.FILES, facility=facility)
        if form.is_valid():
            try:
                job = import_engine.store_import_upload(
                    facility,
                    request.user,
                    form.cleaned_data["source_file"],
                    target_type=form.cleaned_data["target_type"],
                    document_type=form.cleaned_data.get("document_type"),
                    delimiter=form.resolved_delimiter,
                    encoding=form.cleaned_data.get("encoding") or "",
                )
            except ValidationError as exc:
                # Limits, kaputte Kopfzeile, unlesbarer Zeichensatz — alles
                # Nutzerfehler an der Datei, kein Serverfehler.
                for message in exc.messages:
                    form.add_error(None, message)
            else:
                messages.success(request, _("Datei hochgeladen. Bitte die Spalten zuordnen."))
                return redirect("core:import_mapping", pk=job.pk)
        return render(request, "core/import/upload.html", {"form": form})


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_BULK_ACTION, method="POST", block=True),
    name="post",
)
class ImportMappingView(FacilityAdminRequiredMixin, View):
    """Schritt 2: Spalten zuordnen; das POST startet direkt den Probelauf."""

    ALLOWED_STATUSES = (ImportJob.Status.UPLOADED, ImportJob.Status.MAPPED, ImportJob.Status.VALIDATED)

    def get(self, request, pk):
        job = get_scoped_object(ImportJob, request, pk=pk)
        if job.status not in self.ALLOWED_STATUSES:
            return _error_redirect(request, job, _("Der Importlauf ist bereits abgeschlossen."))
        try:
            preview = import_engine.parse_preview(job)
        except ValidationError as exc:
            return _error_redirect(request, job, " ".join(exc.messages))
        return render(request, "core/import/mapping.html", self._context(job, preview))

    def post(self, request, pk):
        job = get_scoped_object(ImportJob, request, pk=pk)
        if job.status not in self.ALLOWED_STATUSES:
            return _error_redirect(request, job, _("Der Importlauf ist bereits abgeschlossen."))
        try:
            preview = import_engine.parse_preview(job)
        except ValidationError as exc:
            return _error_redirect(request, job, " ".join(exc.messages))

        mapping = {
            column: request.POST.get(f"{COLUMN_FIELD_PREFIX}{index}", "")
            for index, column in enumerate(preview["columns"])
        }
        try:
            # Zuordnen + Probelauf laufen als EIN gesperrter Uebergang; ein
            # parallel eintreffender Commit/Abbruch darf hier nicht mehr
            # ueberschrieben werden (Refs #1793).
            import_engine.probe_import(job, request.user, mapping)
        except ValidationError as exc:
            context = self._context(job, preview)
            context["errors"] = list(exc.messages)
            # Die abgelehnte Auswahl bleibt stehen, damit die Korrektur nicht
            # bei null anfaengt.
            for column in context["columns"]:
                column["selected"] = mapping.get(column["name"], "")
            return render(request, "core/import/mapping.html", context, status=400)
        return redirect("core:import_preview", pk=job.pk)

    def _context(self, job, preview):
        selected = (job.mapping_json or {}).get("columns", {})
        # Spalten samt Beispielwerten und Feldnamen fertig aufbereiten: der
        # ``get_item``-Filter kann nur Dicts, ein Index-Zugriff auf die
        # Zeilenlisten waere im Template nicht moeglich.
        columns = [
            {
                "index": index,
                "name": name,
                "field": f"{COLUMN_FIELD_PREFIX}{index}",
                "samples": [row[index] if index < len(row) else "" for row in preview["rows"]],
                "selected": selected.get(name, ""),
            }
            for index, name in enumerate(preview["columns"])
        ]
        return {
            "job": job,
            "preview": preview,
            "columns": columns,
            "targets": import_engine.available_targets(job),
        }


class ImportPreviewView(FacilityAdminRequiredMixin, View):
    """Schritt 3: Ergebnis des Probelaufs mit Fehlertabelle."""

    def get(self, request, pk):
        job = get_scoped_object(ImportJob, request, pk=pk)
        if job.status != ImportJob.Status.VALIDATED:
            return _error_redirect(request, job, _("Für diesen Importlauf liegt kein Probelauf vor."))
        return render(request, "core/import/preview.html", {"job": job, "result": job.result_json or {}})


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_BULK_ACTION, method="POST", block=True),
    name="post",
)
class ImportCommitView(FacilityAdminRequiredMixin, View):
    """Schritt 4: Übernahme — nur nach fehlerfreiem Probelauf."""

    def post(self, request, pk):
        job = get_scoped_object(ImportJob, request, pk=pk)
        try:
            import_engine.commit_import(job, request.user)
        except ValidationError as exc:
            return _error_redirect(request, job, " ".join(exc.messages))
        messages.success(
            request,
            _("Import übernommen: %(count)s Zeile(n) angelegt.")
            % {"count": (job.result_json or {}).get("summary", {}).get("created", 0)},
        )
        return redirect("core:import_detail", pk=job.pk)


@method_decorator(
    ratelimit(key="user", rate=RATELIMIT_MUTATION, method="POST", block=True),
    name="post",
)
class ImportAbortView(FacilityAdminRequiredMixin, View):
    """Abbruch: Quelldatei löschen, Lauf schließen."""

    def post(self, request, pk):
        job = get_scoped_object(ImportJob, request, pk=pk)
        try:
            import_engine.abort_import(job, request.user)
        except ValidationError as exc:
            return _error_redirect(request, job, " ".join(exc.messages))
        messages.success(request, _("Import abgebrochen. Die hochgeladene Datei wurde gelöscht."))
        return redirect("core:import_detail", pk=job.pk)


class ImportDetailView(FacilityAdminRequiredMixin, View):
    """Status, Zähler und Fehlerliste eines Laufs."""

    def get(self, request, pk):
        job = get_scoped_object(
            ImportJob.objects.select_related("created_by", "document_type"),
            request,
            pk=pk,
        )
        return render(request, "core/import/detail.html", {"job": job, "result": job.result_json or {}})
