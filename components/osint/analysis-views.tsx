"use client"

import { useState } from "react"
import useSWR from "swr"

import { Button } from "@/components/ui/button"
import { type AiAnswer, type AiStatus, apiPost, type EntitySummary, type Evidence, fetcher } from "@/lib/api"

import { AiBlock, Badge, EntityChip, EvidenceList, Panel, StateMessage } from "./bits"
import { SearchBox } from "./search-box"

type PathResult = {
  a: EntitySummary; b: EntitySummary
  paths: { cost: number; hops: number; nodes: EntitySummary[]; edges: { id: number; label: string; basis: string }[]; via_hubs: string[] }[]
  deterministic: { summary: string; caveat: string }
  evidence: Evidence[]; ai: AiAnswer; ai_status: AiStatus
}

export function PathsView({ onOpen, onEdge }: { onOpen: (id: string) => void; onEdge: (id: number) => void }) {
  const [a, setA] = useState<EntitySummary | null>(null)
  const [b, setB] = useState<EntitySummary | null>(null)
  const [query, setQuery] = useState<string | null>(null)
  const { data, error, isLoading } = useSWR<PathResult>(query, fetcher, { revalidateOnFocus: false })
  const run = (ai: boolean) => a && b && setQuery(`/api/paths?a=${encodeURIComponent(a.id)}&b=${encodeURIComponent(b.id)}&ai=${ai}`)

  return (
    <div className="flex flex-col gap-4">
      <Panel title="How are these two entities connected?">
        <div className="grid gap-3 md:grid-cols-2">
          <div><p className="mb-1 text-xs text-muted-foreground">Entity A: {a?.name ?? "not selected"}</p><SearchBox onPick={setA} label="Entity A" /></div>
          <div><p className="mb-1 text-xs text-muted-foreground">Entity B: {b?.name ?? "not selected"}</p><SearchBox onPick={setB} label="Entity B" /></div>
        </div>
        <div className="mt-3 flex gap-2">
          <Button size="sm" disabled={!a || !b} onClick={() => run(false)}>Find paths</Button>
          <Button size="sm" variant="outline" disabled={!a || !b} onClick={() => run(true)}>Find + AI explanation (1 request)</Button>
        </div>
      </Panel>
      {query && !data && <StateMessage error={error} loading={isLoading} />}
      {data && (
        <>
          <Panel title="Paths (cheapest first; hubs penalised)">
            <p className="mb-3 text-sm">{data.deterministic.summary}</p>
            <ol className="flex flex-col gap-3">
              {data.paths.map((p, i) => (
                <li key={i} className="rounded border border-border/60 p-3 text-sm">
                  <div className="mb-2 flex gap-2"><Badge>{`#${i + 1}`}</Badge><Badge>{`${p.hops} hops`}</Badge><Badge>{`cost ${p.cost}`}</Badge>{p.via_hubs.length > 0 && <Badge tone="reported">via hub</Badge>}</div>
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    {p.nodes.map((n, j) => (
                      <span key={n.id} className="flex items-center gap-2">
                        <EntityChip entity={n} onOpen={onOpen} />
                        {p.edges[j] && (
                          <button type="button" onClick={() => onEdge(p.edges[j].id)} className="text-xs text-muted-foreground hover:text-primary">
                            {"→ "}{p.edges[j].label} <Badge tone={p.edges[j].basis}>{p.edges[j].basis}</Badge>{" →"}
                          </button>
                        )}
                      </span>
                    ))}
                  </div>
                </li>
              ))}
            </ol>
            <p className="mt-3 text-xs text-muted-foreground">{data.deterministic.caveat}</p>
          </Panel>
          <Panel title="AI explanation"><AiBlock ai={data.ai} status={data.ai_status} /></Panel>
          <Panel title="Evidence pack"><EvidenceList items={data.evidence} /></Panel>
        </>
      )}
    </div>
  )
}

type Lead = { type: string; title: string; score: number; entities?: EntitySummary[]; caveat?: string }

export function LeadsView({ onOpen }: { onOpen: (id: string) => void }) {
  const [type, setType] = useState("")
  const { data, error, isLoading } = useSWR<{ leads: Lead[] }>(`/api/leads${type ? `?type=${type}` : ""}`, fetcher)
  const types = ["", "sanctions", "circular_ownership", "shared_address", "shared_directors"]
  return (
    <Panel title="Investigation leads (deterministic, no AI)" actions={
      <label className="flex items-center gap-2 text-xs text-muted-foreground">Type
        <select value={type} onChange={(e) => setType(e.target.value)} className="rounded border border-input bg-background px-2 py-1 text-xs text-foreground">
          {types.map((t) => <option key={t} value={t}>{t || "all"}</option>)}
        </select>
      </label>
    }>
      {!data ? <StateMessage error={error} loading={isLoading} /> : data.leads.length === 0 ? <StateMessage empty="No leads in the loaded data." /> : (
        <ul className="flex flex-col gap-3">
          {data.leads.map((l, i) => (
            <li key={i} className="rounded border border-border/60 p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2"><Badge>{l.type}</Badge><Badge>{`score ${l.score}`}</Badge><span className="font-medium">{l.title}</span></div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">{(l.entities ?? []).slice(0, 12).map((e) => <EntityChip key={e.id} entity={e} onOpen={onOpen} />)}</div>
              {l.caveat && <p className="mt-2 text-xs text-muted-foreground">{l.caveat}</p>}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

type Match = { id: number; method: string; score: number; status: string; left: EntitySummary; right: EntitySummary; features: Record<string, unknown> }

export function ReviewView({ onOpen }: { onOpen: (id: string) => void }) {
  const [status, setStatus] = useState("pending")
  const { data, error, isLoading, mutate } = useSWR<{ items: Match[] }>(`/api/review?status=${status}`, fetcher)
  const decide = async (id: number, decision: string) => { await apiPost(`/api/review/${id}`, { decision }); mutate() }
  return (
    <Panel title="Entity-resolution review queue" actions={
      <div className="flex gap-1">{["pending", "auto", "confirmed", "rejected"].map((s) => (
        <Button key={s} size="sm" variant={status === s ? "default" : "outline"} onClick={() => setStatus(s)}>{s}</Button>
      ))}</div>
    }>
      <p className="mb-3 text-xs text-muted-foreground">Fuzzy name matches are never merged automatically. Confirming creates an inferred same_as edge; rejecting removes it.</p>
      {!data ? <StateMessage error={error} loading={isLoading} /> : data.items.length === 0 ? <StateMessage empty="Nothing in this queue. Run `python -m app.cli screen` after loading sources." /> : (
        <ul className="flex flex-col gap-2">
          {data.items.map((m) => (
            <li key={m.id} className="flex flex-col gap-2 rounded border border-border/60 p-3 text-sm md:flex-row md:items-center md:justify-between">
              <div className="flex flex-wrap items-center gap-2">
                <Badge>{`#${m.id}`}</Badge><Badge>{m.method}</Badge><Badge>{`score ${m.score.toFixed(1)}`}</Badge>
                <EntityChip entity={m.left} onOpen={onOpen} /><span className="text-muted-foreground">≈</span><EntityChip entity={m.right} onOpen={onOpen} />
              </div>
              <div className="flex gap-2">
                {m.status !== "confirmed" && <Button size="sm" onClick={() => decide(m.id, "confirm")}>Confirm</Button>}
                {m.status !== "rejected" && <Button size="sm" variant="outline" onClick={() => decide(m.id, "reject")}>Reject</Button>}
                {m.status !== "pending" && <Button size="sm" variant="ghost" onClick={() => decide(m.id, "reset")}>Reset</Button>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

export function RelationshipInspector({ relId, onOpen, onClose }: { relId: number; onOpen: (id: string) => void; onClose: () => void }) {
  const { data, error, isLoading } = useSWR<{
    relationship: { id: number; label: string; basis: string; confidence: number; source_id: string; start_date: string | null; end_date: string | null; status: string | null }
    subject: EntitySummary; object: EntitySummary; evidence: Evidence[]; raw_record: { record_key: string; url: string | null; last_fetched_at: string; payload: string } | null
  }>(`/api/relationships/${relId}`, fetcher)
  return (
    <aside aria-label="Evidence inspector" className="fixed inset-y-0 right-0 z-40 flex w-full max-w-lg flex-col border-l border-border bg-card shadow-2xl">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold">Evidence inspector · edge #{relId}</h2>
        <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>
      </header>
      <div className="flex-1 overflow-y-auto p-4">
        {!data ? <StateMessage error={error} loading={isLoading} /> : (
          <div className="flex flex-col gap-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <EntityChip entity={data.subject} onOpen={onOpen} /><span className="text-muted-foreground">{data.relationship.label}</span><EntityChip entity={data.object} onOpen={onOpen} />
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">Basis</dt><dd><Badge tone={data.relationship.basis}>{data.relationship.basis}</Badge></dd>
              <dt className="text-muted-foreground">Confidence</dt><dd className="font-mono">{data.relationship.confidence.toFixed(2)}</dd>
              <dt className="text-muted-foreground">Source</dt><dd>{data.relationship.source_id}</dd>
              <dt className="text-muted-foreground">Dates</dt><dd className="font-mono">{data.relationship.start_date ?? "?"} → {data.relationship.end_date ?? "open"}</dd>
            </dl>
            <div><h3 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">Supporting evidence</h3><EvidenceList items={data.evidence} /></div>
            {data.raw_record && (
              <details>
                <summary className="cursor-pointer text-xs font-semibold uppercase text-muted-foreground">Original source record ({data.raw_record.record_key})</summary>
                <pre className="mt-2 max-h-80 overflow-auto rounded bg-background/60 p-2 font-mono text-[11px]">{data.raw_record.payload.slice(0, 20000)}</pre>
              </details>
            )}
          </div>
        )}
      </div>
    </aside>
  )
}
