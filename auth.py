"""
auth.py — JWT SSO handler for doubtundo.app
Verifies JWT tokens from padikkunnundo.in
"""

import os
from datetime import datetime, timezone

import jwt
from flask import (
    Blueprint, request, redirect, url_for,
    session, render_template, current_app, flash
)

from models import (
    get_user_by_padikku_id, get_user_by_id,
    get_or_create_user, set_nickname, is_nickname_available
)

auth_bp = Blueprint('auth_bp', __name__)

from models import SUBJECTS


def get_jwt_secret():
    secret = os.environ.get('JWT_SECRET')
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable not set")
    return secret


def get_padikku_url():
    return os.environ.get('PADIKKU_BASE_URL', 'https://padikkunnundo.in')


@auth_bp.route('/auth')
def sso_handler():
    """
    SSO entry point — called from padikkunnundo.in with a JWT token.
    GET /auth?token=<HS256 JWT>
    JWT payload: { user_id, email, name, exp }
    """
    token = request.args.get('token', '').strip()
    padikku_url = get_padikku_url()

    if not token:
        return render_template(
            'auth_error.html',
            error_message="No authentication token provided. Please sign in from padikkunnundo.",
            padikku_url=padikku_url
        ), 400

    try:
        secret = get_jwt_secret()
        payload = jwt.decode(
            token,
            secret,
            algorithms=['HS256'],
            options={"require": ["exp", "user_id", "email"]}
        )
    except jwt.ExpiredSignatureError:
        return render_template(
            'auth_error.html',
            error_message="Your sign-in link has expired (5-minute window). Please request a fresh link from padikkunnundo.",
            padikku_url=padikku_url + '?error=token_expired'
        ), 401
    except jwt.InvalidTokenError as e:
        current_app.logger.warning(f"Invalid JWT received: {e}")
        return render_template(
            'auth_error.html',
            error_message="Invalid authentication token. Please try signing in again.",
            padikku_url=padikku_url + '?error=invalid_token'
        ), 401
    except RuntimeError as e:
        current_app.logger.error(f"JWT config error: {e}")
        return render_template(
            'auth_error.html',
            error_message="Authentication service misconfigured. Please contact admin.",
            padikku_url=padikku_url
        ), 500

    # Extract claims
    padikku_user_id = str(payload.get('user_id', ''))
    email = str(payload.get('email', '')).lower().strip()
    name = str(payload.get('name', email.split('@')[0]))

    if not padikku_user_id or not email:
        return render_template(
            'auth_error.html',
            error_message="Invalid token payload. Please sign in again.",
            padikku_url=padikku_url
        ), 400

    try:
        # Upsert user (create if first time, update otherwise)
        admin_emails = [
            e.strip().lower()
            for e in os.environ.get('ADMIN_EMAILS', '').split(',')
            if e.strip()
        ]
        is_admin = email in admin_emails

        user = get_or_create_user(
            padikku_user_id=padikku_user_id,
            email=email,
            name=name,
            is_admin=is_admin
        )
    except Exception as e:
        current_app.logger.error(f"DB error during auth: {e}")
        return render_template(
            'auth_error.html',
            error_message="Database error. Please try again.",
            padikku_url=padikku_url
        ), 500

    # Create session
    session.permanent = True
    session['user_id'] = str(user['id'])
    session['email'] = email  # server-side only
    session.modified = True

    # First visit? → setup nickname
    if not user.get('nickname'):
        return redirect(url_for('auth_bp.setup_nickname'))

    flash("Welcome back, @" + user['nickname'] + "! 👋", "success")
    return redirect(url_for('main.index'))


@auth_bp.route('/setup-nickname', methods=['GET', 'POST'])
def setup_nickname():
    """First-visit nickname setup page."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth_bp.login_redirect'))

    user = get_user_by_id(user_id)

    if request.method == 'POST':
        nickname = request.form.get('nickname', '').strip()

        # Validate
        import re
        if not nickname:
            return render_template('setup_nickname.html', error="Nickname is required.", old_value=nickname)
        if len(nickname) < 3 or len(nickname) > 30:
            return render_template('setup_nickname.html', error="Nickname must be 3–30 characters.", old_value=nickname)
        if not re.match(r'^[a-zA-Z0-9_]+$', nickname):
            return render_template('setup_nickname.html', error="Only letters, numbers, and underscores allowed.", old_value=nickname)

        if not is_nickname_available(nickname, exclude_user_id=user_id):
            return render_template('setup_nickname.html', error="That nickname is taken. Please choose another.", old_value=nickname)

        try:
            set_nickname(user_id, nickname)
        except Exception as e:
            current_app.logger.error(f"Nickname set error: {e}")
            return render_template('setup_nickname.html', error="Could not save nickname. Please try again.", old_value=nickname)

        flash(f"Welcome to doubtundo, @{nickname}! 🎉", "success")
        return redirect(url_for('main.index'))

    # If user already has nickname, redirect
    if user and user.get('nickname'):
        return redirect(url_for('main.index'))

    return render_template('setup_nickname.html')


@auth_bp.route('/check-nickname')
def check_nickname():
    """AJAX endpoint to check nickname availability."""
    from flask import jsonify
    from models import is_nickname_available
    import re

    nickname = request.args.get('n', '').strip()
    user_id = session.get('user_id')

    if not nickname or len(nickname) < 3 or len(nickname) > 30:
        return jsonify({'available': False, 'reason': 'invalid'})
    if not re.match(r'^[a-zA-Z0-9_]+$', nickname):
        return jsonify({'available': False, 'reason': 'invalid_chars'})

    available = is_nickname_available(nickname, exclude_user_id=user_id)
    return jsonify({'available': available})


@auth_bp.route('/logout')
def logout():
    """Clear session and redirect home."""
    session.clear()
    return redirect(url_for('main.index'))


@auth_bp.route('/login')
def login_redirect():
    """SSO redirect handler: redirects to padikkunnundo's SSO trigger endpoint in production, or dev_login in development."""
    # If in local dev mode (DEBUG is True or JWT_SECRET is not configured), allow dev-login
    is_prod = os.environ.get('FLASK_ENV') == 'production' or (os.environ.get('JWT_SECRET') and not current_app.config.get('DEBUG'))
    if is_prod:
        padikku_url = get_padikku_url()
        return redirect(f"{padikku_url}/go-to-doubtundo")
    return redirect(url_for('auth_bp.dev_login'))


@auth_bp.route('/dev-login', methods=['GET', 'POST'])
def dev_login():
    """
    Development / test login — NO padikkunnundo SSO needed.
    Lets you sign in with any nickname directly for testing.
    Remove or guard this route before production!
    """
    from flask import jsonify

    if request.method == 'POST':
        nickname = request.form.get('nickname', '').strip()
        role = request.form.get('role', 'student')

        import re
        if not nickname or not re.match(r'^[a-zA-Z0-9_]{2,30}$', nickname):
            return render_template('dev_login.html', error="Invalid nickname. Use 2–30 letters, numbers, underscores.")

        # Create/get a dev test user
        try:
            fake_email = f"{nickname.lower()}@devtest.local"
            fake_padikku_id = f"dev_{nickname.lower()}"
            is_admin = (role == 'admin')

            user = get_or_create_user(
                padikku_user_id=fake_padikku_id,
                email=fake_email,
                name=nickname,
                is_admin=is_admin
            )

            # Set nickname if not already set
            if not user.get('nickname'):
                set_nickname(user['id'], nickname)
                user['nickname'] = nickname

            session.permanent = True
            session['user_id'] = str(user['id'])
            session['email'] = fake_email
            session.modified = True

            flash(f"Signed in as @{nickname} {'(Admin)' if is_admin else ''} 🎉", "success")
            return redirect(url_for('main.index'))
        except Exception as e:
            current_app.logger.error(f"Dev login error: {e}")
            return render_template('dev_login.html', error=f"Login error: {str(e)}")

    return render_template('dev_login.html')

