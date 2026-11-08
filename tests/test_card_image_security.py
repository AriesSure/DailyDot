"""Offline coverage of the card save and provider boundaries."""

import base64
import json
import os
import socket
import ssl
import stat
import struct
import zlib
from pathlib import Path

import pytest
from urllib3.exceptions import ProtocolError

from app.models import Card, User
from app.services import card_service as cards


# A generated 2x2 JPEG, with no external image or user data.
JPEG = base64.b64decode(
    '/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDg'
    'sLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQU'
    'FBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAw'
    'QFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkK'
    'FhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmq'
    'KjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEB'
    'AQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRob'
    'HBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOE'
    'hYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+P'
    'n6/9oADAMBAAIRAxEAPwD8rZ55bqeSaaR5ppGLvJIxZmYnJJJ6knvRRRSWyOjEfxp+r/M//9k='
)


def png_chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


PNG = (
    b'\x89PNG\r\n\x1a\n'
    + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    + png_chunk(b'IDAT', zlib.compress(b'\x00\x22\x44\x66'))
    + png_chunk(b'IEND', b'')
)


def data_url(payload, mime='image/jpeg'):
    return f'data:{mime};base64,' + base64.b64encode(payload).decode('ascii')


@pytest.fixture
def static_dir(app, tmp_path, monkeypatch):
    folder = (tmp_path / 'static').resolve()
    folder.mkdir()
    monkeypatch.setattr(app, 'static_folder', str(folder))
    return folder


class FakeResponse:
    def __init__(self, body=b'', media_type='image/jpeg', *, status=200, headers=None, interrupt=False):
        self.body = body
        self.status = status
        self.headers = {'Content-Type': media_type, **(headers or {})}
        self.interrupt = interrupt
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def stream(self, amount, *, decode_content):
        assert amount == 16 * 1024
        assert decode_content is False
        if self.interrupt:
            yield self.body[:16]
            raise ProtocolError('interrupted at an internal test endpoint')
        for start in range(0, len(self.body), amount):
            yield self.body[start:start + amount]


@pytest.fixture
def transport(monkeypatch):
    """Record DNS and HTTP independently, and fail on every unexpected request."""
    class FakeTransport:
        answers = {}
        responses = []
        resolutions = []
        requests = []

        def resolve(self, host, port, *, family, type):
            assert port == 443
            assert family == socket.AF_INET
            assert type == socket.SOCK_STREAM
            self.resolutions.append(host)
            return [
                (family, type, 0, '', (ip, port))
                for ip in self.answers.get(host, ['8.8.8.8'])
            ]

        def pool(self, address, **options):
            transport = self

            class FakePool:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    pass

                def urlopen(self, method, path, **request):
                    transport.requests.append((address, options, method, path, request))
                    assert transport.responses, 'unexpected provider request'
                    return transport.responses.pop(0)

            return FakePool()

    fake = FakeTransport()
    monkeypatch.setattr(cards.socket, 'getaddrinfo', fake.resolve)
    monkeypatch.setattr(cards, 'HTTPSConnectionPool', fake.pool)
    return fake


def save(client, auth_headers, **fields):
    return client.post('/cards/api/card/save', json=fields, headers=auth_headers)


def provider(client):
    return client.get('/cards/api/card/image/nature')


_SOS_OFFSET = JPEG.index(b'\xff\xda')
_SCAN_OFFSET = _SOS_OFFSET + 2 + int.from_bytes(JPEG[_SOS_OFFSET + 2:_SOS_OFFSET + 4], 'big')
_JPEG_HEADER = JPEG[:_SCAN_OFFSET]
_EOI = b'\xff\xd9'


def progressive_jpeg_markers():
    """Build a two-scan structural fixture without encoding image pixels."""
    header = _JPEG_HEADER.replace(b'\xff\xc0', b'\xff\xc2', 1)
    dc_scan = header[:-3] + b'\x00\x00\x00\x11'
    ac_scan = b'\xff\xda\x00\x08\x01\x01\x00\x01\x3f\x00\x22'
    return dc_scan + ac_scan + _EOI


@pytest.mark.parametrize('payload', [
    pytest.param(JPEG, id='baseline-jpeg'),
    pytest.param(_JPEG_HEADER + b'\x11\xff\x00\x22' + _EOI, id='stuffed-ff'),
    pytest.param(
        _JPEG_HEADER + b''.join(bytes((0x11, 0xff, code)) for code in range(0xd0, 0xd8)) + b'\x22' + _EOI,
        id='all-restart-markers',
    ),
    pytest.param(
        _JPEG_HEADER + b'\x11\xff\xff\xd0\x22\xff\xff\xd1\x33\xff' + _EOI,
        id='fill-before-restarts-and-eoi',
    ),
    pytest.param(progressive_jpeg_markers(), id='progressive-two-scans'),
])
def test_jpeg_preserves_supported_marker_sequences(payload):
    assert cards._image_type(payload, 'image/jpeg') == 'image/jpeg'


@pytest.mark.parametrize('scan', [
    pytest.param(b'\x11' + b'\xff' * 4096, id='unterminated-ff-run'),
    pytest.param(b'\x11' + b'\xff' * 4096 + b'\x00', id='stuffed-ff-run-at-eof'),
    pytest.param(b'\x11\xff\xc4\x00', id='truncated-marker-length'),
    pytest.param(b'\x11\xff\xd8' + _EOI, id='unexpected-soi'),
    pytest.param(JPEG[_SCAN_OFFSET:-2], id='missing-eoi'),
    pytest.param(JPEG[_SCAN_OFFSET:] + b'\x00', id='trailing-data-after-eoi'),
    pytest.param(b'\x11\xff\xd0\x22\xff', id='truncated-after-restart'),
    pytest.param(b'\x11\xff\xff\x00\x22\xff\xff\xd0\x33\xff\xff', id='multiple-fill-runs-then-truncation'),
    pytest.param(b'\xff\xff\xd9', id='empty-scan-with-fill-before-eoi'),
])
def test_jpeg_invalid_scan_preserves_saved_card(client, auth_headers, static_dir, transport, scan):
    assert save(client, auth_headers, image_data=data_url(JPEG), quote='Keep').json['success']
    saved_card = Card.query.one()
    before = saved_card.id, saved_card.quote, saved_card.image_url
    files = {path: path.read_bytes() for path in static_dir.rglob('*') if path.is_file()}
    assert len(files) == 1

    response = save(client, auth_headers, overwrite=True, image_data=data_url(_JPEG_HEADER + scan), quote='Discard')

    assert response.status_code == 400
    assert response.json == {'success': False, 'error': 'invalid_image_data'}
    saved_card = Card.query.one()
    assert (saved_card.id, saved_card.quote, saved_card.image_url) == before
    assert {path: path.read_bytes() for path in static_dir.rglob('*') if path.is_file()} == files
    assert not transport.resolutions and not transport.requests


@pytest.mark.parametrize('length', [512, 1024, 2048, 4096, 8192, 16384])
def test_jpeg_scan_reads_each_byte_at_most_once(length):
    class SequentialReads:
        def __init__(self, data, start):
            self.data = data
            self.next_offset = start

        def __len__(self):
            return len(self.data)

        def __getitem__(self, index):
            # Disallow slices, duplicate reads and movement back to a consumed suffix.
            assert index == self.next_offset
            self.next_offset += 1
            return self.data[index]

    prefix = b'header'
    run = b'\xff' * length
    unterminated = prefix + b'\x11' + run + b'\x00'
    scan = SequentialReads(unterminated, len(prefix))
    assert cards._jpeg_scan_end(scan, len(prefix)) is None
    assert scan.next_offset == len(unterminated)

    terminated = unterminated + b'\x22' + run + b'\xd0\x33' + run + b'\xd9'
    scan = SequentialReads(terminated + b'unread', len(prefix))
    assert cards._jpeg_scan_end(scan, len(prefix)) == len(terminated) - 2
    assert scan.next_offset == len(terminated)


@pytest.mark.parametrize('url', [
    'https://arbitrary.example/image.jpg',
    'http://arbitrary.example/image.jpg',
    'http://localhost/image.jpg',
    'http://127.0.0.1/image.jpg',
    'http://[::1]/image.jpg',
    'http://10.10.10.10/image.jpg',
    'http://169.254.12.34/image.jpg',
    'http://169.254.169.254/image.jpg',
    'file:///tmp/image.jpg',
    'not a URL',
    'https://sample-user:sample-secret@example.invalid/a.jpg',
    'https://arbitrary.example/redirect?to=localhost',
    'https://api.pexels.com.evil.example/image.jpg',
    'https://picsum.photos/400/400',
    '/static/cards/tmp/1/image.jpg',
])
def test_save_rejects_client_url_without_dns_or_http(client, auth_headers, static_dir, transport, url):
    response = save(client, auth_headers, image_url=url)
    assert response.status_code == 400
    assert response.json == {'success': False, 'error': 'image_url_not_supported'}
    assert not transport.resolutions and not transport.requests
    assert Card.query.count() == 0
    assert not list(static_dir.iterdir())


@pytest.mark.parametrize('mime,payload,extension', [
    ('image/jpeg', JPEG, '.jpg'), ('image/png', PNG, '.png'),
])
def test_save_valid_base64(client, auth_headers, static_dir, mime, payload, extension):
    response = save(client, auth_headers, image_data=data_url(payload, mime))
    assert response.status_code == 200 and response.json['created']
    card = Card.query.one()
    assert card.image_url.endswith(extension)
    stored = client.get(card.image_url)
    assert stored.mimetype == mime and stored.data == payload
    stored.close()
    assert len(list(static_dir.rglob(f'*{extension}'))) == 1


@pytest.mark.parametrize('value', [
    'not valid base64',
    data_url(b'<html>not an image</html>'),
    data_url(JPEG + b'<html>appended</html>'),
    data_url(JPEG[:-2]),
    data_url(PNG[:-12], 'image/png'),
    data_url(PNG, 'image/jpeg'),
    data_url(b'x' * (cards._MAX_IMAGE_BYTES + 1)),
])
def test_save_rejects_invalid_image(client, auth_headers, static_dir, value):
    response = save(client, auth_headers, image_data=value)
    assert response.status_code == 400
    assert response.json['error'] == 'invalid_image_data'
    assert Card.query.count() == 0
    assert not list(static_dir.iterdir())


@pytest.mark.parametrize('image_id', ['../outside', '0' * 32, '/absolute', 'A' * 32, 'a' * 32 + '.jpg'])
def test_save_rejects_unknown_or_malformed_asset(client, auth_headers, static_dir, transport, image_id):
    response = save(client, auth_headers, image_id=image_id)
    assert response.status_code == 400
    assert response.json['error'] == 'invalid_image_id'
    assert not transport.resolutions and not transport.requests
    assert Card.query.count() == 0


def test_save_and_overwrite_with_owned_preview(client, auth_headers, static_dir, transport):
    transport.responses = [FakeResponse(JPEG), FakeResponse(PNG, 'image/png')]
    selected = provider(client).json
    assert selected['source'] == 'picsum-local'
    preview = client.get(selected['image_url'])
    assert preview.data == JPEG
    preview.close()
    response = save(client, auth_headers, image_id=selected['image_id'], quote='First')
    assert response.json['created']
    card_id = response.json['card_id']
    second = provider(client).json
    assert save(client, auth_headers, image_id=second['image_id']).json['reason'] == 'exists'
    response = save(client, auth_headers, image_id=second['image_id'], overwrite=True, quote='Second')
    assert response.json == {'success': True, 'updated': True, 'card_id': card_id}
    assert len(transport.requests) == 2  # Only preview calls can reach providers.
    card = Card.query.one()
    assert card.quote == 'Second'
    image = client.get(card.image_url)
    assert image.data == PNG
    image.close()
    stack = client.get('/cards/stack')
    assert card.image_url.encode() in stack.data and b'Second' in stack.data


def test_save_without_image_and_delete_own_card(client, auth_headers, static_dir):
    response = save(client, auth_headers, quote='No image')
    card_id = response.json['card_id']
    assert Card.query.one().image_url is None
    assert b'linear-gradient' in client.get('/cards/stack').data
    assert client.delete(f'/cards/api/card/{card_id}', headers=auth_headers).json['success']
    assert Card.query.count() == 0
    assert not list(static_dir.iterdir())


def test_other_user_cannot_use_preview_or_delete_card(app, client, auth_headers, static_dir, transport):
    transport.responses = [FakeResponse(JPEG)]
    first = provider(client).json
    card_id = save(client, auth_headers, image_id=first['image_id']).json['card_id']
    other = app.test_client()
    credentials = {'username': 'other-user', 'password': 'Safe@1234'}
    # The app fixture keeps one context open; isolate Flask-Login's request
    # identity cache while testing a second browser session.
    with app.app_context():
        other.post('/auth/register', data={**credentials, 'email': 'other@example.com', 'confirm': 'Safe@1234'})
    with app.app_context():
        other.post('/auth/login', data=credentials)
    with other.session_transaction() as session:
        assert session['_user_id'] == str(User.query.filter_by(username='other-user').one().id)
    with app.app_context():
        assert save(other, auth_headers, image_id=first['image_id']).status_code == 400
    with app.app_context():
        assert other.delete(f'/cards/api/card/{card_id}', headers=auth_headers).status_code == 404
    assert Card.query.one().user_id == User.query.filter_by(username='testuser').one().id
    assert len(transport.requests) == 1


@pytest.mark.parametrize('linked_directory', [False, True])
def test_symlinked_temp_path_is_rejected(client, auth_headers, static_dir, tmp_path, monkeypatch, linked_directory):
    user_id = User.query.filter_by(username='testuser').one().id
    image_id = 'a' * 32
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = outside / f'{image_id}.jpg'
    target.write_bytes(JPEG)
    directory = static_dir / 'cards' / 'tmp' / str(user_id)
    directory.parent.mkdir(parents=True)
    if linked_directory:
        path = directory
        destination = outside
    else:
        directory.mkdir()
        path = directory / target.name
        destination = target
    try:
        path.symlink_to(destination, target_is_directory=linked_directory)
    except OSError:
        # Unprivileged Windows may forbid creation of real symlinks.
        # Exercise the same lstat rejection branch with a simulated link.
        original_lstat = Path.lstat

        def linked_lstat(candidate):
            if candidate == path:
                return os.stat_result((stat.S_IFLNK, 0, 0, 1, 0, 0, 0, 0, 0, 0))
            return original_lstat(candidate)

        monkeypatch.setattr(Path, 'lstat', linked_lstat)
    assert save(client, auth_headers, image_id=image_id).status_code == 400
    assert Card.query.count() == 0
    assert target.read_bytes() == JPEG


@pytest.mark.parametrize('failure', ['url', 'data', 'metadata', 'commit'])
def test_rejected_overwrite_preserves_existing_row_and_image(client, auth_headers, static_dir, db, monkeypatch, failure):
    assert save(client, auth_headers, image_data=data_url(JPEG), quote='Keep').json['success']
    original_url = Card.query.one().image_url
    before = sorted(p for p in static_dir.rglob('*') if p.is_file())
    update = {'overwrite': True, 'quote': 'Changed', 'image_data': data_url(PNG, 'image/png')}
    if failure == 'url':
        update['image_url'] = 'https://example.invalid/preview.jpg'
    elif failure == 'data':
        update['image_data'] = 'broken'
    elif failure == 'metadata':
        update['streak'] = 'invalid'
    else:
        def fail_commit():
            raise RuntimeError('synthetic internal error')
        monkeypatch.setattr(db.session, 'commit', fail_commit)
    response = save(client, auth_headers, **update)
    if failure == 'commit':
        assert response.status_code == 500 and response.json['error'] == 'card_save_failed'
    else:
        assert response.status_code == 400
    assert Card.query.one().quote == 'Keep'
    assert Card.query.one().image_url == original_url
    assert sorted(p for p in static_dir.rglob('*') if p.is_file()) == before
    original = client.get(original_url)
    assert original.data == JPEG
    original.close()


def pexels_response(url):
    body = json.dumps({'photos': [{'src': {'medium': url}, 'photographer': 'Sample'}]}).encode()
    return FakeResponse(body, 'application/json')


def test_pexels_tls_identity_credential_scope_and_ip_pin(client, auth_headers, static_dir, transport, monkeypatch):
    monkeypatch.setenv('PEXELS_API_KEY', 'fake-test-key')
    cdn = 'image-test.pexels.com'
    transport.answers = {'api.pexels.com': ['8.8.8.8'], cdn: ['1.1.1.1']}
    transport.responses = [pexels_response(f'https://{cdn}/image.jpg'), FakeResponse(JPEG)]
    response = provider(client)
    assert response.json['source'] == 'pexels-local'
    assert response.json['photographer'] == 'Sample'
    assert transport.resolutions == ['api.pexels.com', cdn]
    api_request, image_request = transport.requests
    for call, host, address in [(api_request, 'api.pexels.com', '8.8.8.8'), (image_request, cdn, '1.1.1.1')]:
        dial, pool, method, path, req = call
        assert dial == address and method == 'GET' and path.startswith('/')
        assert pool['port'] == 443 and pool['server_hostname'] == pool['assert_hostname'] == host
        assert pool['ssl_context'].verify_mode == ssl.CERT_REQUIRED
        assert pool['timeout'].connect_timeout == 4 and pool['timeout'].read_timeout == 5
        assert req['headers']['Host'] == host
        assert req['redirect'] is req['retries'] is req['preload_content'] is req['decode_content'] is False
    assert api_request[4]['headers']['Authorization'] == 'fake-test-key'
    assert 'Authorization' not in image_request[4]['headers']
    assert save(client, auth_headers, image_id=response.json['image_id']).json['success']
    assert len(transport.requests) == 2


@pytest.mark.parametrize('first', [
    FakeResponse(status=503),
    FakeResponse(b'{"photos":[]}', 'application/json'),
    FakeResponse(status=302, headers={'Location': 'https://api.pexels.com/elsewhere'}),
    pexels_response('https://pexels.com.attacker.example/unsafe.jpg'),
    pexels_response('http://api.pexels.com/unsafe.jpg'),
    pexels_response('https://fake-user:fake-secret@api.pexels.com/unsafe.jpg'),
    pexels_response('https://api.pexels.com:444/unsafe.jpg'),
])
def test_pexels_failure_falls_back_to_picsum(client, auth_headers, static_dir, transport, monkeypatch, first):
    monkeypatch.setenv('PEXELS_API_KEY', 'fake-test-key')
    transport.responses = [first, FakeResponse(JPEG)]
    response = provider(client)
    assert response.json['source'] == 'picsum-local'
    assert transport.resolutions == ['api.pexels.com', 'picsum.photos']
    assert all(call[4]['headers']['Host'] in transport.resolutions for call in transport.requests)
    assert 'Authorization' not in transport.requests[1][4]['headers']


def test_picsum_redirect_is_resolved_and_pinned_per_hop(client, auth_headers, static_dir, transport):
    host = 'image-test.picsum.photos'
    transport.answers[host] = ['1.1.1.1']
    transport.responses = [
        FakeResponse(status=302, headers={'Location': f'https://{host}/image.jpg'}),
        FakeResponse(JPEG),
    ]
    response = provider(client)
    assert response.json['source'] == 'picsum-local'
    assert transport.resolutions == ['picsum.photos', host]
    assert [request[0] for request in transport.requests] == ['8.8.8.8', '1.1.1.1']
    assert response.json['image_url'].startswith('/static/cards/tmp/')


@pytest.mark.parametrize('target', [
    'https://untrusted.example/path', 'https://127.0.0.1/path',
    'https://169.254.169.254/path', '//picsum.photos.attacker.example/path',
    'http://picsum.photos/unsafe', 'https://fake-user@picsum.photos/unsafe',
])
def test_picsum_rejects_redirect_without_dialing_target(client, auth_headers, static_dir, transport, target):
    transport.responses = [FakeResponse(status=302, headers={'Location': target})]
    response = provider(client)
    assert response.status_code == 502 and response.json == {'success': False, 'error': 'card_image_unavailable'}
    assert transport.resolutions == ['picsum.photos'] and len(transport.requests) == 1
    assert not list(static_dir.iterdir())


def test_picsum_redirect_limit(client, auth_headers, static_dir, transport):
    transport.responses = [FakeResponse(status=302, headers={'Location': '/loop'}) for _ in range(3)]
    assert provider(client).status_code == 502
    assert len(transport.requests) == 3
    assert not list(static_dir.iterdir())


def test_picsum_rechecks_dns_for_an_allowed_redirect_host(client, auth_headers, static_dir, transport):
    host = 'image-test.picsum.photos'
    transport.answers[host] = ['169.254.1.2']
    transport.responses = [FakeResponse(status=302, headers={'Location': f'https://{host}/image.jpg'})]
    assert provider(client).status_code == 502
    assert transport.resolutions == ['picsum.photos', host]
    assert len(transport.requests) == 1
    assert not list(static_dir.iterdir())


def test_pexels_image_redirect_is_not_followed(client, auth_headers, static_dir, transport, monkeypatch):
    monkeypatch.setenv('PEXELS_API_KEY', 'fake-test-key')
    transport.responses = [
        pexels_response('https://image-test.pexels.com/photo.jpg'),
        FakeResponse(status=302, headers={'Location': 'https://redirect-test.pexels.com/photo.jpg'}),
        FakeResponse(JPEG),
    ]
    assert provider(client).json['source'] == 'picsum-local'
    assert transport.resolutions == ['api.pexels.com', 'image-test.pexels.com', 'picsum.photos']
    assert len(transport.requests) == 3


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.1.2', '224.0.0.1', '100.64.0.1', '192.0.0.9'])
def test_provider_rejects_non_public_dns_answer(client, auth_headers, static_dir, transport, address):
    transport.answers['picsum.photos'] = ['8.8.8.8', address]
    assert provider(client).status_code == 502
    assert transport.resolutions == ['picsum.photos'] and not transport.requests
    assert not list(static_dir.iterdir())


@pytest.mark.parametrize('fake_response', [
    FakeResponse(b'x' * (cards._MAX_IMAGE_BYTES + 1)),
    FakeResponse(JPEG, headers={'Content-Length': str(cards._MAX_IMAGE_BYTES + 1)}),
    FakeResponse(JPEG, headers={'Content-Length': str(len(JPEG) + 1)}),
    FakeResponse(JPEG, interrupt=True),
    FakeResponse(PNG, 'image/jpeg'),
    FakeResponse(JPEG, 'text/html'),
    FakeResponse(b'<html>non-image</html>'),
    FakeResponse(b'{"body":"not image"}'),
    FakeResponse(JPEG, headers={'Content-Encoding': 'gzip'}),
])
def test_provider_rejects_bad_body_without_writing_file(client, auth_headers, static_dir, transport, fake_response):
    transport.responses = [fake_response]
    response = provider(client)
    assert response.status_code == 502 and response.json['error'] == 'card_image_unavailable'
    assert fake_response.closed
    assert not list(static_dir.iterdir())
