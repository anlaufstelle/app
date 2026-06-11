"""Helpers for RBAC-Matrix-Tests (#929)."""


def login_user_fixture(client, user_fixture, request):
    """Resolve a fixture name to a user object and force-login."""
    user = request.getfixturevalue(user_fixture)
    client.force_login(user)
    return user


def resolve_fixture_kwargs(pairs, request):
    """Löst ("kwarg", "ref")-Paare der AuthZ-Tabelle zu URL-Kwargs auf.

    Konvention (siehe Docstring in _authz_expectations.py): Enthält der
    Wert einen Punkt, wird er als Fixture-Attributpfad aufgelöst
    ("fixture_name.attr"); sonst ist er ein Literal und wird unverändert
    als URL-Kwarg eingesetzt.
    """
    kwargs = {}
    for name, ref in pairs:
        if "." in ref:
            fixture_name, attr = ref.split(".", 1)
            kwargs[name] = getattr(request.getfixturevalue(fixture_name), attr)
        else:
            kwargs[name] = ref
    return kwargs
