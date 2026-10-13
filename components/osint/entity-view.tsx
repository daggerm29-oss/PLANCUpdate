"use client"

import { useState } from "react"
import useSWR from "swr"

import { Button } from "@/components/ui/button"
import { type AiAnswer, type AiStatus, apiPost, enc, type EntitySummary, type Evidence, fetcher, topicsOf } from "@/lib/api"

import { AiBlock, Badge, EntityChip, EvidenceList, Panel, StateMessage } from "./bits"
import { GraphView } from "./graph-view"

type Rel = {
  id: number; predicate: string; label: string; basis: string; confidence: number; source_id: string
  start_date: string | null; end_date: string | null; other: EntitySummary | null
}
type Detail = {
  entity: EntitySummary & { address: string | null; incorporation_date: string | null; legal_form: string | null }
  names: { name: string; name_type: string }[]
  identifiers: { scheme: string; value: string }[]
  relationships: Rel[]
  evidence: Evidence[]
  matches: { id: number; method: string; score: number; status: string }[]
}

const TABS = ["overview", "graph", "ownership", "timeline", "report"] as const
type Tab = (typeof TABS)[number]

export function EntityView({ entityId, onOpen, onEdge, onSave }: {
  entityId: string
  onOpen: (id: string) => void
  onEdge: (relId: number) => void
  onSave: (id: string) => void
}) {
  const [tab, setTab] = useState<Tab>("overview")
  const { data, error, isLoading } = useSWR<Detail>(`/api/entities/${enc(entityId)}`, fetcher)
  if (!data) return <StateMessage error={error} loading={isLoading} />
  const e = data.entity

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-mono text-xs text-muted-foreground">{e.id}</p>
          <h1 className="text-balance text-2xl font-semibold">{e.name}</h1>
          <div className="mt-2 flex flex-wrap gap-2">
            <Badge>{e.kind}</Badge>
            <Badge>{e.source_id}</Badge>
            {e.country && <Badge>{e.country}</Badge>}
            {e.status && <Badge>{e.status}</Badge>}
            {topicsOf(e).map((t) => <Badge key={t} tone="reported">{t}</Badge>)}
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={() => onSave(e.id)}>Add to investigation</Button>
      </header>

      <nav className="flex gap-1 border-b border-border" aria-label="Entity sections">
        {TABS.map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)} aria-current={tab === t ? "page" : undefined}
            className={`-mb-px border-b-2 px-3 py-2 text-sm capitalize ${tab === t ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
            {t}
          </button>
        ))}
      </nav>

      {tab === "overview" && <Overview data={data} onOpen={onOpen} onEdge={onEdge} />}
      {tab === "graph" && <Panel title="Relationship graph"><GraphView entityId={entityId} onOpen={onOpen} onEdge={onEdge} /></Panel>}
      {tab === "ownership" && <Ownership entityId={entityId} onOpen={onOpen} onEdge={onEdge} />}
      {tab === "timeline" && <Timeline entityId={entityId} />}
      {tab === "report" && <Report entityId={entityId} />}
    </div>
  )
}

function Overview({ data, onOpen, onEdge }: { data: Detail; onOpen: (id: string) => void; onEdge: (id: number) => void }) {
  const e = data.entity
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="flex flex-col gap-4 lg:col-span-2">
        <Panel title={`Relationships (${data.relationships.length})`}>
          {data.relationships.length === 0 ? <StateMessage empty="None recorded." /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted-foreground">
                  <tr><th className="pb-2 font-medium">Relation</th><th className="pb-2 font-medium">Counterpart</th><th className="pb-2 font-medium">Basis</th><th className="pb-2 font-medium">Since</th><th className="sr-only">Evidence</th></tr>
                </thead>
                <tbody>
                  {data.relationships.map((r) => (
                    <tr key={r.id} className="border-t border-border/60">
                      <td className="py-2 pr-3 text-muted-foreground">{r.label}</td>
                      <td className="py-2 pr-3"><EntityChip entity={r.other} onOpen={onOpen} /></td>
                      <td className="py-2 pr-3"><Badge tone={r.basis}>{r.basis} {r.confidence.toFixed(2)}</Badge></td>
                      <td className="py-2 pr-3 font-mono text-xs">{r.start_date ?? "—"}</td>
                      <td className="py-2 text-right"><Button variant="ghost" size="sm" onClick={() => onEdge(r.id)}>Evidence</Button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Brief entityId={e.id} />
      </div>
      <div className="flex flex-col gap-4">
        <Panel title="Record">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
            <dt className="text-muted-foreground">Address</dt><dd>{e.address ?? "—"}</dd>
            <dt className="text-muted-foreground">Created</dt><dd className="font-mono">{e.incorporation_date ?? "—"}</dd>
            <dt className="text-muted-foreground">Legal form</dt><dd>{e.legal_form ?? "—"}</dd>
            {data.identifiers.map((i) => (
              <div key={i.scheme + i.value} className="contents"><dt className="text-muted-foreground uppercase">{i.scheme}</dt><dd className="font-mono break-all">{i.value}</dd></div>
            ))}
          </dl>
          {data.names.length > 1 && (
            <p className="mt-3 text-xs text-muted-foreground">Aliases: {data.names.filter((n) => n.name_type !== "legal").map((n) => n.name).join(" · ")}</p>
          )}
        </Panel>
        <Panel title="Source evidence"><EvidenceList items={data.evidence} /></Panel>
      </div>
    </div>
  )
}

function Brief({ entityId }: { entityId: string }) {
  const [question, setQuestion] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)
  const [result, setResult] = useState<{ deterministic: { summary: string; lines: string[]; caveat: string }; ai: AiAnswer; ai_status: AiStatus } | null>(null)

  const run = async (useAi: boolean) => {
    setBusy(true); setErr(null)
    try {
      setResult(await apiPost("/api/assistant/brief", { entity_id: entityId, question: question || null, use_ai: useAi }))
    } catch (e) { setErr(e) } finally { setBusy(false) }
  }

  return (
    <Panel title="Investigative assistant">
      <form className="flex flex-col gap-2 sm:flex-row" onSubmit={(ev) => { ev.preventDefault(); run(true) }}>
        <label htmlFor="brief-q" className="sr-only">Question about this entity</label>
        <input id="brief-q" value={question} onChange={(ev) => setQuestion(ev.target.value)} maxLength={500}
          placeholder="Optional question, e.g. which links should be verified first?"
          className="flex-1 rounded border border-input bg-background px-3 py-1.5 text-sm" />
        <Button type="submit" size="sm" disabled={busy}>{busy ? "Working…" : "Ask AI (1 request)"}</Button>
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => run(false)}>Facts only</Button>
      </form>
      <div className="mt-3 flex flex-col gap-3">
        <StateMessage error={err} />
        {result && (
          <>
            <div className="text-sm">
              <p className="font-medium">{result.deterministic.summary}</p>
              <ul className="mt-2 list-disc pl-5 text-muted-foreground">{result.deterministic.lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
              <p className="mt-2 text-xs text-muted-foreground">{result.deterministic.caveat}</p>
            </div>
            <AiBlock ai={result.ai} status={result.ai_status} />
          </>
        )}
      </div>
    </Panel>
  )
}

type OwnNode = { id: string; depth: number; entity: EntitySummary; edge: { id: number; label: string; basis: string } | null }

function Ownership({ entityId, onOpen, onEdge }: { entityId: string; onOpen: (id: string) => void; onEdge: (id: number) => void }) {
  const [direction, setDirection] = useState<"up" | "down">("up")
  const { data, error, isLoading } = useSWR<{ nodes: OwnNode[]; truncated: boolean }>(
    `/api/entities/${enc(entityId)}/ownership?direction=${direction}`, fetcher)
  return (
    <Panel title="Ownership chain" actions={
      <div className="flex gap-1">
        {(["up", "down"] as const).map((d) => (
          <Button key={d} size="sm" variant={direction === d ? "default" : "outline"} onClick={() => setDirection(d)}>
            {d === "up" ? "Parents / owners" : "Subsidiaries"}
          </Button>
        ))}
      </div>
    }>
      {!data ? <StateMessage error={error} loading={isLoading} /> : data.nodes.length === 0 ? <StateMessage empty="No documented ownership in this direction." /> : (
        <ol className="flex flex-col gap-1.5 text-sm">
          {data.nodes.map((n) => (
            <li key={n.id} className="flex flex-wrap items-center gap-2" style={{ paddingLeft: `${(n.depth - 1) * 20}px` }}>
              <span className="font-mono text-xs text-muted-foreground">L{n.depth}</span>
              <span className="text-muted-foreground">{n.edge?.label ?? "linked"}</span>
              <EntityChip entity={n.entity} onOpen={onOpen} />
              {n.edge && <button type="button" onClick={() => onEdge(n.edge!.id)}><Badge tone={n.edge.basis}>{n.edge.basis}</Badge></button>}
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}

function Timeline({ entityId }: { entityId: string }) {
  const { data, error, isLoading } = useSWR<{ events: { date: string; label: string; source_id: string; type: string }[]; conflicts: { field: string; values: { entity_id: string; value: string }[] }[] }>(
    `/api/entities/${enc(entityId)}/timeline`, fetcher)
  if (!data) return <StateMessage error={error} loading={isLoading} />
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Timeline">
        {data.events.length === 0 ? <StateMessage empty="No dated events." /> : (
          <ol className="relative flex flex-col gap-3 border-l border-border pl-4 text-sm">
            {data.events.map((ev, i) => (
              <li key={i}><p className="font-mono text-xs text-primary">{ev.date}</p><p>{ev.label} <span className="text-muted-foreground">({ev.source_id})</span></p></li>
            ))}
          </ol>
        )}
      </Panel>
      <Panel title="Contradictions between linked records">
        {data.conflicts.length === 0 ? <StateMessage empty="No conflicting fields across same_as-linked records." /> : (
          <ul className="flex flex-col gap-2 text-sm">
            {data.conflicts.map((c) => (
              <li key={c.field}><span className="font-medium">{c.field}</span>: {c.values.map((v) => `${v.entity_id} = ${v.value}`).join(" vs ")}</li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  )
}

function Report({ entityId }: { entityId: string }) {
  const [ai, setAi] = useState(false)
  const { data, error, isLoading } = useSWR<{ markdown: string; ai_status: AiStatus }>(
    `/api/entities/${enc(entityId)}/report?ai=${ai}`, fetcher, { revalidateOnFocus: false })
  const download = () => {
    if (!data) return
    const url = URL.createObjectURL(new Blob([data.markdown], { type: "text/markdown" }))
    const a = Object.assign(document.createElement("a"), { href: url, download: `report-${entityId.replace(/[^\w-]/g, "_")}.md` })
    a.click(); URL.revokeObjectURL(url)
  }
  return (
    <Panel title="Evidence-backed report" actions={
      <div className="flex gap-2">
        <Button size="sm" variant={ai ? "default" : "outline"} onClick={() => setAi(!ai)}>{ai ? "AI section on" : "Add AI section (1 request)"}</Button>
        <Button size="sm" variant="outline" onClick={download} disabled={!data}>Download .md</Button>
      </div>
    }>
      {!data ? <StateMessage error={error} loading={isLoading} /> : (
        <>
          {ai && <AiBlock ai={null} status={data.ai_status.used ? undefined : data.ai_status} />}
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded bg-background/60 p-3 font-mono text-xs leading-relaxed">{data.markdown}</pre>
        </>
      )}
    </Panel>
  )
}
