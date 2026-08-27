"""Model-specific capability declarations and lookup."""

from .registry import (
    MODEL_CAPABILITIES,
    SUPPORTED_MODELS,
    get_declared_capabilities,
    get_model_capabilities,
    specialize_model_capabilities,
)
from .types import (
    CommandCapability,
    CommandSemantic,
    DeviceCapabilities,
    IdentityCapability,
    JointCapability,
    NumericRange,
    OperationCapabilities,
    PercentMapping,
    PhysicalAngleMapping,
    StallFieldCapability,
    StallProtectionCapability,
    TelemetryCapability,
    TelemetrySemantic,
    TransportCapability,
)

__all__ = [
    "MODEL_CAPABILITIES",
    "SUPPORTED_MODELS",
    "CommandCapability",
    "CommandSemantic",
    "DeviceCapabilities",
    "IdentityCapability",
    "JointCapability",
    "NumericRange",
    "OperationCapabilities",
    "PercentMapping",
    "PhysicalAngleMapping",
    "StallFieldCapability",
    "StallProtectionCapability",
    "TelemetryCapability",
    "TelemetrySemantic",
    "TransportCapability",
    "get_declared_capabilities",
    "get_model_capabilities",
    "specialize_model_capabilities",
]
