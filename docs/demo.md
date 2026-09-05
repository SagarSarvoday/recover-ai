# Razorpay Buildathon Demo Guide (5–6 Minutes)

This walkthrough provides an exact script, sequence of screens, and talking points to demonstrate the **RecoverAI Autonomous Revenue Recovery Agent** to judges and evaluators.

---

## Demo Overview & Value Proposition
- **Title**: RecoverAI — Autonomous AI Revenue Recovery Agent for Razorpay Merchants
- **Core Message**: Failed payments cost e-commerce businesses up to 15% of checkout revenue. Instead of static, dumb retry emails, RecoverAI uses an autonomous AI agent to analyze failure reasons, enforce merchant guardrails, generate Razorpay recovery links, and autonomously recover revenue—settled exclusively by verified Razorpay webhooks.

---

## Timeline & Scene-by-Scene Script

```
0:00 - 0:45 | Scene 1: The Problem & Merchant Command Center
0:45 - 1:45 | Scene 2: The Failure Event & Autonomous Ingestion
1:45 - 2:45 | Scene 3: The Autonomous AI Agent in Action (Reasoning & Guardrails)
2:45 - 3:45 | Scene 4: Customer Experience (Branded Email & Razorpay Checkout)
3:45 - 4:45 | Scene 5: Verified Webhook Settlement (Strict Invariant Proof)
4:45 - 5:30 | Scene 6: Architecture, Security & Production Hardening Wrap-Up
```

---

### Scene 1: The Problem & Merchant Command Center (0:00 – 0:45)
- **Screen to Show**: Merchant Dashboard (`http://localhost:3000/dashboard`).
- **Visuals**: Metrics bar showing **Recovery Rate (%)**, **Total Recovered (₹)**, **Active Cases**, and the live **Recovery Case Feed**.
- **Talking Points**:
  > *"When an online payment fails on Razorpay, merchants typically send one generic email or do nothing. RecoverAI changes that. This is the RecoverAI Command Center. Every merchant connects their Razorpay account and configures autonomous recovery policies: maximum attempts, wait intervals, and branded customer notifications."*
- **Action**: Briefly show the **Autonomous Agent toggle** set to **Active** and open the **Settings** tab (`/settings`) to show the integrated Razorpay webhook configuration.

---

### Scene 2: The Failure Event & Autonomous Ingestion (0:45 – 1:45)
- **Screen to Show**: Split screen with Terminal / Webhook Simulator and RecoverAI Dashboard.
- **Action**: Simulate an actual failed payment by sending a `payment.failed` webhook payload to the backend:
  ```bash
  # Simulated payment.failed webhook
  curl -X POST http://localhost:8000/api/v1/webhooks/razorpay \
    -H "Content-Type: application/json" \
    -H "x-razorpay-event-id: evt_demo_failed_101" \
    -H "x-razorpay-signature: <computed-hmac-sha256>" \
    -d '{
      "entity": "event",
      "account_id": "acc_apex_demo",
      "event": "payment.failed",
      "payload": {
        "payment": {
          "entity": {
            "id": "pay_failed_demo_101",
            "amount": 349900,
            "currency": "INR",
            "status": "failed",
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Payment was declined by the issuing bank due to temporary network timeout.",
            "error_source": "bank",
            "error_step": "payment_authorization",
            "error_reason": "payment_failed",
            "email": "customer@example.com",
            "contact": "+919876543210"
          }
        }
      }
    }'
  ```
- **Visuals**: A new case appears on the dashboard with status `open` and amount ₹3,499.00 at risk.
- **Talking Points**:
  > *"The payment failed at the issuing bank. Razorpay instantly fired the payment.failed webhook. RecoverAI verified the HMAC-SHA256 signature, enforced idempotency, mapped the event to this merchant, created customer records, and opened a Recovery Case."*

---

### Scene 3: The Autonomous AI Agent in Action (1:45 – 2:45)
- **Screen to Show**: Click the recovery case to open the **Case Details Drawer**.
- **Visuals**:
  - Show the **AI Decision Banner**: Decision = `contact` or `retry`, Confidence = `94%`.
  - Show the **AI Reasoning**: Explains that the bank error was a temporary authorization timeout; customer has positive transaction history; recommended action is to send a fresh payment link with 48h expiry.
  - Show the **Chronological Activity Timeline**:
    1. 🔵 Case Opened from `payment.failed` webhook.
    2. 🟠 AI Analysis completed with confidence score.
    3. 🔵 Razorpay Recovery Payment Link generated (`https://rzp.io/i/...`).
    4. 🟢 Branded Customer Notification delivered via SMTP.
- **Talking Points**:
  > *"Notice what happened without any human intervention: our autonomous background worker claimed the case, compiled full contextual history including previous payments and bank failure diagnostics, and queried the AI decision engine. The AI evaluated the risk and recommended reaching out with a recovery link. But notice our server guardrail: the AI's suggestion was checked against merchant attempt limits and timing bounds before execution."*

---

### Scene 4: Customer Experience (2:45 – 3:45)
- **Screen to Show**: Customer Email Inbox / Mail Viewer and Razorpay Checkout Page.
- **Visuals**:
  - Show the delivered HTML email: Branded with the merchant's store name, support email, clear statement of why the original payment failed (*"Temporary bank authorization timeout"*), and a clear **"Complete Your Payment"** button.
  - Click the button to load the live Razorpay Payment Link page.
- **Talking Points**:
  > *"The customer doesn't receive a cryptic error. They receive a polished, branded email explaining exactly why the card failed, with an active Razorpay payment link. Clicking the link takes them directly to an authorized Razorpay checkout."*

---

### Scene 5: Verified Webhook Settlement — The Strict Invariant (3:45 – 4:45)
- **Screen to Show**: Case Details Drawer on the Dashboard.
- **Critical Highlighting Point**:
  > *"Look at the case status right now: it is `payment_link_active`. It is NOT recovered. In RecoverAI, neither the AI, nor the merchant, nor sending the email can mark a payment recovered. The case MUST wait for Razorpay."*
- **Action**: Simulate the customer completing the payment by triggering the `payment_link.paid` webhook:
  ```bash
  curl -X POST http://localhost:8000/api/v1/webhooks/razorpay \
    -H "Content-Type: application/json" \
    -H "x-razorpay-event-id: evt_demo_paid_202" \
    -H "x-razorpay-signature: <computed-hmac-sha256>" \
    -d '{
      "entity": "event",
      "account_id": "acc_apex_demo",
      "event": "payment_link.paid",
      "payload": {
        "payment_link": {
          "entity": {
            "id": "plink_demo_101",
            "amount_paid": 349900,
            "status": "paid"
          }
        },
        "payment": {
          "entity": {
            "id": "pay_success_demo_202",
            "amount": 349900,
            "status": "captured"
          }
        }
      }
    }'
  ```
- **Visuals**:
  - Dashboard updates: Case status transitions to `recovered` (Green badge).
  - Amount Recovered increments to ₹3,499.00.
  - Activity Timeline appends a green settlement dot: *"Payment Settled & Case Recovered via Razorpay Webhook"*.
  - Terminal state locked: Case can no longer be modified or re-executed.
- **Talking Points**:
  > *"Boom! The customer paid. Razorpay sent `payment_link.paid`. Our webhook verified the signature, checked idempotency, updated the case to `recovered`, recorded the exact amount recovered, and permanently locked the state. ₹3,499 that would have been lost is now in the merchant's bank account."*

---

### Scene 6: Architecture, Security & Production Hardening (4:45 – 5:30)
- **Screen to Show**: Metrics Bar & Architecture Slide / Diagram from `docs/architecture.md`.
- **Key Talking Points to Conclude**:
  1. **Production Hardened**: 200 automated backend tests (`pytest`) covering state machine transitions, worker lease claiming, webhook replay idempotency, and multi-tenant IDOR isolation.
  2. **Security by Design**: HMAC-SHA256 signature verification, SHA-256 password reset tokens with anti-enumeration, and zero secret leakage.
  3. **Local & Cloud AI Ready**: Powered by Ollama locally without vendor lock-in, with deterministic heuristic fallbacks so recovery never halts if an LLM is offline.
  4. **Summary**: *"RecoverAI turns payment failure from a lost sale into an autonomous recovery loop. Thank you!"*
