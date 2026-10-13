"""DV01, parallel-rate shock and risk-contribution calculations."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Iterable

from .catalog import SyntheticInstrumentCatalog
from .models import (
    PortfolioResult,
    RUB,
    SYNTHETIC_SOURCE,
    ScenarioResult,
    TradeInput,
    TradeResult,
    ZERO,
)


BASIS_POINT = Decimal("0.0001")
SCENARIO_SHOCKS_BPS = (-100, -50, -25, 25, 50, 100)


def estimate_value_change(
    signed_market_value: Decimal,
    duration_mod: Decimal,
    convexity: Decimal,
    shock_bps: int,
) -> Decimal:
    """Estimate value change for a parallel yield shock using duration/convexity."""
    delta_yield = Decimal(shock_bps) * BASIS_POINT
    return signed_market_value * (
        -duration_mod * delta_yield
        + Decimal("0.5") * convexity * delta_yield * delta_yield
    )


def calculate_trade(
    trade: TradeInput,
    catalog: SyntheticInstrumentCatalog,
) -> TradeResult:
    instrument = catalog.get(trade.isin)
    duration_mod, convexity = instrument.risk_factors()
    signed_value = trade.signed_market_value
    dv01 = estimate_value_change(signed_value, duration_mod, convexity, 1)
    scenarios = tuple(
        ScenarioResult(
            shock_bps=shock,
            estimated_value_change=estimate_value_change(
                signed_value, duration_mod, convexity, shock
            ),
        )
        for shock in SCENARIO_SHOCKS_BPS
    )
    return TradeResult(
        isin=instrument.isin,
        instrument_name=instrument.name,
        side=trade.side,
        currency=instrument.currency,
        position_market_value=trade.position_market_value,
        signed_market_value=signed_value,
        duration_mod=duration_mod,
        convexity=convexity,
        dv01=dv01,
        risk_share=ZERO,
        scenarios=scenarios,
        source=instrument.source,
        catalog_version=instrument.catalog_version,
    )


def calculate_portfolio(
    trades: Iterable[TradeInput],
    catalog: SyntheticInstrumentCatalog,
) -> PortfolioResult:
    trade_inputs = tuple(trades)
    if not trade_inputs:
        raise ValueError("Добавьте хотя бы одну сделку для расчёта.")

    results = tuple(calculate_trade(trade, catalog) for trade in trade_inputs)
    if any(result.currency != RUB for result in results):
        raise ValueError("В демонстрационном портфеле поддерживается только RUB.")

    gross_absolute_dv01 = sum((abs(item.dv01) for item in results), ZERO)
    if gross_absolute_dv01 == ZERO:
        shares = (ZERO,) * len(results)
    else:
        shares = tuple(abs(item.dv01) / gross_absolute_dv01 for item in results)
    results = tuple(
        replace(item, risk_share=share)
        for item, share in zip(results, shares, strict=True)
    )

    scenario_totals = tuple(
        ScenarioResult(
            shock_bps=shock,
            estimated_value_change=sum(
                (
                    next(
                        scenario.estimated_value_change
                        for scenario in item.scenarios
                        if scenario.shock_bps == shock
                    )
                    for item in results
                ),
                ZERO,
            ),
        )
        for shock in SCENARIO_SHOCKS_BPS
    )
    return PortfolioResult(
        currency=RUB,
        signed_market_value=sum((item.signed_market_value for item in results), ZERO),
        dv01=sum((item.dv01 for item in results), ZERO),
        trades=results,
        scenarios=scenario_totals,
        source=SYNTHETIC_SOURCE,
        catalog_version=catalog.version,
    )
