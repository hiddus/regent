"""Webhook and external service connectors.

Provides:
- Generic webhook connector for sending events to external services
- Slack integration for notifications
- Email notification support
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


@dataclass
class WebhookEvent:
    """Event to be sent via webhook."""

    event_type: str
    payload: dict[str, Any]
    timestamp: str | None = None
    event_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now(UTC).isoformat()
        if self.event_id is None:
            import uuid

            self.event_id = str(uuid.uuid4())


@dataclass
class WebhookDeliveryResult:
    """Result of a webhook delivery."""

    success: bool
    status_code: int | None = None
    response_body: str | None = None
    error: str | None = None
    delivered_at: str | None = None


class WebhookConnector(Protocol):
    """Protocol for webhook connectors."""

    async def send(self, event: WebhookEvent) -> WebhookDeliveryResult:
        """Send an event via webhook."""
        ...


# ---------------------------------------------------------------------------
# Generic Webhook Connector
# ---------------------------------------------------------------------------


class GenericWebhookConnector:
    """Send events to a generic webhook endpoint."""

    def __init__(
        self,
        *,
        url: str,
        secret: str | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._url = url
        self._secret = secret
        self._headers = headers or {}
        self._timeout_seconds = timeout_seconds

    async def send(self, event: WebhookEvent) -> WebhookDeliveryResult:
        """Send event to webhook URL."""
        payload = {
            "event_type": event.event_type,
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "payload": event.payload,
        }
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        headers = {
            "Content-Type": "application/json",
            **self._headers,
        }

        # Add signature if secret is configured
        if self._secret:
            signature = hmac.new(
                self._secret.encode(),
                payload_json.encode(),
                hashlib.sha256,
            ).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={signature}"

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    self._url,
                    content=payload_json,
                    headers=headers,
                )

                return WebhookDeliveryResult(
                    success=200 <= response.status_code < 300,
                    status_code=response.status_code,
                    response_body=response.text[:1000],
                    delivered_at=datetime.now(UTC).isoformat(),
                )

        except Exception as e:
            logger.exception("Webhook delivery failed")
            return WebhookDeliveryResult(
                success=False,
                error=str(e),
                delivered_at=datetime.now(UTC).isoformat(),
            )


# ---------------------------------------------------------------------------
# Slack Webhook Connector
# ---------------------------------------------------------------------------


class SlackWebhookConnector:
    """Send notifications to Slack via incoming webhook."""

    def __init__(
        self,
        *,
        webhook_url: str,
        default_channel: str | None = None,
        default_username: str = "Regent",
        default_icon: str = ":robot_face:",
    ) -> None:
        self._webhook_url = webhook_url
        self._default_channel = default_channel
        self._default_username = default_username
        self._default_icon = default_icon

    async def send(self, event: WebhookEvent) -> WebhookDeliveryResult:
        """Send event to Slack."""
        # Format message for Slack
        message = self._format_message(event)

        payload = {
            "text": message,
            "username": self._default_username,
            "icon_emoji": self._default_icon,
        }

        if self._default_channel:
            payload["channel"] = self._default_channel

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self._webhook_url,
                    json=payload,
                )

                return WebhookDeliveryResult(
                    success=200 <= response.status_code < 300,
                    status_code=response.status_code,
                    response_body=response.text[:1000],
                    delivered_at=datetime.now(UTC).isoformat(),
                )

        except Exception as e:
            logger.exception("Slack webhook delivery failed")
            return WebhookDeliveryResult(
                success=False,
                error=str(e),
                delivered_at=datetime.now(UTC).isoformat(),
            )

    def _format_message(self, event: WebhookEvent) -> str:
        """Format event as Slack message."""
        event_type = event.event_type.replace("_", " ").title()
        timestamp = event.timestamp or datetime.now(UTC).isoformat()

        # Build message based on event type
        if event.event_type == "goal_completed":
            goal_name = event.payload.get("goal_name", "Unknown")
            return f"✅ *Goal Completed*\n{goal_name}\n<{timestamp}>"

        elif event.event_type == "goal_failed":
            goal_name = event.payload.get("goal_name", "Unknown")
            error = event.payload.get("error", "Unknown error")
            return f"❌ *Goal Failed*\n{goal_name}\nError: {error}\n<{timestamp}>"

        elif event.event_type == "preview_ready":
            preview_url = event.payload.get("url", "")
            return f"🚀 *Preview Ready*\n<{preview_url}|View Preview>\n<{timestamp}>"

        elif event.event_type == "artifact_generated":
            artifact_type = event.payload.get("type", "artifact")
            return f"📦 *Artifact Generated*\nType: {artifact_type}\n<{timestamp}>"

        else:
            # Generic message
            payload_summary = json.dumps(event.payload, indent=2, ensure_ascii=False)[:500]
            return f"*{event_type}*\n```\n{payload_summary}\n```\n<{timestamp}>"


# ---------------------------------------------------------------------------
# Email Notification Connector
# ---------------------------------------------------------------------------


class EmailNotificationConnector:
    """Send email notifications via SMTP or email API."""

    def __init__(
        self,
        *,
        smtp_host: str | None = None,
        smtp_port: int = 587,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        from_email: str = "noreply@regent.local",
        to_emails: list[str] | None = None,
        use_tls: bool = True,
    ) -> None:
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        self._smtp_password = smtp_password
        self._from_email = from_email
        self._to_emails = to_emails or []
        self._use_tls = use_tls

    async def send(self, event: WebhookEvent) -> WebhookDeliveryResult:
        """Send email notification."""
        if not self._smtp_host:
            return WebhookDeliveryResult(
                success=False,
                error="SMTP host not configured",
                delivered_at=datetime.now(UTC).isoformat(),
            )

        if not self._to_emails:
            return WebhookDeliveryResult(
                success=False,
                error="No recipient emails configured",
                delivered_at=datetime.now(UTC).isoformat(),
            )

        # Format email
        subject, body = self._format_email(event)

        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self._from_email
            msg["To"] = ", ".join(self._to_emails)
            msg.set_content(body)

            # Send via SMTP
            if self._use_tls:
                server = smtplib.SMTP(self._smtp_host, self._smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP(self._smtp_host, self._smtp_port)

            if self._smtp_user and self._smtp_password:
                server.login(self._smtp_user, self._smtp_password)

            server.send_message(msg)
            server.quit()

            return WebhookDeliveryResult(
                success=True,
                status_code=200,
                delivered_at=datetime.now(UTC).isoformat(),
            )

        except Exception as e:
            logger.exception("Email delivery failed")
            return WebhookDeliveryResult(
                success=False,
                error=str(e),
                delivered_at=datetime.now(UTC).isoformat(),
            )

    def _format_email(self, event: WebhookEvent) -> tuple[str, str]:
        """Format event as email subject and body."""
        event_type = event.event_type.replace("_", " ").title()
        timestamp = event.timestamp or datetime.now(UTC).isoformat()

        subject = f"[Regent] {event_type}"

        body_parts = [
            f"Event: {event_type}",
            f"Event ID: {event.event_id}",
            f"Timestamp: {timestamp}",
            "",
            "Payload:",
            json.dumps(event.payload, indent=2, ensure_ascii=False),
            "",
            "---",
            "This is an automated notification from Regent.",
        ]

        return subject, "\n".join(body_parts)


# ---------------------------------------------------------------------------
# Webhook Manager
# ---------------------------------------------------------------------------


class WebhookManager:
    """Manage multiple webhook connectors and route events."""

    def __init__(self) -> None:
        self._connectors: dict[str, WebhookConnector] = {}
        self._event_subscriptions: dict[str, list[str]] = {}

    def register_connector(
        self,
        name: str,
        connector: WebhookConnector,
        event_types: list[str] | None = None,
    ) -> None:
        """Register a webhook connector."""
        self._connectors[name] = connector
        if event_types:
            self._event_subscriptions[name] = event_types

    async def send_event(self, event: WebhookEvent) -> dict[str, WebhookDeliveryResult]:
        """Send event to all subscribed connectors."""
        results: dict[str, WebhookDeliveryResult] = {}

        for name, connector in self._connectors.items():
            # Check if connector subscribes to this event type
            subscriptions = self._event_subscriptions.get(name)
            if subscriptions and event.event_type not in subscriptions:
                continue

            results[name] = await connector.send(event)

        return results

    async def send_to_connector(
        self, connector_name: str, event: WebhookEvent
    ) -> WebhookDeliveryResult:
        """Send event to a specific connector."""
        connector = self._connectors.get(connector_name)
        if not connector:
            return WebhookDeliveryResult(
                success=False,
                error=f"Connector not found: {connector_name}",
                delivered_at=datetime.now(UTC).isoformat(),
            )
        return await connector.send(event)

    def list_connectors(self) -> list[str]:
        """List all registered connectors."""
        return list(self._connectors.keys())
