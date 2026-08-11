class DomainLookupError(Exception):
    """Base exception for the stable domain lookup API."""


class InvalidDomainError(DomainLookupError, ValueError):
    """The supplied value cannot be normalized as a domain name."""
