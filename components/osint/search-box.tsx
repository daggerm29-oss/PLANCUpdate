"use client"

import { Search } from "lucide-react"
import { useDeferredValue, useId, useState } from "react"
import useSWR from "swr"

import { type EntitySummary, fetcher } from "@/lib/api"

import { Badge, StateMessage } from "./bits"

export function SearchBox({ onPick, label = "Search entities" }: { onPick: (e: EntitySummary) => void; label?: string }) {
  const [q, setQ] = useState("")
  const deferred = useDeferredValue(q.trim())
  const id = useId()
  const { data, error } = useSWR<{ results: EntitySummary[] }>(
    deferred.length >= 2 ? `/api/search?q=${encodeURIComponent(deferred)}` : null, fetcher, { keepPreviousData: true })

  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="sr-only">{label}</label>
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <input id={id} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, LEI, IMO, entity id…" autoComplete="off"
          className="w-full rounded border border-input bg-background py-1.5 pl-8 pr-3 text-sm" />
      </div>
      {error && <StateMessage error={error} />}
      {deferred.length >= 2 && data && (
        <ul className="flex max-h-72 flex-col overflow-y-auto rounded border border-border" role="listbox" aria-label="Search results">
          {data.results.length === 0 && <li className="p-2 text-sm text-muted-foreground">No matches</li>}
          {data.results.map((e) => (
            <li key={e.id}>
              <button type="button" onClick={() => { onPick(e); setQ("") }}
                className="flex w-full flex-col items-start gap-1 border-b border-border/60 px-2.5 py-2 text-left text-sm hover:bg-accent">
                <span className="font-medium">{e.name}</span>
                <span className="flex gap-1.5"><Badge>{e.kind}</Badge><Badge>{e.source_id}</Badge>{e.country && <Badge>{e.country}</Badge>}{e.sanctioned && <Badge tone="reported">sanction</Badge>}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
