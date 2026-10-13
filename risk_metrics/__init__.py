"""Calculation domain for the hypothetical bond risk-metrics MVP."""

from .calculator import calculate_portfolio, calculate_trade
from .catalog import SyntheticInstrumentCatalog, build_demo_catalog
from .models import (
    CouponType,
    Instrument,
    PortfolioResult,
    PositionSide,
    TradeInput,
)

__all__ = [
    "CouponType",
    "Instrument",
    "PortfolioResult",
    "PositionSide",
    "SyntheticInstrumentCatalog",
    "TradeInput",
    "build_demo_catalog",
    "calculate_portfolio",
    "calculate_trade",
]
