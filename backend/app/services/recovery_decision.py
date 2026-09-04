import json
import logging
import re
from datetime import datetime, timedelta, timezone

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

SYSTEM_PROMPT = """You are a revenue recovery decision engine. Your job is to recommend the safest next recovery action for a failed payment. You do not execute actions. You must choose exactly one allowed action. Minimize unnecessary customer contact, avoid repeated retries, respect stopping rules, and explain your reasoning briefly.

Consider the complete supplied context: current payment, revenue at risk, customer payment history, prior recovery outcomes, and previous attempt logs. Do NOT merely repeat or restate the provider's failure_reason in your explanation (for example, do not just output "Temporary issue with payment processing"). Your `reason` field MUST contain genuine analytical reasoning synthesized from the supplied context (e.g. customer's history of successful payments, transient nature of failure, absence/presence of prior attempts, amount at risk). Do not manufacture facts absent from the context. Never suggest payment execution, database operations, arbitrary code, or contacting the customer directly. Return only the required JSON object."""

ACTION_NEXT_STEPS = {
    "retry": "Retry the payment once",
    "contact": "Contact the customer with a payment link",
    "wait": "Wait for the validated interval, then perform one controlled retry",
    "skip": "Do not initiate recovery",
    "close": "Stop recovery",
}

SYSTEM_PROMPT += """

Your `next_step` must be semantically consistent with your selected `action`. Use these action-aligned next steps: retry = \"Retry the payment once\"; contact = \"Contact the customer with a payment link\"; wait = \"Wait for the validated interval, then perform one controlled retry\"; skip = \"Do not initiate recovery\"; close = \"Stop recovery\". For action `wait`, you MUST provide an integer `wait_minutes` between 1 and 10080. The scheduled follow-up is one controlled retry; do not return wait_minutes for any other action."""

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
) -> RecoveryDecisionContext:
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
