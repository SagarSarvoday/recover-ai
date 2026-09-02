import Link from "next/link";
import styles from "./auth.module.css";

export default function LandingPage() {
  return (
    <main className={styles.landing}>
      <section>
        <p className={styles.kicker}>RecoverAI</p>
        <h1>Turn failed payments into recovered revenue.</h1>
        <p>Give your team a secure, merchant-specific recovery command center.</p>
        <div className={styles.actions}>
          <Link className={styles.primary} href="/register">Create merchant account</Link>
          <Link className={styles.secondary} href="/login">Log in</Link>
        </div>
      </section>
    </main>
  );
}
