"""Geteilte View-Helper.

``safe_page_param`` cappt ``?page=`` auf ``MAX_PAGE``, validiert den
Wert als positive Ganzzahl und faengt fehlerhafte Eingaben (negativ,
``abc``) ab. Vermeidet OFFSET-Seq-Scans bei boesgemeinten oder
fehlerhaften URLs (Audit-Massnahme #32, Refs #733).
"""

from core.constants import MAX_PAGE


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
