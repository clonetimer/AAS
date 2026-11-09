"""Cooperative cancellation signal for staged source imports."""


class SourceImportCancelled(Exception):
    """Raised only before the target database transaction is committed."""
