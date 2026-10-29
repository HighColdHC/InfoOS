from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any

from backend.infrastructure.observability.logging import get_logger

logger = get_logger("notifications.email")


def send_email(*, subject: str, body: str, config: dict[str, Any]) -> bool:
    """Send email via SMTP. Returns True on success, False on failure."""
    try:
        email_cfg = config["email"]
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = email_cfg["from_email"]
        msg["To"] = email_cfg["to_email"]
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP(email_cfg["smtp_host"], email_cfg["smtp_port"], timeout=15) as server:
            server.starttls()
            server.login(email_cfg["username"], email_cfg["password"])
            server.send_message(msg)
    except Exception:  # noqa: BLE001 - MIME and SMTP library failures must return a logged delivery failure
        logger.error("smtp delivery failed")
        return False
    return True
