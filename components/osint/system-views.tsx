"use client"

import { useState } from "react"
import useSWR from "swr"

import { Button } from "@/components/ui/button"
import { apiPost, type EntitySummary, fetcher } from "@/lib/api"

import { Badge, EntityChip, Panel, StateMessage } from "./bits"
import { CrawlerPanel } from "./intel-view"
import { McaUploadPanel } from "./mca-upload-panel"

type Stats = {
  backend?: string
  counts: Record<string, number>
  sources: { id: string; name: string; homepage: string | null; licence: string; entities: number }[]
  runs: { id: number; source_id: string; started_at: string; finished_at: string | null; status: string; error: string | null }[]
  pending_reviews: number
}

type AiStatusFull = {
  provider: { provider: string; model: string; available: boolean; reason?: string }
  limits: Record<string, number>
  today: { requests: number; ok: number; rate_limited: number; failed: number; total_tokens: number; remaining_requests: number }
  window: { requests_last_60s: number; next_slot_in_seconds: number }
  cooldown: { active: boolean; remaining_seconds: number }
  cache: { entries: number; hits: number; misses: number; hit_rate: number | null }
  recent_requests: { id: number; status: string; task: string; attempt: number; http_status: number | null; latency_ms: number | null }[]
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded border border-border/60 bg-background/40 p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 font-mono text-lg">{value}</p>
    </div>
  )
}

type DuckDbBench = {
  status: "ok" | "error"
  engine: string
  httpfs_loaded: boolean
  hf_token_configured: boolean
  hf_repo: string | null
  sample_query_latency_ms: number
  auth_verified: boolean
  target_files: {
    entities: string
    identifiers: string
    relationships: string
  }
  error?: string
}

export function DuckDbHfPanel() {
  const { data, isLoading, mutate } = useSWR<DuckDbBench>("/api/duckdb/test", fetcher)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{
    results?: EntitySummary[]
    duration_ms?: number
    source?: string
    error?: string
  } | null>(null)

  const runLiveSearchTest = async () => {
    setTesting(true)
    try {
      const res = await fetcher<{
        results: EntitySummary[]
        duration_ms: number
        source: string
      }>("/api/search?q=U72900KA2022PTC165499")
      setTestResult(res)
    } catch (e: unknown) {
      setTestResult({ error: e instanceof Error ? e.message : String(e) })
    } finally {
      setTesting(false)
    }
  }

  return (
    <Panel
      title="DuckDB Serverless (Parquet & Hugging Face)"
      actions={
        data?.status === "ok" ? (
          <Badge tone="documented">
            Latency: {data.sample_query_latency_ms} ms
          </Badge>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-3 text-sm">
        <p className="text-muted-foreground">
          Queries remote partitioned Parquet files hosted on Hugging Face using serverless DuckDB with projection pushdown and range requests.
        </p>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Stat label="Engine" value="DuckDB Node" />
          <Stat label="httpfs module" value={data?.httpfs_loaded ? "Loaded" : "Ready"} />
          <Stat
            label="Hugging Face Auth"
            value={data?.hf_token_configured ? "Token Active" : "Local Mode"}
          />
          <Stat
            label="Query Latency"
            value={data ? `${data.sample_query_latency_ms} ms` : "—"}
          />
        </div>

        <div className="rounded border border-border/60 bg-muted/30 p-2.5 font-mono text-xs">
          <div className="text-muted-foreground mb-1">Target Parquet Dataset:</div>
          <div className="truncate">Entities: {data?.target_files?.entities || "loading..."}</div>
          <div className="truncate">Identifiers: {data?.target_files?.identifiers || "loading..."}</div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => mutate()}
            disabled={isLoading}
          >
            Refresh Benchmark
          </Button>
          <Button
            size="sm"
            onClick={runLiveSearchTest}
            disabled={testing}
          >
            {testing ? "Testing..." : "Test Company Search (CIN)"}
          </Button>
          {testResult && (
            <span className="text-xs text-muted-foreground">
              {testResult.error
                ? `Error: ${testResult.error}`
                : `Found: ${testResult.results?.[0]?.name || "None"} (${testResult.duration_ms} ms · ${testResult.source})`}
            </span>
          )}
        </div>
      </div>
    </Panel>
  )
}


export function SystemView() {
  const stats = useSWR<Stats>("/api/stats", fetcher, { refreshInterval: 15000 })
  const ai = useSWR<AiStatusFull>("/api/ai/status", fetcher, { refreshInterval: 10000 })
  return (
    <div className="flex flex-col gap-4">
      <CodexAuthPanel />
      <Panel
        title="Data store"
        actions={
          stats.data?.backend ? (
            <Badge tone="documented">
              Engine: {stats.data.backend}
            </Badge>
          ) : undefined
        }
      >
        {!stats.data ? <StateMessage error={stats.error} loading={stats.isLoading} /> : (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              {Object.entries(stats.data.counts).map(([k, v]) => <Stat key={k} label={k.replace(/_/g, " ")} value={v.toLocaleString()} />)}
              <Stat label="pending reviews" value={stats.data.pending_reviews} />
            </div>
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground"><tr><th className="pb-2 font-medium">Source</th><th className="pb-2 font-medium">Licence</th><th className="pb-2 text-right font-medium">Entities</th></tr></thead>
              <tbody>{stats.data.sources.map((s) => (
                <tr key={s.id} className="border-t border-border/60">
                  <td className="py-1.5">{s.homepage ? <a className="hover:underline" href={s.homepage} target="_blank" rel="noreferrer">{s.name}</a> : s.name}</td>
                  <td className="py-1.5 text-muted-foreground">{s.licence}</td>
                  <td className="py-1.5 text-right font-mono">{s.entities}</td>
                </tr>
              ))}</tbody>
            </table>
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase text-muted-foreground">Ingestion runs</h3>
              {stats.data.runs.length === 0 ? <StateMessage empty="No ingest runs yet. Use the CLI (see NEXT_STEPS.md)." /> : (
                <ul className="flex flex-col gap-1 text-sm">{stats.data.runs.map((r) => (
                  <li key={r.id} className="flex flex-wrap gap-2"><Badge>{`#${r.id}`}</Badge><Badge>{r.source_id}</Badge>
                    <Badge tone={r.status === "success" ? "documented" : r.status === "failed" ? "reported" : undefined}>{r.status}</Badge>
                    <span className="font-mono text-xs text-muted-foreground">{r.started_at}</span>{r.error && <span className="text-destructive">{r.error}</span>}</li>
                ))}</ul>
              )}
            </div>
          </div>
        )}
      </Panel>
      <DuckDbHfPanel />
      <McaUploadPanel />
      <CrawlerPanel />
      <Panel title="AI request budget">
        {!ai.data ? <StateMessage error={ai.error} loading={ai.isLoading} /> : (
          <div className="flex flex-col gap-4">
            <p className="text-sm">
              Provider <span className="font-mono">{ai.data.provider.provider}</span> · model <span className="font-mono">{ai.data.provider.model}</span> ·{" "}
              {ai.data.provider.available ? <Badge tone="documented">available</Badge> : <Badge tone="reported">unavailable</Badge>}
              {ai.data.provider.reason && <span className="ml-2 text-muted-foreground">{ai.data.provider.reason}</span>}
            </p>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Stat label="RPM limit" value={ai.data.limits.rpm_limit} />
              <Stat label="last 60s" value={ai.data.window.requests_last_60s} />
              <Stat label="next slot (s)" value={ai.data.window.next_slot_in_seconds} />
              <Stat label="today used / left" value={`${ai.data.today.requests} / ${ai.data.today.remaining_requests}`} />
              <Stat label="429s today" value={ai.data.today.rate_limited} />
              <Stat label="cooldown (s)" value={ai.data.cooldown.active ? ai.data.cooldown.remaining_seconds : "none"} />
              <Stat label="cache hit rate" value={ai.data.cache.hit_rate ?? "—"} />
              <Stat label="tokens today" value={ai.data.today.total_tokens} />
            </div>
            {ai.data.recent_requests.length > 0 && (
              <ul className="flex flex-col gap-1 text-xs">{ai.data.recent_requests.map((r) => (
                <li key={r.id} className="flex gap-2 font-mono"><span>#{r.id}</span><span>{r.task}</span><span>{r.status}</span><span>{r.http_status ?? ""}</span><span>{r.latency_ms ? `${r.latency_ms}ms` : ""}</span></li>
              ))}</ul>
            )}
          </div>
        )}
      </Panel>
    </div>
  )
}

export function CodexAuthPanel() {
  const { data: auth, mutate, error, isLoading } = useSWR<CodexAuthSession>(
    "/api/ai/auth/status",
    fetcher,
    { refreshInterval: 4000 }
  )
  const [loadingAction, setLoadingAction] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [manualUrl, setManualUrl] = useState("")
  const [showManual, setShowManual] = useState(false)

  const handleConnect = async () => {
    setLoadingAction("connect")
    setActionError(null)
    try {
      const res = await apiPost<{ ok: boolean; auth_url: string; state: string }>(
        "/api/ai/auth/login",
        {}
      )
      if (res?.auth_url) {
        window.open(res.auth_url, "_blank")
      }
      await mutate()
    } catch (err: any) {
      setActionError(err.message || "Failed to initiate ChatGPT OAuth login")
    } finally {
      setLoadingAction(null)
    }
  }

  const handleSelectModel = async (model: string) => {
    setLoadingAction("select-model")
    setActionError(null)
    try {
      await apiPost("/api/ai/auth/select-model", { model })
      await mutate()
    } catch (err: any) {
      setActionError(err.message || "Failed to update selected model")
    } finally {
      setLoadingAction(null)
    }
  }

  const handleRefreshModels = async () => {
    setLoadingAction("refresh-models")
    setActionError(null)
    try {
      await apiPost("/api/ai/auth/refresh-models", {})
      await mutate()
    } catch (err: any) {
      setActionError(err.message || "Failed to refresh models from API")
    } finally {
      setLoadingAction(null)
    }
  }

  const handleRefreshSession = async () => {
    setLoadingAction("refresh-session")
    setActionError(null)
    try {
      await apiPost("/api/ai/auth/refresh", {})
      await mutate()
    } catch (err: any) {
      setActionError(err.message || "Failed to refresh session")
    } finally {
      setLoadingAction(null)
    }
  }

  const handleDisconnect = async () => {
    setLoadingAction("disconnect")
    setActionError(null)
    try {
      await apiPost("/api/ai/auth/disconnect", {})
      await mutate()
    } catch (err: any) {
      setActionError(err.message || "Failed to disconnect")
    } finally {
      setLoadingAction(null)
    }
  }

  const handleManualSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!manualUrl.trim()) return
    setLoadingAction("exchange")
    setActionError(null)
    try {
      let code = manualUrl.trim()
      let state = ""
      if (code.includes("code=")) {
        const queryPart = code.includes("?") ? code.split("?")[1] : code
        const params = new URLSearchParams(queryPart)
        code = params.get("code") || code
        state = params.get("state") || ""
      }
      await apiPost("/api/ai/auth/callback", { code, state })
      setManualUrl("")
      setShowManual(false)
      await mutate()
    } catch (err: any) {
      setActionError(err.message || "Callback authorization failed")
    } finally {
      setLoadingAction(null)
    }
  }

  const status = auth?.status || "not_connected"

  const stateDetails: Record<
    string,
    { label: string; tone: "documented" | "reported" | "inferred" | undefined; badgeClass: string; desc: string }
  > = {
    not_connected: {
      label: "Not connected",
      tone: undefined,
      badgeClass: "bg-muted text-muted-foreground",
      desc: "Connect your ChatGPT / Codex account using official OpenAI OAuth and PKCE verification.",
    },
    connecting: {
      label: "Connecting",
      tone: "reported",
      badgeClass: "bg-amber-500/20 text-amber-300 border-amber-500/30",
      desc: "Awaiting authentication on auth.openai.com. Follow the prompt to complete login and OTP.",
    },
    connected: {
      label: "Connected",
      tone: "documented",
      badgeClass: "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
      desc: "Authenticated with OpenAI Codex. Credentials securely stored in auth_session.json.",
    },
    refreshing: {
      label: "Refreshing session",
      tone: "inferred",
      badgeClass: "bg-blue-500/20 text-blue-300 border-blue-500/30",
      desc: "Exchanging refresh token for a fresh access token without prompting for login.",
    },
    expired: {
      label: "Authentication expired",
      tone: "reported",
      badgeClass: "bg-destructive/20 text-destructive border-destructive/30",
      desc: "Your access token has expired. Use the Refresh action or reconnect to resume requests.",
    },
    failed: {
      label: "Login failed",
      tone: "reported",
      badgeClass: "bg-destructive/20 text-destructive border-destructive/30",
      desc: auth?.error || "OAuth token exchange or verification encountered an error.",
    },
  }

  const current = stateDetails[status] || stateDetails.not_connected

  return (
    <Panel
      title="ChatGPT / Codex Authentication"
      actions={
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium border ${current.badgeClass}`}>
            <span
              className={`size-2 rounded-full ${
                status === "connected"
                  ? "bg-emerald-400"
                  : status === "connecting" || status === "refreshing"
                  ? "bg-amber-400 animate-pulse"
                  : status === "expired" || status === "failed"
                  ? "bg-red-400"
                  : "bg-zinc-400"
              }`}
            />
            {current.label}
          </span>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">{current.desc}</p>

        {actionError && (
          <div className="rounded border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
            {actionError}
          </div>
        )}

        {status === "not_connected" && (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              onClick={handleConnect}
              disabled={loadingAction === "connect"}
              className="bg-primary text-primary-foreground hover:bg-primary/90 font-medium"
            >
              {loadingAction === "connect" ? "Opening OpenAI Login…" : "Connect ChatGPT / Codex"}
            </Button>
            <p className="text-xs text-muted-foreground">
              Callback URI: <code className="font-mono text-foreground">http://localhost:1455/auth/callback</code>
            </p>
          </div>
        )}

        {status === "connecting" && (
          <div className="flex flex-col gap-3 rounded border border-border/80 bg-background/50 p-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-sm font-medium">Browser login in progress</span>
              <div className="flex gap-2">
                {auth?.auth_url && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => window.open(auth.auth_url!, "_blank")}
                  >
                    Open Login Page Again
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={handleDisconnect}
                >
                  Cancel
                </Button>
              </div>
            </div>
            <p className="text-xs text-muted-foreground leading-relaxed">
              Complete sign-in on OpenAI. Once authorized, the browser will redirect to{" "}
              <code className="font-mono text-foreground">http://localhost:1455/auth/callback</code>.
            </p>
            <div>
              <button
                type="button"
                onClick={() => setShowManual(!showManual)}
                className="text-xs text-primary hover:underline"
              >
                {showManual ? "Hide manual callback entry" : "Redirect didn't catch? Paste callback URL manually"}
              </button>
              {showManual && (
                <form onSubmit={handleManualSubmit} className="mt-2 flex gap-2">
                  <input
                    type="text"
                    value={manualUrl}
                    onChange={(e) => setManualUrl(e.target.value)}
                    placeholder="http://localhost:1455/auth/callback?code=...&state=..."
                    className="flex-1 rounded border border-input bg-background px-3 py-1.5 font-mono text-xs"
                  />
                  <Button size="sm" type="submit" disabled={loadingAction === "exchange"}>
                    {loadingAction === "exchange" ? "Exchanging…" : "Submit"}
                  </Button>
                </form>
              )}
            </div>
          </div>
        )}

        {status === "connected" && (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <div className="rounded border border-border/60 bg-background/40 p-2.5">
                <p className="text-xs text-muted-foreground">Account Email</p>
                <p className="mt-1 font-mono text-sm truncate">{auth?.account?.email || "Authenticated User"}</p>
              </div>
              <div className="rounded border border-border/60 bg-background/40 p-2.5">
                <p className="text-xs text-muted-foreground">Account ID</p>
                <p className="mt-1 font-mono text-sm truncate">{auth?.account?.account_id || "Standard Codex"}</p>
              </div>
              <div className="rounded border border-border/60 bg-background/40 p-2.5">
                <p className="text-xs text-muted-foreground">Session Expiry</p>
                <p className="mt-1 font-mono text-sm">
                  {auth?.expires_at ? new Date(auth.expires_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "Active"}
                </p>
              </div>
            </div>

            <div className="flex flex-col gap-2 rounded border border-border/60 bg-background/30 p-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex flex-1 flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
                <label htmlFor="model-select" className="text-xs font-medium text-muted-foreground whitespace-nowrap">
                  Selected Model:
                </label>
                <select
                  id="model-select"
                  value={auth?.selected_model || "gpt-5.6-terra"}
                  onChange={(e) => handleSelectModel(e.target.value)}
                  disabled={loadingAction === "select-model"}
                  className="rounded border border-input bg-background px-3 py-1.5 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                >
                  {(auth?.available_models && auth.available_models.length > 0
                    ? auth.available_models
                    : ["gpt-5.6-terra", "gpt-5-turbo", "o1", "o3-mini", "gpt-4o"]
                  ).map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
                <span className="text-xs text-muted-foreground">
                  (Persisted in <code className="font-mono text-foreground">auth_session.json</code>)
                </span>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-border/60">
              <Button
                size="sm"
                variant="outline"
                onClick={handleRefreshModels}
                disabled={loadingAction === "refresh-models"}
              >
                {loadingAction === "refresh-models" ? "Refreshing Models…" : "Refresh Models"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={handleConnect}
                disabled={loadingAction === "connect"}
              >
                Reconnect
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="text-destructive hover:bg-destructive/10"
                onClick={handleDisconnect}
                disabled={loadingAction === "disconnect"}
              >
                Disconnect
              </Button>
            </div>
          </div>
        )}

        {(status === "expired" || status === "failed") && (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              size="sm"
              onClick={handleRefreshSession}
              disabled={loadingAction === "refresh-session"}
            >
              {loadingAction === "refresh-session" ? "Refreshing…" : "Refresh Session"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={handleConnect}
              disabled={loadingAction === "connect"}
            >
              Reconnect
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={handleDisconnect}
              disabled={loadingAction === "disconnect"}
            >
              Disconnect
            </Button>
          </div>
        )}
      </div>
    </Panel>
  )
}

type Investigation = { id: number; title: string; notes: string | null; updated_at: string; item_count?: number
  items?: { id: number; item_type: string; ref: string; note: string | null; entity: EntitySummary | null }[] }

export function InvestigationsView({ onOpen }: { onOpen: (id: string) => void }) {
  const list = useSWR<{ items: Investigation[] }>("/api/investigations", fetcher)
  const [selected, setSelected] = useState<number | null>(null)
  const detail = useSWR<Investigation>(selected ? `/api/investigations/${selected}` : null, fetcher)
  const [title, setTitle] = useState("")
  const [note, setNote] = useState("")

  const create = async (ev: React.FormEvent) => {
    ev.preventDefault()
    if (!title.trim()) return
    const inv = await apiPost<Investigation>("/api/investigations", { title })
    setTitle(""); setSelected(inv.id); list.mutate()
  }
  const addNote = async (ev: React.FormEvent) => {
    ev.preventDefault()
    if (!selected || !note.trim()) return
    await apiPost(`/api/investigations/${selected}/items`, { item_type: "note", ref: "note", note })
    setNote(""); detail.mutate(); list.mutate()
  }
  const remove = async (itemId: number) => {
    await fetch(`/api/investigations/${selected}/items/${itemId}`, { method: "DELETE" })
    detail.mutate(); list.mutate()
  }

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Panel title="Saved investigations">
        <form onSubmit={create} className="mb-3 flex gap-2">
          <label htmlFor="inv-title" className="sr-only">New investigation title</label>
          <input id="inv-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="New investigation" maxLength={200}
            className="flex-1 rounded border border-input bg-background px-2 py-1.5 text-sm" />
          <Button size="sm" type="submit">Create</Button>
        </form>
        {!list.data ? <StateMessage error={list.error} loading={list.isLoading} /> : (
          <ul className="flex flex-col gap-1">{list.data.items.map((i) => (
            <li key={i.id}><button type="button" onClick={() => setSelected(i.id)}
              className={`w-full rounded px-2 py-1.5 text-left text-sm ${selected === i.id ? "bg-accent" : "hover:bg-accent/60"}`}>
              {i.title} <span className="text-xs text-muted-foreground">({i.item_count})</span></button></li>
          ))}</ul>
        )}
      </Panel>
      <Panel title={detail.data?.title ?? "Investigation"} className="lg:col-span-2">
        {!selected ? <StateMessage empty="Select or create an investigation. Use “Add to investigation” on any entity page." /> :
          !detail.data ? <StateMessage error={detail.error} loading={detail.isLoading} /> : (
            <div className="flex flex-col gap-3">
              <ul className="flex flex-col gap-2">{detail.data.items?.map((it) => (
                <li key={it.id} className="flex items-start justify-between gap-2 rounded border border-border/60 p-2 text-sm">
                  <div className="flex flex-col gap-1"><Badge>{it.item_type}</Badge>
                    {it.entity ? <EntityChip entity={it.entity} onOpen={onOpen} /> : it.item_type !== "note" && <span className="font-mono">{it.ref}</span>}
                    {it.note && <p className="text-muted-foreground">{it.note}</p>}</div>
                  <Button size="sm" variant="ghost" onClick={() => remove(it.id)}>Remove</Button>
                </li>
              ))}</ul>
              <form onSubmit={addNote} className="flex gap-2">
                <label htmlFor="inv-note" className="sr-only">Add note</label>
                <input id="inv-note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add an analyst note" maxLength={2000}
                  className="flex-1 rounded border border-input bg-background px-2 py-1.5 text-sm" />
                <Button size="sm" type="submit">Add note</Button>
              </form>
            </div>
          )}
      </Panel>
    </div>
  )
}

export async function addEntityToLatestInvestigation(entityId: string): Promise<string> {
  const list = await fetcher<{ items: Investigation[] }>("/api/investigations")
  const target = list.items[0] ?? (await apiPost<Investigation>("/api/investigations", { title: "Untitled investigation" }))
  await apiPost(`/api/investigations/${target.id}/items`, { item_type: "entity", ref: entityId })
  return target.title
}
