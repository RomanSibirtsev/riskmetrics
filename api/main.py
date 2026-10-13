"""FastAPI routes for risk calculations and persisted history."""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
import re
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from risk_metrics.calculator import calculate_portfolio
from risk_metrics.catalog import UnknownInstrumentError, build_demo_catalog
from risk_metrics.models import PositionSide, TradeInput

from . import db_models  # noqa: F401 - register tables on Base.metadata
from . import auth_models  # noqa: F401 - register auth tables on Base.metadata
from . import market_data_models  # noqa: F401 - register market-data tables
from .database import Base, engine, get_session
from .db_models import CalculationRecord
from .auth import (
    CurrentUser,
    SESSION_COOKIE,
    clear_session_cookie,
    enforce_same_origin,
    get_current_user,
    hash_token,
    normalize_email,
    password_hasher,
    set_session_cookie,
    verify_password,
)
from .auth_models import AuthSession, AuthUser
from .migrations import migrate_auth_ownership, migrate_market_quote_source
from .moex_ingestion import (
    add_moex_bond,
    market_data_status,
    moex_snapshot_feed,
    sync_moex_data,
)
from .finam_live import finam_live_feed
from .moex_market_data import MoexDataError, search_bonds_by_isin
from .market_data_models import FinamLiveQuote, MoexBondQuote, MoexInstrument
from risk_metrics.moex_calculator import MoexRiskDataError, calculate_moex_portfolio
from .repository import get_calculation, list_calculations, save_calculation
from .schemas import (
    CalculationListItem,
    CalculationListResponse,
    CalculationRequest,
    CalculationResponse,
    ErrorResponse,
    InstrumentListResponse,
    InstrumentResponse,
    MoexSyncRequest,
    MoexRiskRequest,
    ScenarioResponse,
    TradeResponse,
    AuthRequest,
    AuthUserResponse,
    MoexAddBondRequest,
)


catalog = build_demo_catalog()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_auth_ownership(engine)
    migrate_market_quote_source(engine)
    moex_snapshot_feed.start()
    finam_live_feed.start()
    try:
        yield
    finally:
        finam_live_feed.stop()
        moex_snapshot_feed.stop()


app = FastAPI(
    title="Risk Metrics MVP API",
    version="1.0.0",
    description=(
        "Расчёт процентного риска на синтетических инструментах и расширенных "
        "метрик полного переоценивания портфеля на исторических данных MOEX ISS."
    ),
    lifespan=lifespan,
)


@app.post("/api/v1/auth/register", response_model=AuthUserResponse, status_code=201)
def register(
    payload: AuthRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> dict:
    enforce_same_origin(request)
    try:
        email = normalize_email(payload.email)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "validation_error", "message": str(exc), "field": "email"},
        ) from exc
    if session.scalar(select(AuthUser.id).where(AuthUser.email == email)) is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "email_already_registered", "message": "Аккаунт с таким email уже существует."},
        )
    user = AuthUser(email=email, password_hash=password_hasher.hash(payload.password))
    session.add(user)
    try:
        session.flush()
        set_session_cookie(response, session, user)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "email_already_registered", "message": "Аккаунт с таким email уже существует."},
        ) from exc
    return {"id": user.id, "email": user.email}


@app.post("/api/v1/auth/login", response_model=AuthUserResponse)
def login(
    payload: AuthRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> dict:
    enforce_same_origin(request)
    try:
        email = normalize_email(payload.email)
    except ValueError:
        email = payload.email.strip().casefold()
    user = session.scalar(select(AuthUser).where(AuthUser.email == email))
    if user is None or not user.is_active or not verify_password(
        None if user is None else user.password_hash, payload.password
    ):
        raise HTTPException(
            status_code=401,
            detail={"code": "invalid_credentials", "message": "Неверный email или пароль."},
        )
    set_session_cookie(response, session, user)
    return {"id": user.id, "email": user.email}


@app.post("/api/v1/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> Response:
    enforce_same_origin(request)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        session.commit()
    clear_session_cookie(response)
    response.status_code = 204
    return response


@app.get("/api/v1/auth/me", response_model=AuthUserResponse)
def auth_me(user: CurrentUser) -> dict:
    return {"id": user.id, "email": user.email}


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    first_error = exc.errors()[0] if exc.errors() else {}
    location = first_error.get("loc", ())
    path: list[str] = []
    for part in location:
        if part == "body":
            continue
        if isinstance(part, int) and path:
            path[-1] += f"[{part}]"
        else:
            path.append(str(part))
    field = ".".join(path) or None
    message = str(first_error.get("msg", "Проверьте входные данные."))
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": message,
                "field": field,
            }
        },
    )


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        error = detail
    else:
        error = {
            "code": "http_error",
            "message": str(detail),
            "field": None,
        }
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error},
        headers=exc.headers,
    )


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readiness(session: Session = Depends(get_session)) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "PostgreSQL недоступен.",
            },
        ) from exc
    return {"status": "ready", "database": "ok"}


@app.get("/api/v1/market-data/moex")
def moex_market_data_status(session: Session = Depends(get_session)) -> dict:
    """Return counts and the result of the latest MOEX synchronization."""
    return market_data_status(session)


@app.get("/api/v1/market-data/moex/instruments")
def moex_instruments(session: Session = Depends(get_session)) -> dict:
    """List MOEX bond issues loaded with historical prices and cash flows."""
    items = []
    for instrument in session.scalars(select(MoexInstrument).order_by(MoexInstrument.short_name)):
        quote = session.scalar(
            select(MoexBondQuote)
            .where(MoexBondQuote.secid == instrument.secid)
            .order_by(MoexBondQuote.trade_date.desc())
            .limit(1)
        )
        live_quote = session.get(FinamLiveQuote, instrument.secid)
        items.append({
            "secid": instrument.secid,
            "isin": instrument.isin,
            "short_name": instrument.short_name,
            "name": instrument.name,
            "bond_type": instrument.bond_type,
            "face_value": instrument.face_value,
            "latest_trade_date": None if quote is None else quote.trade_date,
            "latest_price_pct": None if quote is None else quote.close_price_pct,
            "latest_z_spread_pct": None if quote is None else quote.z_spread_pct,
            "finam_symbol": None if live_quote is None else live_quote.symbol,
            "live_price_pct": None if live_quote is None else live_quote.last_price_pct,
            "live_quote_at": None if live_quote is None else live_quote.quote_at,
            "quote_source": None if live_quote is None else live_quote.source,
        })
    return {"source": "MOEX ISS", "items": items}


@app.get("/api/v1/market-data/moex/search")
def search_moex_bonds(
    user: CurrentUser,
    isin: str = Query(min_length=12, max_length=12),
    session: Session = Depends(get_session),
) -> dict:
    normalized_isin = isin.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", normalized_isin):
        raise HTTPException(status_code=422, detail="Введите корректный ISIN из 12 символов.")
    try:
        items = search_bonds_by_isin(normalized_isin)
    except MoexDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    for item in items:
        item["already_added"] = session.get(MoexInstrument, item["secid"]) is not None
    return {"source": "MOEX ISS", "items": items}


@app.post("/api/v1/market-data/moex/instruments", status_code=201)
def add_moex_instrument(
    request: MoexAddBondRequest,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> dict:
    try:
        candidates = search_bonds_by_isin(request.isin)
        candidate = next(
            (item for item in candidates if item["secid"] == request.secid), None
        )
        if candidate is None:
            raise HTTPException(
                status_code=404,
                detail="Выпуск с таким ISIN и SECID не найден в MOEX ISS.",
            )
        instrument = add_moex_bond(session, candidate)
        moex_snapshot_feed.refresh([instrument.secid])
        finam_live_feed.refresh_symbols()
    except MoexDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Не удалось сохранить данные облигации в PostgreSQL.",
        ) from exc
    latest_quote = session.scalar(
        select(MoexBondQuote)
        .where(MoexBondQuote.secid == instrument.secid)
        .order_by(MoexBondQuote.trade_date.desc())
        .limit(1)
    )
    return {
        "source": "MOEX ISS",
        "item": {
            "secid": instrument.secid,
            "isin": instrument.isin,
            "short_name": instrument.short_name,
            "name": instrument.name,
            "bond_type": instrument.bond_type,
            "face_value": instrument.face_value,
            "latest_trade_date": None if latest_quote is None else latest_quote.trade_date,
            "latest_price_pct": None if latest_quote is None else latest_quote.close_price_pct,
            "latest_z_spread_pct": None if latest_quote is None else latest_quote.z_spread_pct,
            "finam_symbol": None,
            "live_price_pct": None,
            "live_quote_at": None,
            "quote_source": None,
        },
    }


@app.get("/api/v1/market-data/live/status")
def live_market_data_status(user: CurrentUser) -> dict:
    """Expose feed health without returning the provider secret."""
    return finam_live_feed.status()


@app.get("/api/v1/market-data/live/quotes")
def live_market_data_quotes(
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> dict:
    items = []
    for quote in session.scalars(select(FinamLiveQuote).order_by(FinamLiveQuote.secid)):
        items.append({
            "secid": quote.secid,
            "isin": quote.isin,
            "symbol": quote.symbol,
            "last_price_pct": quote.last_price_pct,
            "bid_price_pct": quote.bid_price_pct,
            "ask_price_pct": quote.ask_price_pct,
            "quote_at": quote.quote_at,
            "source": quote.source,
        })
    return {"source": "Finam Trade API", "items": items}

@app.post("/api/v1/risk/moex")
def calculate_moex_risk(
    request: MoexRiskRequest,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> dict:
    """Calculate full-revaluation KRD, CS01, 1-day historical VaR and ES."""
    try:
        live_quotes = {
            quote.secid: {
                "price_pct": float(quote.last_price_pct),
                "quote_at": quote.quote_at,
                "symbol": quote.symbol,
                "source": quote.source,
            }
            for quote in session.scalars(
                select(FinamLiveQuote).where(
                    FinamLiveQuote.secid.in_([trade.secid for trade in request.trades])
                )
            )
        }
        return calculate_moex_portfolio(
            session,
            [
                {
                    "secid": trade.secid,
                    "position_market_value": trade.position_market_value,
                    "side": trade.side,
                }
                for trade in request.trades
            ],
            confidence=float(request.confidence),
            lookback=request.lookback,
            live_quotes=live_quotes,
        )
    except MoexRiskDataError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "moex_risk_data_unavailable", "message": str(exc), "field": None},
        ) from exc


@app.post("/api/v1/market-data/moex/sync", status_code=200)
def sync_moex_market_data(
    request: MoexSyncRequest,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> dict:
    """Load bond trading history and archived G-curve parameters from MOEX ISS."""
    try:
        run = sync_moex_data(
            session, request.securities, request.from_date, request.till_date
        )
    except MoexDataError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "moex_unavailable",
                "message": str(exc),
                "field": None,
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "Не удалось сохранить рыночные данные MOEX.",
                "field": None,
            },
        ) from exc
    return {
        "id": run.id,
        "source": "MOEX ISS",
        "status": run.status,
        "requested_from": run.requested_from,
        "requested_till": run.requested_till,
        "securities": run.securities,
        "bond_quote_rows": run.quote_rows,
        "gcurve_parameter_rows": run.curve_rows,
    }


@app.get(
    "/api/v1/instruments",
    response_model=InstrumentListResponse,
    responses={422: {"model": ErrorResponse}},
)
def instruments(q: str | None = Query(default=None, max_length=100)) -> dict:
    items = catalog.all()
    if q:
        query = q.casefold()
        items = tuple(
            item
            for item in items
            if query in item.isin.casefold() or query in item.name.casefold()
        )
    return {
        "source": "synthetic-demo",
        "catalog_version": catalog.version,
        "items": [
            InstrumentResponse(
                isin=item.isin,
                name=item.name,
                coupon_type=item.coupon_type.value,
                currency=item.currency,
                duration_mod=item.duration_mod,
                convexity=item.convexity,
                payments_per_year=item.payments_per_year,
            )
            for item in items
        ],
    }


@app.post(
    "/api/v1/calculations",
    response_model=CalculationResponse,
    status_code=201,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def create_calculation(
    request: CalculationRequest,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> dict:
    if not request.trades:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "empty_trades",
                "message": "Добавьте хотя бы одну сделку для расчёта.",
                "field": "trades",
            },
        )

    try:
        for index, item in enumerate(request.trades):
            try:
                catalog.get(item.isin)
            except UnknownInstrumentError as exc:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "unknown_instrument",
                        "message": str(exc),
                        "field": f"trades[{index}].isin",
                    },
                ) from exc
            except (ValueError, TypeError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "validation_error",
                        "message": str(exc),
                        "field": f"trades[{index}].isin",
                    },
                ) from exc
        trade_inputs = tuple(
            TradeInput(
                isin=item.isin,
                position_market_value=item.position_market_value,
                side=PositionSide(item.side),
            )
            for item in request.trades
        )
        result = calculate_portfolio(trade_inputs, catalog)
    except UnknownInstrumentError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "unknown_instrument",
                "message": str(exc),
                "field": "trades.isin",
            },
        ) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "validation_error",
                "message": str(exc),
                "field": None,
            },
        ) from exc

    try:
        record = save_calculation(session, result, user.id)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "Не удалось сохранить расчёт в PostgreSQL.",
                "field": None,
            },
        ) from exc
    return _calculation_response(record.id, record.created_at, result)


@app.get(
    "/api/v1/calculations",
    response_model=CalculationListResponse,
    responses={503: {"model": ErrorResponse}},
)
def calculations(
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    try:
        records, total = list_calculations(session, user.id, limit, offset)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "Не удалось прочитать историю расчётов.",
            },
        ) from exc
    return {
        "items": [
            CalculationListItem(
                id=record.id,
                created_at=record.created_at,
                currency=record.currency,
                signed_market_value=record.signed_market_value,
                dv01=record.dv01,
                trade_count=len(record.trades),
                source=record.source,
                catalog_version=record.catalog_version,
            )
            for record in records
        ],
        "limit": limit,
        "offset": offset,
        "total": total,
    }


@app.get(
    "/api/v1/calculations/{calculation_id}",
    response_model=CalculationResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def calculation_detail(
    calculation_id: UUID,
    user: CurrentUser,
    session: Session = Depends(get_session),
) -> dict:
    try:
        record = get_calculation(session, calculation_id, user.id)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "database_unavailable",
                "message": "Не удалось прочитать расчёт из PostgreSQL.",
            },
        ) from exc
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "calculation_not_found",
                "message": "Расчёт с таким идентификатором не найден.",
                "field": "id",
            },
        )
    return _record_response(record)


def _calculation_response(calculation_id, created_at, result) -> dict:
    return {
        "id": calculation_id,
        "created_at": created_at,
        "currency": result.currency,
        "source": result.source,
        "catalog_version": result.catalog_version,
        "signed_market_value": result.signed_market_value,
        "dv01": result.dv01,
        "trades": [
            {
                "isin": trade.isin,
                "instrument_name": trade.instrument_name,
                "side": trade.side.value,
                "position_market_value": trade.position_market_value,
                "signed_market_value": trade.signed_market_value,
                "duration_mod": trade.duration_mod,
                "convexity": trade.convexity,
                "dv01": trade.dv01,
                "risk_share": trade.risk_share,
                "scenarios": [
                    {
                        "shock_bps": scenario.shock_bps,
                        "estimated_value_change": scenario.estimated_value_change,
                    }
                    for scenario in trade.scenarios
                ],
                "source": trade.source,
                "catalog_version": trade.catalog_version,
            }
            for trade in result.trades
        ],
        "scenarios": [
            {
                "shock_bps": scenario.shock_bps,
                "estimated_value_change": scenario.estimated_value_change,
            }
            for scenario in result.scenarios
        ],
    }


def _record_response(record: CalculationRecord) -> dict:
    return {
        "id": record.id,
        "created_at": record.created_at,
        "currency": record.currency,
        "source": record.source,
        "catalog_version": record.catalog_version,
        "signed_market_value": record.signed_market_value,
        "dv01": record.dv01,
        "trades": [
            {
                "isin": trade.isin,
                "instrument_name": trade.instrument_name,
                "side": trade.side,
                "position_market_value": trade.position_market_value,
                "signed_market_value": trade.signed_market_value,
                "duration_mod": trade.duration_mod,
                "convexity": trade.convexity,
                "dv01": trade.dv01,
                "risk_share": trade.risk_share,
                "scenarios": [
                    {
                        "shock_bps": scenario.shock_bps,
                        "estimated_value_change": scenario.estimated_value_change,
                    }
                    for scenario in trade.scenarios
                ],
                "source": trade.source,
                "catalog_version": trade.catalog_version,
            }
            for trade in record.trades
        ],
        "scenarios": [
            {
                "shock_bps": scenario.shock_bps,
                "estimated_value_change": scenario.estimated_value_change,
            }
            for scenario in record.scenarios
        ],
    }


static_directory = Path(os.getenv("STATIC_DIR", Path(__file__).parent.parent / "ui" / "dist"))
if static_directory.is_dir():
    app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend")
