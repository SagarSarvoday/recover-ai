import { Suspense } from "react";
import { ResetPasswordForm } from "../../components/auth-form";

export const metadata = {
  title: "Reset Password | RecoverAI",
};

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<main style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "#f7f8fa" }}>Loading...</main>}>
      <ResetPasswordForm />
    </Suspense>
  );
}
