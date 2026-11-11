from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from alpha_market_engine.database.base import Base
from alpha_market_engine.database import models  # noqa: F401 - register tables


def test_migration_matches_sqlalchemy_metadata(mysql_test_engine):
    with mysql_test_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diffs = compare_metadata(context, Base.metadata)

    assert diffs == []
