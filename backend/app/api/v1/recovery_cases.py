from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import settings
from app.core.security import get_current_merchant
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.schemas.recovery_case import RecoveryCaseResponse
from app.schemas.recovery_analysis import RecoveryAnalysisResponse
from app.schemas.recovery_actions import (
    PersistedAIRecommendation,
    RecoveryActionContext,
    RecoveryActionExecutionResponse,
    RecoveryWorkflowResponse,
)
from app.services.recovery_actions import (
    InvalidRecoveryActionError,
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

router = APIRouter(tags=["recovery-cases"])


def _get_owned_recovery_case(db: Session, case_id: UUID, merchant_id: UUID) -> RecoveryCase:
    case = db.get(RecoveryCase, case_id)
    if case is None or case.merchant_id != merchant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recovery case not found.",
        )
    return case


@router.get(
    "/recovery-cases",
    response_model=list[RecoveryCaseResponse],
    summary="List all recovery cases with filtering and sorting",
)
def list_recovery_cases(
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
    *,
    status: str | None = Query(None, description="Filter by status (comma-separated or single)"),
    ai_decision: str | None = Query(None, description="Filter by AI decision"),
    payment_link_status: str | None = Query(None, description="Filter by link status: not_created, active, expired, paid"),
    customer_query: str | None = Query(None, description="Search customer name, email, or phone"),
    min_amount: Decimal | None = Query(None, description="Minimum amount at risk"),
    max_amount: Decimal | None = Query(None, description="Maximum amount at risk"),
    sort_by: str = Query("newest_failure", description="Sort by: newest_failure, highest_risk, oldest_unresolved, next_action"),
) -> list[RecoveryCaseResponse]:
    try:
        stmt = (
            select(RecoveryCase, Customer, Payment)
            .outerjoin(Customer, RecoveryCase.customer_id == Customer.id)
            .join(Payment, RecoveryCase.payment_id == Payment.id)
            .where(RecoveryCase.merchant_id == current_merchant.id)
        )

        if isinstance(status, str) and status.strip():
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            if statuses:
                stmt = stmt.where(RecoveryCase.status.in_(statuses))

        if isinstance(ai_decision, str) and ai_decision.strip():
            stmt = stmt.where(RecoveryCase.ai_decision == ai_decision.strip())

        if isinstance(min_amount, (int, float, Decimal)):
            stmt = stmt.where(RecoveryCase.amount_at_risk >= min_amount)

        if isinstance(max_amount, (int, float, Decimal)):
            stmt = stmt.where(RecoveryCase.amount_at_risk <= max_amount)

        if isinstance(customer_query, str) and customer_query.strip():
            q = f"%{customer_query.strip()}%"
            stmt = stmt.where(
                or_(
                    Customer.name.ilike(q),
                    Customer.email.ilike(q),
                    Customer.phone.ilike(q),
                )
            )

        now = func.now()
        if isinstance(payment_link_status, str) and payment_link_status.strip():
            pls = payment_link_status.strip().lower()
            if pls == "paid":
                stmt = stmt.where(RecoveryCase.status == "recovered")
            elif pls == "not_created":
                stmt = stmt.where(RecoveryCase.razorpay_payment_link_id.is_(None))
            elif pls == "expired":
                stmt = stmt.where(
                    RecoveryCase.status != "recovered",
                    RecoveryCase.razorpay_payment_link_id.isnot(None),
                    RecoveryCase.payment_link_expires_at < now,
                )
            elif pls == "active":
                stmt = stmt.where(
                    RecoveryCase.status != "recovered",
                    RecoveryCase.razorpay_payment_link_id.isnot(None),
                    or_(
                        RecoveryCase.payment_link_expires_at.is_(None),
                        RecoveryCase.payment_link_expires_at >= now,
                    ),
                )

        if isinstance(sort_by, str) and sort_by == "highest_risk":
            stmt = stmt.order_by(RecoveryCase.amount_at_risk.desc())
        elif isinstance(sort_by, str) and sort_by == "oldest_unresolved":
            stmt = stmt.order_by(RecoveryCase.created_at.asc())
        elif isinstance(sort_by, str) and sort_by == "next_action":
            stmt = stmt.order_by(RecoveryCase.next_action_at.asc().nulls_last())
        else:
            stmt = stmt.order_by(RecoveryCase.created_at.desc())

        rows = db.execute(stmt).all()
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to load recovery cases from the database.",
        ) from None

    return [
        RecoveryCaseResponse.from_models(case, customer, payment)
        for case, customer, payment in rows
    ]


@router.post(
    "/recovery-cases/{case_id}/analyze",
    response_model=RecoveryAnalysisResponse,
    summary="Analyze a recovery case with the AI decision engine",
)
def analyze_recovery_case(
    case_id: UUID,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> RecoveryAnalysisResponse:
    try:
        case = _get_owned_recovery_case(db, case_id, current_merchant.id)

        customer = db.get(Customer, case.customer_id) if case.customer_id is not None else None
        payment = db.get(Payment, case.payment_id)
        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Recovery case is missing its related payment data.",
            )

        context = build_recovery_context(
            db, case, customer, payment, settings.recovery_max_attempts, trigger="manual_api"
        )
    except HTTPException:
        raise
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to load recovery data from the database.",
        ) from None

    try:
        llm_decision = request_llm_decision(context, settings)
        llm_decision = ensure_action_consistent_next_step(llm_decision)
        decision, guardrails_applied = apply_guardrails(
            llm_decision, case, settings.recovery_max_attempts, context=context
        )
    except LLMConfigurationError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from None
    except LLMServiceError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The AI decision engine could not analyze this case.",
        ) from None
    except InvalidLLMOutputError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The AI decision engine returned an invalid decision.",
        ) from None

    try:
        analyzed_at = persist_analysis(
            db, case, decision, guardrails_applied, settings.ollama_model
        )
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to store the AI analysis.",
        ) from None

    return RecoveryAnalysisResponse(
        case_id=case.id,
        decision=decision,
        guardrails_applied=guardrails_applied,
        stored=True,
        analyzed_at=analyzed_at,
    )


@router.post(
    "/recovery-cases/{case_id}/execute",
    response_model=RecoveryActionExecutionResponse,
    summary="Execute a stored AI recovery recommendation",
)
def execute_recovery_case(
    case_id: UUID,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> RecoveryActionExecutionResponse:
    try:
        case = _get_owned_recovery_case(db, case_id, current_merchant.id)
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to load the recovery case for execution.",
        ) from None

    if case.ai_decision is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Analyze the recovery case before executing a recommendation.",
        )

    try:
        action_result = execute_recovery_action(
            case.ai_decision,
            case.id,
            RecoveryActionExecutionContext(
                db=db,
                max_recovery_attempts=getattr(current_merchant, "max_recovery_attempts", None) or settings.recovery_max_attempts,
                action_context=RecoveryActionContext(source="ai_recommendation"),
            ),
            wait_minutes=getattr(case, "ai_wait_minutes", None),
        )
    except InvalidRecoveryActionError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The stored AI recommendation is not a supported recovery action.",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to record the recovery action execution.",
        ) from None

    return RecoveryActionExecutionResponse(
        case_id=case.id,
        ai_recommendation=PersistedAIRecommendation(
            action=case.ai_decision,
            reason=case.ai_decision_note,
        ),
        action_execution_result=action_result,
    )

@router.post(
    "/recovery-cases/{case_id}/run",
    response_model=RecoveryWorkflowResponse,
    summary="Analyze and execute a recovery workflow",
)
def run_recovery_workflow(
    case_id: UUID,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> RecoveryWorkflowResponse:

    analysis = analyze_recovery_case(case_id, db, current_merchant)

    execution = execute_recovery_case(case_id, db, current_merchant)

    return RecoveryWorkflowResponse(
        analysis=analysis,
        execution=execution,
    )


@router.get(
    "/recovery-cases/{case_id}/activity",
    response_model=list[dict],
    summary="Get audit history and activity for a recovery case",
)
def get_recovery_case_activity(
    case_id: UUID,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> list[dict]:
    case = _get_owned_recovery_case(db, case_id, current_merchant.id)
    logs = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.entity_type == "recovery_case",
            AuditLog.entity_id == case.id,
        )
        .order_by(AuditLog.created_at.desc())
    ).all()
    return [
        {
            "id": str(log.id),
            "action": log.action,
            "actor": log.actor,
            "details": log.details,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]


@router.post(
    "/recovery-cases/{case_id}/actions/retry-link",
    response_model=RecoveryActionExecutionResponse,
    summary="Manually trigger creation/re-send of a recovery payment link to customer",
)
def manual_retry_payment_link(
    case_id: UUID,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> RecoveryActionExecutionResponse:
    case = _get_owned_recovery_case(db, case_id, current_merchant.id)
    try:
        action_result = execute_recovery_action(
            "retry",
            case.id,
            RecoveryActionExecutionContext(
                db=db,
                max_recovery_attempts=getattr(current_merchant, "max_recovery_attempts", None) or settings.recovery_max_attempts,
                action_context=RecoveryActionContext(source="manual"),
            ),
        )
    except InvalidRecoveryActionError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to record recovery link action.",
        ) from None

    return RecoveryActionExecutionResponse(
        case_id=case.id,
        ai_recommendation=PersistedAIRecommendation(
            action=case.ai_decision,
            reason=case.ai_decision_note,
        ),
        action_execution_result=action_result,
    )


@router.post(
    "/recovery-cases/{case_id}/actions/close",
    response_model=RecoveryActionExecutionResponse,
    summary="Manually close a recovery case",
)
def manual_close_recovery_case(
    case_id: UUID,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> RecoveryActionExecutionResponse:
    case = _get_owned_recovery_case(db, case_id, current_merchant.id)
    try:
        action_result = execute_recovery_action(
            "close",
            case.id,
            RecoveryActionExecutionContext(
                db=db,
                max_recovery_attempts=getattr(current_merchant, "max_recovery_attempts", None) or settings.recovery_max_attempts,
                action_context=RecoveryActionContext(source="manual"),
            ),
        )
    except InvalidRecoveryActionError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to record close action.",
        ) from None

    return RecoveryActionExecutionResponse(
        case_id=case.id,
        ai_recommendation=PersistedAIRecommendation(
            action=case.ai_decision,
            reason=case.ai_decision_note,
        ),
        action_execution_result=action_result,
    )
