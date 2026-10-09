"""Bound rate-limit audit volume without retaining request payloads."""

import hashlib
import logging

from django.core.cache import cache
from django.urls import Resolver404, resolve

from core.logging import mask_url
from core.models import AuditLog
from core.services.audit import helpers

logger = logging.getLogger(__name__)
AUDIT_INTERVAL_SECONDS = 60


def audit_rate_limit(request, *, reason="request_rate"):
    """Write once per route class and known account/facility per minute.

    Anonymous requests share a bucket across IPs to prevent rotating sources
    from flooding the system chain. Unknown URLs use one constant class.
    Refs #2527.
    """
    try:
        resolve(request.path_info)
    except Resolver404:
        path_class = "unresolved"
    else:
        path_class = mask_url(request.path_info)
    request_user = getattr(request, "user", None)
    user = (
        request_user
        if getattr(request_user, "is_authenticated", False)
        else getattr(request, "failed_login_user", None)
    )
    if getattr(request, "rate_limit_reason", None) == "super_admin_backoff":
        reason = "super_admin_backoff"
    facility = getattr(request, "current_facility", None) or getattr(user, "facility", None)
    subject = f"{path_class}:{getattr(user, 'pk', '')}:{getattr(facility, 'pk', '')}"
    key = "rate-limit-audit:" + hashlib.sha256(subject.encode()).hexdigest()
    if not cache.add(key, True, timeout=AUDIT_INTERVAL_SECONDS):
        return
    try:
        helpers.log_audit_event(
            request,
            AuditLog.Action.RATE_LIMITED,
            user=user,
            facility=facility,
            detail={"path_class": path_class, "reason": reason},
        )
    except Exception:
        # Preserve the rejection if the audit sink fails; never log its exception
        # text, which may carry query parameters or database values.
        logger.warning(
            "Rate-limit audit unavailable path_class=%s reason=%s",
            path_class,
            reason,
            extra={"user_pk": getattr(user, "pk", None), "facility_id": getattr(facility, "pk", None)},
        )
