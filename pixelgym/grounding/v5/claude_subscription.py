"""Fresh, same-credential Max verification before token-route model execution."""

from __future__ import annotations

import http.client
import json
import ssl
from collections.abc import Mapping
from typing import Any

PROFILE_HOST = "api.anthropic.com"
PROFILE_PATH = "/api/oauth/profile"
PROFILE_URL = "https://" + PROFILE_HOST + PROFILE_PATH
PROFILE_TIMEOUT_SECONDS = 10.0
MAX_PROFILE_BYTES = 65_536
VERIFICATION_VERSION = "pixelgym-claude-same-credential-max-profile-v1"


class SubscriptionVerificationError(ValueError):
    """Safe diagnostics that never include headers, credentials, or response bodies."""


def max_profile_receipt(profile: Any) -> dict[str, Any]:
    """Require the authenticated organization's tier; retain no account identifiers."""

    if not isinstance(profile, Mapping):
        raise SubscriptionVerificationError("OAuth profile has an invalid shape")
    account = profile.get("account")
    organization = profile.get("organization")
    for item in (account, organization):
        if not isinstance(item, Mapping) or not isinstance(item.get("uuid"), str):
            raise SubscriptionVerificationError("OAuth profile omits account or organization")
        if not item["uuid"].strip():
            raise SubscriptionVerificationError("OAuth profile omits account or organization")
    assert isinstance(organization, Mapping)
    if organization.get("organization_type") != "claude_max":
        raise SubscriptionVerificationError("OAuth profile does not verify the Max organization")
    return {
        "version": VERIFICATION_VERSION,
        "endpoint": PROFILE_URL,
        "method": "GET",
        "http_status": 200,
        "organization_type": "claude_max",
        "same_authentication_as_cli": True,
        "authenticated_read_requests": 1,
        "model_requests": 0,
    }


def verify_max_subscription(access_value: str) -> dict[str, Any]:
    """One bounded HTTPS GET; no proxies, redirects, retries, caches, or model call.

    ``setup-token`` credentials can lack profile-read scope. A 401/403 is a hard
    stop requiring an OAuth credential authorized to read its own profile. Neither
    the expected plan tier nor local CLI subscription hints substitute for proof.
    """

    if not access_value or any(character.isspace() for character in access_value):
        raise SubscriptionVerificationError(
            "OAuth profile verification requires a valid credential"
        )
    connection = http.client.HTTPSConnection(
        PROFILE_HOST, timeout=PROFILE_TIMEOUT_SECONDS, context=ssl.create_default_context()
    )
    try:
        connection.request(
            "GET",
            PROFILE_PATH,
            headers={
                "Authorization": "Bearer " + access_value,
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "User-Agent": "claude-code/2.1.292",
            },
        )
        response = connection.getresponse()
        if response.status in (401, 403):
            raise SubscriptionVerificationError(
                "OAuth profile verification refused (HTTP "
                + str(response.status)
                + "); a same-credential profile-readable OAuth login is required"
            )
        if response.status != 200:
            raise SubscriptionVerificationError(
                "OAuth profile verification did not return HTTP 200"
            )
        body = response.read(MAX_PROFILE_BYTES + 1)
        if len(body) > MAX_PROFILE_BYTES:
            raise SubscriptionVerificationError("OAuth profile response exceeded the size limit")
        try:
            profile = json.loads(body)
        except (UnicodeError, ValueError):
            raise SubscriptionVerificationError("OAuth profile is not valid JSON") from None
        return max_profile_receipt(profile)
    except SubscriptionVerificationError:
        raise
    except (OSError, http.client.HTTPException, ValueError):
        # Network/HTTP exceptions can contain request or response data. Keep only
        # a fixed diagnostic and suppress their context at the public boundary.
        raise SubscriptionVerificationError("OAuth profile verification failed") from None
    finally:
        connection.close()
