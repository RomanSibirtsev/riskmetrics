"""Immutable data types used by the risk-calculation domain."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .isin import normalize_isin


ZERO = Decimal("0")
ONE = Decimal("1")
RUB = "RUB"
SYNTHETIC_SOURCE = "synthetic-demo"
CATALOG_VERSION = "demo-1"


class CouponType(str, Enum):
    FIXED = "fixed"
    FLOATING = "floating"


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> Decimal:
        return ONE if self is PositionSide.LONG else -ONE


@dataclass(frozen=True)
class Instrument:
    isin: str
    name: str
    coupon_type: CouponType
    currency: str
    duration_mod: Decimal | None = None
    convexity: Decimal | None = None
    payments_per_year: int | None = None
    source: str = SYNTHETIC_SOURCE
    catalog_version: str = CATALOG_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "isin", normalize_isin(self.isin))
        if not self.name.strip():
            raise ValueError("Название инструмента не может быть пустым.")
        if self.currency != RUB:
            raise ValueError("В демонстрационном каталоге поддерживается только RUB.")
        if self.source != SYNTHETIC_SOURCE:
            raise ValueError("Этап MVP поддерживает только синтетические данные.")

        if self.coupon_type is CouponType.FIXED:
            if self.duration_mod is None or self.convexity is None:
                raise ValueError(
                    "Для фиксированного купона нужны DurationMod и Convexity."
                )
            if self.duration_mod < ZERO or self.convexity < ZERO:
                raise ValueError("DurationMod и Convexity не могут быть отрицательными.")
            if self.payments_per_year is not None:
                if self.payments_per_year <= 0:
                    raise ValueError("Частота купонных выплат должна быть положительной.")
        elif self.coupon_type is CouponType.FLOATING:
            if self.payments_per_year is None or self.payments_per_year <= 0:
                raise ValueError(
                    "Для плавающего купона нужна положительная частота выплат."
                )
            if self.duration_mod is not None or self.convexity is not None:
                raise ValueError(
                    "Для плавающего купона DurationMod и Convexity вычисляются "
                    "по правилу задания и не задаются в каталоге."
                )
        else:
            raise ValueError("Неизвестный тип купона.")

    def risk_factors(self) -> tuple[Decimal, Decimal]:
        """Return the factors used by the assignment's DV01 formula."""
        if self.coupon_type is CouponType.FLOATING:
            assert self.payments_per_year is not None
            return ONE / Decimal(self.payments_per_year), ZERO
        assert self.duration_mod is not None and self.convexity is not None
        return self.duration_mod, self.convexity


@dataclass(frozen=True)
class TradeInput:
    isin: str
    position_market_value: Decimal
    side: PositionSide

    def __post_init__(self) -> None:
        object.__setattr__(self, "isin", normalize_isin(self.isin))
        if not isinstance(self.position_market_value, Decimal):
            raise TypeError("Стоимость позиции должна передаваться как Decimal.")
        if not self.position_market_value.is_finite():
            raise ValueError("Стоимость позиции должна быть конечным числом.")
        if self.position_market_value <= ZERO:
            raise ValueError("Стоимость позиции должна быть больше нуля.")
        if not isinstance(self.side, PositionSide):
            raise TypeError("Направление позиции должно быть long или short.")

    @property
    def signed_market_value(self) -> Decimal:
        return self.position_market_value * self.side.sign


@dataclass(frozen=True)
class ScenarioResult:
    shock_bps: int
    estimated_value_change: Decimal


@dataclass(frozen=True)
class TradeResult:
    isin: str
    instrument_name: str
    side: PositionSide
    currency: str
    position_market_value: Decimal
    signed_market_value: Decimal
    duration_mod: Decimal
    convexity: Decimal
    dv01: Decimal
    risk_share: Decimal
    scenarios: tuple[ScenarioResult, ...]
    source: str
    catalog_version: str


@dataclass(frozen=True)
class PortfolioResult:
    currency: str
    signed_market_value: Decimal
    dv01: Decimal
    trades: tuple[TradeResult, ...]
    scenarios: tuple[ScenarioResult, ...]
    source: str
    catalog_version: str
