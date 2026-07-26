import sqlite3

import config


def connect(db_path=config.DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: Streamlit's st.cache_resource can hand this
    # connection to a rerun executing on a different thread than the one
    # that created it. Ingestion scripts are single-threaded, so this is
    # safe there too -- we just never share one connection across
    # concurrent writers.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_main_db(conn=None):
    """Applies schema.sql + views.sql to data/tracker.db (idempotent)."""
    own_conn = conn is None
    conn = conn or connect(config.DB_PATH)
    conn.executescript(config.SCHEMA_SQL_PATH.read_text())
    if config.VIEWS_SQL_PATH.exists():
        conn.executescript(config.VIEWS_SQL_PATH.read_text())
    conn.commit()
    if own_conn:
        return conn


def init_cache_db(conn=None):
    """Applies sql/cache_schema.sql to .cache/fec_raw.db (idempotent)."""
    own_conn = conn is None
    conn = conn or connect(config.CACHE_DB_PATH)
    cache_schema = config.ROOT / "sql" / "cache_schema.sql"
    conn.executescript(cache_schema.read_text())
    conn.commit()
    if own_conn:
        return conn


def query(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()
