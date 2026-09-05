import logging
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from email.message import EmailMessage
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.config import Settings, settings as global_settings
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase

logger = logging.getLogger(__name__)

PLACEHOLDER_EMAILS = {"void@razorpay.com", "noreply@recoverai.local"}


@dataclass(frozen=True)
class NotificationResult:
    success: bool
    status: str
    channel: str = "email"
    reason: str | None = None
    recipient_email: str | None = None


class SmtpSenderProtocol(Protocol):
    def __call__(self, message: EmailMessage, settings: Settings) -> None: ...


def is_usable_email(value: object) -> bool:
    """Validate that value is a genuine usable email address."""
    if not isinstance(value, str):
        return False
    email = value.strip().lower()
    if not email or email in PLACEHOLDER_EMAILS or "@" not in email:
        return False
    parts = email.split("@")
    if len(parts) != 2 or not parts[0] or not parts[1] or "." not in parts[1]:
        return False
    return True


def build_recovery_email_content(
    customer_name: str | None,
    amount: Decimal | float | str,
    payment_link: str,
    expires_at: datetime | None = None,
    currency: str = "INR",
    *,
    merchant_name: str | None = None,
    failure_reason: str | None = None,
    support_email: str | None = None,
) -> tuple[str, str, str]:
    """Return (subject, plain_text_body, html_body) for recovery email."""
    name = customer_name.strip() if customer_name and customer_name.strip() else "Customer"
    store_name = merchant_name.strip() if merchant_name and merchant_name.strip() else None
    currency_symbol = "₹" if str(currency).upper() == "INR" else f"{currency} "
    formatted_amount = f"{amount:.2f}" if isinstance(amount, (Decimal, float)) else str(amount)

    if expires_at is not None:
        expiry_str = expires_at.strftime("%B %d, %Y at %I:%M %p UTC")
    else:
        expiry_str = "48 hours from receipt"

    if store_name:
        subject = f"Payment required for your recent order with {store_name}"
    else:
        subject = "Payment required for your recent transaction"

    reason_line_plain = f"\nBank note: {failure_reason.strip()}\n" if failure_reason and failure_reason.strip() else ""
    reason_line_html = (
        f'    <p style="margin-bottom: 16px; font-size: 14px; color: #475569; background-color: #f1f5f9; padding: 10px 14px; border-radius: 6px;"><strong>Bank message:</strong> {failure_reason.strip()}</p>\n'
        if failure_reason and failure_reason.strip()
        else ""
    )

    support_line_plain = (
        f"\nNeed help? Contact our support team at {support_email.strip()}.\n"
        if support_email and support_email.strip()
        else ""
    )
    support_line_html = (
        f'    <p style="font-size: 13px; color: #64748b;">Questions or need assistance? Reach out to our support team at <a href="mailto:{support_email.strip()}" style="color: #2563eb;">{support_email.strip()}</a>.</p>\n'
        if support_email and support_email.strip()
        else ""
    )

    sender_signoff = f"{store_name} Customer Support" if store_name else "RecoverAI Customer Support"
    header_title = f"Payment required for your order with {store_name}" if store_name else "Payment required for your recent transaction"

    plain_text = (
        f"Hi {name},\n\n"
        f"Your recent payment of {currency_symbol}{formatted_amount}"
        + (f" to {store_name}" if store_name else "")
        + f" could not be completed.{reason_line_plain}\n\n"
        f"Please complete your payment using the secure payment link below:\n\n"
        f"{payment_link}\n\n"
        f"This payment link expires at {expiry_str}.\n"
        f"{support_line_plain}\n"
        f"If you have already completed the payment, no further action is required.\n\n"
        f"Thank you.\n"
        f"{sender_signoff}\n"
    )

    html = (
        "<!DOCTYPE html>\n"
        "<html>\n"
        f"<head><meta charset=\"utf-8\"><title>{subject}</title></head>\n"
        "<body style=\"font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1e293b; background-color: #f8fafc; margin: 0; padding: 24px;\">\n"
        "  <div style=\"max-width: 560px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 32px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);\">\n"
        f"    <h2 style=\"margin-top: 0; color: #0f172a; font-size: 20px; font-weight: 600;\">{header_title}</h2>\n"
        f"    <p style=\"margin-bottom: 16px;\">Hi {name},</p>\n"
        f"    <p style=\"margin-bottom: 20px;\">Your recent payment of <strong>{currency_symbol}{formatted_amount}</strong>"
        + (f" to <strong>{store_name}</strong>" if store_name else "")
        + " could not be completed.</p>\n"
        + f"{reason_line_html}"
        "    <p style=\"margin-bottom: 24px;\">Please complete your payment using the secure payment link below:</p>\n"
        f"    <p style=\"margin: 28px 0; text-align: center;\">\n"
        f"      <a href=\"{payment_link}\" style=\"background-color: #2563eb; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; display: inline-block; font-weight: 600; font-size: 15px;\">\n"
        "        Complete Payment\n"
        "      </a>\n"
        "    </p>\n"
        f"    <p style=\"font-size: 13px; color: #64748b; word-break: break-all;\">Or copy and paste this link into your browser:<br><a href=\"{payment_link}\" style=\"color: #2563eb;\">{payment_link}</a></p>\n"
        f"    <p style=\"font-size: 13px; color: #64748b;\">This payment link expires at {expiry_str}.</p>\n"
        f"{support_line_html}"
        "    <hr style=\"border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;\" />\n"
        f"    <p style=\"font-size: 12px; color: #94a3b8; margin-bottom: 0;\">If you have already completed the payment, no further action is required.<br>Thank you.<br>{sender_signoff}</p>\n"
        "  </div>\n"
        "</body>\n"
        "</html>"
    )

    return subject, plain_text, html


def build_password_reset_email_content(
    merchant_name: str | None,
    reset_url: str,
    expires_minutes: int = 20,
) -> tuple[str, str, str]:
    """Return (subject, plain_text_body, html_body) for merchant password reset email."""
    name = merchant_name.strip() if merchant_name and merchant_name.strip() else "Merchant"
    subject = "Reset your RecoverAI password"

    plain_text = (
        f"Hi {name},\n\n"
        f"We received a request to reset your password for RecoverAI.\n\n"
        f"Please click the link below to set a new password:\n\n"
        f"{reset_url}\n\n"
        f"This link will expire in {expires_minutes} minutes.\n\n"
        f"If you did not request this password reset, please ignore this email. Your password will remain unchanged.\n\n"
        f"Thank you,\n"
        f"The RecoverAI Security Team\n"
    )

    html = (
        "<!DOCTYPE html>\n"
        "<html>\n"
        f"<head><meta charset=\"utf-8\"><title>{subject}</title></head>\n"
        "<body style=\"font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #1e293b; background-color: #f8fafc; margin: 0; padding: 24px;\">\n"
        "  <div style=\"max-width: 560px; margin: 0 auto; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 32px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);\">\n"
        "    <h2 style=\"margin-top: 0; color: #0f172a; font-size: 20px; font-weight: 600;\">Reset your RecoverAI password</h2>\n"
        f"    <p style=\"margin-bottom: 16px;\">Hi {name},</p>\n"
        "    <p style=\"margin-bottom: 20px;\">We received a request to reset your password for your RecoverAI merchant account.</p>\n"
        "    <p style=\"margin-bottom: 24px;\">Please click the secure button below to set a new password:</p>\n"
        f"    <p style=\"margin: 28px 0; text-align: center;\">\n"
        f"      <a href=\"{reset_url}\" style=\"background-color: #0d9488; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; display: inline-block; font-weight: 600; font-size: 15px;\">\n"
        "        Reset Password\n"
        "      </a>\n"
        "    </p>\n"
        f"    <p style=\"font-size: 13px; color: #64748b; word-break: break-all;\">Or copy and paste this link into your browser:<br><a href=\"{reset_url}\" style=\"color: #0d9488;\">{reset_url}</a></p>\n"
        f"    <p style=\"font-size: 13px; color: #64748b;\">This link expires in {expires_minutes} minutes.</p>\n"
        "    <p style=\"font-size: 13px; color: #64748b; background-color: #f1f5f9; padding: 10px 14px; border-radius: 6px;\">If you did not request this password reset, you can safely ignore this email. Your password will remain unchanged.</p>\n"
        "    <hr style=\"border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;\" />\n"
        "    <p style=\"font-size: 12px; color: #94a3b8; margin-bottom: 0;\">Thank you,<br>The RecoverAI Security Team</p>\n"
        "  </div>\n"
        "</body>\n"
        "</html>"
    )

    return subject, plain_text, html


def _default_smtp_sender(message: EmailMessage, settings: Settings) -> None:
    """Send an email using standard SMTP over TLS."""
    if not settings.smtp_host:
        raise ValueError("smtp_host_not_configured")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_username and settings.smtp_password:
            server.login(
                settings.smtp_username,
                settings.smtp_password.get_secret_value(),
            )
        server.send_message(message)


def _sanitize_error_message(error: Exception) -> str:
    text = str(error)
    text = re.sub(r"(?i)(password|secret|key)\s*[:=]\s*[^,\s]+", r"\1=[redacted]", text)
    return text[:200] if text else type(error).__name__


class NotificationService:
    """Handles merchant-to-customer recovery notifications decoupled from AI reasoning."""

    def __init__(
        self,
        settings: Settings | None = None,
        smtp_sender: SmtpSenderProtocol | None = None,
    ) -> None:
        self._settings = settings or global_settings
        self._smtp_sender = smtp_sender or _default_smtp_sender

    def send_recovery_payment_email(
        self,
        customer: Customer | None,
        payment: Payment,
        payment_link: str,
        expires_at: datetime | None = None,
        *,
        merchant: Any | None = None,
        db: Session | None = None,
        recovery_case: RecoveryCase | None = None,
        actor: str = "notification_service",
    ) -> NotificationResult:
        """Validate customer, prepare email, deliver via SMTP if enabled, and audit result."""

        # ── 1. CUSTOMER VALIDATION ────────────────────────────────────
        if customer is None:
            reason = "customer_not_found"
            self._audit(db, recovery_case, action="notification_failed", actor=actor, details={"channel": "email", "success": False, "reason": reason})
            return NotificationResult(success=False, status="failed", reason=reason)

        if recovery_case is not None:
            cust_merch = getattr(customer, "merchant_id", None)
            case_merch = getattr(recovery_case, "merchant_id", None)
            if cust_merch is not None and case_merch is not None and cust_merch != case_merch:
                reason = "customer_merchant_mismatch"
                self._audit(db, recovery_case, action="notification_failed", actor=actor, details={"channel": "email", "success": False, "reason": reason})
                return NotificationResult(success=False, status="failed", reason=reason)

            case_cust_id = getattr(recovery_case, "customer_id", None)
            cust_id = getattr(customer, "id", None)
            if case_cust_id is not None and cust_id is not None and case_cust_id != cust_id:
                reason = "customer_case_mismatch"
                self._audit(db, recovery_case, action="notification_failed", actor=actor, details={"channel": "email", "success": False, "reason": reason})
                return NotificationResult(success=False, status="failed", reason=reason)

        if payment is not None:
            pay_cust_id = getattr(payment, "customer_id", None)
            cust_id = getattr(customer, "id", None)
            if pay_cust_id is not None and cust_id is not None and pay_cust_id != cust_id:
                reason = "customer_payment_mismatch"
                self._audit(db, recovery_case, action="notification_failed", actor=actor, details={"channel": "email", "success": False, "reason": reason})
                return NotificationResult(success=False, status="failed", reason=reason)

        customer_email = getattr(customer, "email", None)
        if not is_usable_email(customer_email):
            reason = "customer_email_missing"
            self._audit(db, recovery_case, action="notification_failed", actor=actor, details={"channel": "email", "success": False, "reason": reason})
            return NotificationResult(success=False, status="failed", reason=reason, recipient_email=None)

        # ── 2. CHECK PROVIDER STATE (EMAIL_ENABLED) ───────────────────
        if not self._settings.email_enabled:
            reason = "email_disabled"
            self._audit(db, recovery_case, action="notification_failed", actor=actor, details={"channel": "email", "success": False, "reason": reason})
            logger.info("Email notification skipped for customer %s (EMAIL_ENABLED=false)", getattr(customer, "id", None))
            return NotificationResult(
                success=False,
                status="email_disabled",
                reason=reason,
                recipient_email=customer_email,
            )

        # ── 3. PREPARE & DELIVER EMAIL ────────────────────────────────
        merchant_name = None
        support_email = None
        if merchant is not None:
            merchant_name = getattr(merchant, "business_name", None) or getattr(merchant, "name", None)
            support_email = getattr(merchant, "support_email", None)
        elif recovery_case is not None and db is not None and getattr(recovery_case, "merchant_id", None):
            from app.models.merchant import Merchant
            if hasattr(db, "get"):
                merch = db.get(Merchant, recovery_case.merchant_id)
                if merch:
                    merchant_name = getattr(merch, "business_name", None) or getattr(merch, "name", None)
                    support_email = getattr(merch, "support_email", None)

        failure_reason = getattr(payment, "failure_reason", None)

        subject, plain_text, html = build_recovery_email_content(
            customer_name=getattr(customer, "name", None),
            amount=getattr(payment, "amount", Decimal("0.00")),
            payment_link=payment_link,
            expires_at=expires_at,
            currency=getattr(payment, "currency", "INR"),
            merchant_name=merchant_name,
            failure_reason=failure_reason,
            support_email=support_email,
        )

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._settings.email_from
        msg["To"] = customer_email.strip()
        msg.set_content(plain_text)
        msg.add_alternative(html, subtype="html")

        try:
            self._smtp_sender(msg, self._settings)
            self._audit(
                db,
                recovery_case,
                action="notification_sent",
                actor=actor,
                details={
                    "channel": "email",
                    "purpose": "recovery_payment_link",
                    "success": True,
                    "recipient": customer_email.strip(),
                    "payment_link_created": True,
                },
            )
            logger.info("Recovery payment email successfully sent to %s", customer_email)
            return NotificationResult(
                success=True,
                status="sent",
                recipient_email=customer_email.strip(),
            )
        except Exception as error:
            safe_reason = _sanitize_error_message(error)
            logger.warning("SMTP email delivery failed for customer %s: %s", getattr(customer, "id", None), safe_reason)
            self._audit(
                db,
                recovery_case,
                action="notification_failed",
                actor=actor,
                details={
                    "channel": "email",
                    "success": False,
                    "reason": safe_reason,
                },
            )
            return NotificationResult(
                success=False,
                status="failed",
                reason=safe_reason,
                recipient_email=customer_email.strip(),
            )

    def send_password_reset_email(
        self,
        merchant: Merchant,
        raw_token: str,
        *,
        db: Session | None = None,
        actor: str = "auth_service",
    ) -> NotificationResult:
        """Send a password reset email to a merchant without logging or exposing the raw token."""
        merchant_email = getattr(merchant, "email", None)
        if not is_usable_email(merchant_email):
            return NotificationResult(
                success=False,
                status="failed",
                reason="invalid_recipient_email",
                recipient_email=None,
            )

        merchant_id = getattr(merchant, "id", None)
        merchant_name = getattr(merchant, "business_name", None) or getattr(merchant, "name", None)
        frontend_url = self._settings.frontend_base_url.rstrip("/")
        reset_url = f"{frontend_url}/reset-password?token={raw_token}"
        expires_minutes = self._settings.password_reset_token_expire_minutes

        if not self._settings.email_enabled:
            logger.info("Password reset email skipped for merchant %s (EMAIL_ENABLED=false)", merchant_id)
            if db is not None and merchant_id is not None:
                try:
                    db.add(
                        AuditLog(
                            entity_type="merchant",
                            entity_id=merchant_id,
                            action="password_reset_email_disabled",
                            actor=actor,
                            details={"channel": "email", "status": "email_disabled"},
                        )
                    )
                    db.commit()
                except Exception:
                    logger.warning("Failed to record audit log for password reset email", exc_info=True)
            return NotificationResult(
                success=False,
                status="email_disabled",
                reason="email_disabled",
                recipient_email=merchant_email,
            )

        subject, plain_text, html = build_password_reset_email_content(
            merchant_name=merchant_name,
            reset_url=reset_url,
            expires_minutes=expires_minutes,
        )

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._settings.email_from
        msg["To"] = merchant_email.strip()
        msg.set_content(plain_text)
        msg.add_alternative(html, subtype="html")

        try:
            self._smtp_sender(msg, self._settings)
            if db is not None and merchant_id is not None:
                try:
                    db.add(
                        AuditLog(
                            entity_type="merchant",
                            entity_id=merchant_id,
                            action="password_reset_email_sent",
                            actor=actor,
                            details={"channel": "email", "recipient": merchant_email},
                        )
                    )
                    db.commit()
                except Exception:
                    logger.warning("Failed to record audit log for password reset email sent", exc_info=True)
            return NotificationResult(success=True, status="delivered", recipient_email=merchant_email)
        except Exception as error:
            safe_error = _sanitize_error_message(error)
            logger.warning("Failed to deliver password reset email to %s: %s", merchant_email, safe_error)
            if db is not None and merchant_id is not None:
                try:
                    db.add(
                        AuditLog(
                            entity_type="merchant",
                            entity_id=merchant_id,
                            action="password_reset_email_failed",
                            actor=actor,
                            details={"channel": "email", "recipient": merchant_email, "error": safe_error},
                        )
                    )
                    db.commit()
                except Exception:
                    logger.warning("Failed to record audit log for password reset email failed", exc_info=True)
            return NotificationResult(
                success=False,
                status="failed",
                reason=safe_error,
                recipient_email=merchant_email,
            )

    @staticmethod
    def _audit(
        db: Session | None,
        recovery_case: RecoveryCase | None,
        *,
        action: str,
        actor: str,
        details: dict,
    ) -> None:
        if db is None or recovery_case is None:
            return
        case_id = getattr(recovery_case, "id", None)
        if case_id is None:
            return
        try:
            db.add(
                AuditLog(
                    entity_type="recovery_case",
                    entity_id=case_id,
                    action=action,
                    actor=actor,
                    details=details,
                )
            )
            db.commit()
        except Exception:
            logger.warning("Failed to record notification audit event for case %s", case_id, exc_info=True)


def send_recovery_payment_email(
    customer: Customer | None,
    payment: Payment,
    payment_link: str,
    expires_at: datetime | None = None,
    *,
    db: Session | None = None,
    recovery_case: RecoveryCase | None = None,
    settings: Settings | None = None,
    notification_service: NotificationService | None = None,
    actor: str = "notification_service",
) -> NotificationResult:
    """Convenience function delegating to NotificationService."""
    service = notification_service or NotificationService(settings)
    return service.send_recovery_payment_email(
        customer=customer,
        payment=payment,
        payment_link=payment_link,
        expires_at=expires_at,
        db=db,
        recovery_case=recovery_case,
        actor=actor,
    )
