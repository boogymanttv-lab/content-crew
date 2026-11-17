"""Publishing adapters.

Each provider is a function (channel, post) -> {"external_id": str, "simulated": bool}
that raises PublishError on failure. Only `dryrun` ships today. To post for
real, add a provider that calls a posting service whose app already has the
platform approvals (TikTok only allows public API posts from audited apps;
Instagram needs a Business account), then register it in PROVIDERS.
"""
import secrets


class PublishError(Exception):
    pass


def _dryrun(channel, post):
    return {"external_id": "sim-" + secrets.token_hex(4), "simulated": True}


PROVIDERS = {"dryrun": _dryrun}
PROVIDER_LABELS = {"dryrun": "Simulated (no real posting)"}


def publish_post(channel, post):
    provider = PROVIDERS.get(channel["provider"])
    if provider is None:
        raise PublishError(f"Provider '{channel['provider']}' is not configured.")
    return provider(channel, post)
