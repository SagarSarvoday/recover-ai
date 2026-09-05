"""Autonomous recovery agent: bounded OBSERVE → THINK → GUARD → ACT → OBSERVE RESULT cycle.

The agent is purely internal.  It is never exposed to the LLM, never invoked
directly by a merchant, and never holds open an unbounded loop or asyncio task.
Every invocation is a bounded function call driven by a system event.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.schemas.recovery_actions import RecoveryActionContext
from app.services.razorpay_service import RazorpayService
from app.services.recovery_actions import (
    RecoveryActionExecutionContext,
    execute_recovery_action,
)
from app.services.recovery_decision import (
    InvalidLLMOutputError,
    LLMConfigurationError,
    LLMServiceError,
    apply_guardrails,
    build_recovery_context,
    ensure_action_consistent_next_step,
    persist_analysis,
    request_llm_decision,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoveryAgentResult:
    """Bounded result of one agent invocation (possibly multiple cycles)."""

    case_id: UUID
    trigger: str
    cycles_executed: int
    final_action: str | None
    final_outcome: str | None
    stopped_reason: str
    ai_reasoning: str | None


def _audit_agent_event(
    db: Session,
    case_id: UUID,
    action: str,
    *,
    trigger: str | None = None,
    cycle: int | None = None,
    details: dict | None = None,
) -> None:
    """Record a recovery agent lifecycle event in the audit log."""
    safe_details: dict = {}
    if trigger:
        safe_details["trigger"] = trigger
    if cycle is not None:
        safe_details["cycle"] = cycle
    if details:
        safe_details.update(details)
    db.add(
        AuditLog(
            entity_type="recovery_case",
            entity_id=case_id,
            action=action,
            actor="recovery_agent",
            details=safe_details,
        )
    )
    db.commit()


def run_recovery_agent(
    db: Session,
    case_id: UUID,
    *,
    trigger: str,
    max_recovery_attempts: int,
    settings: Settings,
    razorpay_service: RazorpayService | None = None,
) -> RecoveryAgentResult:
    """Run one bounded autonomous recovery agent lifecycle.

    The agent executes OBSERVE → THINK → GUARD → ACT → OBSERVE RESULT and may
    iterate when a RETRY fails and attempt capacity remains.  Iteration is
    bounded by ``max_recovery_attempts``; there is no recursion or unbounded
    loop.  Every cycle is idempotent with respect to audit events and the
    attempt counter is the single source of truth for capacity.
    """
    cycles_executed = 0
    final_action: str | None = None
    final_outcome: str | None = None
    ai_reasoning: str | None = None

    _audit_agent_event(db, case_id, "recovery_agent_started", trigger=trigger, cycle=0)

    # Hard upper bound: max_recovery_attempts + 1 iterations allows the final
    # observation to detect exhausted capacity after the last retry.
    for cycle in range(1, max_recovery_attempts + 2):

        # ── OBSERVE ─────────────────────────────────────────────────
        case = db.get(RecoveryCase, case_id)
        if case is None:
            _audit_agent_event(
                db, case_id, "recovery_agent_stopped",
                trigger=trigger, cycle=cycle,
                details={"reason": "case_not_found"},
            )
            return RecoveryAgentResult(
                case_id=case_id, trigger=trigger,
                cycles_executed=cycles_executed,
                final_action=None, final_outcome=None,
                stopped_reason="case_not_found", ai_reasoning=None,
            )

        if case.status in {"recovered", "closed"}:
            reason = f"case_already_{case.status}"
            _audit_agent_event(
                db, case_id, "recovery_agent_stopped",
                trigger=trigger, cycle=cycle,
                details={"reason": reason},
            )
            return RecoveryAgentResult(
                case_id=case_id, trigger=trigger,
                cycles_executed=cycles_executed,
                final_action=final_action, final_outcome=final_outcome,
                stopped_reason=reason, ai_reasoning=ai_reasoning,
            )

        if case.attempt_count >= max_recovery_attempts:
            _audit_agent_event(
                db, case_id, "recovery_agent_stopped",
                trigger=trigger, cycle=cycle,
                details={"reason": "max_attempts_reached"},
            )
            return RecoveryAgentResult(
                case_id=case_id, trigger=trigger,
                cycles_executed=cycles_executed,
                final_action=final_action, final_outcome=final_outcome,
                stopped_reason="max_attempts_reached", ai_reasoning=ai_reasoning,
            )

        customer = db.get(Customer, case.customer_id) if case.customer_id else None
        payment = db.get(Payment, case.payment_id)
        if payment is None:
            _audit_agent_event(
                db, case_id, "recovery_agent_stopped",
                trigger=trigger, cycle=cycle,
                details={"reason": "payment_not_found"},
            )
            return RecoveryAgentResult(
                case_id=case_id, trigger=trigger,
                cycles_executed=cycles_executed,
                final_action=None, final_outcome=None,
                stopped_reason="payment_not_found", ai_reasoning=None,
            )

        context = build_recovery_context(
            db, case, customer, payment, max_recovery_attempts, trigger=trigger
        )

        # ── THINK ───────────────────────────────────────────────────
        try:
            llm_decision = request_llm_decision(context, settings)
            llm_decision = ensure_action_consistent_next_step(llm_decision)
        except (LLMConfigurationError, LLMServiceError, InvalidLLMOutputError) as error:
            logger.warning(
                "Recovery agent LLM call failed for case %s: %s",
                case_id, type(error).__name__,
            )
            _audit_agent_event(
                db, case_id, "recovery_agent_stopped",
                trigger=trigger, cycle=cycle,
                details={"reason": "llm_error", "error_type": type(error).__name__},
            )
            return RecoveryAgentResult(
                case_id=case_id, trigger=trigger,
                cycles_executed=cycles_executed,
                final_action=None, final_outcome=None,
                stopped_reason="llm_error", ai_reasoning=None,
            )

        # ── GUARD ───────────────────────────────────────────────────
        decision, guardrails_applied = apply_guardrails(
            llm_decision, case, max_recovery_attempts, context=context
        )
        ai_reasoning = decision.reason

        # ── PERSIST ANALYSIS ────────────────────────────────────────
        persist_analysis(db, case, decision, guardrails_applied, settings.ollama_model)

        _audit_agent_event(
            db, case_id, "recovery_agent_analysis",
            trigger=trigger, cycle=cycle,
            details={
                "action": decision.action,
                "confidence": decision.confidence,
                "guardrails_applied": guardrails_applied,
                "ai_reasoning": decision.reason,
            },
        )

        # Guardrails forced a terminal stop.
        if decision.stop:
            final_action = decision.action
            final_outcome = "stopped"
            cycles_executed += 1
            _audit_agent_event(
                db, case_id, "recovery_agent_stopped",
                trigger=trigger, cycle=cycle,
                details={"reason": "guardrail_stop", "action": decision.action},
            )
            return RecoveryAgentResult(
                case_id=case_id, trigger=trigger,
                cycles_executed=cycles_executed,
                final_action=final_action, final_outcome=final_outcome,
                stopped_reason="guardrail_stop",
                ai_reasoning=ai_reasoning,
            )

        # ── ACT ─────────────────────────────────────────────────────
        action_result = execute_recovery_action(
            decision.action,
            case.id,
            RecoveryActionExecutionContext(
                db=db,
                max_recovery_attempts=max_recovery_attempts,
                action_context=RecoveryActionContext(
                    source="recovery_agent",
                    note=f"autonomous_cycle_{cycle}_{trigger}",
                ),
                actor="recovery_agent",
                razorpay_service=razorpay_service,
            ),
            wait_minutes=decision.wait_minutes,
        )

        final_action = decision.action
        final_outcome = action_result.outcome
        cycles_executed += 1

        _audit_agent_event(
            db, case_id, "recovery_agent_action",
            trigger=trigger, cycle=cycle,
            details={
                "action": decision.action,
                "tool_action": action_result.action,
                "outcome": action_result.outcome,
                "success": action_result.success,
                "message": action_result.message,
            },
        )

        # ── OBSERVE RESULT ──────────────────────────────────────────
        if action_result.outcome == "recovered":
            _audit_agent_event(
                db, case_id, "recovery_agent_stopped",
                trigger=trigger, cycle=cycle,
                details={"reason": "recovered"},
            )
            return RecoveryAgentResult(
                case_id=case_id, trigger=trigger,
                cycles_executed=cycles_executed,
                final_action=final_action, final_outcome="recovered",
                stopped_reason="recovered",
                ai_reasoning=ai_reasoning,
            )

        # CONTACT or RETRY → payment link created/reused & notification handled → stop, wait for webhook.
        if decision.action in {"contact", "retry"}:
            if action_result.outcome == "blocked":
                _audit_agent_event(
                    db, case_id, "recovery_agent_stopped",
                    trigger=trigger, cycle=cycle,
                    details={
                        "reason": "action_blocked",
                        "guardrails": action_result.guardrails_applied,
                    },
                )
                return RecoveryAgentResult(
                    case_id=case_id, trigger=trigger,
                    cycles_executed=cycles_executed,
                    final_action=final_action, final_outcome=final_outcome,
                    stopped_reason="action_blocked",
                    ai_reasoning=ai_reasoning,
                )

            if action_result.outcome == "failed":
                _audit_agent_event(
                    db, case_id, "recovery_agent_continuation",
                    trigger=trigger, cycle=cycle,
                    details={"reason": f"{decision.action}_failed_continuing"},
                )
                continue

            stopped_reason = f"{decision.action}_pending_webhook"
            _audit_agent_event(
                db, case_id, "recovery_agent_stopped",
                trigger=trigger, cycle=cycle,
                details={"reason": stopped_reason},
            )
            return RecoveryAgentResult(
                case_id=case_id, trigger=trigger,
                cycles_executed=cycles_executed,
                final_action=final_action, final_outcome=final_outcome,
                stopped_reason=stopped_reason,
                ai_reasoning=ai_reasoning,
            )

        # WAIT → durable schedule persisted → stop, scheduler will resume.
        if decision.action == "wait":
            _audit_agent_event(
                db, case_id, "recovery_agent_stopped",
                trigger=trigger, cycle=cycle,
                details={"reason": "wait_scheduled"},
            )
            return RecoveryAgentResult(
                case_id=case_id, trigger=trigger,
                cycles_executed=cycles_executed,
                final_action=final_action, final_outcome=final_outcome,
                stopped_reason="wait_scheduled",
                ai_reasoning=ai_reasoning,
            )

        # CLOSE / SKIP → stop.
        if decision.action in {"close", "skip"}:
            _audit_agent_event(
                db, case_id, "recovery_agent_stopped",
                trigger=trigger, cycle=cycle,
                details={"reason": "recovery_closed"},
            )
            return RecoveryAgentResult(
                case_id=case_id, trigger=trigger,
                cycles_executed=cycles_executed,
                final_action=final_action, final_outcome=final_outcome,
                stopped_reason="recovery_closed",
                ai_reasoning=ai_reasoning,
            )

        # Unexpected outcome → stop safely.
        logger.warning(
            "Recovery agent encountered unexpected outcome for case %s: %s",
            case_id, action_result.outcome,
        )
        _audit_agent_event(
            db, case_id, "recovery_agent_stopped",
            trigger=trigger, cycle=cycle,
            details={"reason": "unexpected_outcome", "outcome": action_result.outcome},
        )
        break

    # Exited the bounded loop (either break or all iterations exhausted).
    stopped_reason = (
        "max_cycles_reached"
        if final_outcome == "failed"
        else (final_outcome or "completed")
    )
    return RecoveryAgentResult(
        case_id=case_id, trigger=trigger,
        cycles_executed=cycles_executed,
        final_action=final_action, final_outcome=final_outcome,
        stopped_reason=stopped_reason,
        ai_reasoning=ai_reasoning,
    )
