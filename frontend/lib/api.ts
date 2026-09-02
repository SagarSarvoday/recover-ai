export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export type Merchant = {
  id: string;
  name: string;
  email: string;
  razorpay_account_id: string | null;
  created_at: string;
  updated_at: string;
};

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiRequest<T>(path: string, init: RequestInit = {}, accessToken?: string): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("accept", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  const data: unknown = response.status === 204 ? null : await response.json().catch(() => null);

  if (!response.ok) {
    const detail = typeof data === "object" && data !== null && "detail" in data
      ? String(data.detail)
      : `The API returned ${response.status}.`;
    throw new ApiError(response.status, detail);
  }

  return data as T;
}

export function login(email: string, password: string): Promise<{ access_token: string; token_type: string }> {
  return apiRequest("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
}

export function register(name: string, email: string, password: string): Promise<Merchant> {
  return apiRequest("/api/v1/auth/register", { method: "POST", body: JSON.stringify({ name, email, password }) });
}

export function getCurrentMerchant(accessToken: string): Promise<Merchant> {
  return apiRequest("/api/v1/merchant/me", {}, accessToken);
}

export function updateRazorpayAccount(accessToken: string, razorpayAccountId: string): Promise<Merchant> {
  return apiRequest("/api/v1/merchant/razorpay-account", {
    method: "PUT",
    body: JSON.stringify({ razorpay_account_id: razorpayAccountId }),
  }, accessToken);
}

export function authenticatedRequest<T>(path: string, accessToken: string, init: RequestInit = {}): Promise<T> {
  return apiRequest<T>(path, init, accessToken);
}
