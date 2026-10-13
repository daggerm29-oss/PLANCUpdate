import type { AiAnswer, AiStatus, EntitySummary, Evidence } from "./api"

export interface StoredEntity {
  id: string
  source_id: string
  kind: string
  name: string
  name_norm: string
  country: string | null
  jurisdiction?: string | null
  status: string | null
  legal_form?: string | null
  incorporation_date: string | null
  dissolution_date?: string | null
  address: string | null
  topics: string[]
  identifiers: { scheme: string; value: string }[]
  names: { name: string; name_type: string }[]
  attrs: Record<string, unknown>
}

export interface StoredRelationship {
  id: number
  source_id: string
  subject_id: string
  predicate: string
  object_id: string
  basis: string
  confidence: number
  start_date: string | null
  end_date: string | null
  status?: string | null
  raw_record_id?: number | null
  attrs?: Record<string, unknown>
}

export interface StoredEvidence {
  id: number
  key: string
  source_id: string
  url: string | null
  title: string | null
  excerpt: string | null
  entity_id?: string | null
  relationship_id?: number | null
  retrieved_at?: string
  raw_record_id?: number | null
}

export interface StoredMatch {
  id: number
  left_id: string
  right_id: string
  method: string
  score: number
  status: "pending" | "auto" | "confirmed" | "rejected"
  note?: string | null
  features: Record<string, unknown>
}

export interface StoredInvestigationItem {
  id: number
  item_type: "entity" | "relationship" | "path" | "note"
  ref: string
  note: string | null
  created_at: string
}

export interface StoredInvestigation {
  id: number
  title: string
  notes: string | null
  created_at: string
  updated_at: string
  items: StoredInvestigationItem[]
}

export interface StoredMcaJob {
  id: string
  filename: string
  status: "running" | "done" | "failed"
  current_file: string | null
  results: { run_id: number; file: string; new?: number; changed?: number; unchanged?: number; errors?: number }[]
  error: string | null
}

export const LABELS: Record<string, [string, string]> = {
  direct_parent: ["is directly consolidated by", "directly consolidates"],
  ultimate_parent: ["is ultimately consolidated by", "ultimately consolidates"],
  owns: ["owns", "is owned by"],
  director_of: ["is director of", "has director"],
  employed_by: ["is employed by", "employs"],
  member_of: ["is member of", "has member"],
  family_of: ["is family of", "is family of"],
  associate_of: ["is associate of", "is associate of"],
  represents: ["represents", "is represented by"],
  linked_to: ["is linked to", "is linked to"],
  succeeded_by: ["is succeeded by", "succeeds"],
  same_as: ["may be the same entity as", "may be the same entity as"],
  officer_of: ["is an officer of", "has officer"],
  intermediary_of: ["is an intermediary for", "used intermediary"],
  registered_address: ["is registered at", "is the registered address of"],
  similar_to: ["may be the same as (per ICIJ)", "may be the same as (per ICIJ)"],
  agent_of: ["is registered agent for", "has registered agent"],
}

export function edgeLabel(predicate: string, forward = true): string {
  const pair = LABELS[predicate]
  if (!pair) {
    const clean = predicate.replace(/_/g, " ")
    return forward ? clean : `${clean} (reverse)`
  }
  return forward ? pair[0] : pair[1]
}

const SHARED_ADDRESS = "1 Example Quay, Suite 100, Limassol, CY"

class OsintDatabase {
  entities = new Map<string, StoredEntity>()
  relationships = new Map<number, StoredRelationship>()
  evidence = new Map<number, StoredEvidence>()
  matches = new Map<number, StoredMatch>()
  investigations = new Map<number, StoredInvestigation>()
  mcaJobs = new Map<string, StoredMcaJob>()
  nextRelId = 11
  nextEvidenceId = 20
  nextMatchId = 3
  nextInvId = 2
  nextInvItemId = 4

  constructor() {
    this.seed()
    this.loadSessionFromDisk()
  }

  seed() {
    const initialEntities: StoredEntity[] = [
      {
        id: "demo:alpha",
        source_id: "demo",
        kind: "company",
        name: "Alpha Holdings Ltd",
        name_norm: "alpha holdings ltd",
        country: "gb",
        status: "ACTIVE",
        incorporation_date: "2011-03-04",
        address: SHARED_ADDRESS,
        topics: [],
        identifiers: [{ scheme: "lei", value: "DEMO00000000000000A1" }],
        names: [{ name: "Alpha Holdings Ltd", name_type: "legal" }],
        attrs: { synthetic: true },
      },
      {
        id: "demo:beta",
        source_id: "demo",
        kind: "company",
        name: "Beta Trading LLC",
        name_norm: "beta trading llc",
        country: "cy",
        status: "ACTIVE",
        incorporation_date: "2015-06-01",
        address: SHARED_ADDRESS,
        topics: [],
        identifiers: [],
        names: [{ name: "Beta Trading LLC", name_type: "legal" }],
        attrs: { synthetic: true },
      },
      {
        id: "demo:delta",
        source_id: "demo",
        kind: "company",
        name: "Delta Investments SA",
        name_norm: "delta investments sa",
        country: "pa",
        status: "ACTIVE",
        incorporation_date: "2016-01-20",
        address: SHARED_ADDRESS,
        topics: [],
        identifiers: [],
        names: [{ name: "Delta Investments SA", name_type: "legal" }],
        attrs: { synthetic: true },
      },
      {
        id: "demo:gamma",
        source_id: "demo",
        kind: "company",
        name: "Gamma Shipping Co",
        name_norm: "gamma shipping co",
        country: "ae",
        status: "ACTIVE",
        incorporation_date: "2014-09-09",
        address: null,
        topics: ["sanction"],
        identifiers: [{ scheme: "imo", value: "9999999" }],
        names: [{ name: "Gamma Shipping Co", name_type: "legal" }],
        attrs: { synthetic: true, sanctions: [{ authority: "OFAC", program: "SDGT", start_date: "2023-01-15" }] },
      },
      {
        id: "demo:epsilon",
        source_id: "demo",
        kind: "company",
        name: "Epsilon Logistics Ltd",
        name_norm: "epsilon logistics ltd",
        country: "gb",
        status: "ACTIVE",
        incorporation_date: "2018-11-11",
        address: null,
        topics: [],
        identifiers: [],
        names: [{ name: "Epsilon Logistics Ltd", name_type: "legal" }],
        attrs: { synthetic: true },
      },
      {
        id: "demo:person1",
        source_id: "demo",
        kind: "person",
        name: "Jordan Sample",
        name_norm: "jordan sample",
        country: "cy",
        status: "ACTIVE",
        incorporation_date: null,
        address: null,
        topics: [],
        identifiers: [],
        names: [{ name: "Jordan Sample", name_type: "legal" }],
        attrs: { synthetic: true },
      },
      {
        id: "demo:hub",
        source_id: "demo",
        kind: "organization",
        name: "Global Registered Agents Ltd",
        name_norm: "global registered agents ltd",
        country: "cy",
        status: "ACTIVE",
        incorporation_date: "2001-01-01",
        address: null,
        topics: [],
        identifiers: [],
        names: [{ name: "Global Registered Agents Ltd", name_type: "legal" }],
        attrs: { synthetic: true },
      },
    ]

    for (const e of initialEntities) {
      this.entities.set(e.id, e)
      const eid = this.evidence.size + 1
      this.evidence.set(eid, {
        id: eid,
        key: `demo:entity:${e.id}`,
        source_id: "demo",
        url: null,
        title: `Synthetic record: ${e.name}`,
        excerpt: `Fictional demo record for ${e.name}.`,
        entity_id: e.id,
        retrieved_at: "2026-10-12T00:00:00Z",
      })
    }

    const relDefs: [string, string, string, string, number, string | null][] = [
      ["demo:alpha", "owns", "demo:beta", "documented", 1.0, "2015-06-01"],
      ["demo:beta", "owns", "demo:delta", "documented", 1.0, "2016-02-01"],
      ["demo:delta", "owns", "demo:alpha", "reported", 0.7, "2019-05-01"],
      ["demo:person1", "director_of", "demo:alpha", "documented", 1.0, "2011-03-04"],
      ["demo:person1", "director_of", "demo:beta", "documented", 1.0, "2015-06-01"],
      ["demo:delta", "owns", "demo:gamma", "reported", 0.6, "2020-03-15"],
      ["demo:epsilon", "director_of", "demo:hub", "documented", 1.0, null],
      ["demo:hub", "agent_of", "demo:alpha", "documented", 1.0, null],
      ["demo:hub", "agent_of", "demo:epsilon", "documented", 1.0, null],
      ["demo:hub", "agent_of", "demo:gamma", "documented", 1.0, null],
    ]

    relDefs.forEach(([s, p, o, basis, conf, start], index) => {
      const id = index + 1
      this.relationships.set(id, {
        id,
        source_id: "demo",
        subject_id: s,
        predicate: p,
        object_id: o,
        basis,
        confidence: conf,
        start_date: start,
        end_date: null,
        status: "ACTIVE",
      })
      const evId = this.evidence.size + 1
      this.evidence.set(evId, {
        id: evId,
        key: `demo:rel:${s}:${p}:${o}`,
        source_id: "demo",
        url: null,
        title: `Synthetic ${p} relationship`,
        excerpt: `Fictional record: ${s} ${p} ${o} (${basis}, conf ${conf}).`,
        relationship_id: id,
        retrieved_at: "2026-10-12T00:00:00Z",
      })
    })

    this.matches.set(1, {
      id: 1,
      left_id: "demo:alpha",
      right_id: "demo:beta",
      method: "token_sort_ratio",
      score: 78.5,
      status: "pending",
      features: { shared_address: true, shared_director: "Jordan Sample" },
    })
    this.matches.set(2, {
      id: 2,
      left_id: "demo:gamma",
      right_id: "demo:delta",
      method: "partial_ratio",
      score: 64.0,
      status: "pending",
      features: { shared_agent: "Global Registered Agents Ltd" },
    })

    this.investigations.set(1, {
      id: 1,
      title: "Limassol Corporate Group Analysis",
      notes: "Investigating circular ownership cross-holdings between Alpha, Beta, and Delta.",
      created_at: "2026-10-12T08:00:00Z",
      updated_at: "2026-10-12T09:30:00Z",
      items: [
        {
          id: 1,
          item_type: "entity",
          ref: "demo:alpha",
          note: "Top holding company in UK",
          created_at: "2026-10-12T08:15:00Z",
        },
        {
          id: 2,
          item_type: "entity",
          ref: "demo:gamma",
          note: "Designated shipping company connected via Delta Investments",
          created_at: "2026-10-12T08:30:00Z",
        },
        {
          id: 3,
          item_type: "note",
          ref: "note",
          note: "Flagged cyclical loop: Alpha -> Beta -> Delta -> Alpha. Requires registry validation.",
          created_at: "2026-10-12T09:00:00Z",
        },
      ],
    })
  }

  summaryOf(e: StoredEntity): EntitySummary {
    return {
      id: e.id,
      name: e.name,
      kind: e.kind,
      country: e.country,
      source_id: e.source_id,
      status: e.status,
      topics: e.topics,
      sanctioned: e.topics.includes("sanction"),
    }
  }

  search(q: string, limit = 25) {
    const term = q.trim().toLowerCase()
    const detected: { scheme: string; value: string }[] = []

    if (/^[a-z0-9]{20}$/i.test(term)) {
      detected.push({ scheme: "lei", value: term.toUpperCase() })
    } else if (/^\d{7}$/.test(term)) {
      detected.push({ scheme: "imo", value: term })
    } else if (/^[lu]\d{5}[a-z]{2}\d{4}[a-z]{3}\d{6}$/i.test(term)) {
      detected.push({ scheme: "cin", value: term.toUpperCase() })
    }

    const matches: EntitySummary[] = []
    for (const e of this.entities.values()) {
      const matchName = e.name.toLowerCase().includes(term)
      const matchId = e.id.toLowerCase().includes(term)
      const matchIdent = e.identifiers.some(
        (i) => i.value.toLowerCase().includes(term) || (detected.length > 0 && i.scheme === detected[0].scheme && i.value.toLowerCase() === detected[0].value.toLowerCase())
      )
      if (matchName || matchId || matchIdent) {
        matches.push(this.summaryOf(e))
        if (matches.length >= limit) break
      }
    }

    return {
      query: q,
      detected_identifiers: detected,
      results: matches,
    }
  }

  getEntityDetail(entityId: string) {
    const e = this.entities.get(entityId)
    if (!e) return null

    const names = e.names
    const identifiers = e.identifiers
    const rels: Array<{
      id: number
      predicate: string
      label: string
      basis: string
      confidence: number
      source_id: string
      start_date: string | null
      end_date: string | null
      status: string | null
      forward: boolean
      other: EntitySummary | null
    }> = []

    for (const r of this.relationships.values()) {
      if (r.subject_id === entityId || r.object_id === entityId) {
        const forward = r.subject_id === entityId
        const otherId = forward ? r.object_id : r.subject_id
        const otherEntity = this.entities.get(otherId)
        rels.push({
          id: r.id,
          predicate: r.predicate,
          label: edgeLabel(r.predicate, forward),
          basis: r.basis,
          confidence: r.confidence,
          source_id: r.source_id,
          start_date: r.start_date,
          end_date: r.end_date,
          status: r.status ?? "ACTIVE",
          forward,
          other: otherEntity ? this.summaryOf(otherEntity) : null,
        })
      }
    }

    const ev = Array.from(this.evidence.values()).filter((item) => item.entity_id === entityId)
    const matches = Array.from(this.matches.values())
      .filter((m) => m.left_id === entityId || m.right_id === entityId)
      .map((m) => ({ id: m.id, method: m.method, score: m.score, status: m.status }))

    return {
      entity: {
        ...this.summaryOf(e),
        address: e.address,
        incorporation_date: e.incorporation_date,
        legal_form: e.legal_form ?? null,
      },
      names,
      identifiers,
      relationships: rels,
      evidence: ev,
      matches,
    }
  }

  getEntityGraph(entityId: string, limit = 60) {
    const e = this.entities.get(entityId)
    if (!e) return null

    const edges: Array<{
      id: number
      source_id: string
      subject_id: string
      predicate: string
      object_id: string
      basis: string
      confidence: number
      start_date: string | null
      end_date: string | null
      label: string
    }> = []
    const nodeIds = new Set<string>([entityId])

    for (const r of this.relationships.values()) {
      if (r.subject_id === entityId || r.object_id === entityId) {
        nodeIds.add(r.subject_id)
        nodeIds.add(r.object_id)
        edges.push({
          ...r,
          label: edgeLabel(r.predicate, true),
        })
        if (edges.length >= limit) break
      }
    }

    const nodes: EntitySummary[] = []
    for (const id of nodeIds) {
      const ent = this.entities.get(id)
      if (ent) nodes.push(this.summaryOf(ent))
    }

    return {
      root: entityId,
      nodes,
      edges,
    }
  }

  getOwnership(entityId: string, direction: "up" | "down" = "up") {
    const target = this.entities.get(entityId)
    if (!target) return null

    const nodes: Array<{
      id: string
      depth: number
      entity: EntitySummary
      edge: { id: number; label: string; basis: string } | null
    }> = [{ id: entityId, depth: 1, entity: this.summaryOf(target), edge: null }]

    const visited = new Set<string>([entityId])
    const queue: Array<{ id: string; depth: number }> = [{ id: entityId, depth: 1 }]

    while (queue.length > 0) {
      const curr = queue.shift()!
      if (curr.depth >= 6) continue

      for (const r of this.relationships.values()) {
        const isMatch =
          direction === "up"
            ? r.object_id === curr.id && (r.predicate === "owns" || r.predicate === "direct_parent" || r.predicate === "ultimate_parent")
            : r.subject_id === curr.id && (r.predicate === "owns" || r.predicate === "direct_parent" || r.predicate === "ultimate_parent")

        if (isMatch) {
          const nextId = direction === "up" ? r.subject_id : r.object_id
          if (!visited.has(nextId)) {
            visited.add(nextId)
            const ent = this.entities.get(nextId)
            if (ent) {
              const depth = curr.depth + 1
              nodes.push({
                id: nextId,
                depth,
                entity: this.summaryOf(ent),
                edge: { id: r.id, label: edgeLabel(r.predicate, direction === "down"), basis: r.basis },
              })
              queue.push({ id: nextId, depth })
            }
          }
        }
      }
    }

    return { nodes, truncated: false }
  }

  getTimeline(entityId: string) {
    const e = this.entities.get(entityId)
    if (!e) return null

    const events: Array<{ date: string; label: string; source_id: string; type: string }> = []
    if (e.incorporation_date) {
      events.push({ date: e.incorporation_date, label: "Entity incorporated", source_id: e.source_id, type: "incorporated" })
    }

    for (const r of this.relationships.values()) {
      if (r.subject_id === entityId || r.object_id === entityId) {
        if (r.start_date) {
          const forward = r.subject_id === entityId
          const otherId = forward ? r.object_id : r.subject_id
          const other = this.entities.get(otherId)?.name ?? otherId
          events.push({
            date: r.start_date,
            label: `${edgeLabel(r.predicate, forward)} ${other}`,
            source_id: r.source_id,
            type: "relationship_start",
          })
        }
      }
    }

    const sanctions = (e.attrs.sanctions as Array<{ authority?: string; program?: string; start_date?: string }>) || []
    for (const s of sanctions) {
      if (s.start_date) {
        events.push({
          date: s.start_date,
          label: `Listed under sanctions (${s.authority || "Authority"} ${s.program || ""})`,
          source_id: e.source_id,
          type: "sanction",
        })
      }
    }

    events.sort((a, b) => a.date.localeCompare(b.date))
    return { events, conflicts: [] }
  }

  getReport(entityId: string, useAi = false) {
    const e = this.entities.get(entityId)
    if (!e) return null

    const rels = Array.from(this.relationships.values()).filter(
      (r) => r.subject_id === entityId || r.object_id === entityId
    )

    const markdown = `# Intelligence Dossier: ${e.name}
**Entity ID**: \`${e.id}\`
**Classification**: ${e.kind.toUpperCase()} | **Country**: ${e.country?.toUpperCase() ?? "Unknown"}
**Status**: ${e.status ?? "Unrecorded"}
**Address**: ${e.address ?? "None recorded"}
**Incorporation Date**: ${e.incorporation_date ?? "Unknown"}

## Registry Identifiers
${e.identifiers.length ? e.identifiers.map((i) => `- **${i.scheme.toUpperCase()}**: \`${i.value}\``).join("\n") : "_No public identifiers registered._"}

## Documented & Reported Relationships (${rels.length})
${
  rels.length
    ? rels
        .map((r) => {
          const forward = r.subject_id === entityId
          const other = this.entities.get(forward ? r.object_id : r.subject_id)?.name ?? "Unknown"
          return `- **${edgeLabel(r.predicate, forward)}** ${other} *(basis: ${r.basis}, confidence: ${r.confidence.toFixed(2)}, start: ${r.start_date ?? "unknown"})*`
        })
        .join("\n")
    : "_No relationships recorded in public datasets._"
}

${
  e.topics.includes("sanction")
    ? `> **SANCTION NOTICE**: This entity matches designation criteria on public sanctions databases. Proximity or cross-ownership requires compliance scrutiny.`
    : ""
}

## Evidence Trace
Every relationship is supported by verifiable data extracts. This dossier is generated for investigatory review and does not constitute a finding of wrongdoing.
`

    return {
      markdown,
      ai_status: { used: useAi, reason: useAi ? undefined : "not_requested" },
    }
  }

  findPaths(a: string, b: string, k = 3, maxDepth = 6, useAi = false) {
    const entA = this.entities.get(a)
    const entB = this.entities.get(b)
    if (!entA || !entB) throw new Error("Entity not found")

    // Breadth-first search for paths between a and b
    const paths: Array<{
      cost: number
      hops: number
      nodes: EntitySummary[]
      edges: { id: number; label: string; basis: string }[]
      via_hubs: string[]
    }> = []

    // Queue of { current: string, pathNodes: string[], pathEdges: { id: number; label: string; basis: string }[], cost: number }
    const queue: Array<{
      current: string
      pathNodes: string[]
      pathEdges: { id: number; label: string; basis: string }[]
      cost: number
    }> = [{ current: a, pathNodes: [a], pathEdges: [], cost: 0 }]

    while (queue.length > 0 && paths.length < k) {
      const { current, pathNodes, pathEdges, cost } = queue.shift()!
      if (current === b && pathEdges.length > 0) {
        const nodes = pathNodes.map((id) => this.summaryOf(this.entities.get(id)!))
        const viaHubs = pathNodes.filter((id) => id === "demo:hub" && id !== a && id !== b)
        paths.push({
          cost: Math.round(cost * 10) / 10,
          hops: pathEdges.length,
          nodes,
          edges: pathEdges,
          via_hubs: viaHubs,
        })
        continue
      }

      if (pathNodes.length > maxDepth) continue

      for (const r of this.relationships.values()) {
        let nextId: string | null = null
        let forward = true

        if (r.subject_id === current) {
          nextId = r.object_id
          forward = true
        } else if (r.object_id === current) {
          nextId = r.subject_id
          forward = false
        }

        if (nextId && !pathNodes.includes(nextId)) {
          const edgeCost = (r.basis === "documented" ? 1.0 : r.basis === "reported" ? 1.6 : 2.0) + (nextId === "demo:hub" ? 1.5 : 0)
          queue.push({
            current: nextId,
            pathNodes: [...pathNodes, nextId],
            pathEdges: [...pathEdges, { id: r.id, label: edgeLabel(r.predicate, forward), basis: r.basis }],
            cost: cost + edgeCost,
          })
        }
      }
    }

    const evidenceList: Evidence[] = []
    let eidCounter = 1
    for (const p of paths) {
      for (const e of p.edges) {
        evidenceList.push({
          eid: `E${eidCounter++}`,
          id: e.id,
          key: `edge:${e.id}`,
          source_id: "demo",
          url: null,
          title: `Evidence for relationship: ${e.label}`,
          excerpt: `Verified registry filing documenting ${e.label} with basis ${e.basis}.`,
          basis: e.basis,
        })
      }
    }

    const aiAnswer: AiAnswer = useAi && paths.length > 0
      ? {
          summary: `${entA.name} and ${entB.name} are linked via a ${paths[0].hops}-hop ownership and intermediary chain.`,
          claims: [
            {
              text: `Path 1 establishes an indirect control chain across jurisdictions.`,
              citations: ["E1"],
              basis: paths[0].edges[0]?.basis || "documented",
            },
          ],
          uncertainties: ["Beneficial ownership filings should be reconciled with primary corporate registry certificates."],
          next_steps: ["Inspect registered office address filings in Cyprus and Panama."],
          citation_check: { accepted: 1, rejected: 0, unknown_citations: [] },
          disclaimer: "Model-written analysis limited strictly to cited evidence. Not a finding of wrongdoing.",
        }
      : null

    return {
      a: this.summaryOf(entA),
      b: this.summaryOf(entB),
      paths,
      deterministic: {
        summary:
          paths.length > 0
            ? `${paths.length} evidence-backed path(s) connect ${entA.name} and ${entB.name} within ${maxDepth} hops.`
            : `No connection path found within ${maxDepth} hops in the loaded dataset.`,
        caveat: "Relationships, shared agents and ownership links are leads for verification, not proof of wrongdoing.",
      },
      evidence: evidenceList,
      ai: aiAnswer,
      ai_status: {
        used: useAi,
        reason: useAi ? undefined : "not_requested",
        cached: true,
      },
    }
  }

  getLeads(type?: string, limit = 50) {
    const leads: Array<{
      type: string
      title: string
      score: number
      entities?: EntitySummary[]
      caveat?: string
    }> = []

    // Circular ownership lead (alpha -> beta -> delta -> alpha)
    leads.push({
      type: "circular_ownership",
      title: "Circular ownership structure between Alpha Holdings, Beta Trading & Delta Investments",
      score: 9.2,
      entities: [
        this.summaryOf(this.entities.get("demo:alpha")!),
        this.summaryOf(this.entities.get("demo:beta")!),
        this.summaryOf(this.entities.get("demo:delta")!),
      ],
      caveat: "Circular ownership structures can indicate cross-holdings, tax structures, or reporting irregularities.",
    })

    // Sanctions proximity lead (gamma)
    leads.push({
      type: "sanctions",
      title: "Sanctions exposure: Gamma Shipping Co designated under OFAC sanctions",
      score: 8.8,
      entities: [
        this.summaryOf(this.entities.get("demo:gamma")!),
        this.summaryOf(this.entities.get("demo:delta")!),
      ],
      caveat: "Proximity to designated entities requires sanctions compliance review under the 50% rule.",
    })

    // Shared address lead (Limassol)
    leads.push({
      type: "shared_address",
      title: "Shared address cluster at Limassol, Cyprus (3 entities)",
      score: 7.5,
      entities: [
        this.summaryOf(this.entities.get("demo:alpha")!),
        this.summaryOf(this.entities.get("demo:beta")!),
        this.summaryOf(this.entities.get("demo:delta")!),
      ],
      caveat: "Shared registered office addresses frequently belong to corporate service providers.",
    })

    // Shared director lead (Jordan Sample)
    leads.push({
      type: "shared_directors",
      title: "Shared director Jordan Sample sits on multiple related boards",
      score: 6.8,
      entities: [
        this.summaryOf(this.entities.get("demo:person1")!),
        this.summaryOf(this.entities.get("demo:alpha")!),
        this.summaryOf(this.entities.get("demo:beta")!),
      ],
      caveat: "Professional directors and corporate executives legitimately hold multiple directorships.",
    })

    const filtered = type ? leads.filter((l) => l.type === type || l.type.includes(type)) : leads
    return { leads: filtered.slice(0, limit) }
  }

  getRelationshipDetail(relId: number) {
    const r = this.relationships.get(relId)
    if (!r) return null

    const sub = this.entities.get(r.subject_id)
    const obj = this.entities.get(r.object_id)
    const ev = Array.from(this.evidence.values()).filter((e) => e.relationship_id === relId)

    return {
      relationship: {
        id: r.id,
        label: edgeLabel(r.predicate, true),
        basis: r.basis,
        confidence: r.confidence,
        source_id: r.source_id,
        start_date: r.start_date,
        end_date: r.end_date,
        status: r.status ?? "ACTIVE",
      },
      subject: sub ? this.summaryOf(sub) : null,
      object: obj ? this.summaryOf(obj) : null,
      evidence: ev,
      raw_record: {
        record_key: `rel-${r.id}`,
        url: null,
        last_fetched_at: "2026-10-12T00:00:00Z",
        payload: JSON.stringify({
          source: r.source_id,
          predicate: r.predicate,
          subject: r.subject_id,
          object: r.object_id,
          basis: r.basis,
          confidence: r.confidence,
        }, null, 2),
      },
    }
  }

  investigate(query: string, entityId?: string, crawl = false) {
    const searchRes = this.search(query)
    const targetId = entityId || searchRes.results[0]?.id || "demo:alpha"
    const target = this.entities.get(targetId)

    const claims: Array<{
      id: number
      claim_type: string
      field: string
      value: string
      status: "verified" | "unverified" | "conflict" | "related"
      source_url: string | null
      evidence_excerpt: string | null
      extraction_method: string
      confidence: number
      retrieved_at: string
    }> = [
      {
        id: 1,
        claim_type: "corporate_status",
        field: "status",
        value: target?.status || "ACTIVE",
        status: "verified",
        source_url: "https://gleif.org",
        evidence_excerpt: "Official registry entry confirms active legal status.",
        extraction_method: "gleif_sync",
        confidence: 0.99,
        retrieved_at: "2026-10-12T08:00:00Z",
      },
      {
        id: 2,
        claim_type: "jurisdiction",
        field: "country",
        value: target?.country?.toUpperCase() || "GB",
        status: "verified",
        source_url: "https://companieshouse.gov.uk",
        evidence_excerpt: "Incorporation documents filed under national company law.",
        extraction_method: "registry_record",
        confidence: 0.98,
        retrieved_at: "2026-10-12T08:00:00Z",
      },
    ]

    const dossier = target
      ? {
          entity: {
            ...this.summaryOf(target),
            address: target.address,
            incorporation_date: target.incorporation_date,
            legal_form: target.legal_form,
          },
          identifiers: target.identifiers,
          verified: {
            registry_evidence: [
              {
                key: `reg:${target.id}`,
                source_id: target.source_id,
                url: null,
                title: "Official Company Registry Extract",
                excerpt: `Certified extract for ${target.name}.`,
                retrieved_at: "2026-10-12T00:00:00Z",
              },
            ],
            claims,
          },
          connections: {
            same_identifier: [],
            parents: [],
            subsidiaries: [],
            ownership: [],
            people: [],
            possible_same_entity: [],
            flags: {
              own_topics: target.topics,
              linked: [],
            },
            shared_address_peers: {
              count: 3,
              sample: Array.from(this.entities.values())
                .filter((e) => e.address === target.address && e.id !== target.id)
                .map((e) => this.summaryOf(e)),
              capped: false,
            },
          },
          unverified_leads: [],
          caveats: [
            "Corporate and sanctions matches are leads requiring analyst verification.",
          ],
        }
      : null

    return {
      query,
      detected_identifiers: searchRes.detected_identifiers,
      subject_id: targetId,
      candidates: searchRes.results,
      crawl: crawl
        ? {
            ok: true,
            status: "complete",
            cached: true,
            query_sent: query,
            claims: 2,
            message: "Crawl completed using synthetic ledger cache.",
            fields_retrieved: ["address", "directors", "status"],
            serper_calls: { search: 1, scrape_gateway: 1 },
          }
        : null,
      dossier,
      claims,
    }
  }

  getAiStatus(): AiStatus {
    const auth = this.getAuthStatus()
    return {
      used: auth.status === "connected",
      message: auth.status === "connected"
        ? `Connected to ChatGPT / Codex (${auth.selected_model})`
        : `Codex status: ${auth.status}`,
    }
  }

  getAiStatusFull() {
    const auth = this.getAuthStatus()
    const isConnected = auth.status === "connected"
    return {
      provider: {
        provider: "codex",
        model: auth.selected_model || "gpt-5.6-terra",
        available: isConnected,
        reason: !isConnected ? `Status: ${auth.status}` : undefined,
      },
      auth,
      limits: {
        rpm_limit: 30,
        max_concurrent: 3,
        daily_requests: 500,
        cache_ttl: 86400,
      },
      today: {
        requests: isConnected ? 2 : 0,
        ok: isConnected ? 2 : 0,
        rate_limited: 0,
        failed: 0,
        total_tokens: isConnected ? 980 : 0,
        remaining_requests: 498,
      },
      window: {
        requests_last_60s: 0,
        next_slot_in_seconds: 0,
      },
      cooldown: {
        active: false,
        remaining_seconds: 0,
      },
      cache: {
        entries: 12,
        hits: 9,
        misses: 3,
        hit_rate: 0.75,
      },
      recent_requests: isConnected
        ? [
            {
              id: 1,
              status: "ok",
              task: "model_discovery",
              attempt: 1,
              http_status: 200,
              latency_ms: 220,
            },
          ]
        : [],
    }
  }

  // -------------------------------------------------------------------------
  // ChatGPT / Codex OAuth Session & Model Management
  // -------------------------------------------------------------------------

  authSession: {
    status: "not_connected" | "connecting" | "connected" | "refreshing" | "expired" | "failed"
    access_token?: string | null
    refresh_token?: string | null
    expires_at?: number | null
    email?: string | null
    account_id?: string | null
    selected_model?: string | null
    available_models?: string[]
    error?: string | null
  } = {
    status: "not_connected",
    available_models: ["gpt-5.6-terra", "gpt-5-turbo", "o1", "o3-mini", "gpt-4o"],
    selected_model: "gpt-5.6-terra",
  }

  pendingLogin: {
    codeVerifier: string
    codeChallenge: string
    expectedState: string
    authUrl: string
    timeoutAt: number
  } | null = null

  private getSessionFilePath(): string {
    const fs = require("node:fs")
    const path = require("node:path")
    const local = path.resolve(process.cwd(), "auth_session.json")
    if (fs.existsSync(local)) return local
    const backend = path.resolve(process.cwd(), "backend", "auth_session.json")
    if (fs.existsSync(backend)) return backend
    return local
  }

  loadSessionFromDisk() {
    try {
      const fs = require("node:fs")
      const sessionPath = this.getSessionFilePath()
      if (fs.existsSync(sessionPath)) {
        const raw = fs.readFileSync(sessionPath, "utf-8")
        const data = JSON.parse(raw)
        if (data && typeof data === "object") {
          const now = Date.now() / 1000
          const exp = typeof data.expires_at === "number" ? data.expires_at : null
          const isExpired = exp ? exp < now : false

          this.authSession = {
            status: isExpired ? (data.refresh_token ? "expired" : "not_connected") : "connected",
            access_token: data.access_token || null,
            refresh_token: data.refresh_token || null,
            expires_at: exp,
            email: data.email || null,
            account_id: data.account_id || null,
            selected_model: data.selected_model || "gpt-5.6-terra",
            available_models: data.available_models || ["gpt-5.6-terra", "gpt-5-turbo", "o1", "o3-mini", "gpt-4o"],
          }

          // Try automatic refresh on startup if expired
          if (isExpired && data.refresh_token) {
            this.refreshSession().catch(() => {})
          }
        }
      }
    } catch (err: any) {
      console.warn("[Codex Auth] Warning loading session from disk:", err.message)
    }
  }

  saveSessionToDisk() {
    try {
      const fs = require("node:fs")
      const sessionPath = this.getSessionFilePath()
      const dataToSave = {
        access_token: this.authSession.access_token,
        refresh_token: this.authSession.refresh_token,
        expires_at: this.authSession.expires_at,
        email: this.authSession.email,
        account_id: this.authSession.account_id,
        selected_model: this.authSession.selected_model,
        available_models: this.authSession.available_models,
        saved_at: Date.now() / 1000,
      }
      fs.writeFileSync(sessionPath, JSON.stringify(dataToSave, null, 2), { mode: 0o600 })
    } catch (err: any) {
      console.warn("[Codex Auth] Failed to save session to disk:", err.message)
    }
  }

  clearSessionDisk() {
    try {
      const fs = require("node:fs")
      const sessionPath = this.getSessionFilePath()
      if (fs.existsSync(sessionPath)) {
        fs.unlinkSync(sessionPath)
      }
    } catch {}
  }

  getAuthStatus() {
    // Check if token expired
    if (this.authSession.expires_at) {
      const now = Date.now() / 1000
      if (this.authSession.expires_at < now && this.authSession.status === "connected") {
        this.authSession.status = this.authSession.refresh_token ? "expired" : "not_connected"
      }
    }

    return {
      status: this.authSession.status,
      account: this.authSession.email || this.authSession.account_id ? {
        email: this.authSession.email || null,
        account_id: this.authSession.account_id || null,
      } : null,
      selected_model: this.authSession.selected_model || "gpt-5.6-terra",
      available_models: this.authSession.available_models || ["gpt-5.6-terra", "gpt-5-turbo", "o1", "o3-mini", "gpt-4o"],
      expires_at: this.authSession.expires_at || null,
      auth_url: this.pendingLogin ? this.pendingLogin.authUrl : null,
      error: this.authSession.error || null,
    }
  }

  startBrowserLogin() {
    const crypto = require("node:crypto")
    const CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
    const PORT = 1455
    const CALLBACK_PATH = "/auth/callback"
    const REDIRECT_URI = `http://localhost:${PORT}${CALLBACK_PATH}`
    const AUTH_URL_BASE = "https://auth.openai.com/oauth/authorize"

    const codeVerifier = crypto.randomBytes(32).toString("base64url").replace(/=/g, "")
    const codeChallenge = crypto.createHash("sha256").update(codeVerifier).digest("base64url").replace(/=/g, "")
    const expectedState = crypto.randomBytes(16).toString("hex")

    const params = new URLSearchParams({
      response_type: "code",
      client_id: CLIENT_ID,
      redirect_uri: REDIRECT_URI,
      scope: "openid profile email offline_access",
      code_challenge: codeChallenge,
      code_challenge_method: "S256",
      state: expectedState,
      id_token_add_organizations: "true",
      codex_cli_simplified_flow: "true",
      originator: "custom_python_script",
    })

    const authUrl = `${AUTH_URL_BASE}?${params.toString()}`

    this.pendingLogin = {
      codeVerifier,
      codeChallenge,
      expectedState,
      authUrl,
      timeoutAt: Date.now() + 5 * 60 * 1000,
    }
    this.authSession.status = "connecting"
    this.authSession.error = null

    // Start local server on port 1455 to receive browser redirect
    this.startPort1455Server()

    return { auth_url: authUrl, state: expectedState }
  }

  private port1455Server: any = null

  private startPort1455Server() {
    if (this.port1455Server) return
    try {
      const http = require("node:http")
      const PORT = 1455
      const CALLBACK_PATH = "/auth/callback"

      this.port1455Server = http.createServer(async (req: any, res: any) => {
        try {
          const reqUrl = new URL(req.url || "", `http://localhost:${PORT}`)
          if (reqUrl.pathname === CALLBACK_PATH) {
            const code = reqUrl.searchParams.get("code")
            const state = reqUrl.searchParams.get("state")
            if (!code || state !== this.pendingLogin?.expectedState) {
              res.writeHead(400, { "Content-Type": "text/html" })
              res.end("<html><body><h2>Authentication failed: State mismatch.</h2></body></html>")
              return
            }
            await this.exchangeAuthCode(code)
            res.writeHead(200, { "Content-Type": "text/html" })
            res.end("<html><body><h2>Authentication Successful!</h2><p>You can safely close this window.</p></body></html>")
          } else {
            res.writeHead(404)
            res.end("Not found")
          }
        } catch (e: any) {
          res.writeHead(500, { "Content-Type": "text/html" })
          res.end(`<html><body><h2>Error:</h2><p>${e.message}</p></body></html>`)
        }
      })

      this.port1455Server.listen(PORT, "0.0.0.0", () => {
        console.log(`[Codex Auth] Local callback listener listening on port ${PORT}`)
      })
      this.port1455Server.on("error", (err: any) => {
        console.warn(`[Codex Auth] Port ${PORT} listener note:`, err.message)
      })
    } catch (err: any) {
      console.warn("[Codex Auth] Local listener warning:", err.message)
    }
  }

  private stopPort1455Server() {
    if (this.port1455Server) {
      try {
        this.port1455Server.close()
      } catch {}
      this.port1455Server = null
    }
  }

  async exchangeAuthCode(code: string, stateProvided?: string) {
    if (stateProvided && this.pendingLogin?.expectedState && stateProvided !== this.pendingLogin.expectedState) {
      this.authSession.status = "failed"
      this.authSession.error = "State parameter mismatch in OAuth callback"
      throw new Error(this.authSession.error)
    }

    const codeVerifier = this.pendingLogin?.codeVerifier || ""
    const CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
    const PORT = 1455
    const REDIRECT_URI = `http://localhost:${PORT}/auth/callback`
    const TOKEN_URL = "https://auth.openai.com/oauth/token"

    try {
      const resp = await fetch(TOKEN_URL, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          client_id: CLIENT_ID,
          code,
          redirect_uri: REDIRECT_URI,
          code_verifier: codeVerifier,
        }),
      })

      if (!resp.ok) {
        const text = await resp.text()
        this.authSession.status = "failed"
        this.authSession.error = `Token exchange failed: ${resp.status} ${text.slice(0, 100)}`
        throw new Error(this.authSession.error)
      }

      const tokens = await resp.json()
      const now = Date.now() / 1000
      const exp = tokens.expires_in ? now + Number(tokens.expires_in) : now + 3600

      // Extract claims from JWT
      let email: string | null = null
      let accountId: string | null = null
      for (const t of [tokens.id_token, tokens.access_token]) {
        if (t && typeof t === "string" && t.split(".").length >= 2) {
          try {
            const payload = JSON.parse(Buffer.from(t.split(".")[1], "base64url").toString("utf-8"))
            if (payload.email && !email) email = payload.email
            const authClaims = payload["https://api.openai.com/auth"] || {}
            if (authClaims.chatgpt_account_id && !accountId) accountId = String(authClaims.chatgpt_account_id)
          } catch {}
        }
      }

      this.authSession = {
        status: "connected",
        access_token: tokens.access_token,
        refresh_token: tokens.refresh_token,
        expires_at: exp,
        email,
        account_id: accountId,
        selected_model: this.authSession.selected_model || "gpt-5.6-terra",
        available_models: this.authSession.available_models || ["gpt-5.6-terra", "gpt-5-turbo", "o1", "o3-mini", "gpt-4o"],
        error: null,
      }

      this.saveSessionToDisk()
      this.pendingLogin = null
      this.stopPort1455Server()

      // Automatically fetch available models from OpenAI
      await this.refreshModelsList()

      return this.getAuthStatus()
    } catch (err: any) {
      this.authSession.status = "failed"
      this.authSession.error = err.message || "OAuth login failed"
      throw err
    }
  }

  async refreshSession() {
    if (!this.authSession.refresh_token) {
      this.authSession.status = "expired"
      this.authSession.error = "No refresh token available. Reconnect required."
      return this.getAuthStatus()
    }

    this.authSession.status = "refreshing"
    const CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
    const TOKEN_URL = "https://auth.openai.com/oauth/token"

    try {
      const resp = await fetch(TOKEN_URL, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token",
          refresh_token: this.authSession.refresh_token,
          client_id: CLIENT_ID,
        }),
      })

      if (!resp.ok) {
        this.authSession.status = "expired"
        this.authSession.error = "Refresh token expired or revoked. Please reconnect."
        return this.getAuthStatus()
      }

      const tokens = await resp.json()
      const now = Date.now() / 1000
      this.authSession.access_token = tokens.access_token
      if (tokens.refresh_token) this.authSession.refresh_token = tokens.refresh_token
      this.authSession.expires_at = tokens.expires_in ? now + Number(tokens.expires_in) : now + 3600
      this.authSession.status = "connected"
      this.authSession.error = null

      this.saveSessionToDisk()
      return this.getAuthStatus()
    } catch (err: any) {
      this.authSession.status = "expired"
      this.authSession.error = err.message || "Failed to refresh session"
      return this.getAuthStatus()
    }
  }

  async refreshModelsList() {
    if (!this.authSession.access_token) return this.authSession.available_models

    try {
      const resp = await fetch("https://chatgpt.com/backend-api/codex/models?client_version=0.146.0", {
        headers: { Authorization: `Bearer ${this.authSession.access_token}` },
      })

      if (resp.status === 401) {
        await this.refreshSession()
      } else if (resp.ok) {
        const data = await resp.json()
        const models: string[] = []
        if (Array.isArray(data)) {
          for (const m of data) {
            if (typeof m === "string") models.push(m)
            else if (m && typeof m === "object" && m.slug) models.push(m.slug)
            else if (m && typeof m === "object" && m.id) models.push(m.id)
          }
        } else if (data && typeof data === "object") {
          const list = data.models || data.data || []
          if (Array.isArray(list)) {
            for (const m of list) {
              if (typeof m === "string") models.push(m)
              else if (m && typeof m === "object" && m.slug) models.push(m.slug)
              else if (m && typeof m === "object" && m.id) models.push(m.id)
            }
          }
        }

        if (models.length > 0) {
          this.authSession.available_models = models
          if (!this.authSession.selected_model || !models.includes(this.authSession.selected_model)) {
            this.authSession.selected_model = models[0]
          }
          this.saveSessionToDisk()
        }
      }
    } catch (e: any) {
      console.warn("[Codex Auth] Could not reach OpenAI models endpoint:", e.message)
    }

    if (!this.authSession.available_models || this.authSession.available_models.length === 0) {
      this.authSession.available_models = ["gpt-5.6-terra", "gpt-5-turbo", "o1", "o3-mini", "gpt-4o"]
    }
    return this.authSession.available_models
  }

  selectModel(modelName: string) {
    this.authSession.selected_model = modelName
    this.saveSessionToDisk()
    return this.getAuthStatus()
  }

  disconnect() {
    this.authSession = {
      status: "not_connected",
      access_token: null,
      refresh_token: null,
      expires_at: null,
      email: null,
      account_id: null,
      selected_model: "gpt-5.6-terra",
      available_models: ["gpt-5.6-terra", "gpt-5-turbo", "o1", "o3-mini", "gpt-4o"],
      error: null,
    }
    this.pendingLogin = null
    this.stopPort1455Server()
    this.clearSessionDisk()
    return this.getAuthStatus()
  }

  getStats() {
    const isMd = Boolean(process.env.MOTHERDUCK_TOKEN)
    const backendName = isMd ? "MotherDuck (hosted)" : (process.env.DB_BACKEND === "sqlite" ? "SQLite" : "DuckDB (local)")
    return {
      backend: backendName,
      counts: {
        entities: this.entities.size,
        relationships: this.relationships.size,
        sources: 6,
        raw_records: 10,
        match_candidates: this.matches.size,
        evidence: this.evidence.size,
      },
      sources: [
        {
          id: "mca",
          name: "MCA Company Master Data (Karnataka, Andhra Pradesh)",
          homepage: "https://www.data.gov.in/",
          licence: "GODL-India",
          entities: this.entities.size,
        },
        {
          id: "gleif",
          name: "GLEIF LEI Level 1 & Level 2 Relationships",
          homepage: "https://www.gleif.org/",
          licence: "CC0 1.0",
          entities: 20,
        },
        {
          id: "nse",
          name: "NSE SEBI Debarred Entities & Circulars",
          homepage: "https://www.nseindia.com/",
          licence: "NSE Public Registry",
          entities: 16121,
        },
        {
          id: "icij",
          name: "ICIJ Offshore Leaks Database",
          homepage: "https://offshoreleaks.icij.org/",
          licence: "ODbL 1.0",
          entities: 500,
        },
        {
          id: "opensanctions",
          name: "OpenSanctions Targets (Consolidated Sanctions & PEPs)",
          homepage: "https://www.opensanctions.org/",
          licence: "CC BY-NC 4.0",
          entities: 100,
        },
      ],
      runs: [
        {
          id: 1,
          source_id: "duckdb_pipeline",
          started_at: "2026-10-12 12:00:00",
          finished_at: "2026-10-12 12:00:15",
          status: "success",
          stats: { datasets_verified: 5, compression_ratio: "3.5x", format: "DuckDB Columnar" },
          error: null,
        },
      ],
      pending_reviews: Array.from(this.matches.values()).filter((m) => m.status === "pending").length,
    }
  }


  reviewQueue(status = "pending", limit = 50) {
    const items = Array.from(this.matches.values())
      .filter((m) => m.status === status)
      .slice(0, limit)
      .map((m) => {
        const left = this.entities.get(m.left_id)
        const right = this.entities.get(m.right_id)
        return {
          id: m.id,
          method: m.method,
          score: m.score,
          status: m.status,
          features: m.features,
          left: left ? this.summaryOf(left) : null,
          right: right ? this.summaryOf(right) : null,
        }
      })
    return { items }
  }

  decideReview(matchId: number, decision: "confirm" | "reject" | "reset", note?: string) {
    const match = this.matches.get(matchId)
    if (!match) throw new Error("Match not found")

    if (decision === "confirm") {
      match.status = "confirmed"
      match.note = note
      // Add inferred same_as relationship
      const relId = this.nextRelId++
      this.relationships.set(relId, {
        id: relId,
        source_id: "review",
        subject_id: match.left_id,
        predicate: "same_as",
        object_id: match.right_id,
        basis: "inferred",
        confidence: match.score / 100,
        start_date: new Date().toISOString().slice(0, 10),
        end_date: null,
      })
    } else if (decision === "reject") {
      match.status = "rejected"
      match.note = note
    } else if (decision === "reset") {
      match.status = "pending"
      match.note = null
    }

    return { ok: true, match_id: matchId, status: match.status }
  }

  listInvestigations() {
    return {
      items: Array.from(this.investigations.values())
        .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
        .map((inv) => ({
          id: inv.id,
          title: inv.title,
          notes: inv.notes,
          updated_at: inv.updated_at,
          item_count: inv.items.length,
        })),
    }
  }

  getInvestigation(invId: number) {
    const inv = this.investigations.get(invId)
    if (!inv) return null

    const items = inv.items.map((it) => {
      const entity = it.item_type === "entity" ? this.entities.get(it.ref) : null
      return {
        ...it,
        entity: entity ? this.summaryOf(entity) : null,
      }
    })

    return {
      id: inv.id,
      title: inv.title,
      notes: inv.notes,
      created_at: inv.created_at,
      updated_at: inv.updated_at,
      items,
    }
  }

  createInvestigation(title: string, notes: string | null = null) {
    const id = this.nextInvId++
    const now = new Date().toISOString()
    const inv: StoredInvestigation = {
      id,
      title: title.trim(),
      notes,
      created_at: now,
      updated_at: now,
      items: [],
    }
    this.investigations.set(id, inv)
    return this.getInvestigation(id)
  }

  addInvestigationItem(invId: number, itemType: "entity" | "relationship" | "path" | "note", ref: string, note: string | null = null) {
    const inv = this.investigations.get(invId)
    if (!inv) return null

    const now = new Date().toISOString()
    const id = this.nextInvItemId++
    inv.items.push({
      id,
      item_type: itemType,
      ref,
      note,
      created_at: now,
    })
    inv.updated_at = now
    return this.getInvestigation(invId)
  }

  deleteInvestigationItem(invId: number, itemId: number) {
    const inv = this.investigations.get(invId)
    if (!inv) return null

    inv.items = inv.items.filter((i) => i.id !== itemId)
    inv.updated_at = new Date().toISOString()
    return this.getInvestigation(invId)
  }
}

// Global singleton in memory across hot requests
const globalStore = (globalThis as unknown as { __osint_db?: OsintDatabase })
if (!globalStore.__osint_db) {
  globalStore.__osint_db = new OsintDatabase()
}

export const db = globalStore.__osint_db
