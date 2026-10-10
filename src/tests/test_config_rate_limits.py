"""Every configuration mutation has the same per-user limit as domain mutations."""

from uuid import UUID

import pytest
from django.urls import reverse

from core.urls._config import urlpatterns

POST_ROUTES = [pattern for pattern in urlpatterns if hasattr(pattern.callback.view_class, "post")]


@pytest.mark.django_db
@pytest.mark.parametrize("pattern", POST_ROUTES, ids=lambda pattern: pattern.name)
def test_configuration_post_is_rate_limited(client, admin_user, ratelimit_enabled, pattern):
    assert POST_ROUTES
    client.force_login(admin_user)
    kwargs = {name: UUID(int=1) for name in pattern.pattern.converters}
    url = reverse(f"core:{pattern.name}", kwargs=kwargs)
    # Invalid input exercises the public handler without changing configuration.
    for _ in range(60):
        response = client.post(url)
        assert response.status_code != 429
    assert client.post(url).status_code == 429
    ratelimit_enabled.advance(3600)
    assert client.post(url).status_code != 429
