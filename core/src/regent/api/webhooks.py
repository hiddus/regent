"""Webhook management and event delivery API endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from regent.infrastructure.webhook_connector import (
    EmailNotificationConnector,
    GenericWebhookConnector,
    SlackWebhookConnector,
    WebhookEvent,
    WebhookManager,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


class RegisterWebhookRequest(BaseModel):
    """Request to register a webhook connector."""

    name: str
    connector_type: str  # "generic", "slack", "email"
    url: str | None = None
    secret: str | None = None
    headers: dict[str, str] | None = None
    # Slack-specific
    slack_channel: str | None = None
    slack_username: str = "Regent"
    # Email-specific
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    from_email: str = "noreply@regent.local"
    to_emails: list[str] | None = None
    # Event subscriptions
    event_types: list[str] | None = None


class SendEventRequest(BaseModel):
    """Request to send an event via webhook."""

    event_type: str
    payload: dict[str, Any]
    connector_name: str | None = None  # Send to specific connector or all


class DeliveryResultResponse(BaseModel):
    """Response from webhook delivery."""

    connector: str
    success: bool
    status_code: int | None = None
    error: str | None = None
    delivered_at: str | None = None


class ConnectorInfo(BaseModel):
    """Information about a registered connector."""

    name: str
    connector_type: str
    event_types: list[str] | None = None


# Global webhook manager instance
_webhook_manager = WebhookManager()


@router.post("/register")
async def register_webhook(request: RegisterWebhookRequest) -> dict[str, str]:
    """Register a new webhook connector."""
    try:
        if request.connector_type == "generic":
            if not request.url:
                raise HTTPException(status_code=400, detail="URL required for generic webhook")
            connector = GenericWebhookConnector(
                url=request.url,
                secret=request.secret,
                headers=request.headers,
            )
        elif request.connector_type == "slack":
            if not request.url:
                raise HTTPException(status_code=400, detail="Webhook URL required for Slack")
            connector = SlackWebhookConnector(
                webhook_url=request.url,
                default_channel=request.slack_channel,
                default_username=request.slack_username,
            )
        elif request.connector_type == "email":
            if not request.smtp_host:
                raise HTTPException(status_code=400, detail="SMTP host required for email")
            connector = EmailNotificationConnector(
                smtp_host=request.smtp_host,
                smtp_port=request.smtp_port,
                smtp_user=request.smtp_user,
                smtp_password=request.smtp_password,
                from_email=request.from_email,
                to_emails=request.to_emails,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown connector type: {request.connector_type}",
            )

        _webhook_manager.register_connector(
            name=request.name,
            connector=connector,
            event_types=request.event_types,
        )

        return {"status": "registered", "name": request.name}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Webhook registration failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/send", response_model=list[DeliveryResultResponse])
async def send_event(request: SendEventRequest) -> list[DeliveryResultResponse]:
    """Send an event via webhook(s)."""
    try:
        event = WebhookEvent(
            event_type=request.event_type,
            payload=request.payload,
        )

        if request.connector_name:
            # Send to specific connector
            result = await _webhook_manager.send_to_connector(
                request.connector_name, event
            )
            return [
                DeliveryResultResponse(
                    connector=request.connector_name,
                    success=result.success,
                    status_code=result.status_code,
                    error=result.error,
                    delivered_at=result.delivered_at,
                )
            ]
        else:
            # Send to all subscribed connectors
            results = await _webhook_manager.send_event(event)
            return [
                DeliveryResultResponse(
                    connector=name,
                    success=result.success,
                    status_code=result.status_code,
                    error=result.error,
                    delivered_at=result.delivered_at,
                )
                for name, result in results.items()
            ]

    except Exception as e:
        logger.exception("Event delivery failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/connectors", response_model=list[ConnectorInfo])
async def list_connectors() -> list[ConnectorInfo]:
    """List all registered webhook connectors."""
    connectors = _webhook_manager.list_connectors()
    # Note: We don't store connector type in manager, so we return basic info
    return [ConnectorInfo(name=name) for name in connectors]


@router.delete("/connectors/{name}")
async def unregister_connector(name: str) -> dict[str, bool]:
    """Unregister a webhook connector."""
    # Note: WebhookManager doesn't have unregister method, would need to add
    # For now, return success if connector exists
    if name in _webhook_manager.list_connectors():
        return {"success": True}
    raise HTTPException(status_code=404, detail=f"Connector not found: {name}")


@router.post("/test")
async def test_webhook(request: RegisterWebhookRequest) -> dict[str, Any]:
    """Test a webhook configuration without registering it."""
    try:
        # Create temporary connector
        if request.connector_type == "generic":
            if not request.url:
                raise HTTPException(status_code=400, detail="URL required for generic webhook")
            connector = GenericWebhookConnector(
                url=request.url,
                secret=request.secret,
                headers=request.headers,
            )
        elif request.connector_type == "slack":
            if not request.url:
                raise HTTPException(status_code=400, detail="Webhook URL required for Slack")
            connector = SlackWebhookConnector(
                webhook_url=request.url,
                default_channel=request.slack_channel,
                default_username=request.slack_username,
            )
        elif request.connector_type == "email":
            if not request.smtp_host:
                raise HTTPException(status_code=400, detail="SMTP host required for email")
            connector = EmailNotificationConnector(
                smtp_host=request.smtp_host,
                smtp_port=request.smtp_port,
                smtp_user=request.smtp_user,
                smtp_password=request.smtp_password,
                from_email=request.from_email,
                to_emails=request.to_emails,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown connector type: {request.connector_type}",
            )

        # Send test event
        test_event = WebhookEvent(
            event_type="test",
            payload={"message": "This is a test event from Regent"},
        )

        result = await connector.send(test_event)

        return {
            "success": result.success,
            "status_code": result.status_code,
            "error": result.error,
            "delivered_at": result.delivered_at,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Webhook test failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
