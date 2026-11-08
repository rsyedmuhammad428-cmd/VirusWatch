/**
 * Live streaming client for the VirusWatch AI research assistant.
 *
 * Consumes the backend's SSE endpoint (`POST /api/ai/ask/stream`). The
 * backend is the sole source of answers, citations and agent activity —
 * this module only transports what the backend emits; it never invents
 * content. Event contract (each SSE `data:` line is JSON):
 *
 *   { "type": "step", "step": { "id": "...", "kind": "web_search" | "run_command"
 *       | "retrieve_sources" | "analyze" | "write" | <string>,
 *       "label": "...", "detail": "...", "status": "running" | "done" | "failed" } }
 *   { "type": "delta", "text": "partial answer text" }
 *   { "type": "citations", "citations": [...], "limitations": [...] }
 *   { "type": "validated_analysis", "text": "..." }
 *   { "type": "done" }
 *   { "type": "error", "message": "..." }
 */

import { ApiError, getConfiguredBaseUrl } from "./client";
import { endpoints } from "./endpoints";
import { supabase } from "@/integrations/supabase/client";
import type { Citation } from "./types";

export type AgentStepStatus = "running" | "done" | "failed";

export interface AgentStep {
  id: string;
  /** What the agent did, e.g. "web_search", "run_command", "retrieve_sources". */
  kind: string;
  label?: string | null;
  detail?: string | null;
  status: AgentStepStatus;
}

export type StreamEvent =
  | { type: "step"; step: AgentStep }
  | { type: "delta"; text: string }
  | { type: "citations"; citations: Citation[]; limitations?: string[] | null }
  | { type: "validated_analysis"; text: string }
  | { type: "done" }
  | { type: "error"; message: string };

export interface AskStreamHandlers {
  onEvent: (event: StreamEvent) => void;
  signal?: AbortSignal | undefined;
}

function normalize(type: string, payload: Record<string, unknown>): StreamEvent | null {
  switch (type) {
    case "step": {
      const step = payload["step"] as Partial<AgentStep> | undefined;
      if (!step || typeof step.id !== "string" || typeof step.kind !== "string") return null;
      return {
        type: "step",
        step: {
          id: step.id,
          kind: step.kind,
          label: typeof step.label === "string" ? step.label : null,
          detail: typeof step.detail === "string" ? step.detail : null,
          status: step.status === "done" || step.status === "failed" ? step.status : "running",
        },
      };
    }
    case "delta":
      return typeof payload["text"] === "string" ? { type: "delta", text: payload["text"] } : null;
    case "citations": {
      if (!Array.isArray(payload["citations"])) return null;
      return {
        type: "citations",
        citations: payload["citations"] as Citation[],
        limitations: Array.isArray(payload["limitations"]) ? (payload["limitations"] as string[]) : null,
      };
    }
    case "validated_analysis":
      return typeof payload["text"] === "string" ? { type: "validated_analysis", text: payload["text"] } : null;
    case "done":
      return { type: "done" };
    case "error":
      return {
        type: "error",
        message: typeof payload["message"] === "string" ? payload["message"] : "The backend reported an error.",
      };
    default:
      return null;
  }
}

/**
 * Streams an answer from the backend. Resolves when the stream closes.
 * Throws before any event is received if the endpoint is missing or the
 * request fails, so callers can fall back to the non-streaming endpoint.
 */
export async function askAiStream(question: string, handlers: AskStreamHandlers): Promise<void> {
  const base = getConfiguredBaseUrl();
  if (!base) {
    throw new ApiError("No surveillance backend is configured. Set the API endpoint in Settings.");
  }

  const headers: Record<string, string> = { Accept: "text/event-stream" };
  try {
    const { data } = await supabase.auth.getSession();
    if (data.session?.access_token) headers["Authorization"] = `Bearer ${data.session.access_token}`;
  } catch {
    // The backend validates tokens itself; an unauthenticated ask is allowed to fail there.
  }

  const init: RequestInit = {
    method: "POST",
    headers,
    body: JSON.stringify({ question }),
  };
  if (handlers.signal !== undefined) init.signal = handlers.signal;
  let response: Response;
  try {
    response = await fetch(`${base}${endpoints.aiAskStream}`, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(`Could not reach the surveillance backend at ${base}`);
  }

  if (!response.ok || !response.body) {
    throw new ApiError(`Streaming request failed with status ${response.status}`, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (rawEvent: string) => {
    const lines = rawEvent.split("\n");
    const eventName = lines.find((l) => l.startsWith("event:"))?.slice(6).trim();
    const dataLines = lines.filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim());
    if (dataLines.length === 0) return;
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
    } catch {
      return; // Malformed frame — ignore rather than guess.
    }
    const type = typeof payload["type"] === "string" ? payload["type"] : eventName ?? "";
    const event = normalize(type, payload);
    if (event) handlers.onEvent(event);
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let separator = buffer.indexOf("\n\n");
    while (separator !== -1) {
      dispatch(buffer.slice(0, separator));
      buffer = buffer.slice(separator + 2);
      separator = buffer.indexOf("\n\n");
    }
  }
  // Flush a trailing frame that was not followed by a blank line.
  if (buffer.trim()) dispatch(buffer);
}
