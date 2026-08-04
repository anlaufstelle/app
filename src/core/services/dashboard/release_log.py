"""Freigabe-Übersicht herausgegebener Berichtszeiträume (K4a, Refs #1770).

**K4a ist keine Lücke im Verfahren, sondern seine Definitionsgrenze.** Die
k-Anonymitäts-Zusicherung gilt je Artefakt und je Zeitraum; werden Berichte über
Teilzeiträume **und** über deren Vereinigung herausgegeben (Monatsberichte plus
Jahresbericht), lässt sich jede in einem Teilbericht unterdrückte Zelle aus der
Summe der übrigen exakt zurückrechnen — auch dann, wenn die Sekundärsuppression
gegriffen hat, und auch dann, wenn in **keinem** der beteiligten Berichte eine
einzige Zelle unterdrückt wurde. Artefakt-lokal ist dagegen nichts auszurichten:
es gibt keine Zelle, an der eine Suppression ansetzen könnte.

Deshalb flankierend, nicht abwehrend: eine Übersicht der ohnehin auditierten
Herausgaben, die überlappende Zeiträume **sichtbar** macht. Die Analyse hat dafür
zwei Auflagen benannt, beide sind umgesetzt:

* Das ``EXPORT``-Detail braucht ein **Empfänger-/Zweckfeld** — sonst erzeugt jede
  Überlappung einen Alarm, den niemand bewerten kann, und die Übersicht wird
  weggeklickt (:func:`release_detail`).
* Die Historie darf **nicht** von ``auditlog_retention_months`` gekappt werden,
  sonst täuscht die Übersicht Vollständigkeit vor (:data:`RELEASE_EXPORT_Q`,
  ausgewertet in :mod:`core.retention.audit_pruning`).

**``EXPORT`` ist eine Sammelklasse, kein Freigabe-Register.** Unter derselben
Aktion laufen die Art.-15-Datenauskunft einer Person (``target_id`` = Klienten-
UUID), die DSGVO-Dokument-Downloads und **jeder Bildschirm-Aufruf** des externen
Berichts. Eine Berichts-Freigabe erkennt man deshalb an zwei Dingen, die beide im
``detail`` stehen müssen und beide ihren eigenen Grund haben:

* :data:`RELEASE_ARTIFACT_KEY` — **ein Artefakt hat das Haus verlassen.** Ohne
  diese Grenze zählte jeder Reload der Bildschirm-Ansicht als Herausgabe: der
  Log wüchse mit jedem Seitenaufruf unbegrenzt (prune-exempt, mit
  Beschäftigten-Username), und die Ansichten verdrängten bei
  :data:`RELEASE_LOG_LIMIT` die echten Downloads aus dem Auswertungsfenster —
  ein stiller falscher Negativ in genau der Warnung, die die Retention-Ausnahme
  rechtfertigt. Das gilt seit #1772
  für **beide** Formate des externen Berichts: ``?format=json`` liefert
  Maschinen-Text, ist aber dieselbe Seite ohne Empfänger-/Zweckfelder und ohne
  Download-Geste. Alt-Zeilen mit Marker schließt :data:`_LEGACY_VIEW_Q` aus.
* :data:`RELEASE_PERIOD_KEY` — **es gibt einen Berichtszeitraum.** Ohne ihn ist
  keine Überlappung berechenbar; das ist die Grenze gegen die Klienten-/
  DSGVO-Exportpfade.

:data:`RELEASE_EXPORT_Q` fasst beides zu **einer** Definition zusammen, und beide
Konsumenten (Übersicht und Retention-Ausnahme) lesen sie von dort. Zwei Kopien
wären zwei Wahrheiten: eine Zeile, die die Übersicht führt, aber das Pruning
löscht, macht die Übersicht still unvollständig.

**Rollout-Grenze — kein Backfill möglich, und das ist keine Bequemlichkeit.**
:data:`RELEASE_ARTIFACT_KEY` ist neu; **keine** vor seiner Einführung
geschriebene Zeile trägt ihn. Bestands-Freigaben sind deshalb weder
prune-exempt noch in der Übersicht sichtbar — für bis zu
``auditlog_retention_months`` laufen sie wie bisher aus dem Log, und eine
Überlappungswarnung kann gegen sie nicht feuern. Nachtragen ist **ausgeschlossen**:

* ``AuditLog.save()`` verbietet Updates, und der DB-Trigger ``auditlog_immutable``
  steht auf ``ENABLE ALWAYS`` (nur die Owner-Rolle könnte ihn abschalten);
* vor allem geht ``detail`` in den Ketten-Hash ein
  (``core.services.audit.chain._canonical_payload``). Eine nachträgliche
  Markierung bräche die HMAC-Kette ab der geänderten Zeile — genau das, was
  ``test_audit_chain.py::TestTamperDetection::test_inplace_detail_mutation_breaks_chain``
  als Manipulation nachweist. Sie zu „reparieren" hieße, die Kette vorwärts
  neu zu schreiben, und damit ihre einzige Eigenschaft aufzugeben: dass
  Vergangenes nicht unbemerkt änderbar ist. Dieselbe Doktrin gilt schon beim
  Pruning („die Kette wird nie umgeschrieben, der Checkpoint legitimiert nur
  die Lücke", :mod:`core.retention.audit_pruning`).

Die Richtung ist fail-safe (lieber eine Freigabe zu wenig zeigen als eine
erfinden), aber sie macht die Übersicht in den ersten Monaten unvollständig.
Deshalb sagt die Seite es selbst, statt Vollständigkeit zu behaupten; die
Betreiber-Doku nennt dieselbe Grenze. Test-gepinnt in
``test_report_release_log.py`` (``…_altzeilen_vor_der_marker_einfuehrung_…``).
"""

from __future__ import annotations

import dataclasses
from datetime import date

from django.db.models import Q

from core.models import AuditLog
from core.utils.formatting import parse_date

# Wie viele Herausgaben die Übersicht höchstens auswertet. Die Überlappungs-
# prüfung ist quadratisch; die Grenze hält sie bei einer append-only wachsenden
# Tabelle berechenbar. Sie ist bewusst großzügig — drei Jahre monatlicher
# Träger-Berichte über mehrere Formate liegen deutlich darunter.
RELEASE_LOG_LIMIT = 500

# Maximale Länge der Freitextfelder. Sie sind ein **Nachweis**, kein Fließtext:
# „Stadt Musterstadt, Verwendungsnachweis 2026". Die Kappung begrenzt zugleich,
# wie viel eine unbedachte Eingabe in den append-only Audit-Log tragen kann —
# der überlebt die Löschfristen (siehe Modul-Docstring) und ist nachträglich
# nicht redigierbar.
RELEASE_FIELD_MAX_LENGTH = 200

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

# ``AuditLog.target_type`` der externen Berichts-Ansicht. Hier und nicht in der
# View, weil beide Seiten denselben Wert brauchen: die View schreibt ihn, der
# Alt-Zeilen-Ausschluss unten liest ihn.
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
    detail: dict[str, object] = {RELEASE_ARTIFACT_KEY: True} if artifact_released else {}
    for field in RELEASE_FIELDS:
        value = (query_params.get(field) or "").strip()[:RELEASE_FIELD_MAX_LENGTH]
        if value:
            detail[field] = value
    return detail


@dataclasses.dataclass(frozen=True)
class Release:
    """Eine herausgegebene Berichtsperiode aus dem ``EXPORT``-Audit."""

    timestamp: object
    artifact: str
    format: str
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
    return Release(
        timestamp=entry.timestamp,
        artifact=str(detail.get("format") or entry.target_type or ""),
        format=str(detail.get("format") or ""),
        date_from=parse_date(detail.get("date_from")),
        date_to=parse_date(detail.get("date_to")),
        recipient=str(detail.get("recipient") or ""),
        purpose=str(detail.get("purpose") or ""),
    )


def _overlaps(first: Release, second: Release) -> bool:
    """Überschneiden sich zwei Herausgaben **nicht-identisch**?

    Identische Zeiträume sind bewusst **kein** Befund: aus zwei Berichten über
    denselben Zeitraum lässt sich keine Differenz bilden, ganz gleich, an wen
    sie gingen — der zweite ist typischerweise ein erneuter Download desselben
    Artefakts. Der Kanal aus K4a lebt von *verschachtelten* oder *teilweise*
    überlappenden Zeiträumen (A ⊊ B), und genau die bleiben stehen. Ohne diese
    Einschränkung produzierte die Übersicht Alarme ohne Aussage.
    """
    if not (first.has_period and second.has_period):
        return False
    first_from, first_to, second_from, second_to = (
        first.date_from,
        first.date_to,
        second.date_from,
        second.date_to,
    )
    if first_from is None or first_to is None or second_from is None or second_to is None:
        # Kann bei erfuellter has_period-Pruefung nicht eintreten — hier nur,
        # damit mypy die None-Faelle beweisbar ausschliesst.
        return False
    if (first_from, first_to) == (second_from, second_to):
        return False
    return first_from <= second_to and second_from <= first_to


def build_release_overview(facility, limit: int = RELEASE_LOG_LIMIT) -> list[Release]:
    """Die auditierten Herausgaben der Einrichtung, mit Überlappungswarnung.

    Liest ausschließlich ``AuditLog``-Einträge, die :data:`RELEASE_EXPORT_Q`
    erfüllen — es gibt kein zweites Register und keinen neuen Schreibpfad. Der
    Filter ist nicht bloß Kosmetik: ``EXPORT`` umfasst auch Art.-15-Auskünfte
    und DSGVO-Downloads (kein Berichtszeitraum, keine Überlappung berechenbar,
    Klientenbezug auf einer LEAD_PLUS-Seite) sowie jeden **Bildschirm-Aufruf**
    des externen Berichts — letztere verdrängten sonst bei
    :data:`RELEASE_LOG_LIMIT` die echten Downloads aus dem Auswertungsfenster.

    Zwei blinde Flecken, beide bewusst und beide auf der Seite selbst
    ausgewiesen: Herausgaben aus der Zeit **vor** der Markierung (Rollout-Grenze,
    siehe Modul-Docstring — kein Backfill möglich) und Artefakte, die auf
    anderem Weg weitergegeben wurden (abfotografierter Bildschirm,
    weitergeleitete PDF-Datei). Der zweite ist eine organisatorische, keine
    technische Grenze.
    """
    entries = AuditLog.objects.for_facility(facility).filter(RELEASE_EXPORT_Q).order_by("-timestamp")[:limit]
    releases = [_as_release(entry) for entry in entries]
    return [
        dataclasses.replace(
            release,
            overlaps=tuple(other.label for other in releases if other is not release and _overlaps(release, other)),
        )
        for release in releases
    ]
