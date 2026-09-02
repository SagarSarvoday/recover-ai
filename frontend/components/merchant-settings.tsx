"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, updateRazorpayAccount } from "../lib/api";
import { useAuth } from "./auth-provider";
import styles from "../app/settings/settings.module.css";

const OWNERSHIP_CONFLICT = "Razorpay account ID is already assigned to another merchant.";

export default function MerchantSettings() {
  const router = useRouter();
  const { merchant, accessToken, isLoading: isAuthenticating, logout, refreshMerchant } = useAuth();
  const [accountId, setAccountId] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const hasRefreshedProfile = useRef(false);

  const handleUnauthorized = useCallback(() => {
    logout();
    router.replace("/login");
  }, [logout, router]);

  useEffect(() => {
    if (!isAuthenticating && !merchant) {
      router.replace("/login");
      return;
    }
    if (merchant && !hasRefreshedProfile.current) {
      hasRefreshedProfile.current = true;
      setAccountId(merchant.razorpay_account_id ?? "");
      void refreshMerchant().catch(handleUnauthorized);
    }
  }, [handleUnauthorized, isAuthenticating, merchant, refreshMerchant, router]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedAccountId = accountId.trim();
    setSuccess(null);
    if (!trimmedAccountId) {
      setError("Enter your Razorpay account ID.");
      return;
    }
    if (!accessToken) {
      handleUnauthorized();
      return;
    }

    setError(null);
    setIsSaving(true);
    try {
      await updateRazorpayAccount(accessToken, trimmedAccountId);
      setAccountId(trimmedAccountId);
      await refreshMerchant();
      setSuccess("Razorpay account ID saved.");
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 401) {
        handleUnauthorized();
        return;
      }
      if (requestError instanceof ApiError && requestError.status === 409 && requestError.message === OWNERSHIP_CONFLICT) {
        setError("That Razorpay account ID is already connected to another merchant. Use an account ID owned by this merchant.");
      } else {
        setError(requestError instanceof Error ? requestError.message : "Could not save the Razorpay account ID.");
      }
    } finally {
      setIsSaving(false);
    }
  }

  if (isAuthenticating || !merchant) {
    return <main className={styles.loading}>Checking your merchant session…</main>;
  }

  return <main className={styles.page}>
    <section className={styles.card}>
      <Link href="/dashboard" className={styles.back}>← Back to Dashboard</Link>
      <p className={styles.eyebrow}>Merchant settings</p>
      <h1>Razorpay account</h1>
      <p className={styles.description}>Configure the Razorpay account that belongs to this merchant. RecoverAI uses this ID to map webhook events securely.</p>

      <dl className={styles.profile} aria-label="Merchant profile">
        <div><dt>Merchant name</dt><dd>{merchant.name}</dd></div>
        <div><dt>Merchant email</dt><dd>{merchant.email}</dd></div>
        <div><dt>Current Razorpay account ID</dt><dd>{merchant.razorpay_account_id ?? "Not configured"}</dd></div>
      </dl>

      <form className={styles.form} onSubmit={submit}>
        <label htmlFor="razorpay-account-id">Razorpay account ID</label>
        <input id="razorpay-account-id" value={accountId} onChange={(event) => setAccountId(event.target.value)} placeholder="Enter Razorpay account ID" autoComplete="off" disabled={isSaving} />
        <p className={styles.hint}>Use the account ID from your existing Razorpay configuration. This does not initiate Razorpay onboarding.</p>
        {error && <p className={styles.error} role="alert">{error}</p>}
        {success && <p className={styles.success} role="status">{success}</p>}
        <button className={styles.save} disabled={isSaving}>{isSaving ? "Saving…" : "Save Razorpay account"}</button>
      </form>
    </section>
  </main>;
}
