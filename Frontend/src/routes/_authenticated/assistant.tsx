import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  BarChart3,
  Bot,
  Check,
  Copy,
  Database,
  ExternalLink,
  Loader2,
  PenLine,
  Search,
  Send,
  Square,
  Terminal,
  ThumbsDown,
  ThumbsUp,
  UserRound,
  Wrench,
  X,
} from "lucide-react";

import { AppLayout } from "@/components/app-layout";
import { BackendStateCard } from "@/components/data-states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { useBackendQuery, useBackendStatus } from "@/lib/api/hooks";
import { api } from "@/lib/api/endpoints";
import { askAiStream, type AgentStep } from "@/lib/api/streaming";
import { formatDate } from "@/lib/format";
import type { Citation } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_authenticated/assistant")({
  head: () => ({
    meta: [
      { title: "AI Research Assistant — VirusWatch" },
      { name: "description", content: "Evidence-based answers with live-streamed agent activity and expandable citations to original surveillance sources." },
      { property: "og:title", content: "AI Research Assistant — VirusWatch" },
      { property: "og:description", content: "Evidence-based answers with live-streamed agent activity and expandable citations to original surveillance sources." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: AssistantPage,
});

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  limitations?: string[];
  validatedAnalysis?: string | null;
  error?: boolean;
  /** True while answer text is still streaming in from the backend. */
  streaming?: boolean;
  /** Live agent activity reported by the backend (web search, run command, …). */
  steps?: AgentStep[];
}

const SUGGESTED_QUESTIONS = [
  "What does the latest available influenza surveillance report indicate?",
  "How has the most recent indicator changed over the available reporting periods?",
  "What official guidance applies to this disease?",
  "How do the available Pakistan and US datasets differ?",
  "Which sources support this explanation?",
  "What limitations should be considered before interpreting this trend?",
];

/** Icon shown next to each agent activity step reported by the backend. */
function stepIcon(kind: string) {
  const k = kind.toLowerCase();
  if (k.includes("web") || k.includes("search")) return Search;
  if (k.includes("command") || k.includes("run") || k.includes("code") || k.includes("script")) return Terminal;
  if (k.includes("source") || k.includes("retriev") || k.includes("query") || k.includes("fetch")) return Database;
  if (k.includes("analy") || k.includes("comput") || k.includes("stat")) return BarChart3;
  if (k.includes("writ") || k.includes("draft") || k.includes("answer")) return PenLine;
  return Wrench;
}

function AgentActivity({ steps }: { steps: AgentStep[] }) {
  if (steps.length === 0) return null;
  return (
    <div className="space-y-1.5">
      {steps.map((step) => {
        const Icon = stepIcon(step.kind);
        const label = step.label || step.kind.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
        return (
          <div
            key={step.id}
            className={cn(
              "flex items-start gap-2.5 rounded-lg border border-border bg-background/70 px-3 py-2 text-xs",
              step.status === "failed" && "border-destructive/40",
            )}
          >
            <span
              className={cn(
                "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary",
                step.status === "failed" && "bg-destructive/10 text-destructive",
              )}
            >
              {step.status === "running" ? (
                <Loader2 className="size-3 animate-spin" />
              ) : step.status === "failed" ? (
                <X className="size-3" />
              ) : (
                <Check className="size-3" />
              )}
            </span>
            <span className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-muted-foreground">
              <Icon className="size-3.5 shrink-0" />
              <span className="font-medium text-foreground">{label}</span>
              {step.detail ? <span className="min-w-0 break-all">{step.detail}</span> : null}
            </span>
            {step.status === "running" ? (
              <span className="ml-auto shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground/70">
                working…
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function AssistantPage() {
  const { status, baseUrl, recheck } = useBackendStatus();
  const ready = status === "ready";

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  function stop() {
    abortRef.current?.abort();
  }

  async function ask(question: string) {
    const trimmed = question.trim();
    if (!trimmed || busy) return;
    setInput("");
    setBusy(true);
    const userMsg: ChatMessage = { id: `u-${Date.now()}`, role: "user", content: trimmed };
    const assistantId = `a-${Date.now()}`;
    const placeholder: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      streaming: true,
      steps: [],
    };
    setMessages((prev) => [...prev, userMsg, placeholder]);

    const controller = new AbortController();
    abortRef.current = controller;

    const update = (fn: (m: ChatMessage) => ChatMessage) =>
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? fn(m) : m)));

    const upsertStep = (m: ChatMessage, step: AgentStep): ChatMessage => {
      const steps = m.steps ?? [];
      const index = steps.findIndex((s) => s.id === step.id);
      const next = index === -1 ? [...steps, step] : steps.map((s) => (s.id === step.id ? step : s));
      return { ...m, steps: next };
    };

    let receivedEvent = false;
    let backendError: string | null = null;

    try {
      await askAiStream(trimmed, {
        signal: controller.signal,
        onEvent: (event) => {
          receivedEvent = true;
          switch (event.type) {
            case "step":
              update((m) => upsertStep(m, event.step));
              break;
            case "delta":
              update((m) => ({ ...m, content: m.content + event.text }));
              break;
            case "citations":
              update((m) => ({
                ...m,
                citations: event.citations,
                limitations: event.limitations ?? m.limitations ?? [],
              }));
              break;
            case "validated_analysis":
              update((m) => ({ ...m, validatedAnalysis: event.text }));
              break;
            case "error":
              backendError = event.message;
              break;
            case "done":
              break;
          }
        },
      });

      update((m) => {
        const emptyError = !m.content && !m.citations?.length;
        return {
          ...m,
          streaming: false,
          error: emptyError || backendError !== null,
          content:
            backendError !== null
              ? `The assistant could not answer: ${backendError}`
              : m.content || "The backend returned an empty answer.",
        };
      });
    } catch (streamError) {
      if (controller.signal.aborted) {
        update((m) => ({ ...m, streaming: false }));
        toast.info("Response stopped");
      } else if (!receivedEvent) {
        // The backend does not offer the streaming endpoint (or it failed
        // before any event) — fall back to the plain request/response ask.
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
        try {
          const answer = await api.askAi(trimmed);
          setMessages((prev) => [
            ...prev,
            {
              id: assistantId,
              role: "assistant",
              content: answer.answer,
              citations: answer.evidence?.citations ?? [],
              limitations: answer.evidence?.limitations ?? [],
              validatedAnalysis: answer.validated_analysis ?? null,
            },
          ]);
        } catch (error) {
          setMessages((prev) => [
            ...prev,
            {
              id: assistantId,
              role: "assistant",
              content:
                error instanceof Error
                  ? `The assistant could not answer: ${error.message}`
                  : "The assistant could not answer this question.",
              error: true,
            },
          ]);
        }
      } else {
        const reason =
          streamError instanceof Error ? streamError.message : "the connection was interrupted";
        update((m) => ({
          ...m,
          streaming: false,
          error: !m.content,
          content: m.content || `The assistant could not answer: ${reason}`,
        }));
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  return (
    <AppLayout>
      <div className="space-y-5">
        <BackendStateCard status={status} baseUrl={baseUrl} onRetry={recheck} />

        <Card className="flex h-[calc(100vh-13rem)] min-h-[520px] flex-col">
          {/* Conversation */}
          <div ref={scrollRef} className="flex-1 space-y-5 overflow-y-auto p-5">
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
                <span className="flex size-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
                  <Bot className="size-6" />
                </span>
                <div className="max-w-md space-y-1.5">
                  <h2 className="font-display text-lg font-semibold text-foreground">
                    Ask the research assistant
                  </h2>
                  <p className="text-sm text-muted-foreground">
                    Answers stream in live from the surveillance backend, which reports each
                    step it takes — web searches, source retrieval, command runs — with
                    citations you can open. The assistant does not perform a live web search
                    beyond what the backend provides.
                  </p>
                </div>
                {ready ? (
                  <div className="flex max-w-xl flex-wrap justify-center gap-2">
                    {SUGGESTED_QUESTIONS.map((q) => (
                      <button
                        key={q}
                        type="button"
                        onClick={() => ask(q)}
                        className="rounded-full border border-border bg-background px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : (
              messages.map((msg) =>
                msg.role === "user" ? (
                  <div key={msg.id} className="flex justify-end gap-3">
                    <div className="max-w-[75%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm text-primary-foreground">
                      {msg.content}
                    </div>
                    <span className="mt-1 flex size-7 shrink-0 items-center justify-center rounded-full bg-secondary text-secondary-foreground">
                      <UserRound className="size-4" />
                    </span>
                  </div>
                ) : (
                  <AssistantMessage key={msg.id} message={msg} />
                ),
              )
            )}
          </div>

          {/* Composer */}
          <div className="border-t border-border p-3">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                ask(input);
              }}
              className="flex gap-2"
            >
              <Input
                placeholder={ready ? "Ask about surveillance data, guidance or limitations…" : "Connect the surveillance backend first"}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={!ready || busy}
              />
              {busy ? (
                <Button
                  type="button"
                  size="icon"
                  variant="outline"
                  onClick={stop}
                  aria-label="Stop response"
                  title="Stop the live response"
                >
                  <Square className="size-4" />
                </Button>
              ) : (
                <Button type="submit" size="icon" disabled={!ready || !input.trim()} aria-label="Send question">
                  <Send />
                </Button>
              )}
            </form>
          </div>
        </Card>
      </div>
    </AppLayout>
  );
}

function AssistantMessage({ message }: { message: ChatMessage }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    toast.success("Answer copied");
    setTimeout(() => setCopied(false), 2000);
  }

  if (message.error) {
    return (
      <div className="flex gap-3">
        <span className="mt-1 flex size-7 shrink-0 items-center justify-center rounded-full bg-destructive/15 text-destructive">
          <Bot className="size-4" />
        </span>
        <p className="max-w-[75%] rounded-2xl rounded-bl-md border border-destructive/30 bg-destructive/5 px-4 py-2.5 text-sm text-foreground">
          {message.content}
        </p>
      </div>
    );
  }

  const streamingText = message.streaming && !message.content;

  return (
    <div className="flex gap-3">
      <span className="mt-1 flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary">
        <Bot className="size-4" />
      </span>
      <div className="max-w-[85%] space-y-3">
        {/* Live agent activity streamed from the backend */}
        {message.steps && message.steps.length > 0 ? (
          <AgentActivity steps={message.steps} />
        ) : null}

        {streamingText && (!message.steps || message.steps.length === 0) ? (
          <div className="flex items-center gap-2 rounded-2xl rounded-bl-md border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin text-primary" />
            Consulting surveillance data…
          </div>
        ) : message.content ? (
          <div className="rounded-2xl rounded-bl-md border border-border bg-card px-4 py-3 text-sm leading-relaxed text-foreground">
            {message.content}
            {message.streaming ? (
              <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse bg-primary align-middle" />
            ) : null}
            {message.validatedAnalysis ? (
              <div className="mt-3 rounded-lg border-l-2 border-primary bg-muted/50 px-3 py-2">
                <p className="text-xs font-semibold uppercase tracking-wide text-primary">
                  Backend-validated analysis
                </p>
                <p className="mt-1 text-sm text-muted-foreground">{message.validatedAnalysis}</p>
              </div>
            ) : null}
          </div>
        ) : null}

        {message.citations && message.citations.length > 0 ? (
          <Collapsible>
            <CollapsibleTrigger className="group inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground">
              <ExternalLink className="size-3.5" />
              {message.citations.length} source{message.citations.length > 1 ? "s" : ""}
              <span className="transition-transform group-data-[state=open]:rotate-180">▾</span>
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2 space-y-2">
              {message.citations.map((c, i) => (
                <div key={i} className="rounded-lg border border-border bg-background p-3 text-sm">
                  <p className="font-medium text-foreground">
                    {c.url ? (
                      <a href={c.url} target="_blank" rel="noreferrer noopener" className="text-primary underline-offset-4 hover:underline">
                        {c.title}
                      </a>
                    ) : (
                      c.title
                    )}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {[c.organization, formatDate(c.publication_date), c.reporting_period, c.page ? `p. ${c.page}` : null, c.data_freshness]
                      .filter(Boolean)
                      .join(" · ") || "Source details pending"}
                  </p>
                  {c.limitations ? (
                    <p className="mt-1 text-xs text-muted-foreground">Limitations: {c.limitations}</p>
                  ) : null}
                </div>
              ))}
            </CollapsibleContent>
          </Collapsible>
        ) : null}

        {message.limitations && message.limitations.length > 0 ? (
          <div className="space-y-1">
            {message.limitations.map((limitation, i) => (
              <Badge key={i} variant="outline" className="border-warning/40 bg-warning/10 font-normal text-warning-foreground">
                {limitation}
              </Badge>
            ))}
          </div>
        ) : null}

        {!message.streaming ? (
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" className="size-7 text-muted-foreground" onClick={copy} aria-label="Copy answer">
              {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-7 text-muted-foreground"
              disabled
              aria-label="Helpful"
              title="Feedback becomes available when the backend supports it"
            >
              <ThumbsUp className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-7 text-muted-foreground"
              disabled
              aria-label="Not helpful"
              title="Feedback becomes available when the backend supports it"
            >
              <ThumbsDown className="size-3.5" />
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
