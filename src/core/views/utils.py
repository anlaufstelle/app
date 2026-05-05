"""Geteilte View-Helper.

``safe_page_param`` cappt ``?page=`` auf ``MAX_PAGE``, validiert den
Wert als positive Ganzzahl und faengt fehlerhafte Eingaben (negativ,
``abc``) ab. Vermeidet OFFSET-Seq-Scans bei boesgemeinten oder
fehlerhaften URLs (Audit-Massnahme #32, Refs #733).

``safe_redirect_path`` engt ``?next=``-Eingaben auf same-origin Pfade ein.
``startswith("/")`` allein matcht ``//evil.example/login``, das der
Browser als protokoll-relativ interpretiert (Refs #770).
"""

from core.constants import MAX_PAGE


def safe_redirect_path(raw: str | None) -> str:
    """Open-Redirect-Schutz: nur same-origin Pfade akzeptieren.

    Liefert ``raw`` zurueck, wenn es mit genau einem ``/`` beginnt;
    sonst ``"/"``. Faengt damit ``//evil``, ``http://...``, ``javascript:``,
    leere Strings und ``None`` ab. Vorbild war ``views/sudo_mode._safe_next``.
    """
    if not raw:
        return "/"
    if raw.startswith("/") and not raw.startswith("//"):
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
