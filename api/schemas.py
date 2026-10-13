"""Validation and response schemas for API v1."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


MONEY_PATTERN = re.compile(r"^(?:0|[1-9]\d{0,17})(?:\.\d{1,2})?$")


class TradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isin: str = Field(min_length=12, max_length=12)
    position_market_value: Decimal
    side: Literal["long", "short"]

    @field_validator("position_market_value", mode="before")
    @classmethod
    def parse_money_string(cls, value: object) -> Decimal:
        if not isinstance(value, str) or not MONEY_PATTERN.fullmatch(value):
            raise ValueError(
                "Введите стоимость строкой с точностью до копеек, например '1000000.00'."
            )
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("Некорректная сумма позиции.") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Стоимость позиции должна быть больше нуля.")
        return amount


class CalculationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trades: list[TradeRequest]


class AuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128)


class AuthUserResponse(BaseModel):
    id: UUID
    email: str


class MoexSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    securities: list[str] = Field(min_length=1, max_length=20)
    from_date: date
    till_date: date

    @field_validator("securities")
    @classmethod
    def validate_securities(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values]
        if any(not re.fullmatch(r"[A-Z0-9_-]{1,32}", value) for value in normalized):
            raise ValueError("SECID должен содержать латинские буквы, цифры, _ или -.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Список SECID не должен содержать повторы.")
        return normalized

    @field_validator("till_date")
    @classmethod
    def validate_date_order(cls, value: date, info) -> date:
        from_date = info.data.get("from_date")
        if from_date is not None and value < from_date:
            raise ValueError("till_date должна быть не ранее from_date.")
        if from_date is not None and (value - from_date).days > 3650:
            raise ValueError("Для одной загрузки доступен период не более 10 лет.")
        return value


class MoexRiskTradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secid: str = Field(min_length=1, max_length=32)
    position_market_value: Decimal
    side: Literal["long", "short"]

    @field_validator("secid")
    @classmethod
    def normalize_secid(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9_-]{1,32}", normalized):
            raise ValueError("SECID должен содержать латинские буквы, цифры, _ или -.")
        return normalized

    @field_validator("position_market_value", mode="before")
    @classmethod
    def validate_market_value(cls, value: object) -> Decimal:
        if not isinstance(value, str) or not MONEY_PATTERN.fullmatch(value):
            raise ValueError("Введите стоимость строкой с точностью до копеек, например '1000000.00'.")
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("Некорректная стоимость позиции.") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Стоимость позиции должна быть больше нуля.")
        return amount


class MoexRiskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trades: list[MoexRiskTradeRequest] = Field(min_length=1, max_length=20)
    confidence: Decimal = Decimal("0.99")
    lookback: int = Field(default=500, ge=20, le=500)

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: Decimal) -> Decimal:
        if value not in (Decimal("0.95"), Decimal("0.99")):
            raise ValueError("Поддерживается уровень доверия 95% или 99%.")
        return value

    @field_validator("trades")
    @classmethod
    def validate_unique_secids(cls, values: list[MoexRiskTradeRequest]) -> list[MoexRiskTradeRequest]:
        secids = [trade.secid for trade in values]
        if len(set(secids)) != len(secids):
            raise ValueError("Один выпуск можно добавить в портфель только один раз.")
        return values


class MoexAddBondRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secid: str = Field(min_length=1, max_length=32)
    isin: str = Field(min_length=12, max_length=12)

    @field_validator("secid")
    @classmethod
    def normalize_secid(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9_-]{1,32}", normalized):
            raise ValueError("Некорректный SECID MOEX.")
        return normalized

    @field_validator("isin")
    @classmethod
    def normalize_isin(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", normalized):
            raise ValueError("Введите корректный ISIN из 12 символов.")
        return normalized


class ScenarioResponse(BaseModel):
    shock_bps: int
    estimated_value_change: Decimal


class TradeResponse(BaseModel):
    isin: str
    instrument_name: str
    side: Literal["long", "short"]
    position_market_value: Decimal
    signed_market_value: Decimal
    duration_mod: Decimal
    convexity: Decimal
    dv01: Decimal
    risk_share: Decimal
    scenarios: list[ScenarioResponse]
    source: str
    catalog_version: str


class CalculationResponse(BaseModel):
    id: UUID
    created_at: datetime
    currency: str
    source: str
    catalog_version: str
    signed_market_value: Decimal
    dv01: Decimal
    trades: list[TradeResponse]
    scenarios: list[ScenarioResponse]


class CalculationListItem(BaseModel):
    id: UUID
    created_at: datetime
    currency: str
    signed_market_value: Decimal
    dv01: Decimal
    trade_count: int
    source: str
    catalog_version: str


class CalculationListResponse(BaseModel):
    items: list[CalculationListItem]
    limit: int
    offset: int
    total: int


class InstrumentResponse(BaseModel):
    isin: str
    name: str
    coupon_type: str
    currency: str
    duration_mod: Decimal | None
    convexity: Decimal | None
    payments_per_year: int | None


class InstrumentListResponse(BaseModel):
    source: str
    catalog_version: str
    items: list[InstrumentResponse]


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
