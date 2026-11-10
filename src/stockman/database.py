"""PostgreSQL connection policy and the single-writer lease."""
from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row


class DatabaseUnavailable(RuntimeError):
    """Safe database failure for CLI and service diagnostics."""


def validate_url(value):
    if not value.startswith(('postgresql://', 'postgres://')):
        raise ValueError('database URL must be a PostgreSQL URI')
    try:
        options = conninfo_to_dict(value)
    except psycopg.Error:
        raise ValueError('invalid PostgreSQL URL') from None
    host = options.get('host', '')
    if not host or host.startswith('/') or ',' in host or '-pooler.' in host:
        raise ValueError('database URL must use one direct PostgreSQL endpoint')
    if any(key in options for key in ('hostaddr', 'service', 'options')):
        raise ValueError('unsupported PostgreSQL connection option')
    if options.get('sslmode', 'verify-full') not in ('require', 'verify-ca', 'verify-full'):
        raise ValueError('database URL must require TLS')


class Database:
    def __init__(self, location, url=None):
        self.schema = 'stockman'
        self.url = ''
        self.options = {}
        if os.getenv('STOCKMAN_OFFLINE') == '1':
            host = os.getenv('STOCKMAN_TEST_PG_SOCKET', '')
            if not host or not Path(host).is_absolute() or not (Path(host) / '.s.PGSQL.5432').is_socket():
                raise DatabaseUnavailable('offline mode requires an isolated PostgreSQL test socket')
            key = str(Path(location).resolve()).encode()
            self.schema = 'stockman_test_' + sha256(key).hexdigest()[:24]
            self.options = {'host': host, 'port': 5432, 'dbname': 'postgres', 'user': 'stockman_test',
                            'sslmode': 'disable'}
        else:
            self.url = url if url is not None else os.getenv('STOCKMAN_DATABASE_URL', '')
            validate_url(self.url)
            self.options = {'sslmode': 'verify-full', 'sslrootcert': 'system'}

    def connect(self, *, prepare_threshold=5):
        try:
            return psycopg.connect(self.url, **self.options, connect_timeout=5,
                                   options=f'-csearch_path={self.schema}',
                                   autocommit=True, row_factory=dict_row,
                                   prepare_threshold=prepare_threshold)
        except (psycopg.Error, ValueError):
            raise DatabaseUnavailable('PostgreSQL connection failed') from None

    def initialize_schema(self, conn):
        with conn.transaction():
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (self.schema + ':schema',))
            conn.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(self.schema)))

    @contextmanager
    def read_only(self):
        with self.connect() as conn, conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            yield conn

    def writer(self):
        return DatabaseLease(self)


class DatabaseLease:
    def __init__(self, database):
        self.conn = database.connect()
        try:
            row = self.conn.execute('SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS acquired',
                                    (database.schema + ':writer',)).fetchone()
            if not row['acquired']:
                raise DatabaseUnavailable('another StockMan writer is using this database')
        except BaseException:
            self.conn.close()
            raise

    def check(self):
        try:
            self.conn.execute('SELECT 1')
        except psycopg.Error:
            raise DatabaseUnavailable('StockMan database writer lease was lost') from None

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
