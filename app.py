"""
app.py — Flask application factory for doubtundo.app
Academic doubt forum for BCA Cyber Security students at Marian College Kuttikkanam
"""

import os
import re
from datetime import datetime, timezone, timedelta
from functools import wraps

import markdown
import bleach
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, abort, g
)
from flask_session import Session

# Load .env for local development
load_dotenv()


# ============================================================
# APP FACTORY
# ============================================================

def create_app():
    app = Flask(__name__)

    # ── Secret key
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', os.urandom(32))

    # ── Server-side session (filesystem for Render free tier)
    app.config['SESSION_TYPE'] = os.environ.get('SESSION_TYPE', 'filesystem')
    app.config['SESSION_FILE_DIR'] = os.environ.get('SESSION_FILE_DIR', os.path.join(os.getcwd(), 'flask_session'))
    app.config['SESSION_PERMANENT'] = True
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
        days=int(os.environ.get('SESSION_LIFETIME_DAYS', 7))
    )
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV', '') == 'production'

    # ── Session dir
    session_dir = app.config['SESSION_FILE_DIR']
    os.makedirs(session_dir, exist_ok=True)

    Session(app)

    # ── Register blueprints
    from auth import auth_bp
    from admin import admin_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)

    # ── Template filters
    register_template_filters(app)

    # ── Main routes
    register_routes(app)

    # ── Error handlers
    register_error_handlers(app)

    # ── Init SQLite schema on startup
    from models import init_db
    with app.app_context():
        init_db()

    return app


# ============================================================
# TEMPLATE FILTERS
# ============================================================

def register_template_filters(app):
    ALLOWED_TAGS = [
        'p', 'br', 'strong', 'em', 'code', 'pre', 'ul', 'ol', 'li',
        'h1', 'h2', 'h3', 'h4', 'blockquote', 'a', 'hr', 'table',
        'thead', 'tbody', 'tr', 'th', 'td', 'span', 'del', 'ins',
    ]
    ALLOWED_ATTRS = {'a': ['href', 'title', 'target'], 'code': ['class']}

    @app.template_filter('timeago')
    def timeago_filter(dt_value):
        if not dt_value:
            return ''
        if isinstance(dt_value, str):
            try:
                dt_value = datetime.fromisoformat(dt_value.replace('Z', '+00:00'))
            except Exception:
                return dt_value
        now = datetime.now(timezone.utc)
        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=timezone.utc)
        diff = now - dt_value
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            m = seconds // 60
            return f"{m}m ago"
        elif seconds < 86400:
            h = seconds // 3600
            return f"{h}h ago"
        elif seconds < 604800:
            d = seconds // 86400
            return f"{d}d ago"
        else:
            return dt_value.strftime('%b %d, %Y')

    @app.template_filter('format_date')
    def format_date_filter(dt_value):
        if not dt_value:
            return ''
        if isinstance(dt_value, str):
            try:
                dt_value = datetime.fromisoformat(dt_value.replace('Z', '+00:00'))
            except Exception:
                return dt_value
        return dt_value.strftime('%B %d, %Y')

    @app.template_filter('render_markdown')
    def render_markdown_filter(text):
        if not text:
            return ''
        html = markdown.markdown(
            text,
            extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists']
        )
        clean = bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)
        return clean

    @app.template_filter('striptags')
    def striptags_filter(text):
        if not text:
            return ''
        return re.sub(r'<[^>]+>', '', str(text))

    @app.template_filter('truncate')
    def truncate_filter(text, length=100):
        if not text:
            return ''
        text = str(text)
        return text[:length] + '…' if len(text) > length else text


# ============================================================
# REQUEST LIFECYCLE
# ============================================================

def get_current_user():
    """Load current user from session. Cached in g."""
    if hasattr(g, 'current_user'):
        return g.current_user
    user_id = session.get('user_id')
    if not user_id:
        g.current_user = None
        return None
    from models import get_user_by_id
    user = get_user_by_id(user_id)
    g.current_user = user
    return user


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user():
            flash("Please sign in to continue.", "info")
            return redirect(url_for('auth_bp.login_redirect'))
        return f(*args, **kwargs)
    return decorated


# ============================================================
# MAIN ROUTES
# ============================================================

from models import SUBJECTS, SEMESTER_SUBJECTS


def register_routes(app):

    @app.before_request
    def load_user():
        g.current_user = get_current_user()

    @app.context_processor
    def inject_globals():
        return {
            'current_user': g.current_user,
            'subjects': SUBJECTS,
            'semester_subjects': SEMESTER_SUBJECTS,
        }

    # ── Home (Landing Page)
    @app.route('/', endpoint='main.index')
    def index():
        if g.current_user:
            return redirect(url_for('main.feed'))
        return render_template('landing.html')

    # ── Doubt Board Feed (Reddit-style)
    @app.route('/feed', endpoint='main.feed')
    @login_required
    def feed():
        current_user = g.current_user

        # Filters
        filters = {
            'semester': request.args.get('semester', ''),
            'subject': request.args.get('subject', ''),
            'unanswered': bool(request.args.get('unanswered')),
            'admin_answer': bool(request.args.get('admin_answer')),
        }
        current_sort = request.args.get('sort', 'latest')

        from models import list_doubts, get_user_upvotes, get_user_stats
        doubts = list_doubts(
            semester=filters['semester'] or None,
            subject=filters['subject'] or None,
            unanswered=filters['unanswered'],
            admin_answer=filters['admin_answer'],
            sort=current_sort,
        )

        # Get list of upvoted doubt IDs for active state highlights
        doubt_ids = [d['id'] for d in doubts]
        user_upvotes = get_user_upvotes(current_user['id'], doubt_ids) if current_user else set()

        # Get sidebar profile stats
        stats = get_user_stats(current_user['id'])

        # Build query string for sort links (preserve filters)
        qs_parts = []
        if filters['semester']:
            qs_parts.append(f"semester={filters['semester']}")
        if filters['subject']:
            qs_parts.append(f"subject={filters['subject']}")
        if filters['unanswered']:
            qs_parts.append("unanswered=1")
        if filters['admin_answer']:
            qs_parts.append("admin_answer=1")
        query_string = '&'.join(qs_parts)

        return render_template(
            'feed.html',
            doubts=doubts,
            filters=filters,
            current_sort=current_sort,
            query_string=query_string,
            user_upvotes=user_upvotes,
            stats=stats,
        )


    # ── Post a Doubt
    @app.route('/post', methods=['GET', 'POST'], endpoint='main.post_doubt')
    @login_required
    def post_doubt():
        errors = []
        form_data = {}

        if request.method == 'POST':
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            subject = request.form.get('subject', '').strip()
            semester = request.form.get('semester', '').strip()
            tags = request.form.getlist('tags')
            is_anonymous = bool(request.form.get('is_anonymous'))

            form_data = {
                'title': title, 'description': description,
                'subject': subject, 'semester': semester,
                'tags': tags, 'is_anonymous': is_anonymous,
            }

            # Validate
            if not title:
                errors.append("Title is required.")
            elif len(title) > 200:
                errors.append("Title must be 200 characters or less.")
            if not subject or subject not in SUBJECTS:
                errors.append("Please select a valid subject.")
            if not semester or semester not in [str(s) for s in range(1, 7)]:
                errors.append("Please select a valid semester (1–6).")

            if not errors:
                from models import create_doubt
                try:
                    doubt = create_doubt(
                        user_id=g.current_user['id'],
                        title=title,
                        description=description,
                        subject=subject,
                        semester=int(semester),
                        tags=tags,
                        is_anonymous=is_anonymous,
                    )
                    flash("Doubt posted! 🎉 Waiting for replies.", "success")
                    return redirect(url_for('main.feed'))
                except Exception as e:
                    app.logger.error(f"Create doubt error: {e}")
                    errors.append("Could not post doubt. Please try again.")

        return render_template('post_doubt.html', errors=errors, form_data=form_data)

    # ── Doubt Detail
    @app.route('/doubt/<doubt_id>', endpoint='main.doubt_detail')
    def doubt_detail(doubt_id):
        from models import (
            get_doubt, get_replies, get_admin_answer,
            get_user_upvotes
        )

        doubt = get_doubt(doubt_id)
        if not doubt:
            abort(404)

        # Render markdown
        import markdown as md
        import bleach
        ALLOWED_TAGS = ['p','br','strong','em','code','pre','ul','ol','li','h1','h2','h3','h4','blockquote','a','hr','table','thead','tbody','tr','th','td','span']
        ALLOWED_ATTRS = {'a': ['href', 'title'], 'code': ['class']}

        def render_md(text):
            if not text:
                return ''
            html = md.markdown(text, extensions=['fenced_code', 'tables', 'nl2br', 'sane_lists'])
            return bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS)

        doubt['description_html'] = render_md(doubt.get('description', ''))

        admin_answer = get_admin_answer(doubt_id)
        if admin_answer:
            admin_answer['content_html'] = render_md(admin_answer.get('content', ''))

        all_replies = get_replies(doubt_id)
        # Non-admin-answer replies
        replies = [r for r in all_replies if not r.get('is_admin_answer')]
        for r in replies:
            r['content_html'] = render_md(r.get('content', ''))

        reply_count = len(replies)

        # Upvote state
        current_user = g.current_user
        user_has_upvoted_doubt = False
        user_upvoted_replies = set()
        any_helpful_marked = any(r.get('is_helpful') for r in replies)

        if current_user:
            all_ids = [doubt_id] + [r['id'] for r in replies]
            upvoted = get_user_upvotes(current_user['id'], all_ids)
            user_has_upvoted_doubt = doubt_id in upvoted
            user_upvoted_replies = {rid for rid in upvoted if rid != doubt_id}

        is_doubt_owner = (
            current_user and
            str(current_user['id']) == str(doubt.get('user_id'))
        )

        return render_template(
            'doubt_detail.html',
            doubt=doubt,
            admin_answer=admin_answer,
            replies=replies,
            reply_count=reply_count,
            user_has_upvoted_doubt=user_has_upvoted_doubt,
            user_upvoted_replies=user_upvoted_replies,
            is_doubt_owner=is_doubt_owner,
            any_helpful_marked=any_helpful_marked,
        )

    # ── Post Reply
    @app.route('/doubt/<doubt_id>/reply', methods=['POST'], endpoint='main.post_reply')
    @login_required
    def post_reply(doubt_id):
        from models import create_reply, get_doubt
        content = request.form.get('content', '').strip()

        if not content or len(content) < 3:
            flash("Reply must be at least 3 characters.", "error")
            return redirect(url_for('main.doubt_detail', doubt_id=doubt_id))

        doubt = get_doubt(doubt_id)
        if not doubt:
            abort(404)

        try:
            create_reply(
                doubt_id=doubt_id,
                user_id=g.current_user['id'],
                content=content,
                is_admin_answer=False
            )
            flash("Reply posted! 💬", "success")
        except Exception as e:
            app.logger.error(f"Post reply error: {e}")
            flash("Could not post reply. Please try again.", "error")

        return redirect(url_for('main.doubt_detail', doubt_id=doubt_id))

    # ── Inline Replies Dropdown (AJAX HTML provider)
    @app.route('/doubt/<doubt_id>/replies-inline', endpoint='main.replies_inline')
    @login_required
    def replies_inline(doubt_id):
        from models import get_doubt, get_replies, get_admin_answer
        doubt = get_doubt(doubt_id)
        if not doubt:
            abort(404)

        all_replies = get_replies(doubt_id)
        replies = [r for r in all_replies if not r.get('is_admin_answer')]
        admin_answer = get_admin_answer(doubt_id)

        admin_emails = [
            e.strip().lower()
            for e in os.environ.get('ADMIN_EMAILS', '').split(',')
            if e.strip()
        ]

        return render_template(
            'inline_replies.html',
            doubt_id=doubt_id,
            replies=replies,
            admin_answer=admin_answer,
            admin_emails=admin_emails
        )

    # ── Post Reply AJAX Handler
    @app.route('/doubt/<doubt_id>/reply-ajax', methods=['POST'], endpoint='main.post_reply_ajax')
    @login_required
    def post_reply_ajax(doubt_id):
        from models import create_reply, get_doubt
        content = request.form.get('content', '').strip()

        if not content or len(content) < 3:
            return jsonify({'success': False, 'error': 'Reply must be at least 3 characters.'}), 400

        doubt = get_doubt(doubt_id)
        if not doubt:
            return jsonify({'success': False, 'error': 'Doubt not found.'}), 404

        try:
            create_reply(
                doubt_id=doubt_id,
                user_id=g.current_user['id'],
                content=content,
                is_admin_answer=False
            )
            return jsonify({'success': True})
        except Exception as e:
            app.logger.error(f"Post reply AJAX error: {e}")
            return jsonify({'success': False, 'error': 'Error posting reply.'}), 500


    # ── Upvote (AJAX)
    @app.route('/upvote', methods=['POST'], endpoint='main.upvote')
    @login_required
    def upvote():
        from models import toggle_upvote
        data = request.get_json(silent=True) or {}
        target_id = data.get('id', '').strip()
        target_type = data.get('type', '').strip()

        if not target_id or target_type not in ('doubt', 'reply'):
            return jsonify({'error': 'Invalid request'}), 400

        try:
            voted, new_count = toggle_upvote(
                user_id=g.current_user['id'],
                target_id=target_id,
                target_type=target_type
            )
            return jsonify({'voted': voted, 'count': new_count})
        except Exception as e:
            app.logger.error(f"Upvote error: {e}")
            return jsonify({'error': 'Could not toggle upvote'}), 500

    # ── Mark Helpful
    @app.route('/reply/<reply_id>/helpful', methods=['POST'], endpoint='main.mark_helpful')
    @login_required
    def mark_helpful(reply_id):
        from models import mark_reply_helpful, get_db
        conn = get_db()
        try:
            row = conn.execute("SELECT doubt_id FROM replies WHERE id=?", (reply_id,)).fetchone()
        finally:
            conn.close()

        if not row:
            abort(404)
        doubt_id = row['doubt_id']

        success = mark_reply_helpful(reply_id, doubt_id, g.current_user['id'])
        if success:
            flash("Reply marked as helpful ✅", "success")
        else:
            flash("You can only mark helpful on your own doubts.", "error")

        return redirect(url_for('main.doubt_detail', doubt_id=doubt_id))

    # ── Profile
    @app.route('/profile', endpoint='main.profile')
    @login_required
    def profile():
        from models import (
            get_user_doubts, get_user_replies, get_user_stats, can_change_nickname
        )
        user = g.current_user

        can_change, days_left = can_change_nickname(user)
        next_change_date = ''
        if not can_change and user.get('nickname_changed_at'):
            changed_at = datetime.fromisoformat(
                str(user['nickname_changed_at']).replace('Z', '+00:00')
            )
            next_date = changed_at + timedelta(days=30)
            next_change_date = next_date.strftime('%B %d, %Y')

        my_doubts = get_user_doubts(user['id'])
        my_replies = get_user_replies(user['id'])
        stats = get_user_stats(user['id'])

        return render_template(
            'profile.html',
            can_change_nickname=can_change,
            days_until_change=days_left,
            next_change_date=next_change_date,
            my_doubts=my_doubts,
            my_replies=my_replies,
            stats=stats,
        )

    # ── Change Nickname
    @app.route('/profile/nickname', methods=['POST'], endpoint='main.change_nickname')
    @login_required
    def change_nickname():
        from models import can_change_nickname, is_nickname_available, set_nickname
        user = g.current_user

        can_change, _ = can_change_nickname(user)
        if not can_change:
            flash("You can only change your nickname once every 30 days.", "error")
            return redirect(url_for('main.profile'))

        nickname = request.form.get('nickname', '').strip()
        if not nickname or len(nickname) < 3 or len(nickname) > 30:
            flash("Nickname must be 3–30 characters.", "error")
            return redirect(url_for('main.profile'))

        if not re.match(r'^[a-zA-Z0-9_]+$', nickname):
            flash("Only letters, numbers, and underscores allowed.", "error")
            return redirect(url_for('main.profile'))

        if not is_nickname_available(nickname, exclude_user_id=user['id']):
            flash("That nickname is already taken.", "error")
            return redirect(url_for('main.profile'))

        try:
            set_nickname(user['id'], nickname)
            flash(f"Nickname changed to @{nickname} ✅", "success")
        except Exception as e:
            app.logger.error(f"Nickname change error: {e}")
            flash("Could not change nickname. Please try again.", "error")

        return redirect(url_for('main.profile'))





# ============================================================
# ERROR HANDLERS
# ============================================================

def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(e):
        return render_template(
            'auth_error.html',
            error_message="Page not found. The doubt or reply you're looking for doesn't exist.",
            padikku_url=url_for('main.index')
        ), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template(
            'auth_error.html',
            error_message="Access denied. You don't have permission to view this page.",
            padikku_url=url_for('main.index')
        ), 403

    @app.errorhandler(500)
    def server_error(e):
        app.logger.error(f"500 error: {e}")
        return render_template(
            'auth_error.html',
            error_message="Something went wrong on our end. Please try again in a moment.",
            padikku_url=url_for('main.index')
        ), 500


# ============================================================
# WSGI ENTRYPOINT
# ============================================================

app = create_app()

if __name__ == '__main__':
    debug = os.environ.get('DEBUG', 'true').lower() == 'true'
    app.run(debug=debug, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
