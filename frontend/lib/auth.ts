const ACCESS_TOKEN_KEY = "recoverai.access-token";

/**
 * The API currently returns a bearer token rather than an HttpOnly session
 * cookie. sessionStorage keeps it out of URLs and limits its lifetime to the
 * current browser tab/session.
 */
export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(ACCESS_TOKEN_KEY);
}

export function storeAccessToken(accessToken: string): void {
  window.sessionStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
}

export function clearAccessToken(): void {
  if (typeof window !== "undefined") {
    window.sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  }
}
