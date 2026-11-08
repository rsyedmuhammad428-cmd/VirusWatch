/**
 * Centralized typed API client for the VirusWatch surveillance backend.
 *
 * - Base URL is configurable: defaults to VITE_API_BASE_URL and can be
 *   overridden at runtime (Settings page) via localStorage.
 * - Adds auth headers when a session token is available.
 * - Never fabricates data: failures surface as typed error results the UI
 *   renders as clear unavailable/error states.
 */

import { supabase } from "@/integrations/supabase/client";

const BASE_URL_STORAGE_KEY = "viruswatch.api_base_url";

export function getConfiguredBaseUrl(): string {
  try {
    const stored = window.localStorage.getItem(BASE_URL_STORAGE_KEY);
    if (stored && stored.trim()) return stored.trim().replace(/\/+$/, "");
  } catch {
    // localStorage unavailable (SSR/private mode) — fall through to env.
  }
  const envUrl = import.meta.env["VITE_API_BASE_URL"] as string | undefined;
  return (envUrl ?? "").trim().replace(/\/+$/, "");
}

export function setConfiguredBaseUrl(url: string) {
  const cleaned = url.trim().replace(/\/+$/, "");
  if (cleaned) {
    window.localStorage.setItem(BASE_URL_STORAGE_KEY, cleaned);
  } else {
    window.localStorage.removeItem(BASE_URL_STORAGE_KEY);
  }
  window.dispatchEvent(new Event("viruswatch:api-base-url-changed"));
}

export function isApiConfigured(): boolean {
  return getConfiguredBaseUrl().length > 0;
}

export class ApiError extends Error {
  status?: number | undefined;
  detail?: unknown;

  constructor(message: string, status?: number | undefined, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export interface ApiResult<T> {
  data: T;
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | undefined;
  body?: unknown;
  signal?: AbortSignal | undefined;
};

async function authHeader(): Promise<string | null> {
  // getSession() here is only for bearer attachment — the backend validates
  // the token itself.
  try {
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  } catch {
    return null;
  }
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const base = getConfiguredBaseUrl();
  if (!base) {
    throw new ApiError(
      "No surveillance backend is configured. Set the API endpoint in Settings.",
    );
  }

  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const token = await authHeader();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const init: RequestInit = {
    method: options.method ?? "GET",
    headers,
  };
  if (options.body !== undefined) init.body = JSON.stringify(options.body);
  if (options.signal !== undefined) init.signal = options.signal;

  let response: Response;
  try {
    response = await fetch(`${base}${path}`, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(`Could not reach the surveillance backend at ${base}`);
  }

  if (!response.ok) {
    let detail: unknown = null;
    let message = `Request failed with status ${response.status}`;
    try {
      const payload = await response.json();
      detail = payload;
      if (payload && typeof payload === "object" && "detail" in payload) {
        message = String((payload as { detail: unknown }).detail);
      }
    } catch {
      // Non-JSON error body — keep the generic message.
    }
    throw new ApiError(message, response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Lightweight health/availability probe. Returns true when reachable. */
export async function checkApiHealth(signal?: AbortSignal): Promise<boolean> {
  const base = getConfiguredBaseUrl();
  if (!base) return false;
  const timeout = new AbortController();
  const timer = window.setTimeout(() => timeout.abort(), 6000);
  const combined = signal
    ? AbortSignal.any([signal, timeout.signal])
    : timeout.signal;
  try {
    const response = await fetch(`${base}/health`, {
      headers: { Accept: "application/json" },
      signal: combined,
    });
    return response.ok;
  } catch {
    return false;
  } finally {
    window.clearTimeout(timer);
  }
}
