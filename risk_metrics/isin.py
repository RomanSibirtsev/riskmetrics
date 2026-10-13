"""Basic ISIN structure and check-digit validation."""

from __future__ import annotations

import re


_ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class IsinValidationError(ValueError):
    """Raised when an identifier is not a well-formed ISIN."""


def is_valid_isin(value: str) -> bool:
    """Return whether *value* has ISIN syntax and a valid Luhn check digit."""
    if not isinstance(value, str):
        return False
    isin = value.strip().upper()
    if not _ISIN_PATTERN.fullmatch(isin):
        return False

    expanded = "".join(
        str(ord(char) - ord("A") + 10) if char.isalpha() else char
        for char in isin
    )
    checksum = 0
    for index, char in enumerate(reversed(expanded)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
        checksum += digit // 10 + digit % 10
    return checksum % 10 == 0


def normalize_isin(value: str) -> str:
    """Normalize an ISIN to uppercase or raise a useful validation error."""
    if not isinstance(value, str):
        raise IsinValidationError("ISIN должен быть строкой.")
    isin = value.strip().upper()
    if not _ISIN_PATTERN.fullmatch(isin):
        raise IsinValidationError(
            "ISIN должен содержать 12 символов: две буквы страны, "
            "девять букв или цифр и контрольную цифру."
        )
    if not is_valid_isin(isin):
        raise IsinValidationError("Контрольная цифра ISIN не совпадает.")
    return isin
