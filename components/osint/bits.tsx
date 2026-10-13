"use client"

import { ApiError, type EntitySummary, type Evidence, topicsOf } from "@/lib/api"
import { cn } from "@/lib/utils"

export function Panel({ title, actions, children, className }: {
  title?: string
  actions?: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  return (
    <section className={cn("rounded-md border border-border bg-card", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
          {title && <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{title}</h2>}
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

const BASIS_STYLE: Record<string, string> = {
  documented: "border-emerald-500/40 text-emerald-400",
  reported: "border-amber-500/40 text-amber-400",
  inferred: "border-sky-500/40 text-sky-400",
}

export function Badge({ children, tone }: { children: React.ReactNode; tone?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase leading-none",
        (tone && BASIS_STYLE[tone]) || "border-border text-muted-foreground",
      )}
    >
      {children}
    </span>
  )
}

export function EntityChip({ entity, onOpen }: { entity?: EntitySummary | null; onOpen?: (id: string) => void }) {
  if (!entity) return <span className="text-muted-foreground">unknown</span>
  const sanctioned = entity.sanctioned || topicsOf(entity).some((t) => t.startsWith("sanction"))
  return (
    <button
      type="button"
      onClick={() => onOpen?.(entity.id)}
      className="inline-flex items-center gap-1.5 text-left text-foreground hover:text-primary hover:underline"
    >
      <span className="font-medium">{entity.name}</span>
      {sanctioned && <Badge tone="reported">sanction topic</Badge>}
      <span className="font-mono text-[10px] text-muted-foreground">{entity.country?.toUpperCase() ?? ""}</span>
    </button>
  )
}

export function EvidenceList({ items }: { items: Evidence[] }) {
  if (!items.length) return <p className="text-sm text-muted-foreground">No evidence rows.</p>
  return (
    <ul className="flex flex-col gap-2">
      {items.map((ev, i) => (
        <li key={ev.key ?? ev.id ?? i} className="rounded border border-border/60 bg-background/40 p-2.5 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            {ev.eid && <span className="font-mono text-xs text-primary">[{ev.eid}]</span>}
            <Badge>{ev.source_id}</Badge>
            {ev.basis && <Badge tone={ev.basis}>{ev.basis}</Badge>}
            <span className="font-medium">{ev.title}</span>
          </div>
          {ev.excerpt && <p className="mt-1 text-muted-foreground">{ev.excerpt}</p>}
          {ev.url && (
            <a href={ev.url} target="_blank" rel="noreferrer" className="mt-1 block truncate font-mono text-xs text-primary hover:underline">
              {ev.url}
            </a>
          )}
        </li>
      ))}
    </ul>
  )
}

export function StateMessage({ error, loading, empty }: { error?: unknown; loading?: boolean; empty?: string }) {
  if (error) {
    const offline = !(error instanceof ApiError) || error.status >= 500
    return (
      <div role="alert" className="rounded border border-destructive/40 bg-destructive/10 p-3 text-sm">
        {offline ? (
          <>
            <p className="font-medium">Backend not reachable.</p>
            <p className="mt-1 text-muted-foreground">
              Start it locally: <code className="font-mono">cd backend && .venv/bin/uvicorn app.api:app --port 8000</code>
            </p>
          </>
        ) : (
          <p>{(error as Error).message}</p>
        )}
      </div>
    )
  }
  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>
  if (empty) return <p className="text-sm text-muted-foreground">{empty}</p>
  return null
}

export function AiBlock({ ai, status }: { ai: import("@/lib/api").AiAnswer; status?: import("@/lib/api").AiStatus }) {
  if (!status) return null
  if (!status.used || !ai) {
    return (
      <p className="text-xs text-muted-foreground">
        AI not used ({status.reason}){status.message ? `: ${status.message}` : ""}. Deterministic results are shown.
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex flex-wrap gap-2">
        <Badge>{status.cached ? "cached" : "fresh"}</Badge>
        <Badge>{`citations ok ${ai.citation_check.accepted} / rejected ${ai.citation_check.rejected}`}</Badge>
      </div>
      {ai.summary && <p className="leading-relaxed">{ai.summary}</p>}
      {ai.claims.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {ai.claims.map((c, i) => (
            <li key={i} className="flex gap-2">
              <Badge tone={c.basis}>{c.basis}</Badge>
              <span>
                {c.text} <span className="font-mono text-xs text-primary">{c.citations.join(", ")}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {ai.uncertainties.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase text-muted-foreground">Uncertainties</h4>
          <ul className="list-disc pl-5">{ai.uncertainties.map((u, i) => <li key={i}>{u}</li>)}</ul>
        </div>
      )}
      {ai.next_steps.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold uppercase text-muted-foreground">Next checks</h4>
          <ul className="list-disc pl-5">{ai.next_steps.map((u, i) => <li key={i}>{u}</li>)}</ul>
        </div>
      )}
      <p className="text-xs text-muted-foreground">{ai.disclaimer}</p>
    </div>
  )
}
