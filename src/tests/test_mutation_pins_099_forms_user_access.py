"""Mutation-Pins Welle L (0.99) — ``core.forms.user_access`` (Vollmutation #2174).

Batch ``forms-1`` (Refs #2174, Refs #2193). 27 Survivors in
``_UserAccessForm.__init__`` (10), ``_UserAccessForm.clean_role`` (11) und
``UserAccessUpdateForm.clean_is_active`` (6) — alle drei Methoden sind die
Berechtigungs-Waechter des Zugaenge-Reiters (#1871): kein Konto darf sich
selbst herabstufen, selbst deaktivieren oder ueber dieses Formular
Systemadministration erlangen.

Triage-Ergebnis: 15 echte Testluecken (``test``, alle Prio 1 — die
Guard-Bedingungen selbst sind laengst getestet, aber KEIN bestehender Test
prueft den exakten Fehlertext oder die Erreichbarkeit ueber ``clean_role``
direkt), 4 aequivalent, 8 skip (Hilfetext-Strings in ``__init__``, siehe
Bericht Abschnitt 3).

**SUPER_ADMIN-Waechter ist Defense-in-Depth, nicht ueber ``is_valid()``
erreichbar:** ``role_field.choices`` schliesst ``SUPER_ADMIN`` in
``__init__`` bereits aus — jeder Versuch, ``role=super_admin`` per POST zu
senden, scheitert an ``ChoiceField.validate()`` ("... ist keine gültige
Auswahl"), *bevor* ``clean_role()`` ueberhaupt laeuft (empirisch verifiziert:
``form.errors == {"role": ["Bitte eine gültige Auswahl treffen. ..."]}``,
``clean_role()`` nie aufgerufen). ``clean_role``s eigener
``if role == SUPER_ADMIN``-Zweig ist damit eine zweite, unabhaengige
Verteidigungslinie fuer den Fall, dass die Feld-Restriktion einmal entfaellt
— und war bislang GAR NICHT getestet (weder die Bedingung noch die
Nachricht). ``test_super_admin_guard_is_enforced_even_when_reached_directly``
ruft ``clean_role()`` deshalb direkt auf (``cleaned_data`` manuell gesetzt,
wie ein Aufrufer, der die Feld-Restriktion umgeht) und killt ``_5``
(``ValidationError(None)`` — Text wird zu ``"None"``), ``_6``
(``ValidationError(_(None))`` — ``gettext_lazy(None)`` wirft beim Erzwingen
zu ``str()`` ``AttributeError: 'NoneType' object has no attribute
'replace'`` in ``django.utils.translation.trans_real.gettext``, empirisch
verifiziert) sowie ``_7``/``_8``/``_9`` (Text-Grossschreibung).

**Selbst-Herabstufung und Selbst-Deaktivierung sind ueber ``is_valid()``
erreichbar** (das bestaetigt der bestehende Test
``test_admin_cannot_deactivate_or_demote_own_account`` in
``test_user_access_hub.py``, der aber nur ``"role" in demote.errors`` bzw.
``"is_active" in deactivate.errors`` prueft, nie den Wortlaut) — die beiden
Pins hier ergaenzen die exakte Text-Assertion und killen damit zusaetzlich
``_(None)``-Variante und Gross-/Kleinschreibung:

* ``test_self_demotion_is_rejected_with_the_expected_message`` killt
  ``clean_role``-Survivors ``_20`` (``ValidationError(None)``), ``_21``
  (``ValidationError(_(None))``, AttributeError beim Erzwingen) und
  ``_22``/``_23``/``_24`` (Text-Grossschreibung).
* ``test_self_deactivation_is_rejected_with_the_expected_message`` killt
  ``clean_is_active``-Survivors ``_14`` (``ValidationError(None)``), ``_15``
  (``ValidationError(_(None))``) und ``_16``/``_17``/``_18``
  (Text-Grossschreibung).

Aequivalente Mutanten (nachweislich verhaltensgleich, bewusst NICHT
gepinnt):

* ``__init____mutmut_1`` (``self.facility = None``) — ``self.facility``
  wird in der gesamten Klasse und ihren Unterklassen NIE gelesen (kein
  ``PinnedClientMixin`` wie bei anderen Formularen, kein Template-, Service-
  oder Test-Zugriff auf ``form.facility`` — repo-weit per ``grep`` gegen
  ``src/`` verifiziert). Reine tote Zuweisung.
* ``__init____mutmut_6`` (``cast(None, self.fields["role"])`` statt
  ``cast(forms.ChoiceField, ...)``) — ``typing.cast`` ist zur Laufzeit ein
  No-op und gibt unveraendert sein zweites Argument zurueck; das erste
  Argument hat keinerlei Laufzeitwirkung (nur fuer mypy).
* ``clean_role__mutmut_16`` und ``clean_is_active__mutmut_10`` (jeweils
  ``getattr(self.actor, "pk", )`` statt ``getattr(self.actor, "pk", None)``
  — der Default-Wert des dritten Arguments faellt weg): ``actor`` ist in
  ``__init__`` ein PFLICHT-Keyword-Argument ohne eigenen Default und wird an
  jeder Aufrufstelle (``core/views/user_access.py``, alle Form-Tests in
  ``test_user_access_hub.py``) mit einer echten ``User``-Instanz belegt.
  ``.pk`` ist auf jedem Django-``Model`` (und auf ``AnonymousUser``) immer
  ein vorhandenes Attribut — der Default wird nie erreicht, ``getattr(x,
  "pk")`` und ``getattr(x, "pk", None)`` liefern fuer jede in diesem Formular
  jemals uebergebene ``actor``-Instanz dasselbe Ergebnis.

``_encryption_key`` liegt modulweit an (Lehre 112).
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from core.forms.user_access import UserAccessCreateForm, UserAccessUpdateForm
from core.models import User

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _create_data(username, **overrides):
    values = {
        "username": username,
        "email": f"{username}@example.org",
        "display_name": "",
        "role": User.Role.STAFF,
        "mfa_required": "",
        "can_confirm_deletion": "",
    }
    values.update(overrides)
    return values


def _update_data(user, **overrides):
    values = {
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "mfa_required": user.mfa_required,
        "can_confirm_deletion": user.can_confirm_deletion,
    }
    values.update(overrides)
    return values


class TestUserAccessFormRoleGuards:
    def test_super_admin_guard_is_enforced_even_when_reached_directly(self, facility, admin_user):
        """Pins ``xǁ_UserAccessFormǁclean_role__mutmut_5``/``_6``/``_7``/``_8``/
        ``_9``. ``role_field.choices`` schliesst ``SUPER_ADMIN`` bereits aus,
        ``clean_role()`` ist ueber ``is_valid()`` fuer diesen Fall also
        strukturell unerreichbar (siehe Moduldoc) — der Pin ruft die
        Defense-in-Depth-Methode direkt auf, wie es ein Aufrufer taete, der
        die Feld-Restriktion (versehentlich oder absichtlich) umgeht."""
        form = UserAccessCreateForm(_create_data("probe_super_admin"), facility=facility, actor=admin_user)
        assert form.is_valid(), form.errors

        form.cleaned_data["role"] = User.Role.SUPER_ADMIN
        with pytest.raises(ValidationError) as exc_info:
            form.clean_role()

        assert exc_info.value.messages == ["Die Rolle Systemadministration kann hier nicht vergeben werden."]

    def test_self_demotion_is_rejected_with_the_expected_message(self, facility, admin_user):
        """Pins ``xǁ_UserAccessFormǁclean_role__mutmut_20`` (Text wird
        ``"None"``), ``_21`` (``_(None)`` wirft ``AttributeError`` beim
        Erzwingen zu ``str()`` in ``form.errors``) und ``_22``/``_23``/``_24``
        (Text-Grossschreibung). Ergaenzt
        ``test_admin_cannot_deactivate_or_demote_own_account`` in
        ``test_user_access_hub.py``, die nur ``"role" in demote.errors``
        prueft, nie den Wortlaut."""
        demote = UserAccessUpdateForm(
            _update_data(admin_user, role=User.Role.STAFF),
            instance=admin_user,
            facility=facility,
            actor=admin_user,
        )

        assert not demote.is_valid()
        assert demote.errors["role"] == ["Die eigene Rolle kann hier nicht herabgestuft werden."]


class TestUserAccessUpdateFormIsActiveGuard:
    def test_self_deactivation_is_rejected_with_the_expected_message(self, facility, admin_user):
        """Pins ``xǁUserAccessUpdateFormǁclean_is_active__mutmut_14`` (Text
        wird ``"None"``), ``_15`` (``_(None)`` wirft ``AttributeError`` beim
        Erzwingen zu ``str()`` in ``form.errors``) und ``_16``/``_17``/``_18``
        (Text-Grossschreibung). Ergaenzt
        ``test_admin_cannot_deactivate_or_demote_own_account``, die nur
        ``"is_active" in deactivate.errors`` prueft, nie den Wortlaut."""
        deactivate = UserAccessUpdateForm(
            _update_data(admin_user, is_active=""),
            instance=admin_user,
            facility=facility,
            actor=admin_user,
        )

        assert not deactivate.is_valid()
        assert deactivate.errors["is_active"] == ["Der eigene Zugang kann hier nicht deaktiviert werden."]
