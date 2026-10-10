"""Role-based access mixins."""

import logging
from functools import wraps

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import render

from core.services.events.idempotency import (
    get_idempotent_result,
    has_idempotency_payload_mismatch,
    normalize_idempotency_key,
    payload_fingerprint,
    remember_idempotent_result,
)
from core.views._json_contracts import _idempotency_mismatch_response

logger = logging.getLogger(__name__)

# R6 (Refs #1443): Substring beider partiellen Unique-Constraints
# (``event_idem_key_per_user_uniq``/``workitem_idem_key_per_user_uniq``).
# Der Name existiert genau HIER — Prüfstellen nutzen ``is_idempotency_conflict``.
IDEMPOTENCY_UNIQUE_CONSTRAINT = "idem_key_per_user_uniq"


def is_idempotency_conflict(exc) -> bool:
    """Ist dieser ``IntegrityError`` ein Idempotenz-Duplikat (R6, Refs #1443)?

    Zwei parallele Replays desselben Keys passieren beide den Cache-Check
    (get-then-set-Fenster); der DB-Unique-Constraint fängt das Duplikat.
    NUR der Idempotenz-Unique-Constraint darf als Duplikat gelten — ein
    fachfremder IntegrityError im selben Replay-Request würde sonst
    fälschlich als Duplikat maskiert, sobald zufällig eine passende
    Key-Zeile existiert. Die Typ-Prüfung hält das Prädikat auch außerhalb
    eines ``except IntegrityError``-Blocks safe.
    """
    return isinstance(exc, IntegrityError) and IDEMPOTENCY_UNIQUE_CONSTRAINT in str(exc)


# Refs #2015: partieller Unique-Index auf PENDING-Loeschantraegen
# (``models/workitem.py``, Refs #530). Der Name existiert genau HIER —
# Pruefstellen nutzen ``is_pending_deletion_request_conflict``.
PENDING_DELETION_REQUEST_UNIQUE_CONSTRAINT = "unique_pending_deletion_request"


def is_pending_deletion_request_conflict(exc) -> bool:
    """Ist dieser ``IntegrityError`` ein paralleler Loeschantrag (Refs #2015)?

    Die vier ``request_*``-Creator sind get-then-create: zwischen dem
    „laeuft schon einer?"-Lookup und dem ``create`` liegt ein Fenster, in dem
    ein zweiter Request denselben Antrag anlegen kann. Der partielle
    Unique-Index macht den Verlierer sicher — die View beantwortet ihn dann
    idempotent statt mit 500.

    Eng gefasst wie :func:`is_idempotency_conflict` und aus demselben Grund:
    NUR dieser Constraint darf als Duplikat gelten, sonst verschwaende ein
    fachfremder ``IntegrityError`` hinter einer „Antrag existiert bereits"-
    Meldung. Die Typ-Pruefung haelt das Praedikat auch ausserhalb eines
    ``except IntegrityError``-Blocks safe.
    """
    return isinstance(exc, IntegrityError) and PENDING_DELETION_REQUEST_UNIQUE_CONSTRAINT in str(exc)


class IdempotentCreateMixin:
    """Gemeinsamer Idempotenz-Guard des Offline-Replay-Schreibpfads (F-09,
    Refs #1109/#1831).

    Bricht der Offline-Client die Verbindung nach erfolgreichem Server-Write,
    aber vor Empfang der Response ab, spielt er dieselbe Queue-Zeile erneut.
    Das Mixin bündelt die identische Sequenz — Key normalisieren, Payload-
    Fingerprint (N14), Cache-Lookup, Hash-Vergleich, Mismatch-422 — und
    delegiert nur das Ressourcen-spezifische:

    * ``idempotency_scope`` — Cache-Scope (z. B. ``"event_create"``).
    * ``on_idempotent_hit()`` — die Erfolgsform des Replays (Redirect aufs
      Original bzw. die View-eigene Erfolgsantwort).

    Der **DB-Backstop** (R5: Cache-Eviction/Neustart überlebt nur der
    Constraint; R6: parallele Replays) bleibt bewusst in der View — der
    Lookup ist je Ressource verschieden (Model, Filter, Redirect-Ziel,
    Payload-Hash-Prüfung nur wo persistent vorhanden). Ein gefangener
    ``IntegrityError`` wird über :func:`is_idempotency_conflict` als
    Duplikat erkannt.
    """

    idempotency_scope: str

    def read_idempotency(self, request) -> tuple[str | None, str | None]:
        """Key (Header ``X-Idempotency-Key``) + Payload-Hash lesen (N14).

        Ungültige Keys degradieren zum Legacy-Verhalten ohne Dedup
        (:func:`normalize_idempotency_key`).
        """
        idem_key = normalize_idempotency_key(request.headers.get("X-Idempotency-Key"))
        idem_payload_hash = payload_fingerprint(request) if idem_key else None
        return idem_key, idem_payload_hash

    def check_replay(self, request, idem_key: str | None):
        """Cache-Hit kurzschließen (Fast-Path des Guards).

        Liefert die fertige ``HttpResponse`` (Hook-Ergebnis bzw. 422 bei
        Key-Kollision mit abweichendem Body, N14/Refs #1424) oder ``None``,
        wenn kein Key mitkam oder der Schlüssel noch nicht erfolgreich
        verarbeitet wurde. Legacy-Cache-Einträge (nackter PK-String aus der
        Zeit vor N14) gelten als Treffer ohne Hash-Prüfung.

        Refs #1942: Der Payload-Vergleich leitet sich seither aus dem
        ``request`` selbst ab, damit auch ein Fingerprint einer frueheren
        Schluessel-Generation nachrechenbar bleibt — ein vorberechneter Hash
        als Parameter waere dafuer zu wenig. Der von
        :meth:`read_idempotency` gelieferte Hash wird weiterhin gebraucht,
        aber erst beim Merken des Ergebnisses.
        """
        if not idem_key:
            return None
        hit = get_idempotent_result(self.idempotency_scope, request.user.pk, idem_key)
        if hit is None:
            return None
        if has_idempotency_payload_mismatch(hit.payload_hash, request):
            return _idempotency_mismatch_response()
        return self.on_idempotent_hit(request, hit)

    def on_idempotent_hit(self, request, hit):
        """Erfolgsform des Replays — je Ressource (Redirect-Ziel/Partial)."""
        raise NotImplementedError

    def remember_idempotency(self, request, idem_key: str | None, idem_payload_hash: str | None, result_pk) -> None:
        """Ergebnis eines erfolgreichen Writes unter dem Key merken (No-op ohne Key)."""
        remember_idempotent_result(
            self.idempotency_scope, request.user.pk, idem_key, result_pk, payload_hash=idem_payload_hash
        )


SIGNED_IN_LINK_NOTICE_TEMPLATE = "auth/signed_in_link_notice.html"


def anonymous_session_required(view_func):
    """Process an account link only in a session that is not signed in (Refs #2391).

    Invite, password reset and unlock links act on the account the link
    belongs to, and their writes need the database context of that account.
    A signed-in session carries the context of its own account instead:
    writing from it violates the row-level security of the audit log, and
    switching it would point a session that stays signed in at another
    facility. Signing the session out on the fly is no way out either, because
    it skips the local clean-up of a regular sign-out.

    A signed-in session therefore gets a notice with the regular sign-out form
    instead, for GET and POST alike, for every role and also for the account
    of the link itself. Applied to ``dispatch`` with ``method_decorator``, the
    check runs before the view reads or uses a token and before anything
    touches the database context. The notice is a 403: the request is refused
    in the current session state, as with the other notices that carry a
    sign-out form (archived or missing facility).
    """

    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_authenticated:
            return render(request, SIGNED_IN_LINK_NOTICE_TEMPLATE, status=403)
        return view_func(request, *args, **kwargs)

    return _wrapped_view


class AssistantOrAboveRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for all authenticated roles (Assistant, Staff, Lead, Admin)."""

    def test_func(self):
        return self.request.user.is_assistant_or_above


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for Staff, Lead and Admin only."""

    def test_func(self):
        return self.request.user.is_staff_or_above


class LeadOrAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for Lead and Admin only."""

    def test_func(self):
        return self.request.user.is_lead_or_admin


class DeletionConfirmerRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for holders of the right „Löschbestätigung" (Refs #1053).

    Der Vier-Augen-Genehmiger-Pool wird über ``can_confirm_deletion``
    kuratiert statt aus der Rolle abgeleitet — löst den Deadlock bei
    einer einzelnen Leitung ohne erreichbare Anwendungsbetreuung.
    """

    def test_func(self):
        return self.request.user.can_confirm_deletion


class DeletionRequestListAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Liste der Löschanträge: Leitung/Admin (Transparenz über eigene
    Anträge), Träger des Rechts „Löschbestätigung" (Refs #1053) sowie —
    beschränkt auf die eigenen Anträge — wer selbst einen offenen Antrag
    gestellt hat (Refs #1736 B).

    Das Gate lebt als ``User.can_access_deletion_requests`` am Modell, damit
    Navigation und View garantiert dieselbe Bedingung auswerten.
    """

    def test_func(self):
        return self.request.user.can_access_deletion_requests


class FacilityAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for Anwendungsbetreuung (facility_admin) only.

    Refs #867: bisheriger ``AdminRequiredMixin`` umbenannt — die Rolle ist
    auf eine Einrichtung beschraenkt. Fuer installations-weiten Zugriff
    siehe ``SuperAdminRequiredMixin``.
    """

    def test_func(self):
        return self.request.user.is_facility_admin


class SuperAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Access for Systemadministration (super_admin) only.

    Refs #867: oberste Rolle, installations-weit, kein Facility-Bezug.
    Persona Jonas (hostet die Installation). Wird fuer den
    ``/system/``-Bereich verwendet.
    """

    def test_func(self):
        return self.request.user.is_super_admin


# --- HTMX Helper-Mixin (Refs #745) --------------------------------------
#
# ``HTMXPartialMixin`` ist seit #745 in allen Listen-Views mit echtem
# Partial/Full-Branching umgesetzt; verbleibende ``HX-Request``-Checks im
# Code sind bewusste Sonderfälle (s. Docstring von ``HTMXPartialMixin``).


class PaginatedListMixin:
    """Refs #803 (C-36): Eine Stelle fuer ``Paginator(qs, N).get_page(...)``.

    Vorher haben drei Listen-Views (clients, cases, audit) den Paginator
    direkt gebaut, mit unterschiedlichen Page-Sizes (25 vs. 50) und
    teilweise inline-importiertem ``safe_page_param``. Mit dem Mixin
    haengt die Page-Size am View, der Paginator wird einmal gebaut und
    durch ``safe_page_param`` gegen ``page=999999``-Angriffe geschuetzt.
    """

    page_size: int | None = None

    def paginate(self, queryset, request):
        from django.core.paginator import Paginator

        from core.constants import DEFAULT_PAGE_SIZE
        from core.views.utils import safe_page_param

        size = self.page_size if self.page_size is not None else DEFAULT_PAGE_SIZE
        return Paginator(queryset, size).get_page(safe_page_param(request))


class FilteredPaginatedListMixin(PaginatedListMixin):
    """Refs #1164 (R5): Buendelt die q-Suche-, Equality-Filter- und
    ``pagination_params``-Boilerplate der Listen-Views (clients, cases).

    Drei kleine, opt-in Helfer — bewusst *kein* generisches Filter-
    Framework (YAGNI). Die View baut weiterhin ihr Basis-Queryset,
    ``annotate``/``select_related``/``order_by`` und den Context selbst;
    der Mixin uebernimmt nur das wiederholte Muster:

    * ``search_fields`` — Liste von Modellfeld-Lookups (z. B.
      ``["pseudonym"]``). ``apply_search`` filtert per ``__ilike``-
      OR ueber alle Felder, wenn ``?q=`` (getrimmt) nicht leer ist.
    * ``filter_fields`` — Mapping ``GET-Param -> Modellfeld`` (z. B.
      ``{"stage": "contact_stage"}``). ``apply_filters`` haengt fuer
      jeden gesetzten Param ein ``.filter(feld=wert)`` an.
    * ``pagination_params`` — baut den Querystring fuer Paginierungs-
      Links aus genau den Roh-GET-Werten neu, die auch gefiltert
      wurden (leere weggelassen). ``extra_params`` erlaubt View-eigene
      Zusatzparameter (z. B. Audit-Datumsfelder), ohne den Mixin zu
      generalisieren.

    Felder mit Sonderlogik (Datums-Parsing, Sentinels, ``__in``-Listen)
    bleiben in der View — dafuer ``extra_params`` bzw. eigener Code.
    """

    search_fields: list[str] = []
    filter_fields: dict[str, str] = {}

    def get_search_term(self, request) -> str:
        """Den getrimmten ``?q=``-Wert (oder ``""``)."""
        return request.GET.get("q", "").strip()

    def apply_search(self, queryset, request):
        """``__ilike``-OR ueber ``search_fields``, falls ``?q=`` gesetzt."""
        q = self.get_search_term(request)
        if q and self.search_fields:
            from django.db.models import Q

            condition = Q()
            for field in self.search_fields:
                condition |= Q(**{f"{field}__ilike": q})
            queryset = queryset.filter(condition)
        return queryset

    def apply_filters(self, queryset, request):
        """Equality-Filter aus ``filter_fields`` fuer jeden gesetzten Param."""
        for param, field in self.filter_fields.items():
            value = request.GET.get(param)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def pagination_params(self, request, extra_params=None) -> str:
        """Querystring fuer Paginierungs-Links aus den aktiven Filtern.

        ``q`` (falls ``search_fields`` gesetzt), die ``filter_fields``-
        Params und optionale ``extra_params`` (Mapping) — leere Werte
        werden weggelassen. Reihenfolge: ``q``, dann ``filter_fields``,
        dann ``extra_params`` (wie in der Original-View aufgebaut).
        """
        from urllib.parse import urlencode

        params: dict[str, str] = {}
        if self.search_fields:
            params["q"] = self.get_search_term(request)
        for param in self.filter_fields:
            params[param] = request.GET.get(param, "")
        if extra_params:
            params.update(extra_params)
        return urlencode({k: v for k, v in params.items() if v})


def htmx_absage(message, status=409) -> HttpResponse:
    """Die fachliche Absage einer HTMX-Aktion: kurzer Klartext, kein Swap.

    Fuer Kaesten und Kacheln, die nach der Absage unveraendert sichtbar
    bleiben sollen — etwa der Kasten „Wirkungsziele“ mit seinen Meilensteinen,
    die Ereignisliste eines Falls, das Statistik-Dashboard oder die
    Vorschlagskarte einer Loeschfrist-Formularvalidierung. Refs #2358: Seit
    der globalen 4xx-Swap-Regel in ``base.html`` (``responseHandling``, Refs
    #2232) wechselt htmx jede 4xx-Antwort in das ``hx-target`` des
    ausloesenden Elements ein. Eine fachliche Absage (Einschraenkung nach
    Art. 18, Zustandsvertrag der Fallakte, ungueltiger Statistik-Zeitraum,
    fehlende Hold-Begruendung ...) ist aber kein Partial: eingewechselt
    ersetzte der Klartext den ganzen Kasten, und dessen Inhalt war erst nach
    dem Neuladen wieder sichtbar.

    ``HX-Reswap: none`` schaltet den Swap fuer genau diese Antwort ab — der
    Kasten bleibt mit seinem bisherigen Inhalt stehen. Weil nicht
    eingewechselt wurde, zeigt der globale ``htmx:responseError``-Toast
    (``static/js/htmx-errors.js``) den Antworttext als Hinweis. Deshalb genau
    EINE kurze, markup-freie Meldung.

    ``status`` faellt auf 409 zurueck (bisheriges Verhalten, alle
    Bestandsaufrufer). Refs #2385: Formular-/Parametervalidierung antwortet
    fachlich mit 400 — beide Statuscodes durchlaufen dieselbe ``4..``-Zeile
    der ``responseHandling``-Tabelle und damit denselben Swap-Mechanismus.

    Wo die Meldung bewusst an die Stelle des geklickten Elements treten soll
    (Vorschlagskarte im Loeschfristen-Dashboard bei aktivem Hold bzw.
    Doppel-Klick, Refs #2321), bleibt es beim einfachen 409 ohne diesen
    Header.
    """
    response = HttpResponse(message, status=status)
    response["HX-Reswap"] = "none"
    return response


class HTMXPartialMixin:
    """Rendert je nach ``HX-Request``-Header ein Partial- oder Full-Page-
    Template. Erwartet ``partial_template_name`` und ``template_name`` als
    Klassenattribute.

    Nur für den einfachen Render-Branch ("ein Partial vs. ein Full-Page-
    Template"). Folgende Fälle bleiben bewusst inline und nutzen den
    Mixin **nicht** — die Sonderfälle nicht versehentlich migrieren:

    * **Bulk-Actions mit ``HX-Redirect``-Response** (z. B.
      ``RetentionBulkApproveView``, ``WorkItemBulkStatusView``): der HTMX-
      Pfad antwortet mit ``HX-Redirect``-Header statt einem Partial.
    * **API-Format-Negotiation** zwischen JSON und HTML (z. B.
      ``_wants_json_response`` in :file:`views/_json_contracts.py`, genutzt
      von ``EventUpdateView`` und ``WorkItemUpdateView``): hier ist die
      Abzweigung kein Render-Branch, sondern ein Format-Branch.
    * **Mehrere Partials pro View** oder bedingtes Rendern abhängig vom
      Request-Pfad: bleibt explizit, weil der Mixin nur ein einziges
      Partial-Template kennt.
    """

    template_name: str | None = None
    partial_template_name: str | None = None

    def is_htmx(self):
        return self.request.headers.get("HX-Request") == "true"

    def render_htmx_or_full(self, context):
        from django.shortcuts import render

        template = self.partial_template_name if self.is_htmx() else self.template_name
        if template is None:
            raise ValueError("HTMXPartialMixin erfordert partial_template_name und template_name.")
        return render(self.request, template, context)


class ExportRangeMixin:
    """Gemeinsame ``date_from``/``date_to``-Aufloesung der Statistik-Export-Views.

    Refs #1827: CSV-, PDF-, Sachbericht- und Dataset-Export lasen die beiden
    Query-Parameter, validierten sie gegen ``validate_statistics_range`` und
    antworteten bei Fehlern identisch mit einer 400-``HttpResponse`` — 4x
    wortgleich kopiert (Anlass: vier Folgefixes am selben Block in
    ``statistics.py``, siehe #1827-Rationale).

    ``resolve_range`` gibt entweder das ``(date_from, date_to)``-Tupel zurueck
    oder direkt die fertige 400-Antwort; die Aufrufstelle unterscheidet per
    ``isinstance``, weil die Views hier bewusst mit einer Antwort statt einer
    Exception abbrechen (kein neuer Kontrollfluss-Mechanismus fuer eine
    Handvoll Aufrufer)::

        result = self.resolve_range(request)
        if isinstance(result, HttpResponse):
            return result
        date_from, date_to = result
    """

    def resolve_range(self, request):
        from django.http import HttpResponse
        from django.utils.translation import gettext as _

        from core.services.audit.detail_policy import InvalidAuditDetailValueError, validate_release_metadata
        from core.services.dashboard import InvalidStatisticsPeriodError, validate_statistics_range
        from core.utils.formatting import parse_date

        try:
            validate_release_metadata({field: request.GET.get(field, "") for field in ("recipient", "purpose")})
        except InvalidAuditDetailValueError:
            return HttpResponse(
                _(
                    "Empfänger und Zweck dürfen höchstens 200 Zeichen und keine E-Mail-Adressen, "
                    "Telefonnummern oder Klarnamen enthalten."
                ),
                status=400,
            )

        date_from = parse_date(request.GET.get("date_from"), None)
        date_to = parse_date(request.GET.get("date_to"), None)
        if not date_from or not date_to:
            return HttpResponse(_("date_from und date_to erforderlich"), status=400)
        try:
            validate_statistics_range(date_from, date_to)
        except InvalidStatisticsPeriodError as exc:
            return HttpResponse(str(exc), status=400)
        return date_from, date_to


def log_export(request, target_type, *, format, date_from, date_to, extra=None, artifact_released=True):
    """Export-Audit-Zeile: Basisdetail + optionale Zusatzfelder + Freigabe-Metadaten.

    Refs #1827: buendelt den 4x identischen
    ``log_audit_event(..., AuditLog.Action.EXPORT, ...)``-Aufruf aus CSV-,
    PDF-, Sachbericht- und Dataset-Export. ``extra`` haengt View-spezifische
    Detail-Felder an (z. B. den Doktyp-Filter oder den Backend-Slug); die
    Reihenfolge der Dict-Keys ist irrelevant, weil der Audit-Hash die Details
    ueber ``json.dumps(..., sort_keys=True)`` kanonisiert (Refs
    ``services/audit/chain.py``).
    """
    from core.models import AuditLog
    from core.services.audit import log_audit_event
    from core.services.dashboard import release_detail

    log_audit_event(
        request,
        AuditLog.Action.EXPORT,
        target_type=target_type,
        detail={
            "format": format,
            "date_from": str(date_from),
            "date_to": str(date_to),
            **(extra or {}),
            **release_detail(request.GET, artifact_released=artifact_released),
        },
    )
