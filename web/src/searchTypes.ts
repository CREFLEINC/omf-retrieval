export type RelevanceLevel = 'default' | 'strict'

export interface MatchResponse {
  excerpt: string
  line_start: number
  line_end: number
  keyword_rank: number | null
  vector_rank: number | null
  rrf_score: number
}

export interface OriginResponse {
  source_path: string
  content_hash: string
}

export interface EvidenceResponse {
  rank: number
  heading_path: string[]
  matches: MatchResponse[]
  origins: OriginResponse[]
}

export interface SearchResponse {
  request_id: string
  status: 'ok' | 'no_evidence'
  index: {
    run_id: string
    commit_sha: string
  }
  search_policy: {
    policy_id: string
    config_hash: string
  }
  evidence_items: EvidenceResponse[]
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const isOptionalRank = (value: unknown): value is number | null =>
  value === null || Number.isInteger(value)

const isMatchResponse = (value: unknown): value is MatchResponse => {
  if (!isRecord(value)) {
    return false
  }

  return (
    typeof value.excerpt === 'string' &&
    Number.isInteger(value.line_start) &&
    Number.isInteger(value.line_end) &&
    isOptionalRank(value.keyword_rank) &&
    isOptionalRank(value.vector_rank) &&
    typeof value.rrf_score === 'number' &&
    Number.isFinite(value.rrf_score)
  )
}

const isOriginResponse = (value: unknown): value is OriginResponse =>
  isRecord(value) &&
  typeof value.source_path === 'string' &&
  typeof value.content_hash === 'string'

const isEvidenceResponse = (value: unknown): value is EvidenceResponse => {
  if (!isRecord(value)) {
    return false
  }

  return (
    Number.isInteger(value.rank) &&
    Array.isArray(value.heading_path) &&
    value.heading_path.every((heading) => typeof heading === 'string') &&
    Array.isArray(value.matches) &&
    value.matches.every(isMatchResponse) &&
    Array.isArray(value.origins) &&
    value.origins.every(isOriginResponse)
  )
}

export const isSearchResponse = (value: unknown): value is SearchResponse => {
  if (
    !isRecord(value) ||
    !isRecord(value.index) ||
    !isRecord(value.search_policy)
  ) {
    return false
  }

  const evidenceItems = value.evidence_items
  if (
    !Array.isArray(evidenceItems) ||
    !evidenceItems.every(isEvidenceResponse)
  ) {
    return false
  }

  const hasValidStatus = value.status === 'ok' || value.status === 'no_evidence'

  return (
    typeof value.request_id === 'string' &&
    hasValidStatus &&
    typeof value.index.run_id === 'string' &&
    typeof value.index.commit_sha === 'string' &&
    typeof value.search_policy.policy_id === 'string' &&
    typeof value.search_policy.config_hash === 'string' &&
    (value.status !== 'no_evidence' || evidenceItems.length === 0)
  )
}
