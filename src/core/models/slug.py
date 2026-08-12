"""Slug-Helfer der Dokumentationstyp-Familie (Refs #1834).

Eigenes Modul, weil ``DocumentType``, ``FieldTemplate``, ``FieldGroup``
und das Paketformat (``core.packages.schema``) sich dieselbe
Normalisierung, dasselbe Slug-Format und dieselbe Reserved-Liste teilen.
Vor dem Split von ``document_type.py`` (ein Model pro Datei) haetten sie
sonst willkuerlich bei einem der vier Models gewohnt — und ``mixins.py``
haette sie weiter nur per Laufzeit-Import erreichen koennen.
"""

import re

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

_UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def slug_base(name: str, max_length: int = 120) -> str:
    """Normalisierter Slug-Stamm aus ``name`` — leer, wenn keiner entsteht.

    ``slugify`` allein genügt nicht (Final-Review D, Refs #1058): es lässt
    Unterstriche durch (``\\w``) und die Truncation auf ``max_length`` kann auf
    einem Bindestrich enden. Beides verletzt :data:`_SLUG_RE` und wäre damit im
    Paketformat nicht referenzierbar. Deshalb: ``_`` → ``-``, Mehrfach-
    Bindestriche kollabieren, Truncation, danach Rand-Bindestriche strippen.

    Leerer Rückgabewert = der Name ergibt keinen Slug (reine Satzzeichen,
    nicht-lateinische Schrift); die Aufrufer entscheiden über Fehler oder
    Fallback.
    """
    base = slugify(name.lower().translate(_UMLAUT_MAP)).replace("_", "-")
    base = re.sub(r"-{2,}", "-", base).strip("-")
    return base[:max_length].strip("-")


# Feste Formularfeld-Namen, unter denen dynamische Felder eines Dokumentationstyps
# NACKT im selben ``<form>`` gerendert werden (M2, Refs #1726). Ein
# FieldTemplate-Slug, der einen dieser Namen trifft, überschriebe beim Absenden
# das gleichnamige feste Feld (POST-Kollision/Datenverlust) — die Reserved-Liste
# blockt das an ALLEN Schichten: Config-Form (``validate_slugifiable_name``),
# Modell (``FieldTemplate.clean``/``save``) und Paket-Schema
# (``_parse_field_template``). Bewusst hier neben ``slug_base``, damit alle
# Schichten dieselbe Liste importieren (DRY).
#
# Unterstrich-Namen (``contact_stage``, ``document_type``, …) sind über
# ``slug_base``/``_SLUG_RE`` heute als NEUE Slugs unerreichbar (``_`` → ``-``):
# sie stehen hier TROTZDEM defensiv drin — für Bestandsdaten (direkt gesetzte
# Slugs) und Zukunftssicherheit (falls sich das Slug-Format ändert). Gegen eine
# ``slug_base``-Ausgabe matchen sie schlicht nie, schaden aber auch nicht; der
# Modell-Backstop und die Migrations-Inventur (Refs #1730) vergleichen dagegen
# den ROHEN Slug und profitieren von den Unterstrich-Einträgen.
_RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        # ClientForm (core/forms/clients.py)
        "pseudonym",
        "contact_stage",
        "age_cluster",
        "notes",
        "document_type",
        # CaseForm (core/forms/cases.py) — ``document_type`` bereits oben
        "client",
        "title",
        "description",
        "lead_user",
        # EventMetaForm (core/forms/events.py) — ``document_type``/``client`` oben
        "occurred_at",
        "case",
        # Generische POST-Schlüssel in denselben ``<form>``s
        "csrfmiddlewaretoken",
        "expected_updated_at",
        "_save_and_new",
    }
)


def generate_unique_slug(name: str, siblings_qs: models.QuerySet, max_length: int = 120) -> str:
    """Generiert einen Slug aus ``name``, eindeutig innerhalb ``siblings_qs``.

    Modulweiter Helfer (M2, Refs #1058) — extrahiert aus den vormals fast
    identischen Kopien ``FieldTemplate._generate_unique_slug``/
    ``FieldGroup._generate_unique_slug``. ``siblings_qs`` muss bereits auf den
    Kollisions-Scope gefiltert sein (z. B. ``facility=…`` bzw.
    ``document_type=…``) und das eigene Objekt per ``.exclude(pk=…)``
    ausschließen — dieser Helfer filtert hier nur noch auf ``slug=``.
    Truncation auf ``max_length`` VOR dem Counter-Suffix, damit der
    Suffix das Limit des Ziel-``SlugField`` nie sprengt. Normalisierung des
    Stamms: :func:`slug_base` (Refs #1058) — auch die gekürzte Variante vor dem
    Suffix wird von Rand-Bindestrichen befreit, sonst entstünde ``…--2``.
    """
    base = slug_base(name, max_length)
    if not base:
        raise ValidationError(_("Name ergibt keinen gültigen Slug."))
    slug = base
    counter = 2
    while siblings_qs.filter(slug=slug).exists():
        suffix = f"-{counter}"
        slug = f"{base[: max_length - len(suffix)].strip('-')}{suffix}"
        counter += 1
    return slug
