"""Content-bound review metadata; free text is never copied here."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class FreeTextReview(models.Model):
    """One reversible review per facility, target and field."""

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey("core.Facility", on_delete=models.CASCADE)
    target_type = models.CharField(max_length=20)
    target_id = models.UUIDField()
    field_key = models.CharField(max_length=200)
    reviewed_until = models.DateField(verbose_name=_("Geprüft bis"))
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    reviewed_at = models.DateTimeField(auto_now=True)
    content_fingerprint = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "target_type", "target_id", "field_key"],
                name="unique_free_text_review_field",
            ),
        ]

    def __str__(self):
        return f"{self.target_type}:{self.target_id}:{self.field_key}"
