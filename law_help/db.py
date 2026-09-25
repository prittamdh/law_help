import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DEFAULT_URL = "postgresql://law:law@localhost:5432/law_help"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or database_url(), row_factory=dict_row)


def init_schema(conn: psycopg.Connection) -> None:
    conn.execute((Path(__file__).parent / "schema.sql").read_text())
    conn.commit()
