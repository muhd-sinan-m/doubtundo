"""
admin.py — Admin blueprint for doubtundo.app
All routes decorated with @admin_required
"""

from functools import wraps
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, abort, current_app
)
from models import (
    get_user_by_id, get_doubt, create_reply, get_admin_answer,
    get_all_doubts_admin, get_all_replies_admin, get_all_users_admin,
    get_admin_stats, resolve_doubt_db, hide_doubt_db, hide_reply,
    SUBJECTS
)

admin_bp = Blueprint('admin_bp', __name__, url_prefix='/admin')


def admin_required(f):
    """Decorator that checks admin status. Returns 403 for non-admins."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            return redirect(url_for('auth_bp.login_redirect'))
        user = get_user_by_id(user_id)
        if not user or not user.get('is_admin'):
            abort(403)
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/')
@admin_required
def panel():
    """Admin dashboard with doubts, replies, users tables."""
    filters = {
        'status': request.args.get('status', ''),
        'subject': request.args.get('subject', ''),
        'semester': request.args.get('semester', ''),
    }

    doubts = get_all_doubts_admin(
        status=filters['status'] or None,
        subject=filters['subject'] or None,
        semester=filters['semester'] or None,
    )
    replies = get_all_replies_admin(limit=50)
    users = get_all_users_admin()
    stats = get_admin_stats()

    return render_template(
        'admin/panel.html',
        doubts=doubts,
        replies=replies,
        users=users,
        stats=stats,
        filters=filters,
        subjects=SUBJECTS,
    )


@admin_bp.route('/doubt/<doubt_id>/answer', methods=['GET', 'POST'])
@admin_required
def admin_answer_form(doubt_id):
    """GET/POST form to create or update admin answer on a doubt."""
    user_id = session.get('user_id')
    doubt = get_doubt(doubt_id)
    if not doubt:
        abort(404)

    existing_answer = get_admin_answer(doubt_id)

    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        if not content or len(content) < 10:
            flash("Admin answer must be at least 10 characters.", "error")
            return render_template(
                'admin/doubt_admin.html',
                doubt=doubt,
                existing_answer=existing_answer
            )
        try:
            create_reply(
                doubt_id=doubt_id,
                user_id=user_id,
                content=content,
                is_admin_answer=True
            )
            flash("Admin answer posted successfully! ✅", "success")
        except Exception as e:
            current_app.logger.error(f"Admin answer error: {e}")
            flash("Error posting admin answer. Try again.", "error")

        return redirect(url_for('main.doubt_detail', doubt_id=doubt_id))

    return render_template(
        'admin/doubt_admin.html',
        doubt=doubt,
        existing_answer=existing_answer,
    )


@admin_bp.route('/doubt/<doubt_id>/resolve', methods=['POST'])
@admin_required
def resolve_doubt(doubt_id):
    """Mark a doubt as resolved."""
    try:
        resolve_doubt_db(doubt_id)
        flash("Doubt marked as resolved. ✅", "success")
    except Exception as e:
        current_app.logger.error(f"Resolve doubt error: {e}")
        flash("Error resolving doubt.", "error")
    return redirect(request.referrer or url_for('admin_bp.panel'))


@admin_bp.route('/doubt/<doubt_id>/hide', methods=['POST'])
@admin_required
def hide_doubt(doubt_id):
    """Hide a doubt from the feed."""
    try:
        hide_doubt_db(doubt_id)
        flash("Doubt hidden from feed.", "info")
    except Exception as e:
        current_app.logger.error(f"Hide doubt error: {e}")
        flash("Error hiding doubt.", "error")
    return redirect(request.referrer or url_for('admin_bp.panel'))


@admin_bp.route('/reply/<reply_id>/hide', methods=['POST'])
@admin_required
def hide_reply_route(reply_id):
    """Toggle hide/unhide a reply."""
    from models import get_db, row_to_dict

    conn = get_db()
    try:
        row = conn.execute("SELECT is_hidden FROM replies WHERE id=?", (reply_id,)).fetchone()
        if not row:
            conn.close()
            abort(404)
        current_hidden = bool(row['is_hidden'])
        conn.execute("UPDATE replies SET is_hidden=? WHERE id=?",
                     (not current_hidden, reply_id))
        conn.commit()
    finally:
        conn.close()

    action = "unhidden" if current_hidden else "hidden"
    flash(f"Reply {action}.", "info")
    return redirect(request.referrer or url_for('admin_bp.panel'))


@admin_bp.errorhandler(403)
def forbidden(e):
    return render_template('auth_error.html',
                           error_message="Access denied. Admin privileges required.",
                           padikku_url=url_for('main.index')), 403
