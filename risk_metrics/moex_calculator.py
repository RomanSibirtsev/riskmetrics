"""Full-revaluation bond risk metrics based on MOEX history and cash flows."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from math import ceil, exp, isfinite
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.market_data_models import (
    MoexBondCashflow,
    MoexBondQuote,
    MoexGcurveParameter,
    MoexInstrument,
)


KEY_RATE_TENORS = (0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0)
KEY_RATE_LABELS = ("3M", "6M", "9M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
BASIS_POINT = 0.0001


class MoexRiskDataError(ValueError):
    """Market-data and cash-flow prerequisites for a risk result are missing."""


def _number(value: object, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def moex_curve_rate(parameters: dict, tenor_years: float) -> float:
    """Return the MOEX G-curve continuously compounded rate as a decimal."""
    t = max(tenor_years, 1e-8)
    beta0 = _number(parameters.get("b1"))
    beta1 = _number(parameters.get("b2"))
    beta2 = _number(parameters.get("b3"))
    tau = _number(parameters.get("t1"))
    if None in (beta0, beta1, beta2, tau) or tau <= 0:
        raise MoexRiskDataError("В архиве отсутствуют параметры G-кривой MOEX.")

    decay = exp(-t / tau)
    level = (1 - decay) * tau / t
    result_bps = beta0 + beta1 * level + beta2 * (level - decay)

    # MOEX's nine correction terms use fixed Gaussian centers and widths.
    centers = [0.0, 0.6]
    for i in range(2, 9):
        centers.append(centers[-1] + 0.6 * (1.6 ** (i - 1)))
    widths = [0.6]
    for _ in range(1, 9):
        widths.append(widths[-1] * 1.6)
    for i, center in enumerate(centers):
        gamma = _number(parameters.get(f"g{i + 1}"), 0.0)
        result_bps += gamma * exp(-((t - center) / widths[i]) ** 2)
    return result_bps / 10_000


def _cashflows_for(cashflows: list[MoexBondCashflow], as_of: date, face_value: float) -> list[tuple[date, float]]:
    grouped: dict[date, float] = defaultdict(float)
    for row in cashflows:
        if row.cashflow_date <= as_of:
            continue
        face = _number(row.face_value, face_value) or face_value
        if face <= 0:
            continue
        amount = (_number(row.coupon_amount, 0.0) or 0.0) + (_number(row.principal_amount, 0.0) or 0.0)
        grouped[row.cashflow_date] += amount * 100.0 / face
    return sorted(grouped.items())


def _clean_price(
    cashflows: list[tuple[date, float]],
    as_of: date,
    curve: dict,
    spread_decimal: float,
    accrued_per_100: float = 0.0,
    curve_shift=None,
) -> float:
    pv = 0.0
    for flow_date, amount_per_100 in cashflows:
        tenor = max((flow_date - as_of).days / 365.25, 0.0)
        rate = moex_curve_rate(curve, tenor)
        if curve_shift is not None:
            rate += curve_shift(tenor)
        pv += amount_per_100 * exp(-(rate + spread_decimal) * tenor)
    return pv - accrued_per_100


def _hat_shock(node: int):
    left = KEY_RATE_TENORS[node - 1] if node > 0 else 0.0
    center = KEY_RATE_TENORS[node]
    right = KEY_RATE_TENORS[node + 1] if node + 1 < len(KEY_RATE_TENORS) else float("inf")

    def shock(tenor: float) -> float:
        if tenor < left or tenor > right:
            return 0.0
        if tenor <= center:
            return BASIS_POINT if center == left else BASIS_POINT * (tenor - left) / (center - left)
        if right == float("inf"):
            return BASIS_POINT
        return BASIS_POINT * (right - tenor) / (right - center)

    return shock


def _spread_decimal(quote: MoexBondQuote, curve: dict, is_ofz: bool, face: float) -> float:
    if is_ofz:
        return 0.0
    published = _number(quote.z_spread_pct)
    if published is not None:
        return published / 100.0
    # Older ISS history may not contain ZSPREAD. Approximate it with YTM less
    # the MOEX continuously-compounded spot rate at the quoted duration.
    ytm_pct = _number(quote.yield_pct)
    if ytm_pct is None:
        raise MoexRiskDataError(f"Нет доходности/спреда для {quote.secid} на {quote.trade_date}.")
    duration = _number(quote.duration_days)
    tenor = max(duration / 365.25, 0.01) if duration is not None else 1.0
    return ytm_pct / 100.0 - moex_curve_rate(curve, tenor)


def _implied_spread_from_price(
    quote: MoexBondQuote,
    curve: dict,
    flows: list[tuple[date, float]],
    face: float,
) -> float:
    """Solve the constant spread that reproduces MOEX's clean closing price."""
    target = _number(quote.close_price_pct)
    if target is None or not flows:
        return _spread_decimal(quote, curve, False, face)
    accrued = (_number(quote.accrued_interest, 0.0) or 0.0) * 100.0 / face
    low, high = -0.50, 1.00
    low_price = _clean_price(flows, quote.trade_date, curve, low, accrued)
    high_price = _clean_price(flows, quote.trade_date, curve, high, accrued)
    if not (low_price >= target >= high_price):
        return _spread_decimal(quote, curve, False, face)
    for _ in range(56):
        mid = (low + high) / 2
        price = _clean_price(flows, quote.trade_date, curve, mid, accrued)
        if price > target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _implied_spread_for_live_price(
    target_price: float,
    flows: list[tuple[date, float]],
    as_of: date,
    curve: dict,
    accrued_per_100: float,
    fallback_spread: float,
) -> float:
    """Infer a corporate spread that reprices cash flows to a live clean quote."""
    if target_price <= 0 or not flows:
        return fallback_spread
    low, high = -0.50, 1.00
    low_price = _clean_price(flows, as_of, curve, low, accrued_per_100)
    high_price = _clean_price(flows, as_of, curve, high, accrued_per_100)
    if not (low_price >= target_price >= high_price):
        return fallback_spread
    for _ in range(56):
        mid = (low + high) / 2
        if _clean_price(flows, as_of, curve, mid, accrued_per_100) > target_price:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def calculate_moex_portfolio(
    session: Session,
    trades: Iterable[dict],
    confidence: float = 0.99,
    lookback: int = 500,
    live_quotes: dict[str, dict] | None = None,
) -> dict:
    trade_inputs = tuple(trades)
    if not trade_inputs:
        raise MoexRiskDataError("Добавьте хотя бы одну позицию.")
    secids = list(dict.fromkeys(item["secid"] for item in trade_inputs))

    instruments = {
        row.secid: row
        for row in session.scalars(select(MoexInstrument).where(MoexInstrument.secid.in_(secids)))
    }
    if set(instruments) != set(secids):
        missing = sorted(set(secids) - set(instruments))
        raise MoexRiskDataError(f"Нет загруженных параметров MOEX для: {', '.join(missing)}.")

    quotes_by_secid: dict[str, list[MoexBondQuote]] = defaultdict(list)
    for quote in session.scalars(
        select(MoexBondQuote)
        .where(MoexBondQuote.secid.in_(secids))
        .order_by(MoexBondQuote.trade_date)
    ):
        if quote.close_price_pct is not None:
            quotes_by_secid[quote.secid].append(quote)
    if any(not quotes_by_secid[secid] for secid in secids):
        raise MoexRiskDataError("Для одной из позиций отсутствуют котировки MOEX.")

    curves = {
        row.trade_date: row.raw_parameters
        for row in session.scalars(select(MoexGcurveParameter).order_by(MoexGcurveParameter.trade_date))
    }
    cashflows_by_secid: dict[str, list[MoexBondCashflow]] = defaultdict(list)
    for flow in session.scalars(
        select(MoexBondCashflow)
        .where(MoexBondCashflow.secid.in_(secids))
        .order_by(MoexBondCashflow.cashflow_date)
    ):
        cashflows_by_secid[flow.secid].append(flow)

    latest_quotes = {secid: quotes_by_secid[secid][-1] for secid in secids}
    as_of = min(quote.trade_date for quote in latest_quotes.values())
    # Align valuation to the latest date with a quote for every requested bond.
    common_dates = sorted(
        set.intersection(*(set(q.trade_date for q in quotes_by_secid[secid]) for secid in secids))
    )
    if not common_dates:
        raise MoexRiskDataError("У позиций нет общей даты котировок.")
    as_of = common_dates[-1]
    if as_of not in curves:
        raise MoexRiskDataError(f"Для даты {as_of} отсутствует закрытие G-кривой MOEX.")
    current_curve = curves[as_of]
    quote_at_date = {
        secid: next(q for q in reversed(quotes_by_secid[secid]) if q.trade_date == as_of)
        for secid in secids
    }

    history: dict[str, dict[date, tuple[MoexBondQuote, float]]] = {}
    for secid in secids:
        history[secid] = {}
        is_ofz = secid.startswith("SU")
        face = _number(instruments[secid].face_value, 100.0) or 100.0
        issue_cashflows = cashflows_by_secid[secid]
        implied: dict[date, float] = {}
        for quote in quotes_by_secid[secid]:
            curve = curves.get(quote.trade_date)
            if curve is None:
                continue
            if not is_ofz:
                flows = _cashflows_for(issue_cashflows, quote.trade_date, face)
                try:
                    implied[quote.trade_date] = _implied_spread_from_price(quote, curve, flows, face)
                except MoexRiskDataError:
                    continue

        spread_offset = 0.0
        if not is_ofz:
            for quote in quotes_by_secid[secid]:
                if quote.z_spread_pct is not None and quote.trade_date in implied:
                    spread_offset = _number(quote.z_spread_pct) / 100.0 - implied[quote.trade_date]
                    break
        for quote in quotes_by_secid[secid]:
            if quote.trade_date not in curves:
                continue
            if is_ofz:
                spread = 0.0
            elif quote.z_spread_pct is not None:
                spread = _number(quote.z_spread_pct) / 100.0
            else:
                spread = implied.get(quote.trade_date, 0.0) + spread_offset
            history[secid][quote.trade_date] = (quote, spread)

    latest = {secid: history[secid].get(as_of) for secid in secids}
    if any(value is None for value in latest.values()):
        raise MoexRiskDataError("Не удалось собрать единый текущий срез котировок и кривой.")

    valuations: dict[str, dict] = {}
    result_trades = []
    total_signed_value = 0.0
    total_dv01 = 0.0
    total_cs01 = 0.0
    total_krd = [0.0] * len(KEY_RATE_TENORS)
    live_quote_times: list[datetime] = []
    live_used = False
    for trade in trade_inputs:
        secid = trade["secid"]
        quote, historical_spread = latest[secid]
        spread = historical_spread
        instrument = instruments[secid]
        face = _number(instrument.face_value, 100.0) or 100.0
        flows = _cashflows_for(cashflows_by_secid[secid], as_of, face)
        if not flows:
            raise MoexRiskDataError(f"Для {secid} нет будущих денежных потоков на {as_of}.")
        accrued = (_number(quote.accrued_interest, 0.0) or 0.0) * 100.0 / face
        live = (live_quotes or {}).get(secid)
        live_price = None
        live_at = None
        live_source = None
        if live:
            live_at = live.get("quote_at")
            if isinstance(live_at, datetime):
                if live_at.tzinfo is None:
                    live_at = live_at.replace(tzinfo=timezone.utc)
                candidate = _number(live.get("price_pct"))
                if candidate is not None and candidate > 0:
                    live_price = candidate
                    live_quote_times.append(live_at.astimezone(timezone.utc))
                    live_source = str(live.get("source") or "Finam Trade API")
                    live_used = True
        if live_price is not None and not secid.startswith("SU"):
            spread = _implied_spread_for_live_price(
                live_price, flows, as_of, current_curve, accrued, historical_spread
            )
        model_base_price = _clean_price(flows, as_of, current_curve, spread, accrued)
        archived_market_price = _number(quote.close_price_pct)
        base_price = live_price if live_price is not None else (archived_market_price or model_base_price)
        if base_price <= 0 or model_base_price <= 0:
            raise MoexRiskDataError(f"Расчётная цена {secid} получилась неположительной.")
        amount = float(trade["position_market_value"])
        sign = 1.0 if trade["side"] == "long" else -1.0
        signed_amount = amount * sign
        multiplier = signed_amount / base_price

        krd = []
        for index, (label, tenor) in enumerate(zip(KEY_RATE_LABELS, KEY_RATE_TENORS)):
            bumped_price = _clean_price(flows, as_of, current_curve, spread, accrued, _hat_shock(index))
            change = (bumped_price - model_base_price) * multiplier
            krd.append({"tenor": label, "years": tenor, "value_change_per_bp": change})
            total_krd[index] += change
        is_ofz = secid.startswith("SU")
        if is_ofz:
            cs01 = 0.0
        else:
            credit_bumped = _clean_price(flows, as_of, current_curve, spread + BASIS_POINT, accrued)
            cs01 = (credit_bumped - model_base_price) * multiplier
            total_cs01 += cs01

        total_dv01 += sum(item["value_change_per_bp"] for item in krd)
        total_signed_value += signed_amount
        valuations[secid] = {
            "flows": flows,
            "face": face,
            "spread": spread,
            "base_price": base_price,
            "model_base_price": model_base_price,
            "accrued": accrued,
            "multiplier": multiplier,
            "is_ofz": is_ofz,
        }
        result_trades.append({
            "secid": secid,
            "isin": instrument.isin,
            "short_name": instrument.short_name,
            "name": instrument.name,
            "side": trade["side"],
            "market_value": amount,
            "signed_market_value": signed_amount,
            "market_price_pct": live_price if live_price is not None else float(quote.close_price_pct),
            "price_source": f"{live_source} · последняя доступная котировка" if live_price is not None else "MOEX daily close fallback",
            "live_quote_at": None if live_price is None else live_at.isoformat(),
            "z_spread_pct": None if is_ofz else spread * 100,
            "z_spread_source": "not applicable to OFZ" if is_ofz else ("price-implied from Finam live quote and archived MOEX curve" if live_price is not None else ("MOEX ZSPREAD" if quote.z_spread_pct is not None else "price-implied spread from MOEX close and G-curve")),
            "krd_dv01": krd,
            "cs01_value_change_per_bp": cs01 if not is_ofz else None,
        })

    scenario_dates = common_dates[-(lookback + 1):]
    if len(scenario_dates) < 2:
        raise MoexRiskDataError("Недостаточно общих исторических дат для VaR/ES.")
    scenario_pnl: list[tuple[date, float]] = []
    for previous_day, scenario_day in zip(scenario_dates, scenario_dates[1:]):
        previous_curve, scenario_curve = curves.get(previous_day), curves.get(scenario_day)
        if previous_curve is None or scenario_curve is None:
            continue
        pnl = 0.0
        for secid, valuation in valuations.items():
            previous = history[secid].get(previous_day)
            current = history[secid].get(scenario_day)
            if previous is None or current is None:
                break
            previous_spread, current_spread = previous[1], current[1]
            spread_change = 0.0 if valuation["is_ofz"] else current_spread - previous_spread

            def historical_shift(tenor, prev=previous_curve, cur=scenario_curve):
                return moex_curve_rate(cur, tenor) - moex_curve_rate(prev, tenor)

            stressed_price = _clean_price(
                valuation["flows"], as_of, current_curve,
                valuation["spread"] + spread_change,
                valuation["accrued"], historical_shift,
            )
            pnl += (stressed_price - valuation["model_base_price"]) * valuation["multiplier"]
        else:
            scenario_pnl.append((scenario_day, pnl))
    if not scenario_pnl:
        raise MoexRiskDataError("Не удалось сформировать исторические сценарии переоценки.")

    losses = sorted((-pnl for _, pnl in scenario_pnl))
    quantile_index = min(len(losses) - 1, max(0, ceil(confidence * len(losses)) - 1))
    var = losses[quantile_index]
    tail_losses = losses[quantile_index:]
    expected_shortfall = sum(tail_losses) / len(tail_losses)
    live_as_of = min(live_quote_times).isoformat() if live_quote_times else None
    return {
        "source": "Last available quote + MOEX historical scenarios" if live_used else "MOEX historical dataset (live quote unavailable)",
        "as_of": live_as_of or as_of,
        "history_as_of": as_of,
        "curve_as_of": as_of,
        "live_prices_used": live_used,
        "confidence": confidence,
        "lookback_observations": len(scenario_pnl),
        "key_rate_tenors": list(KEY_RATE_LABELS),
        "signed_market_value": total_signed_value,
        "dv01_value_change_per_bp": total_dv01,
        "cs01_value_change_per_bp": total_cs01,
        "key_rate_dv01": [
            {"tenor": label, "years": tenor, "value_change_per_bp": value}
            for label, tenor, value in zip(KEY_RATE_LABELS, KEY_RATE_TENORS, total_krd)
        ],
        "var_1d": var,
        "expected_shortfall_1d": expected_shortfall,
        "trades": result_trades,
        "pnl_scenarios": [
            {"date": day, "pnl": pnl, "loss": -pnl} for day, pnl in scenario_pnl
        ],
        "methodology": {
            "var_es": "1-дневное историческое моделирование, 99% (настраиваемое), полная переоценка на G-кривой плюс кредитный спред.",
            "key_rate_dv01": "Полная переоценка после сдвига узла G-кривой на +1 б.п. с треугольной интерполяцией между узлами.",
            "cs01": "Полная переоценка после параллельного сдвига Z-spread на +1 б.п.; для ОФЗ неприменимо.",
            "historical_credit_spread": "Публикуемый MOEX ZSPREAD; если в старом архиве поле отсутствует — прокси YTM минус G-кривая, с калибровкой к первому опубликованному ZSPREAD.",
            "curve": "Закрывающие параметры G-кривой MOEX по модели Нельсона—Зигеля с поправочными членами MOEX.",
            "cashflows": "Купонные и амортизационные потоки MOEX bondization; переоценка в процентах от номинала.",
        },
    }
