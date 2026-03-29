"""Password generation for initial user passwords."""

import logging
import secrets
import string

logger = logging.getLogger(__name__)


def generate_initial_password(length=12):
    """Generate a random initial password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))
