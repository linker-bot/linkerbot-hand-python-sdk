"""Built-in Hand SDK 1.0 declared profile factories."""

from .l6 import declared_capabilities as l6_capabilities
from .l20 import declared_capabilities as l20_capabilities
from .l20lite import declared_capabilities as l20lite_capabilities
from .l30 import declared_capabilities as l30_capabilities
from .o6 import declared_capabilities as o6_capabilities
from .o20 import declared_capabilities as o20_capabilities
from .o30 import declared_capabilities as o30_capabilities

__all__ = [
    "l6_capabilities",
    "l20_capabilities",
    "l20lite_capabilities",
    "l30_capabilities",
    "o6_capabilities",
    "o20_capabilities",
    "o30_capabilities",
]
