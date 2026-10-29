"""Appliance-side writeback stall notification, once per last-writeback epoch."""
from datetime import timedelta
from backend.application.usecases.notifications import enqueue_notification
from backend.domain.notifications import NotificationKind
from backend.domain.timestamps import parse_utc, format_utc_seconds_z, utc_now_seconds_z
from backend.infrastructure.notifications.config import load_config
from backend.infrastructure.storage.agent_watchdog import AgentWatchdogRepository


def check_agent_writeback(base_dir, *, now: str | None = None) -> dict:
    timestamp = now or utc_now_seconds_z()
    clock, config = parse_utc(timestamp), load_config()['agent_watchdog']
    facts = AgentWatchdogRepository(base_dir).facts(older_than=format_utc_seconds_z(clock - timedelta(hours=6)),
        evaluate_since=config['evaluate_since'], opportunities_since=config['opportunities_since'])
    queued = []
    for fact in facts:
        if not fact['pending']:
            continue
        # Without any historical write, measure from the oldest pending input.
        since = fact['last_writeback'] if fact['last_writeback'] is not None else fact['oldest']
        hours = (clock - parse_utc(since)).total_seconds() / 3600
        if hours < config[f"{fact['job']}_stall_hours"]:
            continue
        label = '评估' if fact['job'] == 'evaluate' else '商机拆解'
        queued.extend(enqueue_notification(base_dir, kind=NotificationKind.AGENT_WRITEBACK_STALLED,
            title=f'{label} 写回停滞', body=f"{label}已 {int(hours)} 小时没有写回，还有 {fact['pending']} 张待处理。"
            f"可能原因：Mac 休眠或关机、隧道断开、Codex 运行失败。Mac 日志在 ~/Library/Logs/infoos-{fact['job']}/。",
            dedupe_key=f"agent-stalled:{fact['job']}:{fact['last_writeback'] or 'never'}", now=timestamp))
    return {'notification_ids': queued}
