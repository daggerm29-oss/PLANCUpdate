"use client"

import { Activity, Bot, Briefcase, ChevronRight, FileSearch, GitCompareArrows, ListChecks, Radar, Search } from "lucide-react"
import { useState } from "react"
import useSWR from "swr"

import { Button } from "@/components/ui/button"
import { apiPost, type CodexAuthSession, fetcher } from "@/lib/api"
import { cn } from "@/lib/utils"

import { LeadsView, PathsView, RelationshipInspector, ReviewView } from "./analysis-views"
import { StateMessage } from "./bits"
import { EntityView } from "./entity-view"
import { IntelView } from "./intel-view"
import { SearchBox } from "./search-box"
import { addEntityToLatestInvestigation, CodexAuthPanel, InvestigationsView, SystemView } from "./system-views"

const VIEWS = [
  { id: "intel", label: "Intelligence", icon: FileSearch },
  { id: "entity", label: "Entity explorer", icon: Search },
  { id: "paths", label: "Connection paths", icon: GitCompareArrows },
  { id: "leads", label: "Leads", icon: Radar },
  { id: "review", label: "Match review", icon: ListChecks },
  { id: "investigations", label: "Investigations", icon: Briefcase },
  { id: "ai_auth", label: "ChatGPT / Codex", icon: Bot },
  { id: "system", label: "Sources & AI budget", icon: Activity },
] as const
type View = (typeof VIEWS)[number]["id"]

function SidebarCodexWidget({ onOpen }: { onOpen: () => void }) {
  const { data: auth } = useSWR<CodexAuthSession>("/api/ai/auth/status", fetcher, { refreshInterval: 4000 })
  const status = auth?.status || "not_connected"

  const handleConnect = async (e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      const res = await apiPost<{ ok: boolean; auth_url: string }>("/api/ai/auth/login", {})
      if (res?.auth_url) {
        window.open(res.auth_url, "_blank")
      }
    } catch {}
    onOpen()
  }

  return (
    <div
      onClick={onOpen}
      role="button"
      tabIndex={0}
      className="cursor-pointer rounded-lg border border-border/80 bg-background/60 p-3 transition hover:border-primary/50 hover:bg-background/90"
    >
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-foreground">
          <Bot className="size-3.5 text-primary" /> ChatGPT / Codex
        </span>
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
      </div>

      {status === "not_connected" && (
        <div className="mt-2.5 flex flex-col gap-1.5">
          <p className="text-[11px] leading-tight text-muted-foreground">Sign in with OpenAI account</p>
          <Button
            size="sm"
            onClick={handleConnect}
            className="w-full bg-primary text-primary-foreground hover:bg-primary/90 h-7 text-xs font-semibold"
          >
            Connect ChatGPT / Codex
          </Button>
        </div>
      )}

      {status === "connecting" && (
        <div className="mt-2 text-xs text-amber-300">
          <p className="font-medium animate-pulse">Connecting to OpenAI…</p>
          <p className="text-[10px] text-muted-foreground mt-0.5">Click to view status</p>
        </div>
      )}

      {status === "connected" && (
        <div className="mt-2 flex items-center justify-between text-xs">
          <div>
            <p className="font-medium text-emerald-400">Connected</p>
            <p className="font-mono text-[11px] text-muted-foreground truncate max-w-[170px]">
              {auth?.selected_model || "gpt-5.6-terra"}
            </p>
          </div>
          <ChevronRight className="size-4 text-muted-foreground" />
        </div>
      )}

      {(status === "expired" || status === "failed") && (
        <div className="mt-2 flex flex-col gap-1">
          <p className="text-xs text-destructive font-medium">
            {status === "expired" ? "Session Expired" : "Login Failed"}
          </p>
          <Button size="sm" variant="outline" onClick={handleConnect} className="h-6 text-[11px]">
            Reconnect
          </Button>
        </div>
      )}
    </div>
  )
}

function TopBarCodexStatus({ onOpen }: { onOpen: () => void }) {
  const { data: auth } = useSWR<CodexAuthSession>("/api/ai/auth/status", fetcher, { refreshInterval: 4000 })
  const status = auth?.status || "not_connected"

  const handleConnect = async () => {
    try {
      const res = await apiPost<{ ok: boolean; auth_url: string }>("/api/ai/auth/login", {})
      if (res?.auth_url) {
        window.open(res.auth_url, "_blank")
      }
    } catch {}
    onOpen()
  }

  return (
    <div className="flex items-center gap-2">
      {status === "not_connected" ? (
        <Button
          size="sm"
          onClick={handleConnect}
          className="bg-primary text-primary-foreground hover:bg-primary/90 h-8 gap-1.5 text-xs font-semibold shadow-sm"
        >
          <Bot className="size-3.5" />
          Connect ChatGPT / Codex
        </Button>
      ) : status === "connecting" ? (
        <Button size="sm" variant="outline" onClick={onOpen} className="h-8 gap-1.5 text-xs border-amber-500/40 text-amber-300">
          <span className="size-2 rounded-full bg-amber-400 animate-pulse" />
          Connecting to OpenAI…
        </Button>
      ) : status === "connected" ? (
        <Button size="sm" variant="outline" onClick={onOpen} className="h-8 gap-1.5 text-xs border-emerald-500/40 bg-emerald-500/10">
          <span className="size-2 rounded-full bg-emerald-400" />
          <span className="font-medium text-emerald-300">ChatGPT Connected</span>
          <span className="font-mono text-xs text-muted-foreground">({auth?.selected_model || "gpt-5.6-terra"})</span>
        </Button>
      ) : (
        <Button size="sm" variant="outline" onClick={handleConnect} className="h-8 gap-1.5 text-xs border-destructive/40 text-destructive">
          <span className="size-2 rounded-full bg-red-400" />
          Reconnect ChatGPT
        </Button>
      )}
    </div>
  )
}

export function Workspace() {
  const [view, setView] = useState<View>("intel")
  const [entityId, setEntityId] = useState<string | null>(null)
  const [relId, setRelId] = useState<number | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const open = (id: string) => { setEntityId(id); setView("entity"); setRelId(null) }
  const save = async (id: string) => {
    try { setToast(`Added to “${await addEntityToLatestInvestigation(id)}”`) } catch (e) { setToast((e as Error).message) }
    setTimeout(() => setToast(null), 3000)
  }

  return (
    <div className="flex min-h-dvh flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col gap-4 border-b border-border bg-sidebar p-4 md:w-72 md:border-b-0 md:border-r">
        <div>
          <p className="font-mono text-xs uppercase tracking-widest text-primary">PLANB</p>
          <p className="text-sm font-semibold">Financial intelligence workspace</p>
        </div>
        <SearchBox onPick={(e) => open(e.id)} />
        <SidebarCodexWidget onOpen={() => setView("ai_auth")} />
        <nav aria-label="Workspace" className="flex flex-row flex-wrap gap-1 md:flex-col">
          {VIEWS.map((v) => (
            <button key={v.id} type="button" onClick={() => setView(v.id)} aria-current={view === v.id ? "page" : undefined}
              className={cn("flex items-center gap-2 rounded px-2.5 py-1.5 text-sm",
                view === v.id ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium" : "text-muted-foreground hover:text-foreground")}>
              <v.icon className="size-4" aria-hidden />{v.label}
            </button>
          ))}
        </nav>
        <p className="mt-auto hidden text-xs leading-relaxed text-muted-foreground md:block">
          Relationships, shared attributes and name matches are leads for review, not proof of wrongdoing.
        </p>
      </aside>

      <main className="min-w-0 flex-1 p-4 md:p-6">
        <header className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-border/60 pb-3">
          <div className="flex items-center gap-2">
            <h1 className="text-base font-semibold tracking-tight text-foreground">
              {VIEWS.find((v) => v.id === view)?.label}
            </h1>
          </div>
          <TopBarCodexStatus onOpen={() => setView("ai_auth")} />
        </header>

        {view === "ai_auth" && <CodexAuthPanel />}
        {view === "entity" && (entityId
          ? <EntityView key={entityId} entityId={entityId} onOpen={open} onEdge={setRelId} onSave={save} />
          : <StateMessage empty="Search for a company, person, LEI or IMO number in the sidebar to open its profile." />)}
        {view === "intel" && <IntelView onOpen={open} />}
        {view === "paths" && <PathsView onOpen={open} onEdge={setRelId} />}
        {view === "leads" && <LeadsView onOpen={open} />}
        {view === "review" && <ReviewView onOpen={open} />}
        {view === "investigations" && <InvestigationsView onOpen={open} />}
        {view === "system" && <SystemView />}
      </main>

      {relId !== null && <RelationshipInspector relId={relId} onOpen={open} onClose={() => setRelId(null)} />}
      {toast && <div role="status" className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded border border-border bg-card px-4 py-2 text-sm shadow-lg">{toast}</div>}
    </div>
  )
}
