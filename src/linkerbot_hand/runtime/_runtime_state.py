"""Small synchronized identity and generation state for ``DeviceRuntime``."""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from typing import TypeVar

from ..api.errors import (
    DeviceNotReadyError,
    IdentityMismatchError,
    ProtocolError,
    ValidationError,
)
from ..capabilities.types import DeviceCapabilities
from ..protocols.base import IdentityResult

_ResultT = TypeVar("_ResultT")


class RuntimeState:
    """Protect verified capabilities, fingerprint, and connection generation."""

    def __init__(self, declared: DeviceCapabilities) -> None:
        if not isinstance(declared, DeviceCapabilities):
            raise ValidationError("declared must be DeviceCapabilities")
        self._lock = threading.Lock()
        self._declared = declared
        self._capabilities = declared
        self._fingerprint: Hashable | None = None
        self._generation = 0
        self._ready = False
        self._admitting = False

    @property
    def declared(self) -> DeviceCapabilities:
        return self._declared

    def capabilities(self) -> DeviceCapabilities:
        with self._lock:
            return self._capabilities

    def generation(self) -> int:
        with self._lock:
            return self._generation

    def ready(self) -> bool:
        with self._lock:
            return self._ready

    def run_if_admitting(
        self,
        action: Callable[[], _ResultT],
        *,
        error: DeviceNotReadyError,
    ) -> _ResultT:
        """Run one admission-sensitive action under the lifecycle lock."""

        with self._lock:
            if not self._ready or not self._admitting:
                raise error
            return action()

    def stop_admission(self) -> None:
        """Synchronously prevent new work before the actor ends a generation."""

        with self._lock:
            self._admitting = False

    def validate_identity(self, identity: IdentityResult, *, recovery: bool) -> None:
        """Reject a wrong model, side, or replacement physical device."""

        if not isinstance(identity, IdentityResult):
            raise ProtocolError("identity transaction returned an invalid result")
        capabilities = identity.capabilities
        if capabilities.model != self._declared.model:
            raise IdentityMismatchError(
                f"expected {self._declared.model}, got {capabilities.model}",
                operation="open" if not recovery else "recover",
                model=self._declared.model,
                generation=self.generation(),
            )
        if capabilities.side is not self._declared.side:
            raise IdentityMismatchError(
                "connected device side does not match configuration",
                operation="open" if not recovery else "recover",
                model=self._declared.model,
                generation=self.generation(),
            )
        if (
            identity.device_info.model != capabilities.model
            or identity.device_info.side is not capabilities.side
        ):
            raise ProtocolError("identity profile and device info disagree")
        with self._lock:
            if (
                self._fingerprint is not None
                and self._fingerprint != identity.fingerprint
            ):
                raise IdentityMismatchError(
                    "connected physical device fingerprint changed",
                    operation="recover" if recovery else "open",
                    model=self._declared.model,
                    generation=self._generation,
                )

    def commit_identity(self, identity: IdentityResult, *, generation: int) -> None:
        """Atomically expose a verified profile only after baseline completion."""

        if type(generation) is not int or generation <= 0:
            raise ValidationError("generation must be a positive int")
        with self._lock:
            self._capabilities = identity.capabilities
            if self._fingerprint is None:
                self._fingerprint = identity.fingerprint
            self._generation = generation
            self._ready = True
            self._admitting = True

    def mark_not_ready(self) -> None:
        with self._lock:
            self._ready = False
            self._admitting = False


__all__ = ["RuntimeState"]
