"""Facility-scoped release history and overlap warnings (Refs #2529).

Only marked report artifacts satisfy ``RELEASE_EXPORT_Q``. Display windows
are bounded; overlap candidates come from the entire matching period history.
The warning compares overlapping periods of the same artifact or reports
sharing a dimension, never employee identities. Unmarked downloads and transfers
outside the app remain outside this register; no historical audit entries are rewritten.
"""

from __future__ import annotations

import dataclasses
from datetime import date

from django.db.models import CharField, F, Q, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce, NullIf

from core.models import AuditLog
from core.reporting.registry import related_report_slugs
from core.services.audit.detail_policy import RELEASE_FIELD_MAX_LENGTH as RELEASE_FIELD_MAX_LENGTH
from core.services.audit.detail_policy import validate_release_metadata
from core.utils.formatting import parse_date

# Default service display window; the view paginates independently.
RELEASE_LOG_LIMIT = 500
RELEASE_PAGE_SIZE = 50

# Freitextfelder am Export. Bewusst **optional**: ein Pflichtfeld erzwänge eine
# Angabe auch dort, wo der Export gar keine Herausgabe ist (Probelauf,
# Zwischenstand) — und Pflichtfelder ohne Anlass werden mit „x" gefüllt.
RELEASE_FIELDS = ("recipient", "purpose")

# Detail-Schlüssel des Berichtszeitraums. Alle Berichts-Export-Views schreiben
# ihn (``date_from``/``date_to``); die Klienten-/DSGVO-Exportpfade tun es nicht —
# sie haben keinen Zeitraum, und ohne Zeitraum ist keine Überlappung berechenbar.
RELEASE_PERIOD_KEY = "date_from"

# Detail-Marker „ein Artefakt hat das Haus verlassen". Er wird ausschließlich von
# den Download-Pfaden geschrieben (:func:`release_detail` mit
# ``artifact_released=True``); eine HTML-Ansicht setzt ihn nie. Ein **positiver**
# Marker statt einer Format-Negativliste: ein künftiges Ausgabeformat ist damit
# per Default *keine* Freigabe (fail-safe), statt durch ein vergessenes
# Listen-Update stillschweigend eine zu werden.
RELEASE_ARTIFACT_KEY = "artifact_released"

# ``AuditLog.target_type`` of the former external report view. The view is gone
# (Refs #2505, its address only redirects); existing rows stay in the log, so
# the legacy exclusion below still reads this value. Nothing writes it anymore.
EXTERNAL_REPORT_TARGET = "ExternalReport"

# Alt-Zeilen der externen **Ansicht**, die den Freigabe-Marker noch tragen
# (Refs #1772). Bis zu dieser Welle galt ``?format=json`` als Herausgabe: das
# Format liefert zwar Maschinen-Text, aber es ist dieselbe Bildschirmseite —
# ohne Empfänger-/Zweckfelder, ohne Download-Geste, bei jedem Aufruf neu
# auditiert. Die Klassifikation ist damit dieselbe wie bei der HTML-Ansicht.
#
# **Warum am Lesepfad und nicht per Backfill:** der Marker steht in ``detail``,
# ``detail`` geht in den Ketten-Hash ein — nachträglich entfernen hieße, die
# HMAC-Kette neu zu schreiben (siehe Modul-Docstring, dieselbe Doktrin wie beim
# Nachtragen). Die Übersicht zeigt deshalb künftig **nur echte Freigaben**, und
# die Alt-Zeilen verlieren zugleich die Retention-Ausnahme: sie werden wieder
# das, was sie immer waren — normale, befristete Audit-Zeilen. Gelöscht wird
# dabei nichts vorzeitig; sie laufen wie jede andere Ansicht nach
# ``auditlog_retention_months`` aus.
#
# Die Ausnahme hängt an **beidem** — Ziel UND Format. Der generische
# Berichts-Download (``target_type="DatasetExport"``) liefert ebenfalls JSON,
# ist aber ein herausgegebenes Artefakt und muss in der Übersicht bleiben.
# ``has_key`` vor dem Wertvergleich, weil ein fehlender JSON-Schlüssel in SQL
# NULL ergibt und die Negation dann weder wahr noch falsch wäre (dieselbe Falle
# wie beim Marker unten).
_LEGACY_VIEW_Q = Q(target_type=EXTERNAL_REPORT_TARGET) & Q(detail__has_key="format") & Q(detail__format="json")

# **Die** Definition einer Berichts-Freigabe (siehe Modul-Docstring). Bewusst ein
# ``Q`` statt einer Aktionsliste: die Aktion allein ist zu grob.
RELEASE_EXPORT_Q = (
    Q(action=AuditLog.Action.EXPORT)
    & Q(detail__has_key=RELEASE_PERIOD_KEY)
    & Q(detail__has_key=RELEASE_ARTIFACT_KEY)
    & ~_LEGACY_VIEW_Q
)

# **Warum ``has_key`` und nicht ``detail__artifact_released=True``:** Der
# Wertvergleich auf einem fehlenden JSON-Schlüssel ergibt in SQL nicht FALSE,
# sondern NULL — und ``exclude(...)`` daraus ``NOT NULL`` = NULL, also „Zeile
# nicht in der Löschmenge". Der Marker wirkte damit genau umgekehrt: eine
# Bildschirm-Ansicht OHNE Marker wäre prune-exempt geworden. ``has_key`` ist ein
# echter Boolescher Operator (``?``) und liefert für fehlende Schlüssel FALSE.
# Test-gepinnt in ``test_report_release_log.py::…::test_bildschirmaufruf_ist_nicht_prune_exempt``.


def release_detail(query_params, *, artifact_released: bool) -> dict[str, object]:
    """Freigabe-Anteil des ``EXPORT``-Audit-Details.

    ``artifact_released`` ist ein **Pflicht-Keyword ohne Default**: jede
    EXPORT-auditierende Aufrufstelle muss ausdrücklich sagen, ob bei ihr ein
    Artefakt das Haus verlassen hat. Ein Default wäre in beide Richtungen
    falsch — ``True`` machte jede künftige Bildschirm-Ansicht still zur
    dauerhaft aufbewahrten Herausgabe, ``False`` ließe einen neuen Download-Pfad
    still aus der Übersicht fallen. Dasselbe Muster wie ``margin``/``order`` in
    der Suppression: lieber ein ``TypeError`` beim Schreiben des Codes als eine
    stille Fehlklassifikation im Betrieb.

    Empfänger/Zweck werden **unabhängig davon** übernommen, wenn sie angegeben
    wurden — sie sind Herkunftsangabe, keine Freigabe-Entscheidung. Die Rückgabe
    ist bewusst **leer**, wenn nichts angegeben wurde: ein Detail mit
    ``{"recipient": ""}`` behauptete eine Angabe, die es nicht gibt, und ließe
    sich später nicht von „Feld gab es damals noch nicht" unterscheiden.

    Kein Schema-Eingriff: alles wandert in das bestehende ``detail``-JSONField.
    Der Audit-Log ist append-only und hash-verkettet — jede Schema-Migration
    darauf wäre teuer und hier unnötig.
    """
    validate_release_metadata({field: query_params.get(field, "") for field in RELEASE_FIELDS})
    detail: dict[str, object] = {RELEASE_ARTIFACT_KEY: True} if artifact_released else {}
    for field in RELEASE_FIELDS:
        value = (query_params.get(field) or "").strip()
        if value:
            detail[field] = value
    return detail


@dataclasses.dataclass(frozen=True)
class Release:
    """Eine herausgegebene Berichtsperiode aus dem ``EXPORT``-Audit."""

    timestamp: object
    artifact: str
    format: str
    # Backend-Slug der Berichts-Pipeline (``DatasetExportView``), sonst leer.
    # Er ist die **Identitaet** der Herausgabe: mit neun registrierten Backends
    # sagt das Ausgabeformat allein nichts mehr darueber, welcher Bericht das
    # Haus verlassen hat (Refs #1754).
    backend: str
    date_from: date | None
    date_to: date | None
    recipient: str
    purpose: str
    # Kurzbeschreibungen der Freigaben, mit denen sich der Zeitraum überschneidet.
    overlaps: tuple[str, ...] = ()

    # **Bewusst ohne handelnde Person.** Für den Zweck („welche Zeiträume sind
    # draußen, und überschneiden sie sich?") ist sie irrelevant; die
    # Rechenschaft über *wer* trägt der Audit-Log selbst. Sie hier zu führen
    # öffnete Leitungen eine Beschäftigten-Metadatensicht, die ``/audit/``
    # (FacilityAdminRequiredMixin) ihnen vorenthält — eine stille
    # Rechteausweitung über eine Nebentür.

    @property
    def has_period(self) -> bool:
        return self.date_from is not None and self.date_to is not None

    @property
    def label(self) -> str:
        if not self.has_period:
            return self.artifact
        return f"{self.artifact} {self.date_from} – {self.date_to}"


def _as_release(entry: AuditLog) -> Release:
    detail = entry.detail if isinstance(entry.detail, dict) else {}
    backend = str(detail.get("backend") or "")
    return Release(
        timestamp=entry.timestamp,
        # Identitaet der Herausgabe, in dieser Reihenfolge: Backend-Slug (die
        # Berichts-Pipeline schreibt ihn seit jeher mit), sonst Format, sonst
        # Ziel. Der Slug steht **vor** dem Format, weil neun Backends dasselbe
        # ``csv`` liefern und eine Warnung „csv ueberschneidet sich mit csv"
        # niemand zuordnen kann (Refs #1754).
        artifact=backend or str(detail.get("format") or entry.target_type or ""),
        format=str(detail.get("format") or ""),
        backend=backend,
        date_from=parse_date(detail.get("date_from")),
        date_to=parse_date(detail.get("date_to")),
        recipient=str(detail.get("recipient") or ""),
        purpose=str(detail.get("purpose") or ""),
    )


def _overlaps(first: Release, second: Release) -> bool:
    """Compare an artifact or shared dimension, including cross-report duplicates.

    Different backends can publish the same dimension. Their identical periods
    must also warn: combining the files may expose cells hidden in either one.
    Repeated downloads of one report for an identical period remain excluded.
    Output format and recipient do not change the shared dimension.
    """
    same_family = bool(first.backend and second.backend and second.backend in related_report_slugs(first.backend))
    if first.artifact != second.artifact and not same_family:
        return False
    if not (first.has_period and second.has_period):
        return False
    first_from, first_to, second_from, second_to = (
        first.date_from,
        first.date_to,
        second.date_from,
        second.date_to,
    )
    if first_from is None or first_to is None or second_from is None or second_to is None:
        # Keep optional dates narrowed for type checking.
        return False
    if first.artifact == second.artifact and (first_from, first_to) == (second_from, second_to):
        return False
    return first_from <= second_to and second_from <= first_to


def release_queryset(facility):
    """Use the shared artifact marker definition and stay inside the facility."""
    return AuditLog.objects.for_facility(facility).filter(RELEASE_EXPORT_Q).order_by("-timestamp", "-pk")


def build_release_overview(facility, limit: int = RELEASE_LOG_LIMIT, *, offset: int = 0) -> list[Release]:
    """Compare the display window with every matching artifact-family period.

    Bound queries by period, never display count. Identical periods of another
    report remain candidates; repeated downloads of one artifact do not.
    """
    history = release_queryset(facility)
    entries = history.only("timestamp", "target_type", "detail")[offset : offset + limit]
    result = []
    for entry in entries:
        release = _as_release(entry)
        if release.date_from is None or release.date_to is None:
            result.append(release)
            continue
        artifacts = related_report_slugs(release.backend) if release.backend else {release.artifact}
        candidates = (
            history.annotate(
                release_artifact=Coalesce(
                    NullIf(KeyTextTransform("backend", "detail"), Value("")),
                    NullIf(KeyTextTransform("format", "detail"), Value("")),
                    F("target_type"),
                    output_field=CharField(),
                )
            )
            .filter(
                release_artifact__in=artifacts,
                detail__date_from__lte=release.date_to.isoformat(),
                detail__date_to__gte=release.date_from.isoformat(),
            )
            .exclude(
                release_artifact=release.artifact,
                detail__date_from=release.date_from.isoformat(),
                detail__date_to=release.date_to.isoformat(),
            )
            .only("timestamp", "target_type", "detail")
        )
        result.append(
            dataclasses.replace(
                release,
                overlaps=tuple(
                    other.label for candidate in candidates if _overlaps(release, other := _as_release(candidate))
                ),
            )
        )
    return result
