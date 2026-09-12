"""Pin-Tests der Welle K (0.98, Refs #2193) — Prio-2-Testlücken in
``core.services.client.restriction``, Batch ``case-client``.

Quelle: ``welle-k-0.98/reports/c-scratch/batch-case-client.json``
(``triage-097-client.json``). Fünf der sechs Batch-Mutanten sind NEU
(``x__restricted_flag__mutmut_1`` ist bereits in
``test_mutation_pins_097_restriction.py::test_anzeige_praedikat_liest_ohne_zeilensperre``
gepinnt — siehe Bericht, nicht hier erneut aufgenommen).

* **``restrict_processing``/``lift_processing_restriction`` schreiben nur die
  Sperrspalten** (``…_restrict_processing__mutmut_22``,
  ``…_lift_processing_restriction__mutmut_38``): beide ersetzen
  ``client.save(update_fields=[*_RESTRICTION_FIELDS, "updated_at"])`` durch
  ``client.save(update_fields=None)``. ``Client.save()`` ruft
  ``_encrypt_sensitive_fields()`` IMMER auf; erst ein voller Save (kein
  ``update_fields``) persistiert dessen ``data_json``-Mutation. Ein per
  Queryset-Update (am Model-``save()`` vorbei) gesetzter Klartext in einem
  ``is_encrypted``-Slug würde beim bloßen Setzen/Aufheben des Art.-18-Vermerks
  also umgeschrieben — genau die Nutzungsänderung, die der Vermerk verbietet.
* **``lift_processing_restriction`` auditiert den aufgehobenen Rechtsgrund**
  (``…mutmut_31``, ``…mutmut_44``, ``…mutmut_48``): ``previous_reason`` fällt
  auf ``None`` bzw. das ``reason``-Kwarg entfällt — in beiden Fällen fehlt der
  Rechtsgrund (lit. a–d) im ``PROCESSING_RESTRICTION_LIFTED``-Audit.

``_encryption_key`` liegt modulweit an (Fernet-Verschlüsselung im
``data_json``-Pfad, Mutantenbaum ohne ``.env``).
"""

from __future__ import annotations

import pytest

from core.models import AuditLog, Client
from core.services.client.restriction import lift_processing_restriction, restrict_processing

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_restrict_und_lift_beruehren_data_json_nicht(client_identified, doc_type_client_profile, staff_user, lead_user):
    """Pins ``x_restrict_processing__mutmut_22`` UND
    ``x_lift_processing_restriction__mutmut_38``: ein per Queryset-Update
    (am ``save()`` vorbei) gesetzter Klartext in einem ``is_encrypted``-Slug
    (``diagnose`` aus ``doc_type_client_profile``) muss sowohl das Setzen ALS
    AUCH das Aufheben des Sperrvermerks unverändert überstehen — die beiden
    Funktionen dürfen nur die vier Sperrspalten + ``updated_at`` schreiben."""
    marker = {"diagnose": "Klartext-Diagnose (Pin098)"}
    Client.objects.filter(pk=client_identified.pk).update(document_type=doc_type_client_profile, data_json=marker)

    restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)
    assert Client.objects.get(pk=client_identified.pk).data_json == marker

    lift_processing_restriction(client_identified, lead_user)
    assert Client.objects.get(pk=client_identified.pk).data_json == marker


def test_lift_audit_traegt_den_aufgehobenen_rechtsgrund(client_identified, facility, staff_user, lead_user):
    """Pins ``x_lift_processing_restriction__mutmut_31`` (Zuweisung auf
    ``None``), ``…mutmut_44`` (``reason=None`` im Aufruf) und ``…mutmut_48``
    (``reason``-Kwarg entfällt): in allen drei Fällen fehlt der Rechtsgrund
    (lit. a–d) im Aufhebungs-Audit — nicht mehr belegt, WELCHE Einschränkung
    aufgehoben wurde."""
    restrict_processing(client_identified, staff_user, reason=Client.RestrictionReason.ACCURACY_DISPUTED)

    lift_processing_restriction(client_identified, lead_user)

    entry = AuditLog.objects.get(
        action=AuditLog.Action.PROCESSING_RESTRICTION_LIFTED,
        target_id=str(client_identified.pk),
    )
    assert entry.detail == {"reason": Client.RestrictionReason.ACCURACY_DISPUTED}
