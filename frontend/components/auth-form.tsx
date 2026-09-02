"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, register } from "../lib/api";
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

function AuthShell({ title, description, children }: Readonly<{ title: string; description: string; children: React.ReactNode }>) {
  return <main className={styles.page}><section className={styles.card}><Link className={styles.brand} href="/">Recover<span>AI</span></Link><h1>{title}</h1><p className={styles.description}>{description}</p>{children}</section></main>;
}
