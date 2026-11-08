"""Card business logic — image handling, quote selection, save/delete."""

import base64
import binascii
import ipaddress
import json
import os
import random
import re
import socket
import ssl
import stat
import struct
import uuid
import zlib
from datetime import date
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

from flask import current_app, url_for
from sqlalchemy.exc import OperationalError
from urllib3 import HTTPSConnectionPool, Timeout
from urllib3.exceptions import HTTPError

from app.extensions import db
from app.models import Card, Habit, Record


# ── Image helpers ──────────────────────────────────────────────


_MAX_IMAGE_BYTES = 700 * 1024  # Base64 plus JSON fits the 1 MiB request limit.
_MAX_PIXELS = 4 * 1024 * 1024
_IMAGE_EXTENSIONS = {'image/jpeg': '.jpg', 'image/png': '.png'}
_ASSET_ID = re.compile(r'[0-9a-f]{32}')
_PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
          (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


class CardInputError(ValueError):
    """Invalid input with a fixed, safe message for the client."""


def _valid_dimensions(width, height):
    return 0 < width <= 4096 and 0 < height <= 4096 and width * height <= _MAX_PIXELS


def _jpeg_scan_end(data, scan_start):
    """Return the last FF before a marker that ends a nonempty scan, or None."""
    run_start = None
    for offset in range(scan_start, len(data)):
        value = data[offset]
        if value == 0xff:
            if run_start is None:
                run_start = offset
        elif run_start is not None:
            # Stuffed bytes and restart markers stay inside the scan.
            if value != 0x00 and not 0xd0 <= value <= 0xd7:
                if run_start == scan_start:
                    return None
                # The caller only needs the final FF and marker code.
                return offset - 1
            run_start = None
    return None


def _is_jpeg(data):
    """Recognize a complete baseline or progressive JPEG marker stream."""
    offset = 2
    components = set()
    saw_scan = False
    has_quantization = False
    has_huffman = False
    while offset < len(data) and data[offset] == 0xff:
        while offset < len(data) and data[offset] == 0xff:
            offset += 1
        if offset == len(data):
            break
        marker = data[offset]
        offset += 1
        if marker == 0xd9:
            return saw_scan and offset == len(data)
        if marker not in (0xc0, 0xc1, 0xc2, 0xc4, 0xda, 0xdb, 0xdd, 0xfe) and not 0xe0 <= marker <= 0xef:
            break
        length = int.from_bytes(data[offset:offset + 2], 'big')
        if length < 2 or offset + length > len(data):
            break
        segment = data[offset + 2:offset + length]
        offset += length
        if marker in (0xc0, 0xc1, 0xc2):
            if components or len(segment) < 6:
                break
            precision, height, width, count = struct.unpack('>BHHB', segment[:6])
            if precision != 8 or count not in (1, 3, 4) or len(segment) != 6 + 3 * count:
                break
            components = set(segment[6::3])
            if len(components) != count or not _valid_dimensions(width, height):
                break
        elif marker == 0xdb:
            if len(segment) < 65:
                break
            has_quantization = True
        elif marker == 0xc4:
            if len(segment) < 18 or sum(segment[1:17]) > len(segment) - 17:
                break
            has_huffman = True
        elif marker == 0xda:
            count = segment[0] if segment else 0
            scan_components = set(segment[1:1 + 2 * count:2])
            if (not has_quantization or not has_huffman or not count or len(segment) != 4 + 2 * count
                    or len(scan_components) != count or not scan_components <= components):
                break
            scan_end = _jpeg_scan_end(data, offset)
            if scan_end is None:
                break
            saw_scan = True
            offset = scan_end
    return False


def _png_pixels_match(compressed, width, height, bits_per_pixel, interlaced):
    # Bound the output before decompressing and check each scanline's filter byte.
    passes = _ADAM7 if interlaced else ((0, 0, 1, 1),)
    rows = []
    for x, y, dx, dy in passes:
        columns = (width - x + dx - 1) // dx
        count = (height - y + dy - 1) // dy
        if columns > 0 and count > 0:
            rows.append((count, 1 + (columns * bits_per_pixel + 7) // 8))
    expected = sum(count * stride for count, stride in rows)
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(compressed, expected + 1)
    if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        return False
    offset = 0
    for count, stride in rows:
        end = offset + count * stride
        if any(filter_byte > 4 for filter_byte in pixels[offset:end:stride]):
            return False
        offset = end
    return True


def _is_png(data):
    offset = len(_PNG_SIGNATURE)
    header = None
    palette = False
    compressed = bytearray()
    after_idat = False
    while offset + 12 <= len(data):
        length, kind = struct.unpack_from('>I4s', data, offset)
        end = offset + 12 + length
        if end > len(data):
            return False
        chunk = data[offset + 8:end - 4]
        if zlib.crc32(data[offset + 4:end - 4]) != struct.unpack_from('>I', data, end - 4)[0]:
            return False
        offset = end
        if header is None:
            if kind != b'IHDR' or length != 13:
                return False
            width, height, depth, color, method, filtering, interlaced = struct.unpack('>IIBBBBB', chunk)
            if (not _valid_dimensions(width, height) or depth not in _PNG_DEPTHS.get(color, ())
                    or method or filtering or interlaced not in (0, 1)):
                return False
            header = (width, height, depth * _PNG_CHANNELS[color], interlaced)
        elif kind == b'PLTE':
            if palette or compressed or not length or length > 768 or length % 3 or color in (0, 4):
                return False
            palette = True
        elif kind == b'IDAT':
            if after_idat or (color == 3 and not palette):
                return False
            compressed.extend(chunk)
        elif kind == b'IEND':
            return length == 0 and end == len(data) and bool(compressed) and _png_pixels_match(compressed, *header)
        elif not kind.isalpha() or not kind[:1].islower():
            return False
        elif compressed:
            after_idat = True
    return False


def _image_type(data, declared_type=None):
    if not data or len(data) > _MAX_IMAGE_BYTES:
        raise CardInputError('invalid_image_data')
    try:
        if data.startswith(b'\xff\xd8') and _is_jpeg(data):
            media_type = 'image/jpeg'
        elif data.startswith(_PNG_SIGNATURE) and _is_png(data):
            media_type = 'image/png'
        else:
            raise CardInputError('invalid_image_data')
    except zlib.error:
        raise CardInputError('invalid_image_data') from None
    if declared_type is not None and declared_type != media_type:
        raise CardInputError('invalid_image_data')
    return media_type


def _decode_image_data(value):
    if not isinstance(value, str):
        raise CardInputError('invalid_image_data')
    media_type = None
    if value.startswith('data:'):
        header, _, value = value.partition(',')
        media_type = header.removeprefix('data:').removesuffix(';base64')
        if header != f'data:{media_type};base64' or media_type not in _IMAGE_EXTENSIONS:
            raise CardInputError('invalid_image_data')
    if len(value) > ((_MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise CardInputError('invalid_image_data')
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise CardInputError('invalid_image_data') from None
    return data, _image_type(data, media_type)


def _is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _card_directory(*parts, create=False):
    root = Path(current_app.static_folder).absolute()
    if root.resolve(strict=True) != root or _is_link(root.lstat()):
        raise OSError('invalid_card_directory')
    directory = root
    for part in ('cards', *parts):
        directory /= part
        if create:
            directory.mkdir(exist_ok=True)
        info = directory.lstat()
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise OSError('invalid_card_directory')
    return directory


def _read_temp_image(image_id, user_id):
    if not isinstance(image_id, str) or not _ASSET_ID.fullmatch(image_id):
        raise CardInputError('invalid_image_id')
    try:
        directory = _card_directory('tmp', str(user_id))
        for media_type, extension in _IMAGE_EXTENSIONS.items():
            path = directory / f'{image_id}{extension}'
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if (_is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or path.resolve(strict=True) != path or info.st_size > _MAX_IMAGE_BYTES):
                break
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
            with os.fdopen(os.open(path, flags), 'rb') as image:
                if not os.path.samestat(info, os.fstat(image.fileno())):
                    break
                data = image.read(_MAX_IMAGE_BYTES + 1)
            return data, _image_type(data, media_type)
    except (OSError, CardInputError):
        pass
    raise CardInputError('invalid_image_id')


def _store_image(data, media_type, *parts, prefix=''):
    directory = _card_directory(*parts, create=True)
    filename = f'{prefix}{uuid.uuid4().hex}{_IMAGE_EXTENSIONS[media_type]}'
    path = directory / filename
    image_url = url_for('static', filename='/'.join(('cards', *parts, filename)))
    image = path.open('xb')
    try:
        with image:
            image.write(data)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return path, image_url


# ── Business logic ─────────────────────────────────────────────


def get_or_create_card_meta(user_id, target_date=None):
    """Return ``(exists, card_id)`` for a given date."""
    if target_date is None:
        target_date = date.today()
    card = db.session.query(Card).filter_by(user_id=user_id, date=target_date).first()
    return bool(card), card.id if card else None


def save_card(user_id, data):
    """Save validated bytes, an owned temporary image, or a card with no image."""
    if not isinstance(data, dict):
        raise CardInputError('invalid_card_data')
    if data.get('image_url') not in (None, ''):
        raise CardInputError('image_url_not_supported')
    image_data = data.get('image_data')
    image_id = data.get('image_id')
    has_data = image_data not in (None, '')
    has_id = image_id not in (None, '')
    if has_data and has_id:
        raise CardInputError('multiple_image_sources')

    selected_date = data.get('selected_date')
    overwrite = data.get('overwrite', False)
    if not isinstance(overwrite, bool):
        raise CardInputError('invalid_card_data')
    try:
        target_date = date.fromisoformat(selected_date) if selected_date not in (None, '') else date.today()
        streak = int(data.get('streak') or 0)
        habits_completed = int(data.get('habits_completed') or 0)
    except (TypeError, ValueError, OverflowError):
        raise CardInputError('invalid_card_data') from None
    for value, limit in ((data.get('quote'), 255), (data.get('weekday'), 10)):
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise CardInputError('invalid_card_data')

    existing = db.session.query(Card).filter_by(user_id=user_id, date=target_date).first()
    if existing and not overwrite:
        return {'created': False, 'reason': 'exists'}

    image = _decode_image_data(image_data) if has_data else None
    if has_id:
        image = _read_temp_image(image_id, user_id)

    new_path = None
    try:
        saved_url = None
        if image:
            new_path, saved_url = _store_image(*image, str(user_id), prefix=f'{target_date.isoformat()}_')
        card = existing if existing is not None else Card(user_id=user_id, date=target_date)
        card.image_url = saved_url
        card.quote = data.get('quote')
        card.weekday = data.get('weekday')
        card.streak = streak
        card.habits_completed = habits_completed
        db.session.add(card)
        db.session.flush()
        card_id = card.id
        db.session.commit()
    except Exception:
        if new_path is not None:
            try:
                new_path.unlink(missing_ok=True)
            except OSError:
                current_app.logger.warning('Card image cleanup failed after save error.')
        raise
    return {'updated' if existing is not None else 'created': True, 'card_id': card_id}


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
        from flask import current_app
        if current_app.testing:
            raise
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


_PEXELS_API = 'https://api.pexels.com/v1/search'
_PICSUM_IMAGE = 'https://picsum.photos/400/400'
_MAX_PROVIDER_JSON_BYTES = 512 * 1024
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_SPECIAL_IPV4 = tuple(map(ipaddress.ip_network, ('192.0.0.0/24', '192.88.99.0/24')))


class ProviderError(ValueError):
    """The provider request or response did not meet the image policy."""


def _provider_target(url, domain):
    if (not isinstance(url, str) or not url.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 or char == '\\' for char in url)):
        raise ProviderError('invalid_provider_url')
    parts = urlsplit(url)
    host = parts.hostname
    if (parts.scheme != 'https' or not host or parts.fragment
            or parts.netloc.lower() not in (host, f'{host}:443')
            or not (host == domain or host.endswith('.' + domain))):
        raise ProviderError('invalid_provider_url')
    path = parts.path or '/'
    return host, f'{path}?{parts.query}' if parts.query else path


def _resolve_provider_ip(host):
    answers = socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    addresses = [ipaddress.IPv4Address(answer[4][0]) for answer in answers]
    if not addresses or any(
        not ip.is_global or ip.is_multicast or ip.is_reserved or any(ip in net for net in _SPECIAL_IPV4)
        for ip in addresses
    ):
        raise ProviderError('invalid_provider_address')
    return str(addresses[0])


def _read_provider_body(response, limit):
    if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
        raise ProviderError('invalid_provider_encoding')
    declared = response.headers.get('Content-Length')
    if declared is not None:
        declared = int(declared)
        if not 0 < declared <= limit:
            raise ProviderError('invalid_provider_length')
    body = bytearray()
    for chunk in response.stream(16 * 1024, decode_content=False):
        if len(body) + len(chunk) > limit:
            raise ProviderError('invalid_provider_length')
        body.extend(chunk)
    if declared is not None and declared != len(body):
        raise ProviderError('invalid_provider_length')
    return bytes(body)


def _get_provider(url, domain, media_types, limit, *, redirects=0, authorization=None):
    for hop in range(redirects + 1):
        host, path = _provider_target(url, domain)
        address = _resolve_provider_ip(host)
        headers = {'Host': host, 'Accept': ', '.join(media_types), 'Accept-Encoding': 'identity'}
        if authorization is not None:
            headers['Authorization'] = authorization
        # Dial the vetted numeric address directly, keeping the original TLS and HTTP identity.
        # A direct pool never reads proxy configuration from the environment.
        with HTTPSConnectionPool(
            address, port=443, server_hostname=host, assert_hostname=host,
            ssl_context=ssl.create_default_context(), timeout=Timeout(connect=4, read=5),
        ) as pool:
            with pool.urlopen(
                'GET', path, headers=headers, preload_content=False, decode_content=False,
                redirect=False, retries=False,
            ) as response:
                if response.status in _REDIRECT_STATUSES and hop < redirects and authorization is None:
                    location = response.headers.get('Location')
                    if not location or any(ord(char) <= 32 for char in location):
                        raise ProviderError('invalid_provider_redirect')
                    url = urljoin(url, location)
                    continue
                media_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                if response.status != 200 or media_type not in media_types:
                    raise ProviderError('invalid_provider_response')
                return _read_provider_body(response, limit), media_type
    raise ProviderError('invalid_provider_redirect')


def fetch_image(category, user_id):
    """Fetch and validate a provider image before issuing a local asset ID."""
    pexels_key = os.environ.get('PEXELS_API_KEY')
    if pexels_key:
        query = urlencode({
            'query': random.choice(_CATEGORY_MAPPING.get(category, ['motivation', 'inspiration'])),
            'per_page': 20, 'page': random.randint(1, 3), 'orientation': 'square',
        })
        try:
            response, _ = _get_provider(
                f'{_PEXELS_API}?{query}', 'api.pexels.com', ('application/json',),
                _MAX_PROVIDER_JSON_BYTES, authorization=pexels_key,
            )
            result = json.loads(response)
            photos = result.get('photos') if isinstance(result, dict) else None
            if isinstance(photos, list) and photos:
                photo = random.choice(photos)
                sources = photo.get('src') if isinstance(photo, dict) else None
                if isinstance(sources, dict):
                    image_url = sources.get('medium') or sources.get('small') or sources.get('original')
                    data, media_type = _get_provider(image_url, 'pexels.com', _IMAGE_EXTENSIONS, _MAX_IMAGE_BYTES)
                    _image_type(data, media_type)
                    _, local_url = _store_image(data, media_type, 'tmp', str(user_id))
                    photographer = photo.get('photographer')
                    return local_url, 'pexels-local', photographer if isinstance(photographer, str) else ''
        except (ValueError, OSError, HTTPError):
            current_app.logger.info('Pexels image unavailable; trying Picsum.')

    try:
        data, media_type = _get_provider(
            f'{_PICSUM_IMAGE}?random={random.randint(1, 1000)}', 'picsum.photos',
            _IMAGE_EXTENSIONS, _MAX_IMAGE_BYTES, redirects=2,
        )
        _image_type(data, media_type)
        _, local_url = _store_image(data, media_type, 'tmp', str(user_id))
        return local_url, 'picsum-local', ''
    except (ValueError, OSError, HTTPError):
        current_app.logger.info('Card image unavailable from configured providers.')
        return None
