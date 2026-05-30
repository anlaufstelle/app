"""Pseudonymisierung von PII fuer den AuditLog (Refs #791).

AuditLog ist append-only und bis zu 24 Monate aufbewahrt. Klartext-PII
(z.B. eingegebene E-Mail-Adressen bei Passwort-Reset-Anfragen) leben
darin laenger, als DSGVO-Datenminimierung zulaesst — und falsch
eingetippte oder fremde E-Mails sind besonders schwer wieder
herauszuholen.

``hmac_hash_email`` liefert einen stabilen, nicht-reversiblen Hash, der
fuer Forensik (gleiche E-Mail -> gleicher Hash) ausreicht und gleichzeitig
den Klartext aus dem Audit-Trail haelt.
"""

from __future__ import annotations

import hashlib
import hmac

from django.conf import settings


def _get_audit_hash_key() -> bytes:
    """Liefert den HMAC-Schluessel als Bytes.

    Bevorzugt ``settings.AUDIT_HASH_KEY`` (separat von ``SECRET_KEY``,
    damit ein potenzieller SECRET_KEY-Leak nicht ruckwirkend Audit-Hashes
    knacken laesst). Fallback: SHA256(SECRET_KEY) — funktional ausreichend
    fuer Test- und Dev-Setups, in Produktion sollte
    ``DJANGO_AUDIT_HASH_KEY`` per Env explizit gesetzt sein.
    """
    raw = getattr(settings, "AUDIT_HASH_KEY", "") or ""
    if raw:
        return raw.encode("utf-8")
    secret = getattr(settings, "SECRET_KEY", "") or ""
    return hashlib.sha256(secret.encode("utf-8")).digest()


def hmac_hash_email(email: str) -> str:
    """Stabiler HMAC-SHA256 ueber die normalisierte E-Mail-Adresse.

    Normalisierung: ``str(email).strip().lower()``. Damit liefern
    ``"Alice@Example.org"`` und ``"alice@example.org "`` denselben Hash —
    der Ops-Forensikflow (Lookup einer bekannten E-Mail) bleibt einsatzfaehig.

    Returns: Hex-Digest (64 Zeichen). Leere Eingaben liefern ``""``.
    """
    if not email:
        return ""
    normalized = str(email).strip().lower()
    return hmac.new(_get_audit_hash_key(), normalized.encode("utf-8"), hashlib.sha256).hexdigest()
