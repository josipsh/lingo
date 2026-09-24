"""Protected learner identifiers."""

from __future__ import annotations

import hashlib
import hmac


def learner_id(phone_number: str, secret: str) -> str:
    """Return a stable identifier without retaining the raw phone number."""
    if not phone_number or not secret:
        raise ValueError("phone number and learner ID secret are required")
    normalized = "".join(character for character in phone_number if character.isdigit())
    if not normalized:
        raise ValueError("phone number must contain digits")
    return hmac.new(secret.encode(), normalized.encode(), hashlib.sha256).hexdigest()
