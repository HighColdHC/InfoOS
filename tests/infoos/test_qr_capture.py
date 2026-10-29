import base64
import json
import struct
import zlib

import pytest
from backend.infrastructure.browser.png_crop import crop_png, _chunk
from backend.infrastructure.browser import qr_capture
from backend.infrastructure.opencli.commands import OpenCLIResult


def png(width=200, height=160):
    raw = (b'\0' + b'\xff\xff\xff' * width) * height
    return b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + _chunk(b'IDAT', zlib.compress(raw)) + _chunk(b'IEND', b'')


@pytest.fixture
def qr_clock(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(qr_capture.time, 'monotonic', lambda: elapsed[0])
    monkeypatch.setattr(qr_capture.time, 'sleep', lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    return elapsed


def test_crop_scales_rectangle_and_capture_keeps_only_qr(tmp_path, monkeypatch):
    raw = png()
    crop = crop_png(raw, (20, 20, 50, 50), 100)
    assert struct.unpack('>II', crop[16:24]) == (132, 132)
    commands = []
    def command(site, *args, **kwargs):
        commands.append(args)
        if args[0] == 'find':
            return '{"entries":[{"ref":"login-button"}]}'
        if args[0] == 'eval':
            if sum(call[0] == 'eval' for call in commands) == 1:
                return 'null'
            return '{"x":20,"y":20,"width":50,"height":50,"viewport_width":100}'
        if args[0] == 'screenshot':
            return base64.b64encode(raw).decode()
        return '{}'
    monkeypatch.setattr(qr_capture, '_command', command)
    path = tmp_path / 'code.png'
    qr_capture.capture_qr('xiaohongshu', 'synthetic-qr', path)
    assert path.read_bytes() == crop
    assert path.stat().st_mode & 0o777 == 0o600
    assert commands[-1] == ('screenshot',)
    assert ('click', 'login-button') in commands
    with pytest.raises(ValueError, match='invalid_qr_rectangle'):
        crop_png(raw, (0, 0, 0, 0), 100)


def test_wechat_qr_uses_outer_iframe_rectangle(monkeypatch):
    calls = []
    def command(site, *args, **kwargs):
        calls.append(args)
        return '{"x":100,"y":50,"width":300,"height":400,"viewport_width":1200}'
    monkeypatch.setattr(qr_capture, '_command', command)
    result = qr_capture._rectangle('synthetic', qr_capture.SELECTORS['zsxq'], deadline=qr_capture.time.monotonic() + 20)
    assert result == {'x': 100, 'y': 50, 'width': 300, 'height': 400, 'viewport_width': 1200}
    assert len(calls) == 1
    assert calls[0][0] == 'eval'


@pytest.mark.parametrize('agreement', [True, False])
def test_zsxq_entries_retry_delayed_agreement_or_skip_when_absent(tmp_path, monkeypatch, agreement, qr_clock):
    calls, clicks = [], []
    agree_searches = 0
    raw = png()
    def run(args, **kwargs):
        nonlocal agree_searches
        step, *options = args[4:]
        calls.append((step, *options))
        if step == 'find':
            selector = options[1]
            if selector == '.agree-btn':
                agree_searches += 1
                if not agreement or agree_searches == 1:
                    return OpenCLIResult(returncode=1, stdout='{"error":{"code":"semantic_not_found"}}')
            return OpenCLIResult(stdout=json.dumps({'entries': [{'ref': selector}]}))
        if step == 'click':
            clicks.append(options[0])
        if step == 'eval':
            ready = '.agree-btn' in clicks if agreement else qr_clock[0] >= 3
            return OpenCLIResult(stdout=json.dumps(
                {'x': 20, 'y': 20, 'width': 50, 'height': 50, 'viewport_width': 100} if ready else None))
        if step == 'screenshot':
            return OpenCLIResult(stdout=base64.b64encode(raw).decode())
        return OpenCLIResult(stdout='{}')
    monkeypatch.setattr(qr_capture, 'run_opencli', run)
    path = tmp_path / 'zsxq.png'
    qr_capture.capture_qr('zsxq', 'synthetic-zsxq', path)
    assert clicks == (['.get-qr-btn', '.agree-btn'] if agreement else ['.get-qr-btn'])
    assert agree_searches >= 2
    assert path.read_bytes() == crop_png(raw, (20, 20, 50, 50), 100)
    assert calls[-1] == ('screenshot',)


def test_visible_qr_does_not_click_entries(tmp_path, monkeypatch):
    calls = []
    def command(site, *args, **kwargs):
        calls.append(args[0])
        if args[0] == 'eval':
            return '{"x":20,"y":20,"width":50,"height":50,"viewport_width":100}'
        if args[0] == 'screenshot':
            return base64.b64encode(png()).decode()
        return '{}'
    monkeypatch.setattr(qr_capture, '_command', command)
    qr_capture.capture_qr('zsxq', 'visible-qr', tmp_path / 'qr.png')
    assert calls == ['open', 'eval', 'screenshot']


def test_find_semantic_not_found_is_absent_entry(monkeypatch):
    monkeypatch.setattr(qr_capture, 'run_opencli', lambda *a, **kw:
        OpenCLIResult(returncode=1, stdout='{"error":{"code":"semantic_not_found"}}'))
    assert json.loads(qr_capture._command('synthetic', 'find', '--css', '.agree-btn')) == {'entries': []}


def test_missing_entries_and_qr_expire_as_element_not_found(tmp_path, monkeypatch, qr_clock, caplog):
    def run(args, **kwargs):
        if args[4] == 'find':
            return OpenCLIResult(returncode=1, stdout='{"error":{"code":"semantic_not_found"}}')
        return OpenCLIResult(stdout='null')
    monkeypatch.setattr(qr_capture, 'run_opencli', run)
    path = tmp_path / 'missing.png'
    with pytest.raises(qr_capture.QrCaptureError, match='^qr_element_not_found$'):
        qr_capture.capture_qr('zsxq', 'missing-qr', path)
    assert qr_clock[0] == 20
    assert not path.exists()
    assert caplog.records == []


@pytest.mark.parametrize('step', ['open', 'eval', 'find', 'click', 'screenshot'])
@pytest.mark.parametrize('stdout,code', [
    ('{"error":{"code":"browser_unavailable","message":"https://example.test/?secret=fixture"}}', 'browser_unavailable'),
    ('non-json output with secret=fixture', 'None'),
])
def test_opencli_failure_identifies_step_and_logs_only_metadata(monkeypatch, caplog, step, stdout, code):
    monkeypatch.setattr(qr_capture, 'run_opencli', lambda *a, **kw: OpenCLIResult(returncode=1, stdout=stdout))
    with pytest.raises(qr_capture.QrCaptureError, match=f'^qr_opencli_{step}_failed$'):
        qr_capture._command('synthetic', step)
    assert f'step={step} returncode=1 code={code}' in caplog.text
    assert 'secret' not in caplog.text
