"""Cards blueprint — achievement card creation, API endpoints, and card stack."""

from flask import Blueprint, render_template, jsonify, redirect, url_for, flash, request
from flask_login import current_user, login_required
from datetime import date
from sqlalchemy.exc import OperationalError
import os
import time
import requests
import random
import base64
import uuid

from app.extensions import db, csrf
from app.models import Card, Habit, Record

cards_bp = Blueprint('cards', __name__, url_prefix='/cards')


# ── Internal helpers ──────────────────────────────────────────


def _save_image_locally(src_url, user_id, target_date_val):
    """Download external image and store under static/cards/<user_id>/<date>.jpg.
    Return a /static/... URL with ?v=<ts> to bypass cache, or empty string on failure."""
    try:
        if not src_url or not src_url.startswith('http'):
            return ''
        from flask import current_app as app
        user_dir = os.path.join(app.root_path, 'static', 'cards', str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        ts = int(time.time())
        filename = f"{target_date_val.isoformat()}.jpg"
        filepath = os.path.join(user_dir, filename)
        resp = requests.get(src_url, timeout=15)
        resp.raise_for_status()
        with open(filepath, 'wb') as f:
            f.write(resp.content)
        return url_for('static', filename=f'cards/{user_id}/{filename}') + f'?v={ts}'
    except Exception:
        return ''


def _save_image_bytes_to_local(image_bytes, user_id, target_date_val):
    """Write raw image bytes to local file, return URL or empty string."""
    try:
        if not image_bytes:
            return ''
        from flask import current_app as app
        user_dir = os.path.join(app.root_path, 'static', 'cards', str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        ts = int(time.time())
        filename = f"{target_date_val.isoformat()}.jpg"
        filepath = os.path.join(user_dir, filename)
        with open(filepath, 'wb') as f:
            f.write(image_bytes)
        return url_for('static', filename=f'cards/{user_id}/{filename}') + f'?v={ts}'
    except Exception:
        return ''


def _save_temp_image(from_url, user_id):
    """Download an image to a temp location under static/cards/tmp/<user_id>/.
    Return local URL or empty string."""
    try:
        from flask import current_app as app
        tmp_dir = os.path.join(app.root_path, 'static', 'cards', 'tmp', str(user_id))
        os.makedirs(tmp_dir, exist_ok=True)
        filename = uuid.uuid4().hex + '.jpg'
        filepath = os.path.join(tmp_dir, filename)
        resp = requests.get(from_url, timeout=12)
        resp.raise_for_status()
        with open(filepath, 'wb') as f:
            f.write(resp.content)
        return url_for('static', filename=f'cards/tmp/{user_id}/{filename}')
    except Exception:
        return ''


# ── Routes ────────────────────────────────────────────────────


@cards_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_card():
    try:
        today = date.today()
        first_record = db.session.query(Record).join(Habit).filter(
            Habit.user_id == current_user.id
        ).order_by(Record.checkin_date.asc()).first()

        streak_days = 0
        earliest_checkin_date_iso = today.isoformat()
        if first_record:
            streak_days = (today - first_record.checkin_date).days + 1
            earliest_checkin_date_iso = first_record.checkin_date.isoformat()

        today_completed_habits = db.session.query(Record).join(Habit).filter(
            Habit.user_id == current_user.id,
            Record.checkin_date == today
        ).count()

        return render_template('new_card.html',
                               title='Create Achievement Card',
                               today_date=today.strftime('%B %d, %Y'),
                               weekday=today.strftime('%A'),
                               streak_days=streak_days,
                               earliest_checkin_date=earliest_checkin_date_iso,
                               completed_today=today_completed_habits,
                               motivational_quote="Every day is a new opportunity to be better.")
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to load card page: {str(e)}', 'danger')
        return redirect(url_for('main.home'))


@cards_bp.route('/api/card/save', methods=['POST'])
@csrf.exempt
@login_required
def save_card_api():
    try:
        data = request.get_json(force=True)
        selected_date_str = data.get('selected_date')
        try:
            target_date = date.fromisoformat(selected_date_str) if selected_date_str else date.today()
        except Exception:
            target_date = date.today()

        existing = db.session.query(Card).filter_by(user_id=current_user.id, date=target_date).first()
        overwrite = bool(data.get('overwrite'))
        if existing and not overwrite:
            return jsonify({'success': False, 'reason': 'exists'}), 200

        incoming_image_url = data.get('image_url')
        incoming_image_data = data.get('image_data')

        if existing and overwrite:
            saved_url = ''
            if incoming_image_data:
                try:
                    if incoming_image_data.startswith('data:'):
                        _, b64data = incoming_image_data.split(',', 1)
                    else:
                        b64data = incoming_image_data
                    image_bytes = base64.b64decode(b64data)
                    saved_url = _save_image_bytes_to_local(image_bytes, current_user.id, target_date)
                except Exception:
                    saved_url = ''
            if not saved_url and incoming_image_url:
                saved_url = _save_image_locally(incoming_image_url, current_user.id, target_date)
            if saved_url:
                existing.image_url = saved_url
            elif incoming_image_url:
                existing.image_url = incoming_image_url
            existing.quote = data.get('quote')
            existing.weekday = data.get('weekday')
            existing.streak = int(data.get('streak') or 0)
            existing.habits_completed = int(data.get('habits_completed') or 0)
            db.session.commit()
            return jsonify({'success': True, 'updated': True, 'card_id': existing.id})

        # New record
        saved_url = ''
        if incoming_image_data:
            try:
                if incoming_image_data.startswith('data:'):
                    _, b64data = incoming_image_data.split(',', 1)
                else:
                    b64data = incoming_image_data
                image_bytes = base64.b64decode(b64data)
                saved_url = _save_image_bytes_to_local(image_bytes, current_user.id, target_date)
            except Exception:
                saved_url = ''
        if not saved_url and incoming_image_url:
            saved_url = _save_image_locally(incoming_image_url, current_user.id, target_date)

        card = Card(
            user_id=current_user.id,
            date=target_date,
            image_url=saved_url or incoming_image_url,
            quote=data.get('quote'),
            weekday=data.get('weekday'),
            streak=int(data.get('streak') or 0),
            habits_completed=int(data.get('habits_completed') or 0)
        )
        db.session.add(card)
        db.session.commit()
        return jsonify({'success': True, 'created': True, 'card_id': card.id})

    except OperationalError as oe:
        if 'no such table' in str(oe).lower():
            db.create_all()
            try:
                data = request.get_json(force=True)
                today = date.today()
                existing = db.session.query(Card).filter_by(user_id=current_user.id, date=today).first()
                overwrite = bool(data.get('overwrite'))
                if existing and not overwrite:
                    return jsonify({'success': False, 'reason': 'exists'}), 200
                if existing and overwrite:
                    existing.quote = data.get('quote')
                    existing.weekday = data.get('weekday')
                    existing.streak = int(data.get('streak') or 0)
                    existing.habits_completed = int(data.get('habits_completed') or 0)
                    db.session.commit()
                    return jsonify({'success': True, 'updated': True, 'card_id': existing.id})
                card = Card(
                    user_id=current_user.id, date=today,
                    image_url=data.get('image_url'), quote=data.get('quote'),
                    weekday=data.get('weekday'), streak=int(data.get('streak') or 0),
                    habits_completed=int(data.get('habits_completed') or 0)
                )
                db.session.add(card)
                db.session.commit()
                return jsonify({'success': True, 'created': True, 'card_id': card.id})
            except Exception as e2:
                db.session.rollback()
                return jsonify({'success': False, 'error': str(e2)}), 500
        db.session.rollback()
        return jsonify({'success': False, 'error': str(oe)}), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@cards_bp.route('/api/card/today')
@csrf.exempt
@login_required
def get_today_card_meta():
    try:
        selected_date_str = request.args.get('selected_date')
        try:
            target_date = date.fromisoformat(selected_date_str) if selected_date_str else date.today()
        except Exception:
            target_date = date.today()
        existing = db.session.query(Card).filter_by(user_id=current_user.id, date=target_date).first()
        return jsonify({'exists': bool(existing), 'card_id': existing.id if existing else None})
    except OperationalError as oe:
        if 'no such table' in str(oe).lower():
            db.create_all()
            selected_date_str = request.args.get('selected_date')
            try:
                target_date = date.fromisoformat(selected_date_str) if selected_date_str else date.today()
            except Exception:
                target_date = date.today()
            existing = db.session.query(Card).filter_by(user_id=current_user.id, date=target_date).first()
            return jsonify({'exists': bool(existing), 'card_id': existing.id if existing else None})
        return jsonify({'exists': False, 'error': str(oe)}), 500


@cards_bp.route('/api/card/image/<category>')
@login_required
def get_card_image(category):
    try:
        pexels_api_key = os.environ.get('PEXELS_API_KEY', '')

        category_mapping = {
            'nature': ['nature', 'landscape', 'forest', 'mountain', 'ocean', 'sky'],
            'abstract': ['abstract', 'pattern', 'texture', 'art', 'design'],
            'minimal': ['minimal', 'simple', 'clean', 'white', 'space'],
            'gradient': ['gradient', 'color', 'background', 'smooth'],
            'geometric': ['geometric', 'shapes', 'pattern', 'architecture']
        }
        search_terms = category_mapping.get(category, ['motivation', 'inspiration'])
        search_term = random.choice(search_terms)

        if pexels_api_key:
            headers = {'Authorization': pexels_api_key}
            params = {'query': search_term, 'per_page': 20, 'page': random.randint(1, 3), 'orientation': 'square'}
            response = requests.get('https://api.pexels.com/v1/search', headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('photos'):
                    photo = random.choice(data['photos'])
                    image_url = photo['src'].get('medium') or photo['src'].get('small') or photo['src'].get('original')
                    if image_url:
                        tmp_url = _save_temp_image(image_url, current_user.id)
                        if tmp_url:
                            return jsonify({'success': True, 'image_url': tmp_url,
                                            'photographer': photo.get('photographer', ''),
                                            'source': 'pexels-local'})

        fallback_url = f"https://picsum.photos/400/400?random={random.randint(1, 1000)}"
        tmp_url = _save_temp_image(fallback_url, current_user.id)
        if tmp_url:
            return jsonify({'success': True, 'image_url': tmp_url, 'source': 'picsum-local'})
        return jsonify({'success': True, 'image_url': fallback_url, 'source': 'picsum'})
    except Exception as e:
        fb = f"https://picsum.photos/400/400?random={random.randint(1, 1000)}"
        return jsonify({'success': True, 'image_url': fb, 'source': 'picsum', 'error': str(e)})


@cards_bp.route('/api/card/quote/<category>')
@login_required
def get_card_quote(category):
    try:
        quotes = {
            'motivation': [
                "Every day is a new opportunity to be better.",
                "Success is the sum of small efforts repeated day in and day out.",
                "The journey of a thousand miles begins with one step.",
                "Believe you can and you're halfway there.",
                "Your only limit is your mind."
            ],
            'success': [
                "Success doesn't just find you. You have to go out and get it.",
                "The harder you work for something, the greater you'll feel when you achieve it.",
                "Great things never come from comfort zones.",
                "Dream it. Wish it. Do it.",
                "Success is not final, failure is not fatal: it is the courage to continue that counts."
            ],
            'persistence': [
                "Don't stop when you're tired. Stop when you're done.",
                "It's not whether you get knocked down, it's whether you get up.",
                "Difficult roads often lead to beautiful destinations.",
                "Fall seven times, stand up eight.",
                "The difference between ordinary and extraordinary is that little extra."
            ],
            'growth': [
                "Be yourself; everyone else is already taken.",
                "You are never too old to set another goal or to dream a new dream.",
                "The only way to do great work is to love what you do.",
                "Growth begins at the end of your comfort zone.",
                "What lies behind us and what lies before us are tiny matters compared to what lies within us."
            ],
            'daily': [
                "Wake up with determination. Go to bed with satisfaction.",
                "Do something today that your future self will thank you for.",
                "Little progress is still progress.",
                "Great things take time.",
                "Today is a good day to have a good day."
            ]
        }
        category_quotes = quotes.get(category, quotes['motivation'])
        selected_quote = random.choice(category_quotes)
        return jsonify({'success': True, 'quote': selected_quote, 'category': category})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e),
                        'quote': "Every day is a new opportunity to be better."})


@cards_bp.route('/stack')
@login_required
def card_stack():
    try:
        try:
            cards = (db.session.query(Card)
                     .filter_by(user_id=current_user.id)
                     .order_by(Card.date.desc(), Card.id.desc())
                     .all())
        except OperationalError as oe:
            if 'no such table' in str(oe).lower():
                db.create_all()
                cards = (db.session.query(Card)
                         .filter_by(user_id=current_user.id)
                         .order_by(Card.date.desc(), Card.id.desc())
                         .all())
            else:
                raise

        cards_payload = []
        for c in cards:
            cards_payload.append({
                'id': c.id,
                'date': c.date.strftime('%b %d, %Y'),
                'weekday': c.weekday or c.date.strftime('%a'),
                'quote': c.quote or '',
                'streak_days': c.streak,
                'completed_habits': c.habits_completed,
                'image_url': c.image_url or ''
            })
        return render_template('card_stack.html', title='Card Stack', cards=cards_payload)
    except Exception as e:
        flash(f'Failed to load card stack: {str(e)}', 'danger')
        return redirect(url_for('auth.account'))


@cards_bp.route('/api/card/<int:card_id>', methods=['DELETE'])
@csrf.exempt
@login_required
def delete_card(card_id):
    try:
        card = db.session.query(Card).filter_by(id=card_id, user_id=current_user.id).first()
        if not card:
            return jsonify({'success': False, 'error': 'Not found'}), 404
        db.session.delete(card)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
