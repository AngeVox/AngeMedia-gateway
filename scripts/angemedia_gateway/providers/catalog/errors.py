"""Provider catalog parsing errors."""
from __future__ import annotations


class CatalogValidationError(ValueError):
    """Raised when the static provider catalog is invalid."""
