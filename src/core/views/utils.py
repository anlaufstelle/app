"""Geteilte View-Helper.

``safe_page_param`` cappt ``?page=`` auf ``MAX_PAGE``, validiert den
Wert als positive Ganzzahl und faengt fehlerhafte Eingaben (negativ,
``abc``) ab. Vermeidet OFFSET-Seq-Scans bei boesgemeinten oder
fehlerhaften URLs (Refs #733).

``safe_redirect_path`` engt ``?next=``-Eingaben auf same-origin Pfade ein.
``startswith("/")`` allein matcht ``//evil.example/login``, das der
Browser als protokoll-relativ interpretiert (Refs #770).
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.urls import Resolver404, resolve, reverse
from django.utils.http import url_has_allowed_host_and_scheme

from core.constants import MAX_PAGE


def object_back_link(url_name: str, label, **kwargs) -> dict[str, str]:
    """Der Rücklink einer Objekt-Eltern-Seite für ``context["back_link"]`` (Refs #1802).

    Fünf Seitengruppen haben als Heimat ein **Objekt** statt einer Liste
    (Konzept #1802 §3.4) — Episode unter einem Fall, Berichtigung und
    Löschantrag unter einer Person, Ereignis-Bearbeiten unter dem Ereignis,
    Feldgruppe unter einem Dokumentationstyp. Das Objekt liegt in der View
    ohnehin vor; die Registry kann es nicht kennen.

    **Kein Verwandter von** ``safe_redirect_path`` (Regel R3): ``?next=``
    beantwortet „wohin nach dem Absenden", der Rücklink „wo bin ich zuhause".
    Beide dürfen dasselbe Ziel haben, werden aber nie voneinander abgeleitet —
    deshalb nimmt dieser Helfer einen ``url_name``, nie einen rohen Pfad aus
    der Anfrage.
    """
    return {"url": reverse(url_name, kwargs=kwargs), "label": label}


def safe_redirect_path(raw: str | None) -> str:
    """Open-Redirect-Schutz: nur same-origin Pfade akzeptieren.

    Liefert ``raw`` zurueck, wenn es mit genau einem ``/`` beginnt **und**
    Django's ``url_has_allowed_host_and_scheme`` es als same-origin einstuft;
    sonst ``"/"``. Faengt damit ``//evil``, ``http://...``, ``javascript:``,
    Backslash-Tricks wie ``/\\evil`` (Browser lesen ``\\`` wie ``/``),
    leere Strings und ``None`` ab. Vorbild war ``views/sudo_mode._safe_next``.

    ``allowed_hosts=None`` laesst nur relative Pfade ohne Host zu — genau die
    same-origin-Semantik, die wir wollen. Der explizite ``startswith('/')``
    bleibt, damit blanke relative Eingaben (``"evil"``) weiter auf ``"/"``
    normalisiert werden (Django allein wuerde sie als same-origin zulassen).
    """
    if raw and raw.startswith("/") and url_has_allowed_host_and_scheme(raw, allowed_hosts=None):
        return raw
    return "/"


def _declared_http_methods(view) -> set[str] | None:
    """The request methods a resolved view accepts, or ``None`` if it declares none.

    Class-based views declare them by their handler methods;
    ``require_http_methods``/``require_POST`` wrappers keep the allowed list in
    the decorator closure, reachable through the ``__wrapped__`` chain.
    """
    view_class = getattr(view, "view_class", None)
    if view_class is not None:
        return {name.upper() for name in view_class.http_method_names if hasattr(view_class, name)}
    while view is not None:
        code = getattr(view, "__code__", None)
        if code is not None:
            for name, cell in zip(code.co_freevars, view.__closure__ or (), strict=False):
                if name == "request_method_list":
                    return {method.upper() for method in cell.cell_contents}
        view = getattr(view, "__wrapped__", None)
    return None


def _is_plain_path(path: str) -> bool:
    """Whether ``path`` reaches the resolver exactly as written.

    The server decodes percent-escapes and the browser reads a backslash as a
    slash and removes dot segments before it sends the request. Resolving such a
    path as written would judge a different target than the one the navigation
    lands on, so only plain paths are judged.
    """
    if "%" in path or "\\" in path:
        return False
    return not {".", ".."} & set(path.split("/"))


def is_get_navigable(path: str) -> bool:
    """Whether a browser can land on ``path`` with a GET request.

    POST-only endpoints (mutations, the offline-key salt) cannot be a
    navigation target: following them answers 405. The path is resolved the way
    the navigation does it, including the end slash ``APPEND_SLASH`` adds. A
    path that is not plain (see ``_is_plain_path``) is refused, since it cannot
    be judged without guessing. Unknown paths and views that declare no method
    restriction count as navigable, so only a positively identified POST-only
    target or a non-plain path is refused.
    """
    url_path = urlsplit(path).path
    if not _is_plain_path(url_path):
        return False
    candidates = [url_path]
    if settings.APPEND_SLASH and not url_path.endswith("/"):
        candidates.append(f"{url_path}/")
    for candidate in candidates:
        try:
            match = resolve(candidate)
        except Resolver404:
            continue
        methods = _declared_http_methods(match.func)
        return methods is None or "GET" in methods
    return True


def safe_page_param(request, default=1, max_page=None):
    """Liefert eine sichere Page-Nummer aus ``request.GET['page']``.

    - Nicht-Ganzzahl/leer → ``default``
    - Negativ oder 0 → 1
    - Groesser als ``max_page`` (oder ``MAX_PAGE``) → Cap auf ``max_page``
    """
    cap = max_page if max_page is not None else MAX_PAGE
    raw = request.GET.get("page", default)
    try:
        page = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(page, cap))
