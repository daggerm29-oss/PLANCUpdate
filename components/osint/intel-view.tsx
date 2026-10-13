"use client"

import { useState } from "react"
import useSWR, { mutate } from "swr"

import { Button } from "@/components/ui/button"
import { apiPost, type EntitySummary, fetcher } from "@/lib/api"

import { Badge, EntityChip, Panel, StateMessage } from "./bits"

type Claim = {
  id: number
  claim_type: string
  field: string
  value: string
  status: "unverified" | "verified" | "conflict" | "related"
  source_url: string | null
  evidence_excerpt: string | null
  extraction_method: string
  confidence: number
  retrieved_at: string
  verification?: unknown
}

type Rel = { id: number; predicate: string; label: string; basis: string; source_id: string; other?: EntitySummary | null }

type Dossier = {
  entity: EntitySummary & { legal_form?: string | null; incorporation_date?: string | null; address?: string | null }
  identifiers: { scheme: string; value: string }[]
  verified: {
    registry_evidence: { key: string; source_id: string; url: string | null; title: string | null; excerpt: string | null; retrieved_at: string }[]
    claims: Claim[]
  }
  connections: {
    same_identifier: { scheme: string; value: string; entity: EntitySummary }[]
    parents: Rel[]
    subsidiaries: Rel[]
    ownership: Rel[]
    people: Rel[]
    possible_same_entity: Rel[]
    flags: { own_topics: string[]; linked: { entity: EntitySummary; topics: string[]; via: string }[] }
    shared_address_peers: { count: number; sample: EntitySummary[]; capped: boolean }
  }
  unverified_leads: Claim[]
  caveats: string[]
}

type CrawlInfo = {
  ok: boolean
  status?: string
  cached?: boolean
  deduplicated?: boolean
  query_sent: string
  claims?: number
  message?: string
  error?: { code: string; message: string; retry_after?: number | null }
  fields_retrieved?: string[]
  serper_calls?: { search: number; scrape_gateway: number } | null
  fetch_log?: { url: string; kind: string; outcome: string; via: string | null; reason: string | null }[]
}

type Result = {
  query: string
  detected_identifiers: { scheme: string; value: string }[]
  subject_id: string | null
  candidates: EntitySummary[]
  crawl: CrawlInfo | null
  dossier: Dossier | null
  claims?: Claim[]
}

const STATUS_TONE: Record<Claim["status"], string> = {
  verified: "documented",
  unverified: "reported",
  related: "inferred",
  conflict: "reported",
}

export function IntelView({ onOpen }: { onOpen: (id: string) => void }) {
  const [query, setQuery] = useState("")
  const [crawl, setCrawl] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [result, setResult] = useState<Result | null>(null)

  const run = async (entityId?: string) => {
    if (!query.trim()) return
    setBusy(true)
    setError(null)
    try {
      setResult(await apiPost<Result>("/api/intel/investigate", { query: query.trim(), entity_id: entityId, crawl }))
      if (crawl) mutate("/api/crawler/usage")
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Investigate">
        <form onSubmit={(e) => { e.preventDefault(); run() }} className="flex flex-col gap-3">
          <label htmlFor="intel-q" className="text-sm text-muted-foreground">
            CIN, LEI, PAN, DIN or legal name / alias
          </label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input id="intel-q" value={query} onChange={(e) => setQuery(e.target.value)} maxLength={200}
              placeholder="e.g. L29246GJ1988PLC010888 or Wabtec Corporation"
              className="h-9 flex-1 rounded border border-input bg-background px-3 font-mono text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" />
            <Button type="submit" disabled={busy || !query.trim()}>{busy ? "Working…" : "Investigate"}</Button>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={crawl} onChange={(e) => setCrawl(e.target.checked)} className="size-4 accent-primary" />
            Also run the web crawler <span className="text-muted-foreground">(spends up to 4 Serper credits unless cached)</span>
          </label>
        </form>
      </Panel>

      <StateMessage error={error} />
      {result && <ResultView result={result} onOpen={onOpen} onPick={(id) => run(id)} />}
      <CrawlerPanel />
    </div>
  )
}

const CIN_STATE = /^[LU]\d{5}([A-Z]{2})\d{4}[A-Z]{3}\d{6}$/

function missingRegistryHint(result: Result): string {
  const cin = result.claims?.find((c) => c.field === "cin")?.value ?? result.dossier?.unverified_leads.find((c) => c.field === "cin")?.value
  const state = cin?.match(CIN_STATE)?.[1]
  if (cin && state) {
    return `No local record yet. The crawler found CIN ${cin} (state ${state}). Upload the ${state} MCA Company Master CSV under "Sources & AI budget", then investigate again to verify it.`
  }
  return "No local record matches. Upload the state's MCA Company Master CSV under \"Sources & AI budget\", or run the crawler for web leads."
}

function ResultView({ result, onOpen, onPick }: { result: Result; onOpen: (id: string) => void; onPick: (id: string) => void }) {
  const d = result.dossier
  return (
    <>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">Detected:</span>
        {result.detected_identifiers.length
          ? result.detected_identifiers.map((i) => <Badge key={i.scheme + i.value}>{i.scheme}: {i.value}</Badge>)
          : <Badge>name search</Badge>}
      </div>

      {result.crawl && <CrawlStatus crawl={result.crawl} />}

      {!d && (
        <Panel title={`Candidates (${result.candidates.length})`}>
          {result.candidates.length ? (
            <ul className="flex flex-col gap-1.5 text-sm">
              {result.candidates.map((c) => (
                <li key={c.id} className="flex flex-wrap items-center gap-2">
                  <EntityChip entity={c} onOpen={onOpen} />
                  <Badge>{c.source_id}</Badge>
                  <button type="button" onClick={() => onPick(c.id)} className="text-xs text-primary hover:underline">use as subject</button>
                </li>
              ))}
            </ul>
          ) : <StateMessage empty={missingRegistryHint(result)} />}
        </Panel>
      )}

      {d && <DossierView d={d} onOpen={onOpen} />}
      {!d && result.claims && result.claims.length > 0 && (
        <Panel title={`Unverified web leads (${result.claims.length})`}><ClaimList claims={result.claims} /></Panel>
      )}
    </>
  )
}

function CrawlStatus({ crawl }: { crawl: CrawlInfo }) {
  return (
    <div role="status" className="rounded border border-border bg-card p-3 text-sm">
      {crawl.ok ? (
        <p>
          Crawler <span className="font-mono">{crawl.status}</span> for “{crawl.query_sent}”
          {crawl.cached && " (served from cache, no credits used)"}
          {crawl.deduplicated && " (shared an identical in-flight request)"}
          {typeof crawl.claims === "number" && ` · ${crawl.claims} claims stored`}
          {crawl.message && ` · ${crawl.message}`}
          {crawl.serper_calls &&
            ` · Serper: ${crawl.serper_calls.search} search, ${crawl.serper_calls.scrape_gateway} gateway`}
        </p>
      ) : (
        <p className="text-destructive">
          Crawler {crawl.error?.code}: {crawl.error?.message}
          {crawl.error?.retry_after ? ` (retry in ${Math.ceil(crawl.error.retry_after)}s)` : ""}
        </p>
      )}
      {crawl.fields_retrieved && crawl.fields_retrieved.length > 0 && (
        <p className="mt-1 text-xs text-muted-foreground">
          Fields extracted (unverified): <span className="font-mono">{crawl.fields_retrieved.join(", ")}</span>
        </p>
      )}
      {crawl.fetch_log && crawl.fetch_log.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1 text-xs" aria-label="Source fetch attempts">
          {crawl.fetch_log.map((f) => (
            <li key={f.url} className="flex flex-wrap gap-x-2">
              <span className="font-mono">{f.kind}</span>
              <span className={f.outcome === "fetched" ? "text-foreground" : "text-muted-foreground"}>{f.outcome}</span>
              <a href={f.url} target="_blank" rel="noreferrer" className="truncate underline underline-offset-2">
                {f.url}
              </a>
              {f.reason && <span className="text-muted-foreground">{f.reason}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function DossierView({ d, onOpen }: { d: Dossier; onOpen: (id: string) => void }) {
  const c = d.connections
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Subject" className="lg:col-span-2">
        <div className="flex flex-col gap-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <EntityChip entity={d.entity} onOpen={onOpen} />
            <Badge>{d.entity.source_id}</Badge>
            {d.entity.status && <Badge>{d.entity.status}</Badge>}
            {c.flags.own_topics.map((t) => <Badge key={t} tone="reported">{t}</Badge>)}
          </div>
          {d.entity.address && <p className="text-muted-foreground">{d.entity.address}</p>}
          <div className="flex flex-wrap gap-1.5">
            {d.identifiers.map((i) => <Badge key={i.scheme + i.value}>{i.scheme}: {i.value}</Badge>)}
          </div>
        </div>
      </Panel>

      <Panel title="Verified facts">
        {d.verified.registry_evidence.length === 0 && d.verified.claims.length === 0
          ? <StateMessage empty="No registry evidence for this record." />
          : (
            <ul className="flex flex-col gap-2 text-sm">
              {d.verified.registry_evidence.map((ev) => (
                <li key={ev.key} className="rounded border border-border/60 bg-background/40 p-2.5">
                  <div className="flex flex-wrap items-center gap-2"><Badge tone="documented">{ev.source_id}</Badge><span className="font-medium">{ev.title}</span></div>
                  {ev.excerpt && <p className="mt-1 text-muted-foreground">{ev.excerpt}</p>}
                  <SourceLine url={ev.url} at={ev.retrieved_at} />
                </li>
              ))}
              {d.verified.claims.length > 0 && <li><ClaimList claims={d.verified.claims} /></li>}
            </ul>
          )}
      </Panel>

      <Panel title="Connections">
        <div className="flex flex-col gap-3 text-sm">
          {!(c.parents.length || c.subsidiaries.length || c.ownership.length || c.people.length ||
            c.possible_same_entity.length || c.same_identifier.length || c.flags.linked.length || c.shared_address_peers.count) && (
            <StateMessage empty="No connections in the loaded datasets. The samples do not overlap; load full files to see cross-source links." />
          )}
          <RelGroup title="Parent companies" rels={c.parents} onOpen={onOpen} />
          <RelGroup title="Subsidiaries" rels={c.subsidiaries} onOpen={onOpen} />
          <RelGroup title="Ownership" rels={c.ownership} onOpen={onOpen} />
          <RelGroup title="People" rels={c.people} onOpen={onOpen} />
          <RelGroup title="Possible same entity" rels={c.possible_same_entity} onOpen={onOpen} />
          {c.same_identifier.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold text-muted-foreground">Same identifier in another source</h3>
              <ul className="flex flex-col gap-1">
                {c.same_identifier.map((s) => (
                  <li key={s.entity.id + s.value} className="flex flex-wrap items-center gap-2">
                    <Badge>{s.scheme}</Badge><EntityChip entity={s.entity} onOpen={onOpen} /><Badge>{s.entity.source_id}</Badge>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {c.flags.linked.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold text-muted-foreground">Flagged linked records (leads)</h3>
              <ul className="flex flex-col gap-1">
                {c.flags.linked.map((f) => (
                  <li key={f.entity.id} className="flex flex-wrap items-center gap-2">
                    <EntityChip entity={f.entity} onOpen={onOpen} /><span className="text-xs text-muted-foreground">{f.via}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {c.shared_address_peers.count > 0 && (
            <p className="text-muted-foreground">
              {c.shared_address_peers.capped ? "25+" : c.shared_address_peers.count} other companies share this registered address
              (common for registered-agent and accountant addresses).
            </p>
          )}
        </div>
      </Panel>

      <Panel title={`Unverified leads (${d.unverified_leads.length})`} className="lg:col-span-2">
        {d.unverified_leads.length ? <ClaimList claims={d.unverified_leads} /> : <StateMessage empty="No web claims. Run the crawler to collect leads." />}
      </Panel>

      <Panel title="Caveats" className="lg:col-span-2">
        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          {d.caveats.map((t) => <li key={t}>{t}</li>)}
        </ul>
      </Panel>
    </div>
  )
}

function RelGroup({ title, rels, onOpen }: { title: string; rels: Rel[]; onOpen: (id: string) => void }) {
  if (!rels.length) return null
  return (
    <div>
      <h3 className="mb-1 text-xs font-semibold text-muted-foreground">{title}</h3>
      <ul className="flex flex-col gap-1">
        {rels.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">{r.label}</span>
            <EntityChip entity={r.other} onOpen={onOpen} />
            <Badge tone={r.basis}>{r.basis}</Badge>
            <Badge>{r.source_id}</Badge>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ClaimList({ claims }: { claims: Claim[] }) {
  return (
    <ul className="flex flex-col gap-2 text-sm">
      {claims.map((c) => (
        <li key={c.id} className="rounded border border-border/60 bg-background/40 p-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONE[c.status]}>{c.status}</Badge>
            <span className="font-mono text-xs text-muted-foreground">{c.field}</span>
            <span className="font-medium">{c.value}</span>
            <span className="ml-auto font-mono text-[10px] text-muted-foreground">conf {c.confidence.toFixed(2)}</span>
          </div>
          {c.evidence_excerpt && <p className="mt-1 text-muted-foreground">{c.evidence_excerpt}</p>}
          <p className="mt-1 font-mono text-[10px] text-muted-foreground">{c.extraction_method}</p>
          <SourceLine url={c.source_url} at={c.retrieved_at} />
        </li>
      ))}
    </ul>
  )
}

function SourceLine({ url, at }: { url: string | null; at?: string }) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[10px] text-muted-foreground">
      {url && <a href={url} target="_blank" rel="noreferrer" className="max-w-full truncate text-primary hover:underline">{url}</a>}
      {at && <span>retrieved {at}</span>}
    </div>
  )
}

type Health = { configured: boolean; reachable: boolean; serper_configured?: boolean; latency_ms?: number; url?: string; error?: string }
type Usage = {
  limits: { rpm: number; daily_budget: number; max_retries: number }
  last_24h: { upstream_attempts: number; serper_credits_upper_bound: number; remaining_attempts: number }
  cache: { entries: number; hits: number }
  note: string
}

export function CrawlerPanel() {
  const health = useSWR<Health>("/api/crawler/health", fetcher, { refreshInterval: 60000 })
  const usage = useSWR<Usage>("/api/crawler/usage", fetcher, { refreshInterval: 15000 })
  return (
    <Panel title="Crawler">
      {!health.data || !usage.data ? <StateMessage error={health.error || usage.error} loading /> : (
        <div className="flex flex-col gap-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={health.data.reachable ? "documented" : "reported"}>
              {!health.data.configured ? "not configured" : health.data.reachable ? "reachable" : "unreachable"}
            </Badge>
            {health.data.configured && <Badge tone={health.data.serper_configured ? "documented" : "reported"}>
              serper key {health.data.serper_configured ? "set" : "missing"}
            </Badge>}
            {health.data.latency_ms !== undefined && <span className="font-mono text-xs text-muted-foreground">{health.data.latency_ms} ms</span>}
          </div>
          {health.data.error && <p className="text-muted-foreground">{health.data.error}</p>}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-xs sm:grid-cols-4">
            <dt className="text-muted-foreground">calls 24h</dt><dd>{usage.data.last_24h.upstream_attempts} / {usage.data.limits.daily_budget}</dd>
            <dt className="text-muted-foreground">credits ≤</dt><dd>{usage.data.last_24h.serper_credits_upper_bound}</dd>
            <dt className="text-muted-foreground">rpm limit</dt><dd>{usage.data.limits.rpm}</dd>
            <dt className="text-muted-foreground">cache hits</dt><dd>{usage.data.cache.hits} ({usage.data.cache.entries} entries)</dd>
          </dl>
          <p className="text-xs text-muted-foreground">{usage.data.note}</p>
        </div>
      )}
    </Panel>
  )
}
