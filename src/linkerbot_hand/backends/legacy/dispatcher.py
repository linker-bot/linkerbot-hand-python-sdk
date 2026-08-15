"""Command-scoped immediate sends over legacy dispatcher objects."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ...errors import TransportError


class DispatchClaimRejected(Exception):
    """Abort a legacy protocol call before its first transport send."""


class _ImmediateDispatchState:
    __slots__ = ("claim_dispatch", "claim_result", "mark_dispatched", "marked")

    def __init__(
        self,
        claim_dispatch: Callable[[], bool],
        mark_dispatched: Callable[[], None],
    ) -> None:
        self.claim_dispatch = claim_dispatch
        self.claim_result: bool | None = None
        self.mark_dispatched = mark_dispatched
        self.marked = False


class ScopedImmediateDispatcher:
    """Add command-scoped synchronous sends to an injected legacy dispatcher.

    Ordinary legacy calls are delegated unchanged. During
    :meth:`immediate_dispatch`, calls to :meth:`send` use the injected
    dispatcher's ``send_now`` method so the unified worker owns transport timing.
    """

    def __init__(self, dispatcher: Any) -> None:
        """Wrap a legacy dispatcher without starting I/O.

        Args:
            dispatcher: Legacy dispatcher providing ``send`` and ``send_now``.
        """
        self._dispatcher = dispatcher
        self._local = threading.local()

    def send(self, message: Any) -> None:
        """Send through the active command scope or delegate normally.

        Args:
            message: Legacy protocol message to send.

        Raises:
            TransportError: If scoped dispatch is active but ``send_now`` is
                unavailable.
        """
        state: _ImmediateDispatchState | None = getattr(self._local, "state", None)
        if state is None:
            self._dispatcher.send(message)
            return
        send_now = getattr(self._dispatcher, "send_now", None)
        if not callable(send_now):
            raise TransportError(
                "injected legacy dispatcher must provide callable send_now()"
            )
        if state.claim_result is None:
            state.claim_result = state.claim_dispatch()
        if not state.claim_result:
            return
        send_now(message)
        if not state.marked:
            state.mark_dispatched()
            state.marked = True

    @contextmanager
    def immediate_dispatch(
        self,
        claim_dispatch: Callable[[], bool],
        mark_dispatched: Callable[[], None],
    ) -> Iterator[None]:
        """Route sends in this thread through one synchronous command scope.

        Args:
            claim_dispatch: Callback evaluated once before the first send.
            mark_dispatched: Callback evaluated once after the first successful
                transport send.

        Yields:
            Control to the legacy manager operation within the active scope.
        """
        previous = getattr(self._local, "state", None)
        self._local.state = _ImmediateDispatchState(claim_dispatch, mark_dispatched)
        try:
            yield
        finally:
            if previous is None:
                del self._local.state
            else:
                self._local.state = previous

    def __getattr__(self, name: str) -> Any:
        """Delegate unsupported attributes to the wrapped dispatcher."""
        return getattr(self._dispatcher, name)


__all__ = ["DispatchClaimRejected", "ScopedImmediateDispatcher"]
