"""Persistent attendance. Fishing public.users is never modified here."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import psycopg2


@contextmanager
def database():
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE_URL is required; refusing local fallback')
    conn = psycopg2.connect(url, connect_timeout=15)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '20s'")
                yield cur
    finally:
        conn.close()


def initialize(default_path):
    # Separate schema avoids altering any existing Supabase tables.
    with database() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(736192040)')
        cur.execute('CREATE SCHEMA IF NOT EXISTS bot_attendance')
        cur.execute('''CREATE TABLE IF NOT EXISTS bot_attendance.users (
            user_id BIGINT PRIMARY KEY, last_check TEXT,
            count INTEGER NOT NULL DEFAULT 0,
            sol_erda_pieces INTEGER NOT NULL DEFAULT 0,
            last_voice_at TEXT, voice_day TEXT,
            voice_seconds INTEGER NOT NULL DEFAULT 0,
            last_tick TIMESTAMPTZ)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS bot_attendance.settings (
            guild_id BIGINT PRIMARY KEY, channel_id BIGINT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS bot_attendance.migrations (
            name TEXT PRIMARY KEY, completed_at TIMESTAMPTZ DEFAULT now())''')
        explicit = os.environ.get('LEGACY_SQLITE_PATH')
        path = Path(explicit or default_path)
        if explicit and not path.is_file():
            raise RuntimeError('LEGACY_SQLITE_PATH does not exist; migration stopped')
        cur.execute("SELECT 1 FROM bot_attendance.migrations WHERE name = 'sqlite-v1'")
        done = cur.fetchone()
        if path.is_file() and not done:
            migrate_sqlite(cur, path)
        elif not path.is_file() and not done:
            raise RuntimeError('Legacy SQLite file missing. Refusing to start with empty attendance. Set LEGACY_SQLITE_PATH to the backed-up database and import it first.')
    print('Supabase attendance storage ready; fishing users unchanged.', flush=True)


def migrate_sqlite(cur, path):
    # Read-only: never create, edit or delete the source database.
    source = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, key, columns in (
            ('attendance_users', 'user_id', ('user_id', 'last_check', 'count', 'sol_erda_pieces', 'last_voice_at')),
            ('settings', 'guild_id', ('guild_id', 'channel_id')),
        ):
            if table not in tables:
                continue
            dest = 'users' if table == 'attendance_users' else 'settings'
            for row in source.execute('SELECT * FROM ' + table):
                data = dict(row)
                values = [data.get(c) for c in columns]
                if dest == 'users':
                    values[2] = values[2] or 0
                    values[3] = values[3] or 0
                # Conflicting records require reconciliation, never silent overwrite.
                cur.execute('SELECT ' + ','.join(columns) + ' FROM bot_attendance.' + dest + ' WHERE ' + key + '=%s', (data[key],))
                existing = cur.fetchone()
                if existing is not None:
                    if tuple(existing) != tuple(values):
                        raise RuntimeError('Legacy migration conflict in ' + table + '; existing data preserved. Reconcile before migration.')
                    continue
                cur.execute('INSERT INTO bot_attendance.' + dest + ' (' + ','.join(columns) + ') VALUES (' + ','.join(['%s'] * len(columns)) + ')', values)
        cur.execute("INSERT INTO bot_attendance.migrations(name) VALUES ('sqlite-v1')")
    finally:
        source.close()
    print('Legacy SQLite import completed (source file preserved).', flush=True)


def read_user(user_id):
    with database() as cur:
        cur.execute('SELECT count, sol_erda_pieces FROM bot_attendance.users WHERE user_id=%s', (user_id,))
        return cur.fetchone() or (0, 0)


def set_channel(guild_id, channel_id):
    with database() as cur:
        cur.execute('''INSERT INTO bot_attendance.settings VALUES (%s,%s)
            ON CONFLICT(guild_id) DO UPDATE SET channel_id=EXCLUDED.channel_id''', (guild_id, channel_id))


def set_pieces(user_id, amount):
    with database() as cur:
        cur.execute('''INSERT INTO bot_attendance.users(user_id,sol_erda_pieces) VALUES (%s,%s)
            ON CONFLICT(user_id) DO UPDATE SET sol_erda_pieces=EXCLUDED.sol_erda_pieces''', (user_id, amount))


def tick(user_id, guild_id, day):
    with database() as cur:
        cur.execute('INSERT INTO bot_attendance.users(user_id) VALUES (%s) ON CONFLICT DO NOTHING', (user_id,))
        # A row lock and DB clock prevent two processes awarding the same minute/day.
        cur.execute('''SELECT last_check,count,sol_erda_pieces,voice_day,voice_seconds,
            last_tick IS NULL OR last_tick <= clock_timestamp() - interval '55 seconds'
            FROM bot_attendance.users WHERE user_id=%s FOR UPDATE''', (user_id,))
        last_check, count, pieces, voice_day, seconds, eligible = cur.fetchone()
        if not eligible:
            return None
        seconds = (seconds if voice_day == day else 0) + 60
        awarded = last_check != day and seconds >= 600
        if awarded:
            count += 1
            pieces += 5 if count % 7 == 0 else 0
            last_check = day
        cur.execute('''UPDATE bot_attendance.users SET last_check=%s,count=%s,
            sol_erda_pieces=%s,voice_day=%s,voice_seconds=%s,last_tick=clock_timestamp(),
            last_voice_at=%s WHERE user_id=%s''',
            (last_check,count,pieces,day,seconds,day,user_id))
        if not awarded:
            return None
        cur.execute('SELECT channel_id FROM bot_attendance.settings WHERE guild_id=%s', (guild_id,))
        row = cur.fetchone()
        return count, pieces, row[0] if row else None
