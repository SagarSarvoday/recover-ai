import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MerchantSettings from "../components/merchant-settings";
import { AuthProvider } from "../components/auth-provider";

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

function renderSettings() {
  return render(<AuthProvider><MerchantSettings /></AuthProvider>);
}

function mockInitialProfile() {
  window.sessionStorage.setItem("recoverai.access-token", "safe-token");
  vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(merchant)).mockResolvedValueOnce(jsonResponse(merchant));
}

describe("merchant Razorpay settings", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    replace.mockReset();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("requires an authenticated merchant", async () => {
    renderSettings();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  });

  it("loads and displays the authenticated merchant profile", async () => {
    mockInitialProfile();
    renderSettings();
    expect(await screen.findByText(merchant.name)).toBeTruthy();
    expect(screen.getByText(merchant.email)).toBeTruthy();
    expect(screen.getByText("Not configured")).toBeTruthy();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/v1/merchant/me"), expect.anything()));
  });

  it("trims and saves a Razorpay account ID, then shows success", async () => {
    const configuredMerchant = { ...merchant, razorpay_account_id: "acc_unique123" };
    mockInitialProfile();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(configuredMerchant)).mockResolvedValueOnce(jsonResponse(configuredMerchant));
    const user = userEvent.setup();
    renderSettings();
    const input = await screen.findByLabelText("Razorpay account ID");
    await user.type(input, "  acc_unique123  ");
    await user.click(screen.getByRole("button", { name: "Save Razorpay account" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
    const [requestUrl, requestInit] = vi.mocked(fetch).mock.calls[2];
    expect(requestUrl).toContain("/api/v1/merchant/razorpay-account");
    expect(requestInit?.method).toBe("PUT");
    expect(requestInit?.body).toBe(JSON.stringify({ razorpay_account_id: "acc_unique123" }));
    expect((await screen.findByRole("status")).textContent).toContain("saved");
  });

  it("rejects an empty Razorpay account ID without calling the update API", async () => {
    mockInitialProfile();
    const user = userEvent.setup();
    renderSettings();
    await screen.findByLabelText("Razorpay account ID");
    await user.click(screen.getByRole("button", { name: "Save Razorpay account" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Enter your Razorpay account ID.");
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/razorpay-account"))).toBe(false);
  });

  it("shows an ownership conflict without attempting reassignment", async () => {
    mockInitialProfile();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ detail: "Razorpay account ID is already assigned to another merchant." }, 409));
    const user = userEvent.setup();
    renderSettings();
    await user.type(await screen.findByLabelText("Razorpay account ID"), "acc_taken123");
    await user.click(screen.getByRole("button", { name: "Save Razorpay account" }));
    expect((await screen.findByRole("alert")).textContent).toContain("already connected to another merchant");
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("provides navigation back to the dashboard", async () => {
    mockInitialProfile();
    renderSettings();
    const link = await screen.findByRole("link", { name: "← Back to Dashboard" });
    expect(link.getAttribute("href")).toBe("/dashboard");
  });
});
