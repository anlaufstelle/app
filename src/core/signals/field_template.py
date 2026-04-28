"""Schutz vor FieldTemplate-Löschung, wenn Events Daten unter diesem Slug enthalten.

Hintergrund (Issue #356): `DocumentTypeField.field_template` ist `CASCADE`, d.h. beim
Löschen eines `FieldTemplate` verschwindet die Feldzuordnung zum Dokumenttyp. Die
tatsächlichen Werte bleiben aber in `Event.data_json` unter dem Slug erhalten — und
werden beim nächsten Edit stillschweigend verworfen. Um diesen Datenverlust zu
verhindern, blockieren wir das Hard-Delete, sobald noch Events mit Daten zu diesem
Slug existieren. Als Alternative bleibt `is_active = False` (Soft-Delete).
"""

from django.db.models import ProtectedError
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from core.models import Event, FieldTemplate


@receiver(pre_delete, sender=FieldTemplate)
def protect_fieldtemplate_with_data(sender, instance, origin=None, **kwargs):
    """Verhindere das Löschen einer FieldTemplate, wenn noch Events Daten enthalten.

    Nur echte, direkt initiierte Löschungen einer FieldTemplate werden geprüft:

    * ``instance.delete()`` → ``origin is instance``
    * ``FieldTemplate.objects.filter(...).delete()`` → ``origin`` ist ein QuerySet
      mit ``model is FieldTemplate``

    Kaskaden-Löschungen (z.B. via ``Facility`` → ``FieldTemplate`` und
    ``Facility`` → ``Event``) lassen wir ungeprüft passieren, da in diesem Fall
    auch die referenzierenden Events mitgelöscht werden und kein Datenverlust
    entsteht.
    """
    if not _is_direct_fieldtemplate_delete(instance, origin):
        return

    if not instance.slug:
        return

    has_data = Event.objects.filter(
        facility_id=instance.facility_id,
        data_json__has_key=instance.slug,
    ).exists()
    if has_data:
        raise ProtectedError(
            _(
                "Feldvorlage „%(name)s“ kann nicht gelöscht werden — es existieren "
                "Events mit Daten unter dem Slug „%(slug)s“. "
                "Alternativ: Feldvorlage deaktivieren (is_active=False)."
            )
            % {"name": instance.name, "slug": instance.slug},
            [instance],
        )


def _is_direct_fieldtemplate_delete(instance, origin):
    """True, wenn die Löschung direkt auf der FieldTemplate-Ebene ausgelöst wurde."""
    if origin is None:
        # Keine Origin-Info → konservativ: prüfen (z.B. bei manuellen Signalaufrufen).
        return True
    if origin is instance:
        return True
    origin_model = getattr(origin, "model", None)
    return origin_model is FieldTemplate
