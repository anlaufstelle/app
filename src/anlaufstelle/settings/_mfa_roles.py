"""Parsing + Fail-loud-Validierung der rollen-basierten MFA-Enforcement-Sets.

Ausgelagert aus ``base.py`` (Refs #1639), damit die Env-Parse-/Validierungs-
logik ohne Settings-Reimport testbar ist. Zwei Kommalisten-Settings steuern,
welche Rollen MFA allein ueber ihre Rolle erzwingen (nur zusammen mit
``MFA_ENFORCE_PRIVILEGED_ROLES``):

* ``MFA_ENFORCED_ROLES_IMMEDIATE`` — SOFORTIGE Erzwingung ohne Grace-Frist
  (Default ``super_admin,facility_admin``; A3.1, Refs #1019).
* ``MFA_ENFORCED_ROLES_GRACE`` — Erzwingung erst nach der 30-Tage-Grace-Frist
  (Default ``lead,staff,assistant``; Refs #689/#1639), damit Bestandsnutzer beim
  Aktivieren nicht ausgesperrt werden.

Die kanonischen Rollenwerte sind hier bewusst als Literal gepflegt: Settings-
Module laufen vor dem App-Registry-Load und duerfen ``core.models.user.User``
nicht importieren. Ein Drift-Guard-Test (``test_mfa_role_config``) friert
``VALID_MFA_ROLES`` gegen ``User.Role`` ein.
"""

from django.core.exceptions import ImproperlyConfigured

# == User.Role.values (per Test gegen das Modell eingefroren, Refs #1639).
VALID_MFA_ROLES = frozenset({"super_admin", "facility_admin", "lead", "staff", "assistant"})


def _parse_role_list(raw: str, *, setting_name: str) -> tuple[str, ...]:
    """Kommaliste -> Rollen-Tupel; leere Liste erlaubt (= bewusstes Abschalten).

    Ein leerer oder rein-whitespace String ergibt genau ein leeres Tupel und
    schaltet die Rollen-Ebene bewusst ab. Ein unbekannter Rollenname bricht den
    Start fail-closed ab (``ImproperlyConfigured``), statt still eine Rolle aus
    dem Zwang fallen zu lassen.
    """
    roles = tuple(part.strip() for part in raw.split(",") if part.strip())
    unknown = sorted({r for r in roles if r not in VALID_MFA_ROLES})
    if unknown:
        raise ImproperlyConfigured(
            f"{setting_name} enthaelt unbekannte Rolle(n): {', '.join(unknown)}. "
            f"Erlaubt sind: {', '.join(sorted(VALID_MFA_ROLES))}."
        )
    return roles


def parse_enforced_role_sets(immediate_raw: str, grace_raw: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(immediate, grace)`` aus zwei Kommalisten, fail-loud validiert.

    Leere Mengen sind erlaubt (bewusstes Abschalten). Unbekannte Rollennamen
    oder eine Ueberschneidung beider Mengen brechen den Start fail-closed ab —
    eine Rolle darf nicht gleichzeitig sofort UND grace-behaftet erzwungen
    werden (die Middleware muesste sonst raten).
    """
    immediate = _parse_role_list(immediate_raw, setting_name="MFA_ENFORCED_ROLES_IMMEDIATE")
    grace = _parse_role_list(grace_raw, setting_name="MFA_ENFORCED_ROLES_GRACE")
    overlap = sorted(set(immediate) & set(grace))
    if overlap:
        raise ImproperlyConfigured(
            "MFA_ENFORCED_ROLES_IMMEDIATE und MFA_ENFORCED_ROLES_GRACE duerfen sich nicht "
            f"ueberschneiden: {', '.join(overlap)}. Eine Rolle ist entweder sofort ODER "
            "grace-behaftet MFA-pflichtig, nicht beides."
        )
    return immediate, grace
