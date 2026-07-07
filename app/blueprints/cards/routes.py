"""Cards blueprint — achievement card creation, API endpoints, and card stack."""

from flask import Blueprint, render_template, jsonify, redirect, url_for, flash, request
from flask_login import current_user, login_required

from app.extensions import db, csrf
from app.services.card_service import (
    get_card_page_data,
    save_card,
    get_card_stack,
    get_or_create_card_meta,
    delete_card_by_id,
    get_random_quote,
    fetch_image,
)

cards_bp = Blueprint('cards', __name__, url_prefix='/cards')


@cards_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_card():
    try:
        data = get_card_page_data(current_user.id)
        return render_template('new_card.html', title='Create Achievement Card', **data)
    except Exception as e:
        db.session.rollback()
        flash(f'Failed to load card page: {str(e)}', 'danger')
        return redirect(url_for('main.home'))


@cards_bp.route('/api/card/save', methods=['POST'])
@csrf.exempt
@login_required
def save_card_api():
    try:
        result = save_card(current_user.id, request.get_json(force=True))
        if result.get('reason') == 'exists':
            return jsonify({'success': False, 'reason': 'exists'}), 200
        return jsonify({'success': True, **result})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@cards_bp.route('/api/card/today')
@csrf.exempt
@login_required
def get_today_card_meta():
    try:
        selected_date_str = request.args.get('selected_date')
        from datetime import date
        try:
            target = date.fromisoformat(selected_date_str) if selected_date_str else date.today()
        except Exception:
            target = date.today()
        exists, card_id = get_or_create_card_meta(current_user.id, target)
        return jsonify({'exists': exists, 'card_id': card_id})
    except Exception as e:
        return jsonify({'exists': False, 'error': str(e)}), 500


@cards_bp.route('/api/card/image/<category>')
@login_required
def get_card_image(category):
    try:
        local_url, source, photographer = fetch_image(category, current_user.id)
        return jsonify({
            'success': True,
            'image_url': local_url,
            'source': source,
            'photographer': photographer,
        })
    except Exception as e:
        fb = f"https://picsum.photos/400/400?random=__import__('random').randint(1,1000)"
        return jsonify({'success': True, 'image_url': fb, 'source': 'picsum', 'error': str(e)})


@cards_bp.route('/api/card/quote/<category>')
@login_required
def get_card_quote(category):
    try:
        return jsonify({'success': True, 'quote': get_random_quote(category), 'category': category})
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'quote': 'Every day is a new opportunity to be better.',
        })


@cards_bp.route('/stack')
@login_required
def card_stack():
    try:
        return render_template('card_stack.html', title='Card Stack', cards=get_card_stack(current_user.id))
    except Exception as e:
        flash(f'Failed to load card stack: {str(e)}', 'danger')
        return redirect(url_for('auth.account'))


@cards_bp.route('/api/card/<int:card_id>', methods=['DELETE'])
@csrf.exempt
@login_required
def delete_card(card_id):
    try:
        delete_card_by_id(card_id, current_user.id)
        return jsonify({'success': True})
    except ValueError:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
