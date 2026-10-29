"""One private-message consumer; reconnects without exposing event content."""
import argparse
import logging
import signal
from pathlib import Path
from threading import Event

from backend.application.usecases.feishu_commands import handle_inbound
from backend.domain.timestamps import utc_now_seconds_z
from backend.infrastructure.notifications.config import load_config, readiness
from backend.infrastructure.notifications.lark_cli import LarkCli, LarkCliError

logger = logging.getLogger(__name__)


def run(base_dir: Path, stopped: Event) -> None:
    backoff = 10
    while not stopped.is_set():
        stream = None
        try:
            if not readiness(load_config())['feishu']:
                stopped.wait(60)
                continue
            stream = LarkCli().events()
            connected = False
            while not stopped.is_set():
                events = stream.poll()
                if stream.ready and not connected:
                    logger.info('feishu_inbox_connected')
                    connected = True
                    backoff = 10
                for event in events:
                    if stopped.is_set():
                        break
                    handle_inbound(base_dir, event, now=utc_now_seconds_z())
                if stream.finished:
                    logger.warning('feishu_inbox_exit code=%s reason=%s error=%s invalid_lines=%s',
                        stream.process.returncode, stream.exit_reason, stream.error_code, stream.invalid_lines)
                    break
        except LarkCliError as exc:
            logger.warning('feishu_inbox_error code=%s', exc.code)
        except Exception:  # noqa: BLE001 - worker 顶层只记录稳定错误码，不泄露消息或配置
            logger.error('feishu_inbox_error code=inbox_processing_failed')
        finally:
            if stream is not None:
                stream.close()
        stopped.wait(backoff)
        backoff = min(backoff * 2, 300)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path, default=Path('.'))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    stopped = Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    run(args.base_dir, stopped)


if __name__ == '__main__':
    main()
