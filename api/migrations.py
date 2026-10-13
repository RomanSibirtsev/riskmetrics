"""Small idempotent schema adjustments for databases created before auth."""

from sqlalchemy import text
from sqlalchemy.engine import Engine


def migrate_auth_ownership(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE risk_calculations "
                "ADD COLUMN IF NOT EXISTS user_id UUID NULL "
                "REFERENCES auth_users(id) ON DELETE CASCADE"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_risk_calculations_user_id "
                "ON risk_calculations (user_id)"
            )
        )


def migrate_market_quote_source(engine: Engine) -> None:
    """Mark whether the latest stored quote came from Finam or MOEX ISS."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE finam_live_quotes "
                "ADD COLUMN IF NOT EXISTS source VARCHAR(32) NOT NULL "
                "DEFAULT 'Finam Trade API'"
            )
        )
