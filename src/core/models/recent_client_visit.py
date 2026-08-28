"""Tracks recent client page visits per user for dashboard widget."""

from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.constants import RECENT_VISIT_TOUCH_THROTTLE_MINUTES


class RecentClientVisitManager(models.Manager):
    """Traegt die Besuchs-Drosselung aus Refs #2068 (d).

    ``visited_at`` war ``auto_now``: der Upsert in
    :func:`core.services.client.main.track_client_visit` schrieb die Zeile bei
    JEDEM Aufruf einer Personenakte neu — auch beim Neuladen im Sekundentakt,
    beim Zurueckspringen aus einem Unterformular, bei jedem HTMX-Vollrender.
    Fuer die einzige Leserin (Dashboard-Widget „Zuletzt besucht", Sortierung
    nach ``-is_favorite, -visited_at``) ist diese Aufloesung wertlos; bezahlt
    wurde sie mit einem toten Tupel plus Index-Eintraegen je Seitenaufruf.

    Die Drosselung sitzt hier und nicht am Aufrufer, weil sie eine Eigenschaft
    **dieser Tabelle** ist: sie muss fuer jeden Upsert-Weg gelten, nicht nur
    fuer den einen heutigen. ``update_or_create`` ist der einzige Schreibweg
    (``merge.py`` setzt ``visited_at`` bewusst explizit per Queryset-Update und
    laeuft nicht hier durch).

    Kern der Ersparnis: bei aktiver Drosselung wird **gar nichts** geschrieben.
    Ein Rewrite auf denselben Wert erzeugte dasselbe tote Tupel wie ein Rewrite
    auf einen neuen — ``auto_now`` durch einen throttelnden ``pre_save`` zu
    ersetzen haette also nichts gebracht.

    Eine echte Feldaenderung (heute: ``facility``, wenn dieselbe Person unter
    einem anderen Einrichtungs-Scope aufgerufen wird) geht IMMER durch — sonst
    verloere die Zeile ihren Scope. Nur der Zeitstempel wird unterdrueckt.
    """

    #: Innerhalb dieses Fensters seit dem letzten Stempel: kein Rewrite.
    touch_throttle = timedelta(minutes=RECENT_VISIT_TOUCH_THROTTLE_MINUTES)

    def update_or_create(self, defaults=None, create_defaults=None, **kwargs):
        """Refs #2068 (d); Nebenlaeufigkeit + Signatur aus dem Cross-Review.

        ``create_defaults`` MUSS in der Signatur stehen (Django 6): landete es
        in ``**kwargs``, wuerde Django es als Lookup-Feld deuten und mit
        ``FieldError`` scheitern. Es wird unveraendert durchgereicht — beim
        Anlegen stempelt der Feld-Default, die Drosselung hat nichts zu tun.

        Die Bestandspruefung liegt INNERHALB derselben Transaktion wie der
        Schreibvorgang und liest unter ``select_for_update()``. Ein vorgelagertes
        ``.first()`` ausserhalb des Locks hielt die Zusicherung „kein Rewrite
        unter fuenf Minuten" unter Parallelitaet nicht: zwei gleichzeitige
        Aufrufe nach Fensterablauf saehen beide denselben alten Stempel und
        schrieben nacheinander — der zweite Schreibvorgang laege dann innerhalb
        des Fensters. Unter dem Lock wartet der zweite Aufruf, liest den frisch
        gestempelten Wert und faellt in den Drosselungs-Zweig.

        Existiert die Zeile noch nicht, gibt es nichts zu sperren; das Rennen
        zweier Ersteinfuegungen faengt wie gehabt Djangos ``get_or_create``
        ueber ``unique_together (user, client)`` ab.
        """
        update_defaults = dict(defaults or {})
        with transaction.atomic(using=self.db):
            existing = self.select_for_update().filter(**kwargs).first()
            if existing is not None:
                now = timezone.now()
                is_fresh = existing.visited_at is not None and now - existing.visited_at < self.touch_throttle
                unchanged = all(getattr(existing, name) == value for name, value in update_defaults.items())
                if is_fresh and unchanged:
                    return existing, False
                # Ausserhalb des Fensters (oder bei echter Aenderung) faellt der
                # Stempel bewusst hier, nicht per ``auto_now``: nur so landet
                # ``visited_at`` in den ``update_fields``, die
                # ``update_or_create`` aus den ``defaults``-Schluesseln ableitet.
                update_defaults["visited_at"] = now
            return super().update_or_create(defaults=update_defaults, create_defaults=create_defaults, **kwargs)


class RecentClientVisit(models.Model):
    """Records when a user last visited a client detail page."""

    objects = RecentClientVisitManager()

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recent_client_visits",
    )
    client = models.ForeignKey(
        "core.Client",
        on_delete=models.CASCADE,
        related_name="recent_visits",
    )
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
    )
    # Refs #2068 (d): ``default`` statt ``auto_now``. Der Stempel entsteht beim
    # Anlegen und wird danach nur noch von
    # :class:`RecentClientVisitManager` gesetzt — kontrolliert und gedrosselt.
    # ``auto_now`` haette ihn zusaetzlich bei jedem unbeteiligten ``save()``
    # neu geschrieben (etwa einem ``is_favorite``-Wechsel), was fachlich falsch
    # ist: Favorisieren ist kein Besuch.
    visited_at = models.DateTimeField(default=timezone.now)
    is_favorite = models.BooleanField(default=False)

    class Meta:
        unique_together = [("user", "client")]
        ordering = ["-visited_at"]
        indexes = [
            models.Index(fields=["user", "facility", "-visited_at"]),
        ]
        verbose_name = _("Personen-Besuch")
        verbose_name_plural = _("Personen-Besuche")

    def __str__(self):
        return f"RecentClientVisit({self.user}, {self.client})"
