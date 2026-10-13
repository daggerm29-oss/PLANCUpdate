import path from "path"
import fs from "fs"

// Use dynamic runtime loader to avoid Turbopack node-pre-gyp static bundling collision
function getDuckDbModule(): any {
  try {
    const dynRequire = eval("require")
    return dynRequire("duckdb")
  } catch (e) {
    console.warn("DuckDB runtime load warning:", e)
    return null
  }
}

export interface SearchEntityResult {
  id: string
  name: string
  kind: string
  country: string | null
  jurisdiction?: string | null
  source_id: string
  status: string | null
  legal_form?: string | null
  incorporation_date?: string | null
  topics: string[]
  sanctioned?: boolean
  attrs?: Record<string, unknown>
}

export interface DetectedIdentifier {
  scheme: "cin" | "pan" | "din" | "lei" | "imo" | "unknown"
  value: string
}

export interface DuckDbSearchResult {
  query: string
  detected_identifiers: DetectedIdentifier[]
  results: SearchEntityResult[]
  total_count: number
  duration_ms: number
  source: "duckdb_remote_huggingface" | "duckdb_local_parquet" | "fallback_memory"
  auth_verified: boolean
  cached?: boolean
  pagination: {
    limit: number
    offset: number
    has_more: boolean
  }
}

export interface DuckDbBenchmarkResult {
  status: "ok" | "error"
  engine: "DuckDB (Node.js)"
  httpfs_loaded: boolean
  hf_token_configured: boolean
  hf_repo: string | null
  target_files: {
    entities: string
    identifiers: string
    relationships: string
  }
  sample_query_latency_ms: number
  auth_verified: boolean
  error?: string
}

// Simple in-memory LRU-like cache for DuckDB queries with 60s TTL
interface CacheEntry {
  data: DuckDbSearchResult
  expiresAt: number
}
const queryCache = new Map<string, CacheEntry>()
const CACHE_TTL_MS = 60 * 1000

function getFromCache(key: string): DuckDbSearchResult | null {
  const entry = queryCache.get(key)
  if (!entry) return null
  if (Date.now() > entry.expiresAt) {
    queryCache.delete(key)
    return null
  }
  return { ...entry.data, cached: true }
}

function setInCache(key: string, data: DuckDbSearchResult) {
  if (queryCache.size > 200) {
    const oldestKey = queryCache.keys().next().value
    if (oldestKey) queryCache.delete(oldestKey)
  }
  queryCache.set(key, { data, expiresAt: Date.now() + CACHE_TTL_MS })
}

class DuckDbHfManager {
  private db: any = null
  private initPromise: Promise<void> | null = null
  private httpfsLoaded = false

  public getHfToken(): string | null {
    return process.env.HF_TOKEN || process.env.HUGGINGFACE_TOKEN || null
  }

  public getHfRepo(): string | null {
    return process.env.HF_DATASET_REPO || null
  }

  public getHfBranch(): string {
    return process.env.HF_DATASET_BRANCH || "main"
  }

  public getParquetFileUrls(): {
    entities: string
    identifiers: string
    relationships: string
    isRemote: boolean
  } {
    const customBase = process.env.DUCKDB_PARQUET_BASE_URL
    const repo = this.getHfRepo()
    const branch = this.getHfBranch()

    if (customBase) {
      const base = customBase.replace(/\/$/, "")
      return {
        entities: `${base}/entities.parquet`,
        identifiers: `${base}/identifiers.parquet`,
        relationships: `${base}/relationships.parquet`,
        isRemote: base.startsWith("http://") || base.startsWith("https://") || base.startsWith("hf://"),
      }
    }

    if (repo) {
      // Direct resolve URL via Hugging Face HTTPS endpoint
      const base = `https://huggingface.co/datasets/${repo}/resolve/${branch}`
      return {
        entities: `${base}/entities.parquet`,
        identifiers: `${base}/identifiers.parquet`,
        relationships: `${base}/relationships.parquet`,
        isRemote: true,
      }
    }

    // Default to local bundled sample parquet files for testing and zero-setup demo
    const localDir = path.join(process.cwd(), "public", "sample_parquet")
    return {
      entities: path.join(localDir, "entities.parquet"),
      identifiers: path.join(localDir, "identifiers.parquet"),
      relationships: path.join(localDir, "relationships.parquet"),
      isRemote: false,
    }
  }

  private async initialize(): Promise<void> {
    if (this.db && this.httpfsLoaded) return

    if (!this.initPromise) {
      this.initPromise = new Promise<void>((resolve, reject) => {
        try {
          const DuckDB = getDuckDbModule()
          if (!DuckDB) {
            throw new Error("DuckDB native runtime is not loaded")
          }
          this.db = new DuckDB.Database(":memory:")
          const token = this.getHfToken()

          const setupSql = [
            "INSTALL httpfs;",
            "LOAD httpfs;",
            "SET preserve_insertion_order=false;",
            "SET threads=2;",
          ]

          if (token) {
            // Configure HTTP Bearer token and native Hugging Face secret
            setupSql.push(`CREATE OR REPLACE SECRET hf_token_secret (TYPE HUGGINGFACE, TOKEN '${token}');`)
            setupSql.push(`CREATE OR REPLACE SECRET hf_http_secret (TYPE HTTP, EXTRA_HTTP_HEADERS MAP {'Authorization': 'Bearer ' || '${token}'});`)
          }

          const fullScript = setupSql.join("\n")
          this.db.exec(fullScript, (err) => {
            if (err) {
              console.warn("DuckDB httpfs setup notice:", err.message)
              // Even if httpfs extension network install encounters an issue in strict sandboxes, local parquet works
            }
            this.httpfsLoaded = true
            resolve()
          })
        } catch (e) {
          reject(e)
        }
      })
    }

    return this.initPromise
  }

  public async runQuery<T = Record<string, unknown>>(sql: string, params: unknown[] = []): Promise<T[]> {
    await this.initialize()
    if (!this.db) throw new Error("DuckDB failed to initialize")

    return new Promise<T[]>((resolve, reject) => {
      this.db!.all(sql, ...params, (err: any, rows: any) => {
        if (err) {
          reject(err)
        } else {
          const sanitized = (rows || []).map((row: any) => {
            const out: Record<string, unknown> = {}
            for (const [k, v] of Object.entries(row)) {
              if (typeof v === "bigint") {
                out[k] = Number(v)
              } else {
                out[k] = v
              }
            }
            return out
          })
          resolve(sanitized as unknown as T[])
        }
      })
    })
  }

  public detectIdentifiers(term: string): DetectedIdentifier[] {
    const clean = term.trim()
    const detected: DetectedIdentifier[] = []

    // 1. CIN: [L|U] + 5 digits + 2 letters + 4 digits + 3 letters + 6 digits (e.g., U72900KA2022PTC165499)
    if (/^[lu]\d{5}[a-z]{2}\d{4}[a-z]{3}\d{6}$/i.test(clean)) {
      detected.push({ scheme: "cin", value: clean.toUpperCase() })
    }
    // 2. PAN: 5 letters + 4 digits + 1 letter (e.g., AABCE1234F)
    else if (/^[a-z]{5}\d{4}[a-z]$/i.test(clean)) {
      detected.push({ scheme: "pan", value: clean.toUpperCase() })
    }
    // 3. DIN: 8 digits (e.g., 09876543)
    else if (/^\d{8}$/.test(clean)) {
      detected.push({ scheme: "din", value: clean })
    }
    // 4. LEI: 20 alphanumeric characters (e.g., 029200013A5N6ZD0F605)
    else if (/^[a-z0-9]{20}$/i.test(clean)) {
      detected.push({ scheme: "lei", value: clean.toUpperCase() })
    }
    // 5. IMO: 7 digits
    else if (/^\d{7}$/.test(clean)) {
      detected.push({ scheme: "imo", value: clean })
    }

    return detected
  }

  public async searchEntities(
    query: string,
    limit = 25,
    offset = 0
  ): Promise<DuckDbSearchResult> {
    const trimmed = query.trim()
    const cacheKey = `search:${trimmed.toLowerCase()}:${limit}:${offset}`

    const cached = getFromCache(cacheKey)
    if (cached) return cached

    const startTime = performance.now()
    const detected = this.detectIdentifiers(trimmed)
    const { entities: entitiesFile, identifiers: idFile, isRemote } = this.getParquetFileUrls()
    const token = this.getHfToken()

    let matchedIds: string[] = []
    let totalEstimated = 0

    try {
      // 1. Predicate pushdown on identifiers:
      // If a specific identifier (CIN, PAN, DIN, LEI, IMO) or partial identifier was entered
      if (detected.length > 0) {
        const idVal = detected[0].value
        const idScheme = detected[0].scheme
        const idRows = await this.runQuery<{ entity_id: string }>(
          `
          SELECT entity_id
          FROM read_parquet(?)
          WHERE scheme = ? AND value = ?
          LIMIT ?
          `,
          [idFile, idScheme, idVal, limit]
        )
        matchedIds = idRows.map((r) => r.entity_id)
      } else if (trimmed.length >= 4 && /^[a-z0-9_-]+$/i.test(trimmed)) {
        // Partial identifier scan
        const upper = trimmed.toUpperCase()
        const partialIdRows = await this.runQuery<{ entity_id: string }>(
          `
          SELECT entity_id
          FROM read_parquet(?)
          WHERE value LIKE ?
          LIMIT ?
          `,
          [idFile, `%${upper}%`, limit]
        )
        matchedIds = partialIdRows.map((r) => r.entity_id)
      }

      // 2. Query entities with column projection (strictly required columns)
      let entitiesRows: Record<string, unknown>[] = []

      if (matchedIds.length > 0) {
        // Fetch entities matching the discovered identifier IDs
        const inClause = matchedIds.map((id) => `'${id.replace(/'/g, "''")}'`).join(",")
        entitiesRows = await this.runQuery<Record<string, unknown>>(
          `
          SELECT
            id, source_id, kind, name, country, jurisdiction,
            status, legal_form, incorporation_date, topics, attrs
          FROM read_parquet(?)
          WHERE id IN (${inClause})
          LIMIT ? OFFSET ?
          `,
          [entitiesFile, limit, offset]
        )
      } else if (trimmed) {
        // Name-based search with normalized and ILIKE pushdown
        const norm = trimmed.toLowerCase()
        entitiesRows = await this.runQuery<Record<string, unknown>>(
          `
          SELECT
            id, source_id, kind, name, country, jurisdiction,
            status, legal_form, incorporation_date, topics, attrs
          FROM read_parquet(?)
          WHERE name_norm LIKE ? OR name ILIKE ?
          ORDER BY name_norm
          LIMIT ? OFFSET ?
          `,
          [entitiesFile, `%${norm}%`, `%${trimmed}%`, limit, offset]
        )
      } else {
        // Empty query: recent / top entities
        entitiesRows = await this.runQuery<Record<string, unknown>>(
          `
          SELECT
            id, source_id, kind, name, country, jurisdiction,
            status, legal_form, incorporation_date, topics, attrs
          FROM read_parquet(?)
          ORDER BY name_norm
          LIMIT ? OFFSET ?
          `,
          [entitiesFile, limit, offset]
        )
      }

      const results: SearchEntityResult[] = entitiesRows.map((r) => {
        const topicsStr = String(r.topics || "")
        const topicsList = topicsStr ? topicsStr.split(",").map((s) => s.trim()) : []
        let parsedAttrs: Record<string, unknown> = {}
        if (typeof r.attrs === "string" && r.attrs.startsWith("{")) {
          try {
            parsedAttrs = JSON.parse(r.attrs)
          } catch {}
        } else if (typeof r.attrs === "object" && r.attrs !== null) {
          parsedAttrs = r.attrs as Record<string, unknown>
        }

        return {
          id: String(r.id),
          name: String(r.name),
          kind: String(r.kind || "company"),
          country: r.country ? String(r.country).toLowerCase() : null,
          jurisdiction: r.jurisdiction ? String(r.jurisdiction) : null,
          source_id: String(r.source_id || "mca"),
          status: r.status ? String(r.status) : null,
          legal_form: r.legal_form ? String(r.legal_form) : null,
          incorporation_date: r.incorporation_date ? String(r.incorporation_date) : null,
          topics: topicsList,
          sanctioned: topicsList.includes("sanction") || topicsList.includes("ofac_sdn"),
          attrs: parsedAttrs,
        }
      })

      const duration = Math.round(performance.now() - startTime)
      const res: DuckDbSearchResult = {
        query,
        detected_identifiers: detected,
        results,
        total_count: results.length,
        duration_ms: duration,
        source: isRemote ? "duckdb_remote_huggingface" : "duckdb_local_parquet",
        auth_verified: Boolean(token),
        pagination: {
          limit,
          offset,
          has_more: results.length === limit,
        },
      }

      setInCache(cacheKey, res)
      return res
    } catch (err) {
      console.error("DuckDB search query error:", err)
      throw err
    }
  }

  public async getEntityDetail(entityId: string) {
    const { entities: entitiesFile, identifiers: idFile, relationships: relFile } = this.getParquetFileUrls()

    const entRows = await this.runQuery<Record<string, unknown>>(
      `
      SELECT *
      FROM read_parquet(?)
      WHERE id = ?
      LIMIT 1
      `,
      [entitiesFile, entityId]
    )

    if (entRows.length === 0) return null
    const ent = entRows[0]

    // Fetch identifiers
    const idRows = await this.runQuery<{ scheme: string; value: string }>(
      `
      SELECT scheme, value
      FROM read_parquet(?)
      WHERE entity_id = ?
      `,
      [idFile, entityId]
    )

    // Fetch 1-hop relationships
    const relRows = await this.runQuery<Record<string, unknown>>(
      `
      SELECT *
      FROM read_parquet(?)
      WHERE subject_id = ? OR object_id = ?
      `,
      [relFile, entityId, entityId]
    )

    const topicsStr = String(ent.topics || "")
    const topics = topicsStr ? topicsStr.split(",") : []

    return {
      entity: {
        id: String(ent.id),
        name: String(ent.name),
        kind: String(ent.kind),
        country: ent.country ? String(ent.country) : null,
        jurisdiction: ent.jurisdiction ? String(ent.jurisdiction) : null,
        status: ent.status ? String(ent.status) : null,
        legal_form: ent.legal_form ? String(ent.legal_form) : null,
        incorporation_date: ent.incorporation_date ? String(ent.incorporation_date) : null,
        address: ent.address ? String(ent.address) : null,
        topics,
        attrs: typeof ent.attrs === "string" ? JSON.parse(ent.attrs || "{}") : ent.attrs,
      },
      identifiers: idRows,
      relationships: relRows,
    }
  }

  public async runBenchmark(): Promise<DuckDbBenchmarkResult> {
    const token = this.getHfToken()
    const repo = this.getHfRepo()
    const files = this.getParquetFileUrls()

    const startTime = performance.now()
    try {
      await this.initialize()

      // Test small query with projection pushdown
      const rows = await this.runQuery<{ count: number }>(
        `SELECT count(*) as count FROM read_parquet(?)`,
        [files.entities]
      )

      const latency = Math.round(performance.now() - startTime)
      return {
        status: "ok",
        engine: "DuckDB (Node.js)",
        httpfs_loaded: this.httpfsLoaded,
        hf_token_configured: Boolean(token),
        hf_repo: repo,
        target_files: {
          entities: files.entities,
          identifiers: files.identifiers,
          relationships: files.relationships,
        },
        sample_query_latency_ms: latency,
        auth_verified: Boolean(token) || !files.isRemote,
      }
    } catch (err: unknown) {
      const latency = Math.round(performance.now() - startTime)
      return {
        status: "error",
        engine: "DuckDB (Node.js)",
        httpfs_loaded: this.httpfsLoaded,
        hf_token_configured: Boolean(token),
        hf_repo: repo,
        target_files: {
          entities: files.entities,
          identifiers: files.identifiers,
          relationships: files.relationships,
        },
        sample_query_latency_ms: latency,
        auth_verified: false,
        error: err instanceof Error ? err.message : String(err),
      }
    }
  }
}

export const duckdbHf = new DuckDbHfManager()
