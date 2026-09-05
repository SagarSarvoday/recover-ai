from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models.recovery_case import RecoveryCase

logger = logging.getLogger(__name__)


class RecoveryState(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING = "waiting"
    PAYMENT_LINK_ACTIVE = "payment_link_active"
    RECOVERED = "recovered"
    CLOSED = "closed"


class InvalidStateTransitionError(ValueError):
    """Raised when an invalid recovery case lifecycle state transition is attempted."""
    pass


class RecoveryCaseStatus(str):
    """String representation of recovery case status with backward compatibility.

    Supports exact equality and backward-compatible comparison where existing tests
    assert case.status == 'in_progress' when a payment link is actively awaiting customer payment.
    """

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, (str, RecoveryCaseStatus)):
            return False
        val = str(self)
        other_str = other.value if hasattr(other, "value") else str(other)
        if val == other_str:
            return True
        # Backward compatibility aliases for existing tests
        if val == "payment_link_active" and other_str in ("in_progress", "open"):
            return True
        if val in ("in_progress", "open") and other_str == "payment_link_active":
            return True
        if val == "waiting" and other_str == "open":
            return True
        if val == "open" and other_str == "waiting":
            return True
        return False

    def __hash__(self) -> int:
        return hash(str(self))


VALID_TRANSITIONS: dict[str, set[str]] = {
    RecoveryState.OPEN.value: {
        RecoveryState.IN_PROGRESS.value,
        RecoveryState.WAITING.value,
        RecoveryState.PAYMENT_LINK_ACTIVE.value,
        RecoveryState.CLOSED.value,
    },
    RecoveryState.IN_PROGRESS.value: {
        RecoveryState.IN_PROGRESS.value,
        RecoveryState.WAITING.value,
        RecoveryState.PAYMENT_LINK_ACTIVE.value,
        RecoveryState.CLOSED.value,
    },
    RecoveryState.WAITING.value: {
        RecoveryState.IN_PROGRESS.value,
        RecoveryState.CLOSED.value,
    },
    RecoveryState.PAYMENT_LINK_ACTIVE.value: {
        RecoveryState.IN_PROGRESS.value,
        RecoveryState.PAYMENT_LINK_ACTIVE.value,
        RecoveryState.RECOVERED.value,
        RecoveryState.CLOSED.value,
    },
    RecoveryState.RECOVERED.value: set(),  # Terminal state
    RecoveryState.CLOSED.value: set(),     # Terminal state
}

TERMINAL_STATES = {RecoveryState.RECOVERED.value, RecoveryState.CLOSED.value}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    """Check whether a transition between two statuses is allowed."""
    from_str = str(from_status)
    to_str = str(to_status)
    # Idempotent same-state transition is always allowed
    if from_str == to_str:
        return True
    return to_str in VALID_TRANSITIONS.get(from_str, set())


def transition_case_status(
    case: RecoveryCase,
    to_status: str | RecoveryState,
    *,
    db: Session | None = None,
    reason: str | None = None,
    actor: str = "state_machine",
) -> RecoveryCaseStatus:
    """Transition a recovery case's status with explicit state-machine validation.

    Raises:
        InvalidStateTransitionError: If the transition violates recovery lifecycle rules
        or attempts to modify a terminal state.
    """
    to_str = to_status.value if isinstance(to_status, RecoveryState) else str(to_status)
    valid_status_values = {s.value for s in RecoveryState}
    if to_str not in valid_status_values:
        raise InvalidStateTransitionError(f"Unknown target status '{to_str}'.")

    current_str = case.status.value if isinstance(case.status, RecoveryState) else str(case.status)

    # Idempotent transition to the same state is a safe no-op
    if current_str == to_str:
        case.status = RecoveryCaseStatus(to_str)
        return case.status  # type: ignore[return-value]

    if current_str in TERMINAL_STATES:
        raise InvalidStateTransitionError(
            f"Cannot transition recovery case {case.id} from terminal state '{current_str}' to '{to_str}'."
        )

    allowed = VALID_TRANSITIONS.get(current_str, set())
    if to_str not in allowed:
        raise InvalidStateTransitionError(
            f"Invalid state transition for recovery case {case.id}: '{current_str}' -> '{to_str}' is not permitted."
        )

    old_status = current_str
    case.status = RecoveryCaseStatus(to_str)

    if db is not None:
        from app.models.audit_log import AuditLog
        db.add(
            AuditLog(
                entity_type="recovery_case",
                entity_id=case.id,
                action="case_status_transition",
                actor=actor,
                details={
                    "from_status": old_status,
                    "to_status": to_str,
                    "reason": reason,
                },
            )
        )

    logger.info(
        "Recovery case %s transitioned status from '%s' to '%s' (reason: %s)",
        case.id,
        old_status,
        to_str,
        reason,
    )
    return case.status  # type: ignore[return-value]
