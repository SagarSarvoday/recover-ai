from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import settings
from app.models.customer import Customer
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


@router.get(
    "/recovery-cases",
    response_model=list[RecoveryCaseResponse],
    summary="List all recovery cases",
)
def list_recovery_cases(db: Session = Depends(get_db)) -> list[RecoveryCaseResponse]:
    try:
        rows = db.execute(
            select(RecoveryCase, Customer, Payment)
            .join(Customer, RecoveryCase.customer_id == Customer.id)
            .join(Payment, RecoveryCase.payment_id == Payment.id)
            .order_by(RecoveryCase.created_at.desc())
        ).all()
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
def analyze_recovery_case(case_id: UUID, db: Session = Depends(get_db)) -> RecoveryAnalysisResponse:
    try:
        case = db.get(RecoveryCase, case_id)
        if case is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Recovery case not found.",
            )

        customer = db.get(Customer, case.customer_id)
        payment = db.get(Payment, case.payment_id)
        if customer is None or payment is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Recovery case is missing related customer or payment data.",
            )

        context = build_recovery_context(
            db, case, customer, payment, settings.recovery_max_attempts
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
            llm_decision, case, settings.recovery_max_attempts
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
) -> RecoveryActionExecutionResponse:
    try:
        case = db.get(RecoveryCase, case_id)
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to load the recovery case for execution.",
        ) from None

    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recovery case not found.",
        )
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
                max_recovery_attempts=settings.recovery_max_attempts,
                action_context=RecoveryActionContext(source="ai_recommendation"),
            ),
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
) -> RecoveryWorkflowResponse:

    analysis = analyze_recovery_case(case_id, db)

    execution = execute_recovery_case(case_id, db)

    return RecoveryWorkflowResponse(
        analysis=analysis,
        execution=execution,
    )