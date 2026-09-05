"use client";

import { use, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "../../../components/auth-provider";
import {
  getCustomer,
  updateCustomer,
  type CustomerDetail,
  ApiError,
} from "../../../lib/api";
import styles from "../../page.module.css";

function formatCurrency(value: number | string | undefined): string {
  const parsed = Number(value ?? 0);
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(Number.isFinite(parsed) ? parsed : 0);
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleDateString([], {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return value;
  }
}

export default function CustomerDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: customerId } = use(params);
  const router = useRouter();
  const { merchant, accessToken, isLoading: isAuthenticating, logout } = useAuth();
  const [detail, setDetail] = useState<CustomerDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Edit Modal State
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [editName, setEditName] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editPhone, setEditPhone] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const loadDetail = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setError(null);
    try {
      const data = await getCustomer(customerId, accessToken);
      setDetail(data);
      setEditName(data.customer.name || "");
      setEditEmail(data.customer.email || "");
      setEditPhone(data.customer.phone || "");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        logout();
        router.replace("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to load customer profile.");
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, customerId, logout, router]);

  useEffect(() => {
    if (!isAuthenticating && !merchant) {
      router.replace("/login");
      return;
    }
    if (merchant) {
      void loadDetail();
    }
  }, [isAuthenticating, merchant, router, loadDetail]);

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accessToken) return;

    setIsSaving(true);
    setEditError(null);
    try {
      await updateCustomer(customerId, accessToken, {
        name: editName.trim() || undefined,
        email: editEmail.trim() || undefined,
        phone: editPhone.trim() || undefined,
      });
      setIsEditModalOpen(false);
      await loadDetail();
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Failed to update customer.");
    } finally {
      setIsSaving(false);
    }
  };

  if (isAuthenticating || !merchant) {
    return <main className={styles.authLoading}>Checking your merchant session…</main>;
  }

  return (
    <main className={styles.shell}>
      <aside className={styles.sidebar}>
        <Link className={styles.brand} href="/dashboard" aria-label="RecoverAI dashboard">
          <span className={styles.brandMark}>R</span>
          <span>
            Recover<span>AI</span>
          </span>
        </Link>

        <nav className={styles.nav} aria-label="Dashboard navigation">
          <Link href="/dashboard">
            <span>▦</span> Overview
          </Link>
          <Link href="/dashboard#cases">
            <span>◌</span> Recovery cases
          </Link>
          <Link className={styles.navActive} href="/customers">
            <span>👥</span> Customers
          </Link>
          <Link href="/settings">
            <span>⚙</span> Settings
          </Link>
        </nav>

        <div className={styles.sidebarFooter}>
          <span className={styles.liveDot} /> Live recovery intelligence
        </div>
      </aside>

      <section className={styles.content}>
        <div style={{ marginBottom: 20 }}>
          <Link
            href="/customers"
            style={{
              fontSize: 13,
              color: "#64748b",
              textDecoration: "none",
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            ← Back to All Customers
          </Link>
        </div>

        {error ? (
          <div className={styles.errorState}>
            <strong>Customer not found or error loading profile</strong>
            <span>{error}</span>
            <button onClick={() => void loadDetail()}>Retry</button>
          </div>
        ) : isLoading || !detail ? (
          <div className={styles.loadingTable}>
            <span />
            <span />
            <span />
          </div>
        ) : (
          <>
            <header className={styles.header}>
              <div>
                <p className={styles.eyebrow}>Customer Profile</p>
                <h1>
                  {detail.customer.name || "Unnamed Customer"}
                </h1>
                <p className={styles.subhead}>
                  {detail.customer.email || "No email on record"} &bull;{" "}
                  {detail.customer.phone || "No phone on record"}
                </p>
              </div>

              <div className={styles.headerActions}>
                <button
                  className={styles.secondaryButton}
                  onClick={() => setIsEditModalOpen(true)}
                >
                  Edit Profile
                </button>
              </div>
            </header>

            {/* Stats Metrics */}
            <div className={styles.metrics} style={{ marginTop: 32 }}>
              <article className={styles.metricCard}>
                <p>Total Revenue Spent</p>
                <strong className={styles.recovered}>
                  {formatCurrency(detail.total_spent)}
                </strong>
                <span>From successful direct payments</span>
              </article>

              <article className={styles.metricCard}>
                <p>Total Recovered</p>
                <strong className={styles.rate}>
                  {formatCurrency(detail.total_recovered)}
                </strong>
                <span>Rescued via RecoverAI</span>
              </article>

              <article className={styles.metricCard}>
                <p>Recovery Rate</p>
                <strong>{detail.recovery_rate}%</strong>
                <span>Recovered ÷ total at risk</span>
              </article>

              <article className={styles.metricCard}>
                <p>Total Payment Events</p>
                <strong>{detail.total_payments_count}</strong>
                <span>{detail.successful_payments_count} successful · {detail.failed_payments_count} failed</span>
              </article>

              <article className={styles.metricCard}>
                <p>Active Cases</p>
                <strong className={styles.risk}>{detail.active_cases_count}</strong>
                <span>{detail.recovery_cases.length} lifetime recovery cases</span>
              </article>
            </div>

            {/* Recovery Cases Table */}
            <div className={styles.tableSection}>
              <div className={styles.sectionHead}>
                <div>
                  <p className={styles.eyebrow}>AI Interventions</p>
                  <h2>Recovery Cases ({detail.recovery_cases.length})</h2>
                </div>
              </div>

              {detail.recovery_cases.length === 0 ? (
                <div className={styles.emptyState}>
                  <strong>No recovery incidents</strong>
                  <span>This customer has no failed payments on record.</span>
                </div>
              ) : (
                <div className={styles.tableWrap}>
                  <table>
                    <thead>
                      <tr>
                        <th>Status</th>
                        <th>At Risk</th>
                        <th>Recovered</th>
                        <th>Attempts</th>
                        <th>AI Decision</th>
                        <th>Payment Link</th>
                        <th>Created</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.recovery_cases.map((rc) => (
                        <tr key={rc.id}>
                          <td>
                            <span className={`${styles.badge} ${styles[`status-${rc.status}`]}`}>
                              {rc.status}
                            </span>
                          </td>
                          <td className={styles.money}>{formatCurrency(rc.amount_at_risk)}</td>
                          <td className={styles.recovered}>{formatCurrency(rc.amount_recovered)}</td>
                          <td>
                            <span className={styles.attempts}>{rc.attempt_count}</span>
                          </td>
                          <td>
                            {rc.ai_decision ? (
                              <span className={`${styles.badge} ${styles[`decision-${rc.ai_decision}`]}`}>
                                {rc.ai_decision}
                              </span>
                            ) : (
                              <span className={styles.muted}>—</span>
                            )}
                          </td>
                          <td>
                            <span className={`${styles.linkBadge} ${styles[`link-${rc.payment_link_status}`]}`}>
                              {rc.payment_link_status}
                            </span>
                          </td>
                          <td>{formatTimestamp(rc.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Payments History Table */}
            <div className={styles.tableSection} style={{ marginTop: 36 }}>
              <div className={styles.sectionHead}>
                <div>
                  <p className={styles.eyebrow}>Transactions</p>
                  <h2>Payment Records ({detail.payments.length})</h2>
                </div>
              </div>

              {detail.payments.length === 0 ? (
                <div className={styles.emptyState}>
                  <strong>No payments</strong>
                  <span>No payment records found for this customer.</span>
                </div>
              ) : (
                <div className={styles.tableWrap}>
                  <table>
                    <thead>
                      <tr>
                        <th>Amount</th>
                        <th>Status</th>
                        <th>Failure Reason</th>
                        <th>Paid At</th>
                        <th>Recorded At</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.payments.map((p) => (
                        <tr key={p.id}>
                          <td className={styles.money}>{formatCurrency(p.amount)}</td>
                          <td>
                            <span
                              className={`${styles.badge} ${
                                p.status === "succeeded"
                                  ? styles["status-recovered"]
                                  : styles["status-open"]
                              }`}
                            >
                              {p.status}
                            </span>
                          </td>
                          <td>{p.failure_reason || <span className={styles.muted}>None</span>}</td>
                          <td>{formatTimestamp(p.paid_at)}</td>
                          <td>{formatTimestamp(p.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Customer Notification History */}
            <div className={styles.tableSection} style={{ marginTop: 36 }}>
              <div className={styles.sectionHead}>
                <div>
                  <p className={styles.eyebrow}>Outreach</p>
                  <h2>Customer Notifications ({(detail.notifications ?? []).length})</h2>
                </div>
              </div>

              {(detail.notifications ?? []).length === 0 ? (
                <div className={styles.emptyState}>
                  <strong>No notification records</strong>
                  <span>No recovery emails or notifications recorded for this customer.</span>
                </div>
              ) : (
                <div className={styles.tableWrap}>
                  <table>
                    <thead>
                      <tr>
                        <th>Status</th>
                        <th>Channel</th>
                        <th>Recipient</th>
                        <th>Details</th>
                        <th>Recorded At</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(detail.notifications ?? []).map((n) => {
                        const isSuccess = n.action === "notification_sent";
                        const recipient = (n.details?.recipient as string) || detail.customer.email || "Customer";
                        const reason = (n.details?.reason as string) || null;
                        return (
                          <tr key={n.id}>
                            <td>
                              <span
                                className={`${styles.badge} ${
                                  isSuccess ? styles["status-recovered"] : styles["status-closed"]
                                }`}
                              >
                                {isSuccess ? "Delivered" : "Delivery Failed"}
                              </span>
                            </td>
                            <td>Email</td>
                            <td>{recipient}</td>
                            <td>
                              {reason ? (
                                <span style={{ color: "#ef4444" }}>{reason}</span>
                              ) : (
                                <span style={{ color: "#059669" }}>Payment link sent successfully</span>
                              )}
                            </td>
                            <td>{formatTimestamp(n.created_at)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </section>

      {/* Edit Customer Modal */}
      {isEditModalOpen && (
        <div className={styles.modalBackdrop} onClick={() => setIsEditModalOpen(false)}>
          <div className={styles.modalBox} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ margin: "0 0 16px", fontSize: 18, fontWeight: 700 }}>
              Edit Customer Profile
            </h3>

            {editError && (
              <div className={styles.agentErrorMessage} style={{ marginBottom: 16 }}>
                {editError}
              </div>
            )}

            <form onSubmit={handleUpdate}>
              <div className={styles.formGroup}>
                <label htmlFor="editName">Full Name</label>
                <input
                  id="editName"
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                />
              </div>

              <div className={styles.formGroup}>
                <label htmlFor="editEmail">Email Address</label>
                <input
                  id="editEmail"
                  type="email"
                  value={editEmail}
                  onChange={(e) => setEditEmail(e.target.value)}
                />
              </div>

              <div className={styles.formGroup}>
                <label htmlFor="editPhone">Phone Number</label>
                <input
                  id="editPhone"
                  type="tel"
                  value={editPhone}
                  onChange={(e) => setEditPhone(e.target.value)}
                />
              </div>

              <div className={styles.formActions}>
                <button
                  type="button"
                  className={styles.secondaryButton}
                  onClick={() => setIsEditModalOpen(false)}
                  disabled={isSaving}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className={styles.primaryButton}
                  disabled={isSaving}
                >
                  {isSaving ? "Saving…" : "Save Changes"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </main>
  );
}
