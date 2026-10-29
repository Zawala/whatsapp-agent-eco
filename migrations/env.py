"""Alembic env: plain SQL migrations, URL from CARE_DATABASE_URL."""
import os

from alembic import context
from sqlalchemy import create_engine


def sqlalchemy_url(url: str) -> str:
    # The app uses a libpq URL (postgresql://); SQLAlchemy needs the driver named.
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


url = context.config.attributes.get("url") or os.environ["CARE_DATABASE_URL"]
with create_engine(sqlalchemy_url(url)).connect() as conn:
    context.configure(connection=conn)
    with context.begin_transaction():
        context.run_migrations()
