"""Card business logic — image handling, quote selection, save/delete."""

import os
import time
import random
import uuid
import base64
from datetime import date

import requests
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import Card, Habit, Record


# ── Image helpers ──────────────────────────────────────────────


def save_image_locally(src_url, user_id, target_date_val):
    """Download external image → ``static/cards/<user_id>/<date>.jpg``.

    Returns local ``/static/…`` URL (with cache-busting timestamp) or empty string.
    """
    try:
        if not src_url or not src_url.startswith('http'):
            return ''
        from flask import current_app as app, url_for
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


def save_image_bytes_to_local(image_bytes, user_id, target_date_val):
    """Write raw bytes → local file.  Returns URL or empty string."""
    try:
        if not image_bytes:
            return ''
        from flask import current_app as app, url_for
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


def save_temp_image(from_url, user_id):
    """Download to ``static/cards/tmp/<user_id>/``.  Returns local URL or ``''``."""
    try:
        from flask import current_app as app, url_for
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


def decode_image_data(incoming_image_data):
    """Decode base64 image data (with or without ``data:`` prefix).  Returns bytes."""
    if not incoming_image_data:
        return None
    if incoming_image_data.startswith('data:'):
        _, b64data = incoming_image_data.split(',', 1)
    else:
        b64data = incoming_image_data
    return base64.b64decode(b64data)


# ── Business logic ─────────────────────────────────────────────


def get_or_create_card_meta(user_id, target_date=None):
    """Return ``(exists, card_id)`` for a given date."""
    if target_date is None:
        target_date = date.today()
    card = db.session.query(Card).filter_by(user_id=user_id, date=target_date).first()
    return bool(card), card.id if card else None


def save_card(user_id, data):
    """Create or update a card.  Returns a result dict.

    *data* keys: ``selected_date``, ``overwrite``, ``image_url``, ``image_data``,
    ``quote``, ``weekday``, ``streak``, ``habits_completed``.
    """
    selected_date_str = data.get('selected_date')
    try:
        target_date = date.fromisoformat(selected_date_str) if selected_date_str else date.today()
    except Exception:
        target_date = date.today()

    existing = db.session.query(Card).filter_by(user_id=user_id, date=target_date).first()
    overwrite = bool(data.get('overwrite'))

    if existing and not overwrite:
        return {'created': False, 'reason': 'exists'}

    incoming_image_url = data.get('image_url')
    incoming_image_data = data.get('image_data')

    saved_url = ''
    if incoming_image_data:
        try:
            image_bytes = decode_image_data(incoming_image_data)
            saved_url = save_image_bytes_to_local(image_bytes, user_id, target_date)
        except Exception:
            saved_url = ''
    if not saved_url and incoming_image_url:
        saved_url = save_image_locally(incoming_image_url, user_id, target_date)

    if existing and overwrite:
        existing.image_url = saved_url or incoming_image_url
        existing.quote = data.get('quote')
        existing.weekday = data.get('weekday')
        existing.streak = int(data.get('streak') or 0)
        existing.habits_completed = int(data.get('habits_completed') or 0)
        db.session.commit()
        return {'updated': True, 'card_id': existing.id}

    card = Card(
        user_id=user_id,
        date=target_date,
        image_url=saved_url or incoming_image_url,
        quote=data.get('quote'),
        weekday=data.get('weekday'),
        streak=int(data.get('streak') or 0),
        habits_completed=int(data.get('habits_completed') or 0),
    )
    db.session.add(card)
    db.session.commit()
    return {'created': True, 'card_id': card.id}


def delete_card_by_id(card_id, user_id):
    """Delete a card.  Raises ``ValueError`` if not found."""
    card = db.session.query(Card).filter_by(id=card_id, user_id=user_id).first()
    if not card:
        raise ValueError('Not found')
    db.session.delete(card)
    db.session.commit()


def get_card_stack(user_id):
    """Return list of card dicts for the card stack template."""
    try:
        cards = (db.session.query(Card)
                 .filter_by(user_id=user_id)
                 .order_by(Card.date.desc(), Card.id.desc())
                 .all())
    except OperationalError:
        db.create_all()
        cards = (db.session.query(Card)
                 .filter_by(user_id=user_id)
                 .order_by(Card.date.desc(), Card.id.desc())
                 .all())

    payload = []
    for c in cards:
        payload.append({
            'id': c.id,
            'date': c.date.strftime('%b %d, %Y'),
            'weekday': c.weekday or c.date.strftime('%a'),
            'quote': c.quote or '',
            'streak_days': c.streak,
            'completed_habits': c.habits_completed,
            'image_url': c.image_url or '',
        })
    return payload


def get_card_page_data(user_id):
    """Return data needed by the ``new_card`` page: streak, habits count, etc."""
    today = date.today()

    first_record = db.session.query(Record).join(Habit).filter(
        Habit.user_id == user_id
    ).order_by(Record.checkin_date.asc()).first()

    streak_days = 0
    earliest_checkin_date_iso = today.isoformat()
    if first_record:
        streak_days = (today - first_record.checkin_date).days + 1
        earliest_checkin_date_iso = first_record.checkin_date.isoformat()

    completed_today = db.session.query(Record).join(Habit).filter(
        Habit.user_id == user_id,
        Record.checkin_date == today
    ).count()

    return {
        'today_date': today.strftime('%B %d, %Y'),
        'weekday': today.strftime('%A'),
        'streak_days': streak_days,
        'earliest_checkin_date': earliest_checkin_date_iso,
        'completed_today': completed_today,
        'motivational_quote': "Every day is a new opportunity to be better.",
    }


import json
from pathlib import Path

# ── Quote / image API ─────────────────────────────────────────


def _load_quotes() -> dict[str, list[str]]:
    """Load quotes from ``app/data/quotes.json``."""
    path = Path(__file__).resolve().parent.parent / "data" / "quotes.json"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}  # graceful fallback


_QUOTES = _load_quotes()
_FALLBACK_QUOTES = {
    'motivation': ["Every day is a new opportunity to be better."],
    'success': ["Success is not final, failure is not fatal."],
    'persistence': ["Fall seven times, stand up eight."],
    'growth': ["Growth begins at the end of your comfort zone."],
    'daily': ["Today is a good day to have a good day."],
}


def get_random_quote(category='motivation'):
    """Return a random quote from the given category."""
    cat = _QUOTES.get(category) or _FALLBACK_QUOTES.get(category) or _FALLBACK_QUOTES['motivation']
    return random.choice(cat)


_CATEGORY_MAPPING = {
    'nature': ['nature', 'landscape', 'forest', 'mountain', 'ocean', 'sky'],
    'abstract': ['abstract', 'pattern', 'texture', 'art', 'design'],
    'minimal': ['minimal', 'simple', 'clean', 'white', 'space'],
    'gradient': ['gradient', 'color', 'background', 'smooth'],
    'geometric': ['geometric', 'shapes', 'pattern', 'architecture'],
}


def fetch_image(category, user_id):
    """Fetch a random image via Pexels (or Picsum fallback) and save locally.

    Returns ``(local_url, source_name, photographer)``.
    """
    pexels_key = os.environ.get('PEXELS_API_KEY', '')
    search_terms = _CATEGORY_MAPPING.get(category, ['motivation', 'inspiration'])
    search_term = random.choice(search_terms)

    if pexels_key:
        headers = {'Authorization': pexels_key}
        params = {
            'query': search_term,
            'per_page': 20,
            'page': random.randint(1, 3),
            'orientation': 'square',
        }
        resp = requests.get(
            'https://api.pexels.com/v1/search',
            headers=headers,
            params=params,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get('photos'):
                photo = random.choice(data['photos'])
                img_url = (
                    photo['src'].get('medium')
                    or photo['src'].get('small')
                    or photo['src'].get('original')
                )
                if img_url:
                    tmp_url = save_temp_image(img_url, user_id)
                    if tmp_url:
                        return tmp_url, 'pexels-local', photo.get('photographer', '')

    fallback = f"https://picsum.photos/400/400?random={random.randint(1, 1000)}"
    tmp_url = save_temp_image(fallback, user_id)
    if tmp_url:
        return tmp_url, 'picsum-local', ''
    return fallback, 'picsum', ''
