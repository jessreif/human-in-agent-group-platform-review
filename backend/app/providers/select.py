"""Provider selection (``MODEL_PROVIDER``): which adapter the worker builds.

- ``auto`` (default): the pinned loopback adapter when ``LOCAL_MODEL_BASE_URL`` is set,
  else deterministic-only (``None``) — identical to a provider outage under D14.
- ``ollama``/``local``: force the loopback adapter (fails startup if unconfigured).
- ``openai``/``minimax``: build the remote adapters in ``remote.py`` — the worker then
  refuses them at ``ensure_provider_allowed`` in every mode (BA-35); those adapters
  are for offline fixture comparison only, never session traffic.
- ``none``/``off``: deterministic-only even if endpoints are configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.providers.base import TextProvider
from app.providers.local import provider_from_settings as _local_from_settings
from app.providers.remote import remote_from_settings

if TYPE_CHECKING:
    from app.settings import Settings


def provider_from_settings(settings: Settings) -> TextProvider | None:
    selected = settings.MODEL_PROVIDER
    if selected in ("none", "off"):
        return None
    remote = remote_from_settings(settings)
    if remote is not None:
        return remote
    if selected in ("ollama", "local") and settings.LOCAL_MODEL_BASE_URL is None:
        raise ValueError("MODEL_PROVIDER=ollama requires LOCAL_MODEL_BASE_URL and LOCAL_MODEL_ID")
    return _local_from_settings(settings)
