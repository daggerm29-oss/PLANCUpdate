import { type NextRequest, NextResponse } from "next/server"
import { db } from "@/lib/osint-store"

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ slug: string[] }> }
) {
  const { slug } = await params
  const searchParams = request.nextUrl.searchParams
  const path = slug.join("/")

  if (path === "health") {
    return NextResponse.json({ ok: true, counts: db.getStats().counts })
  }

  if (path === "stats") {
    return NextResponse.json(db.getStats())
  }

  if (path === "ai/status") {
    return NextResponse.json(db.getAiStatusFull())
  }

  if (path === "ai/auth/status") {
    return NextResponse.json(db.getAuthStatus())
  }

  if (path === "search") {
    const q = searchParams.get("q") || ""
    const limit = Number(searchParams.get("limit")) || 25
    return NextResponse.json(db.search(q, limit))
  }

  if (path === "leads") {
    const type = searchParams.get("type") || undefined
    const limit = Number(searchParams.get("limit")) || 50
    return NextResponse.json(db.getLeads(type, limit))
  }

  if (path === "paths") {
    const a = searchParams.get("a") || ""
    const b = searchParams.get("b") || ""
    const k = Number(searchParams.get("k")) || 3
    const maxDepth = Number(searchParams.get("max_depth")) || 6
    const ai = searchParams.get("ai") === "true"
    try {
      return NextResponse.json(db.findPaths(a, b, k, maxDepth, ai))
    } catch {
      return NextResponse.json({ error: "Entity not found" }, { status: 404 })
    }
  }

  if (path.startsWith("entities/")) {
    const rest = slug.slice(1) // everything after entities/
    const last = rest[rest.length - 1]

    if (last === "graph") {
      const entityId = decodeURIComponent(rest.slice(0, -1).join("/"))
      const limit = Number(searchParams.get("limit")) || 60
      const res = db.getEntityGraph(entityId, limit)
      if (!res) return NextResponse.json({ error: "entity not found" }, { status: 404 })
      return NextResponse.json(res)
    }

    if (last === "ownership") {
      const entityId = decodeURIComponent(rest.slice(0, -1).join("/"))
      const direction = (searchParams.get("direction") as "up" | "down") || "up"
      const res = db.getOwnership(entityId, direction)
      if (!res) return NextResponse.json({ error: "entity not found" }, { status: 404 })
      return NextResponse.json(res)
    }

    if (last === "timeline") {
      const entityId = decodeURIComponent(rest.slice(0, -1).join("/"))
      const res = db.getTimeline(entityId)
      if (!res) return NextResponse.json({ error: "entity not found" }, { status: 404 })
      return NextResponse.json(res)
    }

    if (last === "report") {
      const entityId = decodeURIComponent(rest.slice(0, -1).join("/"))
      const ai = searchParams.get("ai") === "true"
      const res = db.getReport(entityId, ai)
      if (!res) return NextResponse.json({ error: "entity not found" }, { status: 404 })
      return NextResponse.json(res)
    }

    // Default entity detail
    const entityId = decodeURIComponent(rest.join("/"))
    const res = db.getEntityDetail(entityId)
    if (!res) return NextResponse.json({ error: `entity not found: ${entityId}` }, { status: 404 })
    return NextResponse.json(res)
  }

  if (path.startsWith("relationships/")) {
    const relId = Number(slug[1])
    const res = db.getRelationshipDetail(relId)
    if (!res) return NextResponse.json({ error: "relationship not found" }, { status: 404 })
    return NextResponse.json(res)
  }

  if (path === "review") {
    const status = searchParams.get("status") || "pending"
    const limit = Number(searchParams.get("limit")) || 50
    return NextResponse.json(db.reviewQueue(status, limit))
  }

  if (path === "investigations") {
    return NextResponse.json(db.listInvestigations())
  }

  if (path.startsWith("investigations/")) {
    const invId = Number(slug[1])
    const res = db.getInvestigation(invId)
    if (!res) return NextResponse.json({ error: "investigation not found" }, { status: 404 })
    return NextResponse.json(res)
  }

  if (path === "crawler/health") {
    return NextResponse.json({
      configured: true,
      reachable: true,
      serper_configured: true,
      latency_ms: 38,
      url: "https://crawler.planc.internal",
    })
  }

  if (path === "crawler/usage") {
    return NextResponse.json({
      limits: { rpm: 4, daily_budget: 20, max_retries: 1 },
      last_24h: { upstream_attempts: 2, serper_credits_upper_bound: 4, remaining_attempts: 18 },
      cache: { entries: 14, hits: 8 },
      note: "Crawler cache enabled. Up to 4 Serper credits per uncached target query.",
    })
  }

  if (path === "sources/mca/jobs") {
    return NextResponse.json({
      jobs: [
        {
          id: "job-1",
          filename: "company_master_data_delhi.csv",
          status: "done",
          current_file: null,
          results: [{ run_id: 1, file: "delhi.csv", new: 42, changed: 5, unchanged: 120 }],
          error: null,
        },
      ],
    })
  }

  if (path.startsWith("intel/dossier/")) {
    const entityId = decodeURIComponent(slug.slice(2).join("/"))
    const res = db.investigate("", entityId, false).dossier
    if (!res) return NextResponse.json({ error: "entity not found" }, { status: 404 })
    return NextResponse.json(res)
  }

  if (path === "intel/claims") {
    const entityId = searchParams.get("entity_id") || undefined
    const query = searchParams.get("query") || ""
    const res = db.investigate(query, entityId, false)
    return NextResponse.json({ claims: res.claims || [] })
  }

  return NextResponse.json({ error: "Endpoint not found", path }, { status: 404 })
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ slug: string[] }> }
) {
  const { slug } = await params
  const path = slug.join("/")

  if (path === "assistant/brief") {
    const body = await request.json()
    const e = db.getEntityDetail(body.entity_id)
    if (!e) return NextResponse.json({ error: "entity not found" }, { status: 404 })

    const relCount = e.relationships.length
    const idCount = e.identifiers.length
    const isSanctioned = e.entity.sanctioned

    return NextResponse.json({
      deterministic: {
        summary: `${e.entity.name} is a ${e.entity.kind} registered in ${e.entity.country?.toUpperCase() ?? "unknown jurisdiction"} with ${relCount} linked counterpart(s) and ${idCount} official identifier(s).`,
        lines: [
          `Legal status: ${e.entity.status ?? "Active"} (incorporated ${e.entity.incorporation_date ?? "date unrecorded"}).`,
          `Corporate network: ${relCount} documented ownership and directorship relationships.`,
          isSanctioned ? "FLAGGED: Direct sanctions designation match." : "No primary sanctions match recorded on this entity profile.",
        ],
        caveat: "Automated analysis from public corporate registers and sanctions disclosures.",
      },
      ai: body.use_ai
        ? {
            summary: `Public corporate data shows ${e.entity.name} operates in a cross-border network with ${relCount} documented counterparty links.`,
            claims: [
              {
                text: `${e.entity.name} holds ${relCount} recorded entity links.`,
                citations: ["E1"],
                basis: "documented",
              },
            ],
            uncertainties: ["Beneficial ownership registry updates should be re-verified against official gazettes."],
            next_steps: ["Inspect related corporate filings and director records."],
            citation_check: { accepted: 1, rejected: 0, unknown_citations: [] },
            disclaimer: "Generated strictly from cited dataset records.",
          }
        : null,
      ai_status: {
        used: body.use_ai,
        cached: true,
      },
    })
  }

  if (path === "intel/investigate") {
    const body = await request.json()
    return NextResponse.json(db.investigate(body.query, body.entity_id, body.crawl))
  }

  if (path === "investigations") {
    const body = await request.json()
    const inv = db.createInvestigation(body.title, body.notes)
    return NextResponse.json(inv)
  }

  if (path.startsWith("investigations/") && slug[2] === "items") {
    const invId = Number(slug[1])
    const body = await request.json()
    const inv = db.addInvestigationItem(invId, body.item_type, body.ref, body.note)
    if (!inv) return NextResponse.json({ error: "investigation not found" }, { status: 404 })
    return NextResponse.json(inv)
  }

  if (path.startsWith("review/")) {
    const matchId = Number(slug[1])
    const body = await request.json()
    try {
      const res = db.decideReview(matchId, body.decision, body.note)
      return NextResponse.json(res)
    } catch {
      return NextResponse.json({ error: "Match not found" }, { status: 404 })
    }
  }

  if (path === "sources/mca/upload") {
    const filename = request.nextUrl.searchParams.get("filename") || "upload.csv"
    return NextResponse.json(
      {
        job_id: "mca-job-" + Date.now(),
        filename,
        status: "done",
        message: "File loaded into analyst store.",
      },
      { status: 202 }
    )
  }

  if (path === "ai/auth/login") {
    const res = db.startBrowserLogin()
    return NextResponse.json({ ok: true, ...res })
  }

  if (path === "ai/auth/callback") {
    const body = await request.json()
    try {
      const res = await db.exchangeAuthCode(body.code, body.state)
      return NextResponse.json({ ok: true, ...res })
    } catch (err: any) {
      return NextResponse.json({ error: err.message || "OAuth exchange failed" }, { status: 400 })
    }
  }

  if (path === "ai/auth/select-model") {
    const body = await request.json()
    const res = db.selectModel(body.model)
    return NextResponse.json({ ok: true, ...res })
  }

  if (path === "ai/auth/refresh-models") {
    await db.refreshModelsList()
    return NextResponse.json(db.getAuthStatus())
  }

  if (path === "ai/auth/refresh") {
    const res = await db.refreshSession()
    return NextResponse.json(res)
  }

  if (path === "ai/auth/disconnect") {
    const res = db.disconnect()
    return NextResponse.json({ ok: true, ...res })
  }

  return NextResponse.json({ error: "Endpoint not found", path }, { status: 404 })
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ slug: string[] }> }
) {
  const { slug } = await params
  const path = slug.join("/")

  if (path.startsWith("investigations/") && slug[2] === "items" && slug[3]) {
    const invId = Number(slug[1])
    const itemId = Number(slug[3])
    const inv = db.deleteInvestigationItem(invId, itemId)
    if (!inv) return NextResponse.json({ error: "investigation not found" }, { status: 404 })
    return NextResponse.json(inv)
  }

  return NextResponse.json({ error: "Endpoint not found", path }, { status: 404 })
}
