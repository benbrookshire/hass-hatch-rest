"""Errors raised by the Hatch API client."""


class HatchError(Exception):
    """Base error for the Hatch API client."""


class HatchAuthError(HatchError):
    """The Hatch account credentials were rejected."""


class HatchConnectionError(HatchError):
    """The Hatch cloud could not be reached, or returned an unexpected error."""
