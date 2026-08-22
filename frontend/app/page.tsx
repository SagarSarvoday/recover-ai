"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./page.module.css";

type ApiRecoveryCase = {
  id: string;
  customer_id: string;
  customer_name: string;
  customer_email: string;
  payment_id: string;
  payment_status: string;
  failure_reason: string | null;
  payment_amount: number | string;
  status: "open" | "in_progress" | "recovered" | "closed";
  amount_at_risk: number | string;
  recovered_amount?: number | string;
  amount_recovered?: number | string;
  attempt_count: number;
  ai_decision: "retry" | "contact" | "wait" | "skip" | "close" | null;
  ai_reason?: string | null;
  ai_decision_note?: string | null;
  next_action_at?: string | null;
  created_at: string;
  updated_at: string;
};

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const activeStatuses = new Set(["open", "in_progress"]);

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

export default function Dashboard() {
  const [cases, setCases] = useState<ApiRecoveryCase[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const loadCases = useCallback(async (refresh = false) => {
    refresh ? setIsRefreshing(true) : setIsLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/recovery-cases`);
      if (!response.ok) throw new Error(`The API returned ${response.status}.`);
      const data: unknown = await response.json();
      if (!Array.isArray(data)) throw new Error("The API returned an unexpected response.");
      setCases(data as ApiRecoveryCase[]);
      setLastUpdated(new Date());
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Could not load recovery cases.");
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadCases();
  }, [loadCases]);

  const metrics = useMemo(() => {
    const atRisk = cases.reduce((sum, item) => sum + toNumber(item.amount_at_risk), 0);
    const recovered = cases.reduce(
      (sum, item) => sum + toNumber(item.recovered_amount ?? item.amount_recovered),
      0,
    );
    return {
      atRisk,
      recovered,
      rate: atRisk ? (recovered / atRisk) * 100 : 0,
      active: cases.filter((item) => activeStatuses.has(item.status)).length,
    };
  }, [cases]);

  return (
    <main className={styles.shell}>
      <aside className={styles.sidebar}>
        <a className={styles.brand} href="#top" aria-label="RecoverAI dashboard">
          <span className={styles.brandMark}>R</span>
          <span>Recover<span>AI</span></span>
        </a>
        <nav className={styles.nav} aria-label="Dashboard navigation">
          <a className={styles.navActive} href="#top"><span>▦</span> Overview</a>
          <a href="#cases"><span>◌</span> Recovery cases</a>
        </nav>
        <div className={styles.sidebarFooter}>
          <span className={styles.liveDot} /> Live recovery intelligence
        </div>
      </aside>

      <section className={styles.content} id="top">
        <header className={styles.header}>
          <div>
            <p className={styles.eyebrow}>Recovery command center</p>
            <h1>Revenue recovery, <em>at a glance.</em></h1>
            <p className={styles.subhead}>Prioritise failed payments and see every recovery signal in one place.</p>
          </div>
          <button className={styles.refresh} onClick={() => void loadCases(true)} disabled={isLoading || isRefreshing}>
            <span className={isRefreshing ? styles.spin : ""}>↻</span>
            {isRefreshing ? "Refreshing" : "Refresh data"}
          </button>
        </header>

        <section className={styles.metrics} aria-label="Recovery metrics">
          <MetricCard label="Revenue at risk" value={formatCurrency(metrics.atRisk)} accent="risk" detail="Across all recovery cases" loading={isLoading} />
          <MetricCard label="Revenue recovered" value={formatCurrency(metrics.recovered)} accent="recovered" detail="Recovered through follow-ups" loading={isLoading} />
          <MetricCard label="Recovery rate" value={`${metrics.rate.toFixed(1)}%`} accent="rate" detail="Recovered ÷ amount at risk" loading={isLoading} />
          <MetricCard label="Active recovery cases" value={String(metrics.active)} accent="active" detail="Open or in progress" loading={isLoading} />
        </section>

        <section className={styles.tableSection} id="cases">
          <div className={styles.sectionHead}>
            <div>
              <p className={styles.eyebrow}>Live queue</p>
              <h2>Recovery cases</h2>
            </div>
            <p className={styles.updated}>{lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Connecting to recovery API"}</p>
          </div>

          {error ? (
            <div className={styles.errorState} role="alert">
              <div><strong>Couldn’t load recovery cases.</strong><span>{error} Make sure the FastAPI server is running at {API_BASE_URL}.</span></div>
              <button onClick={() => void loadCases()}>Try again</button>
            </div>
          ) : isLoading ? (
            <LoadingTable />
          ) : cases.length === 0 ? (
            <div className={styles.emptyState}><strong>No recovery cases yet</strong><span>New cases from the recovery pipeline will appear here.</span></div>
          ) : (
            <div className={styles.tableWrap}>
              <table>
                <thead><tr><th>Customer</th><th>Amount at risk</th><th>Recovered</th><th>Status</th><th>AI decision</th><th>Attempts</th><th>AI reason</th></tr></thead>
                <tbody>
                  {cases.map((item) => {
                    const recovered = toNumber(item.recovered_amount ?? item.amount_recovered);
                    return <tr key={item.id}>
                      <td>
                        <div className={styles.customer}>
                          <span className={styles.customerAvatar}>{item.customer_name.slice(0, 1).toUpperCase()}</span>
                          <span><span className={styles.customerName}>{item.customer_name}</span><span className={styles.customerEmail}>{item.customer_email}</span></span>
                        </div>
                      </td>
                      <td className={styles.money}>{formatCurrency(toNumber(item.amount_at_risk))}</td>
                      <td className={recovered > 0 ? styles.recovered : styles.muted}>{formatCurrency(recovered)}</td>
                      <td><Badge type="status" value={item.status} /></td>
                      <td>{item.ai_decision ? <Badge type="decision" value={item.ai_decision} /> : <span className={styles.muted}>—</span>}</td>
                      <td><span className={styles.attempts}>{item.attempt_count}</span></td>
                      <td className={styles.reason}>{item.ai_reason ?? item.ai_decision_note ?? "No reason recorded"}</td>
                    </tr>;
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </section>
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
