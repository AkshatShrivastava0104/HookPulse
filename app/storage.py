import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False
    ConnectionPool = None


class BaseStorage(ABC):
    @abstractmethod
    def init_db(self):
        pass

    @abstractmethod
    def insert_message(self, message_id: str, from_msisdn: str, to_msisdn: str,
                       ts: str, text: Optional[str]) -> bool:
        pass

    @abstractmethod
    def get_messages(self, limit: int = 50, offset: int = 0, from_filter: Optional[str] = None,
                     since: Optional[str] = None, q: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def is_healthy(self) -> bool:
        pass

    @abstractmethod
    def close(self):
        pass

    # Worker queue methods for async processing & retries
    @abstractmethod
    def enqueue_delivery(self, delivery_id: str, message_id: str, target_url: str) -> bool:
        pass

    @abstractmethod
    def fetch_pending_deliveries(self, limit: int = 10) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def update_delivery(self, delivery_id: str, status: str, attempts: int,
                        last_error: Optional[str] = None, next_retry_at: Optional[str] = None) -> bool:
        pass

    @abstractmethod
    def get_delivery_stats(self) -> Dict[str, Any]:
        pass


class SQLiteStorage(BaseStorage):
    def __init__(self, db_path: str):
        # Clean sqlite URI if provided
        if db_path.startswith("sqlite:///"):
            db_path = db_path.replace("sqlite:///", "")
        
        self.db_path = db_path
        self._lock = threading.Lock()
        self._mem_conn = None

        if self.db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        elif os.path.dirname(self.db_path):
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self.init_db()

    @contextmanager
    def get_connection(self):
        if self._mem_conn is not None:
            with self._lock:
                yield self._mem_conn
        else:
            conn = sqlite3.connect(
                self.db_path,
                timeout=10.0,
                check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            # Enable WAL mode and pragmas for performance & concurrency
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            try:
                yield conn
            finally:
                conn.close()

    def init_db(self):
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    from_msisdn TEXT NOT NULL,
                    to_msisdn TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    text TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_from_msisdn ON messages(from_msisdn)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ts ON messages(ts)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ts_message_id ON messages(ts ASC, message_id ASC)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    target_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER DEFAULT 0,
                    last_error TEXT,
                    next_retry_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_deliveries_status ON deliveries(status, next_retry_at)
            """)
            conn.commit()

    def insert_message(self, message_id: str, from_msisdn: str, to_msisdn: str,
                       ts: str, text: Optional[str]) -> bool:
        try:
            with self.get_connection() as conn:
                created_at = datetime.utcnow().isoformat() + 'Z'
                conn.execute("""
                    INSERT INTO messages (message_id, from_msisdn, to_msisdn, ts, text, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (message_id, from_msisdn, to_msisdn, ts, text, created_at))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False

    def get_messages(self, limit: int = 50, offset: int = 0, from_filter: Optional[str] = None,
                     since: Optional[str] = None, q: Optional[str] = None) -> Dict[str, Any]:
        with self.get_connection() as conn:
            where_clauses = []
            params = []

            if from_filter:
                where_clauses.append("from_msisdn = ?")
                params.append(from_filter)

            if since:
                where_clauses.append("ts >= ?")
                params.append(since)

            if q:
                where_clauses.append("text LIKE ?")
                params.append(f"%{q}%")

            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

            count_query = f"SELECT COUNT(*) as total FROM messages WHERE {where_sql}"
            total = conn.execute(count_query, params).fetchone()['total']

            data_query = f"""
                SELECT message_id, from_msisdn, to_msisdn, ts, text
                FROM messages
                WHERE {where_sql}
                ORDER BY ts ASC, message_id ASC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            rows = conn.execute(data_query, params).fetchall()

            messages = [{
                'message_id': row['message_id'],
                'from': row['from_msisdn'],
                'to': row['to_msisdn'],
                'ts': row['ts'],
                'text': row['text']
            } for row in rows]

            return {
                'data': messages,
                'total': total,
                'limit': limit,
                'offset': offset
            }

    def get_stats(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            total_query = "SELECT COUNT(*) as total FROM messages"
            total_messages = conn.execute(total_query).fetchone()['total']

            senders_query = "SELECT COUNT(DISTINCT from_msisdn) as count FROM messages"
            senders_count = conn.execute(senders_query).fetchone()['count']

            per_sender_query = """
                SELECT from_msisdn, COUNT(*) as count
                FROM messages
                GROUP BY from_msisdn
                ORDER BY count DESC
                LIMIT 10
            """
            per_sender = conn.execute(per_sender_query).fetchall()
            messages_per_sender = [{'from': row['from_msisdn'], 'count': row['count']}
                                   for row in per_sender]

            ts_query = "SELECT MIN(ts) as first_ts, MAX(ts) as last_ts FROM messages"
            ts_result = conn.execute(ts_query).fetchone()

            return {
                'total_messages': total_messages,
                'senders_count': senders_count,
                'messages_per_sender': messages_per_sender,
                'first_message_ts': ts_result['first_ts'],
                'last_message_ts': ts_result['last_ts']
            }

    def is_healthy(self) -> bool:
        try:
            with self.get_connection() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    def close(self):
        pass

    def enqueue_delivery(self, delivery_id: str, message_id: str, target_url: str) -> bool:
        try:
            with self.get_connection() as conn:
                now = datetime.utcnow().isoformat() + 'Z'
                conn.execute("""
                    INSERT INTO deliveries (id, message_id, target_url, status, attempts, created_at, updated_at)
                    VALUES (?, ?, ?, 'pending', 0, ?, ?)
                """, (delivery_id, message_id, target_url, now, now))
                conn.commit()
                return True
        except Exception:
            return False

    def fetch_pending_deliveries(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            now = datetime.utcnow().isoformat() + 'Z'
            rows = conn.execute("""
                SELECT d.id, d.message_id, d.target_url, d.attempts, d.status,
                       m.from_msisdn, m.to_msisdn, m.ts, m.text
                FROM deliveries d
                JOIN messages m ON d.message_id = m.message_id
                WHERE d.status IN ('pending', 'retry')
                  AND (d.next_retry_at IS NULL OR d.next_retry_at <= ?)
                ORDER BY d.created_at ASC
                LIMIT ?
            """, (now, limit)).fetchall()
            return [dict(r) for r in rows]

    def update_delivery(self, delivery_id: str, status: str, attempts: int,
                        last_error: Optional[str] = None, next_retry_at: Optional[str] = None) -> bool:
        with self.get_connection() as conn:
            now = datetime.utcnow().isoformat() + 'Z'
            conn.execute("""
                UPDATE deliveries
                SET status = ?, attempts = ?, last_error = ?, next_retry_at = ?, updated_at = ?
                WHERE id = ?
            """, (status, attempts, last_error, next_retry_at, now, delivery_id))
            conn.commit()
            return True

    def get_delivery_stats(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) as count FROM deliveries").fetchone()['count']
            pending = conn.execute("SELECT COUNT(*) as count FROM deliveries WHERE status = 'pending'").fetchone()['count']
            delivered = conn.execute("SELECT COUNT(*) as count FROM deliveries WHERE status = 'delivered'").fetchone()['count']
            retrying = conn.execute("SELECT COUNT(*) as count FROM deliveries WHERE status = 'retry'").fetchone()['count']
            failed = conn.execute("SELECT COUNT(*) as count FROM deliveries WHERE status = 'failed'").fetchone()['count']
            return {
                "total": total,
                "pending": pending,
                "delivered": delivered,
                "retrying": retrying,
                "failed": failed
            }


class PostgresStorage(BaseStorage):
    def __init__(self, db_url: str, min_size: int = 2, max_size: int = 20):
        if not HAS_POSTGRES:
            raise RuntimeError("psycopg and psycopg_pool must be installed to use PostgresStorage")
        
        self.db_url = db_url
        self.pool = ConnectionPool(
            conninfo=self.db_url,
            min_size=min_size,
            max_size=max_size,
            open=True,
            kwargs={"row_factory": dict_row}
        )
        self.init_db()

    @contextmanager
    def get_connection(self):
        with self.pool.connection() as conn:
            yield conn

    def init_db(self):
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        message_id VARCHAR(255) PRIMARY KEY,
                        from_msisdn VARCHAR(32) NOT NULL,
                        to_msisdn VARCHAR(32) NOT NULL,
                        ts VARCHAR(64) NOT NULL,
                        text TEXT,
                        created_at VARCHAR(64) NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_from_msisdn ON messages(from_msisdn)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ts ON messages(ts)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_ts_message_id ON messages(ts ASC, message_id ASC)
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS deliveries (
                        id VARCHAR(64) PRIMARY KEY,
                        message_id VARCHAR(255) NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
                        target_url TEXT NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        attempts INTEGER DEFAULT 0,
                        last_error TEXT,
                        next_retry_at VARCHAR(64),
                        created_at VARCHAR(64) NOT NULL,
                        updated_at VARCHAR(64) NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_deliveries_status ON deliveries(status, next_retry_at)
                """)
            conn.commit()

    def insert_message(self, message_id: str, from_msisdn: str, to_msisdn: str,
                       ts: str, text: Optional[str]) -> bool:
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    created_at = datetime.utcnow().isoformat() + 'Z'
                    cur.execute("""
                        INSERT INTO messages (message_id, from_msisdn, to_msisdn, ts, text, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (message_id) DO NOTHING
                        RETURNING message_id
                    """, (message_id, from_msisdn, to_msisdn, ts, text, created_at))
                    row = cur.fetchone()
                conn.commit()
                return row is not None
        except Exception:
            return False

    def get_messages(self, limit: int = 50, offset: int = 0, from_filter: Optional[str] = None,
                     since: Optional[str] = None, q: Optional[str] = None) -> Dict[str, Any]:
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                where_clauses = []
                params = []

                if from_filter:
                    where_clauses.append("from_msisdn = %s")
                    params.append(from_filter)

                if since:
                    where_clauses.append("ts >= %s")
                    params.append(since)

                if q:
                    where_clauses.append("text ILIKE %s")
                    params.append(f"%{q}%")

                where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

                count_query = f"SELECT COUNT(*) as total FROM messages WHERE {where_sql}"
                cur.execute(count_query, params)
                total = cur.fetchone()['total']

                data_query = f"""
                    SELECT message_id, from_msisdn, to_msisdn, ts, text
                    FROM messages
                    WHERE {where_sql}
                    ORDER BY ts ASC, message_id ASC
                    LIMIT %s OFFSET %s
                """
                query_params = list(params)
                query_params.extend([limit, offset])
                cur.execute(data_query, query_params)
                rows = cur.fetchall()

                messages = [{
                    'message_id': row['message_id'],
                    'from': row['from_msisdn'],
                    'to': row['to_msisdn'],
                    'ts': row['ts'],
                    'text': row['text']
                } for row in rows]

                return {
                    'data': messages,
                    'total': total,
                    'limit': limit,
                    'offset': offset
                }

    def get_stats(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                total_query = "SELECT COUNT(*) as total FROM messages"
                cur.execute(total_query)
                total_messages = cur.fetchone()['total']

                senders_query = "SELECT COUNT(DISTINCT from_msisdn) as count FROM messages"
                cur.execute(senders_query)
                senders_count = cur.fetchone()['count']

                per_sender_query = """
                    SELECT from_msisdn, COUNT(*) as count
                    FROM messages
                    GROUP BY from_msisdn
                    ORDER BY count DESC
                    LIMIT 10
                """
                cur.execute(per_sender_query)
                per_sender = cur.fetchall()
                messages_per_sender = [{'from': row['from_msisdn'], 'count': row['count']}
                                       for row in per_sender]

                ts_query = "SELECT MIN(ts) as first_ts, MAX(ts) as last_ts FROM messages"
                cur.execute(ts_query)
                ts_result = cur.fetchone()

                return {
                    'total_messages': total_messages,
                    'senders_count': senders_count,
                    'messages_per_sender': messages_per_sender,
                    'first_message_ts': ts_result['first_ts'],
                    'last_message_ts': ts_result['last_ts']
                }

    def is_healthy(self) -> bool:
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return True
        except Exception:
            return False

    def close(self):
        try:
            self.pool.close()
        except Exception:
            pass

    def enqueue_delivery(self, delivery_id: str, message_id: str, target_url: str) -> bool:
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    now = datetime.utcnow().isoformat() + 'Z'
                    cur.execute("""
                        INSERT INTO deliveries (id, message_id, target_url, status, attempts, created_at, updated_at)
                        VALUES (%s, %s, %s, 'pending', 0, %s, %s)
                    """, (delivery_id, message_id, target_url, now, now))
                conn.commit()
                return True
        except Exception:
            return False

    def fetch_pending_deliveries(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                now = datetime.utcnow().isoformat() + 'Z'
                cur.execute("""
                    SELECT d.id, d.message_id, d.target_url, d.attempts, d.status,
                           m.from_msisdn, m.to_msisdn, m.ts, m.text
                    FROM deliveries d
                    JOIN messages m ON d.message_id = m.message_id
                    WHERE d.status IN ('pending', 'retry')
                      AND (d.next_retry_at IS NULL OR d.next_retry_at <= %s)
                    ORDER BY d.created_at ASC
                    LIMIT %s
                """, (now, limit))
                rows = cur.fetchall()
                return [dict(r) for r in rows]

    def update_delivery(self, delivery_id: str, status: str, attempts: int,
                        last_error: Optional[str] = None, next_retry_at: Optional[str] = None) -> bool:
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                now = datetime.utcnow().isoformat() + 'Z'
                cur.execute("""
                    UPDATE deliveries
                    SET status = %s, attempts = %s, last_error = %s, next_retry_at = %s, updated_at = %s
                    WHERE id = %s
                """, (status, attempts, last_error, next_retry_at, now, delivery_id))
            conn.commit()
            return True

    def get_delivery_stats(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as count FROM deliveries")
                total = cur.fetchone()['count']
                cur.execute("SELECT COUNT(*) as count FROM deliveries WHERE status = 'pending'")
                pending = cur.fetchone()['count']
                cur.execute("SELECT COUNT(*) as count FROM deliveries WHERE status = 'delivered'")
                delivered = cur.fetchone()['count']
                cur.execute("SELECT COUNT(*) as count FROM deliveries WHERE status = 'retry'")
                retrying = cur.fetchone()['count']
                cur.execute("SELECT COUNT(*) as count FROM deliveries WHERE status = 'failed'")
                failed = cur.fetchone()['count']
                return {
                    "total": total,
                    "pending": pending,
                    "delivered": delivered,
                    "retrying": retrying,
                    "failed": failed
                }


class Database:
    """
    Unified database facade supporting both SQLite and PostgreSQL.
    Automatically detects database type from URL / path.
    """
    def __init__(self, db_url_or_path: str, **kwargs):
        self.db_url_or_path = db_url_or_path
        if db_url_or_path.startswith("postgresql://") or db_url_or_path.startswith("postgres://"):
            self._backend: BaseStorage = PostgresStorage(db_url_or_path, **kwargs)
        else:
            self._backend: BaseStorage = SQLiteStorage(db_url_or_path)

    def init_db(self):
        return self._backend.init_db()

    def insert_message(self, message_id: str, from_msisdn: str, to_msisdn: str,
                       ts: str, text: Optional[str]) -> bool:
        return self._backend.insert_message(message_id, from_msisdn, to_msisdn, ts, text)

    def get_messages(self, limit: int = 50, offset: int = 0, from_filter: Optional[str] = None,
                     since: Optional[str] = None, q: Optional[str] = None) -> Dict[str, Any]:
        return self._backend.get_messages(limit, offset, from_filter, since, q)

    def get_stats(self) -> Dict[str, Any]:
        return self._backend.get_stats()

    def is_healthy(self) -> bool:
        return self._backend.is_healthy()

    def close(self):
        return self._backend.close()

    def enqueue_delivery(self, delivery_id: str, message_id: str, target_url: str) -> bool:
        return self._backend.enqueue_delivery(delivery_id, message_id, target_url)

    def fetch_pending_deliveries(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self._backend.fetch_pending_deliveries(limit)

    def update_delivery(self, delivery_id: str, status: str, attempts: int,
                        last_error: Optional[str] = None, next_retry_at: Optional[str] = None) -> bool:
        return self._backend.update_delivery(delivery_id, status, attempts, last_error, next_retry_at)

    def get_delivery_stats(self) -> Dict[str, Any]:
        return self._backend.get_delivery_stats()
