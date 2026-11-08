import { useEffect, useState } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import {
  checkApiHealth,
  getConfiguredBaseUrl,
  isApiConfigured,
  setConfiguredBaseUrl,
} from "./client";

export type BackendStatus = "unconfigured" | "unreachable" | "ready";

const BASE_URL_QUERY_KEY = ["api-base-url"] as const;

/**
 * Tracks the configured backend endpoint and its reachability. Re-checks
 * whenever the endpoint changes in Settings.
 */
export function useBackendStatus(): {
  baseUrl: string;
  configured: boolean;
  status: BackendStatus | "checking";
  recheck: () => void;
  setBaseUrl: (url: string) => void;
} {
  const [baseUrl, setBaseUrlState] = useState(() => getConfiguredBaseUrl());
  const [nonce, setNonce] = useState(0);

  // VITE_API_BASE_URL is build-time config; getConfiguredBaseUrl reads
  // localStorage on the client. Avoid hydration mismatches by reading once
  // mounted.
  useEffect(() => {
    setBaseUrlState(getConfiguredBaseUrl());
    const onChange = () => setBaseUrlState(getConfiguredBaseUrl());
    window.addEventListener("viruswatch:api-base-url-changed", onChange);
    return () => window.removeEventListener("viruswatch:api-base-url-changed", onChange);
  }, []);

  const health = useQuery({
    queryKey: ["api-health", baseUrl, nonce],
    enabled: !!baseUrl,
    staleTime: 60_000,
    retry: false,
    queryFn: ({ signal }) => checkApiHealth(signal),
  });

  const configured = isApiConfigured() && baseUrl.length > 0;
  const status: BackendStatus | "checking" = !configured
    ? "unconfigured"
    : health.isPending
      ? "checking"
      : health.data
        ? "ready"
        : "unreachable";

  return {
    baseUrl,
    configured,
    status,
    recheck: () => {
      setNonce((n) => n + 1);
    },
    setBaseUrl: (url: string) => {
      setConfiguredBaseUrl(url);
      setBaseUrlState(url);
      setNonce((n) => n + 1);
    },
  };
}

/**
 * Standard query wrapper for backend data. Queries stay disabled until the
 * backend is configured and reachable, so pages can render a clear
 * configuration state instead of a storm of failed requests.
 */
export function useBackendQuery<T>(
  key: readonly unknown[],
  fetcher: (signal: AbortSignal) => Promise<T>,
  status: BackendStatus | "checking",
): UseQueryResult<T, Error> {
  return useQuery({
    queryKey: [...BASE_URL_QUERY_KEY, ...key],
    enabled: status === "ready",
    staleTime: 30_000,
    retry: 1,
    queryFn: ({ signal }) => fetcher(signal),
  });
}
