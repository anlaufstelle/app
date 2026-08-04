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
UUID), die DSGVO-Dokument-Downloads und jeder Aufruf des externen Berichts. Was
eine **Berichts-Freigabe** ausmacht, ist deshalb nicht die Aktion, sondern der
mitgeschriebene **Berichtszeitraum** — :data:`RELEASE_EXPORT_Q` ist die eine
Stelle, an der diese Grenze definiert wird, und beide Konsumenten (Übersicht und
Retention-Ausnahme) lesen sie von dort. Ohne die Grenze wäre die Übersicht eine
Beschäftigten-Metadatensicht für Leitungen (``/audit/`` ist ADMIN_ONLY) und die
Retention-Ausnahme eine Speicherbegrenzungs-Regression für Klientendaten.
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

# Detail-Schlüssel, an dem eine Berichts-Freigabe erkennbar ist. Alle
# Berichts-Export-Views schreiben ihn (``date_from``/``date_to``); die
# Klienten-/DSGVO-Exportpfade tun es nicht — sie haben keinen Zeitraum.
RELEASE_PERIOD_KEY = "date_from"

# **Die** Definition einer Berichts-Freigabe (siehe Modul-Docstring). Bewusst ein
# ``Q`` statt einer Aktionsliste: die Aktion allein ist zu grob.
RELEASE_EXPORT_Q = Q(action=AuditLog.Action.EXPORT) & Q(detail__has_key=RELEASE_PERIOD_KEY)


def release_detail(query_params) -> dict[str, str]:
    """Empfänger/Zweck aus den Query-Parametern für das ``EXPORT``-Audit-Detail.

    Rückgabe ist bewusst **leer**, wenn nichts angegeben wurde: ein Detail mit
    ``{"recipient": ""}`` behauptete eine Angabe, die es nicht gibt, und ließe
    sich später nicht von „Feld gab es damals noch nicht" unterscheiden.

    Kein Schema-Eingriff: die Felder wandern in das bestehende ``detail``-
    JSONField. Der Audit-Log ist append-only und hash-verkettet — jede
    Schema-Migration darauf wäre teuer und hier unnötig.
    """
    detail = {}
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
    if (first.date_from, first.date_to) == (second.date_from, second.date_to):
        return False
    return first.date_from <= second.date_to and second.date_from <= first.date_to


def build_release_overview(facility, limit: int = RELEASE_LOG_LIMIT) -> list[Release]:
    """Die auditierten Herausgaben der Einrichtung, mit Überlappungswarnung.

    Liest ausschließlich ``AuditLog``-Einträge, die :data:`RELEASE_EXPORT_Q`
    erfüllen — es gibt kein zweites Register und keinen neuen Schreibpfad. Der
    Filter ist nicht bloß Kosmetik: ``EXPORT`` umfasst auch Art.-15-Auskünfte
    und DSGVO-Downloads, die hier weder hingehören (kein Berichtszeitraum, keine
    Überlappung berechenbar) noch hin dürfen (Klientenbezug auf einer
    LEAD_PLUS-Seite).

    Die Übersicht ist damit genau so vollständig wie die Audit-Spur der
    Berichts-Exporte; ihr blinder Fleck sind Artefakte, die auf anderem Weg
    weitergegeben wurden (abfotografierter Bildschirm, weitergeleitete
    PDF-Datei) — das ist eine organisatorische, keine technische Grenze und
    gehört so in die Betreiber-Doku.
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
