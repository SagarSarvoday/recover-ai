import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.razorpay import RazorpayWebhookEnvelope, RazorpayWebhookResponse
from app.services.razorpay_service import RazorpayConfigurationError, RazorpayService
from app.services.razorpay_webhooks import (
    SUPPORTED_RAZORPAY_EVENTS,
    InvalidRazorpayWebhookPayloadError,
    IncompleteRazorpayPaymentPayloadError,
    extract_external_entity_id,
    ingest_payment_link_paid_webhook,
    ingest_payment_failed_webhook,
    record_webhook_event,
)

router = APIRouter(tags=["webhooks"])


def get_razorpay_service() -> RazorpayService:
    return RazorpayService(settings)


@router.post(
    "/webhooks/razorpay",
    response_model=RazorpayWebhookResponse,
    summary="Receive Razorpay Test Mode webhooks",
)
async def receive_razorpay_webhook(
    request: Request,
    db: Session = Depends(get_db),
    razorpay_service: RazorpayService = Depends(get_razorpay_service),
) -> RazorpayWebhookResponse:
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature")
    event_id = request.headers.get("x-razorpay-event-id")

    if not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Razorpay webhook signature.",
        )
    if not event_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Razorpay webhook event identifier.",
        )

    try:
        is_valid = razorpay_service.verify_webhook_signature(raw_body, signature)
    except RazorpayConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Razorpay webhook verification is not configured.",
        ) from None
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Razorpay webhook signature.",
        )

    try:
        payload = json.loads(raw_body)

        webhook = RazorpayWebhookEnvelope.model_validate(payload)

        external_entity_id = extract_external_entity_id(webhook)

    except (json.JSONDecodeError, ValidationError, InvalidRazorpayWebhookPayloadError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Razorpay webhook payload.",
        ) from None

    try:
        if webhook.event == "payment.failed":
            processing_status = ingest_payment_failed_webhook(
                db,
                razorpay_event_id=event_id,
                webhook=webhook,
            )
            if processing_status == "duplicate":
                return RazorpayWebhookResponse(
                    event_id=event_id,
                    event_type=webhook.event,
                    status="duplicate",
                )
            return RazorpayWebhookResponse(
                event_id=event_id,
                event_type=webhook.event,
                status="processed",
            )
        if webhook.event == "payment_link.paid":
            processing_status = ingest_payment_link_paid_webhook(
                db,
                razorpay_event_id=event_id,
                webhook=webhook,
            )
            return RazorpayWebhookResponse(
                event_id=event_id,
                event_type=webhook.event,
                status=processing_status,
            )

        processing_status = "processed" if webhook.event in SUPPORTED_RAZORPAY_EVENTS else "ignored"
        inserted = record_webhook_event(
            db,
            razorpay_event_id=event_id,
            event_type=webhook.event,
            external_entity_id=external_entity_id,
            processing_status=processing_status,
        )
    except IncompleteRazorpayPaymentPayloadError:
        db.rollback()
        inserted = record_webhook_event(
            db,
            razorpay_event_id=event_id,
            event_type=webhook.event,
            external_entity_id=external_entity_id,
            processing_status="ignored",
        )
        return RazorpayWebhookResponse(
            event_id=event_id,
            event_type=webhook.event,
            status="ignored" if inserted else "duplicate",
        )
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to record Razorpay webhook delivery.",
        ) from None

    if not inserted:
        return RazorpayWebhookResponse(
            event_id=event_id,
            event_type=webhook.event,
            status="duplicate",
        )
    return RazorpayWebhookResponse(
        event_id=event_id,
        event_type=webhook.event,
        status=processing_status,
    )
