"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ApiError,
  authenticatedRequest,
  getMerchantAgentStatus,
  getMerchantMetrics,
  getRecoveryCaseActivity,
  startMerchantAgent,
  stopMerchantAgent,
  triggerManualCloseCase,
  triggerManualRetryLink,
  triggerRunAnalysis,
  type CaseActivityEvent,
  type MerchantAgentStatus,
  type MerchantMetrics,
} from "../lib/api";
import { useAuth } from "./auth-provider";
import styles from "../app/page.module.css";

type ApiRecoveryCase = {
  id: string;
  customer_id: string;
  customer_name: string;
  customer_email: string;
  payment_id: string;
  payment_status: string;
  failure_reason: string | null;
  payment_amount: number | string;
  status: "open" | "in_progress" | "waiting" | "payment_link_active" | "recovered" | "closed";
  amount_at_risk: number | string;
  recovered_amount?: number | string;
  amount_recovered?: number | string;
  attempt_count: number;
  ai_decision: "retry" | "contact" | "wait" | "skip" | "close" | null;
  ai_reason?: string | null;
  ai_decision_note?: string | null;
  ai_confidence?: number | null;
  next_action_at?: string | null;
  scheduled_action?: string | null;
  razorpay_payment_link_id?: string | null;
  payment_link_status?: "not_created" | "active" | "expired" | "paid";
  payment_link_expires_at?: string | null;
  payment_link_created_at?: string | null;
  payment_link_sent_at?: string | null;
  payment_link_paid_at?: string | null;
  created_at: string;
  updated_at: string;
};

const activeStatuses = new Set(["open", "in_progress", "waiting", "payment_link_active"]);

function toNumber(value: string | number | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}

function readable(value: string): string {
  return value.replace(/_/g, " ");
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      day: "numeric",
      month: "short",
    });
  } catch {
    return value;
  }
}

function getTimelineDetailsSummary(action: string, details?: Record<string, unknown>): { title: string; dotClass: string; summary?: string } {
  const normAction = action.toLowerCase();
  if (normAction.includes("payment_link.paid") || normAction.includes("payment_recovered") || normAction.includes("recovered")) {
    return {
      title: "Razorpay Payment Settled & Case Recovered",
      dotClass: styles.timelineDotGreen,
      summary: details?.amount ? `Amount received: ₹${Number(details.amount).toFixed(2)}` : "Confirmed via Razorpay webhook signature",
    };
  }
  if (normAction === "notification_sent") {
    const channel = details?.channel ? String(details.channel) : "email";
    return {
      title: `Recovery Notification Delivered (${channel.toUpperCase()})`,
      dotClass: styles.timelineDotGreen,
      summary: details?.recipient ? `Sent to ${details.recipient}` : "Payment link successfully delivered to customer",
    };
  }
  if (normAction === "notification_failed") {
    const reason = details?.reason ? String(details.reason) : "unknown error";
    return {
      title: "Notification Delivery Attempt Failed",
      dotClass: styles.timelineDotRed,
      summary: `Failure reason: ${readable(reason)}`,
    };
  }
  if (normAction.includes("payment_link") || normAction.includes("retry_payment")) {
    return {
      title: normAction.includes("reused") ? "Active Payment Link Reused" : "Razorpay Payment Link Generated",
      dotClass: styles.timelineDotBlue,
      summary: details?.razorpay_payment_link_id ? `Link ID: ${details.razorpay_payment_link_id}` : undefined,
    };
  }
  if (normAction.includes("ai_decision") || normAction.includes("analyzed") || normAction.includes("recommendation")) {
    return {
      title: "AI Recovery Assessment Completed",
      dotClass: styles.timelineDotAmber,
      summary: details?.decision ? `Decision: ${readable(String(details.decision))}` : undefined,
    };
  }
  if (normAction === "case_status_transition") {
    const toStatus = details?.to_status ? readable(String(details.to_status)) : "updated";
    return {
      title: `Case Status Changed → ${toStatus}`,
      dotClass: styles.timelineDotAmber,
      summary: details?.reason ? `Trigger: ${readable(String(details.reason))}` : undefined,
    };
  }
  return {
    title: readable(action),
    dotClass: styles.timelineDotBlue,
  };
}

export default function Dashboard() {
  const router = useRouter();
  const { merchant, accessToken, isLoading: isAuthenticating, logout } = useAuth();
  const [cases, setCases] = useState<ApiRecoveryCase[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [actionCaseId, setActionCaseId] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [agentStatus, setAgentStatus] = useState<MerchantAgentStatus | null>(null);
  const [isTogglingAgent, setIsTogglingAgent] = useState(false);
  const [agentFeedback, setAgentFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [inspectCase, setInspectCase] = useState<ApiRecoveryCase | null>(null);
  const [activityLogs, setActivityLogs] = useState<CaseActivityEvent[]>([]);
  const [isLoadingActivity, setIsLoadingActivity] = useState(false);
  const [metricsData, setMetricsData] = useState<MerchantMetrics | null>(null);
  const [isPerformingDrawerAction, setIsPerformingDrawerAction] = useState(false);
  const [drawerActionMessage, setDrawerActionMessage] = useState<string | null>(null);

  // Filter & Sort states
  const [filterSearch, setFilterSearch] = useState("");
  const [filterStatus, setFilterStatus] = useState("all");
  const [filterDecision, setFilterDecision] = useState("all");
  const [filterLinkStatus, setFilterLinkStatus] = useState("all");
  const [filterSort, setFilterSort] = useState("newest_failure");
  const [quickFilter, setQuickFilter] = useState<"all" | "waiting" | "expired">("all");

  const handleUnauthorized = useCallback(() => {
    logout();
    router.replace("/login");
  }, [logout, router]);

  const handleOpenInspect = useCallback(async (c: ApiRecoveryCase) => {
    setInspectCase(c);
    setDrawerActionMessage(null);
    if (!accessToken) return;
    setIsLoadingActivity(true);
    try {
      const logs = await getRecoveryCaseActivity(c.id, accessToken);
      setActivityLogs(logs);
    } catch {
      setActivityLogs([]);
    } finally {
      setIsLoadingActivity(false);
    }
  }, [accessToken]);

  const handleCloseInspect = useCallback(() => {
    setInspectCase(null);
    setActivityLogs([]);
    setDrawerActionMessage(null);
  }, []);

  const loadCases = useCallback(async (refresh = false) => {
    if (!accessToken) return;
    if (refresh) setIsRefreshing(true);
    else setIsLoading(true);
    setError(null);

    try {
      const [caseData, metricData] = await Promise.all([
        authenticatedRequest<ApiRecoveryCase[]>("/api/v1/recovery-cases", accessToken),
        getMerchantMetrics(accessToken).catch(() => null),
      ]);
      if (!Array.isArray(caseData)) throw new Error("The API returned an unexpected response.");
      setCases(caseData);
      if (metricData) setMetricsData(metricData);
      setLastUpdated(new Date());
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 401) {
        handleUnauthorized();
        return;
      }
      setError(requestError instanceof Error ? requestError.message : "Could not load recovery cases.");
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, [accessToken, handleUnauthorized]);

  const handleManualAnalyze = useCallback(async (caseId: string) => {
    if (!accessToken) return handleUnauthorized();
    setIsPerformingDrawerAction(true);
    setDrawerActionMessage(null);
    try {
      const res = await triggerRunAnalysis(caseId, accessToken);
      setDrawerActionMessage(`AI analysis complete: recommended "${res.decision.action}".`);
      await loadCases(true);
      const logs = await getRecoveryCaseActivity(caseId, accessToken);
      setActivityLogs(logs);
    } catch (err) {
      setDrawerActionMessage(err instanceof Error ? err.message : "Failed to run analysis.");
    } finally {
      setIsPerformingDrawerAction(false);
    }
  }, [accessToken, handleUnauthorized, loadCases]);

  const handleManualRetryLink = useCallback(async (caseId: string) => {
    if (!accessToken) return handleUnauthorized();
    setIsPerformingDrawerAction(true);
    setDrawerActionMessage(null);
    try {
      const res = await triggerManualRetryLink(caseId, accessToken);
      setDrawerActionMessage(res.action_execution_result.message);
      await loadCases(true);
      const logs = await getRecoveryCaseActivity(caseId, accessToken);
      setActivityLogs(logs);
    } catch (err) {
      setDrawerActionMessage(err instanceof Error ? err.message : "Failed to trigger recovery link.");
    } finally {
      setIsPerformingDrawerAction(false);
    }
  }, [accessToken, handleUnauthorized, loadCases]);

  const handleManualClose = useCallback(async (caseId: string) => {
    if (!accessToken) return handleUnauthorized();
    setIsPerformingDrawerAction(true);
    setDrawerActionMessage(null);
    try {
      const res = await triggerManualCloseCase(caseId, accessToken);
      setDrawerActionMessage(res.action_execution_result.message);
      await loadCases(true);
      const logs = await getRecoveryCaseActivity(caseId, accessToken);
      setActivityLogs(logs);
    } catch (err) {
      setDrawerActionMessage(err instanceof Error ? err.message : "Failed to close recovery case.");
    } finally {
      setIsPerformingDrawerAction(false);
    }
  }, [accessToken, handleUnauthorized, loadCases]);

const runWorkflow = useCallback(async (caseId: string) => {
  setActionCaseId(caseId);
  setActionMessage(null);

  try {
    if (!accessToken) return handleUnauthorized();
    const data = await authenticatedRequest<{ analysis: { decision: { action: string } }; execution: { action_execution_result: { success: boolean; message: string } } }>(`/api/v1/recovery-cases/${caseId}/run`, accessToken, { method: "POST" });

    const decision = data.analysis.decision;
    const result = data.execution.action_execution_result;

    if (result.success) {
      setActionMessage(
        `AI chose "${decision.action}". ${result.message}`,
      );
    } else {
      setActionMessage(
        `AI chose "${decision.action}", but execution was blocked: ${result.message}`,
      );
    }

    await loadCases(true);
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 401) {
        handleUnauthorized();
        return;
      }
    setActionMessage(
      requestError instanceof Error
        ? requestError.message
        : "Could not run the AI recovery workflow.",
    );
  } finally {
    setActionCaseId(null);
  }
}, [accessToken, handleUnauthorized, loadCases]);
  useEffect(() => {
    if (!isAuthenticating && !merchant) router.replace("/login");
    if (merchant) void loadCases();
  }, [isAuthenticating, loadCases, merchant, router]);

  const loadAgentStatus = useCallback(async () => {
    if (!accessToken) return;
    try {
      const data = await getMerchantAgentStatus(accessToken);
      setAgentStatus(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleUnauthorized();
      }
    }
  }, [accessToken, handleUnauthorized]);

  useEffect(() => {
    if (!accessToken || !merchant) return;
    void loadAgentStatus();
    const interval = setInterval(() => {
      void loadAgentStatus();
    }, 5000);
    return () => clearInterval(interval);
  }, [accessToken, loadAgentStatus, merchant]);

  const handleToggleAgent = useCallback(async () => {
    if (!accessToken) return handleUnauthorized();
    setIsTogglingAgent(true);
    setAgentFeedback(null);
    try {
      if (agentStatus?.enabled) {
        const updated = await stopMerchantAgent(accessToken);
        setAgentStatus(updated);
        setAgentFeedback({
          type: "success",
          message: "AI Recovery Agent stopped. Autonomous cycles paused.",
        });
      } else {
        const updated = await startMerchantAgent(accessToken);
        setAgentStatus(updated);
        setAgentFeedback({
          type: "success",
          message: "AI Recovery Agent is RUNNING. Autonomous cycles active.",
        });
      }
      await loadCases(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleUnauthorized();
        return;
      }
      setAgentFeedback({
        type: "error",
        message: err instanceof Error ? err.message : "Failed to update AI recovery agent.",
      });
    } finally {
      setIsTogglingAgent(false);
    }
  }, [accessToken, agentStatus?.enabled, handleUnauthorized, loadCases]);

  const nextScheduledAction = useMemo(() => {
    const scheduled = cases
      .filter((c) => c.next_action_at && new Date(c.next_action_at).getTime() > Date.now())
      .sort((a, b) => new Date(a.next_action_at!).getTime() - new Date(b.next_action_at!).getTime());
    if (scheduled.length === 0) return null;
    const first = scheduled[0];
    return {
      action: first.scheduled_action ? readable(first.scheduled_action) : "recovery followup",
      time: formatTimestamp(first.next_action_at),
    };
  }, [cases]);

  const metrics = useMemo(() => {
    const totalFailed = cases.length;
    const atRisk = cases.reduce(
      (sum, item) => sum + toNumber(item.amount_at_risk),
      0,
    );

    const recovered = cases.reduce(
      (sum, item) =>
        sum + toNumber(item.recovered_amount ?? item.amount_recovered),
      0,
    );

    const waiting = cases.filter(
      (item) => item.ai_decision === "wait" && (item.status === "open" || item.status === "in_progress")
    ).length;

    const actionPending = cases.filter(
      (item) =>
        item.status === "in_progress" &&
        (item.ai_decision === "retry" || item.ai_decision === "contact")
    ).length;

    const closed = cases.filter((item) => item.status === "closed").length;

    return {
      totalFailed,
      atRisk,
      recovered,
      rate: atRisk ? (recovered / atRisk) * 100 : 0,
      active: cases.filter((item) => activeStatuses.has(item.status)).length,
      waiting,
      actionPending,
      closed,
    };
  }, [cases]);

  const filteredCases = useMemo(() => {
    return cases
      .filter((c) => {
        if (quickFilter === "waiting" && c.status !== "waiting" && c.ai_decision !== "wait") return false;
        if (quickFilter === "expired" && c.payment_link_status !== "expired") return false;
        if (filterStatus !== "all" && c.status !== filterStatus) return false;
        if (filterDecision !== "all" && c.ai_decision !== filterDecision) return false;
        if (filterLinkStatus !== "all" && c.payment_link_status !== filterLinkStatus) return false;
        if (filterSearch.trim()) {
          const q = filterSearch.toLowerCase().trim();
          const matchName = (c.customer_name ?? "").toLowerCase().includes(q);
          const matchEmail = (c.customer_email ?? "").toLowerCase().includes(q);
          if (!matchName && !matchEmail) return false;
        }
        return true;
      })
      .sort((a, b) => {
        if (filterSort === "highest_risk") {
          return toNumber(b.amount_at_risk) - toNumber(a.amount_at_risk);
        }
        if (filterSort === "oldest_unresolved") {
          return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
        }
        if (filterSort === "next_action") {
          if (!a.next_action_at) return 1;
          if (!b.next_action_at) return -1;
          return new Date(a.next_action_at).getTime() - new Date(b.next_action_at).getTime();
        }
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      });
  }, [cases, filterDecision, filterLinkStatus, filterSearch, filterSort, filterStatus, quickFilter]);

  const notificationLogs = useMemo(() => {
    return activityLogs.filter(
      (log) => log.action === "notification_sent" || log.action === "notification_failed"
    );
  }, [activityLogs]);

if (isAuthenticating || !merchant) {
  return <main className={styles.authLoading}>Checking your merchant session…</main>;
}

return (
  <main className={styles.shell}>
    <aside className={styles.sidebar}>
      <a
        className={styles.brand}
        href="#top"
        aria-label="RecoverAI dashboard"
      >
        <span className={styles.brandMark}>R</span>
        <span>
          Recover<span>AI</span>
        </span>
      </a>

      <nav className={styles.nav} aria-label="Dashboard navigation">
        <Link className={styles.navActive} href="/dashboard">
          <span>▦</span> Overview
        </Link>
        <Link href="/dashboard#cases">
          <span>◌</span> Recovery cases
        </Link>
        <Link href="/customers">
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

    <section className={styles.content} id="top">
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Recovery command center</p>
          <h1>
            Revenue recovery, <em>at a glance.</em>
          </h1>
          <p className={styles.subhead}>
            Prioritise failed payments and see every recovery signal in one
            place.
          </p>
        </div>

        <div className={styles.headerActions}>
          <div className={styles.merchantIdentity}>
            <strong>{merchant.name}</strong>
            <span>{merchant.email}</span>
          </div>
          <button className={styles.logout} onClick={() => { logout(); router.replace("/login"); }}>Logout</button>
          <button className={styles.refresh} onClick={() => void loadCases(true)} disabled={isLoading || isRefreshing}>
            <span className={isRefreshing ? styles.spin : ""}>↻</span>
            {isRefreshing ? "Refreshing" : "Refresh data"}
          </button>
        </div>
      </header>

      {!merchant?.razorpay_account_id && (
        <div className={styles.onboardingBanner}>
          <div className={styles.onboardingBannerContent}>
            <div className={styles.onboardingBannerIcon}>⚡</div>
            <div>
              <h3>Setup Incomplete: Connect Your Razorpay Account</h3>
              <p>To enable autonomous recovery cycles and webhook synchronization, connect your Razorpay Merchant Account ID in Settings.</p>
            </div>
          </div>
          <Link href="/settings" className={styles.onboardingBannerBtn}>
            Configure Settings & Webhook →
          </Link>
        </div>
      )}

      {actionMessage && (
        <div className={styles.actionMessage} role="status">
          {actionMessage}
        </div>
      )}

      {agentFeedback && (
        <div
          className={
            agentFeedback.type === "success"
              ? styles.agentSuccessMessage
              : styles.agentErrorMessage
          }
          role="status"
        >
          {agentFeedback.message}
        </div>
      )}

      <section className={styles.agentControlPanel} aria-label="AI Recovery Agent Control">
        <div className={styles.agentControlHeader}>
          <div className={styles.agentIdentity}>
            <span className={styles.agentBadge}>AI AGENT</span>
            <div className={styles.agentStatusRow}>
              <div
                className={
                  agentStatus?.enabled
                    ? styles.agentStatusRunning
                    : styles.agentStatusOff
                }
              >
                <span
                  className={
                    agentStatus?.enabled ? styles.pulseDot : styles.offDot
                  }
                />
                {agentStatus?.enabled ? "RUNNING" : "OFF"}
              </div>
              <span className={styles.agentSubtext}>
                {agentStatus?.enabled
                  ? "Continuously watching eligible recovery work and triggering autonomous cycles."
                  : "Autonomous agent cycles are currently paused for your store."}
              </span>
            </div>
          </div>

          <button
            className={
              agentStatus?.enabled
                ? styles.agentStopButton
                : styles.agentStartButton
            }
            onClick={() => void handleToggleAgent()}
            disabled={isTogglingAgent}
          >
            {isTogglingAgent
              ? "Updating..."
              : agentStatus?.enabled
              ? "STOP AI AGENT"
              : "START AI AGENT"}
          </button>
        </div>

        <div className={styles.agentStatsGrid}>
          <div className={styles.agentStatItem}>
            <span className={styles.agentStatLabel}>Active cases</span>
            <strong className={styles.agentStatValue}>
              {agentStatus?.active_cases ?? 0}
            </strong>
          </div>
          <div className={styles.agentStatItem}>
            <span className={styles.agentStatLabel}>Actions today</span>
            <strong className={styles.agentStatValue}>
              {agentStatus?.actions_today ?? 0}
            </strong>
          </div>
          <div className={styles.agentStatItem}>
            <span className={styles.agentStatLabel}>Recovered today</span>
            <strong className={styles.agentStatValue}>
              {formatCurrency(toNumber(agentStatus?.recovered_today))}
            </strong>
          </div>
          <div className={styles.agentStatItem}>
            <span className={styles.agentStatLabel}>Last activity</span>
            <strong className={styles.agentStatValue}>
              {formatTimestamp(agentStatus?.last_activity_at)}
            </strong>
          </div>
          <div className={styles.agentStatItem}>
            <span className={styles.agentStatLabel}>Next scheduled action</span>
            <strong className={styles.agentStatValue}>
              {nextScheduledAction
                ? `${nextScheduledAction.action} (${nextScheduledAction.time})`
                : "None"}
            </strong>
          </div>
        </div>
      </section>

      <section className={styles.metrics} aria-label="Recovery metrics">
        <MetricCard
          label="Revenue at risk"
          value={formatCurrency(metrics.atRisk)}
          accent="risk"
          detail={`${metrics.totalFailed} total failed payments`}
          loading={isLoading}
        />

        <MetricCard
          label="Revenue recovered"
          value={formatCurrency(metrics.recovered)}
          accent="recovered"
          detail="Confirmed via payment webhook"
          loading={isLoading}
        />

        <MetricCard
          label="Recovery rate"
          value={`${metrics.rate.toFixed(1)}%`}
          accent="rate"
          detail="Recovered ÷ amount at risk"
          loading={isLoading}
        />

        <MetricCard
          label="Active recovery cases"
          value={String(metrics.active)}
          accent="active"
          detail={`${metrics.waiting} wait · ${metrics.actionPending} pending link · ${metrics.closed} closed`}
          loading={isLoading}
        />

        <MetricCard
          label="Payment links sent"
          value={String(metricsData?.payment_links_sent ?? cases.filter((c) => !!c.razorpay_payment_link_id).length)}
          accent="rate"
          detail="Active customer outreach links"
          loading={isLoading}
        />

        <MetricCard
          label="Expired payment links"
          value={String(metricsData?.expired_payment_links ?? cases.filter((c) => c.payment_link_status === "expired").length)}
          accent="risk"
          detail="Require reassessment / re-send"
          loading={isLoading}
        />

        <MetricCard
          label="Notification failures"
          value={String(metricsData?.customer_notification_failures ?? 0)}
          accent="active"
          detail="Missing email or delivery issues"
          loading={isLoading}
        />

        <MetricCard
          label="Waiting scheduled"
          value={String(metricsData?.waiting_cases ?? metrics.waiting)}
          accent="rate"
          detail="Will reassess when elapsed"
          loading={isLoading}
        />
      </section>

      <section className={styles.tableSection} id="cases">
        <div className={styles.sectionHead}>
          <div>
            <p className={styles.eyebrow}>Live queue</p>
            <h2>Recovery cases</h2>
          </div>

          <p className={styles.updated}>
            {lastUpdated
              ? `Updated ${lastUpdated.toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}`
              : null}
          </p>
        </div>

        {/* Filter and Sort Toolbar */}
        <div className={styles.filterToolbar}>
          <input
            type="text"
            className={styles.filterInput}
            placeholder="Search by customer name or email…"
            value={filterSearch}
            onChange={(e) => setFilterSearch(e.target.value)}
          />

          <select
            className={styles.filterSelect}
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            aria-label="Filter by case status"
          >
            <option value="all">All Statuses</option>
            <option value="open">Open</option>
            <option value="in_progress">In Progress</option>
            <option value="waiting">Waiting on Schedule</option>
            <option value="payment_link_active">Payment Link Active</option>
            <option value="recovered">Recovered</option>
            <option value="closed">Closed</option>
          </select>

          <select
            className={styles.filterSelect}
            value={filterDecision}
            onChange={(e) => setFilterDecision(e.target.value)}
            aria-label="Filter by AI decision"
          >
            <option value="all">All AI Decisions</option>
            <option value="retry">Retry</option>
            <option value="contact">Contact</option>
            <option value="wait">Wait</option>
            <option value="skip">Skip</option>
            <option value="close">Close</option>
          </select>

          <select
            className={styles.filterSelect}
            value={filterLinkStatus}
            onChange={(e) => setFilterLinkStatus(e.target.value)}
            aria-label="Filter by payment link state"
          >
            <option value="all">All Link States</option>
            <option value="active">Active Link</option>
            <option value="expired">Expired Link</option>
            <option value="paid">Paid Link</option>
            <option value="not_created">Not Created</option>
          </select>

          <select
            className={styles.filterSelect}
            value={filterSort}
            onChange={(e) => setFilterSort(e.target.value)}
            aria-label="Sort cases"
          >
            <option value="newest_failure">Newest Failure</option>
            <option value="highest_risk">Highest Risk Amount</option>
            <option value="oldest_unresolved">Oldest Unresolved</option>
            <option value="next_action">Next Scheduled Action</option>
          </select>

          <div className={styles.pillGroup}>
            <button
              type="button"
              className={`${styles.filterPill} ${quickFilter === "waiting" ? styles.filterPillActive : ""}`}
              onClick={() => setQuickFilter((prev) => (prev === "waiting" ? "all" : "waiting"))}
            >
              ⏳ Waiting ({metrics.waiting})
            </button>
            <button
              type="button"
              className={`${styles.filterPill} ${quickFilter === "expired" ? styles.filterPillActive : ""}`}
              onClick={() => setQuickFilter((prev) => (prev === "expired" ? "all" : "expired"))}
            >
              ⚠️ Expired Links
            </button>
          </div>
        </div>

        {error ? (
          <div className={styles.errorState} role="alert">
            <div>
              <strong>Couldn’t load recovery cases.</strong>
              <span>{error}</span>
            </div>
            <button onClick={() => void loadCases()}>Try again</button>
          </div>
        ) : isLoading ? (
          <LoadingTable />
        ) : cases.length === 0 ? (
          <div className={styles.emptyState}>
            <strong>No recovery cases yet</strong>
            <span>New cases from the recovery pipeline will appear here.</span>
          </div>
        ) : filteredCases.length === 0 ? (
          <div className={styles.emptyState}>
            <strong>No matching recovery cases</strong>
            <span>Try adjusting your search query or filter options.</span>
          </div>
        ) : (
          <div className={styles.tableWrap}>
            <table>
              <thead>
                <tr>
                  <th>Customer</th>
                  <th>Amount at risk</th>
                  <th>Recovered</th>
                  <th>Status</th>
                  <th>AI decision</th>
                  <th>Confidence</th>
                  <th>Payment link</th>
                  <th>Attempts</th>
                  <th>AI reason</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredCases.map((item) => {
                  const recovered = toNumber(
                    item.recovered_amount ?? item.amount_recovered,
                  );

                  const isWorking = actionCaseId === item.id;
                  const isFinished =
                    item.status === "recovered" || item.status === "closed";

                  return (
                    <tr key={item.id}>
                      <td>
                        <div className={styles.customer}>
                          <span className={styles.customerAvatar}>
                            {(item.customer_name ?? "?").slice(0, 1).toUpperCase()}
                          </span>

                          <span>
                            <span className={styles.customerName}>
                              {item.customer_name}
                            </span>

                            <span className={styles.customerEmail}>
                              {item.customer_email}
                            </span>
                          </span>
                        </div>
                      </td>

                      <td className={styles.money}>
                        {formatCurrency(toNumber(item.amount_at_risk))}
                      </td>

                      <td
                        className={
                          recovered > 0
                            ? styles.recovered
                            : styles.muted
                        }
                      >
                        {formatCurrency(recovered)}
                      </td>

                      <td>
                        <Badge type="status" value={item.status} />
                      </td>

                      <td>
                        {item.ai_decision ? (
                          <>
                            <Badge
                              type="decision"
                              value={item.ai_decision}
                            />
                            {item.ai_decision === "wait" && item.next_action_at && (
                              <span className={styles.scheduleDetail}>
                                Next {item.scheduled_action ?? "recovery action"}: {new Date(item.next_action_at).toLocaleString()}
                              </span>
                            )}
                          </>
                        ) : (
                          <span className={styles.muted}>—</span>
                        )}
                      </td>

                      <td>
                        {item.ai_confidence !== undefined && item.ai_confidence !== null ? (
                          <strong style={{ fontSize: 12, color: "#1e293b", fontFamily: "monospace" }}>
                            {Math.round(Number(item.ai_confidence) * 100)}%
                          </strong>
                        ) : (
                          <span className={styles.muted}>—</span>
                        )}
                      </td>

                      <td>
                        <span
                          className={`${styles.linkBadge} ${
                            styles[`link-${item.payment_link_status || "not_created"}`]
                          }`}
                        >
                          {readable(item.payment_link_status || "not_created")}
                        </span>
                      </td>

                      <td>
                        <span className={styles.attempts}>
                          {item.attempt_count}
                        </span>
                      </td>

                      <td className={styles.reason}>
                        {item.ai_reason ??
                          item.ai_decision_note ??
                          "No reason recorded"}
                      </td>

                      <td>
                        <button
                          className={styles.inspectBtn}
                          onClick={() => void handleOpenInspect(item)}
                          title="View case details & activity timeline"
                        >
                          Details
                        </button>
                        {isWorking ? (
                          <button
                            className={styles.actionButton}
                            disabled
                          >
                            Running...
                          </button>
                        ) : isFinished ? (
                          <span className={styles.actionDone}>
                            ✓ {item.status}
                          </span>
                        ) : (
                          <button
                            className={styles.actionButton}
                            onClick={() => void runWorkflow(item.id)}
                          >
                            Run AI
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </section>

    {inspectCase && (
      <div className={styles.modalOverlay} onClick={handleCloseInspect}>
        <div className={styles.drawer} onClick={(e) => e.stopPropagation()}>
          <div className={styles.drawerHeader}>
            <div>
              <p className={styles.eyebrow}>Recovery Case</p>
              <h3>Case {inspectCase.id.slice(0, 8)}…</h3>
            </div>
            <button className={styles.closeBtn} onClick={handleCloseInspect} aria-label="Close">
              ✕
            </button>
          </div>

          <div className={styles.drawerBody}>
            <div className={styles.aiTransparencyBanner}>
              <strong>Authoritative State Machine Invariant</strong>
              <p>
                AI recommendations analyze telemetry and automate customer outreach. A case transitions to <code>recovered</code> exclusively when a verified Razorpay payment webhook confirms settlement.
              </p>
            </div>

            <div className={styles.flowStepper}>
              <div className={`${styles.flowStep} ${styles.flowStepDone}`}>
                <span className={`${styles.flowStepIcon} ${styles.flowStepDone}`}>✓</span>
                <span>1. Failed Payment Recorded</span>
              </div>
              <div className={`${styles.flowStep} ${inspectCase.ai_decision ? styles.flowStepDone : styles.flowStepActive}`}>
                <span className={`${styles.flowStepIcon} ${inspectCase.ai_decision ? styles.flowStepDone : styles.flowStepActive}`}>
                  {inspectCase.ai_decision ? "✓" : "2"}
                </span>
                <span>
                  2. AI Decision: {inspectCase.ai_decision ? readable(inspectCase.ai_decision).toUpperCase() : "Awaiting analysis"}
                  {inspectCase.ai_confidence ? ` (${Math.round(Number(inspectCase.ai_confidence) * 100)}% conf)` : ""}
                </span>
              </div>
              <div className={`${styles.flowStep} ${inspectCase.payment_link_status === "active" || inspectCase.payment_link_status === "paid" || inspectCase.payment_link_status === "expired" ? styles.flowStepDone : (inspectCase.scheduled_action ? styles.flowStepDone : styles.flowStepPending)}`}>
                <span className={`${styles.flowStepIcon} ${inspectCase.payment_link_status !== "not_created" || inspectCase.scheduled_action ? styles.flowStepDone : styles.flowStepPending}`}>
                  {inspectCase.payment_link_status !== "not_created" || inspectCase.scheduled_action ? "✓" : "3"}
                </span>
                <span>
                  3. Recovery Action: {inspectCase.payment_link_status !== "not_created" ? `Link ${readable(inspectCase.payment_link_status || "created")}` : (inspectCase.scheduled_action ? `Wait scheduled` : "Pending")}
                </span>
              </div>
              <div className={`${styles.flowStep} ${inspectCase.payment_link_status !== "not_created" ? styles.flowStepDone : styles.flowStepPending}`}>
                <span className={`${styles.flowStepIcon} ${inspectCase.payment_link_status !== "not_created" ? styles.flowStepDone : styles.flowStepPending}`}>
                  {inspectCase.payment_link_status !== "not_created" ? "✓" : "4"}
                </span>
                <span>4. Customer Outreach (Secure link emailed)</span>
              </div>
              <div className={`${styles.flowStep} ${inspectCase.status === "recovered" ? styles.flowStepDone : styles.flowStepPending}`}>
                <span className={`${styles.flowStepIcon} ${inspectCase.status === "recovered" ? styles.flowStepDone : styles.flowStepPending}`}>
                  {inspectCase.status === "recovered" ? "✓" : "5"}
                </span>
                <span>
                  5. Settlement: {inspectCase.status === "recovered" ? "Verified via Razorpay Webhook" : "Waiting for customer payment"}
                </span>
              </div>
            </div>

            <div className={styles.drawerControls}>
              <span className={styles.drawerControlsTitle}>Merchant Recovery Controls</span>
              {drawerActionMessage && (
                <div className={styles.agentSuccessMessage} style={{ margin: "4px 0", fontSize: 11 }}>
                  {drawerActionMessage}
                </div>
              )}
              <div className={styles.drawerButtonRow}>
                <button
                  className={`${styles.ctrlBtn} ${styles.ctrlBtnPrimary}`}
                  onClick={() => void handleManualAnalyze(inspectCase.id)}
                  disabled={isPerformingDrawerAction || inspectCase.status === "recovered" || inspectCase.status === "closed"}
                  title="Run AI decision engine on this case now"
                >
                  Run AI Analysis Now
                </button>
                <button
                  className={styles.ctrlBtn}
                  onClick={() => void handleManualRetryLink(inspectCase.id)}
                  disabled={isPerformingDrawerAction || inspectCase.status === "recovered" || inspectCase.status === "closed"}
                  title="Generate or re-send payment link to customer"
                >
                  Send / Re-send Payment Link
                </button>
                <button
                  className={`${styles.ctrlBtn} ${styles.ctrlBtnDanger}`}
                  onClick={() => void handleManualClose(inspectCase.id)}
                  disabled={isPerformingDrawerAction || inspectCase.status === "recovered" || inspectCase.status === "closed"}
                  title="Close this recovery case"
                >
                  Close Case
                </button>
              </div>
            </div>

            <div className={styles.detailCard}>
              <h4 className={styles.detailCardTitle}>Customer & Payment</h4>
              <div className={styles.detailGrid}>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Customer</span>
                  <span className={styles.detailFieldValue}>{inspectCase.customer_name}</span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Email</span>
                  <span className={styles.detailFieldValue}>{inspectCase.customer_email || "None"}</span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Amount at Risk</span>
                  <span className={styles.detailFieldValue}>{formatCurrency(toNumber(inspectCase.amount_at_risk))}</span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Amount Recovered</span>
                  <span className={styles.detailFieldValue}>{formatCurrency(toNumber(inspectCase.recovered_amount ?? inspectCase.amount_recovered))}</span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Payment Status</span>
                  <span className={styles.detailFieldValue}>{inspectCase.payment_status}</span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Failure Reason</span>
                  <span className={styles.detailFieldValue}>{inspectCase.failure_reason || "None recorded"}</span>
                </div>
              </div>
            </div>

            <div className={styles.detailCard}>
              <h4 className={styles.detailCardTitle}>AI Recovery Assessment</h4>
              <div className={styles.detailGrid}>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Decision</span>
                  <span className={styles.detailFieldValue}>
                    {inspectCase.ai_decision ? readable(inspectCase.ai_decision) : "Pending analysis"}
                  </span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Confidence</span>
                  <span className={styles.detailFieldValue}>
                    {inspectCase.ai_confidence ? `${Math.round(Number(inspectCase.ai_confidence) * 100)}%` : "—"}
                  </span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Attempt Count</span>
                  <span className={styles.detailFieldValue}>{inspectCase.attempt_count}</span>
                </div>
                <div className={styles.detailField} style={{ gridColumn: "span 2" }}>
                  <span className={styles.detailFieldLabel}>Reasoning</span>
                  <span className={styles.detailFieldValue}>{inspectCase.ai_reason ?? inspectCase.ai_decision_note ?? "No note recorded"}</span>
                </div>
                {inspectCase.next_action_at && (
                  <div className={styles.detailField} style={{ gridColumn: "span 2" }}>
                    <span className={styles.detailFieldLabel}>Next Scheduled Follow-up</span>
                    <span className={styles.detailFieldValue}>{formatTimestamp(inspectCase.next_action_at)} ({readable(inspectCase.scheduled_action ?? "action")})</span>
                  </div>
                )}
              </div>
            </div>

            <div className={styles.detailCard}>
              <h4 className={styles.detailCardTitle}>Payment Link Lifecycle</h4>
              <div className={styles.detailGrid}>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>State</span>
                  <span className={styles.detailFieldValue}>
                    <span className={`${styles.linkBadge} ${styles['link-' + (inspectCase.payment_link_status || 'not_created')]}`}>
                      {readable(inspectCase.payment_link_status || "not_created")}
                    </span>
                  </span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Link ID</span>
                  <span className={styles.detailFieldValue}>{inspectCase.razorpay_payment_link_id || "None created"}</span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Created At</span>
                  <span className={styles.detailFieldValue}>{inspectCase.payment_link_created_at ? formatTimestamp(inspectCase.payment_link_created_at) : "N/A"}</span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Sent At</span>
                  <span className={styles.detailFieldValue}>{inspectCase.payment_link_sent_at ? formatTimestamp(inspectCase.payment_link_sent_at) : "N/A"}</span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Expires At</span>
                  <span className={styles.detailFieldValue}>{inspectCase.payment_link_expires_at ? formatTimestamp(inspectCase.payment_link_expires_at) : "N/A"}</span>
                </div>
                <div className={styles.detailField}>
                  <span className={styles.detailFieldLabel}>Paid At</span>
                  <span className={styles.detailFieldValue}>{inspectCase.payment_link_paid_at ? formatTimestamp(inspectCase.payment_link_paid_at) : "N/A"}</span>
                </div>
              </div>
            </div>

            <div className={styles.detailCard}>
              <h4 className={styles.detailCardTitle}>Customer Notification History</h4>
              {isLoadingActivity ? (
                <p style={{ color: "#94a3b8", fontSize: 12 }}>Loading notification logs…</p>
              ) : notificationLogs.length === 0 ? (
                <p style={{ color: "#94a3b8", fontSize: 12 }}>No notification attempts recorded yet.</p>
              ) : (
                <div className={styles.notifList}>
                  {notificationLogs.map((log) => {
                    const isSuccess = log.action === "notification_sent";
                    const recipient = (log.details?.recipient as string) || inspectCase.customer_email || "Customer";
                    const reason = (log.details?.reason as string) || null;
                    return (
                      <div key={log.id} className={styles.notifCard}>
                        <div className={styles.notifHeader}>
                          <span className={`${styles.notifBadge} ${isSuccess ? styles.notifBadgeSent : styles.notifBadgeFailed}`}>
                            {isSuccess ? "Delivered" : "Delivery Failed"}
                          </span>
                          <span className={styles.notifMeta}>{formatTimestamp(log.created_at)}</span>
                        </div>
                        <span className={styles.notifMeta}>To: {recipient} (Email)</span>
                        {reason && (
                          <span className={styles.notifReason}>Reason: {reason}</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            <div className={styles.detailCard}>
              <h4 className={styles.detailCardTitle}>Activity Timeline</h4>
              {isLoadingActivity ? (
                <p style={{ color: "#94a3b8", fontSize: 12 }}>Loading timeline events…</p>
              ) : activityLogs.length === 0 ? (
                <p style={{ color: "#94a3b8", fontSize: 12 }}>No activity events recorded yet.</p>
              ) : (
                <div className={styles.timelineWrap}>
                  {activityLogs.map((log) => {
                    const info = getTimelineDetailsSummary(log.action, log.details);
                    return (
                      <div key={log.id} className={styles.timelineItem}>
                        <span className={info.dotClass} />
                        <div className={styles.timelineContent}>
                          <div className={styles.timelineEventHeader}>
                            <span className={styles.timelineTitle}>{info.title}</span>
                            <span className={styles.timelineActorBadge}>{log.actor}</span>
                          </div>
                          <span className={styles.timelineTime}>{formatTimestamp(log.created_at)}</span>
                          {info.summary && (
                            <p className={styles.timelineSummaryText}>{info.summary}</p>
                          )}
                          {log.details && Object.keys(log.details).length > 0 && !info.summary && (
                            <pre className={styles.timelineDetails}>
                              {JSON.stringify(log.details, null, 2)}
                            </pre>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    )}
  </main>
);
}
function MetricCard({ label, value, detail, accent, loading }: { label: string; value: string; detail: string; accent: string; loading: boolean }) {
  return <article className={`${styles.metricCard} ${styles[accent]}`}><p>{label}</p><strong className={loading ? styles.valueLoading : ""}>{loading ? "" : value}</strong><span>{detail}</span></article>;
}

function Badge({ type, value }: { type: "status" | "decision"; value: string }) {
  return <span className={`${styles.badge} ${styles[`${type}-${value}`]}`}>{readable(value)}</span>;
}

function LoadingTable() {
  return <div className={styles.loadingTable} aria-label="Loading recovery cases">{Array.from({ length: 5 }, (_, index) => <span key={index} />)}</div>;
}
