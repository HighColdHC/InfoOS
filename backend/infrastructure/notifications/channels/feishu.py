"""Private bot messages through the official lark-cli credential store."""
from pathlib import Path

from backend.infrastructure.notifications.channels import ChannelResult
from backend.infrastructure.notifications.lark_cli import LarkCli, LarkCliError


class FeishuChannel:
    def __init__(self, config: dict, *, cli: LarkCli | None = None):
        self.config = config
        self.cli = LarkCli() if cli is None else cli

    def send(self, *, title: str, body: str, image_path: str | None, message_id: str) -> ChannelResult:
        command = ['im', '+messages-send', '--as', 'bot', '--user-id', self.config['receive_id']]
        text_key = message_id if image_path is None else message_id[:46] + '-t'
        try:
            response = self.cli.run([*command, '--text', f'{title}\n{body}', '--idempotency-key', text_key])
            if image_path is not None:
                image = Path(image_path).resolve()
                response = self.cli.run([*command, '--image', './' + image.name,
                    '--idempotency-key', message_id[:46] + '-i'], cwd=image.parent)
            provider_id = response.get('message_id')
            if provider_id is not None and not isinstance(provider_id, str):
                raise LarkCliError('lark_invalid_response')
            return ChannelResult('sent', provider_message_id=provider_id)
        except LarkCliError as exc:
            return ChannelResult('retryable' if exc.retryable else 'failed', exc.code)
