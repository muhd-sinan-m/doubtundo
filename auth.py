"""
auth.py — JWT SSO handler for doubtundo.app
Verifies JWT tokens from padikkunnundo.in
"""

import os
import re
from datetime import datetime, timezone

import jwt
from flask import (
    Blueprint, request, redirect, url_for,
    session, render_template, current_app, flash, abort, jsonify
)

from models import (
    get_user_by_padikku_id, get_user_by_id,
    get_or_create_user, set_nickname, is_nickname_available,
    SUBJECTS
)

auth_bp = Blueprint('auth_bp', __name__)


def get_jwt_secret():
    secret = os.environ.get('JWT_SECRET')
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable not set")
    return secret


def get_padikku_url():
    return os.environ.get('PADIKKU_BASE_URL', 'https://padikkunnundo.in')


@auth_bp.route('/auth')
def sso_handler():
    """SSO entry point — handles incoming JWT tokens."""
    token = request.args.get('token', '').strip()
    padikku_url = get_padikku_url()

    if not token:
        return render_template(
            'auth_error.html',
            error_message="No authentication token provided. Please sign in again.",
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
            error_message="Your sign-in link has expired. Please request a fresh link.",
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
            error_message="Authentication service unavailable. Please contact the administrator.",
            padikku_url=padikku_url
        ), 500

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
            error_message="An unexpected error occurred. Please try again.",
            padikku_url=padikku_url
        ), 500

    session.permanent = True
    session['user_id'] = str(user['id'])
    session['email'] = email
    session.modified = True

    if not user.get('nickname'):
        flash("Successfully authenticated! Please choose a nickname to complete your profile.", "success")
        return redirect(url_for('main.index'))

    flash("Welcome back, @" + user['nickname'] + "! 👋", "success")
    return redirect(url_for('main.index'))


@auth_bp.route('/setup-nickname', methods=['GET', 'POST'])
def setup_nickname():
    """First-visit nickname setup."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth_bp.login_redirect'))

    user = get_user_by_id(user_id)

    if request.method == 'POST':
        nickname = request.form.get('nickname', '').strip()

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

    if user and user.get('nickname'):
        return redirect(url_for('main.index'))

    return render_template('setup_nickname.html')


@auth_bp.route('/check-nickname')
def check_nickname():
    """AJAX endpoint to check nickname availability."""
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
    """Clear session and redirect to home."""
    session.clear()
    return redirect(url_for('main.index'))


@auth_bp.route('/login')
def login_redirect():
    """SSO redirect handler."""
    user_id = session.get('user_id')
    if user_id:
        user = get_user_by_id(user_id)
        if user and not user.get('nickname'):
            return redirect(url_for('auth_bp.setup_nickname'))

    is_prod = os.environ.get('FLASK_ENV') == 'production' or (os.environ.get('JWT_SECRET') and not current_app.config.get('DEBUG'))
    if is_prod:
        padikku_url = get_padikku_url()
        return redirect(f"{padikku_url}/go-to-doubtundo")
    return redirect(url_for('auth_bp.dev_login'))


@auth_bp.route('/dev-login', methods=['GET', 'POST'])
def dev_login():
    """Development / test login endpoint (disabled in production)."""
    is_prod = os.environ.get('FLASK_ENV') == 'production' and not current_app.config.get('DEBUG')
    if is_prod:
        abort(404)

    if request.method == 'POST':
        nickname = request.form.get('nickname', '').strip()
        role = request.form.get('role', 'student')

        if not nickname or not re.match(r'^[a-zA-Z0-9_]{2,30}$', nickname):
            return render_template('dev_login.html', error="Invalid nickname. Use 2–30 letters, numbers, underscores.")

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
            return render_template('dev_login.html', error="An unexpected error occurred during login. Please try again.")

    return render_template('dev_login.html')

