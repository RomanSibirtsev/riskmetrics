"""Persistence operations for immutable calculation history."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from risk_metrics.models import PortfolioResult

from .db_models import CalculationRecord, CalculationTradeRecord, ScenarioRecord


def save_calculation(
    session: Session, result: PortfolioResult, user_id: UUID
) -> CalculationRecord:
    record = CalculationRecord(
        user_id=user_id,
        currency=result.currency,
        signed_market_value=result.signed_market_value,
        dv01=result.dv01,
        source=result.source,
        catalog_version=result.catalog_version,
    )

    for line_number, trade in enumerate(result.trades, start=1):
        trade_record = CalculationTradeRecord(
            line_number=line_number,
            isin=trade.isin,
            instrument_name=trade.instrument_name,
            side=trade.side.value,
            position_market_value=trade.position_market_value,
            signed_market_value=trade.signed_market_value,
            duration_mod=trade.duration_mod,
            convexity=trade.convexity,
            dv01=trade.dv01,
            risk_share=trade.risk_share,
            currency=trade.currency,
            source=trade.source,
            catalog_version=trade.catalog_version,
        )
        for scenario in trade.scenarios:
            trade_record.scenarios.append(
                ScenarioRecord(
                    calculation=record,
                    trade=trade_record,
                    shock_bps=scenario.shock_bps,
                    estimated_value_change=scenario.estimated_value_change,
                )
            )
        record.trades.append(trade_record)

    for scenario in result.scenarios:
        record.scenarios.append(
            ScenarioRecord(
                calculation=record,
                shock_bps=scenario.shock_bps,
                estimated_value_change=scenario.estimated_value_change,
            )
        )

    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def list_calculations(
    session: Session, user_id: UUID, limit: int, offset: int
) -> tuple[list[CalculationRecord], int]:
    owned = select(CalculationRecord).where(CalculationRecord.user_id == user_id)
    total = session.scalar(
        select(func.count()).select_from(owned.subquery())
    ) or 0
    records = session.scalars(
        owned
        .options(selectinload(CalculationRecord.trades))
        .order_by(CalculationRecord.created_at.desc(), CalculationRecord.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(records), total


def get_calculation(
    session: Session, calculation_id: UUID, user_id: UUID
) -> CalculationRecord | None:
    return session.scalar(
        select(CalculationRecord)
        .where(
            CalculationRecord.id == calculation_id,
            CalculationRecord.user_id == user_id,
        )
        .options(
            selectinload(CalculationRecord.trades).selectinload(
                CalculationTradeRecord.scenarios
            ),
            selectinload(CalculationRecord.scenarios),
        )
    )
