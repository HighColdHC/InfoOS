import stat

import pytest

from backend.infrastructure.notifications.channels.feishu import FeishuChannel
from backend.infrastructure.notifications.lark_cli import LarkCli, LarkCliError, process_env
from tests.infoos import lark_cli_fakes

fake_lark = lark_cli_fakes.fake_lark


def test_text_image_and_idempotency(fake_lark, tmp_path):
    channel = FeishuChannel({'receive_id': 'ou_fixture'})
    key = 'a' * 48
    assert channel.send(title='标题', body='正文', image_path=None, message_id=key).provider_message_id == 'synthetic-message'
    image = tmp_path / '二维码.png'
    image.write_bytes(b'fake')
    assert channel.send(title='标题', body='正文', image_path=str(image), message_id=key).status == 'sent'
    calls = fake_lark.calls()
    assert len(calls) == 3
    assert calls[0]['args'] == ['im', '+messages-send', '--as', 'bot', '--user-id', 'ou_fixture',
                               '--text', '标题\n正文', '--idempotency-key', key]
    assert calls[1]['args'][-1] == key[:46] + '-t'
    assert calls[2]['args'][-4:] == ['--image', './二维码.png', '--idempotency-key', key[:46] + '-i']
    assert calls[2]['cwd'] == str(tmp_path)
    for call in calls:
        assert 'UNRELATED_SECRET' not in call['env']
        assert all(call['env'][name] == value for name, value in process_env().items())
        assert set(call['env']) <= set(process_env()) | {'LC_CTYPE'}  # Python's macOS locale coercion
    home = process_env()['HOME']
    from pathlib import Path
    assert stat.S_IMODE(Path(home).stat().st_mode) == 0o700


@pytest.mark.parametrize('payload,exit_code,status,code', [
    ({'ok': False, 'error': {'type': 'network', 'message': 'private-raw'}}, 4, 'retryable', 'lark_network'),
    ({'ok': False, 'error': {'type': 'authorization'}}, 1, 'failed', 'lark_authorization'),
    ({'ok': False, 'error': {'type': 'api', 'code': 99991400}}, 1, 'failed', 'lark_api'),
    ({}, 10, 'failed', 'lark_confirmation_required'),
    ({'ok': False, 'error': {'type': 'confirmation'}}, 1, 'failed', 'lark_confirmation_required'),
    ({}, 4, 'retryable', 'lark_network'),
    ({}, 0, 'sent', None),
    ({'ok': True, 'data': {'message_id': 'm'}}, 1, 'sent', None),
])
def test_error_mapping(fake_lark, payload, exit_code, status, code, caplog):
    fake_lark.control(send_results=[{'payload': payload, 'exit': exit_code}])
    result = FeishuChannel({'receive_id': 'ou_fixture'}).send(title='t', body='b', image_path=None, message_id='id')
    assert (result.status, result.error_code) == (status, code)
    assert 'private-raw' not in caplog.text
    assert all('--yes' not in call['args'] for call in fake_lark.calls())


def test_timeout_terminates_cli(fake_lark):
    fake_lark.control(delay=5)
    with pytest.raises(LarkCliError) as error:
        LarkCli().run(['im', '+messages-send'], timeout=0.1)
    assert error.value.code == 'lark_timeout' and error.value.retryable


def test_partial_image_retry_reuses_both_keys(fake_lark, tmp_path):
    fake_lark.control(send_results=[{'ok': True}, {'payload': {'ok': False, 'error': {'type': 'network'}}, 'exit': 4}])
    image = tmp_path / 'qr.png'
    image.touch()
    channel = FeishuChannel({'receive_id': 'ou_fixture'})
    args = dict(title='t', body='b', image_path=str(image), message_id='fixed-id')
    assert channel.send(**args).status == 'retryable'
    assert channel.send(**args).status == 'sent'
    assert [call['args'][-1] for call in fake_lark.calls()] == ['fixed-id-t', 'fixed-id-i'] * 2


def test_event_stream_ready_ndjson_and_stdin_shutdown(fake_lark, caplog):
    event = {'chat_type': 'p2p', 'message_type': 'text', 'sender_id': 'ou_fixture',
             'message_id': 'om_fixture', 'content': 'private-command-never-log'}
    fake_lark.control(event_before_ready=event, event_ready_gate=True, events=['invalid-private-line', [], event])
    stream = LarkCli().events()
    try:
        assert stream.poll(0.05) == []
        assert not stream.ready and stream.process.poll() is None
        fake_lark.scan('event_connect')
        received = []
        def collect():
            received.extend(stream.poll(0.05))
            return len(received) == 2 and stream.invalid_lines == 2
        lark_cli_fakes.eventually(collect)
        assert stream.ready and received == [event, event]
        call = fake_lark.calls()[0]
        assert call['args'] == ['event', 'consume', 'im.message.receive_v1', '--as', 'bot']
        assert 'UNRELATED_SECRET' not in call['env']
    finally:
        stream.close()
    assert stream.process.returncode == 0
    assert 'private-command-never-log' not in caplog.text
    assert 'invalid-private-line' not in caplog.text


def test_event_stream_requires_ready_marker(fake_lark):
    fake_lark.control(event_ready=False, events=[{'content': 'private'}])
    stream = LarkCli().events()
    try:
        assert stream.poll(0.1) == [] and not stream.ready
        stream._started -= 31
        with pytest.raises(LarkCliError, match='lark_event_ready_timeout'):
            stream.poll(0.01)
    finally:
        stream.close()


def test_event_stream_drains_events_before_exit(fake_lark):
    fake_lark.control(events=[{'content': 'private'}], event_exit=4)
    stream = LarkCli().events()
    received = []
    try:
        def collect():
            received.extend(stream.poll(0.05))
            return stream.finished
        lark_cli_fakes.eventually(collect)
        assert received == [{'content': 'private'}] and stream.process.returncode == 4
    finally:
        stream.close()
