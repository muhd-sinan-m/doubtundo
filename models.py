import os
import uuid
import json
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

class PostgresConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        if 'cursor_factory' not in kwargs:
            kwargs['cursor_factory'] = CustomRealDictCursor
        return self._conn.cursor(*args, **kwargs)

    def execute(self, sql, params=None):
        # Swap SQLite parameter ? with PostgreSQL %s
        sql = sql.replace('?', '%s')
        
        # Handle tags serialization (convert stringified JSON arrays back to lists for PostgreSQL array support)
        if params:
            new_params = []
            for p in params:
                if isinstance(p, str) and p.startswith('[') and p.endswith(']'):
                    try:
                        p = json.loads(p)
                    except:
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
        self._conn.close()

def get_db():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    raw_conn = psycopg2.connect(db_url)
    return PostgresConnectionWrapper(raw_conn)

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
        row = conn.execute(
            "SELECT * FROM users WHERE padikku_user_id = ?", (padikku_user_id,)
        ).fetchone()

        if row:
            user = row_to_dict(row)
            user['is_admin'] = bool(user['is_admin'])
            # Promote to admin if needed
            if is_admin and not user['is_admin']:
                conn.execute("UPDATE users SET is_admin=TRUE WHERE id=?", (user['id'],))
                conn.commit()
                user['is_admin'] = True
            return user

        # Create new user
        new_id = _uid()
        conn.execute(
            "INSERT INTO users (id, padikku_user_id, email, is_admin, created_at) VALUES (?,?,?,?,?)",
            (new_id, padikku_user_id, email, is_admin, _now())
        )
        conn.commit()
        return row_to_dict(conn.execute("SELECT * FROM users WHERE id=?", (new_id,)).fetchone())
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
        conn.execute(
            "UPDATE users SET nickname=?, nickname_changed_at=? WHERE id=?",
            (nickname, now, user_id)
        )
        conn.commit()
        return row_to_dict(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
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
            conditions.append("d.semester=?")
            params.append(int(semester))
        if subject:
            conditions.append("d.subject=?")
            params.append(subject)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        order = {
            'upvotes': 'ORDER BY d.upvotes DESC',
            'replies': 'ORDER BY reply_count DESC',
        }.get(sort, 'ORDER BY d.created_at DESC')

        sql = f"""
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
            {order}
            LIMIT ?
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
            # Post-filter
            if unanswered and r['reply_count'] > 0:
                continue
            if admin_answer and not r['has_admin_answer']:
                continue
            result.append(r)

        return result
    finally:
        conn.close()


def get_doubt(doubt_id: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM doubts WHERE id=?", (doubt_id,)).fetchone()
        if not row:
            return None
        return _enrich_doubt(row_to_dict(row), conn)
    finally:
        conn.close()


def create_doubt(
    user_id: str, title: str, description: str,
    subject: str, semester: int, tags: list, is_anonymous: bool
) -> dict:
    conn = get_db()
    try:
        new_id = _uid()
        conn.execute(
            """INSERT INTO doubts (id, user_id, title, description, subject, semester, tags, is_anonymous, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (new_id, user_id, title.strip(), description.strip() if description else None,
             subject, int(semester), json.dumps(tags), is_anonymous, _now())
        )
        conn.commit()
        row = conn.execute("SELECT * FROM doubts WHERE id=?", (new_id,)).fetchone()
        return _enrich_doubt(row_to_dict(row), conn)
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
        conn.execute(
            "INSERT INTO replies (id, doubt_id, user_id, content, is_admin_answer, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (new_id, doubt_id, user_id, content.strip(), is_admin_answer, _now())
        )
        conn.commit()
        return row_to_dict(conn.execute("SELECT * FROM replies WHERE id=?", (new_id,)).fetchone())
    finally:
        conn.close()


def mark_reply_helpful(reply_id: str, doubt_id: str, requester_user_id: str) -> bool:
    conn = get_db()
    try:
        doubt = conn.execute("SELECT user_id FROM doubts WHERE id=?", (doubt_id,)).fetchone()
        if not doubt or doubt['user_id'] != requester_user_id:
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


# ============================================================
# UPVOTE HELPERS
# ============================================================

def toggle_upvote(user_id: str, target_id: str, target_type: str) -> tuple[bool, int]:
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM upvotes WHERE user_id=? AND target_id=?", (user_id, target_id)
        ).fetchone()

        if existing:
            conn.execute("DELETE FROM upvotes WHERE id=?", (existing['id'],))
            delta = -1
            voted = False
        else:
            conn.execute(
                "INSERT INTO upvotes (id, user_id, target_id, target_type, created_at) VALUES (?,?,?,?,?)",
                (_uid(), user_id, target_id, target_type, _now())
            )
            delta = 1
            voted = True

        table = 'doubts' if target_type == 'doubt' else 'replies'
        new_count = max(0, conn.execute(
            f"SELECT upvotes FROM {table} WHERE id=?", (target_id,)
        ).fetchone()[0] + delta)
        conn.execute(f"UPDATE {table} SET upvotes=? WHERE id=?", (new_count, target_id))
        conn.commit()
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
            conditions.append("d.is_resolved=1")
        elif status == 'unresolved':
            conditions.append("d.is_resolved=0")
        if subject:
            conditions.append("d.subject=?"); params.append(subject)
        if semester:
            conditions.append("d.semester=?"); params.append(int(semester))

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
             SELECT d.*, u.nickname, u.email AS poster_email,
                    (SELECT COUNT(*) FROM replies r WHERE r.doubt_id=d.id AND r.is_hidden=0 AND r.is_admin_answer=0) AS reply_count,
                    (SELECT COUNT(*) FROM replies r JOIN users u2 ON u2.id=r.user_id WHERE r.doubt_id=d.id AND u2.is_admin=1) AS has_admin_answer
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
        users = rows_to_dicts(conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC"
        ).fetchall())
        for u in users:
            u['is_admin'] = bool(u.get('is_admin'))
            u['doubt_count'] = conn.execute(
                "SELECT COUNT(*) FROM doubts WHERE user_id=?", (u['id'],)
            ).fetchone()[0]
            u['reply_count'] = conn.execute(
                "SELECT COUNT(*) FROM replies WHERE user_id=?", (u['id'],)
            ).fetchone()[0]
        return users
    finally:
        conn.close()


def get_admin_stats() -> dict:
    conn = get_db()
    try:
        return {
            'total_doubts':  conn.execute("SELECT COUNT(*) FROM doubts").fetchone()[0],
            'total_replies': conn.execute("SELECT COUNT(*) FROM replies").fetchone()[0],
            'resolved_count': conn.execute("SELECT COUNT(*) FROM doubts WHERE is_resolved=TRUE").fetchone()[0],
            'total_users':   conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
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


def get_supabase():
    """Stub — only here so any code that imports get_supabase doesn't crash.
    In SQLite mode this is unused. Will be removed when switching back to Supabase."""
    raise RuntimeError("Running in SQLite mode — get_supabase() is not available.")


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
    conn = get_db()
    try:
        return {
            'doubts_posted': conn.execute("SELECT COUNT(*) FROM doubts WHERE user_id=?", (user_id,)).fetchone()[0],
            'replies_given': conn.execute("SELECT COUNT(*) FROM replies WHERE user_id=?", (user_id,)).fetchone()[0],
            'helpful_marks': conn.execute("SELECT COUNT(*) FROM replies WHERE user_id=? AND is_helpful=1", (user_id,)).fetchone()[0],
        }
    finally:
        conn.close()
