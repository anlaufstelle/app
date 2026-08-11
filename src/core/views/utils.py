"""Geteilte View-Helper.

``safe_page_param`` cappt ``?page=`` auf ``MAX_PAGE``, validiert den
Wert als positive Ganzzahl und faengt fehlerhafte Eingaben (negativ,
``abc``) ab. Vermeidet OFFSET-Seq-Scans bei boesgemeinten oder
fehlerhaften URLs (Refs #733).

``safe_redirect_path`` engt ``?next=``-Eingaben auf same-origin Pfade ein.
``startswith("/")`` allein matcht ``//evil.example/login``, das der
Browser als protokoll-relativ interpretiert (Refs #770).
"""

from django.urls import reverse
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
