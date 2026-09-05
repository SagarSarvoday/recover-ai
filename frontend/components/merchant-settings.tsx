"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  MerchantSettings as MerchantSettingsType,
  getMerchantSettings,
  updateMerchantSettings,
} from "../lib/api";
import { useAuth } from "./auth-provider";
import styles from "../app/settings/settings.module.css";

const OWNERSHIP_CONFLICT = "Razorpay account ID is already assigned to another merchant.";

export default function MerchantSettings() {
  const router = useRouter();
  const { merchant, accessToken, isLoading: isAuthenticating, logout, refreshMerchant } = useAuth();

  const [settingsData, setSettingsData] = useState<MerchantSettingsType | null>(null);
  const [isLoadingSettings, setIsLoadingSettings] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Form states
  const [businessName, setBusinessName] = useState("");
  const [supportEmail, setSupportEmail] = useState("");
  const [supportPhone, setSupportPhone] = useState("");
  const [razorpayAccountId, setRazorpayAccountId] = useState("");
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [expiryHours, setExpiryHours] = useState(48);
  const [waitMinutes, setWaitMinutes] = useState(60);
  const [autoNotify, setAutoNotify] = useState(true);

  // UI state
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [copiedWebhook, setCopiedWebhook] = useState(false);

  const hasFetched = useRef(false);

  const handleUnauthorized = useCallback(() => {
    logout();
    router.replace("/login");
  }, [logout, router]);

  const loadSettings = useCallback(async () => {
    if (!accessToken) return;
    setIsLoadingSettings(true);
    setLoadError(null);
    try {
      const data = await getMerchantSettings(accessToken);
      setSettingsData(data);
      setBusinessName(data.business_name ?? "");
      setSupportEmail(data.support_email ?? "");
      setSupportPhone(data.support_phone ?? "");
      setRazorpayAccountId(data.integrations.razorpay_account_id ?? "");
      setMaxAttempts(data.recovery.max_recovery_attempts ?? 3);
      setExpiryHours(data.recovery.default_payment_link_expiry_hours ?? 48);
      setWaitMinutes(data.recovery.default_wait_minutes ?? 60);
      setAutoNotify(data.recovery.auto_notify_customer ?? true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleUnauthorized();
        return;
      }
      setLoadError(err instanceof Error ? err.message : "Failed to load merchant settings.");
    } finally {
      setIsLoadingSettings(false);
    }
  }, [accessToken, handleUnauthorized]);

  useEffect(() => {
    if (!isAuthenticating && !merchant) {
      router.replace("/login");
      return;
    }
    if (merchant && accessToken && !hasFetched.current) {
      hasFetched.current = true;
      void loadSettings();
    }
  }, [isAuthenticating, merchant, accessToken, loadSettings, router]);

  async function handleSave(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) {
      handleUnauthorized();
      return;
    }

    setSaveSuccess(null);
    setSaveError(null);
    setIsSaving(true);

    try {
      const updated = await updateMerchantSettings(accessToken, {
        business_name: businessName.trim() || null,
        support_email: supportEmail.trim() || null,
        support_phone: supportPhone.trim() || null,
        razorpay_account_id: razorpayAccountId.trim() || null,
        max_recovery_attempts: Number(maxAttempts),
        default_payment_link_expiry_hours: Number(expiryHours),
        default_wait_minutes: Number(waitMinutes),
        auto_notify_customer: Boolean(autoNotify),
      });

      setSettingsData(updated);
      setBusinessName(updated.business_name ?? "");
      setSupportEmail(updated.support_email ?? "");
      setSupportPhone(updated.support_phone ?? "");
      setRazorpayAccountId(updated.integrations.razorpay_account_id ?? "");
      setMaxAttempts(updated.recovery.max_recovery_attempts);
      setExpiryHours(updated.recovery.default_payment_link_expiry_hours);
      setWaitMinutes(updated.recovery.default_wait_minutes);
      setAutoNotify(updated.recovery.auto_notify_customer);

      await refreshMerchant();
      setSaveSuccess("Settings and configurations successfully updated.");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleUnauthorized();
        return;
      }
      if (err instanceof ApiError && err.status === 409 && (err.message === OWNERSHIP_CONFLICT || err.message.includes("already assigned"))) {
        setSaveError("That Razorpay account ID is already connected to another merchant account. Use an account ID owned by your store.");
      } else {
        setSaveError(err instanceof Error ? err.message : "Unable to save merchant settings.");
      }
    } finally {
      setIsSaving(false);
    }
  }

  function copyWebhookUrl() {
    if (!settingsData?.webhook.webhook_url) return;
    navigator.clipboard.writeText(settingsData.webhook.webhook_url);
    setCopiedWebhook(true);
    setTimeout(() => setCopiedWebhook(false), 2500);
  }

  if (isAuthenticating || (isLoadingSettings && !settingsData)) {
    return (
      <main className={styles.loading}>
        <div className={styles.spinner} />
        <p>Loading merchant settings and integration status…</p>
      </main>
    );
  }

  if (loadError) {
    return (
      <main className={styles.page}>
        <div className={styles.card}>
          <Link href="/dashboard" className={styles.back}>← Back to Dashboard</Link>
          <div className={styles.error} role="alert">
            <p><strong>Failed to load settings:</strong> {loadError}</p>
            <button type="button" onClick={() => void loadSettings()} className={styles.retryBtn}>Retry</button>
          </div>
        </div>
      </main>
    );
  }

  const integrations = settingsData?.integrations;
  const webhook = settingsData?.webhook;
  const isOnboardingComplete = integrations?.onboarding_complete ?? false;

  return (
    <main className={styles.page}>
      <div className={styles.container}>
        <header className={styles.header}>
          <div>
            <Link href="/dashboard" className={styles.back}>← Back to Recovery Dashboard</Link>
            <p className={styles.eyebrow}>RecoverAI Merchant Operations</p>
            <h1>Settings & Integrations</h1>
            <p className={styles.description}>
              Configure your store branding, payment gateway connection, webhook security, email delivery, and autonomous recovery rules.
            </p>
          </div>
          <div className={styles.headerStatus}>
            <span className={isOnboardingComplete ? styles.statusBadgeActive : styles.statusBadgePending}>
              {isOnboardingComplete ? "✓ Onboarding Complete" : "● Setup Incomplete"}
            </span>
          </div>
        </header>

        {/* ONBOARDING CHECKLIST */}
        <section className={styles.onboardingCard}>
          <div className={styles.onboardingHeader}>
            <div>
              <h2>Integration Checklist</h2>
              <p>Everything required for autonomous payment recovery to monitor, link, and recover failed orders.</p>
            </div>
          </div>
          <div className={styles.checklistGrid}>
            <div className={styles.checkItem}>
              <div className={styles.checkIcon}>{merchant?.name ? "✓" : "○"}</div>
              <div>
                <strong>Merchant Account</strong>
                <span>{merchant?.email}</span>
              </div>
              <span className={styles.badgeSuccess}>Active</span>
            </div>

            <div className={styles.checkItem}>
              <div className={styles.checkIcon}>{integrations?.razorpay_configured ? "✓" : "!"}</div>
              <div>
                <strong>Razorpay Account ID</strong>
                <span>{integrations?.razorpay_account_id || "acc_... needed"}</span>
              </div>
              <span className={integrations?.razorpay_configured ? styles.badgeSuccess : styles.badgeWarning}>
                {integrations?.razorpay_configured ? "Connected" : "Action Needed"}
              </span>
            </div>

            <div className={styles.checkItem}>
              <div className={styles.checkIcon}>{webhook?.secret_configured ? "✓" : "!"}</div>
              <div>
                <strong>Webhook Secret</strong>
                <span>{webhook?.secret_configured ? "Configured & Secured" : "Missing Secret"}</span>
              </div>
              <span className={webhook?.secret_configured ? styles.badgeSuccess : styles.badgeWarning}>
                {webhook?.secret_configured ? "Verified" : "Unverified"}
              </span>
            </div>

            <div className={styles.checkItem}>
              <div className={styles.checkIcon}>{integrations?.smtp_configured ? "✓" : "○"}</div>
              <div>
                <strong>Customer Email (SMTP)</strong>
                <span>{integrations?.email_enabled ? "Enabled (Delivery ready)" : "Disabled / Simulation mode"}</span>
              </div>
              <span className={integrations?.email_enabled ? styles.badgeSuccess : styles.badgeMuted}>
                {integrations?.email_enabled ? "Enabled" : "Mock / Disabled"}
              </span>
            </div>
          </div>
        </section>

        {saveSuccess && <div className={styles.success} role="status">{saveSuccess}</div>}
        {saveError && <div className={styles.error} role="alert">{saveError}</div>}

        <form onSubmit={handleSave} className={styles.settingsForm}>
          {/* SECTION 1: BUSINESS PROFILE & BRANDING */}
          <section className={styles.card}>
            <div className={styles.cardHeader}>
              <div>
                <h2>Store Profile & Customer Support</h2>
                <p>These details are included in customer recovery emails and payment notifications.</p>
              </div>
            </div>

            <div className={styles.fieldGrid}>
              <div className={styles.field}>
                <label htmlFor="business-name">Business / Store Name</label>
                <input
                  id="business-name"
                  type="text"
                  placeholder="e.g. Acme Apparel India"
                  value={businessName}
                  onChange={(e) => setBusinessName(e.target.value)}
                  disabled={isSaving}
                  maxLength={150}
                />
                <span className={styles.hint}>Displayed as the store name in customer recovery emails.</span>
              </div>

              <div className={styles.field}>
                <label htmlFor="support-email">Customer Support Email</label>
                <input
                  id="support-email"
                  type="email"
                  placeholder="support@yourstore.com"
                  value={supportEmail}
                  onChange={(e) => setSupportEmail(e.target.value)}
                  disabled={isSaving}
                  maxLength={255}
                />
                <span className={styles.hint}>Customers can reply or reach out here if they have payment questions.</span>
              </div>

              <div className={styles.field}>
                <label htmlFor="support-phone">Customer Support Phone</label>
                <input
                  id="support-phone"
                  type="tel"
                  placeholder="+91 98765 43210"
                  value={supportPhone}
                  onChange={(e) => setSupportPhone(e.target.value)}
                  disabled={isSaving}
                  maxLength={30}
                />
                <span className={styles.hint}>Optional customer contact telephone number.</span>
              </div>
            </div>
          </section>

          {/* SECTION 2: RAZORPAY CONFIGURATION */}
          <section className={styles.card}>
            <div className={styles.cardHeader}>
              <div>
                <h2>Razorpay Payment Gateway</h2>
                <p>Connect your Razorpay account so RecoverAI can generate payment links and map webhooks.</p>
              </div>
              <span className={integrations?.razorpay_configured ? styles.badgeSuccess : styles.badgeWarning}>
                {integrations?.razorpay_configured ? "Ready" : "Incomplete"}
              </span>
            </div>

            <div className={styles.field}>
              <label htmlFor="razorpay-account-id">Razorpay Merchant Account ID</label>
              <input
                id="razorpay-account-id"
                type="text"
                placeholder="acc_XXXXXXXXXXXXXX"
                value={razorpayAccountId}
                onChange={(e) => setRazorpayAccountId(e.target.value)}
                disabled={isSaving}
                autoComplete="off"
              />
              <span className={styles.hint}>
                Find this in your Razorpay Dashboard under Account Settings. RecoverAI uses this ID to map incoming webhooks strictly to your account.
              </span>
            </div>

            <div className={styles.credentialsStatus}>
              <div className={styles.credentialItem}>
                <span>Razorpay Key ID</span>
                <span className={integrations?.razorpay_key_id_configured ? styles.badgeSuccess : styles.badgeMuted}>
                  {integrations?.razorpay_key_id_configured ? "Configured (Server env)" : "Not set"}
                </span>
              </div>
              <div className={styles.credentialItem}>
                <span>Razorpay Key Secret</span>
                <span className={integrations?.razorpay_key_secret_configured ? styles.badgeSuccess : styles.badgeMuted}>
                  {integrations?.razorpay_key_secret_configured ? "Configured (Server env)" : "Not set"}
                </span>
              </div>
              <div className={styles.credentialItem}>
                <span>API Secrets Policy</span>
                <span className={styles.securityNote}>
                  🔒 RecoverAI isolates API secrets in secure server storage. Secrets are never exposed to browser clients.
                </span>
              </div>
            </div>
          </section>

          {/* SECTION 3: WEBHOOK SETUP INSTRUCTIONS */}
          <section className={styles.card}>
            <div className={styles.cardHeader}>
              <div>
                <h2>Razorpay Webhook Configuration</h2>
                <p>Point your Razorpay Dashboard webhook to this URL to trigger autonomous recoveries in real-time.</p>
              </div>
              <span className={webhook?.secret_configured ? styles.badgeSuccess : styles.badgeWarning}>
                {webhook?.secret_configured ? "Secret Active" : "No Secret Set"}
              </span>
            </div>

            <div className={styles.field}>
              <label htmlFor="webhook-url">Your Dedicated Webhook URL</label>
              <div className={styles.copyGroup}>
                <input
                  id="webhook-url"
                  type="text"
                  readOnly
                  value={webhook?.webhook_url ?? ""}
                  className={styles.readOnlyInput}
                />
                <button
                  type="button"
                  onClick={copyWebhookUrl}
                  className={styles.copyBtn}
                >
                  {copiedWebhook ? "✓ Copied" : "Copy URL"}
                </button>
              </div>
            </div>

            <div className={styles.webhookInstructions}>
              <h4>Setup Instructions for Razorpay Dashboard</h4>
              <ol>
                <li>Log in to your <strong>Razorpay Dashboard</strong> and navigate to <em>Settings → Webhooks</em>.</li>
                <li>Click <strong>+ Add New Webhook</strong> and paste your Webhook URL above.</li>
                <li>Set the <strong>Secret</strong> to match your environment <code>RAZORPAY_WEBHOOK_SECRET</code>.</li>
                <li>Under <strong>Active Events</strong>, select:
                  <div className={styles.eventTags}>
                    <code>payment.failed</code>
                    <code>payment_link.paid</code>
                  </div>
                </li>
                <li>Click <strong>Save Webhook</strong>. All failed customer payments will instantly sync into RecoverAI.</li>
              </ol>
            </div>
          </section>

          {/* SECTION 4: EMAIL NOTIFICATIONS / SMTP STATUS */}
          <section className={styles.card}>
            <div className={styles.cardHeader}>
              <div>
                <h2>Customer Recovery Notifications (Email)</h2>
                <p>Payment retry links are automatically delivered to customers via branded transactional emails.</p>
              </div>
              <span className={integrations?.email_enabled ? styles.badgeSuccess : styles.badgeMuted}>
                {integrations?.email_enabled ? "SMTP Ready" : "Email Disabled (Simulation)"}
              </span>
            </div>

            <div className={styles.smtpStatusGrid}>
              <div className={styles.smtpItem}>
                <label>SMTP Host</label>
                <span>{integrations?.smtp_host ?? "None (disabled)"}</span>
              </div>
              <div className={styles.smtpItem}>
                <label>SMTP Port</label>
                <span>{integrations?.smtp_port ?? "N/A"}</span>
              </div>
              <div className={styles.smtpItem}>
                <label>Sender Address</label>
                <span>{integrations?.smtp_from_email ?? "noreply@recoverai.io"}</span>
              </div>
              <div className={styles.smtpItem}>
                <label>Customer Branding</label>
                <span>Includes {businessName || "Store Name"} & Support Link</span>
              </div>
            </div>

            <p className={styles.emailPreviewNote}>
              💡 <strong>How recovery emails work:</strong> When RecoverAI generates a recovery payment link, it sends an email displaying the store name, the specific bank failure reason, an expiry countdown, and your customer support contact.
            </p>
          </section>

          {/* SECTION 5: AUTONOMOUS RECOVERY CONFIGURATION */}
          <section className={styles.card}>
            <div className={styles.cardHeader}>
              <div>
                <h2>Autonomous Recovery Engine Rules</h2>
                <p>Fine-tune retry limits, payment link lifespans, and reassessment schedules for your store.</p>
              </div>
            </div>

            <div className={styles.fieldGrid}>
              <div className={styles.field}>
                <label htmlFor="max-attempts">
                  Maximum Recovery Attempts
                  <span className={styles.fieldValueBadge}>{maxAttempts} attempts</span>
                </label>
                <input
                  id="max-attempts"
                  type="range"
                  min="1"
                  max="5"
                  value={maxAttempts}
                  onChange={(e) => setMaxAttempts(Number(e.target.value))}
                  disabled={isSaving}
                  className={styles.rangeInput}
                />
                <span className={styles.hint}>
                  Number of recovery actions allowed per case before strict auto-closure (1 to 5). Default: 3.
                </span>
              </div>

              <div className={styles.field}>
                <label htmlFor="expiry-hours">
                  Payment Link Expiration
                  <span className={styles.fieldValueBadge}>{expiryHours} hours</span>
                </label>
                <input
                  id="expiry-hours"
                  type="range"
                  min="1"
                  max="168"
                  step="1"
                  value={expiryHours}
                  onChange={(e) => setExpiryHours(Number(e.target.value))}
                  disabled={isSaving}
                  className={styles.rangeInput}
                />
                <span className={styles.hint}>
                  Duration before a generated Razorpay recovery link expires (1 to 168 hours). Default: 48h.
                </span>
              </div>

              <div className={styles.field}>
                <label htmlFor="wait-minutes">Default Wait Window (Minutes)</label>
                <input
                  id="wait-minutes"
                  type="number"
                  min="15"
                  max="1440"
                  step="5"
                  value={waitMinutes}
                  onChange={(e) => setWaitMinutes(Number(e.target.value))}
                  disabled={isSaving}
                />
                <span className={styles.hint}>
                  Scheduled cooldown duration before reassessing temporary bank delays (15 to 1440 mins). Default: 60m.
                </span>
              </div>

              <div className={styles.checkboxField}>
                <label className={styles.checkboxLabel}>
                  <input
                    type="checkbox"
                    checked={autoNotify}
                    onChange={(e) => setAutoNotify(e.target.checked)}
                    disabled={isSaving}
                  />
                  <span>Automatically dispatch email notifications when payment retry links are created</span>
                </label>
                <span className={styles.hint}>
                  If disabled, links will be created but notifications will not be sent automatically.
                </span>
              </div>
            </div>
          </section>

          {/* FORM ACTIONS */}
          <div className={styles.formFooter}>
            <Link href="/dashboard" className={styles.cancelLink}>Cancel</Link>
            <button
              type="submit"
              disabled={isSaving}
              className={styles.saveBtn}
            >
              {isSaving ? "Saving Settings…" : "Save All Changes"}
            </button>
          </div>
        </form>
      </div>
    </main>
  );
}
