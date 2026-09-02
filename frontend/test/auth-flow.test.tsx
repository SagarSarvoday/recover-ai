import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Dashboard from "../components/dashboard";
import { AuthProvider } from "../components/auth-provider";
import { LoginForm, RegisterForm } from "../components/auth-form";

const replace = vi.fn();
const router = { replace };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const merchant = {
  id: "merchant-1", name: "Acme Store", email: "owner@acme.test", razorpay_account_id: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
};

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

describe("merchant authentication flow", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    replace.mockReset();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("logs in, stores the session token, and opens the dashboard", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ access_token: "safe-token", token_type: "bearer" }))
      .mockResolvedValueOnce(jsonResponse(merchant));
    const user = userEvent.setup();
    render(<AuthProvider><LoginForm /></AuthProvider>);
    await user.type(screen.getByLabelText("Email"), merchant.email);
    await user.type(screen.getByLabelText("Password"), "password123");
    await user.click(screen.getByRole("button", { name: "Log in" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
    expect(window.sessionStorage.getItem("recoverai.access-token")).toBe("safe-token");
    expect(fetch).toHaveBeenNthCalledWith(2, expect.stringContaining("/api/v1/merchant/me"), expect.objectContaining({ headers: expect.any(Headers) }));
  });

  it("shows a login failure without persisting a token", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ detail: "Invalid email or password." }, 401));
    const user = userEvent.setup();
    render(<AuthProvider><LoginForm /></AuthProvider>);
    await user.type(screen.getByLabelText("Email"), merchant.email);
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Log in" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Invalid email or password.");
    expect(window.sessionStorage.getItem("recoverai.access-token")).toBeNull();
  });

  it("registers an account and directs the merchant to login", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(merchant, 201));
    const setTimeoutSpy = vi.spyOn(window, "setTimeout");
    render(<AuthProvider><RegisterForm /></AuthProvider>);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Name"), merchant.name);
    await user.type(screen.getByLabelText("Email"), merchant.email);
    await user.type(screen.getByLabelText("Password"), "password123");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("status")).textContent).toContain("Account created");
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/v1/auth/register"), expect.anything());
    expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), 700);
  });

  it("redirects an unauthenticated dashboard visitor to login", async () => {
    render(<AuthProvider><Dashboard /></AuthProvider>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  });

  it("loads authenticated recovery cases with a bearer authorization header", async () => {
    window.sessionStorage.setItem("recoverai.access-token", "safe-token");
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(merchant)).mockResolvedValueOnce(jsonResponse([]));
    render(<AuthProvider><Dashboard /></AuthProvider>);
    expect(await screen.findByText("Acme Store")).toBeTruthy();
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    const [, init] = vi.mocked(fetch).mock.calls[1];
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer safe-token");
  });

  it("logs out and clears the stored token", async () => {
    window.sessionStorage.setItem("recoverai.access-token", "safe-token");
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(merchant)).mockResolvedValueOnce(jsonResponse([]));
    const user = userEvent.setup();
    render(<AuthProvider><Dashboard /></AuthProvider>);
    await user.click(await screen.findByRole("button", { name: "Logout" }));
    expect(window.sessionStorage.getItem("recoverai.access-token")).toBeNull();
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("clears the session and redirects when recovery requests return 401", async () => {
    window.sessionStorage.setItem("recoverai.access-token", "expired-token");
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(merchant)).mockResolvedValueOnce(jsonResponse({ detail: "Unauthorized" }, 401));
    render(<AuthProvider><Dashboard /></AuthProvider>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(window.sessionStorage.getItem("recoverai.access-token")).toBeNull();
  });
});
