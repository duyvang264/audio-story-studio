import io
import json
import stat
import time
import zipfile

import httpx
import pytest

from studio import drive
from studio.config import Settings
from studio.story import parse_story_file
from studio.uploaders import load_token, save_token, token_path

FID = '1pOxQqfGoSLU5dfu8BXoJG3AAkQpDJJQz'


@pytest.fixture
def cfg(tmp_path):
    s = Settings(_env_file=None, storage_dir=tmp_path)
    secrets = tmp_path / 'secrets'
    secrets.mkdir()
    (secrets / 'client_secret_example.json').write_text(json.dumps({
        'installed': {'client_id': 'client', 'client_secret': 'secret'}}))
    save_token('google_drive', {'access_token': 'access', 'refresh_token': 'refresh',
                             'expires_at': time.time() + 3600}, s)
    return s


def test_config_autodetect_and_token_permissions(cfg):
    assert drive.client_config(cfg)['client_id'] == 'client'
    assert stat.S_IMODE(token_path('google_drive', cfg).stat().st_mode) == 0o600
    (cfg.storage / 'secrets/client_secret_other.json').write_text('{}')
    with pytest.raises(drive.DriveError, match='GOOGLE_DRIVE_CLIENT_SECRETS'):
        drive.client_config(cfg)


@pytest.mark.parametrize('source', [FID, f'https://docs.google.com/document/d/{FID}/edit?usp=drive_link',
                                    f'https://drive.google.com/file/d/{FID}/view',
                                    f'https://drive.google.com/open?id={FID}'])
def test_file_id(source):
    assert drive.file_id(source) == FID


@pytest.mark.parametrize('source', ['https://evil.com/d/' + FID, 'http://drive.google.com/d/' + FID,
                                    '../secrets/token.json', 'https://drive.google.com/drive/folders/' + FID])
def test_reject_bad_source(source):
    with pytest.raises(drive.DriveError):
        drive.file_id(source)


def test_token_refresh_and_preserve_refresh_token(cfg):
    save_token('google_drive', {'access_token': 'old', 'refresh_token': 'refresh', 'expires_at': 0}, cfg)
    def handler(req):
        assert req.url == drive.GOOGLE_TOKEN
        assert b'grant_type=refresh_token' in req.content
        return httpx.Response(200, json={'access_token': 'new', 'expires_in': 3600})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert drive.access_token(cfg, client) == 'new'
    assert load_token('google_drive', cfg)['refresh_token'] == 'refresh'


def test_missing_auth(cfg):
    token_path('google_drive', cfg).unlink()
    with httpx.Client() as client, pytest.raises(drive.DriveError, match='drive-auth'):
        drive.access_token(cfg, client)


def test_google_doc_export_intake(cfg):
    requests = []
    def handler(req):
        requests.append(req)
        assert req.headers['Authorization'] == 'Bearer access'
        if req.url.path.endswith('/export'):
            assert req.url.params['mimeType'] == 'text/plain'
            return httpx.Response(200, content='## Chương một\n\nTruyện tiếng Việt.'.encode())
        return httpx.Response(200, json={'name': '../../Truyện hay', 'mimeType': drive.DOC,
                                         'capabilities': {'canDownload': True}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        path = drive.fetch_story(FID, cfg, client)
    parsed = parse_story_file(path)
    assert parsed['meta']['rights'] == 'unknown'
    assert parsed['chapters'][0]['title'] == 'Chương một'
    assert path.parent == cfg.storage / 'imports/google_drive'
    assert len(requests) == 2


@pytest.mark.parametrize('mime,name,body', [('text/plain', 'truyen.txt', 'Nội dung truyện.'),
                                         ('text/markdown', 'truyen.md', '---\ntitle: Tên riêng\n---\n\n## Chương\nTruyện.')])
def test_text_and_preserved_markdown(cfg, mime, name, body):
    def handler(req):
        if req.url.params.get('alt') == 'media':
            return httpx.Response(200, content=body.encode('utf-8-sig'))
        return httpx.Response(200, json={'name': name, 'mimeType': mime,
                                        'capabilities': {'canDownload': True}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        path = drive.fetch_story(FID, cfg, client)
    parsed = parse_story_file(path)
    if mime == 'text/markdown':
        assert parsed['meta']['title'] == 'Tên riêng'
    assert parsed['chapters']


def test_docx_download(cfg):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('word/document.xml', '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Chương 1</w:t></w:r></w:p><w:p><w:r><w:t>Truyện hay.</w:t></w:r></w:p></w:body></w:document>''')
    def handler(req):
        if req.url.params.get('alt') == 'media':
            return httpx.Response(200, content=data.getvalue())
        return httpx.Response(200, json={'name': 'truyen.docx', 'mimeType': drive.DOCX,
                                        'capabilities': {'canDownload': True}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        path = drive.fetch_story(FID, cfg, client)
    assert parse_story_file(path)['chapters'][0]['title'] == 'Chương 1'


@pytest.mark.parametrize('status', [401, 403, 404])
def test_error_messages_do_not_expose_response(cfg, status):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text='SECRET'))) as client:
        with pytest.raises(drive.DriveError) as exc:
            drive.fetch_story(FID, cfg, client)
    assert str(status) in str(exc.value)
    assert 'SECRET' not in str(exc.value)


def test_unsupported_file_and_download_denied(cfg):
    for meta in [{'name': 'scan.pdf', 'mimeType': 'application/pdf', 'capabilities': {'canDownload': True}},
                 {'name': 'doc', 'mimeType': drive.DOC, 'capabilities': {'canDownload': False}}]:
        with httpx.Client(transport=httpx.MockTransport(lambda r, meta=meta: httpx.Response(200, json=meta))) as client:
            with pytest.raises(drive.DriveError):
                drive.fetch_story(FID, cfg, client)


def test_download_limit(cfg, monkeypatch):
    monkeypatch.setattr(drive, 'MAX_BYTES', 2)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'123'))) as client:
        with pytest.raises(drive.DriveError, match='20 MB'):
            drive.download(client, drive.API, {}, {})


def test_auth_denied_and_timeout(cfg, monkeypatch):
    # Never opens a browser or network listener; exercises timeout cleanup.
    class Server:
        server_port = 1234
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(drive.http.server, 'HTTPServer', Server)
    with pytest.raises(drive.DriveError, match='cấp quyền'):
        drive.authorize(cfg, no_browser=True, timeout=0)


def test_auth_state_validation_and_exchange(cfg, monkeypatch):
    monkeypatch.setattr(drive.secrets, 'token_urlsafe', lambda n: 'expected-state')
    attempts = []
    class Server:
        server_port = 1234
        def __init__(self, address, handler): self.handler = handler
        def __enter__(self): return self
        def __exit__(self, *a): attempts.append('closed')
        def handle_request(self):
            obj = object.__new__(self.handler)
            valid = bool(attempts)
            obj.path = '/?state=' + ('expected-state' if valid else 'wrong') + '&code=secret-code'
            obj.wfile = io.BytesIO()
            obj.send_response = lambda status: attempts.append(status)
            obj.send_header = lambda *a: None
            obj.end_headers = lambda: None
            obj.do_GET()
    monkeypatch.setattr(drive.http.server, 'HTTPServer', Server)
    def handler(req):
        assert attempts == [400, 200, 'closed']
        assert req.url == drive.GOOGLE_TOKEN
        assert b'code_verifier=' in req.content
        assert b'code=secret-code' in req.content
        return httpx.Response(200, json={'access_token': 'authorized', 'refresh_token': 'new-refresh'})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(drive.httpx, 'Client', lambda **kw: client)
    drive.authorize(cfg, no_browser=True)
    assert load_token('google_drive', cfg)['access_token'] == 'authorized'
