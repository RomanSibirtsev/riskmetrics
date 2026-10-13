"""MOEX market observations, kept separately from synthetic demo positions."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class MoexBondQuote(Base):
    __tablename__ = "moex_bond_quotes"
    __table_args__ = (
        UniqueConstraint("secid", "board_id", "trade_date", name="uq_moex_bond_quote"),
        Index("ix_moex_bond_quote_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    secid: Mapped[str] = mapped_column(String(32), nullable=False)
    board_id: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    close_price_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    yield_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    duration_days: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    accrued_interest: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    z_spread_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    z_spread_at_wap_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MoexGcurveParameter(Base):
    __tablename__ = "moex_gcurve_parameters"
    __table_args__ = (
        UniqueConstraint("trade_date", "trade_time", name="uq_moex_gcurve_observation"),
        Index("ix_moex_gcurve_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    trade_time: Mapped[str] = mapped_column(String(16), nullable=False, default="close")
    raw_parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MoexInstrument(Base):
    __tablename__ = "moex_instruments"

    secid: Mapped[str] = mapped_column(String(32), primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), nullable=False)
    short_name: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    face_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    face_unit: Mapped[str | None] = mapped_column(String(16))
    bond_type: Mapped[str | None] = mapped_column(String(128))
    coupon_details: Mapped[str | None] = mapped_column(String(128))
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MoexBondCashflow(Base):
    __tablename__ = "moex_bond_cashflows"
    __table_args__ = (
        UniqueConstraint(
            "secid", "cashflow_date", "coupon_amount", "principal_amount",
            name="uq_moex_bond_cashflow",
        ),
        Index("ix_moex_bond_cashflow_secid_date", "secid", "cashflow_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    secid: Mapped[str] = mapped_column(String(32), nullable=False)
    cashflow_date: Mapped[date] = mapped_column(Date, nullable=False)
    coupon_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=0)
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(24, 8), nullable=False, default=0)
    face_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    source_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)

class MoexIngestionRun(Base):
    __tablename__ = "moex_ingestion_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_from: Mapped[date] = mapped_column(Date, nullable=False)
    requested_till: Mapped[date] = mapped_column(Date, nullable=False)
    securities: Mapped[list] = mapped_column(JSON, nullable=False)
    quote_rows: Mapped[int] = mapped_column(default=0, nullable=False)
    curve_rows: Mapped[int] = mapped_column(default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class FinamLiveQuote(Base):
    """Latest available external market quote per instrument."""

    __tablename__ = "finam_live_quotes"
    __table_args__ = (Index("ix_finam_live_quote_time", "quote_at"),)

    secid: Mapped[str] = mapped_column(String(32), primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    quote_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_price_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    bid_price_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    ask_price_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="Finam Trade API")
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
