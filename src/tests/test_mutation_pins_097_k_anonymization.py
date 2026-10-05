"""Pin-Test zum Meilenstein release-0.97 — Prio-1-Survivor in ``core.services.compliance.k_anonymization``.

Quelle: atomic-Messlauf der Welle J (#2149),
Batch ``client``. Gepinnt wird ``x_k_anonymize_client__mutmut_12``: die
``update_fields``-Liste des Saves faellt auf ``None``, aus dem gezielten Write
der sechs Generalisierungs-Spalten wird also ein VOLLER Save.

Anders als ``restrict_processing`` liest diese Primitive die Zeile **nicht**
unter Sperre neu ein — geschrieben wird die Instanz, die der Retention-Lauf
uebergibt (``core/retention/anonymization.py``). Ein voller Save kippt damit
jede Spalte, die seit dem Laden fremd geaendert wurde, auf den stalen Stand
zurueck: Papierkorb-Zustand, die Quasi-Identifikatoren, auf denen die
k-Anonymitaet ueberhaupt ruht — und einen zwischenzeitlich gesetzten
Art.-18-Sperrvermerk, der danach still aufgehoben waere.
"""

import pytest

from core.models import Client
from core.services.compliance.k_anonymization import k_anonymize_client

pytestmark = pytest.mark.django_db


def test_k_anonymisierung_schreibt_nur_die_generalisierten_spalten(client_identified):
    """Pins ``x_k_anonymize_client__mutmut_12``.

    Der fremde Schreibvorgang laeuft bewusst per Queryset-``update()``: er
    aendert die DB-Zeile, ohne die uebergebene Instanz zu beruehren — genau die
    Lage, in der ein voller Save den fremden Stand ueberschreibt. Geprueft wird
    nach dem Rennen an der DB, nicht an der Instanz.
    """
    stale = Client.objects.get(pk=client_identified.pk)
    Client.objects.filter(pk=stale.pk).update(
        processing_restricted=True,
        processing_restriction_reason=Client.RestrictionReason.ACCURACY_DISPUTED,
        is_deleted=True,
    )

    k_anonymize_client(stale)

    frisch = Client.objects.get(pk=stale.pk)
    # Fremd geschriebene Spalten ueberleben den gezielten Save.
    assert frisch.processing_restricted is True
    assert frisch.processing_restriction_reason == Client.RestrictionReason.ACCURACY_DISPUTED
    assert frisch.is_deleted is True
    # Die sechs Generalisierungs-Spalten sind trotzdem geschrieben.
    assert frisch.pseudonym.startswith("anon-")
    assert frisch.notes == ""
    assert frisch.data_json == {}
    assert frisch.document_type_id is None
    assert frisch.is_active is False
    assert frisch.k_anonymized is True
