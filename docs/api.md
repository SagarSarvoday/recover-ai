# RecoverAI REST API Reference

The RecoverAI API is a RESTful service served over HTTP/JSON with standard HTTP status codes, Bearer JWT authentication, and strict multi-tenant merchant isolation.

---

## Global Conventions

- **Base URL**: `http://localhost:8000` (development) or `https://api.yourdomain.com` (production)
- **Authentication**: Bearer Token in `Authorization: Bearer <access_token>` header
- **Content-Type**: `application/json`
- **Multi-Tenancy**: All authenticated endpoints automatically isolate records by the caller's `merchant_id`. Cross-tenant queries return `404 Not Found`.

---

## 1. Authentication Endpoints

### Register Merchant
- **Method / Path**: `POST /api/v1/auth/register`
- **Auth**: None (Public)
- **Purpose**: Create a new merchant account.
- **Request Body**:
  ```json
  {
    "name": "Apex Store",
    "email": "merchant@apex.com",
    "password": "secure-password-min-8-chars"
  }
  ```
- **Response**: `200 OK`
  ```json
  {
    "id": "7488f58b-0b5c-411f-827a-8b88a913bc54",
    "name": "Apex Store",
    "email": "merchant@apex.com",
    "razorpay_account_id": null,
    "created_at": "2026-09-05T08:00:00Z"
  }
  ```

### Log In Merchant
- **Method / Path**: `POST /api/v1/auth/login`
- **Auth**: None (Public)
- **Purpose**: Authenticate merchant credentials and obtain a JWT access token.
- **Request Body**:
  ```json
  {
    "email": "merchant@apex.com",
    "password": "secure-password-min-8-chars"
  }
  ```
- **Response**: `200 OK`
  ```json
  {
    "access_token": "eyJhbGciOiJIUzI1NiIsIn...",
    "token_type": "bearer"
  }
  ```

### Request Password Reset
- **Method / Path**: `POST /api/v1/auth/forgot-password`
- **Auth**: None (Public)
- **Purpose**: Initiate a single-use, time-limited password reset flow.
- **Behavior**: Anti-enumeration guaranteed — returns identical HTTP 200 message whether the email exists or not.
- **Request Body**:
  ```json
  {
    "email": "merchant@apex.com"
  }
  ```
- **Response**: `200 OK`
  ```json
  {
    "message": "If an account exists for this email, you will receive a password reset link shortly."
  }
  ```

### Reset Password
- **Method / Path**: `POST /api/v1/auth/reset-password`
- **Auth**: None (Public)
- **Purpose**: Set a new password using a valid, unexpired reset token.
- **Request Body**:
  ```json
  {
    "token": "urlsafe_raw_token_from_email",
    "new_password": "new-secure-password"
  }
  ```
- **Response**: `200 OK`
  ```json
  {
    "message": "Password has been successfully reset. You can now log in with your new password."
  }
  ```

---

## 2. Merchant Settings & Observability

### Get Current Merchant Profile
- **Method / Path**: `GET /api/v1/merchant/me`
- **Auth**: Bearer JWT
- **Purpose**: Returns the authenticated merchant record.

### Connect Razorpay Account
- **Method / Path**: `PUT /api/v1/merchant/razorpay-account`
- **Auth**: Bearer JWT
- **Purpose**: Link a Razorpay Merchant Account ID to the tenant.
- **Request Body**:
  ```json
  {
    "razorpay_account_id": "acc_apex_12345"
  }
  ```

### Get Autonomous Agent Status
- **Method / Path**: `GET /api/v1/merchant/agent`
- **Auth**: Bearer JWT
- **Purpose**: Inspect whether the merchant's autonomous recovery agent worker is enabled.
- **Response**: `200 OK` `{"enabled": true}`

### Toggle Autonomous Agent
- **Method / Path**: `POST /api/v1/merchant/agent/toggle`
- **Auth**: Bearer JWT
- **Purpose**: Enable or disable autonomous recovery cycles for this merchant.
- **Request Body**: `{"enabled": true}`

### Get Merchant Metrics
- **Method / Path**: `GET /api/v1/merchant/metrics`
- **Auth**: Bearer JWT
- **Purpose**: Retrieve aggregate recovery KPIs scoped strictly to this merchant.
- **Response**: `200 OK`
  ```json
  {
    "total_at_risk": "145000.00",
    "total_recovered": "98500.00",
    "recovery_rate_pct": "67.93",
    "active_cases_count": 12,
    "recovered_cases_count": 28,
    "closed_cases_count": 4
  }
  ```

### Get Detailed Merchant Settings
- **Method / Path**: `GET /api/v1/merchant/settings`
- **Auth**: Bearer JWT
- **Purpose**: Returns complete merchant profile, webhook URL, integration status checklist, and recovery policies without leaking secrets.

### Update Merchant Settings
- **Method / Path**: `PUT /api/v1/merchant/settings`
- **Auth**: Bearer JWT
- **Purpose**: Update store profile, support info, and autonomous recovery rules.
- **Request Body**:
  ```json
  {
    "business_name": "Apex Gear Store",
    "support_email": "support@apexgear.com",
    "support_phone": "+919876543210",
    "max_recovery_attempts": 3,
    "default_payment_link_expiry_hours": 48,
    "default_wait_minutes": 60,
    "auto_notify_customer": true
  }
  ```

---

## 3. Customer Management

### List Customers
- **Method / Path**: `GET /api/v1/customers`
- **Auth**: Bearer JWT
- **Query Params**: `page` (default 1), `limit` (default 20, max 100), `search` (optional string)
- **Purpose**: Paginated list of customers belonging to the authenticated merchant.

### Create Customer
- **Method / Path**: `POST /api/v1/customers`
- **Auth**: Bearer JWT
- **Request Body**:
  ```json
  {
    "name": "Vikram Patel",
    "email": "vikram@example.com",
    "phone": "+919876543210"
  }
  ```

### Get Customer Detail
- **Method / Path**: `GET /api/v1/customers/{customer_id}`
- **Auth**: Bearer JWT
- **Purpose**: Returns customer profile, payment history, and associated recovery cases.

### Update Customer
- **Method / Path**: `PUT /api/v1/customers/{customer_id}`
- **Auth**: Bearer JWT
- **Request Body**: `{"name": "...", "phone": "..."}`

### Delete Customer
- **Method / Path**: `DELETE /api/v1/customers/{customer_id}`
- **Auth**: Bearer JWT
- **Purpose**: Deletes a customer record (only allowed if no active recovery cases are associated).

---

## 4. Recovery Cases

### List Recovery Cases
- **Method / Path**: `GET /api/v1/recovery-cases`
- **Auth**: Bearer JWT
- **Query Params**: `status` (filter by `open`, `in_progress`, `waiting`, `payment_link_active`, `recovered`, `closed`)
- **Purpose**: List recovery cases scoped to the merchant, ordered by recency.

### Get Recovery Case Detail
- **Method / Path**: `GET /api/v1/recovery-cases/{case_id}`
- **Auth**: Bearer JWT
- **Purpose**: Returns detailed case state, payment link URLs, expiry timestamps, AI recommendations, and confidence score.

### Get Case Activity Timeline
- **Method / Path**: `GET /api/v1/recovery-cases/{case_id}/activity`
- **Auth**: Bearer JWT
- **Purpose**: Returns chronological audit history for the case drawer timeline.

---

## 5. Recovery Actions & Manual Controls

### Analyze Recovery Case (AI Inference)
- **Method / Path**: `POST /api/v1/recovery-cases/{case_id}/analyze`
- **Auth**: Bearer JWT
- **Purpose**: Run AI decision engine on the case without executing actions.
- **Response**: `200 OK`
  ```json
  {
    "decision": {
      "action": "contact",
      "reason": "Temporary issuing bank timeout. Customer has high historical success.",
      "confidence": 0.94,
      "recommended_channel": "email"
    }
  }
  ```

### Execute Prior Decision
- **Method / Path**: `POST /api/v1/recovery-cases/{case_id}/execute`
- **Auth**: Bearer JWT
- **Purpose**: Executes the previously persisted AI decision.

### Run (Analyze + Execute in One Call)
- **Method / Path**: `POST /api/v1/recovery-cases/{case_id}/run`
- **Auth**: Bearer JWT
- **Purpose**: Evaluates AI decision with server guardrails and immediately executes the resulting action.

### Manual Retry Payment Link
- **Method / Path**: `POST /api/v1/recovery-cases/{case_id}/actions/retry-link`
- **Auth**: Bearer JWT
- **Purpose**: Generates (or reuses) a Razorpay payment link, sends customer notification, and sets state to `payment_link_active`.
- **Note**: Does NOT mark case recovered.

### Manual Close Case
- **Method / Path**: `POST /api/v1/recovery-cases/{case_id}/actions/close`
- **Auth**: Bearer JWT
- **Purpose**: Explicitly closes a case and cancels pending scheduled timers.

---

## 6. Razorpay Webhooks

### Receive Razorpay Webhook
- **Method / Path**: `POST /api/v1/webhooks/razorpay`
- **Auth**: `x-razorpay-signature` and `x-razorpay-event-id` headers required
- **Purpose**: Ingests Razorpay webhook events.
- **Supported Events**:
  - `payment.failed`: Records payment failure, resolves customer, and creates `RecoveryCase`.
  - `payment_link.paid`: Cryptographically verifies payment, settles recovery case to `recovered`, updates recovered amount, and records transaction.
- **Response**: `200 OK`
  ```json
  {
    "event_id": "evt_123456789",
    "event_type": "payment_link.paid",
    "status": "processed"
  }
  ```
