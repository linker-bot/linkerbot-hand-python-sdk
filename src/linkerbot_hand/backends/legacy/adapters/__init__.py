"""Explicit registry of adapters for legacy model driver families."""

from ....errors import ValidationError
from .base import LegacyModelAdapter
from .classic import ClassicAdapter
from .l30 import L30Adapter
from .o20 import O20Adapter
from .o30i import O30iAdapter

LEGACY_MODEL_ADAPTERS: dict[str, LegacyModelAdapter] = {
    adapter.model: adapter
    for adapter in (
        ClassicAdapter("L6", "linkerbot.hand.l6", "L6", ("l6",)),
        ClassicAdapter("O6", "linkerbot.hand.o6", "O6", ("o6",)),
        ClassicAdapter(
            "L20Lite",
            "linkerbot.hand.l20lite",
            "L20lite",
            ("l20lite", "l20"),
        ),
        ClassicAdapter("L25", "linkerbot.hand.l25", "L25", ("l25",)),
        L30Adapter(),
        O20Adapter(),
        O30iAdapter(),
    )
}


def get_legacy_model_adapter(model: str) -> LegacyModelAdapter:
    """Return the registered adapter for a canonical model name.

    Args:
        model: Canonical, case-sensitive model name.

    Returns:
        Adapter for the model's legacy driver family.

    Raises:
        ValidationError: If the model has no registered legacy adapter.
    """
    try:
        return LEGACY_MODEL_ADAPTERS[model]
    except KeyError as exc:
        raise ValidationError(f"unsupported legacy hand model {model!r}") from exc


__all__ = [
    "LEGACY_MODEL_ADAPTERS",
    "LegacyModelAdapter",
    "get_legacy_model_adapter",
]
