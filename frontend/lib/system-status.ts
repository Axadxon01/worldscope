/** Mirrors `SystemStatus` from the backend (worldscope/services/system_status.py). */

export type ComponentState = "ok" | "down" | "not_configured";

export interface ComponentStatus {
  status: ComponentState;
  detail: string | null;
}

export interface WorkerStatus {
  status: "ok" | "stale" | "unknown";
  last_heartbeat_at: string | null;
  age_seconds: number | null;
}

export interface SystemStatus {
  status: "ok" | "degraded" | "down";
  version: string;
  environment: string;
  checked_at: string;
  database: ComponentStatus;
  redis: ComponentStatus;
  workers: WorkerStatus;
  llm: ComponentStatus;
}

export type StatusResult =
  | { reachable: true; status: SystemStatus }
  | { reachable: false; reason: string };

export type Tone = "good" | "warn" | "critical" | "neutral";

export interface StatusLine {
  name: string;
  value: string;
  tone: Tone;
  /** What to do about it, shown in the detailed view. */
  hint?: string;
}

function age(seconds: number): string {
  if (seconds < 90) return `${seconds} s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes} min ago`;
  return `${Math.round(minutes / 60)} h ago`;
}

/** Turns the API's status into the plain-language lines the status panel shows. */
export function describe(result: StatusResult): StatusLine[] {
  if (!result.reachable) {
    return [
      {
        name: "API",
        value: result.reason,
        tone: "critical",
        hint: "Start the API (make dev), or check API_INTERNAL_URL for the web app.",
      },
    ];
  }
  const s = result.status;
  const hints: Record<string, string> = {
    Database: "Check that PostgreSQL is running and DATABASE_URL is correct.",
    Queue: "Check that Redis is running and REDIS_URL is correct.",
  };
  const component = (name: string, c: ComponentStatus): StatusLine =>
    c.status === "ok"
      ? { name, value: "Connected", tone: "good" }
      : { name, value: "Not reachable", tone: "critical", hint: hints[name] };

  const workers: StatusLine =
    s.workers.status === "ok"
      ? { name: "Workers", value: `Last seen ${age(s.workers.age_seconds ?? 0)}`, tone: "good" }
      : s.workers.status === "stale"
        ? {
            name: "Workers",
            value: `Silent since ${age(s.workers.age_seconds ?? 0)}`,
            tone: "warn",
            hint: "A worker or the scheduler stopped. Check their logs: make logs.",
          }
        : {
            name: "Workers",
            value: "No heartbeat yet",
            tone: "warn",
            hint: "Start a worker and the scheduler (make dev starts both). The first heartbeat arrives within a minute.",
          };

  const llm: StatusLine =
    s.llm.status === "ok"
      ? {
          name: "AI provider",
          value: s.llm.detail === "anthropic" ? "Claude" : (s.llm.detail ?? "Ready"),
          tone: "good",
        }
      : {
          name: "AI provider",
          value: "API key not set",
          tone: "neutral",
          hint: "AI features stay off until ANTHROPIC_API_KEY is set in .env and the API restarts.",
        };

  return [
    { name: "API", value: `Version ${s.version}`, tone: "good" },
    component("Database", s.database),
    component("Queue", s.redis),
    workers,
    llm,
  ];
}
