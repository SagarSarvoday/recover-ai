"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { getCurrentMerchant, login as loginRequest, Merchant } from "../lib/api";
import { clearAccessToken, getAccessToken, storeAccessToken } from "../lib/auth";

type AuthContextValue = {
  merchant: Merchant | null;
  accessToken: string | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  refreshMerchant: () => Promise<Merchant>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  const [merchant, setMerchant] = useState<Merchant | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const logout = useCallback(() => {
    clearAccessToken();
    setAccessToken(null);
    setMerchant(null);
  }, []);

  useEffect(() => {
    const token = getAccessToken();
    if (!token) {
      setIsLoading(false);
      return;
    }

    setAccessToken(token);
    void getCurrentMerchant(token)
      .then(setMerchant)
      .catch(logout)
      .finally(() => setIsLoading(false));
  }, [logout]);

  const login = useCallback(async (email: string, password: string) => {
    const { access_token } = await loginRequest(email, password);
    storeAccessToken(access_token);
    setAccessToken(access_token);
    try {
      setMerchant(await getCurrentMerchant(access_token));
    } catch (error) {
      logout();
      throw error;
    }
  }, [logout]);

  const refreshMerchant = useCallback(async () => {
    if (!accessToken) throw new Error("No active merchant session.");
    try {
      const refreshedMerchant = await getCurrentMerchant(accessToken);
      setMerchant(refreshedMerchant);
      return refreshedMerchant;
    } catch (error) {
      logout();
      throw error;
    }
  }, [accessToken, logout]);

  const value = useMemo(() => ({ merchant, accessToken, isLoading, login, refreshMerchant, logout }), [merchant, accessToken, isLoading, login, refreshMerchant, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider.");
  return context;
}
