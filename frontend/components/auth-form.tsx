"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ApiError, register, requestPasswordReset, resetPassword } from "../lib/api";
import { useAuth } from "./auth-provider";
import styles from "../app/auth.module.css";

export function LoginForm() {
  const router = useRouter();
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login(email, password);
      router.replace("/dashboard");
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Could not log in. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return <AuthShell title="Welcome back" description="Log in to view your merchant recovery cases."><form onSubmit={submit} className={styles.form}>
    <label>Email<input aria-label="Email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
    <label>Password<input aria-label="Password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    <button className={styles.primary} disabled={isSubmitting}>{isSubmitting ? "Logging in…" : "Log in"}</button>
    <p className={styles.switch}><Link href="/forgot-password">Forgot password?</Link></p>
    <p className={styles.switch}>New to RecoverAI? <Link href="/register">Create an account</Link></p>
  </form></AuthShell>;
}

export function RegisterForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(null);
    setIsSubmitting(true);
    try {
      await register(name, email, password);
      setNotice("Account created. Redirecting you to log in…");
      window.setTimeout(() => router.replace("/login"), 700);
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Could not create your account. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return <AuthShell title="Create your merchant account" description="Start with a secure RecoverAI workspace."><form onSubmit={submit} className={styles.form}>
    <label>Name<input aria-label="Name" autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} required /></label>
    <label>Email<input aria-label="Email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
    <label>Password<input aria-label="Password" type="password" autoComplete="new-password" minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {notice && <p role="status" className={styles.notice}>{notice}</p>}
    <button className={styles.primary} disabled={isSubmitting}>{isSubmitting ? "Creating account…" : "Create account"}</button>
    <p className={styles.switch}>Already registered? <Link href="/login">Log in</Link></p>
  </form></AuthShell>;
}

export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(null);
    setIsSubmitting(true);
    try {
      const response = await requestPasswordReset(email);
      setNotice(response.message || "If an account exists for this email, you will receive a password reset link shortly.");
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Unable to process password reset request. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthShell
      title="Reset your password"
      description="Enter your merchant email address and we'll send you a recovery link."
    >
      <form onSubmit={submit} className={styles.form}>
        <label>
          Email
          <input
            aria-label="Email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
        </label>
        {error && <p role="alert" className={styles.error}>{error}</p>}
        {notice && <p role="status" className={styles.notice}>{notice}</p>}
        <button className={styles.primary} disabled={isSubmitting}>
          {isSubmitting ? "Sending reset link…" : "Send reset link"}
        </button>
        <p className={styles.switch}>
          Remember your password? <Link href="/login">Log in</Link>
        </p>
      </form>
    </AuthShell>
  );
}

export function ResetPasswordForm() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token") || "";

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isSuccess, setIsSuccess] = useState(false);

  if (!token) {
    return (
      <AuthShell
        title="Invalid Reset Link"
        description="The password reset link is missing a token or is malformed."
      >
        <div className={styles.form}>
          <p role="alert" className={styles.error}>
            No reset token found in the URL. Please request a new password reset link.
          </p>
          <Link href="/forgot-password" className={styles.primary} style={{ textAlign: "center", display: "block" }}>
            Request New Reset Link
          </Link>
          <p className={styles.switch}>
            Back to <Link href="/login">Log in</Link>
          </p>
        </div>
      </AuthShell>
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(null);

    if (password.length < 8) {
      setError("Password must be at least 8 characters long.");
      return;
    }

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await resetPassword(token, password);
      setIsSuccess(true);
      setNotice(response.message || "Password has been successfully reset. You can now log in.");
    } catch (requestError) {
      setError(
        requestError instanceof ApiError
          ? requestError.message
          : "Could not reset your password. The link may have expired or already been used."
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AuthShell
      title="Set a new password"
      description="Choose a strong, new password for your merchant account."
    >
      {isSuccess ? (
        <div className={styles.form}>
          <p role="status" className={styles.notice}>
            {notice}
          </p>
          <Link href="/login" className={styles.primary} style={{ textAlign: "center", display: "block" }}>
            Proceed to Log In
          </Link>
        </div>
      ) : (
        <form onSubmit={submit} className={styles.form}>
          <label>
            New Password
            <input
              aria-label="New Password"
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          <label>
            Confirm New Password
            <input
              aria-label="Confirm New Password"
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              required
            />
          </label>
          {error && <p role="alert" className={styles.error}>{error}</p>}
          <button className={styles.primary} disabled={isSubmitting}>
            {isSubmitting ? "Resetting password…" : "Reset Password"}
          </button>
          <p className={styles.switch}>
            Need a new link? <Link href="/forgot-password">Request reset</Link>
          </p>
        </form>
      )}
    </AuthShell>
  );
}

function AuthShell({ title, description, children }: Readonly<{ title: string; description: string; children: React.ReactNode }>) {
  return <main className={styles.page}><section className={styles.card}><Link className={styles.brand} href="/">Recover<span>AI</span></Link><h1>{title}</h1><p className={styles.description}>{description}</p>{children}</section></main>;
}

