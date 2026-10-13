"use client"

import { useRef, useState } from "react"
import useSWR, { mutate } from "swr"

import { Button } from "@/components/ui/button"
import { ApiError, fetcher } from "@/lib/api"

import { Badge, Panel, StateMessage } from "./bits"

type LoadResult = { run_id: number; file: string; new?: number; changed?: number; unchanged?: number; errors?: number }
type Job = {
  id: string
  filename: string
  status: "running" | "done" | "failed"
  current_file: string | null
  results: LoadResult[]
  error: string | null
}

const DATA_GOV_URL = "https://www.data.gov.in/catalog/company-master-data"

export function McaUploadPanel() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const jobs = useSWR<{ jobs: Job[] }>("/api/sources/mca/jobs", fetcher, {
    refreshInterval: (data) => (data?.jobs.some((j) => j.status === "running") ? 2000 : 0),
    onSuccess: (data) => {
      if (!data.jobs.some((j) => j.status === "running")) mutate("/api/stats")
    },
  })
  const running = jobs.data?.jobs.some((j) => j.status === "running") ?? false

  const upload = async (ev: React.FormEvent) => {
    ev.preventDefault()
    if (!file) return
    setSending(true)
    setError(null)
    try {
      const res = await fetch(`/api/sources/mca/upload?filename=${encodeURIComponent(file.name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: file,
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new ApiError(typeof body?.detail === "string" ? body.detail : res.statusText, res.status)
      }
      setFile(null)
      if (inputRef.current) inputRef.current.value = ""
      await jobs.mutate()
    } catch (e) {
      setError(e)
    } finally {
      setSending(false)
    }
  }

  return (
    <Panel title="Load MCA company master data">
      <div className="flex flex-col gap-3 text-sm">
        <p className="text-muted-foreground leading-relaxed">
          {"Download a state's Company Master CSV from "}
          <a href={DATA_GOV_URL} target="_blank" rel="noreferrer" className="text-foreground underline underline-offset-2">data.gov.in</a>
          {" (Karnataka for CINs containing "}<span className="font-mono text-foreground">KA</span>
          {") and upload it here (max 300 MB; zip larger files). Loading uses no crawler credits or AI requests, and re-uploading the same file is safe."}
        </p>
        <form onSubmit={upload} className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <label htmlFor="mca-file" className="sr-only">MCA Company Master CSV or ZIP</label>
          <input
            id="mca-file"
            ref={inputRef}
            type="file"
            accept=".csv,.zip,text/csv,application/zip"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="flex-1 text-sm file:mr-3 file:rounded file:border file:border-border file:bg-secondary file:px-3 file:py-1.5 file:text-sm file:text-secondary-foreground"
          />
          <Button type="submit" disabled={!file || sending || running}>
            {sending ? "Uploading…" : running ? "Loading…" : "Upload and load"}
          </Button>
        </form>
        {file && <p className="font-mono text-xs text-muted-foreground">{file.name} · {(file.size / (1024 * 1024)).toFixed(1)} MB</p>}
        <StateMessage error={error} />
        {jobs.data && jobs.data.jobs.length > 0 && (
          <ul className="flex flex-col gap-2" aria-live="polite">
            {jobs.data.jobs.map((j) => (
              <li key={j.id} className="flex flex-col gap-1 rounded border border-border/60 bg-background/40 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={j.status === "done" ? "documented" : j.status === "failed" ? "reported" : undefined}>{j.status}</Badge>
                  <span className="font-mono text-xs">{j.filename}</span>
                  {j.status === "running" && j.current_file && <span className="text-xs text-muted-foreground">loading {j.current_file}…</span>}
                </div>
                {j.results.map((r) => (
                  <p key={r.run_id} className="font-mono text-xs text-muted-foreground">
                    {`run #${r.run_id} · ${r.file}: ${r.new ?? 0} new, ${r.changed ?? 0} changed, ${r.unchanged ?? 0} unchanged, ${r.errors ?? 0} bad rows`}
                  </p>
                ))}
                {j.error && <p className="text-xs text-destructive">{j.error}</p>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  )
}
