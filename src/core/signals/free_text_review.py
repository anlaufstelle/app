"""Erase polymorphic review markers when their source becomes unavailable."""

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from core.models import Client, Event
from core.services.free_text_review import remove_target_reviews


@receiver(pre_delete, sender=Client)
@receiver(pre_delete, sender=Event)
def remove_deleted_reviews(sender, instance, **kwargs):
    remove_target_reviews(instance)


@receiver(post_save, sender=Client)
@receiver(post_save, sender=Event)
def remove_unavailable_reviews(sender, instance, raw=False, **kwargs):
    if not raw and (
        instance.is_deleted
        or (isinstance(instance, Client) and not instance.is_active)
        or getattr(instance, "anonymized_at", None) is not None
    ):
        remove_target_reviews(instance)
