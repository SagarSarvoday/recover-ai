# RecoverAI Architecture Deep Dive

This document provides a comprehensive technical breakdown of the architecture, subsystems, state machines, and data models powering RecoverAI.

---

## 1. High-Level System Architecture

RecoverAI is structured as an autonomous, event-driven recovery engine composed of a **FastAPI backend**, an **autonomous background worker system**, an **AI decision engine**, and a **Next.js 15 merchant control center**.

```mermaid
flowchart TD
    subgraph External Systems
        RZP[Razorpay Payment Gateway]
        CUST[End Customer]
        SMTP[SMTP Mail Server]
        LLM[Ollama / Local LLM Engine]
    end

    subgraph RecoverAI Platform
        subgraph Ingestion & Security
            WH[Webhook Ingestion\nPOST /api/v1/webhooks/razorpay]
            HMAC[HMAC-SHA256 Signature Verification]
            IDEM[Webhook Idempotency Layer]
        end

        subgraph Core Engine
            RC[Recovery Case Manager]
            SM[Recovery State Machine]
            AIE[AI Decision Engine & Guardrails]
            RA[Recovery Action Executor]
        end

        subgraph Background Workers
            CAW[Continuous Agent Worker\nDaemon Thread (10s)]
            SRAW[Scheduled Recovery Worker\nDaemon Thread (15s)]
        end

        subgraph Database & Persistence
            DB[(PostgreSQL / Supabase)]
        end

        subgraph Merchant Experience
            API[FastAPI REST API v1]
            FE[Next.js 15 Merchant Dashboard]
        end
    end

    RZP -->|payment.failed webhook| WH
    WH --> HMAC --> IDEM --> RC
    RC --> DB
    CAW -->|Poll unhandled open cases| RC
    SRAW -->|Poll due wait jobs| DB
    RC --> AIE
    AIE <-->|Prompt / JSON Decision| LLM
    AIE -->|Guarded Decision| RA
    RA -->|Create Payment Link| RZP
    RA -->|Send Recovery Email| SMTP
    SMTP -->|Email with Recovery Link| CUST
    CUST -->|Click Link & Pay| RZP
    RZP -->|payment_link.paid webhook| WH
    WH -->|Settle & Recover| SM
    FE <-->|JWT Authenticated REST| API
    API <--> DB
```

---

## 2. End-to-End Recovery Lifecycle

The complete recovery journey guarantees autonomous execution while strictly enforcing that **only Razorpay payment webhooks can mark a case recovered**.

```mermaid
sequenceDiagram
    autonumber
    participant RZP as Razorpay
    participant WH as Webhook Ingestion
    participant DB as PostgreSQL
    participant AI as AI Engine & Guardrails
    participant Worker as Background Workers
    participant Email as Notification Service
    participant Cust as Customer

    Note over RZP,WH: 1. Payment Failure Ingestion
    RZP->>WH: POST /api/v1/webhooks/razorpay (payment.failed)
    WH->>WH: Verify HMAC-SHA256 Signature & Check Event Idempotency
    WH->>DB: Persist Payment (status=failed), Resolve Customer, Create RecoveryCase (status=open)
    
    Note over Worker,AI: 2. Autonomous Analysis
    Worker->>DB: Atomic lease claim on open / in_progress cases
    Worker->>AI: Build RecoveryDecisionContext (history, bank error code, merchant settings)
    AI->>AI: Query LLM + Apply Server Guardrails (attempt limits, delay bounds)
    
    alt Decision: WAIT
        AI->>DB: Schedule durable ScheduledRecoveryAction (status=waiting)
        Note over Worker: Wait window elapses (15m - 24h)
        Worker->>DB: Claim due job, transition case to in_progress (wait_elapsed=True)
        Worker->>AI: Reassess case with full history
    else Decision: RETRY / CONTACT
        AI->>RZP: Request Razorpay Payment Link (with merchant expiry)
        RZP-->>AI: Payment Link Created (plink_id, short_url)
        AI->>DB: Store Link, Increment attempt_count, Status = payment_link_active
        AI->>Email: Build branded email (store name, failure reason, link)
        Email->>Cust: Deliver Recovery Email via SMTP
    else Decision: CLOSE / SKIP
        AI->>DB: Transition status to closed (terminal)
    end

    Note over Cust,RZP: 3. Customer Settlement
    Cust->>RZP: Customer opens payment link and pays successfully
    RZP->>WH: POST /api/v1/webhooks/razorpay (payment_link.paid)
    WH->>WH: Verify HMAC-SHA256 Signature
    WH->>DB: Lock case row, update amount_recovered, transition status to 'recovered'
    WH-->>RZP: HTTP 200 OK (Processed)
```

---

## 3. The Strict Recovery Invariant

> [!IMPORTANT]
> **Core Architectural Invariant**:
> - Creating a Razorpay payment link does **NOT** recover a payment.
> - Reusing an existing payment link does **NOT** recover a payment.
> - Sending a recovery email does **NOT** recover a payment.
> - Scheduling or executing a retry does **NOT** recover a payment.
> - An AI decision does **NOT** recover a payment.
> - A manual merchant dashboard action does **NOT** recover a payment.
> 
> A recovery case transitions to `recovered` **strictly and exclusively** upon receipt and cryptographic verification of a Razorpay `payment_link.paid` webhook.

---

## 4. State Machine Architecture

The lifecycle of a recovery case is governed by `app/services/recovery_state_machine.py`. Any invalid transition raises an `InvalidStateTransitionError`.

```mermaid
stateDiagram-v2
    [*] --> open: payment.failed webhook

    open --> in_progress: AI / Worker claims case
    open --> waiting: Direct wait schedule
    open --> payment_link_active: Immediate link creation
    open --> closed: Attempt limit / non-recoverable

    in_progress --> waiting: AI decides WAIT
    in_progress --> payment_link_active: AI creates / sends link
    in_progress --> closed: Max attempts exceeded / manual close
    in_progress --> in_progress: Re-assessment cycle

    waiting --> in_progress: Scheduled timer elapses
    waiting --> closed: Manual cancellation

    payment_link_active --> in_progress: Link expired / re-assessment
    payment_link_active --> payment_link_active: Notification retry (attempt count unchanged)
    payment_link_active --> recovered: Verified payment_link.paid webhook
    payment_link_active --> closed: Link expired & max attempts reached

    recovered --> [*]: TERMINAL (Immutable)
    closed --> [*]: TERMINAL (Immutable)
```

### State Machine Transition Table
| Current State | Allowed Next States | Trigger |
|---|---|---|
| `open` | `in_progress`, `waiting`, `payment_link_active`, `closed` | Background claim, scheduled wait, direct link, or guardrail close |
| `in_progress` | `in_progress`, `waiting`, `payment_link_active`, `closed` | Reassessment, wait schedule, link generation, or attempt limit |
| `waiting` | `in_progress`, `closed` | Scheduled action timer elapsed, or merchant manual closure |
| `payment_link_active` | `in_progress`, `payment_link_active`, `recovered`, `closed` | Reassessment, email re-send, customer payment settled, or expiry closure |
| `recovered` | *None* | **Terminal state**. Locked permanently. |
| `closed` | *None* | **Terminal state**. Locked permanently. |

---

## 5. AI Decision Engine & Guardrails

The AI decision engine (`app/services/recovery_decision.py`) evaluates payment failures dynamically while keeping execution strictly bound by deterministic code.

```mermaid
flowchart TD
    IN[Recovery Decision Input] --> CTX[Build Context:\n- Case data & attempts\n- Customer payment history\n- Actual bank failure reason\n- Merchant configuration limits]
    CTX --> LLM[Ollama / Local LLM Inference]
    LLM --> PARSE{Parse Structured JSON Decision}
    PARSE -->|Valid| GR[Server Guardrail Engine]
    PARSE -->|Invalid / Error| FALLBACK[Deterministic Rule-Based Fallback]
    FALLBACK --> GR

    subgraph Server-Side Guardrails
        GR --> G1{Attempt >= Max Configured?}
        G1 -->|Yes| FORCE_CLOSE[Force Action = CLOSE]
        G1 -->|No| G2{Case Already Resolved?}
        G2 -->|Yes| FORCE_CLOSE
        G2 -->|No| G3{Action == WAIT?}
        G3 -->|Yes| CHECK_TIME[Validate delay window\n15m <= delay <= 1440m]
        G3 -->|No| VALID[Pass Guardrails]
    end

    FORCE_CLOSE --> OUT[Persist Decision in Case]
    CHECK_TIME --> OUT
    VALID --> OUT
```

### Guardrail Guarantees
1. **Hard Maximum Attempts**: If `attempt_count >= merchant.max_recovery_attempts` (default 3), the action is strictly forced to `close`.
2. **Timing Validation**: All `wait` recommendations must have an explicit, bounded delay (`wait_minutes` between 15 and 1440). If out of range, the guardrail clamps or provides a safe default.
3. **No Infinite Loops**: When an action resumes after a wait period (`wait_elapsed=True`), the engine prevents scheduling back-to-back waits without forward progress.

---

## 6. Background Worker Subsystems

RecoverAI runs two background worker threads embedded within the FastAPI application lifespan:

### A. Continuous Recovery Agent Worker (`app/services/recovery_agent_worker.py`)
- **Poll Interval**: 10 seconds (configurable via `RECOVERY_AGENT_POLL_SECONDS`).
- **Function**: Queries cases in `open` or unhandled states belonging to merchants with `ai_agent_enabled=True`.
- **Atomic Lease Claiming**: Claims cases using an in-memory lease mechanism to prevent concurrent workers from processing the same case twice.
- **Cooldown & Safety**: Cases undergoing active payment link waiting or recent action are skipped to respect customer patience.

### B. Scheduled Recovery Worker (`app/services/scheduled_recovery_actions.py`)
- **Poll Interval**: 15 seconds (configurable via `RECOVERY_SCHEDULER_POLL_SECONDS`).
- **Function**: Scans `scheduled_recovery_actions` where `status = 'pending'` and `scheduled_for <= NOW()`.
- **Atomic Claiming**: Uses atomic SQL updates (`UPDATE ... WHERE status = 'pending' RETURNING id`) to claim jobs safely across multiple instances.
- **Continuation**: Transitions the case back to `in_progress` and invokes the AI decision engine with `wait_elapsed=True` for subsequent action.

---

## 7. Database Entity Relationship Model

```mermaid
erDiagram
    MERCHANTS ||--o{ CUSTOMERS : owns
    MERCHANTS ||--o{ PAYMENTS : owns
    MERCHANTS ||--o{ RECOVERY_CASES : owns
    MERCHANTS ||--o{ TRANSACTIONS : owns
    MERCHANTS ||--o{ SCHEDULED_RECOVERY_ACTIONS : owns
    MERCHANTS ||--o{ MERCHANT_PASSWORD_RESET_TOKENS : has

    CUSTOMERS ||--o{ PAYMENTS : makes
    CUSTOMERS ||--o{ RECOVERY_CASES : targeted_by
    CUSTOMERS ||--o{ TRANSACTIONS : participates_in

    PAYMENTS ||--o| RECOVERY_CASES : triggers
    PAYMENTS ||--o{ TRANSACTIONS : associates

    RECOVERY_CASES ||--o{ SCHEDULED_RECOVERY_ACTIONS : schedules
    RECOVERY_CASES ||--o{ AUDIT_LOGS : records

    MERCHANTS {
        uuid id PK
        text name
        text email UK
        text password_hash
        text razorpay_account_id UK
        text business_name
        text support_email
        text support_phone
        int max_recovery_attempts
        int default_payment_link_expiry_hours
        int default_wait_minutes
        boolean auto_notify_customer
        boolean ai_agent_enabled
        timestamp created_at
        timestamp updated_at
    }

    CUSTOMERS {
        uuid id PK
        uuid merchant_id FK
        text name
        text email
        text phone
        timestamp created_at
        timestamp updated_at
    }

    PAYMENTS {
        uuid id PK
        uuid merchant_id FK
        uuid customer_id FK
        numeric amount
        text currency
        text status
        text razorpay_payment_id
        text razorpay_order_id
        text failure_reason
        text failure_code
        timestamp created_at
        timestamp updated_at
    }

    RECOVERY_CASES {
        uuid id PK
        uuid merchant_id FK
        uuid customer_id FK
        uuid payment_id FK
        text status
        numeric amount_at_risk
        numeric amount_recovered
        int attempt_count
        text ai_decision
        numeric ai_confidence
        text razorpay_payment_link_id
        text razorpay_payment_link_url
        timestamp payment_link_expires_at
        timestamp payment_link_sent_at
        timestamp created_at
        timestamp updated_at
    }

    MERCHANT_PASSWORD_RESET_TOKENS {
        uuid id PK
        uuid merchant_id FK
        text token_hash UK
        timestamp expires_at
        timestamp used_at
        timestamp created_at
    }

    SCHEDULED_RECOVERY_ACTIONS {
        uuid id PK
        uuid recovery_case_id FK
        uuid merchant_id FK
        text action_type
        timestamp scheduled_for
        text status
        timestamp executed_at
        timestamp lease_expires_at
        timestamp created_at
    }

    AUDIT_LOGS {
        uuid id PK
        text entity_type
        uuid entity_id
        text action
        text actor
        jsonb details
        timestamp created_at
    }

    RAZORPAY_WEBHOOK_EVENTS {
        uuid id PK
        text razorpay_event_id UK
        text event_type
        text external_entity_id
        text processing_status
        timestamp created_at
    }
```

---

## 8. Multi-Tenant Merchant Isolation (Zero-IDOR)

Every request reaching the RecoverAI API is subject to strict merchant tenancy boundary checks:
1. **JWT Verification**: The `Authorization: Bearer <token>` header is decoded and validated; the merchant record is extracted as the request context (`current_merchant`).
2. **Explicit Foreign Key Enforcement**: Queries for customers, recovery cases, payments, and settings filter strictly on `merchant_id == current_merchant.id`.
3. **Absence Over Error**: If Merchant A attempts to query an ID belonging to Merchant B, the API returns a generic `404 Not Found` (never a `403 Forbidden` that leaks entity existence).
4. **Unique Scopes**: Customer uniqueness is enforced per-merchant `(merchant_id, email)`, allowing different merchants to serve the same consumer independently.
