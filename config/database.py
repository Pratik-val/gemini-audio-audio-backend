# config/database.py
import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# Cloud SQL (PostgreSQL) configuration
DATABASE_URL = os.getenv("DATABASE_URL", "")
SCHEMA_SQL_PATH = os.getenv("DB_SCHEMA_SQL", "db/schema.sql")


def get_connection():
    """Return a psycopg2 connection with dict cursor, or None if DB unreachable."""
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set. Falling back to in-memory mode.")
        return None
    try:
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    except Exception as e:
        logger.warning(f"Could not connect to PostgreSQL ({e}). Falling back to in-memory mode.")
        return None


def init_db():
    """Create required tables if they do not exist. Safe to call on startup."""
    conn = get_connection()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            if os.path.exists(SCHEMA_SQL_PATH):
                with open(SCHEMA_SQL_PATH, "r", encoding="utf-8") as f:
                    cur.execute(f.read())
                conn.commit()
                logger.info("Database schema initialized.")
            else:
                logger.warning(f"Schema file not found at {SCHEMA_SQL_PATH}; skipping init.")
        conn.close()
    except Exception as e:
        logger.error(f"Failed to initialize database schema: {e}")