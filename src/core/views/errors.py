"""Custom Error-Views (Refs #358, #699).

Hier liegen Views, die Djangos Built-in-Error-Pages ersetzen — aktuell
nur die CSRF-Failure-View. 400/403/404/500 nutzen Djangos
Auto-Resolution via Templates ohne View-Override.
"""

from django.shortcuts import render


def csrf_failure(request, reason=""):
    """CSRF-Failure-View — liefert ``403_csrf.html`` mit Status 403.

    Refs #699: Djangos Built-in-Page passt nicht zum Design-System und
    ist auf Englisch. Unsere Version nutzt ``base.html``-Inheritance
    (Tailwind, DE) und bietet Reload + Startseiten-Link.

    ``reason`` (Django-Kontext) loggen wir nicht — die Information ist
    fuer User irrelevant, und CSRF-Failures werden ohnehin auf
    Webserver-Ebene mitgezaehlt.
    """
    return render(request, "403_csrf.html", status=403)
