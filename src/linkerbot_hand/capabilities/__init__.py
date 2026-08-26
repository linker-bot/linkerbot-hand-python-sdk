"""Model-specific capability declarations and lookup."""

from .registry import (
    MODEL_CAPABILITIES,
    get_model_capabilities,
    specialize_model_capabilities,
)

__all__ = [
    "MODEL_CAPABILITIES",
    "get_model_capabilities",
    "specialize_model_capabilities",
]
