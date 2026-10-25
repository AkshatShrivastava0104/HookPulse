
import os
import uuid

import pytest

from app.storage import PostgresStorage

from concurrent.futures import ThreadPoolExecutor


@pytest.fixture
def pg_storage():
    database_url = os.getenv("TEST_DATABASE_URL")

    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")

    storage = PostgresStorage(database_url)

    try:
        yield storage
    finally:
        storage.close()


def test_postgres_duplicate_message_is_idempotent(pg_storage):
    message_id = f"pytest-{uuid.uuid4()}"

    try:
        first_insert = pg_storage.insert_message(
            message_id,
            "+919876543210",
            "+14155550100",
            "2025-01-15T10:00:00Z",
            "PostgreSQL integration test",
        )

        second_insert = pg_storage.insert_message(
            message_id,
            "+919876543210",
            "+14155550100",
            "2025-01-15T10:00:00Z",
            "PostgreSQL integration test",
        )

        assert first_insert is True
        assert second_insert is False

        result = pg_storage.get_messages(limit=1000, q="PostgreSQL integration test")
        matches = [
            message
            for message in result["data"]
            if message["message_id"] == message_id
        ]

        assert len(matches) == 1

    finally:
        # Remove only this test's uniquely identified record.
        with pg_storage.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM messages WHERE message_id = %s",
                    (message_id,),
                )
            conn.commit()

def test_postgres_concurrent_duplicate_inserts(pg_storage):
    message_id = f"pytest-concurrent-{uuid.uuid4()}"

    def insert_message(_):
        return pg_storage.insert_message(
            message_id,
            "+919876543210",
            "+14155550100",
            "2025-01-15T10:00:00Z",
            "Concurrent PostgreSQL test",
        )

    try:
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(insert_message, range(10)))

        # Exactly one request should insert the message.
        assert sum(results) == 1

        # Verify the actual PostgreSQL record count.
        with pg_storage.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS total FROM messages WHERE message_id = %s",
                    (message_id,),
                )
                row = cur.fetchone()

        assert row["total"] == 1

    finally:
        with pg_storage.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM messages WHERE message_id = %s",
                    (message_id,),
                )
            conn.commit()
