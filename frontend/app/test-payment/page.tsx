"use client";

import { useEffect, useState } from "react";
import styles from "./test-payment.module.css";

declare global {
  interface Window {
    Razorpay?: new (options: RazorpayOptions) => RazorpayCheckout;
  }
}

type RazorpayCheckout = {
  open: () => void;
  on: (event: "payment.failed", handler: (response: RazorpayFailureResponse) => void) => void;
};

type RazorpayOptions = {
  key: string;
  amount: number;
  currency: string;
  name: string;
  description: string;
  order_id: string;
  handler: (response: RazorpaySuccessResponse) => void;
  modal: { ondismiss: () => void };
};

type RazorpaySuccessResponse = {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
};

type RazorpayFailureResponse = {
  error: Record<string, string>;
};

const scriptUrl = "https://checkout.razorpay.com/v1/checkout.js";
const keyId = process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID;

function loadRazorpayCheckout() {
  return new Promise<void>((resolve, reject) => {
    if (window.Razorpay) {
      resolve();
      return;
    }

    const existingScript = document.querySelector<HTMLScriptElement>(
      `script[src="${scriptUrl}"]`,
    );
    if (existingScript) {
      existingScript.addEventListener("load", () => resolve(), { once: true });
      existingScript.addEventListener("error", () => reject(new Error("Unable to load Razorpay Checkout.")), { once: true });
      return;
    }

    const script = document.createElement("script");
    script.src = scriptUrl;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Unable to load Razorpay Checkout."));
    document.body.appendChild(script);
  });
}

export default function TestPaymentPage() {
  const [checkoutReady, setCheckoutReady] = useState(false);
  const [isOpening, setIsOpening] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    loadRazorpayCheckout()
      .then(() => setCheckoutReady(true))
      .catch((error: Error) => setResult({ status: "failed", message: error.message }));
  }, []);

  const openCheckout = () => {
    if (!keyId) {
      setResult({ status: "failed", message: "NEXT_PUBLIC_RAZORPAY_KEY_ID is not configured." });
      return;
    }
    if (!window.Razorpay) {
      setResult({ status: "failed", message: "Razorpay Checkout is not ready yet." });
      return;
    }

    setIsOpening(true);
    setResult(null);

    const checkout = new window.Razorpay({
      key: keyId,
      amount: 10000,
      currency: "INR",
      name: "RecoverAI",
      description: "TEST-TANYA-001",
      order_id: "order_TX2m3w5tJ4FB8a",
      handler: (response) => {
        setResult({ status: "success", ...response });
        setIsOpening(false);
      },
      modal: {
        ondismiss: () => {
          setResult({ status: "dismissed", message: "Checkout was closed before payment completed." });
          setIsOpening(false);
        },
      },
    });

    checkout.on("payment.failed", (response) => {
      setResult({ status: "failed", ...response });
      setIsOpening(false);
    });
    checkout.open();
  };

  const buttonLabel = !keyId
    ? "Razorpay key required"
    : isOpening
      ? "Opening checkout…"
      : "Pay ₹100 Test Transaction";

  return (
    <main className={styles.page}>
      <section className={styles.card} aria-labelledby="test-payment-title">
        <p className={styles.eyebrow}>Razorpay Test Mode</p>
        <h1 id="test-payment-title">Test transaction checkout</h1>
        <p className={styles.description}>Order TEST-TANYA-001 · ₹100.00 INR</p>
        <button
          className={styles.payButton}
          type="button"
          onClick={openCheckout}
          disabled={!checkoutReady || !keyId || isOpening}
        >
          {buttonLabel}
        </button>
        {!checkoutReady && keyId && <p className={styles.helper}>Loading Razorpay Checkout…</p>}
        {result && (
          <pre className={result.status === "success" ? styles.success : styles.failure} aria-live="polite">
            {JSON.stringify(result, null, 2)}
          </pre>
        )}
      </section>
    </main>
  );
}
