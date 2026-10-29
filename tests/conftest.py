"""Postgres for the state and API tests.

Set CARE_TEST_DATABASE_URL to a database the tests may wipe, e.g. the one
compose.yaml creates: postgresql://care:care@localhost:5433/care_test.
Tests that need it are skipped without it. Never point it at a database with
data you want to keep.
"""
import os

import pytest

TEST_DB = os.environ.get("CARE_TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def _migrated():
    if not TEST_DB:
        pytest.skip("set CARE_TEST_DATABASE_URL to run the Postgres tests")
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.attributes["url"] = TEST_DB
    command.upgrade(cfg, "head")
    return TEST_DB


@pytest.fixture
def db_url(_migrated):
    """A migrated, empty database."""
    import psycopg

    with psycopg.connect(_migrated) as conn:
        conn.execute("TRUNCATE conversations, messages")
    return _migrated
