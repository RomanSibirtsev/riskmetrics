"""Import a normalized MOEX dataset JSON into the local PostgreSQL database.

This is a fallback for Docker environments that cannot resolve external hosts;
the JSON file must be produced from MOEX ISS and retains each source payload.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from .database import Base, SessionLocal, engine
from .market_data_models import (
    MoexBondCashflow,
    MoexBondQuote,
    MoexGcurveParameter,
    MoexIngestionRun,
    MoexInstrument,
)


def import_dataset(path: Path) -> None:
    dataset = json.loads(path.read_text(encoding="utf-8-sig"))
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        quotes = dataset.get("bond_quotes", [])
        curves = dataset.get("gcurve_parameters", [])
        secids = dataset.get("securities", [])
        instruments = dataset.get("instruments", [])
        cashflows = dataset.get("cashflows", [])
        from_date = date.fromisoformat(dataset["from_date"])
        till_date = date.fromisoformat(dataset["till_date"])

        for row in quotes:
            values = dict(row)
            values["trade_date"] = date.fromisoformat(values["trade_date"])
            statement = insert(MoexBondQuote).values(**values)
            statement = statement.on_conflict_do_update(
                constraint="uq_moex_bond_quote",
                set_={key: value for key, value in values.items() if key not in {"secid", "board_id", "trade_date"}},
            )
            session.execute(statement)

        for row in curves:
            values = dict(row)
            values["trade_date"] = date.fromisoformat(values["trade_date"])
            statement = insert(MoexGcurveParameter).values(**values)
            statement = statement.on_conflict_do_update(
                constraint="uq_moex_gcurve_observation",
                set_={"raw_parameters": values["raw_parameters"], "source_url": values["source_url"]},
            )
            session.execute(statement)

        for row in instruments:
            values = dict(row)
            statement = insert(MoexInstrument).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[MoexInstrument.secid],
                set_={key: value for key, value in values.items() if key != "secid"},
            )
            session.execute(statement)

        for row in cashflows:
            values = dict(row)
            values["cashflow_date"] = date.fromisoformat(values["cashflow_date"])
            values["source_url"] = (
                "https://iss.moex.com/iss/securities/"
                f"{values['secid']}/bondization.json"
            )
            statement = insert(MoexBondCashflow).values(**values)
            statement = statement.on_conflict_do_update(
                constraint="uq_moex_bond_cashflow",
                set_={
                    "face_value": values["face_value"],
                    "source_payload": values["source_payload"],
                    "source_url": values["source_url"],
                },
            )
            session.execute(statement)

        session.add(
            MoexIngestionRun(
                status="completed",
                requested_from=from_date,
                requested_till=till_date,
                securities=secids,
                quote_rows=len(quotes),
                curve_rows=len(curves),
                completed_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m api.import_moex_dataset <dataset.json>")
    import_dataset(Path(sys.argv[1]))
