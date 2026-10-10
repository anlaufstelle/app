"""Owner-bound sample attachments for every facility (live AuthZ audit only).

The seed attaches its person and case sample files through the ``anhang`` file
template, which only the ``default`` package ships. A second facility on another
package therefore owns no downloadable person/case attachment, and the
cross-facility probes for ``client_attachment_download`` /
``case_attachment_download`` had no object to aim at.

The audit fixture runs :func:`ensure_owner_attachments` in a subprocess against
the E2E database (``manage.py shell``), because the encrypted files must be
written through the real vault code and the pytest process does not talk to
that database. It is idempotent: facilities that already own both attachments
are left alone, and nothing outside the audit run calls it.
"""

from django.db import connection

from core.models import Facility, FieldTemplate, User
from core.seed.attachments import OWNER_FILE_TEMPLATE_SLUG, attach_files_to_client_and_case


def ensure_owner_attachments() -> int:
    """Create the missing person/case sample attachments; return how many."""
    with connection.cursor() as cursor:
        # RLS: the ORM below runs without a request, so open the session wide.
        cursor.execute("SELECT set_config('app.is_super_admin', 'true', false)")

    created = 0
    for facility in Facility.objects.order_by("name"):
        FieldTemplate.objects.get_or_create(
            facility=facility,
            slug=OWNER_FILE_TEMPLATE_SLUG,
            defaults={"name": "Anhang", "field_type": FieldTemplate.FieldType.FILE},
        )
        users = list(User.objects.filter(facility=facility).order_by("username"))
        created += attach_files_to_client_and_case(facility, users)
    return created
