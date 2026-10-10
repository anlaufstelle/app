"""Plugin-Registry fuer Reporting-Backends mit Lazy-Auto-Discovery (M5, Refs #1061).

``@register_backend("slug")`` traegt eine konkrete
:class:`~core.reporting.base.DatasetBackend`-Unterklasse in ein
Modul-Dict (:data:`_REGISTRY`) ein — als **Singleton-Instanz**, da Backends
zustandslos sind (kein Konstruktor-Zustand, jeder Aufruf bekommt Facility/
Zeitraum explizit als Argumente).

**Lazy-Auto-Discovery statt ``AppConfig.ready()``:** Kuenftige Backends
importieren ``core.models`` (fuer Queries gegen Facility-Daten). Wuerde
:mod:`core.reporting.backends` beim Django-App-Start (``ready()``) oder
bereits beim Import dieses Moduls gescannt, entstuende ein Importzyklus
sobald Backends selbst wieder Code importieren, das (mittelbar) auf
``core.reporting`` zurueckverweist. Der Decorator-Import
``from core.reporting.registry import register_backend`` bleibt dagegen
immer zyklusfrei, weil dieses Modul das ``backends``-Package erst **lazy**
beim ersten ``get_*``-Aufruf laedt (:func:`_discover`, ueber das
Modul-Flag ``_discovered`` idempotent) — nie zur Modul-Importzeit.
"""

import importlib
import logging
import pkgutil
import re
from collections.abc import Callable

from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext as _

from core.reporting.base import EXCLUDED_KEY, DatasetBackend, feld_bindungen, typ_bindungen

logger = logging.getLogger(__name__)

# Kleinbuchstaben/Ziffern, getrennt durch einzelne ``-``/``_`` (Refs #1739:
# ``sample-report-1`` braucht den Bindestrich). Trennzeichen am Rand oder
# doppelt sind bewusst ungueltig — der Slug landet als Dateinamen-Praefix
# (``export_dataset``), als XML-Attribut und als ``backend_data``-Schluessel.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")

# Singleton-Instanzen je Slug. Direkt am Modul gepflegt (kein Proxy), damit
# Tests per ``monkeypatch.setattr(registry, "_REGISTRY", ...)`` isolieren
# koennen.
_REGISTRY: dict[str, DatasetBackend] = {}

# Discovery-Flag fuer Idempotenz — ``_discover()`` scannt ``backends/``
# hoechstens einmal pro Prozess.
_discovered = False


def _vokabular(backends, sammler) -> dict[str, str]:
    """Schluessel → Label ueber alle von ``sammler`` gelieferten Bindungen.

    Die Reihenfolge folgt ``get_backends()`` (slug-sortiert) und innerhalb eines
    Backends der Deklaration — deterministisch, damit ein Dropdown nicht je
    Prozessstart anders aussieht. Kollisionen sind unkritisch, weil
    :func:`_pruefe_bindungen` identische Labels je Schluessel erzwingt.
    """
    vokabular: dict[str, str] = {}
    for backend in backends:
        for bindung in sammler(backend):
            vokabular.update(bindung.vokabular())
    return vokabular


def _ueber_report_key(sammler):
    """Sammler-Wrapper: nur Bindungen, die tatsaechlich ueber ``report_key`` binden.

    **Der Werteraum von** ``report_key`` **ist nicht die Menge aller deklarierten
    Schluessel** (Refs #1849, Disclosure-Review). ``ban_overview`` bindet ueber
    ``system_type`` (Konzept #1847 §3) und deklariert dabei den Schluessel
    ``ban``. Stuende der im Vokabular, boete ihn das Dropdown zur Auswahl an —
    und ein so gesetzter ``report_key`` bindet **nichts**: Er wuerde weder als
    „ohne Zuordnung" gemeldet (der Schluessel ist ja gesetzt) noch als
    „unbekannt" (er steht ja im Vokabular), und waere nach dem Setzen
    unwiderruflich. Genau die Silent-Nothing-Klasse, die Entscheid E-H
    schliessen soll. Das Paketformat normalisiert ``ban`` obendrein auf
    ``excluded`` — ein Dropdown, das ihn anbietet, widerspraeche ihm.

    Gilt symmetrisch auf Feld-Ebene: eine ``FeldBindung`` mit ``bindet="slug"``
    reagiert nicht auf ``FieldTemplate.report_key``.

    **Nicht** angewendet auf :func:`describe_type_bindings` und den
    Label-Guard: „erscheint in …" muss ``ban_overview`` weiterhin nennen (dort
    entscheidet der ``system_type``, und der Treffer ist echt), und ein Label
    darf je Schluessel auch ueber die Bindungsarten hinweg nur einmal vergeben
    sein.
    """

    def _gefiltert(backend):
        return tuple(bindung for bindung in sammler(backend) if bindung.bindet == "report_key")

    return _gefiltert


def _pruefe_bindungen(backend: DatasetBackend) -> None:
    """Die beiden Deklarationsregeln aus Konzept #1847 §3 — laut statt still.

    1. ``excluded`` ist reserviert („gehoert ausdruecklich in keinen Bericht",
       E3). Ein Backend, das ihn deklariert, machte den Ausschluss selbst zu
       einer Berichtszeile.
    2. **Ein Schluessel, ein Label.** Mehrfach-Deklaration ist der Normalfall
       (``contact`` steckt in ``sample_report_1`` UND ``counseling_hours``) —
       zwei verschiedene Labels dazu waeren zwei Dropdown-Eintraege fuer
       dieselbe Sache. Verglichen wird ueber ``str()``: Labels sind
       ``gettext_lazy``-Proxies und muessen fuer den Vergleich in der beim
       Import aktiven Sprache ausgewertet werden.
    3. **Kein Schluessel doppelt in EINER Deklaration.** ``vokabular()``,
       ``ziel_map()`` und ``_eintrag()`` sind Dicts — ein zweiter Eintrag mit
       demselben Schluessel verschwindet dort still, und die Aufloesung folgt
       ihm nicht. Das faellt sonst erst auf, wenn Zahlen fehlen.

    Typ- und Feld-Schluessel sind **getrennte Werteraeume** (Konzept §3.1):
    derselbe Name darf auf beiden Ebenen etwas anderes bedeuten.
    """
    for sammler in (typ_bindungen, feld_bindungen):
        bestand = _vokabular(_REGISTRY.values(), sammler)
        for bindung in sammler(backend):
            keys = bindung.keys()
            doppelt = sorted({key for key in keys if keys.count(key) > 1})
            if doppelt:
                raise ImproperlyConfigured(
                    f"Reporting-Backend {backend.slug!r} deklariert Berichtsschlüssel mehrfach in "
                    f"derselben Bindung: {doppelt} — der zweite Eintrag würde in den abgeleiteten "
                    "Dicts still verschwinden."
                )
            for key, label in bindung.vokabular().items():
                if key == EXCLUDED_KEY:
                    raise ImproperlyConfigured(
                        f"Reporting-Backend {backend.slug!r} deklariert den reservierten "
                        f"Berichtsschlüssel {EXCLUDED_KEY!r} — er bedeutet: gehört in keinen Bericht."
                    )
                bekannt = bestand.get(key)
                if bekannt is not None and str(bekannt) != str(label):
                    raise ImproperlyConfigured(
                        f"Berichtsschlüssel {key!r} ist mit zwei verschiedenen Labels deklariert "
                        f"({str(bekannt)!r} vs. {str(label)!r}, zuletzt in {backend.slug!r}) — "
                        "das Vokabular-Label muss je Schlüssel eindeutig sein."
                    )
                bestand[key] = label


def register_backend(slug: str) -> Callable[[type[DatasetBackend]], type[DatasetBackend]]:
    """Decorator: registriert eine ``DatasetBackend``-Klasse unter ``slug``.

    Setzt ``cls.slug = slug`` auf der Klasse und legt eine Instanz im
    Registry-Dict ab. Wirft :class:`ImproperlyConfigured` bei ungueltigem
    Slug-Format, bei Doppelregistrierung desselben Slugs und bei einer
    ungueltigen Bindungs-Deklaration (:func:`_pruefe_bindungen`).
    """

    def decorator(cls: type[DatasetBackend]) -> type[DatasetBackend]:
        if not _SLUG_RE.fullmatch(slug):
            raise ImproperlyConfigured(f"Ungültiger Reporting-Backend-Slug {slug!r} — erwartet {_SLUG_RE.pattern}.")
        if slug in _REGISTRY:
            raise ImproperlyConfigured(f"Reporting-Backend-Slug {slug!r} ist bereits registriert.")
        cls.slug = slug
        backend = cls()
        _pruefe_bindungen(backend)
        _REGISTRY[slug] = backend
        return cls

    return decorator


def _discover() -> None:
    """Importiert einmalig alle Module unter ``core.reporting.backends``.

    Idempotent ueber das Modul-Flag ``_discovered``. Der Import des
    ``backends``-Package selbst passiert erst hier (lazy) — siehe
    Modul-Docstring zur Zyklenfreiheit.

    Scheitert der Import eines Backend-Moduls, wird der Fehler als
    :class:`ImproperlyConfigured` mit dem Modulnamen re-raised **und
    ``_discovered`` zurueckgesetzt**: sonst gaelte die Discovery als
    erledigt und jeder weitere ``get_backends()``-Aufruf lieferte still
    eine unvollstaendige Registry (fehlendes Backend statt lauter Fehler).

    Ein :class:`ImproperlyConfigured` **aus dem Modul selbst** (z. B.
    ``register_backend`` bei ungueltigem Slug oder Doppelregistrierung) wird
    unveraendert durchgereicht statt in die generische „nicht importierbar"-
    Meldung gewickelt zu werden — sonst verschleift die Wrapper-Meldung die
    eigentliche Registrierungsursache hinter Import-Vokabular.
    """
    global _discovered
    if _discovered:
        return
    _discovered = True

    from core.reporting import backends

    for _finder, module_name, _is_pkg in pkgutil.iter_modules(backends.__path__):
        dotted_name = f"{backends.__name__}.{module_name}"
        try:
            importlib.import_module(dotted_name)
        except ImproperlyConfigured:
            _discovered = False
            raise
        except Exception as exc:
            _discovered = False
            msg = f"Reporting-Backend-Modul {dotted_name!r} ist nicht importierbar: {exc}"
            raise ImproperlyConfigured(msg) from exc


def get_backends() -> list[DatasetBackend]:
    """Alle registrierten Backend-Instanzen, sortiert nach ``slug``. Loest Discovery aus."""
    _discover()
    return sorted(_REGISTRY.values(), key=lambda backend: backend.slug)


def get_backend(slug: str) -> DatasetBackend:
    """Die registrierte Instanz zu ``slug``. ``KeyError`` bei unbekanntem Slug."""
    _discover()
    return _REGISTRY[slug]


def backend_is_available(backend: DatasetBackend, facility: object, *, on_error: Callable | None = None) -> bool:
    """``backend.is_available(facility)`` mit Fehlerisolation (Refs #2070, #2118).

    ``is_available`` ist kein reiner Praedikat-Getter: die Default-Ableitung aus
    :attr:`DatasetBackend.verfuegbarkeit` setzt eine echte DB-Query ab
    (``Anforderung.erfuellt``). Ein einziges werfendes Backend riss damit jeden
    Aufrufer mit — bis #2070 die Statistik-Views, danach weiterhin die beiden
    Konfigurationsseiten und das Bindungs-Inventar, die ueber
    :func:`describe_type_bindings`/:func:`describe_field_bindings` hier
    hereinkommen.

    Ein Wurf bedeutet „nicht verfuegbar": dasselbe Ergebnis wie ein falsy
    Rueckgabewert, damit Sichtbarkeit (Liste) und Erreichbarkeit (Panel/Export
    → 404) nicht auseinanderlaufen. Fail-closed ist auch die sichere Richtung —
    ein Bericht, dessen Verfuegbarkeitspruefung nicht laeuft, ist nicht
    nachweislich freigegeben.

    **Die Isolation sitzt bewusst hier und nicht in den Aufrufern** (Revision
    der #2070-Begruendung): vier ungeschuetzte Aufrufer in drei Schichten
    (Views, Management-Command) haetten sonst dieselbe ``try/except``-Klammer
    viermal wiederholt, und der fuenfte neue Aufrufer haette sie wieder
    vergessen. ``logging`` macht dieses Modul nicht webframework-abhaengig; die
    ``django.http``-Importe in :func:`get_backend_or_404` bleiben lazy.

    ``on_error`` ist der Weg fuer Aufrufer, die den Ausfall zusaetzlich
    **sichtbar** machen muessen (Kommando-``stderr``), ohne die
    Fail-closed-Semantik zu aendern.
    """
    try:
        return bool(backend.is_available(facility))
    except Exception:
        logger.exception("reporting: is_available des Berichts-Backends %r ist gescheitert", backend.slug)
        if on_error is not None:
            on_error(backend)
        return False


def get_available_backends(facility: object, *, on_error: Callable | None = None) -> list[DatasetBackend]:
    """``get_backends()``, gefiltert auf ein **truthy** ``is_available(facility)``.

    Panel- und Export-View verwenden dasselbe Praedikat (``not
    backend.is_available(facility)`` → HTTP 404), damit Sichtbarkeit und
    Erreichbarkeit nicht auseinanderlaufen. Ein werfendes Backend gilt als
    nicht verfuegbar (:func:`backend_is_available`, Refs #2118).
    """
    return [backend for backend in get_backends() if backend_is_available(backend, facility, on_error=on_error)]


def get_backend_or_404(facility: object, slug: str) -> DatasetBackend:
    """``slug`` → verfuegbares Backend dieser Facility, sonst ``Http404``.

    Ein werfendes ``is_available`` wird wie „nicht verfuegbar" behandelt
    (Refs #2118) — sonst antwortete derselbe Defekt in der Liste mit stillem
    Verschwinden und hier mit einem 500er.

    Refs #1827: Panel- und Export-View loesten das Backend bislang mit
    identischem ``try/except KeyError`` + ``is_available``-Guard auf (siehe
    :func:`get_available_backends`-Docstring: dasselbe Praedikat, damit
    Sichtbarkeit und Erreichbarkeit nicht auseinanderlaufen). Lazy-Import von
    ``django.http``/``gettext``, damit dieses Modul webframework-frei bleibt
    (siehe Modul-Docstring zur Lazy-Discovery).
    """
    from django.http import Http404
    from django.utils.translation import gettext as _

    try:
        backend = get_backend(slug)
    except KeyError:
        raise Http404(_("Unbekanntes Berichts-Backend.")) from None
    if not backend_is_available(backend, facility):
        raise Http404(_("Bericht für diese Einrichtung nicht verfügbar."))
    return backend


def get_report_key_vocabulary() -> dict[str, str]:
    """Alle deklarierten **Typ**-Berichtsschluessel mit ihrem Label (E1, Refs #1824).

    Der Werteraum kommt aus den Backends, nicht aus einer handgepflegten Liste:
    E3 speist damit das ``report_key``-Select am Dokumentationstyp und den
    „kennt ein Bericht diesen Schluessel?"-Vergleich des
    Vollstaendigkeitshinweises. :data:`~core.reporting.base.EXCLUDED_KEY` ist
    hier nie enthalten — er ist reserviert und wird von der Auswahl gesondert
    angeboten.

    **Nur Bindungen ueber** ``report_key`` (:func:`_ueber_report_key`): ein
    ueber ``system_type`` gebundener Schluessel waere waehlbar, ohne etwas zu
    bewirken.
    """
    return _vokabular(get_backends(), _ueber_report_key(typ_bindungen))


def get_field_key_vocabulary() -> dict[str, str]:
    """Alle deklarierten **Feld**-Berichtsschluessel mit ihrem Label.

    Eigener Werteraum neben :func:`get_report_key_vocabulary` (Konzept §3.1):
    auf Feld-Ebene gilt dieselbe Regel, aber eine andere Kardinalitaet — und
    dieselbe Einschraenkung auf Bindungen ueber ``report_key``.
    """
    return _vokabular(get_backends(), _ueber_report_key(feld_bindungen))


def backends_for_report_key(key: str) -> list[DatasetBackend]:
    """Die Backends, in denen ein Dokumentationstyp mit diesem ``report_key`` erscheint.

    Wie :func:`get_report_key_vocabulary` auf Bindungen ueber ``report_key``
    beschraenkt — sonst behauptete die Auskunft einen Treffer, den ein gesetzter
    ``report_key`` gar nicht erzeugen kann.
    """
    sammler = _ueber_report_key(typ_bindungen)
    return [backend for backend in get_backends() if any(key in b.vokabular() for b in sammler(backend))]


def backends_for_field_key(key: str) -> list[DatasetBackend]:
    """Die Backends, in denen eine Feldvorlage mit diesem ``report_key`` erscheint."""
    sammler = _ueber_report_key(feld_bindungen)
    return [backend for backend in get_backends() if any(key in b.vokabular() for b in sammler(backend))]


def _belongs_to_facility(obj, facility) -> bool:
    """Reject foreign model instances while retaining declaration-only callers."""
    return not hasattr(obj, "facility_id") or obj.facility_id == getattr(facility, "pk", None)


def _requirement_state(requirement, document_type, facility, *, active_only=True):
    """Return relevance and missing bindings for one local AND requirement."""
    from core.models import DocumentType

    if not _belongs_to_facility(document_type, facility) or (
        active_only and not getattr(document_type, "is_active", True)
    ):
        return False, None
    if requirement.entity_type is not None and document_type.entity_type != requirement.entity_type:
        return False, None
    if (
        requirement.typ_bindung is None
        and requirement.feld_bindungen
        and getattr(document_type, "report_key", None) == EXCLUDED_KEY
    ):
        return False, None
    missing = []
    matched = False
    if requirement.typ_bindung is not None:
        binding = requirement.typ_bindung
        matched = binding.resolve_key(*_typ_werte(document_type, binding)) is not None
        if not matched:
            if any(_typ_werte(document_type, binding)):
                # An assigned key is immutable; this alternative cannot be completed on this type.
                return False, None
            missing.append(_("Typ-Schlüssel: %(keys)s") % {"keys": _binding_labels(binding)})
    for binding in requirement.feld_bindungen:
        types = DocumentType.objects.for_facility(facility).filter(pk=getattr(document_type, "pk", None))
        # Each binding gets its own query: different fields satisfy the AND.
        present = (
            facility is not None
            and not getattr(getattr(document_type, "_state", None), "adding", False)
            and types.filter(binding.template_q(), fields__field_template__facility=facility).exists()
        )
        matched = matched or present
        if not present:
            missing.append(_("Feld-Schlüssel: %(keys)s") % {"keys": _binding_labels(binding)})
    return matched, missing


def _binding_labels(binding) -> str:
    """Keep alternatives within a binding distinct from required bindings."""
    return _(" oder ").join(f"{label} ({key})" for key, label in binding.vokabular().items())


def _diagnose_backend(backend, document_type, facility, *, field_template=None) -> str | None:
    states = [_requirement_state(a, document_type, facility) for a in backend.verfuegbarkeit]
    if any(missing == [] for _, missing in states):
        return None
    if field_template is not None:
        # Preserve complete local OR alternatives before limiting missing-field feedback.
        states = [
            state
            for requirement, state in zip(backend.verfuegbarkeit, states, strict=True)
            if any(binding.key_for_template(field_template) is not None for binding in requirement.feld_bindungen)
        ]
    alternatives = ["; ".join(missing) for matched, missing in states if matched and missing]
    if not alternatives:
        return None
    return _("Für „%(report)s“ fehlen an diesem Typ noch Zuordnungen: %(requirements)s.") % {
        "report": backend.name,
        "requirements": _(" oder ").join(alternatives),
    }


def diagnose_type_bindings(document_type, facility) -> list[str]:
    """Warn about relevant, incomplete OR alternatives without blocking saves.

    Names come only from backend declarations and the supplied local type.
    Facility-wide availability never suppresses a local configuration warning.
    """
    return [warning for backend in get_backends() if (warning := _diagnose_backend(backend, document_type, facility))]


def _linked_types(field_template, facility, *, active_only=True):
    from core.models import DocumentType

    types = DocumentType.objects.for_facility(facility).filter(
        entity_type="event", fields__field_template=field_template, fields__field_template__facility=facility
    )
    if active_only:
        types = types.filter(is_active=True)
    return types.distinct()


def diagnose_field_bindings(field_template, facility) -> list[str]:
    """Diagnose only local active types using this relevant field template."""
    if not _belongs_to_facility(field_template, facility):
        return []
    relevant = [
        backend
        for backend in get_backends()
        if any(binding.key_for_template(field_template) is not None for binding in feld_bindungen(backend))
    ]
    warnings = []
    for document_type in _linked_types(field_template, facility):
        for backend in relevant:
            if warning := _diagnose_backend(backend, document_type, facility, field_template=field_template):
                warnings.append(_("Typ „%(type)s“: %(warning)s") % {"type": document_type.name, "warning": warning})
    return warnings


def describe_type_bindings(document_type, facility) -> list[str]:
    """List available reports that count this type, including incomplete or inactive types."""
    if not _belongs_to_facility(document_type, facility):
        return []
    descriptions: list[str] = []
    for backend in get_available_backends(facility):
        for requirement in backend.contributions_for_type(document_type, facility):
            binding = requirement.typ_bindung
            if binding is None:
                description = str(backend.name)
            else:
                key = binding.resolve_key(*_typ_werte(document_type, binding))
                if key is None:
                    continue
                description = f"{backend.name} ({binding.vokabular()[key]})"
            if description not in descriptions:
                descriptions.append(description)
    return descriptions


def _typ_werte(document_type, bindung) -> tuple:
    """Die Aufloesungswerte eines Typs — Feldnamen kommen aus der Deklaration.

    Die Berichts-Backends binden ueber ``report_key``, ``ban_overview``
    weiterhin ueber ``system_type``. Welche Attribute zu lesen sind, sagt die
    Bindung selbst
    (:meth:`~core.reporting.base.TypBindung.values_fields`) — ein hier fest
    verdrahtetes ``document_type.system_type`` waere wieder die zweite Wahrheit,
    die #1824 beseitigt hat, und liesse die Auskunft still am Bericht
    vorbeilaufen.
    """
    return tuple(getattr(document_type, feld) for feld in bindung.values_fields())


def describe_field_bindings(field_template, facility) -> list[str]:
    """List available reports reading this exact template, including historical types."""
    if not _belongs_to_facility(field_template, facility):
        return []
    descriptions: list[str] = []
    for backend in get_available_backends(facility):
        for binding in feld_bindungen(backend):
            key = binding.key_for_template(field_template)
            if key is None:
                continue
            if (backend.verfuegbarkeit or hasattr(field_template, "facility_id")) and not any(
                backend.uses_field_template(field_template, doc_type, facility, binding)
                for doc_type in _linked_types(field_template, facility, active_only=False)
            ):
                continue
            descriptions.append(f"{backend.name} ({binding.vokabular()[key]})")
    return descriptions
