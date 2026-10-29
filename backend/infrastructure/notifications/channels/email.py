"""Plain-text adapter for the existing SMTP sender."""
from backend.infrastructure.notifications.channels import ChannelResult
from backend.infrastructure.notifications.email_sender import send_email


class EmailChannel:
    def __init__(self, config: dict):
        self.config = config

    def send(self, *, title: str, body: str, image_path: str | None, message_id: str) -> ChannelResult:
        return ChannelResult("sent") if send_email(subject=title, body=body, config=self.config) else ChannelResult("retryable", "smtp_delivery_failed")
