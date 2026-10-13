"""Read-only Finam Trade API quote stream for the bond risk MVP."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import os
import threading
from typing import Any

from sqlalchemy import select

from .database import SessionLocal
from .market_data_models import FinamLiveQuote, MoexInstrument


class FinamLiveFeed:
    """Owns the process-wide Finam quote stream and its health snapshot."""

    def __init__(self) -> None:
        self._secret = os.getenv("FINAM_SECRET", "").strip()
        self._stop = threading.Event()
        self._refresh_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._active_stream: Any | None = None
        self._state: dict[str, Any] = {
            "source": "Finam Trade API",
            "enabled": bool(self._secret),
            "connected": False,
            "last_update": None,
            "mapped_instruments": 0,
            "message": "Ожидается FINAM_SECRET." if not self._secret else "Поток ещё не запущен.",
        }

    def start(self) -> None:
        if not self._secret or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._run, name="finam-live-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def refresh_symbols(self) -> None:
        """Reconnect the feed so instruments added after startup are subscribed."""
        if not self._secret:
            return
        self._refresh_requested.set()
        with self._lock:
            stream = self._active_stream
        cancel = getattr(stream, "cancel", None)
        if callable(cancel):
            cancel()

    def _set_state(self, **changes: Any) -> None:
        with self._lock:
            self._state.update(changes)

    def _run(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                self._stream_once()
                if self._refresh_requested.is_set():
                    self._refresh_requested.clear()
                backoff = 2
            except Exception as exc:  # Feed failures must not stop the risk API.
                if self._refresh_requested.is_set():
                    self._refresh_requested.clear()
                    backoff = 2
                    continue
                self._set_state(
                    connected=False,
                    message=f"Поток недоступен ({type(exc).__name__}); повторное подключение.",
                )
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30)

    def _stream_once(self) -> None:
        from finam_trade_api import FinamClient
        from finam_trade_api.assets import AllAssetsRequest
        from finam_trade_api.market_data import QuoteRequest, SubscribeQuoteRequest

        with SessionLocal() as session:
            instruments = list(session.scalars(select(MoexInstrument)))
        if not instruments:
            self._set_state(connected=False, mapped_instruments=0, message="В базе нет облигаций для подписки.")
            self._stop.wait(15)
            return

        with FinamClient(secret=self._secret) as client:
            target_isins = {instrument.isin.upper() for instrument in instruments}
            by_isin: dict[str, tuple[int, str]] = {}
            cursor = 0
            # An ISIN can have several Finam listings. Scan the full catalogue
            # and prefer the MOEX listing over legacy RUSX aliases.
            while True:
                response = client.assets.AllAssets(
                    AllAssetsRequest(cursor=cursor, only_active=True)
                )
                for asset in getattr(response, "assets", []):
                    isin = str(getattr(asset, "isin", "")).strip().upper()
                    symbol = str(getattr(asset, "symbol", "")).strip()
                    mic = str(getattr(asset, "mic", "")).strip().upper()
                    priority = 1 if mic == "MISX" else 0
                    if isin in target_isins and symbol:
                        current = by_isin.get(isin)
                        if current is None or priority > current[0]:
                            by_isin[isin] = (priority, symbol)
                cursor = int(getattr(response, "next_cursor", 0) or 0)
                if not cursor:
                    break

            symbol_to_secid = {
                by_isin[instrument.isin.upper()][1]: instrument.secid
                for instrument in instruments
                if instrument.isin.upper() in by_isin
            }
            symbols = list(dict.fromkeys(symbol_to_secid))
            self._set_state(
                connected=False,
                mapped_instruments=len(symbols),
                message=("Инструменты сопоставлены; загружаем начальные котировки."
                         if symbols else "Finam не вернул облигации из текущего списка по ISIN."),
            )
            if not symbols:
                self._stop.wait(30)
                return

            # Take a REST snapshot before listening, so the app has a price immediately.
            for symbol in symbols:
                if self._stop.is_set():
                    return
                snapshot = client.market_data.LastQuote(QuoteRequest(symbol=symbol))
                quote = getattr(snapshot, "quote", None)
                if quote is not None:
                    self._persist_quote(symbol_to_secid[symbol], symbol, quote)

            stream = client.market_data.SubscribeQuote(SubscribeQuoteRequest(symbols=symbols))
            with self._lock:
                self._active_stream = stream
            try:
                for message in stream:
                    if self._stop.is_set() or self._refresh_requested.is_set():
                        return
                    quotes = getattr(message, "quote", [])
                    if getattr(message, "error", None) and getattr(message.error, "description", ""):
                        raise RuntimeError("Finam stream returned an error")
                    self._set_state(connected=True, message="Получаем live-котировки Finam.")
                    for quote in quotes:
                        symbol = str(getattr(quote, "symbol", ""))
                        secid = symbol_to_secid.get(symbol)
                        if secid:
                            self._persist_quote(secid, symbol, quote)
            finally:
                with self._lock:
                    self._active_stream = None
            if not self._stop.is_set():
                if self._refresh_requested.is_set():
                    return
                raise RuntimeError("Finam stream ended")

    def _persist_quote(self, secid: str, symbol: str, quote: Any) -> None:
        last = _decimal(getattr(quote, "last", None))
        if last is None or last <= 0:
            return
        bid = _decimal(getattr(quote, "bid", None))
        ask = _decimal(getattr(quote, "ask", None))
        volume = _decimal(getattr(quote, "volume", None))
        quote_at = _timestamp(getattr(quote, "timestamp", None))
        self._persist_values(secid, symbol, quote_at, last, bid, ask, volume, "Finam Trade API")

    def _persist_values(
        self,
        secid: str,
        symbol: str,
        quote_at: datetime,
        last: Decimal,
        bid: Decimal | None,
        ask: Decimal | None,
        volume: Decimal | None,
        source: str,
    ) -> None:
        with SessionLocal() as session:
            # Load the instrument before adding a new quote row. Otherwise the
            # following SELECT can autoflush an incomplete row with NULL fields.
            instrument = session.get(MoexInstrument, secid)
            row = session.get(FinamLiveQuote, secid)
            if row is not None and row.quote_at is not None:
                existing_at = row.quote_at
                if existing_at.tzinfo is None:
                    existing_at = existing_at.replace(tzinfo=timezone.utc)
                if existing_at > quote_at:
                    return
            if row is None:
                row = FinamLiveQuote(
                    secid=secid,
                    isin=instrument.isin if instrument else "",
                    symbol=symbol,
                    quote_at=quote_at,
                    last_price_pct=last,
                    bid_price_pct=bid,
                    ask_price_pct=ask,
                    volume=volume,
                    source=source,
                )
                session.add(row)
            else:
                row.isin = instrument.isin if instrument else ""
                row.symbol = symbol
                row.quote_at = quote_at
                row.last_price_pct = last
                row.bid_price_pct = bid
                row.ask_price_pct = ask
                row.volume = volume
                row.source = source
            session.commit()
        self._set_state(last_update=quote_at.isoformat())


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    try:
        number = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _timestamp(value: Any) -> datetime:
    if value is not None and hasattr(value, "seconds"):
        return datetime.fromtimestamp(
            int(value.seconds) + int(getattr(value, "nanos", 0)) / 1_000_000_000,
            tz=timezone.utc,
        )
    # Do not disguise a quote without a provider timestamp as a fresh live tick.
    return datetime.fromtimestamp(0, tz=timezone.utc)


finam_live_feed = FinamLiveFeed()
