export type EntitySummary = {
  id: string
  name: string
  kind: string
  country: string | null
  source_id: string
  status: string | null
  topics?: string[] | string
  sanctioned?: boolean
}

export type Evidence = {
  eid?: string
  id?: number
  key?: string
  source_id: string
  url: string | null
  title: string | null
  excerpt: string | null
  basis?: string
  about?: string
}

export type AiStatus = { used: boolean; reason?: string; message?: string; cached?: boolean; retry_after?: number | null }

export type AiAnswer = {
  summary: string | null
  claims: { text: string; citations: string[]; basis: string }[]
  uncertainties: string[]
  next_steps: string[]
  citation_check: { accepted: number; rejected: number; unknown_citations: string[] }
  disclaimer: string
} | null

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message)
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {}
    throw new ApiError(detail || `HTTP ${res.status}`, res.status)
  }
  return res.json() as Promise<T>
}

export const fetcher = <T = unknown>(url: string): Promise<T> => fetch(url).then((r) => handle<T>(r))

export function apiPost<T = unknown>(url: string, body: unknown): Promise<T> {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => handle<T>(r))
}

export const enc = (id: string) => id.split("/").map(encodeURIComponent).join("/")

export function topicsOf(e: EntitySummary): string[] {
  if (!e.topics) return []
  return Array.isArray(e.topics) ? e.topics : e.topics.split(",").filter(Boolean)
}

export type CodexAuthState =
  | "not_connected"
  | "connecting"
  | "connected"
  | "refreshing"
  | "expired"
  | "failed"

export type CodexAuthSession = {
  status: CodexAuthState
  account?: {
    email?: string | null
    account_id?: string | null
  } | null
  selected_model?: string | null
  available_models?: string[]
  expires_at?: number | null
  auth_url?: string | null
  error?: string | null
}
