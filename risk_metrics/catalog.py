"""Versioned synthetic instruments for the public demonstration MVP."""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from .isin import normalize_isin
from .models import CATALOG_VERSION, CouponType, Instrument, RUB


class UnknownInstrumentError(LookupError):
    """Raised when a valid ISIN is absent from the synthetic demo catalog."""


class SyntheticInstrumentCatalog:
    def __init__(self, instruments: Iterable[Instrument]) -> None:
        items = tuple(instruments)
        self._by_isin = {item.isin: item for item in items}
        if len(self._by_isin) != len(items):
            raise ValueError("В каталоге обнаружены повторяющиеся ISIN.")
        self.version = items[0].catalog_version if items else CATALOG_VERSION
        if any(item.catalog_version != self.version for item in items):
            raise ValueError("В каталоге смешаны версии набора данных.")

    def get(self, isin: str) -> Instrument:
        normalized = normalize_isin(isin)
        try:
            return self._by_isin[normalized]
        except KeyError as exc:
            raise UnknownInstrumentError(
                f"Инструмент {normalized} не найден в демонстрационном каталоге."
            ) from exc

    def all(self) -> tuple[Instrument, ...]:
        return tuple(self._by_isin[key] for key in sorted(self._by_isin))


def build_demo_catalog() -> SyntheticInstrumentCatalog:
    """Build a deterministic catalog; every value is invented for demo use."""
    return SyntheticInstrumentCatalog(
        (
            Instrument(
                isin="ZZ0000000016",
                name="Синтетическая облигация A",
                coupon_type=CouponType.FIXED,
                currency=RUB,
                duration_mod=Decimal("2.40"),
                convexity=Decimal("7.80"),
                payments_per_year=2,
            ),
            Instrument(
                isin="ZZ0000000024",
                name="Синтетическая облигация B",
                coupon_type=CouponType.FIXED,
                currency=RUB,
                duration_mod=Decimal("5.20"),
                convexity=Decimal("38.00"),
                payments_per_year=2,
            ),
            Instrument(
                isin="ZZ0000000032",
                name="Синтетическая облигация C",
                coupon_type=CouponType.FIXED,
                currency=RUB,
                duration_mod=Decimal("0.80"),
                convexity=Decimal("1.20"),
                payments_per_year=4,
            ),
            Instrument(
                isin="ZZ0000000040",
                name="Синтетическая облигация D",
                coupon_type=CouponType.FLOATING,
                currency=RUB,
                payments_per_year=4,
            ),
            Instrument(
                isin="ZZ0000000057",
                name="Синтетическая облигация E",
                coupon_type=CouponType.FLOATING,
                currency=RUB,
                payments_per_year=2,
            ),
            Instrument(
                isin="ZZ0000000065",
                name="Синтетическая облигация F",
                coupon_type=CouponType.FIXED,
                currency=RUB,
                duration_mod=Decimal("8.10"),
                convexity=Decimal("88.00"),
                payments_per_year=2,
            ),
        )
    )
