"""No-I/O factory for unified devices backed by legacy model drivers."""

from __future__ import annotations

from typing import Any

from ...backend import Clock
from ...contracts import HandSide, SessionConfig
from ...models import get_model_capabilities
from ...session import HandDevice
from .backend import LegacyHandBackend


def create_hand_device(
    model: str,
    *,
    side: HandSide | str = HandSide.UNKNOWN,
    session_config: SessionConfig | None = None,
    clock: Clock | None = None,
    **transport_options: Any,
) -> HandDevice:
    """Create a no-I/O device configuration for a supported legacy model.

    The returned object does not construct the legacy driver or open transport
    resources until :meth:`HandDevice.open` is called.

    Args:
        model: Canonical model name or supported alias.
        side: Requested hand side.
        session_config: Optional lifecycle and scheduler configuration.
        clock: Optional injected clock.
        **transport_options: Options forwarded to the legacy model constructor.

    Returns:
        Configured, closed unified hand device.

    Raises:
        ValidationError: If model, side, or configuration is invalid.
    """
    capabilities = get_model_capabilities(model, side=side)

    def backend_factory() -> LegacyHandBackend:
        return LegacyHandBackend(
            capabilities.model,
            side=capabilities.side,
            options=transport_options,
        )

    return HandDevice(
        capabilities,
        backend_factory,
        config=session_config,
        clock=clock,
    )


__all__ = ["create_hand_device"]
