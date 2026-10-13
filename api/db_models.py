"""PostgreSQL records for immutable calculation history."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class CalculationRecord(Base):
    __tablename__ = "risk_calculations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    signed_market_value: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False
    )
    dv01: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(40), nullable=False)

    trades: Mapped[list["CalculationTradeRecord"]] = relationship(
        back_populates="calculation", cascade="all, delete-orphan", order_by="CalculationTradeRecord.line_number"
    )
    scenarios: Mapped[list["ScenarioRecord"]] = relationship(
        back_populates="calculation", cascade="all, delete-orphan"
    )


class CalculationTradeRecord(Base):
    __tablename__ = "calculation_trades"
    __table_args__ = (
        UniqueConstraint("calculation_id", "line_number", name="uq_trade_line_number"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    calculation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("risk_calculations.id", ondelete="CASCADE"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    isin: Mapped[str] = mapped_column(String(12), nullable=False)
    instrument_name: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(String(5), nullable=False)
    position_market_value: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False
    )
    signed_market_value: Mapped[Decimal] = mapped_column(
        Numeric(20, 2), nullable=False
    )
    duration_mod: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    convexity: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    dv01: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    risk_share: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(40), nullable=False)

    calculation: Mapped[CalculationRecord] = relationship(back_populates="trades")
    scenarios: Mapped[list["ScenarioRecord"]] = relationship(
        back_populates="trade", cascade="all, delete-orphan"
    )


class ScenarioRecord(Base):
    __tablename__ = "calculation_scenarios"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    calculation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("risk_calculations.id", ondelete="CASCADE"), nullable=False
    )
    trade_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("calculation_trades.id", ondelete="CASCADE"), nullable=True
    )
    shock_bps: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    estimated_value_change: Mapped[Decimal] = mapped_column(
        Numeric(24, 10), nullable=False
    )

    calculation: Mapped[CalculationRecord] = relationship(back_populates="scenarios")
    trade: Mapped[CalculationTradeRecord | None] = relationship(
        back_populates="scenarios"
    )


Index(
    "uq_trade_scenario_per_calculation",
    ScenarioRecord.calculation_id,
    ScenarioRecord.trade_id,
    ScenarioRecord.shock_bps,
    unique=True,
    postgresql_where=text("trade_id IS NOT NULL"),
)
Index(
    "uq_portfolio_scenario_per_calculation",
    ScenarioRecord.calculation_id,
    ScenarioRecord.shock_bps,
    unique=True,
    postgresql_where=text("trade_id IS NULL"),
)
