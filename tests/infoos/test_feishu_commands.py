"""Private commands use synthetic incidents, outbox rows and queued browser actions."""
import pytest

from backend.application.tasks.handlers import auth_qr as handlers
from backend.application.usecases.feishu_commands import handle_inbound
from backend.application.workflows.auth_qr import start_qr_round
from backend.domain.feishu_commands import Command, parse_command
from backend.domain.notifications import NotificationKind, delivery_time, route_channels
from backend.domain.timestamps import parse_utc
from backend.infrastructure.storage.auth_qr import AuthQrRepository
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository
from tests.infoos import test_auth_qr_rounds as rounds

NOW, _capture, qr_setup = rounds.NOW, rounds._capture, rounds.qr_setup


@pytest.mark.parametrize('text,expected', [
    ('小红书二维码', Command('qr', 'xiaohongshu')),
    ('红书重发', Command('qr', 'xiaohongshu')),
    (' XHS扫码 ', Command('qr', 'xiaohongshu')),
    ('抖音登录', Command('qr', 'douyin')),
    ('douyin重新生成', Command('qr', 'douyin')),
    ('知识星球码', Command('qr', 'zsxq')),
    ('星球二维码', Command('qr', 'zsxq')),
    ('生财有术登录', Command('qr', 'zsxq')),
    ('生财重发', Command('qr', 'zsxq')),
    ('zsxq扫码', Command('qr', 'zsxq')),
    ('小红书登录了吗', Command('status', 'xiaohongshu')),
    ('抖音是否登录', Command('status', 'douyin')),
    ('星球登录了没', Command('status', 'zsxq')),
    ('小红书没有登录', Command('status', 'xiaohongshu')),
    ('登录状态', Command('status')),
    ('YouTube二维码', Command('help')),
    ('帮助', Command('help')),
    ('执行 shell rm -rf /', Command('help')),
    ('1', Command('qr', 'xiaohongshu')),
    (' 3 ', Command('qr', 'zsxq')),
    ('４', Command('status')),
    ('0', Command('help')),
    ('6', Command('help')),
])
def test_parse(text, expected):
    assert parse_command(text) == expected


def event(text='小红书二维码', message_id='om_command', **patch):
    return dict(chat_type='p2p', message_type='text', sender_type='user',
        sender_id='ou_fixture', message_id=message_id, content=text) | patch


def reply(tmp_path, message_id='om_command'):
    return NotificationOutboxRepository(tmp_path).by_dedupe(f'feishu-cmd:{message_id}', channel='feishu')


@pytest.mark.parametrize('patch', [
    {'chat_type': 'group'}, {'message_type': 'image'}, {'sender_id': 'ou_stranger'},
    {'message_id': None}, {'content': {}},
])
def test_filter_does_not_persist(tmp_path, qr_setup, patch):
    handle_inbound(tmp_path, event(**patch), now=NOW)
    with NotificationOutboxRepository(tmp_path).connect_existing() as conn:
        assert conn.execute('select count(*) from notification_outbox').fetchone()[0] == 0
    assert AuthQrRepository(tmp_path).get(qr_setup[0]['incident_id']) is None


def test_new_round_dedupes_without_extra_reply(tmp_path, qr_setup):
    incident, tasks, _, _ = qr_setup
    handle_inbound(tmp_path, event(), now=NOW)
    repo = AuthQrRepository(tmp_path)
    row = repo.get(incident['incident_id'])
    assert row['status'] == 'capturing' and row['manual'] == 1
    assert tasks.get(f"qrcapture_{incident['incident_id']}_1_1") is not None
    receipt = reply(tmp_path)
    assert receipt['status'] == 'skipped' and receipt['body'] == ''
    handle_inbound(tmp_path, event(), now=NOW)
    assert reply(tmp_path)['status'] == 'skipped'
    assert repo.get(incident['incident_id'])['round_no'] == 1
    # A distinct command during capture gets the explicit busy reply.
    handle_inbound(tmp_path, event(message_id='om_busy'), now=NOW)
    assert reply(tmp_path, 'om_busy')['body'] == '正在生成，稍候。'


def test_idle_round_overrides_daily_cap_and_quiet_hours(tmp_path, qr_setup):
    incident = qr_setup[0]
    start_qr_round(tmp_path, incident['incident_id'], now=NOW)
    with AuthQrRepository(tmp_path).connect() as conn:
        conn.execute("update auth_qr_rounds set status='idle',rounds_today=4,next_round_at='2026-10-25T00:00:00Z'")
    handle_inbound(tmp_path, event(), now='2026-10-21T16:00:00Z')
    row = AuthQrRepository(tmp_path).get(incident['incident_id'])
    assert row['status'] == 'capturing' and row['round_no'] == 2 and row['manual'] == 1


def test_waiting_replaces_even_last_code_and_cancels_old_delivery(tmp_path, monkeypatch, qr_setup):
    incident, tasks, paths, _ = qr_setup
    incident_id = incident['incident_id']
    start_qr_round(tmp_path, incident_id, now=NOW)
    _capture(tmp_path, tasks, incident_id, 1, 1, monkeypatch, NOW)
    with AuthQrRepository(tmp_path).connect() as conn:
        conn.execute('update auth_qr_rounds set codes_sent=3 where incident_id=?', (incident_id,))
    handle_inbound(tmp_path, event(), now=NOW)
    row = AuthQrRepository(tmp_path).get(incident_id)
    assert row['status'] == 'capturing' and row['manual'] == 1 and row['code_expires_at'] is None
    assert tasks.get(f'qrcapture_{incident_id}_1_4') is not None
    assert not paths[0].exists()
    assert NotificationOutboxRepository(tmp_path).by_dedupe(f'{incident_id}:1:1', channel='feishu')['status'] == 'expired'
    assert _capture(tmp_path, tasks, incident_id, 1, 4, monkeypatch, NOW)['status'] == 'success'
    new_code = NotificationOutboxRepository(tmp_path).by_dedupe(f'{incident_id}:1:4', channel='feishu')
    assert '第 4 张' in new_code['body'] and paths[-1].exists()
    handle_inbound(tmp_path, event(), now=NOW)
    assert AuthQrRepository(tmp_path).get(incident_id)['status'] == 'waiting'
    assert reply(tmp_path)['body'] == ''


def test_closing_restarts_durably_after_close(tmp_path, monkeypatch, qr_setup):
    incident, tasks, _, closed = qr_setup
    incident_id = incident['incident_id']
    start_qr_round(tmp_path, incident_id, now=NOW)
    repo = AuthQrRepository(tmp_path)
    repo.close(incident_id, now=NOW, next_round_at='2026-10-25T00:00:00Z')
    handle_inbound(tmp_path, event(), now=NOW)
    receipt = reply(tmp_path)
    assert receipt['body'] == '正在重新生成，稍候。'
    task = tasks.get(f"qrmanual_{receipt['notification_id']}")
    assert repo.get(incident_id)['status'] == 'closing'
    monkeypatch.setattr(handlers, 'current_iso', lambda: NOW)
    assert handlers.handle_auth_qr_check(task, tmp_path)['status'] == 'success'
    row = repo.get(incident_id)
    assert len(closed) == 1 and row['round_no'] == 2 and row['manual'] == 1
    assert row['status'] == 'capturing'


def test_status_and_help_replies_are_immediate_feishu_only(tmp_path, monkeypatch, qr_setup):
    incident, tasks, _, _ = qr_setup
    incident_id = incident['incident_id']
    start_qr_round(tmp_path, incident_id, now=NOW)
    _capture(tmp_path, tasks, incident_id, 1, 1, monkeypatch, NOW)
    handle_inbound(tmp_path, event(text='登录状态'), now=NOW)
    receipt = reply(tmp_path)
    assert receipt['body'].splitlines() == [
        '小红书：等待扫码登录（掉线于 2026-10-21 09:00），当前二维码有效至 09:02',
        '抖音：没有掉线记录', '生财有术：没有掉线记录',
    ]
    assert receipt['kind'] == 'command_reply' and receipt['status'] == 'pending'
    assert tasks.get(f"notification_{receipt['notification_id']}").next_run_at == NOW
    handle_inbound(tmp_path, event('帮助', 'om_help'), now='2026-10-21T16:00:00Z')
    assert reply(tmp_path, 'om_help')['body'].splitlines()[:5] == [
        '回复数字：', '1 小红书二维码', '2 抖音二维码', '3 生财有术二维码', '4 登录状态']
    assert reply(tmp_path, 'om_help')['next_attempt_at'] == '2026-10-21T16:00:00Z'
    quiet = {'start': '23:00', 'end': '08:00', 'timezone': 'Asia/Shanghai'}
    now = parse_utc('2026-10-21T16:00:00Z')
    assert delivery_time(now, quiet, NotificationKind.COMMAND_REPLY) == now
    assert route_channels(NotificationKind.COMMAND_REPLY, {'feishu': True, 'email': True}) == ('feishu',)
    with NotificationOutboxRepository(tmp_path).connect_existing() as conn:
        assert conn.execute("select count(*) from notification_outbox where kind='command_reply' and channel!='feishu'").fetchone()[0] == 0


def test_no_outage_and_resolved_incident_need_no_qr(tmp_path, qr_setup):
    incident = qr_setup[0]
    handle_inbound(tmp_path, event('抖音二维码'), now=NOW)
    assert reply(tmp_path)['body'] == '抖音 当前没有掉线记录，不需要扫码。'
    with AuthQrRepository(tmp_path).connect() as conn:
        conn.execute("update auth_recovery_incidents set status='resolved' where incident_id=?", (incident['incident_id'],))
    handle_inbound(tmp_path, event(message_id='om_resolved'), now=NOW)
    assert reply(tmp_path, 'om_resolved')['body'] == '小红书 当前没有掉线记录，不需要扫码。'
