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
import pkgutil
import re
from collections.abc import Callable

from django.core.exceptions import ImproperlyConfigured

from core.reporting.base import EXCLUDED_KEY, DatasetBackend, feld_bindungen, typ_bindungen

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


def get_available_backends(facility: object) -> list[DatasetBackend]:
    """``get_backends()``, gefiltert auf ein **truthy** ``is_available(facility)``.

    Panel- und Export-View verwenden dasselbe Praedikat (``not
    backend.is_available(facility)`` → HTTP 404), damit Sichtbarkeit und
    Erreichbarkeit nicht auseinanderlaufen.
    """
    return [backend for backend in get_backends() if backend.is_available(facility)]


def get_report_key_vocabulary() -> dict[str, str]:
    """Alle deklarierten **Typ**-Berichtsschluessel mit ihrem Label (E1, Refs #1824).

    Der Werteraum kommt aus den Backends, nicht aus einer handgepflegten Liste:
    E2 zeigt daraus „erscheint in …", E3 speist damit das ``report_key``-Select
    am Dokumentationstyp. :data:`~core.reporting.base.EXCLUDED_KEY` ist hier
    nie enthalten — er ist reserviert und wird von der Auswahl gesondert
    angeboten.
    """
    return _vokabular(get_backends(), typ_bindungen)


def get_field_key_vocabulary() -> dict[str, str]:
    """Alle deklarierten **Feld**-Berichtsschluessel mit ihrem Label.

    Eigener Werteraum neben :func:`get_report_key_vocabulary` (Konzept §3.1):
    auf Feld-Ebene gilt dieselbe Regel, aber eine andere Kardinalitaet.
    """
    return _vokabular(get_backends(), feld_bindungen)


def backends_for_report_key(key: str) -> list[DatasetBackend]:
    """Die Backends, in denen ein Dokumentationstyp mit diesem Schluessel erscheint."""
    return [
        backend for backend in get_backends() if any(key in bindung.vokabular() for bindung in typ_bindungen(backend))
    ]


def backends_for_field_key(key: str) -> list[DatasetBackend]:
    """Die Backends, in denen eine Feldvorlage mit diesem Schluessel erscheint."""
    return [
        backend for backend in get_backends() if any(key in bindung.vokabular() for bindung in feld_bindungen(backend))
    ]


def describe_type_bindings(document_type, facility) -> list[str]:
    """„Erscheint in …" fuer einen Dokumentationstyp (E2, Refs #1848).

    Je **verfuegbarem** Backend mit Treffer eine Zeile ``"Backend-Name
    (Label)"`` — die Konfigurations-UI verkettet die Liste mit ``" · "``.
    Baut AUSSCHLIESSLICH auf der E1-API auf
    (:meth:`~core.reporting.base.TypBindung.resolve_key`,
    :meth:`~core.reporting.base.TypBindung.vokabular`,
    :func:`get_available_backends`): keine eigene Aufloesungslogik, dieselbe
    Entscheidung wie ``build_dataset``.

    **Nur verfuegbare Backends** (Review-Befund #1848): gefiltert ueber
    :func:`get_available_backends` — nicht :func:`get_backends`. Ohne den
    Filter behauptete die Auskunft „erscheint in X" auch fuer ein Backend,
    dessen ``is_available(facility)`` False ist (z. B. ``needle-exchange-
    balance`` ohne die dafuer noetigen ``ausgabe``-/``rueckgabe``-
    Feldvorlagen am Typ) — Panel und Export liefern fuer ein nicht
    verfuegbares Backend 404 (:func:`get_available_backends` ist dieselbe
    Praedikat-Quelle wie dort). ``facility`` ist deshalb Pflichtargument, kein
    optionaler Zusatz.

    Iteriert bewusst je **Backend** (nicht je Registry-Schluessel): zwei
    Backends koennen denselben ``system_type`` unterschiedlich (nicht) binden
    (z. B. ``needle_exchange`` kennt ``counseling_hours`` nicht) — nur die
    eigene Deklaration eines Backends darf ueber seinen Treffer entscheiden.
    ``monthly_trend`` importiert die Bindung von ``sample_report_1`` und
    erzeugt deshalb bewusst eine zweite Zeile (Konzept #1847 §3,
    Mehrfach-Deklaration).
    """
    beschreibungen: list[str] = []
    for backend in get_available_backends(facility):
        for bindung in typ_bindungen(backend):
            key = bindung.resolve_key(*_typ_werte(document_type, bindung))
            if key is None:
                continue
            beschreibungen.append(f"{backend.name} ({bindung.vokabular()[key]})")
    return beschreibungen


def _typ_werte(document_type, bindung) -> tuple:
    """Die Aufloesungswerte eines Typs — Feldnamen kommen aus der Deklaration.

    Seit E3 (Refs #1849) binden die Berichts-Backends ueber ``report_key``,
    ``ban_overview`` weiterhin ueber ``system_type``. Welche Attribute zu lesen
    sind, sagt die Bindung selbst
    (:meth:`~core.reporting.base.TypBindung.values_fields`) — ein hier fest
    verdrahtetes ``document_type.system_type`` waere wieder die zweite Wahrheit,
    die #1824 beseitigt hat, und liesse die Auskunft still am Bericht
    vorbeilaufen.
    """
    return tuple(getattr(document_type, feld) for feld in bindung.values_fields())


def resolves_only_via_slug(document_type, facility) -> bool:
    """Haengt die Berichtszuordnung dieses Typs AUSSCHLIESSLICH am Slug? (E3-Gate)

    Vorbedingung fuer die Abschaltung des Slug-Zweitschluessels (Konzept #1847
    §E3.7): kein Typ darf danach still aus dem Bericht fallen. „Nur ueber den
    Slug" heisst: mindestens ein Treffer, und bei **jedem** Treffer war die
    Erststufe leer — sonst haette sie entschieden (dieselbe Auflösungsordnung
    wie :meth:`~core.reporting.base.TypBindung._eintrag`).

    Gefiltert wie :func:`describe_type_bindings` ueber die **verfuegbaren**
    Backends: ein Treffer in einem Bericht, den die Einrichtung gar nicht
    aufrufen kann, ist keine Zuordnung, die verlorengehen koennte.
    """
    treffer = False
    for backend in get_available_backends(facility):
        for bindung in typ_bindungen(backend):
            werte = _typ_werte(document_type, bindung)
            if bindung.resolve_key(*werte) is None:
                continue
            if werte[0]:
                return False
            treffer = True
    return treffer


def describe_field_bindings(field_template, facility) -> list[str]:
    """„Erscheint in …" fuer eine Feldvorlage (E2, Refs #1848) — Entsprechung auf Feld-Ebene.

    Wie :func:`describe_type_bindings`, aber ueber
    :meth:`~core.reporting.base.FeldBindung.key_for_template` (der Rueckweg von
    der Feldvorlage zu ihrem Feld-Berichtsschluessel) und ebenso NUR ueber
    :func:`get_available_backends` gefiltert (dieselbe Begruendung: eine
    Zeile fuer ein nicht verfuegbares Backend waere eine falsche Zusage).

    **Verbleibende Grenze.** Seit E3 (Refs #1849) traegt die Feldvorlage ihren
    Berichtsschluessel selbst; der Treffer prueft aber weiterhin nur die
    **Vorlage**, nicht ob GENAU DIESE Vorlage einem Dokumentationstyp zugeordnet
    ist, der die Berichtsbedingung des Backends erfuellt. ``is_available``
    prueft nur, dass *irgendeine* passende Kombination aus Typ und Feldvorlage
    in der Einrichtung existiert. Eine facility-weit vorhandene, aber nirgends
    zugeordnete Vorlage mit passendem Schluessel kann deshalb als „erscheint in
    …" auftauchen, obwohl sie faktisch nichts beitraegt.
    """
    beschreibungen: list[str] = []
    for backend in get_available_backends(facility):
        for bindung in feld_bindungen(backend):
            key = bindung.key_for_template(field_template)
            if key is None:
                continue
            beschreibungen.append(f"{backend.name} ({bindung.vokabular()[key]})")
    return beschreibungen
