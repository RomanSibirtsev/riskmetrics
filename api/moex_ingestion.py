"""Synchronize MOEX ISS observations into PostgreSQL."""

from __future__ import annotations

from datetime import date, datetime, timezone
import threading
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .market_data_models import (
    MoexBondCashflow,
    MoexBondQuote,
    MoexGcurveParameter,
    MoexIngestionRun,
    MoexInstrument,
    FinamLiveQuote,
)
from .database import SessionLocal
from .moex_market_data import (
    MoexDataError,
    fetch_current_bond_quote,
    fetch_bond_history,
    fetch_bond_terms,
    fetch_gcurve_history,
)


def sync_moex_data(
    session: Session, secids: list[str], from_date: date, till_date: date
) -> MoexIngestionRun:
    run = MoexIngestionRun(
        status="running",
        requested_from=from_date,
        requested_till=till_date,
        securities=secids,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    try:
        quote_values: list[dict] = []
        trading_dates: set[date] = set()
        for secid in secids:
            endpoint = (
                "https://iss.moex.com/iss/history/engines/stock/markets/bonds/"
                f"securities/{secid}.json?{urlencode({'from': from_date.isoformat(), 'till': till_date.isoformat()})}"
            )
            for row in fetch_bond_history(secid, from_date, till_date):
                trading_dates.add(row["trade_date"])
                quote_values.append({
                    "secid": row["secid"],
                    "board_id": row["board_id"],
                    "trade_date": row["trade_date"],
                    "close_price_pct": row["close_price_pct"],
                    "yield_pct": row["yield_pct"],
                    "duration_days": row["duration_days"],
                    "accrued_interest": row["accrued_interest"],
                    "z_spread_pct": row["z_spread_pct"],
                    "z_spread_at_wap_pct": row["z_spread_at_wap_pct"],
                    "volume": row["volume"],
                    "turnover": row["turnover"],
                    "raw_payload": row["raw"],
                    "source_url": endpoint,
                })

        # A multi-year history can contain hundreds of observations. Send them
        # to PostgreSQL in chunks instead of issuing one INSERT per row.
        for offset in range(0, len(quote_values), 250):
            batch = quote_values[offset:offset + 250]
            statement = insert(MoexBondQuote).values(batch)
            statement = statement.on_conflict_do_update(
                constraint="uq_moex_bond_quote",
                set_={key: getattr(statement.excluded, key) for key in batch[0] if key not in {"secid", "board_id", "trade_date"}},
            )
            session.execute(statement)
        quote_count = len(quote_values)

        existing_curve_dates = set()
        if trading_dates:
            existing_curve_dates = set(session.scalars(
                select(MoexGcurveParameter.trade_date).where(
                    MoexGcurveParameter.trade_date.in_(trading_dates)
                )
            ))
        curve_rows = fetch_gcurve_history(sorted(trading_dates - existing_curve_dates))
        curve_values = []
        for row in curve_rows:
            trade_time = str(row["trade_time"] or "close")
            curve_endpoint = (
                "https://iss.moex.com/iss/history/engines/stock/zcyc.json?"
                f"{urlencode({'date': row['trade_date'].isoformat(), 'time': trade_time})}"
            )
            curve_values.append({
                "trade_date": row["trade_date"],
                "trade_time": trade_time,
                "raw_parameters": row["parameters"],
                "source_url": curve_endpoint,
            })
        for offset in range(0, len(curve_values), 250):
            batch = curve_values[offset:offset + 250]
            statement = insert(MoexGcurveParameter).values(batch)
            statement = statement.on_conflict_do_update(
                constraint="uq_moex_gcurve_observation",
                set_={"raw_parameters": statement.excluded.raw_parameters, "source_url": statement.excluded.source_url},
            )
            session.execute(statement)

        run.status = "completed"
        run.quote_rows = quote_count
        run.curve_rows = len(curve_rows)
        run.completed_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(run)
        return run
    except Exception as exc:
        session.rollback()
        failed_run = session.get(MoexIngestionRun, run.id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.error_message = str(exc)[:4000]
            failed_run.completed_at = datetime.now(timezone.utc)
            session.commit()
        raise


def add_moex_bond(session: Session, candidate: dict) -> MoexInstrument:
    """Load MOEX price history, curve, issue terms, and cash flows for a found bond."""
    secid = candidate["secid"]
    existing = session.get(MoexInstrument, secid)
    if existing is not None:
        return existing

    till_date = date.today()
    from_date = date(till_date.year - 3, 1, 1)
    run = sync_moex_data(session, [secid], from_date, till_date)
    if run.quote_rows < 21:
        raise MoexDataError(
            "Для выбранной облигации MOEX ISS вернул слишком мало исторических котировок для расчёта риска."
        )

    latest_quote = session.scalar(
        select(MoexBondQuote)
        .where(MoexBondQuote.secid == secid)
        .order_by(MoexBondQuote.trade_date.desc())
        .limit(1)
    )
    if latest_quote is None:
        raise MoexDataError("В MOEX ISS не найдена история торгов выбранной облигации.")

    terms, cashflows = fetch_bond_terms(secid, candidate["isin"])
    raw = latest_quote.raw_payload
    instrument = MoexInstrument(
        secid=secid,
        isin=terms["isin"] or candidate["isin"],
        short_name=str(raw.get("SHORTNAME") or candidate["short_name"])[:128],
        name=terms["name"] or candidate["name"],
        face_value=terms["face_value"] or raw.get("FACEVALUE"),
        face_unit=str(raw.get("FACEUNIT") or terms["face_unit"] or "RUB")[:16],
        bond_type=str(raw.get("BONDTYPE") or terms["bond_type"] or "")[:128],
        coupon_details=str(raw.get("COUPON_DETAILS") or terms["coupon_details"] or "")[:128],
        source_url=terms["source_url"],
    )
    session.add(instrument)
    session.flush()
    for flow in cashflows:
        statement = insert(MoexBondCashflow).values(**flow)
        statement = statement.on_conflict_do_update(
            constraint="uq_moex_bond_cashflow",
            set_={
                "face_value": flow["face_value"],
                "source_payload": flow["source_payload"],
                "source_url": flow["source_url"],
            },
        )
        session.execute(statement)
    session.commit()
    session.refresh(instrument)
    return instrument


def sync_moex_latest_quotes(session: Session, secids: list[str] | None = None) -> int:
    """Refresh cached last prices from MOEX without replacing newer provider ticks."""
    statement = select(MoexInstrument).order_by(MoexInstrument.secid)
    if secids:
        statement = statement.where(MoexInstrument.secid.in_(secids))
    updated = 0
    for instrument in session.scalars(statement):
        try:
            snapshot = fetch_current_bond_quote(instrument.secid)
        except MoexDataError:
            continue
        if snapshot is None:
            continue
        row = session.get(FinamLiveQuote, instrument.secid)
        if row is not None:
            current_at = row.quote_at
            if current_at.tzinfo is None:
                current_at = current_at.replace(tzinfo=timezone.utc)
            if current_at >= snapshot["quote_at"]:
                continue
        if row is None:
            row = FinamLiveQuote(
                secid=instrument.secid,
                isin=instrument.isin,
                symbol=instrument.secid,
                quote_at=snapshot["quote_at"],
                last_price_pct=snapshot["price_pct"],
                bid_price_pct=snapshot["bid_price_pct"],
                ask_price_pct=snapshot["ask_price_pct"],
                volume=None,
                source="MOEX ISS",
            )
            session.add(row)
        else:
            row.isin = instrument.isin
            row.symbol = instrument.secid
            row.quote_at = snapshot["quote_at"]
            row.last_price_pct = snapshot["price_pct"]
            row.bid_price_pct = snapshot["bid_price_pct"]
            row.ask_price_pct = snapshot["ask_price_pct"]
            row.volume = None
            row.source = "MOEX ISS"
        updated += 1
    if updated:
        session.commit()
    else:
        session.rollback()
    return updated


class MoexSnapshotFeed:
    """Keep a last available MOEX price cached as a fallback to Finam ticks."""

    def __init__(self, interval_seconds: int = 60) -> None:
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="moex-snapshot-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def refresh(self, secids: list[str] | None = None) -> int:
        with SessionLocal() as session:
            return sync_moex_latest_quotes(session, secids)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                # A missing MOEX snapshot must not stop user calculations.
                pass
            self._stop.wait(self._interval)


moex_snapshot_feed = MoexSnapshotFeed()


def market_data_status(session: Session) -> dict:
    quote_count = session.scalar(select(func.count()).select_from(MoexBondQuote)) or 0
    curve_count = session.scalar(select(func.count()).select_from(MoexGcurveParameter)) or 0
    instrument_count = session.scalar(select(func.count()).select_from(MoexInstrument)) or 0
    cashflow_count = session.scalar(select(func.count()).select_from(MoexBondCashflow)) or 0
    latest_run = session.scalar(
        select(MoexIngestionRun).order_by(MoexIngestionRun.started_at.desc()).limit(1)
    )
    return {
        "source": "MOEX ISS",
        "bond_quote_rows": quote_count,
        "gcurve_parameter_rows": curve_count,
        "instrument_count": instrument_count,
        "cashflow_rows": cashflow_count,
        "last_sync": None if latest_run is None else {
            "id": latest_run.id,
            "status": latest_run.status,
            "started_at": latest_run.started_at,
            "completed_at": latest_run.completed_at,
            "requested_from": latest_run.requested_from,
            "requested_till": latest_run.requested_till,
            "securities": latest_run.securities,
            "quote_rows": latest_run.quote_rows,
            "curve_rows": latest_run.curve_rows,
            "error_message": latest_run.error_message,
        },
    }
