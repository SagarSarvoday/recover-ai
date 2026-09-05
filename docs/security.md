# RecoverAI Security Architecture & Controls

This document details the implemented security model of the RecoverAI platform, explicitly delineating active, code-enforced defenses from future architectural recommendations.

---

## 1. Summary Matrix of Security Controls

| Security Domain | Control Name | Status | Enforcement Layer |
|---|---|---|---|
| **Authentication** | JWT Stateless Token Authentication | **Implemented** | FastAPI `Depends(get_current_merchant)` |
| **Multi-Tenancy** | Merchant-Scoped Isolation (Zero-IDOR) | **Implemented** | Database query filters + 404 absence masking |
| **Webhook Security** | HMAC-SHA256 Signature Verification | **Implemented** | Webhook route dependency (`x-razorpay-signature`) |
| **Webhook Security** | Event Idempotency & Replay Protection | **Implemented** | `razorpay_webhook_events` table unique constraint |
| **Password Security** | SHA-256 Reset Token Hashing | **Implemented** | `MerchantPasswordResetToken` table |
| **Password Security** | Anti-Enumeration Protections | **Implemented** | Generic responses on forgot-password |
| **Credential Hygiene** | Zero Secret Leakage in APIs & Logs | **Implemented** | Pydantic `SecretStr` + redacted audit logs |
| **State Integrity** | Explicit Recovery State Machine | **Implemented** | `app/services/recovery_state_machine.py` |
| **Guardrails** | Hard Maximum Recovery Attempts | **Implemented** | Server-side guardrail engine |
| **Auditability** | Comprehensive Append-Only Audit Logs | **Implemented** | `AuditLog` model on all recovery operations |

---

## 2. Implemented Security Defenses

### A. JWT Authentication
- **Mechanism**: JSON Web Tokens signed using HMAC-SHA256 (`HS256`).
- **Subject**: Encodes `merchant.id` (UUID) with expiration (`exp`) and issuance (`iat`) claims.
- **Enforcement**: Protected API endpoints consume `get_current_merchant` dependency. Missing, expired, or tampered tokens return `401 Unauthorized`.
- **Credential Storage**: Merchant passwords are encrypted using `bcrypt` via Passlib; plaintext passwords are never logged or stored.

### B. Multi-Tenant Merchant Isolation & Zero-IDOR Protection
- **Tenant Scope**: Every merchant operates in complete data isolation. Every payment, customer, recovery case, scheduled action, and metric is foreign-keyed to `merchant_id`.
- **Zero-IDOR Enforcement**: All retrieval, update, and action endpoints query:
  ```python
  select(Model).where(Model.id == entity_id, Model.merchant_id == current_merchant.id)
  ```
- **Absence Masking**: Attempting to query an existing ID belonging to another merchant returns HTTP `404 Not Found` (rather than `403 Forbidden`) to eliminate entity enumeration risks.
- **Customer Uniqueness**: Enforced per-tenant via `UniqueConstraint("merchant_id", "email")`.

### C. Razorpay Webhook HMAC-SHA256 Verification
- **Header Check**: Webhooks require both `x-razorpay-signature` and `x-razorpay-event-id`.
- **Cryptographic Verification**: The raw binary request payload is verified against the configured `RAZORPAY_WEBHOOK_SECRET` using HMAC-SHA256 before any JSON deserialization:
  ```python
  expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
  is_valid = hmac.compare_digest(expected, signature)
  ```
- **Tamper Protection**: Tampered or unauthenticated webhook payloads are rejected immediately with `401 Unauthorized`.

### D. Webhook Idempotency & Replay Protection
- **Idempotency Key**: Each webhook's `x-razorpay-event-id` is stored in `razorpay_webhook_events` with a `UNIQUE` database constraint.
- **Duplicate Prevention**: If Razorpay retries a previously processed event (e.g. `payment_link.paid`), the second request is recognized as a duplicate and returns status `duplicate` without double-crediting recovery amounts.

### E. Password Reset Token Security & Anti-Enumeration
- **Cryptographic Entropy**: Generated via `secrets.token_urlsafe(32)` providing 256 bits of entropy.
- **Zero Raw Token Storage**: The raw token is sent only in the email reset link (`?token=...`). The database stores only the SHA-256 hex digest (`token_hash`), preventing token compromise even in the event of database exfiltration.
- **Single-Use & Invalidation**: Using a reset token stamps `used_at = NOW()`. Any other pending tokens for that merchant are simultaneously invalidated. Replay attempts return `400 Bad Request`.
- **Anti-Enumeration Guarantee**: `POST /api/v1/auth/forgot-password` always returns the identical generic confirmation message whether the email address exists in the database or not.

### F. Secret Non-Leakage & Log Sanitization
- **Configuration Secrets**: Stored in Pydantic `SecretStr` wrappers (`razorpay_key_secret`, `razorpay_webhook_secret`, `smtp_password`, `jwt_secret_key`).
- **Log Masking**: Custom sanitization removes credentials, tokens, and authorization headers from application error logs.
- **Client Bundles**: The frontend Next.js application exposes only public keys (`NEXT_PUBLIC_RAZORPAY_KEY_ID`); private keys and webhook secrets are strictly isolated to backend server environments.

### G. Recovery State Machine Guardrails & The Recovery Invariant
- **Strict Invariant**: Neither AI reasoning, nor merchant manual action, nor payment link generation, nor email delivery can mark a payment as recovered.
- **Settlement Authority**: The status `recovered` can only be set by the internal webhook handler (`ingest_payment_link_paid_webhook`) upon cryptographic verification of Razorpay's `payment_link.paid` event.
- **Terminal Immutability**: States `recovered` and `closed` have zero outward transitions. Any attempt to modify a terminal case raises `InvalidStateTransitionError`.
- **Attempt Limits**: Server-side guardrails enforce `merchant.max_recovery_attempts` (1 to 5, default 3). Once reached, cases are closed autonomously.

### H. Append-Only Audit Logging
- Every critical event (`payment_failed_ingested`, `ai_analysis_completed`, `payment_link_created`, `payment_link_reused`, `notification_delivered`, `notification_failed`, `payment_link_paid_ingested`, `password_reset_requested`, `password_reset_completed`) is persisted to `audit_logs` with timestamps, actor information, and structured JSON context.

---

## 3. Production Security Recommendations (Future Roadmap)

While the core security posture is production-hardened, enterprise deployments should consider:
1. **Rate Limiting**: Add Redis-backed token-bucket rate limiting on `/api/v1/auth/*` and public webhook endpoints.
2. **Key Rotation**: Implement automated rotation schedules for `JWT_SECRET_KEY` and Razorpay webhook secrets.
3. **Database Network Isolation**: Place the PostgreSQL database in a private VPC subnet with access restricted to the backend application container security groups.
4. **WAF & DDoS Mitigation**: Deploy Cloudflare or AWS WAF in front of both API and Webhook endpoints.
