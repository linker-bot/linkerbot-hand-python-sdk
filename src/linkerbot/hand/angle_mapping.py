"""Standard-to-hardware raw angle mapping for hand angle control.

The public hand angle APIs use two input spaces:
- percentage angles, via ``set_angles()``, in the existing 0-100 range;
- standard raw angles, via ``set_raw_angles()``, in the 0-255 range.

This module keeps the per-device mapping from standard raw values to the raw
values sent to hardware. Mappings are cached in memory for the send path and
persisted to a TOML file when they are created, updated, or reset.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

try:  # pragma: no cover - fcntl is unavailable on Windows
    _fcntl = importlib.import_module("fcntl")
except ImportError:  # pragma: no cover - platform dependent
    _fcntl = None

if sys.version_info >= (3, 11):  # pragma: no cover - depends on Python version
    import tomllib
else:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib

# tomli_w is imported lazily inside _save_file() so importing the hand package
# does not require it until a mapping is actually persisted to disk.

from linkerbot_transport.errors import ValidationError

_MAPPING_SIZE = 256
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[Path, threading.RLock] = {}


class AngleMappingManager:
    """Manage one hand device's standard-to-hardware raw angle mapping.

    A mapping matrix has ``joint_count`` rows and 256 columns. Row ``j`` belongs
    to joint ``j`` in the model's documented joint order; column ``i`` is the
    standard raw input value; the stored integer is the hardware raw value to
    send. The TOML section is namespaced by model, side, and CAN interface so
    left/right hands and same-model devices on different buses can be calibrated
    independently.
    """

    def __init__(
        self,
        model: str,
        joint_names: list[str],
        mapping_path: str | Path | None = None,
        *,
        side: str | None = None,
        interface_name: str | None = None,
    ) -> None:
        self._model = model
        self._side = side or "unknown"
        self._interface_name = interface_name or "unknown"
        self._section_key = f"{self._model}:{self._side}:{self._interface_name}"
        self._joint_names = list(joint_names)
        self._joint_count = len(joint_names)
        self._path = (
            Path(mapping_path).expanduser() if mapping_path else _default_mapping_path()
        )
        self._state_lock = threading.Lock()
        self._update_lock = threading.Lock()
        mapping = self._load_or_create()
        self._generation = (mapping, _build_inverse_mapping(mapping))

    @property
    def path(self) -> Path:
        """Return the TOML file path used for persistence."""
        return self._path

    def map_values(self, raw_values: list[int]) -> list[int]:
        """Map standard raw joint values to hardware raw values."""
        validated = validate_raw_values(raw_values, self._joint_count)
        with self._state_lock:
            mapping = self._generation[0]
        return [mapping[index][value] for index, value in enumerate(validated)]

    def unmap_values(self, hardware_values: list[int]) -> list[int]:
        """Project hardware raw values back into the standard raw space.

        Custom compatibility mappings may be non-injective or omit hardware
        values. In those cases the nearest mapped value is used, with the
        lowest standard raw index winning ties. This keeps legacy percentage
        readback deterministic while preserving the exact hardware values on
        the unified raw sensor path.
        """
        validated = validate_raw_values(hardware_values, self._joint_count)
        with self._state_lock:
            inverse_mapping = self._generation[1]
        return [inverse_mapping[index][value] for index, value in enumerate(validated)]

    def get_mapping(self) -> list[list[int]]:
        """Return a deep copy of the current mapping matrix."""
        with self._state_lock:
            mapping = self._generation[0]
        return deepcopy(mapping)

    def get_default_mapping(self) -> list[list[int]]:
        """Return a new default linear mapping matrix."""
        return default_angle_mapping(self._joint_count)

    def set_mapping(self, mapping: list[list[int]]) -> None:
        """Replace the current mapping after validation and persist it."""
        validated = validate_angle_mapping(mapping, self._joint_count)
        inverse_mapping = _build_inverse_mapping(validated)
        with self._update_lock:
            with _mapping_file_lock(self._path):
                data = self._load_file()
                data[self._section_key] = self._section(validated)
                self._save_file(data)
            with self._state_lock:
                self._generation = (validated, inverse_mapping)

    def reset_mapping(self) -> None:
        """Restore the default linear mapping and persist it."""
        self.set_mapping(default_angle_mapping(self._joint_count))

    def _load_or_create(self) -> list[list[int]]:
        with _mapping_file_lock(self._path):
            data = self._load_file()
            section = data.get(self._section_key)
            if section is None:
                mapping = default_angle_mapping(self._joint_count)
                data[self._section_key] = self._section(mapping)
                self._save_file(data)
                return mapping
        if not isinstance(section, dict):
            raise ValidationError(
                f"Angle mapping section {self._section_key!r} must be a table"
            )
        joint_names = section.get("joint_names")
        if joint_names is not None and joint_names != self._joint_names:
            raise ValidationError(
                f"Angle mapping section {self._section_key!r} joint_names do not match "
                "the current hand model"
            )
        mapping = section.get("mapping")
        return validate_angle_mapping(mapping, self._joint_count)

    def _section(self, mapping: list[list[int]]) -> dict[str, Any]:
        return {
            "model": self._model,
            "side": self._side,
            "interface_name": self._interface_name,
            "joint_names": list(self._joint_names),
            "mapping": deepcopy(mapping),
        }

    def _load_file(self) -> dict[str, Any]:
        try:
            with self._path.open("rb") as f:
                data = tomllib.load(f)
        except FileNotFoundError:
            return {"version": 1}
        except tomllib.TOMLDecodeError as exc:
            raise ValidationError(f"Invalid angle mapping TOML: {exc}") from exc
        if not isinstance(data, dict):
            raise ValidationError("Angle mapping TOML must contain a table")
        data.setdefault("version", 1)
        return data

    def _save_file(self, data: dict[str, Any]) -> None:
        import tomli_w  # local import: only required when persisting to disk

        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)
                tmp_file.write(tomli_w.dumps(data))
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_path, self._path)
            _fsync_directory(self._path.parent)
            tmp_path = None
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)


def _path_lock(path: Path) -> threading.RLock:
    key = path.resolve()
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _default_mapping_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "linkerbot" / "hand_angle_mappings.toml"


@contextmanager
def _mapping_file_lock(path: Path) -> Iterator[None]:
    """Serialize mapping-file transactions in-process and across POSIX processes."""
    with _path_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(f".{path.name}.lock")
        with lock_path.open("a+b") as lock_file:
            if _fcntl is not None:
                _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX)
            try:
                yield
            finally:
                if _fcntl is not None:
                    _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":  # pragma: no cover - platform dependent
        return
    directory_fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _build_inverse_mapping(mapping: list[list[int]]) -> list[list[int]]:
    return [
        [
            min(
                range(_MAPPING_SIZE),
                key=lambda standard, target=hardware: (
                    abs(row[standard] - target),
                    standard,
                ),
            )
            for hardware in range(_MAPPING_SIZE)
        ]
        for row in mapping
    ]


def default_angle_mapping(joint_count: int) -> list[list[int]]:
    """Create a default linear raw angle mapping matrix."""
    return [list(range(_MAPPING_SIZE)) for _ in range(joint_count)]


def validate_angle_mapping(
    mapping: object, joint_count: int, *, name: str = "angle mapping"
) -> list[list[int]]:
    """Validate and copy an n x 256 angle mapping matrix."""
    if not isinstance(mapping, list):
        raise ValidationError(f"{name} must be a list")
    if len(mapping) != joint_count:
        raise ValidationError(
            f"{name} must contain {joint_count} rows, got {len(mapping)}"
        )

    validated: list[list[int]] = []
    for row_index, row in enumerate(mapping):
        if not isinstance(row, list):
            raise ValidationError(f"{name} row {row_index} must be a list")
        if len(row) != _MAPPING_SIZE:
            raise ValidationError(
                f"{name} row {row_index} must contain {_MAPPING_SIZE} values, "
                f"got {len(row)}"
            )
        validated_row: list[int] = []
        for value_index, value in enumerate(row):
            if type(value) is not int:
                raise ValidationError(
                    f"{name} row {row_index} value {value_index} must be int"
                )
            if not 0 <= value <= 255:
                raise ValidationError(
                    f"{name} row {row_index} value {value_index} out of range [0, 255]"
                )
            validated_row.append(value)
        validated.append(validated_row)
    return validated


def validate_raw_values(raw_values: object, joint_count: int) -> list[int]:
    """Validate and copy a standard raw angle list."""
    if not isinstance(raw_values, list):
        raise ValidationError(
            f"raw_angles must be a list, got {type(raw_values).__name__}"
        )
    if len(raw_values) != joint_count:
        raise ValidationError(
            f"Expected {joint_count} raw angle values, got {len(raw_values)}"
        )
    validated: list[int] = []
    for index, value in enumerate(raw_values):
        if type(value) is not int:
            raise ValidationError(f"Raw angle value {index} must be int")
        if not 0 <= value <= 255:
            raise ValidationError(f"Raw angle value {index} out of range [0, 255]")
        validated.append(value)
    return validated
