export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

export type Merchant = {
  id: string;
  name: string;
  email: string;
  business_name?: string | null;
  support_email?: string | null;
  support_phone?: string | null;
  razorpay_account_id: string | null;
  ai_agent_enabled?: boolean;
  max_recovery_attempts?: number;
  default_payment_link_expiry_hours?: number;
  default_wait_minutes?: number;
  auto_notify_customer?: boolean;
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

export function requestPasswordReset(email: string): Promise<{ message: string }> {
  return apiRequest("/api/v1/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(token: string, newPassword: string): Promise<{ message: string }> {
  return apiRequest("/api/v1/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
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

export type MerchantAgentStatus = {
  enabled: boolean;
  status: "running" | "stopped";
  started_at: string | null;
  last_activity_at: string | null;
  active_cases: number;
  actions_today: number;
  recovered_today: number | string;
};

export function getMerchantAgentStatus(accessToken: string): Promise<MerchantAgentStatus> {
  return apiRequest("/api/v1/merchant/agent", {}, accessToken);
}

export function startMerchantAgent(accessToken: string): Promise<MerchantAgentStatus> {
  return apiRequest("/api/v1/merchant/agent/start", { method: "POST" }, accessToken);
}

export function stopMerchantAgent(accessToken: string): Promise<MerchantAgentStatus> {
  return apiRequest("/api/v1/merchant/agent/stop", { method: "POST" }, accessToken);
}

export function authenticatedRequest<T>(path: string, accessToken: string, init: RequestInit = {}): Promise<T> {
  return apiRequest<T>(path, init, accessToken);
}

export type CaseActivityEvent = {
  id: string;
  action: string;
  actor: string;
  details: Record<string, unknown>;
  created_at: string | null;
};

export function getRecoveryCaseActivity(caseId: string, accessToken: string): Promise<CaseActivityEvent[]> {
  return authenticatedRequest<CaseActivityEvent[]>(`/api/v1/recovery-cases/${caseId}/activity`, accessToken);
}

export type Customer = {
  id: string;
  merchant_id: string;
  name: string | null;
  email: string | null;
  phone: string | null;
  razorpay_customer_id: string | null;
  created_at: string;
  updated_at: string;
};

export type CustomerPaymentItem = {
  id: string;
  amount: number | string;
  currency: string;
  status: string;
  failure_reason: string | null;
  paid_at: string | null;
  created_at: string;
};

export type CustomerRecoveryCaseItem = {
  id: string;
  payment_id: string;
  status: string;
  amount_at_risk: number | string;
  amount_recovered: number | string;
  attempt_count: number;
  ai_decision: string | null;
  razorpay_payment_link_id: string | null;
  payment_link_status: string;
  created_at: string;
};

export type CustomerDetail = {
  customer: Customer;
  total_payments_count: number;
  successful_payments_count: number;
  failed_payments_count: number;
  total_spent: number | string;
  total_recovered: number | string;
  recovery_rate: number;
  active_cases_count: number;
  payments: CustomerPaymentItem[];
  recovery_cases: CustomerRecoveryCaseItem[];
  notifications: Array<{
    id: string;
    case_id?: string;
    action: string;
    actor: string;
    details: Record<string, unknown>;
    created_at: string | null;
  }>;
};

export type MerchantMetrics = {
  failed_payments: number;
  total_value_at_risk: number | string;
  recovered_amount: number | string;
  recovery_rate: number;
  active_recoveries: number;
  waiting_cases: number;
  payment_links_sent: number;
  expired_payment_links: number;
  customer_notification_failures: number;
};

export function getCustomers(accessToken: string, query?: string): Promise<Customer[]> {
  const url = query ? `/api/v1/customers?query=${encodeURIComponent(query)}` : "/api/v1/customers";
  return authenticatedRequest<Customer[]>(url, accessToken);
}

export function getCustomer(customerId: string, accessToken: string): Promise<CustomerDetail> {
  return authenticatedRequest<CustomerDetail>(`/api/v1/customers/${customerId}`, accessToken);
}

export function createCustomer(
  accessToken: string,
  data: { name?: string; email: string; phone?: string }
): Promise<Customer> {
  return authenticatedRequest<Customer>("/api/v1/customers", accessToken, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updateCustomer(
  customerId: string,
  accessToken: string,
  data: { name?: string; email?: string; phone?: string }
): Promise<Customer> {
  return authenticatedRequest<Customer>(`/api/v1/customers/${customerId}`, accessToken, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function deleteCustomer(customerId: string, accessToken: string): Promise<void> {
  return authenticatedRequest<void>(`/api/v1/customers/${customerId}`, accessToken, {
    method: "DELETE",
  });
}

export function getMerchantMetrics(accessToken: string): Promise<MerchantMetrics> {
  return authenticatedRequest<MerchantMetrics>("/api/v1/merchant/metrics", accessToken);
}

export function triggerManualRetryLink(
  caseId: string,
  accessToken: string
): Promise<{ action_execution_result: { success: boolean; message: string } }> {
  return authenticatedRequest(`/api/v1/recovery-cases/${caseId}/actions/retry-link`, accessToken, {
    method: "POST",
  });
}

export function triggerManualCloseCase(
  caseId: string,
  accessToken: string
): Promise<{ action_execution_result: { success: boolean; message: string } }> {
  return authenticatedRequest(`/api/v1/recovery-cases/${caseId}/actions/close`, accessToken, {
    method: "POST",
  });
}

export function triggerRunAnalysis(
  caseId: string,
  accessToken: string
): Promise<{ decision: { action: string; reason: string; confidence: number } }> {
  return authenticatedRequest(`/api/v1/recovery-cases/${caseId}/analyze`, accessToken, {
    method: "POST",
  });
}

export type WebhookInstruction = {
  webhook_url: string;
  secret_configured: boolean;
  recommended_events: string[];
  instructions: string;
};

export type IntegrationStatus = {
  razorpay_account_id: string | null;
  razorpay_configured: boolean;
  razorpay_key_id_configured: boolean;
  razorpay_key_secret_configured: boolean;
  razorpay_webhook_secret_configured: boolean;
  smtp_configured: boolean;
  smtp_host: string | null;
  smtp_port: number | null;
  smtp_from_email: string | null;
  email_enabled: boolean;
  ai_agent_enabled: boolean;
  onboarding_complete: boolean;
};

export type RecoveryConfiguration = {
  max_recovery_attempts: number;
  default_payment_link_expiry_hours: number;
  default_wait_minutes: number;
  auto_notify_customer: boolean;
};

export type MerchantSettings = {
  id: string;
  name: string;
  email: string;
  business_name: string | null;
  support_email: string | null;
  support_phone: string | null;
  integrations: IntegrationStatus;
  webhook: WebhookInstruction;
  recovery: RecoveryConfiguration;
};

export type MerchantSettingsUpdate = {
  business_name?: string | null;
  support_email?: string | null;
  support_phone?: string | null;
  razorpay_account_id?: string | null;
  max_recovery_attempts?: number;
  default_payment_link_expiry_hours?: number;
  default_wait_minutes?: number;
  auto_notify_customer?: boolean;
};

export function getMerchantSettings(accessToken: string): Promise<MerchantSettings> {
  return authenticatedRequest<MerchantSettings>("/api/v1/merchant/settings", accessToken);
}

export function updateMerchantSettings(
  accessToken: string,
  payload: MerchantSettingsUpdate
): Promise<MerchantSettings> {
  return authenticatedRequest<MerchantSettings>("/api/v1/merchant/settings", accessToken, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}


