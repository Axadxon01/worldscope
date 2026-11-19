"use client";

import { RotateCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { describe, type StatusResult, type Tone } from "@/lib/system-status";

const POLL_MS = 30_000;

const toneDot: Record<Tone, string> = {
  good: "bg-status-good",
  warn: "bg-status-warn",
  critical: "bg-status-critical",
  neutral: "bg-muted-foreground/60",
};

function timeUtc(date: Date): string {
  return date.toISOString().slice(11, 19);
}

/**
 * Real system state from the API, checked on load, every 30 seconds while the tab is
 * visible, and on request. Nothing here is decorative: every line comes from a check.
 */
export function StatusPanel({
  className,
  detailed = false,
}: {
  className?: string;
  /** Larger text and a fix-it hint under any line that needs attention. */
  detailed?: boolean;
}) {
  const [result, setResult] = useState<StatusResult | null>(null);
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);
  const [checking, setChecking] = useState(false);

  const check = useCallback(async () => {
    setChecking(true);
    try {
      const response = await fetch("/api/system-status", { cache: "no-store" });
      setResult((await response.json()) as StatusResult);
    } catch {
      setResult({ reachable: false, reason: "Web server not reachable" });
    } finally {
      setCheckedAt(new Date());
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    // Initial check on mount; the effect only schedules work, it renders nothing itself.
    const first = setTimeout(check, 0);
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void check();
    }, POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [check]);

  const lines = result ? describe(result) : [];

  return (
    <section
      aria-labelledby={detailed ? undefined : "system-status-heading"}
      aria-label={detailed ? "Platform status" : undefined}
      className={cn(detailed ? "text-ui" : "text-meta", className)}
    >
      <div className="flex items-center justify-between">
        {!detailed && (
          <h2 id="system-status-heading" className="font-medium text-muted-foreground">
            Platform status
          </h2>
        )}
        <button
          type="button"
          onClick={() => void check()}
          disabled={checking}
          className="ml-auto inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-60"
        >
          <RotateCw aria-hidden className={cn("size-3", checking && "animate-spin")} />
          {checking ? "Checking" : "Check now"}
        </button>
      </div>

      <dl className="mt-2 space-y-1.5" aria-live="polite">
        {result === null ? (
          <div className="text-muted-foreground">Checking the API…</div>
        ) : (
          lines.map((line) => (
            <div key={line.name} className="grid grid-cols-[1fr_auto] items-baseline gap-x-3">
              <dt className="flex items-center gap-2 text-muted-foreground">
                <span
                  aria-hidden
                  className={cn("size-1.5 shrink-0 rounded-full", toneDot[line.tone])}
                />
                {line.name}
              </dt>
              <dd className="text-right tabular-nums text-foreground">{line.value}</dd>
              {detailed && line.hint && (
                <dd className="col-span-2 mt-0.5 mb-1.5 pl-3.5 text-meta text-muted-foreground">
                  {line.hint}
                </dd>
              )}
            </div>
          ))
        )}
      </dl>

      {checkedAt && (
        <p className="mt-2 tabular-nums text-muted-foreground/80">
          Checked at {timeUtc(checkedAt)} UTC
        </p>
      )}
    </section>
  );
}
