"""Gemeinsame Helfer des Vier-Augen-Löschworkflows (Refs #1053, #1736).

Der Genehmiger-Pool wird über das Recht ``can_confirm_deletion`` kuratiert und
nicht aus der Rolle abgeleitet. Er wird an zwei Stellen gebraucht — im
Setup-Guard der Löschanträge-Liste und auf der Antragsseite, die vor dem
Absenden zeigt, wer den Antrag überhaupt bestätigen könnte. Damit beide
Flächen dieselbe Menge meinen, liegt die Abfrage hier statt zweimal in den
Views.
"""

from core.models import User


def eligible_deletion_confirmers(facility, *, exclude_user=None):
    """Aktive Träger:innen des Rechts „Löschbestätigung" einer Einrichtung.

    ``exclude_user`` blendet die antragstellende Person aus — sie darf ihren
    eigenen Antrag nicht genehmigen (Vier-Augen-Prinzip, hart durchgesetzt in
    ``approve_client_deletion``/``approve_deletion`` und per DB-Constraint).
    Ist die zurückgegebene Menge leer, gibt es für einen Antrag dieser Person
    keinen möglichen Genehmiger: der Antrag würde still liegenbleiben.
    """
    qs = User.objects.filter(facility=facility, is_active=True, can_confirm_deletion=True)
    if exclude_user is not None and exclude_user.pk is not None:
        qs = qs.exclude(pk=exclude_user.pk)
    return qs.order_by("display_name", "username")
