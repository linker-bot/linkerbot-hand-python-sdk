"""O6 robotic hand control interface.

This module provides the main O6 class for controlling the O6 robotic hand
via CAN bus communication. It integrates angle control, force sensor
data acquisition, and unified sensor streaming into a single interface.
"""

import queue
import threading
import time
from collections.abc import Callable
from typing import Literal, cast

from linkerbot_transport.can import CanInterface, CANMessageDispatcher
from linkerbot_transport.errors import (
    CANError,
    StateError,
    TimeoutError,
    ValidationError,
)
from linkerbot_transport.queue import IterableQueue

from .._can_protocol import CANSubscriptionScope
from .angle import AngleManager
from .events import (
    AccelerationEvent,
    AngleEvent,
    FaultEvent,
    ForceSensorEvent,
    O6Snapshot,
    SensorEvent,
    SensorSource,
    SpeedEvent,
    TemperatureEvent,
    TorqueEvent,
)
from .fault import FaultManager
from .force_sensor import ForceSensorManager
from .speed import AccelerationManager, SpeedManager
from .temperature import TemperatureManager
from .torque import TorqueManager
from .version import VersionManager

_DEFAULT_POLL_INTERVALS: dict[SensorSource, float] = {
    SensorSource.ANGLE: 1 / 60,
    SensorSource.FORCE_SENSOR: 1 / 30,
}
_THREAD_JOIN_TIMEOUT_S = 2.0


class O6:
    """Main interface for O6 robotic hand control.

    This class provides a unified interface for controlling the O6 robotic hand,
    integrating angle control, speed control, sensor data acquisition, and
    unified sensor streaming.

    The O6 class should be used as a context manager to ensure proper resource
    cleanup:

    ```python
    with O6(side='left', interface_name='can0') as hand:
        # Control angles
        hand.angle.set_angles([50, 30, 60, 60, 60, 60])

        # Start polling sensors
        hand.start_polling({SensorSource.ANGLE: 0.05, SensorSource.TEMPERATURE: 1.0})

        # Read cached data
        snap = hand.get_snapshot()
        print(snap.angle, snap.temperature)

        # Stream all events
        for event in hand.stream():
            match event:
                case AngleEvent(data=ad):
                    print(f"Angles: {ad.angles}")
                case TemperatureEvent(data=td):
                    print(f"Temps: {td.temperatures}")
            if should_stop():
                break

        hand.stop_polling()
        hand.stop_stream()
    ```

    Attributes:
        angle: AngleManager instance for joint angle control and sensing.
        speed: SpeedManager instance for motor speed control and sensing.
        acceleration: AccelerationManager instance for motor acceleration control.
        force_sensor: ForceSensorManager instance for force sensor data acquisition.
        torque: TorqueManager instance for joint torque control and sensing.
        temperature: TemperatureManager instance for temperature data acquisition.
        fault: FaultManager instance for fault status reading.
        version: VersionManager instance for device version information.

    Args:
        side: Side of the hand (left or right, default: left).
        interface_name: Name of the CAN interface (e.g., 'can0', 'vcan0').
        interface_type: Type of CAN interface backend (default: 'socketcan').
    """

    def __init__(
        self,
        side: Literal["left", "right"],
        interface_name: str,
        interface_type: str = "socketcan",
        dispatcher: CANMessageDispatcher | None = None,
        auto_start_polling: bool = False,
    ) -> None:
        """Initialize the O6 robotic hand interface.

        Args:
            side: Side of the hand (left or right, default: left).
            interface_name: Name of the CAN interface (e.g., 'can0', 'vcan0').
            interface_type: Type of CAN interface backend (default: 'socketcan').
            dispatcher: Optional injected CAN dispatcher for tests or shared buses.
            auto_start_polling: Start legacy default polling during construction.
                Defaults to False; lifecycle users should call start_polling().
        """
        if side not in ("left", "right"):
            raise ValidationError(f"side must be 'left' or 'right', got {side!r}")
        self._bus_error: Exception | None = None
        self._close_lock = threading.RLock()
        self._resources_closed = False
        self._closed = False
        self._owns_dispatcher = dispatcher is None
        self._dispatcher: CANMessageDispatcher | None = None
        self._subscription_scope: CANSubscriptionScope | None = None
        self._arbitration_id = 0x27 if side == "right" else 0x28
        self._unified_queue: IterableQueue[SensorEvent] | None = None
        self._stop_polling = threading.Event()
        self._stop_polling.set()
        self._polling_threads: dict[str, threading.Thread] = {}
        self._polling_senders: dict[str, Callable[[], None]] = {}

        try:
            self._dispatcher = (
                dispatcher
                if dispatcher is not None
                else CANMessageDispatcher(
                    interface_name=interface_name,
                    interface_type=interface_type,
                    on_bus_error=self._on_bus_error,
                )
            )
            self._subscription_scope = CANSubscriptionScope(self._dispatcher)
            manager_dispatcher = cast(CANMessageDispatcher, self._subscription_scope)

            self.angle = AngleManager(
                arbitration_id=self._arbitration_id,
                dispatcher=manager_dispatcher,
            )
            self.force_sensor = ForceSensorManager(
                arbitration_id=self._arbitration_id, dispatcher=manager_dispatcher
            )
            self.torque = TorqueManager(
                arbitration_id=self._arbitration_id, dispatcher=manager_dispatcher
            )
            self.speed = SpeedManager(
                arbitration_id=self._arbitration_id, dispatcher=manager_dispatcher
            )
            self.acceleration = AccelerationManager(
                arbitration_id=self._arbitration_id, dispatcher=manager_dispatcher
            )
            self.temperature = TemperatureManager(
                arbitration_id=self._arbitration_id, dispatcher=manager_dispatcher
            )
            self.fault = FaultManager(
                arbitration_id=self._arbitration_id, dispatcher=manager_dispatcher
            )
            self.version = VersionManager(
                arbitration_id=self._arbitration_id, dispatcher=manager_dispatcher
            )
            self._polling_senders = {
                "angle": self.angle._send_sense_request,
                "torque": self.torque._send_sense_request,
                "speed": self.speed._send_sense_request,
                "acceleration": self.acceleration._send_sense_request,
                "temperature": self.temperature._send_sense_request,
                "fault": self.fault._send_fault_request,
                "force_sensor": self.force_sensor._send_sense_request,
            }

            self.angle._set_event_sink(lambda d: self._push_event(AngleEvent(data=d)))
            self.torque._set_event_sink(lambda d: self._push_event(TorqueEvent(data=d)))
            self.speed._set_event_sink(lambda d: self._push_event(SpeedEvent(data=d)))
            self.acceleration._set_event_sink(
                lambda d: self._push_event(AccelerationEvent(data=d))
            )
            self.temperature._set_event_sink(
                lambda d: self._push_event(TemperatureEvent(data=d))
            )
            self.fault._set_event_sink(lambda d: self._push_event(FaultEvent(data=d)))
            self.force_sensor._set_event_sink(
                lambda d: self._push_event(ForceSensorEvent(data=d))
            )

            if auto_start_polling:
                self.start_polling()
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise

    @property
    def can_interface(self) -> CanInterface:
        """CAN interface this hand is bound to."""
        dispatcher = self._dispatcher
        if dispatcher is None:  # pragma: no cover - construction never returns here
            raise StateError("O6 dispatcher is unavailable")
        return dispatcher.can_interface

    def __enter__(self) -> "O6":
        """Enter the context manager.

        Returns:
            Self for use in with statements.
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Exit the context manager and clean up resources.

        Returns:
            False to propagate exceptions.
        """
        self.close()
        return False

    # ===== Unified snapshot =====

    def get_snapshot(self) -> O6Snapshot:
        """Get all sensor data as a single snapshot (non-blocking).

        Returns:
            O6Snapshot with the latest cached data from all sensors.
            Individual fields are None if no data has been received yet.
        """
        return O6Snapshot(
            angle=self.angle.get_snapshot(),
            torque=self.torque.get_snapshot(),
            speed=self.speed.get_snapshot(),
            acceleration=self.acceleration.get_snapshot(),
            temperature=self.temperature.get_snapshot(),
            fault=self.fault.get_snapshot(),
            force_sensor=self.force_sensor.get_snapshot(),
            timestamp=time.time(),
        )

    # ===== Unified stream =====

    def stream(self, maxsize: int = 100) -> IterableQueue[SensorEvent]:
        """Start unified event stream, delivering all sensor responses.

        Returns an IterableQueue that receives SensorEvent instances.
        Use match-case to dispatch by event type.

        Calling stream() again automatically closes the previous queue.

        Args:
            maxsize: Maximum queue size (default: 100). When full, oldest event is dropped.

        Returns:
            IterableQueue[SensorEvent] for receiving sensor events.
        """
        self._ensure_open()
        if self._unified_queue is not None:
            self.stop_stream()
        self._unified_queue = IterableQueue(maxsize=maxsize)
        return self._unified_queue

    def stop_stream(self) -> None:
        """Stop the unified event stream.

        Closes the queue, causing any active for-loop to exit.
        Idempotent: safe to call multiple times.
        """
        if self._unified_queue is None:
            return
        self._unified_queue.close()
        self._unified_queue = None

    # ===== Polling control =====

    def start_polling(
        self,
        intervals: dict[SensorSource, float] = _DEFAULT_POLL_INTERVALS,
    ) -> None:
        """Start background polling for sensor data.

        Polling sends periodic query requests to the specified sensors.
        Responses are cached (readable via get_snapshot()) and pushed
        to the stream if active.

        Calling start_polling() again automatically stops the previous polling.

        Args:
            intervals: Per-sensor polling intervals in seconds.
                Defaults to all sensors at model-specific intervals.

        Raises:
            ValidationError: If any interval is not positive.
        """
        with self._close_lock:
            self._ensure_open()
            if self._polling_threads or not self._stop_polling.is_set():
                self.stop_polling()
            for source, interval in intervals.items():
                if interval <= 0:
                    raise ValidationError(
                        f"Interval for {source.value} must be positive, got {interval}"
                    )
            self._stop_polling.clear()
            try:
                for source, interval in intervals.items():
                    thread = threading.Thread(
                        target=self._polling_loop,
                        args=(source.value, interval),
                        daemon=True,
                        name=f"O6-Polling-{source.value}",
                    )
                    thread.start()
                    self._polling_threads[source.value] = thread
            except BaseException:
                self.stop_polling()
                raise

    def stop_polling(self) -> None:
        """Stop all background polling.

        Idempotent: safe to call multiple times.
        """
        with self._close_lock:
            self._stop_polling.set()
            deadline = time.monotonic() + _THREAD_JOIN_TIMEOUT_S
            current = threading.current_thread()
            alive: dict[str, threading.Thread] = {}
            for source_name, thread in tuple(self._polling_threads.items()):
                if thread is not current and thread.is_alive():
                    thread.join(timeout=max(0.0, deadline - time.monotonic()))
                if thread.is_alive():
                    alive[source_name] = thread
            self._polling_threads = alive
            if alive:
                names = ", ".join(thread.name for thread in alive.values())
                raise TimeoutError(f"O6 polling threads did not stop: {names}")

    # ===== Lifecycle =====

    def close(self) -> None:
        """Close the O6 interface and release all resources.

        This method is idempotent and safe to call multiple times.
        """
        with self._close_lock:
            if self._resources_closed:
                return

            self._closed = True
            self.stop_polling()
            self.stop_stream()
            if self._subscription_scope is not None:
                self._subscription_scope.close()

            if self._owns_dispatcher and self._dispatcher is not None:
                self._dispatcher.stop()

            self._resources_closed = True

    def __del__(self) -> None:
        """Destructor for defensive resource cleanup."""
        if not hasattr(self, "_close_lock"):
            return
        try:
            self.close()
        except BaseException:
            pass

    def is_closed(self) -> bool:
        """Check if the interface has been closed.

        Returns:
            True if the interface is closed, False otherwise.
        """
        return self._closed

    def _on_bus_error(self, error: Exception) -> None:
        self._bus_error = error
        self._closed = True

    def _ensure_open(self) -> None:
        if self._bus_error is not None:
            raise CANError(f"CAN bus unavailable: {self._bus_error}")
        if self._closed:
            raise StateError(
                "O6 interface is closed. Create a new instance or use context manager."
            )

    # ===== Internal =====

    def _polling_loop(self, source_name: str, interval: float) -> None:
        sender = self._polling_senders[source_name]
        while not self._stop_polling.is_set():
            sender()
            self._stop_polling.wait(interval)

    def _push_event(self, event: SensorEvent) -> None:
        q = self._unified_queue
        if q is None:
            return
        try:
            q.put_nowait(event)
        except (queue.Full, StateError):
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(event)
            except (queue.Full, StateError):
                pass
