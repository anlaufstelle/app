"""Helpers for RBAC-Matrix-Tests (Welle 6 #929)."""


def login_user_fixture(client, user_fixture, request):
    """Resolve a fixture name to a user object and force-login."""
    user = request.getfixturevalue(user_fixture)
    client.force_login(user)
    return user
