import type { SearchResponse } from './searchTypes'

interface EvidenceResultsProps {
  response: SearchResponse
}

const formatRank = (rank: number | null, label: string): string =>
  rank === null ? `${label} 없음` : `${label} ${rank}위`

export const EvidenceResults = ({
  response,
}: EvidenceResultsProps): React.JSX.Element => (
  <section className="results-panel" aria-labelledby="results-title">
    <div className="results-heading">
      <p className="result-kicker">조회 완료</p>
      <h2 id="results-title">검색 결과 {response.evidence_items.length}건</h2>
    </div>

    <div className="evidence-list">
      {response.evidence_items.map((evidence) => (
        <article
          className="evidence-card"
          key={`${evidence.rank}-${evidence.heading_path.join('/')}`}
        >
          <p className="evidence-rank">근거 순위 {evidence.rank}위</p>
          <h3>
            {evidence.heading_path.length === 0
              ? '제목 정보 없음'
              : evidence.heading_path.join(' / ')}
          </h3>

          <div className="match-list">
            {evidence.matches.map((match) => (
              <section
                className="match-card"
                aria-label={`${match.line_start}행부터 ${match.line_end}행 근거`}
                key={`${match.line_start}-${match.line_end}-${match.excerpt}`}
              >
                <p className="excerpt">{match.excerpt}</p>
                <dl className="match-metadata">
                  <div>
                    <dt>원문 위치</dt>
                    <dd>
                      {match.line_start}–{match.line_end}행
                    </dd>
                  </div>
                  <div>
                    <dt>키워드 검색</dt>
                    <dd>{formatRank(match.keyword_rank, '키워드 순위')}</dd>
                  </div>
                  <div>
                    <dt>의미 검색</dt>
                    <dd>{formatRank(match.vector_rank, '의미 검색 순위')}</dd>
                  </div>
                  <div>
                    <dt>통합 순위</dt>
                    <dd>통합 순위 점수(RRF) {match.rrf_score}</dd>
                  </div>
                </dl>
              </section>
            ))}
          </div>

          <section className="origins" aria-label="원본 경로">
            <h4>원본</h4>
            <ul>
              {evidence.origins.map((origin) => (
                <li key={`${origin.source_path}-${origin.content_hash}`}>
                  <dl>
                    <div>
                      <dt>저장소 경로</dt>
                      <dd className="long-token">{origin.source_path}</dd>
                    </div>
                    <div>
                      <dt>내용 해시</dt>
                      <dd className="long-token">{origin.content_hash}</dd>
                    </div>
                  </dl>
                </li>
              ))}
            </ul>
          </section>
        </article>
      ))}
    </div>

    <details className="reproducibility-details">
      <summary>검색 재현 정보</summary>
      <dl>
        <div>
          <dt>색인 실행 식별자</dt>
          <dd className="long-token">{response.index.run_id}</dd>
        </div>
        <div>
          <dt>원본 커밋</dt>
          <dd className="long-token">{response.index.commit_sha}</dd>
        </div>
        <div>
          <dt>검색 정책 식별자</dt>
          <dd className="long-token">{response.search_policy.policy_id}</dd>
        </div>
        <div>
          <dt>검색 정책 설정 해시</dt>
          <dd className="long-token">{response.search_policy.config_hash}</dd>
        </div>
      </dl>
    </details>
  </section>
)
