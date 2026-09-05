import os
import uuid
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import psycopg2
import psycopg2.extras

# ============================================================
# CONNECTION + SCHEMA
# ============================================================

class RealDictRowWithIntegerIndexing(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._values = list(self.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            try:
                return self._values[key]
            except IndexError:
                raise KeyError(key)
        
        val = super().__getitem__(key)
        if key == 'tags' and isinstance(val, (list, dict)):
            return json.dumps(val)
        return val

    def get(self, key, default=None):
        val = super().get(key, default)
        if key == 'tags' and isinstance(val, (list, dict)):
            return json.dumps(val)
        return val

class CustomRealDictCursor(psycopg2.extras.RealDictCursor):
    def fetchone(self):
        row = super().fetchone()
        if row is None:
            return None
        return RealDictRowWithIntegerIndexing(row)

    def fetchall(self):
        rows = super().fetchall()
        return [RealDictRowWithIntegerIndexing(r) for r in rows]

from psycopg2.pool import ThreadedConnectionPool

_connection_pool = None
_pool_pid = None
_connection_last_used = {}

_POOL_MIN = 1
_POOL_MAX = 5
_IDLE_PING_AFTER = 10


def _make_pool(db_url: str) -> ThreadedConnectionPool:
    """Create a new ThreadedConnectionPool configured for PostgreSQL connection pooling."""
    return ThreadedConnectionPool(
        _POOL_MIN, _POOL_MAX,
        dsn=db_url,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )


def get_connection_pool() -> ThreadedConnectionPool:
    global _connection_pool, _pool_pid
    current_pid = os.getpid()
    if _connection_pool is None or _pool_pid != current_pid:
        if _connection_pool is not None:
            try:
                _connection_pool.closeall()
            except Exception:
                pass
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            raise RuntimeError("DATABASE_URL environment variable is not set")
        _connection_pool = _make_pool(db_url)
        _pool_pid = current_pid
    return _connection_pool


def _discard_conn(pool: ThreadedConnectionPool, raw_conn) -> None:
    """Safely return a broken connection to the pool and close it."""
    conn_id = id(raw_conn)
    _connection_last_used.pop(conn_id, None)
    try:
        raw_conn.close()
    except Exception:
        pass
    try:
        pool.putconn(raw_conn)
    except Exception:
        pass


class PostgresConnectionWrapper:
    def __init__(self, conn, pool=None, request_scoped=False):
        self._conn = conn
        self._pool = pool
        self.request_scoped = request_scoped

    def cursor(self, *args, **kwargs):
        if 'cursor_factory' not in kwargs:
            kwargs['cursor_factory'] = CustomRealDictCursor
        return self._conn.cursor(*args, **kwargs)

    def execute(self, sql, params=None):
        sql = sql.replace('?', '%s')

        if params:
            new_params = []
            for p in params:
                if isinstance(p, str) and p.startswith('[') and p.endswith(']'):
                    try:
                        p = json.loads(p)
                    except Exception:
                        pass
                new_params.append(p)
            params = tuple(new_params)

        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        """No-op if connection is request-scoped."""
        if self.request_scoped:
            return
        self.actual_close()

    def actual_close(self):
        """Return the connection to the pool or close it."""
        if self._pool:
            try:
                self._conn.rollback()
            except Exception:
                pass
            _connection_last_used[id(self._conn)] = time.time()
            try:
                self._pool.putconn(self._conn)
            except Exception:
                try:
                    self._conn.close()
                except Exception:
                    pass
        else:
            self._conn.close()


def _acquire_raw_connection():
    pool = get_connection_pool()
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    last_err = None
    for _attempt in range(3):
        try:
            raw_conn = pool.getconn()
        except Exception as e:
            # Pool exhausted or internally broken — fall through to direct connect
            last_err = e
            break

        # psycopg2 already knows this connection is dead
        if raw_conn.closed != 0:
            _discard_conn(pool, raw_conn)
            continue

        now = time.time()
        conn_id = id(raw_conn)
        last_used = _connection_last_used.get(conn_id, 0)

        if now - last_used > _IDLE_PING_AFTER:
            # Light health-check: just execute SELECT 1 and rollback any open txn.
            # We deliberately skip reset() because it issues SET commands that
            # PgBouncer (transaction mode) does not support between transactions.
            try:
                with raw_conn.cursor() as cur:
                    cur.execute("SELECT 1")
                # Rollback health check only if autocommit is False
                if not raw_conn.autocommit:
                    raw_conn.rollback()  # close the health-check transaction cleanly
            except Exception as e:
                last_err = e
                _discard_conn(pool, raw_conn)
                continue

        return raw_conn, pool

    # All pool attempts failed — open a fresh direct connection as last resort
    raw_conn = psycopg2.connect(db_url)
    return raw_conn, None


def get_db() -> 'PostgresConnectionWrapper':
    """Obtain a healthy DB connection from the pool.

    Uses Flask's `g` request context to cache the connection if available,
    enables autocommit by default to optimize latency, and retries on failure.
    """
    from flask import has_app_context, g

    if has_app_context():
        if 'db_conn' not in g:
            raw_conn, pool = _acquire_raw_connection()
            # Enable autocommit for fast query round-trips
            raw_conn.autocommit = True
            g.db_conn = PostgresConnectionWrapper(raw_conn, pool=pool, request_scoped=True)
        return g.db_conn
    else:
        # Non-Flask context (CLI/standalone scripts)
        raw_conn, pool = _acquire_raw_connection()
        raw_conn.autocommit = True
        return PostgresConnectionWrapper(raw_conn, pool=pool, request_scoped=False)

def row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return dict(row)

def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]

def init_db():
    """Verifies connection to the database. Tables must be created via schema.sql in Supabase SQL editor."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.close()
    finally:
        conn.close()


def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# ============================================================
# SUBJECTS BY SEMESTER
# ============================================================

SEMESTER_SUBJECTS = {
    1: [
        "Discrete Mathematics",
        "Digital Fundamentals",
        "Fundamentals of Programming Using C++",
        "English for Science",
        "Cyber Laws and Security",
        "Software Lab in C++",
        "Spanish 1",
        "French 1"
    ],
    2: [
        "Indian Constitution: Legal and Ethical Perspectives",
        "Web Technology",
        "Operating Systems",
        "Data Structures",
        "Mathematics Foundations to Computer Science",
        "AEC — English",
        "Spanish 2",
        "French 2"
    ],
    3: [
        "Python Programming",
        "Database Management Systems",
        "Design and Analysis of Algorithms",
        "Software Engineering",
        "Quantitative Techniques",
        "Feature Engineering",
        "Introduction to Cyber Security",
        "Interactive Web Application Development Using PHP and MySQL",
        "Basics of Data Analytics Using Spreadsheet"
    ],
    4: [
        "Object Oriented Programming Using Java",
        "Design Thinking and Innovation",
        "Entrepreneurship and Startup Ecosystem",
        "Probability Distributions and Statistical Inference",
        "Artificial Intelligence",
        "Network Simulation",
        "Intro to ML",
        "Data Visualization",
        "Web Application Development Using Node.js and Express.js"
    ],
    5: [
        "Computer Networks",
        "Digital Marketing",
        "Disaster Management",
        "Introduction to Data Science",
        "Time Series Analysis",
        "Machine Learning",
        "Introduction to Deep Learning",
        "Digital Image Processing",
        "Natural Language Processing",
        "Web Development with Python - Django/Flask",
        "Cross-Platform Application Development with Dart and Flutter",
        "Modern Web Application Development with React.js",
        "Ethical Hacking",
        "Cloud Security",
        "IoT Security"
    ],
    6: []
}

# Derived flattened list of all subjects
SUBJECTS = []
for _subs in SEMESTER_SUBJECTS.values():
    SUBJECTS.extend(_subs)
SUBJECTS = sorted(list(set(SUBJECTS)))



# ============================================================
# USER HELPERS
# ============================================================

def get_or_create_user(padikku_user_id: str, email: str, name: str, is_admin: bool = False) -> dict:
    conn = get_db()
    try:
        new_id = _uid()
        now = _now()
        # Single-query upsert using PostgreSQL ON CONFLICT ... RETURNING
        row = conn.execute(
            """INSERT INTO users (id, padikku_user_id, email, is_admin, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (padikku_user_id) DO UPDATE
               SET is_admin = CASE WHEN EXCLUDED.is_admin THEN TRUE ELSE users.is_admin END
               RETURNING *""",
            (new_id, padikku_user_id, email, is_admin, now)
        ).fetchone()
        conn.commit()
        user = row_to_dict(row)
        user['is_admin'] = bool(user['is_admin'])
        return user
    finally:
        conn.close()


def get_user_by_id(user_id: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            return None
        u = row_to_dict(row)
        u['is_admin'] = bool(u['is_admin'])
        return u
    finally:
        conn.close()


def get_user_by_padikku_id(padikku_user_id: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE padikku_user_id=?", (padikku_user_id,)
        ).fetchone()
        if not row:
            return None
        u = row_to_dict(row)
        u['is_admin'] = bool(u['is_admin'])
        return u
    finally:
        conn.close()


def is_nickname_available(nickname: str, exclude_user_id: str = None) -> bool:
    conn = get_db()
    try:
        if exclude_user_id:
            row = conn.execute(
                "SELECT id FROM users WHERE lower(nickname)=lower(?) AND id!=?",
                (nickname, exclude_user_id)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM users WHERE lower(nickname)=lower(?)", (nickname,)
            ).fetchone()
        return row is None
    finally:
        conn.close()


def set_nickname(user_id: str, nickname: str) -> dict:
    conn = get_db()
    try:
        now = _now()
        row = conn.execute(
            "UPDATE users SET nickname=?, nickname_changed_at=? WHERE id=? RETURNING *",
            (nickname, now, user_id)
        ).fetchone()
        conn.commit()
        return row_to_dict(row)
    finally:
        conn.close()


def can_change_nickname(user: dict) -> tuple[bool, int]:
    changed_at = user.get('nickname_changed_at')
    if not changed_at:
        return True, 0
    try:
        dt = datetime.fromisoformat(changed_at.replace('Z', '+00:00'))
    except Exception:
        return True, 0
    next_allowed = dt + timedelta(days=30)
    now = datetime.now(timezone.utc)
    if now >= next_allowed:
        return True, 0
    return False, (next_allowed - now).days + 1


# ============================================================
# DOUBT HELPERS
# ============================================================

def _enrich_doubt(row: dict, conn) -> dict:
    """Add poster nickname, reply_count, has_admin_answer to a doubt dict."""
    if not row:
        return row
    # Parse tags
    try:
        row['tags'] = json.loads(row.get('tags') or '[]')
    except Exception:
        row['tags'] = []
    row['is_anonymous'] = bool(row.get('is_anonymous'))
    row['is_resolved'] = bool(row.get('is_resolved'))

    # Poster info
    user = conn.execute(
        "SELECT nickname, email FROM users WHERE id=?", (row['user_id'],)
    ).fetchone()
    row['nickname'] = user['nickname'] if user else 'Unknown'
    row['poster_email'] = user['email'] if user else ''

    # Counts
    row['reply_count'] = conn.execute(
        "SELECT COUNT(*) FROM replies WHERE doubt_id=? AND is_hidden=FALSE AND is_admin_answer=FALSE",
        (row['id'],)
    ).fetchone()[0]
    row['has_admin_answer'] = conn.execute(
        "SELECT COUNT(*) FROM replies r JOIN users u ON u.id=r.user_id WHERE r.doubt_id=? AND u.is_admin=TRUE",
        (row['id'],)
    ).fetchone()[0]

    return row


def list_doubts(
    semester: str = None,
    subject: str = None,
    unanswered: bool = False,
    admin_answer: bool = False,
    sort: str = 'latest',
    limit: int = 50
) -> list[dict]:
    conn = get_db()
    try:
        conditions = []
        params = []
        if semester:
            conditions.append("d.semester=%s")
            params.append(int(semester))
        if subject:
            conditions.append("d.subject=%s")
            params.append(subject)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        order = {
            'upvotes': 'ORDER BY upvotes DESC',
            'replies': 'ORDER BY reply_count DESC',
        }.get(sort, 'ORDER BY created_at DESC')

        # Build HAVING clause for unanswered / admin_answer filters so filtering
        # happens in Postgres instead of Python (avoids fetching rows we discard).
        having_parts = []
        if unanswered:
            having_parts.append("reply_count = 0")
        if admin_answer:
            having_parts.append("has_admin_answer > 0")
        # Wrap in a subquery so HAVING can reference computed alias columns
        # without requiring GROUP BY on every column in d.*.
        having_clause = ("HAVING " + " AND ".join(having_parts)) if having_parts else ""

        sql = f"""
            SELECT * FROM (
                SELECT d.*,
                       u.nickname,
                       u.email AS poster_email,
                       (SELECT COUNT(*) FROM replies r
                        WHERE r.doubt_id=d.id AND r.is_hidden=FALSE AND r.is_admin_answer=FALSE) AS reply_count,
                       (SELECT COUNT(*) FROM replies r JOIN users u2 ON u2.id=r.user_id
                        WHERE r.doubt_id=d.id AND u2.is_admin=TRUE) AS has_admin_answer
                FROM doubts d
                LEFT JOIN users u ON u.id=d.user_id
                {where}
            ) sub
            {having_clause}
            {order}
            LIMIT %s
        """
        params.append(limit)
        rows = rows_to_dicts(conn.execute(sql, params).fetchall())

        result = []
        for r in rows:
            try:
                r['tags'] = json.loads(r.get('tags') or '[]')
            except Exception:
                r['tags'] = []
            r['is_anonymous'] = bool(r.get('is_anonymous'))
            r['is_resolved'] = bool(r.get('is_resolved'))
            result.append(r)

        return result
    finally:
        conn.close()


def get_doubt(doubt_id: str) -> dict | None:
    conn = get_db()
    try:
        sql = """
            SELECT d.*,
                   u.nickname,
                   u.email AS poster_email,
                   (SELECT COUNT(*) FROM replies r
                    WHERE r.doubt_id=d.id AND r.is_hidden=FALSE AND r.is_admin_answer=FALSE) AS reply_count,
                   (SELECT COUNT(*) FROM replies r JOIN users u2 ON u2.id=r.user_id
                    WHERE r.doubt_id=d.id AND u2.is_admin=TRUE) AS has_admin_answer
            FROM doubts d
            LEFT JOIN users u ON u.id=d.user_id
            WHERE d.id = ?
        """
        row = conn.execute(sql, (doubt_id,)).fetchone()
        if not row:
            return None
        r = row_to_dict(row)
        try:
            r['tags'] = json.loads(r.get('tags') or '[]')
        except Exception:
            r['tags'] = []
        r['is_anonymous'] = bool(r.get('is_anonymous'))
        r['is_resolved'] = bool(r.get('is_resolved'))
        r['nickname'] = r.get('nickname') or 'Unknown'
        r['poster_email'] = r.get('poster_email') or ''
        return r
    finally:
        conn.close()


def create_doubt(
    user_id: str, title: str, description: str,
    subject: str, semester: int, tags: list, is_anonymous: bool
) -> dict:
    conn = get_db()
    try:
        new_id = _uid()
        now = _now()
        row = conn.execute(
            """INSERT INTO doubts (id, user_id, title, description, subject, semester, tags, is_anonymous, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               RETURNING *""",
            (new_id, user_id, title.strip(), description.strip() if description else None,
             subject, int(semester), json.dumps(tags), is_anonymous, now)
        ).fetchone()
        conn.commit()
        r = row_to_dict(row)
        try:
            r['tags'] = json.loads(r.get('tags') or '[]')
        except Exception:
            r['tags'] = []
        r['is_anonymous'] = bool(r.get('is_anonymous'))
        r['is_resolved'] = bool(r.get('is_resolved'))
        r['reply_count'] = 0
        r['has_admin_answer'] = False
        user = conn.execute("SELECT nickname, email FROM users WHERE id=?", (user_id,)).fetchone()
        r['nickname'] = user['nickname'] if user else 'Unknown'
        r['poster_email'] = user['email'] if user else ''
        return r
    finally:
        conn.close()


# ============================================================
# REPLY HELPERS
# ============================================================

def get_replies(doubt_id: str) -> list[dict]:
    conn = get_db()
    try:
        rows = rows_to_dicts(conn.execute(
            "SELECT r.*, u.nickname, u.email, u.is_admin FROM replies r "
            "JOIN users u ON u.id=r.user_id "
            "WHERE r.doubt_id=? ORDER BY r.created_at",
            (doubt_id,)
        ).fetchall())
        for r in rows:
            r['is_admin_answer'] = bool(r.get('is_admin_answer'))
            r['is_helpful'] = bool(r.get('is_helpful'))
            r['is_hidden'] = bool(r.get('is_hidden'))
            r['is_admin'] = bool(r.get('is_admin'))
        return rows
    finally:
        conn.close()


def get_admin_answer(doubt_id: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT r.*, u.nickname FROM replies r "
            "JOIN users u ON u.id=r.user_id "
            "WHERE r.doubt_id=? AND r.is_admin_answer=TRUE",
            (doubt_id,)
        ).fetchone()
        if not row:
            return None
        r = row_to_dict(row)
        r['is_admin_answer'] = True
        return r
    finally:
        conn.close()


def create_reply(doubt_id: str, user_id: str, content: str, is_admin_answer: bool = False) -> dict:
    conn = get_db()
    try:
        if is_admin_answer:
            conn.execute(
                "DELETE FROM replies WHERE doubt_id=? AND is_admin_answer=TRUE", (doubt_id,)
            )
        new_id = _uid()
        row = conn.execute(
            "INSERT INTO replies (id, doubt_id, user_id, content, is_admin_answer, created_at) "
            "VALUES (?,?,?,?,?,?) RETURNING *",
            (new_id, doubt_id, user_id, content.strip(), is_admin_answer, _now())
        ).fetchone()
        conn.commit()
        r = row_to_dict(row)
        r['is_admin_answer'] = bool(r.get('is_admin_answer'))
        r['is_helpful'] = bool(r.get('is_helpful'))
        r['is_hidden'] = bool(r.get('is_hidden'))
        return r
    finally:
        conn.close()


def mark_reply_helpful(reply_id: str, doubt_id: str, requester_user_id: str) -> bool:
    conn = get_db()
    try:
        doubt = conn.execute("SELECT user_id FROM doubts WHERE id=?", (doubt_id,)).fetchone()
        if not doubt or str(doubt['user_id']) != str(requester_user_id):
            return False
        conn.execute("UPDATE replies SET is_helpful=FALSE WHERE doubt_id=?", (doubt_id,))
        conn.execute("UPDATE replies SET is_helpful=TRUE WHERE id=?", (reply_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def hide_reply(reply_id: str, hide: bool = True):
    conn = get_db()
    try:
        conn.execute("UPDATE replies SET is_hidden=? WHERE id=?", (hide, reply_id))
        conn.commit()
    finally:
        conn.close()


def delete_user_db(user_id: str):
    conn = get_db()
    try:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def delete_doubt_db(doubt_id: str):
    conn = get_db()
    try:
        # Delete upvotes for replies on this doubt
        conn.execute("DELETE FROM upvotes WHERE target_id IN (SELECT id FROM replies WHERE doubt_id=?)", (doubt_id,))
        # Delete upvotes for the doubt itself
        conn.execute("DELETE FROM upvotes WHERE target_id=?", (doubt_id,))
        # Delete the doubt itself (cascades to replies)
        conn.execute("DELETE FROM doubts WHERE id=?", (doubt_id,))
        conn.commit()
    finally:
        conn.close()


def delete_reply_db(reply_id: str):
    conn = get_db()
    try:
        conn.execute("DELETE FROM replies WHERE id=?", (reply_id,))
        conn.commit()
    finally:
        conn.close()



# ============================================================
# UPVOTE HELPERS
# ============================================================

def toggle_upvote(user_id: str, target_id: str, target_type: str) -> tuple[bool, int]:
    conn = get_db()
    try:
        deleted = conn.execute(
            "DELETE FROM upvotes WHERE user_id=? AND target_id=? RETURNING id",
            (user_id, target_id)
        ).fetchone()

        table = 'doubts' if target_type == 'doubt' else 'replies'
        if deleted:
            voted = False
            row = conn.execute(
                f"UPDATE {table} SET upvotes = GREATEST(0, upvotes - 1) WHERE id=? RETURNING upvotes",
                (target_id,)
            ).fetchone()
        else:
            voted = True
            conn.execute(
                "INSERT INTO upvotes (id, user_id, target_id, target_type, created_at) VALUES (?,?,?,?,?)",
                (_uid(), user_id, target_id, target_type, _now())
            )
            row = conn.execute(
                f"UPDATE {table} SET upvotes = upvotes + 1 WHERE id=? RETURNING upvotes",
                (target_id,)
            ).fetchone()

        conn.commit()
        new_count = row['upvotes'] if row and 'upvotes' in row else (row[0] if row else 0)
        return voted, new_count
    finally:
        conn.close()


def get_user_upvotes(user_id: str, target_ids: list[str]) -> set[str]:
    if not target_ids:
        return set()
    conn = get_db()
    try:
        placeholders = ','.join('?' * len(target_ids))
        rows = conn.execute(
            f"SELECT target_id FROM upvotes WHERE user_id=? AND target_id IN ({placeholders})",
            [user_id] + target_ids
        ).fetchall()
        return {r['target_id'] for r in rows}
    finally:
        conn.close()


# ============================================================
# ADMIN HELPERS
# ============================================================

def get_all_doubts_admin(status: str = None, subject: str = None, semester: str = None) -> list[dict]:
    conn = get_db()
    try:
        conditions = []
        params = []
        if status == 'resolved':
            conditions.append("d.is_resolved=TRUE")
        elif status == 'unresolved':
            conditions.append("d.is_resolved=FALSE")
        if subject:
            conditions.append("d.subject=?"); params.append(subject)
        if semester:
            conditions.append("d.semester=?"); params.append(int(semester))

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
             SELECT d.*, u.nickname, u.email AS poster_email,
                    (SELECT COUNT(*) FROM replies r WHERE r.doubt_id=d.id AND r.is_hidden=FALSE AND r.is_admin_answer=FALSE) AS reply_count,
                    (SELECT COUNT(*) FROM replies r JOIN users u2 ON u2.id=r.user_id WHERE r.doubt_id=d.id AND u2.is_admin=TRUE) AS has_admin_answer
            FROM doubts d LEFT JOIN users u ON u.id=d.user_id
            {where} ORDER BY d.created_at DESC LIMIT 200
        """
        rows = rows_to_dicts(conn.execute(sql, params).fetchall())
        for r in rows:
            try: r['tags'] = json.loads(r.get('tags') or '[]')
            except: r['tags'] = []
            r['is_resolved'] = bool(r.get('is_resolved'))
            r['is_anonymous'] = bool(r.get('is_anonymous'))
        return rows
    finally:
        conn.close()


def get_all_replies_admin(limit: int = 100) -> list[dict]:
    conn = get_db()
    try:
        rows = rows_to_dicts(conn.execute(
            "SELECT r.*, u.nickname, u.email, d.title AS doubt_title "
            "FROM replies r "
            "JOIN users u ON u.id=r.user_id "
            "JOIN doubts d ON d.id=r.doubt_id "
            "ORDER BY r.created_at DESC LIMIT ?", (limit,)
        ).fetchall())
        for r in rows:
            r['is_hidden'] = bool(r.get('is_hidden'))
            r['is_admin_answer'] = bool(r.get('is_admin_answer'))
        return rows
    finally:
        conn.close()


def get_all_users_admin() -> list[dict]:
    conn = get_db()
    try:
        # Fetch users with doubt_count and reply_count in one query
        # to avoid N+1 (previously 2 extra queries per user).
        users = rows_to_dicts(conn.execute("""
            SELECT u.*,
                   (SELECT COUNT(*) FROM doubts d WHERE d.user_id=u.id) AS doubt_count,
                   (SELECT COUNT(*) FROM replies r WHERE r.user_id=u.id) AS reply_count
            FROM users u
            ORDER BY u.created_at DESC
        """).fetchall())
        for u in users:
            u['is_admin'] = bool(u.get('is_admin'))
        return users
    finally:
        conn.close()


def get_admin_stats() -> dict:
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM doubts)                                  AS total_doubts,
                (SELECT COUNT(*) FROM replies)                                 AS total_replies,
                (SELECT COUNT(*) FROM doubts WHERE is_resolved=TRUE)           AS resolved_count,
                (SELECT COUNT(*) FROM users)                                   AS total_users
        """).fetchone()
        return {
            'total_doubts':  row['total_doubts'] or 0,
            'total_replies': row['total_replies'] or 0,
            'resolved_count': row['resolved_count'] or 0,
            'total_users':   row['total_users'] or 0,
        }
    finally:
        conn.close()


def resolve_doubt_db(doubt_id: str):
    conn = get_db()
    try:
        conn.execute("UPDATE doubts SET is_resolved=TRUE WHERE id=?", (doubt_id,))
        conn.commit()
    finally:
        conn.close()


def hide_doubt_db(doubt_id: str):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE doubts SET title='[Hidden by Admin]', description=NULL WHERE id=?", (doubt_id,)
        )
        conn.commit()
    finally:
        conn.close()

# ============================================================
# PROFILE HELPERS
# ============================================================

def get_user_doubts(user_id: str) -> list[dict]:
    conn = get_db()
    try:
        sql = """
            SELECT d.*, u.nickname,
                   (SELECT COUNT(*) FROM replies r WHERE r.doubt_id=d.id AND r.is_hidden=FALSE AND r.is_admin_answer=FALSE) AS reply_count,
                   (SELECT COUNT(*) FROM replies r JOIN users u2 ON u2.id=r.user_id WHERE r.doubt_id=d.id AND u2.is_admin=TRUE) AS has_admin_answer
            FROM doubts d JOIN users u ON u.id=d.user_id
            WHERE d.user_id=? ORDER BY d.created_at DESC
        """
        rows = rows_to_dicts(conn.execute(sql, (user_id,)).fetchall())
        for r in rows:
            try: r['tags'] = json.loads(r.get('tags') or '[]')
            except: r['tags'] = []
            r['is_resolved'] = bool(r.get('is_resolved'))
            r['is_anonymous'] = bool(r.get('is_anonymous'))
        return rows
    finally:
        conn.close()


def get_user_replies(user_id: str) -> list[dict]:
    conn = get_db()
    try:
        rows = rows_to_dicts(conn.execute(
            "SELECT r.*, d.title AS doubt_title FROM replies r "
            "JOIN doubts d ON d.id=r.doubt_id "
            "WHERE r.user_id=? ORDER BY r.created_at DESC",
            (user_id,)
        ).fetchall())
        for r in rows:
            r['is_helpful'] = bool(r.get('is_helpful'))
        return rows
    finally:
        conn.close()


def get_user_stats(user_id: str) -> dict:
    """Return doubts_posted, replies_given, and helpful_marks in a single DB round-trip."""
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM doubts   WHERE user_id=%s)                          AS doubts_posted,
                (SELECT COUNT(*) FROM replies  WHERE user_id=%s)                          AS replies_given,
                (SELECT COUNT(*) FROM replies  WHERE user_id=%s AND is_helpful=TRUE)      AS helpful_marks
            """,
            (user_id, user_id, user_id)
        ).fetchone()
        return {
            'doubts_posted': row['doubts_posted'] or 0,
            'replies_given': row['replies_given'] or 0,
            'helpful_marks': row['helpful_marks'] or 0,
        }
    finally:
        conn.close()
