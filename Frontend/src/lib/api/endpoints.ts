/**
 * Documented endpoint contract for the VirusWatch surveillance backend.
 *
 * The backend (FastAPI) is the source of truth for surveillance records,
 * statistical results, AI answers, and notifications. These are the paths
 * the frontend expects; if an endpoint is not implemented yet the UI shows
 * a controlled "endpoint unavailable" state — it never substitutes mock data.
 */

import { apiRequest } from "./client";
import type {
  ActivityEvent,
  AiAnswer,
  Anomaly,
  DataFreshness,
  DataSource,
  Disease,
  Geography,
  NotificationItem,
  Observation,
  Paginated,
  Report,
  SummaryMetrics,
  TimeSeriesPoint,
} from "./types";

export interface ObservationQuery {
  disease?: string | undefined;
  geography?: string | undefined;
  indicator?: string | undefined;
  source?: string | undefined;
  quality?: string | undefined;
  period_from?: string | undefined;
  period_to?: string | undefined;
  q?: string | undefined;
  page?: number | undefined;
  page_size?: number | undefined;
  sort?: string | undefined;
  order?: "asc" | "desc" | undefined;
}

export const endpoints = {
  summary: "/api/summary",
  freshness: "/api/freshness",
  activity: "/api/activity",
  diseases: "/api/diseases",
  diseaseDetail: (slug: string) => `/api/diseases/${encodeURIComponent(slug)}`,
  diseaseSeries: (slug: string) => `/api/diseases/${encodeURIComponent(slug)}/series`,
  geographies: "/api/geographies",
  observations: "/api/observations",
  observationExport: "/api/observations/export",
  anomalies: "/api/anomalies",
  reports: "/api/reports",
  notifications: "/api/notifications",
  notificationPreferences: "/api/notifications/preferences",
  sources: "/api/sources",
  aiAsk: "/api/ai/ask",
  /**
   * Server-sent events (SSE) version of aiAsk for live streaming. The
   * backend streams JSON events of type: step (tool activity such as
   * web_search / run_command / retrieve_sources), delta (answer text
   * tokens), citations, validated_analysis, done, error.
   */
  aiAskStream: "/api/ai/ask/stream",
} as const;

export const api = {
  getSummary: (signal?: AbortSignal) => apiRequest<SummaryMetrics>(endpoints.summary, { signal }),
  getFreshness: (signal?: AbortSignal) => apiRequest<DataFreshness>(endpoints.freshness, { signal }),
  getActivity: (signal?: AbortSignal) => apiRequest<ActivityEvent[]>(endpoints.activity, { signal }),

  getDiseases: (signal?: AbortSignal) => apiRequest<Disease[]>(endpoints.diseases, { signal }),
  getDisease: (slug: string, signal?: AbortSignal) =>
    apiRequest<Disease>(endpoints.diseaseDetail(slug), { signal }),
  getDiseaseSeries: (slug: string, signal?: AbortSignal) =>
    apiRequest<TimeSeriesPoint[]>(endpoints.diseaseSeries(slug), { signal }),

  getGeographies: (signal?: AbortSignal) => apiRequest<Geography[]>(endpoints.geographies, { signal }),

  getObservations: (query: ObservationQuery, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== "" && value !== null) {
        params.set(key, String(value));
      }
    });
    const qs = params.toString();
    return apiRequest<Paginated<Observation>>(
      `${endpoints.observations}${qs ? `?${qs}` : ""}`,
      { signal },
    );
  },

  getAnomalies: (signal?: AbortSignal) => apiRequest<Anomaly[]>(endpoints.anomalies, { signal }),

  getReports: (signal?: AbortSignal) => apiRequest<Report[]>(endpoints.reports, { signal }),

  getNotifications: (signal?: AbortSignal) =>
    apiRequest<NotificationItem[]>(endpoints.notifications, { signal }),
  getNotificationPreferences: (signal?: AbortSignal) =>
    apiRequest<Record<string, unknown>>(endpoints.notificationPreferences, { signal }),
  saveNotificationPreferences: (preferences: Record<string, unknown>, signal?: AbortSignal) =>
    apiRequest<Record<string, unknown>>(endpoints.notificationPreferences, {
      method: "PUT",
      body: preferences,
      signal,
    }),

  getSources: (signal?: AbortSignal) => apiRequest<DataSource[]>(endpoints.sources, { signal }),

  askAi: (question: string, signal?: AbortSignal) =>
    apiRequest<AiAnswer>(endpoints.aiAsk, { method: "POST", body: { question }, signal }),
};
