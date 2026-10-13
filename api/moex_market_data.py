"""MOEX ISS market-data ingestion for the educational MVP.

Historical observations are stored separately from the synthetic portfolio.
The adapter preserves the source fields and does not infer risk parameters.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ISS_ROOT = "https://iss.moex.com/iss"
PAGE_SIZE = 100


class MoexDataError(RuntimeError):
    """Raised when MOEX ISS cannot return or parse the requested data."""


def _get_json(path: str, params: dict[str, str | int]) -> dict[str, Any]:
    url = f"{ISS_ROOT}{path}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "risk-metrics-mvp/1.0"})
    delays = (0.4, 1.2, 2.5)
    for attempt in range(len(delays) + 1):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise MoexDataError("MOEX ISS вернул неожиданный формат JSON.")
            return payload
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise MoexDataError(
                    "MOEX ISS ограничил доступ к историческим данным. "
                    "Проверьте права доступа и условия выбранного тарифа."
                ) from exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == len(delays):
                raise MoexDataError(f"MOEX ISS вернул HTTP {exc.code}.") from exc
            time.sleep(delays[attempt])
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            if attempt == len(delays):
                raise MoexDataError(
                    f"Не удалось получить ответ MOEX ISS после {attempt + 1} попыток: {exc}"
                ) from exc
            time.sleep(delays[attempt])
    raise MoexDataError("MOEX ISS не ответил после повторных попыток.")


def _rows(payload: dict[str, Any], block_name: str) -> list[dict[str, Any]]:
    block = payload.get(block_name)
    if not isinstance(block, dict):
        return []
    columns, rows = block.get("columns"), block.get("data")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return []
    return [dict(zip(columns, row)) for row in rows]


def _decimal(row: dict[str, Any], *names: str) -> Decimal | None:
    for name in names:
        value = row.get(name)
        if value is None or value == "":
            continue
        try:
            result = Decimal(str(value))
        except InvalidOperation:
            continue
        if result.is_finite():
            return result
    return None


def search_bonds_by_isin(isin: str) -> list[dict[str, Any]]:
    """Search the official MOEX ISS catalogue and return exact ISIN matches."""
    payload = _get_json(
        "/securities.json",
        {"q": isin, "iss.meta": "off", "securities.limit": 50},
    )
    matches = []
    for row in _rows(payload, "securities"):
        row_isin = str(row.get("isin") or "").strip().upper()
        group = str(row.get("group") or "").strip().lower()
        if row_isin != isin or "bond" not in group:
            continue
        matches.append({
            "secid": str(row.get("secid") or "").strip().upper(),
            "isin": row_isin,
            "short_name": str(row.get("shortname") or row.get("name") or "").strip(),
            "name": str(row.get("name") or row.get("shortname") or "").strip(),
            "group": group,
            "is_traded": bool(row.get("is_traded")),
            "primary_boardid": str(row.get("primary_boardid") or ""),
        })
    return [row for row in matches if row["secid"]]


def fetch_current_bond_quote(secid: str) -> dict[str, Any] | None:
    """Fetch the latest available MOEX ISS market snapshot for a bond."""
    payload = _get_json(
        f"/engines/stock/markets/bonds/securities/{secid}.json",
        {"iss.meta": "off"},
    )
    rows = _rows(payload, "marketdata")
    if not rows:
        return None
    row = rows[0]
    price = _decimal(row, "LAST", "MARKETPRICE", "LEGALCLOSEPRICE", "CLOSE")
    if price is None or price <= 0:
        return None
    stamp = row.get("SYSTIME") or row.get("UPDATETIME")
    if not stamp:
        return None
    try:
        quote_at = datetime.strptime(str(stamp), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone(timedelta(hours=3))
        )
    except ValueError:
        return None
    return {
        "secid": secid,
        "price_pct": price,
        "bid_price_pct": _decimal(row, "BID"),
        "ask_price_pct": _decimal(row, "OFFER", "ASK"),
        "quote_at": quote_at,
    }


def fetch_bond_terms(secid: str, fallback_isin: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load MOEX bondization, normalize security fields, and build cash flows."""
    terms = _get_json(f"/securities/{secid}/bondization.json", {"coupons.limit": 1, "iss.meta": "off"})
    coupons: list[dict[str, Any]] = []
    start = 0
    while True:
        coupon_payload = _get_json(
            f"/securities/{secid}/bondization.json",
            {"coupons.start": start, "coupons.limit": 100, "iss.meta": "off"},
        )
        page = _rows(coupon_payload, "coupons")
        coupons.extend(page)
        if len(page) < 100:
            break
        start += len(page)

    amortizations = _rows(terms, "amortizations")
    offers = _rows(terms, "offers")
    unsupported_offers = [
        row for row in offers
        if row.get("offerdate")
        and str(row.get("offerdate")) > "0000-00-00"
        and "погаш" not in str(row.get("offertype") or "").casefold()
        and "maturity" not in str(row.get("offertype") or "").casefold()
    ]
    if unsupported_offers:
        raise MoexDataError(
            "Выпуск содержит оферту; для корректного расчёта пока поддерживаются облигации без оферты."
        )

    valid_coupons = [row for row in coupons if _valid_cashflow_date(row.get("coupondate"))]
    if not valid_coupons:
        raise MoexDataError("MOEX ISS не вернул купонный график для выбранного ISIN.")
    first = valid_coupons[0]
    metadata = {
        "secid": secid,
        "isin": str(first.get("isin") or fallback_isin).strip().upper(),
        "short_name": str(first.get("shortname") or first.get("short_name") or "").strip(),
        "name": str(first.get("name") or first.get("shortname") or "").strip(),
        "face_value": _decimal(first, "facevalue", "face_value"),
        "face_unit": str(first.get("faceunit") or "RUB"),
        "bond_type": str(first.get("bondtype") or ""),
        "coupon_details": str(first.get("coupondetails") or ""),
        "source_url": f"{ISS_ROOT}/securities/{secid}/bondization.json",
    }
    cashflows = []
    for row in valid_coupons:
        cashflows.append({
            "secid": secid,
            "cashflow_date": date.fromisoformat(str(row["coupondate"])),
            "coupon_amount": _decimal(row, "value_rub") or Decimal("0"),
            "principal_amount": Decimal("0"),
            "face_value": _decimal(row, "facevalue", "face_value") or metadata["face_value"],
            "source_payload": row,
            "source_url": metadata["source_url"],
        })
    for row in amortizations:
        if not _valid_cashflow_date(row.get("amortdate")):
            continue
        cashflows.append({
            "secid": secid,
            "cashflow_date": date.fromisoformat(str(row["amortdate"])),
            "coupon_amount": Decimal("0"),
            "principal_amount": _decimal(row, "value_rub") or Decimal("0"),
            "face_value": _decimal(row, "facevalue", "face_value") or metadata["face_value"],
            "source_payload": row,
            "source_url": metadata["source_url"],
        })
    if not cashflows:
        raise MoexDataError("MOEX ISS не вернул будущие денежные потоки для выбранного выпуска.")
    return metadata, cashflows


def _valid_cashflow_date(value: Any) -> bool:
    if not value:
        return False
    try:
        return date.fromisoformat(str(value)).year > 1
    except ValueError:
        return False


def fetch_bond_history(
    secid: str, from_date: date, till_date: date
) -> list[dict[str, Any]]:
    """Fetch daily bond trading history, paging through ISS results."""
    if from_date > till_date:
        raise ValueError("from_date должна быть не позднее till_date.")
    if not secid or len(secid) > 32:
        raise ValueError("Некорректный SECID.")

    result: list[dict[str, Any]] = []
    start = 0
    while True:
        payload = _get_json(
            f"/history/engines/stock/markets/bonds/securities/{secid}.json",
            {
                "from": from_date.isoformat(),
                "till": till_date.isoformat(),
                "start": start,
                "iss.meta": "off",
            },
        )
        page = _rows(payload, "history")
        for row in page:
            trade_date = row.get("TRADEDATE")
            if not trade_date:
                continue
            result.append(
                {
                    "secid": str(row.get("SECID") or secid),
                    "board_id": str(row.get("BOARDID") or ""),
                    "trade_date": date.fromisoformat(str(trade_date)),
                    "close_price_pct": _decimal(
                        row, "LEGALCLOSEPRICE", "CLOSE", "WAPRICE"
                    ),
                    "yield_pct": _decimal(row, "YIELDCLOSE", "YIELDATWAP"),
                    "duration_days": _decimal(row, "DURATION"),
                    "accrued_interest": _decimal(row, "ACCRUEDINT", "ACCINT"),
                    "z_spread_pct": _decimal(row, "ZSPREAD"),
                    "z_spread_at_wap_pct": _decimal(row, "ZSPREADATWAPRICE"),
                    "volume": _decimal(row, "VOLUME"),
                    "turnover": _decimal(row, "VALUE"),
                    "raw": row,
                }
            )
        if len(page) < PAGE_SIZE:
            break
        start += len(page)
    return result


def fetch_gcurve_history(trading_dates: list[date]) -> list[dict[str, Any]]:
    """Fetch end-of-day MOEX G-curve parameters for dates with bond trading.

    Raw parameters are retained because MOEX's published parametrization is
    needed to produce consistent tenor nodes in the calculation stage.
    The ISS history route ignores a from/till range here, so request one trading
    date and the final 18:54:59 observation at a time.
    """
    def fetch_date(day: date) -> list[dict[str, Any]]:
        payload = _get_json(
            "/history/engines/stock/zcyc.json",
            {
                "date": day.isoformat(),
                "time": "18:54:59",
                "iss.meta": "off",
            },
        )
        rows = _rows(payload, "params") or _rows(payload, "history")
        return [
            {
                "trade_date": date.fromisoformat(str(row.get("tradedate") or row.get("TRADEDATE"))),
                "trade_time": row.get("tradetime") or row.get("TRADETIME"),
                "parameters": row,
            }
            for row in rows
            if row.get("tradedate") or row.get("TRADEDATE")
        ]

    unique_dates = sorted(set(trading_dates))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_date, day) for day in unique_dates]
        for future in as_completed(futures):
            results.extend(future.result())
    return sorted(results, key=lambda row: (row["trade_date"], row["trade_time"] or ""))

