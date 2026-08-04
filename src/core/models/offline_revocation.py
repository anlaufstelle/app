"""Server-gefuehrte Revoke-Liste fuer ausgelieferte Offline-Bundles (Refs #1760).

Ein bereits auf ein Geraet uebertragenes Offline-Bundle
(:mod:`core.services.system.offline`) traegt bis zu ``BUNDLE_TTL_SECONDS``
(48 h) Klartext im IndexedDB des Browsers — Pseudonym, Notizen, Fall-Titel,
Ereignisdaten. Wird die Person serverseitig anonymisiert, invalidierte bis
#1760 **kein** Signal diesen Bestand:

* die bestehende Revalidierung (``revalidateCachedClient`` in
  ``offline-store.js``) purgt nur bei 401/404/410 — eine anonymisierte Person
  existiert aber weiter und liefert **200** mit anonymisiertem Bundle, und
* dieses 200 ueberschreibt den lokalen Klartext NICHT, solange zu der Person
  ungesyncte Aenderungen offen sind (``"skipped"``-Zweig).

Diese Tabelle ist die **positive** Gegenrichtung: sie nennt personen-genau,
was das Geraet loeschen muss, unabhaengig davon, was der Bundle-Endpoint zu
dieser Person antwortet.

**Kein Klartext.** Der Eintrag traegt ausschliesslich Facility-Scope, die
(ehemalige) Client-UUID und den Zeitstempel. Ein Pseudonym oder eine
Begruendung waere neue PII, die die Anonymisierung ueberlebt — genau das, was
#1760 verhindern soll. ``client_pk`` ist bewusst **kein** ``ForeignKey``: der
Eintrag muss die Zeile ueberdauern koennen, auf die er zeigt, und darf keine
Kaskade erben.

Abgrenzung zu :func:`core.signals.offline_invalidation.revoke_offline_access`:
jener Kanal entwertet den Offline-Zugang **einer Nutzer:in** komplett (Salt-
Rotation + Session-Flush, bei Rollenwechsel/Deaktivierung/Lockout). Hier geht es
um die Gegenachse — **eine Person**, aber auf allen Geraeten aller Nutzer:innen
der Einrichtung. Eine Salt-Rotation waere dafuer das falsche Werkzeug: sie
entwertete den gesamten Offline-Bestand jeder Kraft der Einrichtung.
"""

from uuid import uuid4

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class OfflineClientRevocation(models.Model):
    """Ein Widerruf: „diese Person darf offline nicht fortbestehen"."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="offline_client_revocations",
        verbose_name=_("Einrichtung"),
    )
    # Kein FK (siehe Modul-Docstring): die (ehemalige) Personen-UUID als opaker
    # Bezeichner. Der Client gleicht sie gegen seinen IndexedDB-Bestand ab.
    client_pk = models.UUIDField(verbose_name=_("Personen-ID"))
    # ``default`` statt ``auto_now_add``: der Upsert in
    # ``revoke_offline_client_bundles`` frischt den Zeitstempel bewusst auf, was
    # ``auto_now_add`` nicht zulaesst (nur beim INSERT).
    revoked_at = models.DateTimeField(default=timezone.now, verbose_name=_("Widerrufen am"))

    objects = FacilityScopedManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "client_pk"],
                name="unique_facility_offline_client_revocation",
            ),
        ]
        indexes = [
            # Traegt sowohl die Abhol-Abfrage (Facility + Fenster) als auch den
            # Prune (Facility + Cutoff).
            models.Index(fields=["facility", "revoked_at"]),
        ]
        ordering = ["-revoked_at"]
        verbose_name = _("Offline-Widerruf")
        verbose_name_plural = _("Offline-Widerrufe")

    def __str__(self):
        return f"OfflineClientRevocation({self.client_pk})"
