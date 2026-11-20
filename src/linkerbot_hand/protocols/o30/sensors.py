"""Compatibility exports for the shared HOP sensors implementation."""

from ..hop.sensors import (
    SENSOR_NAMES,
    decode_tactile_fields,
    parse_tactile_descriptor,
    sensor_data_chunks,
    tactile_transaction,
)

__all__ = [
    "SENSOR_NAMES",
    "decode_tactile_fields",
    "parse_tactile_descriptor",
    "sensor_data_chunks",
    "tactile_transaction",
]
