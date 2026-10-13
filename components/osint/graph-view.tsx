"use client"

import useSWR from "swr"

import { enc, type EntitySummary, fetcher, topicsOf } from "@/lib/api"

import { StateMessage } from "./bits"

type Edge = { id: number; subject_id: string; object_id: string; predicate: string; basis: string; label: string }
type GraphData = { root: string; nodes: EntitySummary[]; edges: Edge[] }

const BASIS_COLOR: Record<string, string> = { documented: "#34d399", reported: "#fbbf24", inferred: "#38bdf8" }

export function GraphView({ entityId, onOpen, onEdge }: {
  entityId: string
  onOpen: (id: string) => void
  onEdge: (relId: number) => void
}) {
  const { data, error, isLoading } = useSWR<GraphData>(`/api/entities/${enc(entityId)}/graph`, fetcher)
  if (!data) return <StateMessage error={error} loading={isLoading} />
  if (!data.edges.length) return <StateMessage empty="No relationships recorded for this entity." />

  const size = 520
  const c = size / 2
  const others = data.nodes.filter((n) => n.id !== data.root)
  const pos: Record<string, { x: number; y: number }> = { [data.root]: { x: c, y: c } }
  others.forEach((n, i) => {
    const a = (2 * Math.PI * i) / others.length - Math.PI / 2
    pos[n.id] = { x: c + Math.cos(a) * (c - 70), y: c + Math.sin(a) * (c - 70) }
  })

  return (
    <div className="flex flex-col gap-2">
      <svg viewBox={`0 0 ${size} ${size}`} className="mx-auto w-full max-w-xl" role="img" aria-label="Relationship graph">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="22" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" className="text-muted-foreground" />
          </marker>
        </defs>
        {data.edges.map((e) => {
          const a = pos[e.subject_id]
          const b = pos[e.object_id]
          if (!a || !b) return null
          return (
            <g key={e.id} className="cursor-pointer" onClick={() => onEdge(e.id)}>
              <title>{`${e.label} (${e.basis}) — click to inspect evidence`}</title>
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={BASIS_COLOR[e.basis] ?? "#888"} strokeWidth={2}
                strokeDasharray={e.basis === "documented" ? undefined : "5 4"} markerEnd="url(#arrow)" />
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="transparent" strokeWidth={12} />
              <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4} textAnchor="middle" className="fill-muted-foreground text-[9px]">
                {e.predicate}
              </text>
            </g>
          )
        })}
        {data.nodes.map((n) => {
          const p = pos[n.id]
          const root = n.id === data.root
          const flagged = n.sanctioned || topicsOf(n).some((t) => t.startsWith("sanction"))
          return (
            <g key={n.id} className="cursor-pointer" onClick={() => onOpen(n.id)}>
              <title>{n.name}</title>
              <circle cx={p.x} cy={p.y} r={root ? 14 : 10}
                className={root ? "fill-primary" : flagged ? "fill-amber-500" : "fill-secondary stroke-border"} strokeWidth={1} />
              <text x={p.x} y={p.y + (root ? 28 : 24)} textAnchor="middle" className="fill-foreground text-[10px]">
                {n.name.length > 26 ? `${n.name.slice(0, 25)}…` : n.name}
              </text>
            </g>
          )
        })}
      </svg>
      <p className="text-center text-xs text-muted-foreground">
        Solid green = documented · dashed amber = reported · dashed blue = inferred. Click a node to open it, an edge to inspect its evidence.
      </p>
    </div>
  )
}
