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

from core.reporting.base import DatasetBackend

_SLUG_RE = re.compile(r"^[a-z0-9_]+$")

# Singleton-Instanzen je Slug. Direkt am Modul gepflegt (kein Proxy), damit
# Tests per ``monkeypatch.setattr(registry, "_REGISTRY", ...)`` isolieren
# koennen.
_REGISTRY: dict[str, DatasetBackend] = {}

# Discovery-Flag fuer Idempotenz — ``_discover()`` scannt ``backends/``
# hoechstens einmal pro Prozess.
_discovered = False


def register_backend(slug: str) -> Callable[[type[DatasetBackend]], type[DatasetBackend]]:
    """Decorator: registriert eine ``DatasetBackend``-Klasse unter ``slug``.

    Setzt ``cls.slug = slug`` auf der Klasse und legt eine Instanz im
    Registry-Dict ab. Wirft :class:`ImproperlyConfigured` bei ungueltigem
    Slug-Format oder Doppelregistrierung desselben Slugs.
    """

    def decorator(cls: type[DatasetBackend]) -> type[DatasetBackend]:
        if not _SLUG_RE.fullmatch(slug):
            raise ImproperlyConfigured(f"Ungültiger Reporting-Backend-Slug {slug!r} — erwartet ^[a-z0-9_]+$.")
        if slug in _REGISTRY:
            raise ImproperlyConfigured(f"Reporting-Backend-Slug {slug!r} ist bereits registriert.")
        cls.slug = slug
        _REGISTRY[slug] = cls()
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
