from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langgraph.checkpoint.postgres import PostgresSaver
import psycopg
from psycopg import sql

from stockman.database import Database, DatabaseUnavailable, validate_url


def assert_text_not_persisted(test, database, text):
    """Inspect business rows and binary checkpoints in the actual PostgreSQL schema."""
    with database.read_only() as conn:
        tables=[row['tablename'] for row in conn.execute(
            'SELECT tablename FROM pg_tables WHERE schemaname=current_schema()')]
        test.assertIn('tasks',tables)
        test.assertIn('checkpoint_blobs',tables)
        count=conn.execute('SELECT count(*) AS total FROM checkpoint_blobs').fetchone()['total']
        test.assertGreater(count,0)
        for table in tables:
            query=sql.SQL('SELECT row_to_json(record)::text AS payload FROM {} AS record').format(sql.Identifier(table))
            for row in conn.execute(query):
                test.assertNotIn(text,row['payload'],table)
                test.assertNotIn(text.encode().hex(),row['payload'],table)


class PostgresStorageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Database(Path(self.directory.name))

    def test_initialization_is_idempotent_and_read_only_connections_cannot_write(self):
        with self.database.connect() as conn:
            self.database.initialize_schema(conn)
            self.database.initialize_schema(conn)
            conn.execute('CREATE TABLE probe(id integer primary key)')
            conn.execute('INSERT INTO probe VALUES (1)')
        with self.database.read_only() as conn:
            self.assertEqual(conn.execute('SELECT id FROM probe').fetchone(), {'id': 1})
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction), conn.transaction():
                conn.execute('INSERT INTO probe VALUES (2)')

    def test_business_store_preserves_timestamp_precision_and_stable_order(self):
        from stockman.storage import Store
        store = Store(self.directory.name, self.database)
        try:
            first = store.create_task('session', 'first')
            second = store.create_task('session', 'second')
            self.assertLess(store.task(first)['seq'], store.task(second)['seq'])
            stamp = 1794350000.125
            old = store.snapshot('snaptrade', 'owner', {'AAPL': {'shares': 1}}, stamp)
            new = store.snapshot('snaptrade', 'owner', {'AAPL': {'shares': 2}}, stamp)
            self.assertEqual(store.latest('snaptrade', 'owner')['id'], new)
            self.assertEqual(store.get_snapshot(old)['asof'], stamp)
        finally:
            store.close()

    def test_writer_lease_is_shared_across_connections_and_released_on_close(self):
        other = Database(Path(self.directory.name))
        with self.database.writer() as lease:
            with self.assertRaises(DatabaseUnavailable):
                other.writer()
            lease.check()
            lease.conn.close()
            with self.assertRaises(DatabaseUnavailable):
                lease.check()
        with other.writer() as lease:
            lease.check()

    def test_checkpoint_schema_supports_reconnect(self):
        with self.database.connect(prepare_threshold=0) as conn:
            self.database.initialize_schema(conn)
            PostgresSaver(conn).setup()
        with self.database.connect(prepare_threshold=0) as conn:
            saver = PostgresSaver(conn)
            saver.setup()
            self.assertEqual(list(saver.list(None)), [])

    def test_offline_config_never_uses_production_dsn(self):
        with patch.dict('os.environ', {'STOCKMAN_TEST_PG_SOCKET':'', 'STOCKMAN_DATABASE_URL':'postgresql://secret@prod.invalid/db'}):
            with self.assertRaisesRegex(DatabaseUnavailable, 'isolated PostgreSQL'):
                Database(self.directory.name)
        for dsn in ('postgresql://secret@abc-pooler.neon.tech/db', 'postgresql://x@host/db?sslmode=disable',
                    'postgresql://x@host/db?hostaddr=127.0.0.1'):
            with self.subTest(dsn=dsn), self.assertRaises(ValueError):
                validate_url(dsn)
