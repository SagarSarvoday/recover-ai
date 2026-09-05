"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "../../components/auth-provider";
import {
  createCustomer,
  getCustomers,
  type Customer,
  ApiError,
} from "../../lib/api";
import styles from "../page.module.css";

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleDateString([], {
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  } catch {
    return value;
  }
}

export default function CustomersPage() {
  const router = useRouter();
  const { merchant, accessToken, isLoading: isAuthenticating, logout } = useAuth();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modal state for adding a customer
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [formName, setFormName] = useState("");
  const [formEmail, setFormEmail] = useState("");
  const [formPhone, setFormPhone] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const loadCustomers = useCallback(
    async (query?: string) => {
      if (!accessToken) return;
      setIsLoading(true);
      setError(null);
      try {
        const data = await getCustomers(accessToken, query);
        setCustomers(data);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          logout();
          router.replace("/login");
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to load customers.");
      } finally {
        setIsLoading(false);
      }
    },
    [accessToken, logout, router]
  );

  useEffect(() => {
    if (!isAuthenticating && !merchant) {
      router.replace("/login");
      return;
    }
    if (merchant) {
      void loadCustomers(searchQuery);
    }
  }, [isAuthenticating, merchant, router, loadCustomers, searchQuery]);

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(e.target.value);
  };

  const handleCreateCustomer = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!accessToken) return;
    if (!formEmail.trim()) {
      setFormError("Customer email is required.");
      return;
    }

    setIsSubmitting(true);
    setFormError(null);
    try {
      await createCustomer(accessToken, {
        name: formName.trim() || undefined,
        email: formEmail.trim(),
        phone: formPhone.trim() || undefined,
      });
      setIsAddModalOpen(false);
      setFormName("");
      setFormEmail("");
      setFormPhone("");
      await loadCustomers(searchQuery);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to create customer.");
    } finally {
      setIsSubmitting(false);
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
        <header className={styles.header}>
          <div>
            <p className={styles.eyebrow}>Directory & Accounts</p>
            <h1>
              Customer <em>Management</em>
            </h1>
            <p className={styles.subhead}>
              View and manage customers associated with your store. Each customer&apos;s recovery
              activity is isolated and tracked automatically upon payment events.
            </p>
          </div>

          <div className={styles.headerActions}>
            <button
              className={styles.primaryButton}
              onClick={() => setIsAddModalOpen(true)}
            >
              + Add Customer
            </button>
          </div>
        </header>

        <div className={styles.tableSection}>
          <div className={styles.sectionHead}>
            <div>
              <p className={styles.eyebrow}>Directory</p>
              <h2>All Customers ({customers.length})</h2>
            </div>
            <div>
              <input
                type="search"
                className={styles.searchInput}
                placeholder="Search by name, email, or phone…"
                value={searchQuery}
                onChange={handleSearchChange}
              />
            </div>
          </div>

          {error ? (
            <div className={styles.errorState}>
              <strong>Failed to load customers</strong>
              <span>{error}</span>
              <button onClick={() => void loadCustomers(searchQuery)}>Retry</button>
            </div>
          ) : isLoading ? (
            <div className={styles.loadingTable}>
              <span />
              <span />
              <span />
            </div>
          ) : customers.length === 0 ? (
            <div className={styles.emptyState}>
              <strong>No customers found</strong>
              <span>
                {searchQuery
                  ? "No customers matched your search query."
                  : "Customers will appear here automatically when failed payments occur, or you can add them manually."}
              </span>
            </div>
          ) : (
            <div className={styles.tableWrap}>
              <table>
                <thead>
                  <tr>
                    <th>Customer</th>
                    <th>Phone</th>
                    <th>Razorpay ID</th>
                    <th>Created</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {customers.map((c) => {
                    const initials = (c.name || c.email || "?")
                      .split(" ")
                      .map((n) => n[0])
                      .slice(0, 2)
                      .join("")
                      .toUpperCase();
                    return (
                      <tr key={c.id}>
                        <td>
                          <div className={styles.customer}>
                            <span className={styles.customerAvatar}>{initials}</span>
                            <div>
                              <strong className={styles.customerName}>
                                {c.name || "Unnamed Customer"}
                              </strong>
                              <span className={styles.customerEmail}>{c.email || "No email"}</span>
                            </div>
                          </div>
                        </td>
                        <td>{c.phone || <span className={styles.muted}>—</span>}</td>
                        <td>
                          {c.razorpay_customer_id ? (
                            <code style={{ fontSize: 11 }}>{c.razorpay_customer_id}</code>
                          ) : (
                            <span className={styles.muted}>—</span>
                          )}
                        </td>
                        <td>{formatTimestamp(c.created_at)}</td>
                        <td>
                          <Link href={`/customers/${c.id}`} className={styles.inspectBtn}>
                            View Profile →
                          </Link>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      {/* Add Customer Modal */}
      {isAddModalOpen && (
        <div className={styles.modalBackdrop} onClick={() => setIsAddModalOpen(false)}>
          <div className={styles.modalBox} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ margin: "0 0 16px", fontSize: 18, fontWeight: 700 }}>
              Add New Customer
            </h3>

            {formError && (
              <div className={styles.agentErrorMessage} style={{ marginBottom: 16 }}>
                {formError}
              </div>
            )}

            <form onSubmit={handleCreateCustomer}>
              <div className={styles.formGroup}>
                <label htmlFor="customerName">Full Name</label>
                <input
                  id="customerName"
                  type="text"
                  placeholder="e.g. Aarav Patel"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                />
              </div>

              <div className={styles.formGroup}>
                <label htmlFor="customerEmail">Email Address *</label>
                <input
                  id="customerEmail"
                  type="email"
                  required
                  placeholder="e.g. aarav@example.com"
                  value={formEmail}
                  onChange={(e) => setFormEmail(e.target.value)}
                />
              </div>

              <div className={styles.formGroup}>
                <label htmlFor="customerPhone">Phone Number</label>
                <input
                  id="customerPhone"
                  type="tel"
                  placeholder="e.g. +919876543210"
                  value={formPhone}
                  onChange={(e) => setFormPhone(e.target.value)}
                />
              </div>

              <div className={styles.formActions}>
                <button
                  type="button"
                  className={styles.secondaryButton}
                  onClick={() => setIsAddModalOpen(false)}
                  disabled={isSubmitting}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className={styles.primaryButton}
                  disabled={isSubmitting}
                >
                  {isSubmitting ? "Creating…" : "Save Customer"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </main>
  );
}
