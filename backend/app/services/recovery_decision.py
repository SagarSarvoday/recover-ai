import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.scheduled_recovery_action import ScheduledRecoveryAction
from app.schemas.recovery_analysis import (
    PaymentHistoryItem,
    PreviousRecoveryOutcome,
    RecoveryAttemptItem,
    RecoveryDecision,
    RecoveryDecisionContext,
)

SYSTEM_PROMPT = """You are a revenue recovery decision engine. Your job is to recommend the safest next recovery action for a failed payment. You do not execute actions or interact with payment gateways. You must choose exactly one allowed action. Minimize unnecessary customer contact, avoid repeated retries, respect stopping rules, and explain your reasoning briefly.

Allowed actions:
- 'retry': Generate or reuse a secure Razorpay recovery payment link and notify the customer by email. This does NOT execute a direct charge or mark payment successful.
- 'contact': Generate or reuse a secure Razorpay recovery payment link and notify the customer by email.
- 'wait': Defer recovery action until a future scheduled time. Requires wait_minutes (1-10080).
- 'skip': Do not initiate recovery at this time.
- 'close': Permanently close recovery (e.g. max attempts reached, unrecoverable card/account).

CRITICAL RULES FOR SCHEDULING AND RECOVERY LIFECYCLE:
1. When `is_scheduled_followup` is true or `wait_elapsed` is true, the scheduled wait interval has ALREADY completed. You must conduct a FRESH REASSESSMENT of the recovery context. Do NOT blindly repeat 'wait'. Repeating 'wait' is only permitted if there is an explicit newly discovered delayed dependency.
2. The fact that the previous decision was 'wait' must NEVER by itself justify another 'wait'.
3. Successful payment must NEVER be inferred or assumed by the LLM. Only verified incoming payment webhooks from the payment gateway confirm recovery.
4. If an active payment link already exists and has not expired, 'retry' or 'contact' will safely reuse that link. If the payment link has expired (`is_payment_link_expired` is true), a fresh link is required.
5. Your `reason` field MUST contain genuine analytical reasoning synthesized from the supplied context (e.g. past successful transactions, failure reason nature, elapsed wait interval, remaining attempts, revenue at risk). Do not merely repeat the provider's raw failure_reason."""

ACTION_NEXT_STEPS = {
    "retry": "Retry the payment once",
    "contact": "Contact the customer with a payment link",
    "wait": "Wait for the validated interval, then perform one controlled retry",
    "skip": "Do not initiate recovery",
    "close": "Stop recovery",
}

SYSTEM_PROMPT += """

Your `next_step` must be semantically consistent with your selected `action`. Use these action-aligned next steps: retry = "Retry the payment once"; contact = "Contact the customer with a payment link"; wait = "Wait for the validated interval, then perform one controlled retry"; skip = "Do not initiate recovery"; close = "Stop recovery". For action `wait`, you MUST provide an integer `wait_minutes` between 1 and 10080. Do not return wait_minutes for any other action. Return only the required JSON object matching the schema."""

logger = logging.getLogger(__name__)


class LLMConfigurationError(Exception):
    pass


class LLMServiceError(Exception):
    pass


class InvalidLLMOutputError(Exception):
    pass


def _sanitize_diagnostic(value: object | None) -> str | None:
    """Keep provider diagnostics useful without allowing sensitive values into logs."""
    if value is None:
        return None

    text = str(value)
    if "Recovery context:" in text or "Analyze this recovery context" in text:
        return "[redacted: provider response contained request context]"

    text = re.sub(r"(https?://)[^/@\s]+@", r"\1[redacted]@", text)
    text = re.sub(r"(?i)(api[_-]?key|authorization|password)\s*[:=]\s*[^,\s]+", r"\1=[redacted]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted-email]", text)
    text = re.sub(r"\+?\d[\d() .-]{7,}\d", "[redacted-phone]", text)
    return text[:2_000]


def _log_ollama_error(error: Exception) -> None:
    response = getattr(error, "response", None)
    status_code = getattr(error, "status_code", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)

    response_body = getattr(error, "error", None)
    if response_body is None and response is not None:
        response_body = getattr(response, "text", None)
        if response_body is None:
            response_body = getattr(response, "content", None)

    logger.error(
        "Ollama recovery-decision request failed: exception_type=%s exception_message=%s "
        "http_status=%s response_body=%s",
        type(error).__name__,
        _sanitize_diagnostic(error),
        status_code,
        _sanitize_diagnostic(response_body),
    )


def build_recovery_context(
    db: Session,
    case: RecoveryCase,
    customer: Customer | None,
    payment: Payment,
    max_recovery_attempts: int,
    *,
    trigger: str = "manual",
    now: datetime | None = None,
) -> RecoveryDecisionContext:
    now = now or datetime.now(timezone.utc)
    payment_history = []
    prior_cases = []
    if customer is not None:
        payment_history = db.scalars(
            select(Payment)
            .where(Payment.customer_id == customer.id)
            .order_by(Payment.created_at.desc())
            .limit(10)
        ).all()
        prior_cases = db.scalars(
            select(RecoveryCase)
            .where(RecoveryCase.customer_id == customer.id, RecoveryCase.id != case.id)
            .order_by(RecoveryCase.updated_at.desc())
            .limit(5)
        ).all()
    audit_logs = db.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "recovery_case", AuditLog.entity_id == case.id)
        .order_by(AuditLog.created_at.desc())
        .limit(5)
    ).all()

    def history_item(item: Payment) -> PaymentHistoryItem:
        return PaymentHistoryItem(
            status=item.status,
            amount=item.amount,
            failure_reason=item.failure_reason,
            paid_at=item.paid_at,
            created_at=item.created_at,
        )

    # Lifecycle and scheduling computations
    scheduled_action = getattr(case, "scheduled_action", None)
    scheduled_at = getattr(case, "next_action_at", None)
    if scheduled_at is not None and scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

    is_scheduled_due = bool(scheduled_at and scheduled_at <= now)
    is_scheduled_followup = trigger in {"scheduled_action", "scheduled_worker"}
    wait_elapsed = is_scheduled_followup or (
        case.ai_decision == "wait" and (is_scheduled_due or scheduled_at is None)
    )

    active_link_id = getattr(case, "razorpay_payment_link_id", None)
    link_expires_at = getattr(case, "payment_link_expires_at", None)
    if link_expires_at is not None and link_expires_at.tzinfo is None:
        link_expires_at = link_expires_at.replace(tzinfo=timezone.utc)

    is_link_expired = False
    if active_link_id is not None:
        if link_expires_at is not None:
            is_link_expired = link_expires_at <= now
        else:
            # Fallback for links created before explicit expiry column: 48h from attempt or creation
            ref_dt = case.last_attempt_at or case.created_at
            if ref_dt is not None:
                if ref_dt.tzinfo is None:
                    ref_dt = ref_dt.replace(tzinfo=timezone.utc)
                is_link_expired = (ref_dt + timedelta(hours=48)) <= now

    last_notif_status: str | None = None
    last_notif_reason: str | None = None
    for log in audit_logs:
        if log.action in ("notification_sent", "notification_failed"):
            last_notif_status = "sent" if log.action == "notification_sent" else "failed"
            if isinstance(log.details, dict):
                last_notif_reason = log.details.get("reason")
            break

    return RecoveryDecisionContext(
        case_id=case.id,
        case_status=case.status,
        amount_at_risk=case.amount_at_risk,
        amount_recovered=case.amount_recovered,
        attempt_count=case.attempt_count,
        max_recovery_attempts=max_recovery_attempts,
        existing_ai_decision=case.ai_decision,
        existing_ai_reason=case.ai_decision_note,
        last_attempt_at=case.last_attempt_at,
        payment=history_item(payment),
        customer_payment_history=[history_item(item) for item in payment_history],
        customer_has_successfully_paid_before=any(item.status == "succeeded" for item in payment_history),
        previous_recovery_outcomes=[
            PreviousRecoveryOutcome(
                status=item.status,
                amount_at_risk=item.amount_at_risk,
                amount_recovered=item.amount_recovered,
                ai_decision=item.ai_decision,
                updated_at=item.updated_at,
            )
            for item in prior_cases
        ],
        previous_recovery_attempts=[
            RecoveryAttemptItem(
                action=item.action,
                actor=item.actor,
                created_at=item.created_at,
                recommended_action=item.details.get("recommended_action"),
                outcome=item.details.get("outcome"),
            )
            for item in audit_logs
        ],
        trigger=trigger,
        is_scheduled_followup=is_scheduled_followup,
        scheduled_action=scheduled_action,
        scheduled_at=scheduled_at,
        current_time=now,
        is_scheduled_due=is_scheduled_due,
        wait_elapsed=wait_elapsed,
        active_payment_link_id=active_link_id,
        payment_link_expires_at=link_expires_at,
        is_payment_link_expired=is_link_expired,
        last_notification_status=last_notif_status,
        last_notification_failure_reason=last_notif_reason,
    )


def request_llm_decision(
    context: RecoveryDecisionContext,
    settings: Settings,
) -> RecoveryDecision:
    if settings.llm_provider != "ollama":
        raise LLMConfigurationError("The configured LLM provider is not supported.")

    try:
        from ollama import Client

        schema = RecoveryDecision.model_json_schema()
        client = Client(host=settings.ollama_base_url)
        response = client.chat(
            model=settings.ollama_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Analyze this recovery context and return a JSON object matching "
                        f"this schema: {json.dumps(schema)}\n\n"
                        f"Recovery context: {json.dumps(context.model_dump(mode='json'))}"
                    ),
                },
            ],
            format=schema,
            options={"temperature": 0},
            stream=False,
        )
    except Exception as error:
        _log_ollama_error(error)
        raise LLMServiceError("The LLM provider could not analyze this recovery case.") from error

    if not response.message.content:
        raise InvalidLLMOutputError("The LLM returned no structured output.")

    try:
        return RecoveryDecision.model_validate_json(response.message.content)
    except ValidationError as error:
        raise InvalidLLMOutputError("The LLM returned invalid structured output.") from error


def ensure_action_consistent_next_step(decision: RecoveryDecision) -> RecoveryDecision:
    """Enforce a bounded next step so it cannot contradict the selected action."""
    expected_next_step = ACTION_NEXT_STEPS[decision.action]
    if decision.next_step == expected_next_step:
        return decision
    return decision.model_copy(update={"next_step": expected_next_step})


def apply_guardrails(
    decision: RecoveryDecision,
    case: RecoveryCase,
    max_recovery_attempts: int,
    context: RecoveryDecisionContext | None = None,
) -> tuple[RecoveryDecision, list[str]]:
    guardrails_applied: list[str] = []

    if case.status in {"recovered", "closed"}:
        guardrails_applied.append("case_already_resolved")
        return (
            RecoveryDecision(
                action="close",
                reason="The recovery case is already resolved and requires no further recovery action.",
                confidence=1.0,
                next_step=ACTION_NEXT_STEPS["close"],
                stop=True,
            ),
            guardrails_applied,
        )

    if case.attempt_count >= max_recovery_attempts:
        guardrails_applied.append("maximum_recovery_attempts_reached")
        return (
            RecoveryDecision(
                action="close",
                reason="The configured maximum number of recovery attempts has been reached.",
                confidence=1.0,
                next_step=ACTION_NEXT_STEPS["close"],
                stop=True,
            ),
            guardrails_applied,
        )

    # Prevent infinite wait loops when wait interval has already completed
    if decision.action == "wait" and context is not None and context.wait_elapsed:
        guardrails_applied.append("infinite_wait_loop_prevented")
        fallback_action = "retry" if case.attempt_count < max_recovery_attempts else "close"
        return (
            RecoveryDecision(
                action=fallback_action,
                reason="Scheduled wait interval has elapsed; continuing active recovery to prevent an infinite wait loop.",
                confidence=0.85,
                next_step=ACTION_NEXT_STEPS[fallback_action],
                stop=(fallback_action == "close"),
            ),
            guardrails_applied,
        )

    return decision, guardrails_applied


def persist_analysis(
    db: Session,
    case: RecoveryCase,
    decision: RecoveryDecision,
    guardrails_applied: list[str],
    model: str,
) -> datetime:
    analyzed_at = datetime.now(timezone.utc)
    case.ai_decision = decision.action
    case.ai_decision_note = decision.reason
    case.ai_confidence = Decimal(str(round(decision.confidence, 2)))
    case.ai_wait_minutes = decision.wait_minutes
    _sync_wait_followup_schedule(db, case, decision, analyzed_at)
    db.add(
        AuditLog(
            entity_type="recovery_case",
            entity_id=case.id,
            action="ai_analysis",
            actor="ai_recovery_engine",
            details={
                "ai_generated": True,
                "recommended_action": decision.action,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "next_step": decision.next_step,
                "stop": decision.stop,
                "wait_minutes": decision.wait_minutes,
                "guardrails_applied": guardrails_applied,
                "model": model,
                "analyzed_at": analyzed_at.isoformat(),
            },
        )
    )
    db.commit()
    return analyzed_at


def _sync_wait_followup_schedule(
    db: Session,
    case: RecoveryCase,
    decision: RecoveryDecision,
    analyzed_at: datetime,
) -> None:
    """Keep the durable WAIT retry, and its case summary fields, in one transaction."""
    existing_job = db.scalar(
        select(ScheduledRecoveryAction).where(
            ScheduledRecoveryAction.recovery_case_id == case.id,
            ScheduledRecoveryAction.action == "retry",
            ScheduledRecoveryAction.status.in_(("pending", "running")),
        )
    )

    if decision.action != "wait":
        if existing_job is not None:
            existing_job.status = "skipped"
            existing_job.executed_at = analyzed_at
            existing_job.lease_expires_at = None
            existing_job.error_message = "Superseded by a later guarded AI analysis."
        case.next_action_at = None
        case.scheduled_action = None
        return

    wait_minutes = decision.wait_minutes
    # RecoveryDecision validates this already. Keep the persistence boundary defensive
    # so a caller cannot schedule an unvalidated value by constructing a model manually.
    if isinstance(wait_minutes, bool) or not isinstance(wait_minutes, int) or not 1 <= wait_minutes <= 10_080:
        raise ValueError("WAIT decisions require a validated wait_minutes value between 1 and 10080.")

    if existing_job is None:
        scheduled_at = analyzed_at + timedelta(minutes=wait_minutes)
        db.add(
            ScheduledRecoveryAction(
                recovery_case_id=case.id,
                merchant_id=case.merchant_id,
                action="retry",
                scheduled_at=scheduled_at,
                status="pending",
            )
        )
    else:
        # Re-analysis is idempotent: keep the original active job and its due time.
        scheduled_at = existing_job.scheduled_at

    case.next_action_at = scheduled_at
    case.scheduled_action = "retry"
