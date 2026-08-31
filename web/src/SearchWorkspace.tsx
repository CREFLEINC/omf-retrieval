import { useEffect, useRef, useState } from 'react'

import { EvidenceResults } from './EvidenceResults'
import {
  isSearchResponse,
  type RelevanceLevel,
  type SearchResponse,
} from './searchTypes'

interface SearchWorkspaceProps {
  accessToken: string
  onDisconnect: () => void
}

type SearchView =
  | { kind: 'idle' }
  | { kind: 'pending' }
  | { kind: 'error'; message: string }
  | { kind: 'no_evidence' }
  | { kind: 'ok'; response: SearchResponse }

const SEARCH_ERROR_MESSAGES: Readonly<Record<number, string>> = {
  403: 'OMF 정보 조회 권한이 없습니다. 관리자에게 권한을 요청한 뒤 다시 시도해 주세요.',
  409: '활성화된 검색 색인이 없습니다. 관리자에게 서비스 상태를 확인해 달라고 요청해 주세요.',
  422: '검색 요청을 처리할 수 없습니다. 검색어와 설정을 확인한 뒤 다시 시도해 주세요.',
  503: '검색 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.',
}

const DEFAULT_SEARCH_ERROR =
  '검색 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.'
const NETWORK_ERROR =
  '검색 서비스에 연결할 수 없습니다. 네트워크 상태를 확인한 뒤 다시 시도해 주세요.'
const MALFORMED_RESPONSE_ERROR =
  '검색 결과를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.'

export const SearchWorkspace = ({
  accessToken,
  onDisconnect,
}: SearchWorkspaceProps): React.JSX.Element => {
  const [query, setQuery] = useState('')
  const [relevanceLevel, setRelevanceLevel] =
    useState<RelevanceLevel>('default')
  const [limit, setLimit] = useState(5)
  const [queryError, setQueryError] = useState<string | null>(null)
  const [view, setView] = useState<SearchView>({ kind: 'idle' })
  const isRequestPending = useRef(false)
  const queryInputRef = useRef<HTMLInputElement>(null)
  const resultHeadingRef = useRef<HTMLHeadingElement>(null)
  const isPending = view.kind === 'pending'

  useEffect(() => {
    if (view.kind === 'ok' || view.kind === 'no_evidence') {
      resultHeadingRef.current?.focus()
    }
  }, [view])

  const finishWithError = (message: string): void => {
    setView({ kind: 'error', message })
    isRequestPending.current = false
  }

  const handleSubmit = async (
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> => {
    event.preventDefault()

    if (isRequestPending.current) {
      return
    }

    const normalizedQuery = query.trim()
    if (!normalizedQuery) {
      setQueryError('검색어를 입력해 주세요.')
      queryInputRef.current?.focus()
      return
    }

    setQueryError(null)
    setView({ kind: 'pending' })
    isRequestPending.current = true

    let response: Response
    try {
      response = await fetch('/v1/search', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query: normalizedQuery,
          limit,
          relevance_level: relevanceLevel,
        }),
      })
    } catch {
      finishWithError(NETWORK_ERROR)
      return
    }

    if (response.status === 401) {
      isRequestPending.current = false
      onDisconnect()
      return
    }

    if (!response.ok) {
      finishWithError(
        SEARCH_ERROR_MESSAGES[response.status] ?? DEFAULT_SEARCH_ERROR,
      )
      return
    }

    let responseBody: unknown
    try {
      responseBody = await response.json()
    } catch {
      finishWithError(MALFORMED_RESPONSE_ERROR)
      return
    }

    if (!isSearchResponse(responseBody)) {
      finishWithError(MALFORMED_RESPONSE_ERROR)
      return
    }

    if (responseBody.status === 'no_evidence') {
      setView({ kind: 'no_evidence' })
    } else {
      setView({ kind: 'ok', response: responseBody })
    }
    isRequestPending.current = false
  }

  const queryDescriptionIds = [
    'search-description',
    queryError && 'query-error',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <section className="connected-panel" aria-labelledby="connected-title">
      <div className="workspace-heading">
        <div>
          <p className="connection-status" role="status">
            연결됨
          </p>
          <h2 id="connected-title">OMF 근거 검색</h2>
          <p id="search-description">
            검색어와 관련성 수준을 설정해 OMF 설계 문서의 근거를 조회하세요.
          </p>
        </div>
        <button
          className="secondary-button compact-button"
          type="button"
          disabled={isPending}
          onClick={onDisconnect}
        >
          토큰 변경
        </button>
      </div>

      <form className="search-form" noValidate onSubmit={handleSubmit}>
        <div className="field-group search-query-field">
          <label htmlFor="search-query">검색어</label>
          <input
            ref={queryInputRef}
            id="search-query"
            name="query"
            type="search"
            value={query}
            aria-describedby={queryDescriptionIds}
            aria-invalid={queryError !== null}
            disabled={isPending}
            onChange={(event) => setQuery(event.target.value)}
          />
          {queryError === null ? null : (
            <p id="query-error" className="field-error" role="alert">
              {queryError}
            </p>
          )}
        </div>

        <fieldset className="relevance-fieldset" disabled={isPending}>
          <legend>연관성 수준</legend>
          <label className="relevance-option">
            <input
              type="radio"
              name="relevance-level"
              value="default"
              checked={relevanceLevel === 'default'}
              onChange={() => setRelevanceLevel('default')}
            />
            <span>
              <strong>기본</strong>
              <small>키워드 또는 의미 검색에서 확인된 근거를 표시합니다.</small>
            </span>
          </label>
          <label className="relevance-option">
            <input
              type="radio"
              name="relevance-level"
              value="strict"
              checked={relevanceLevel === 'strict'}
              onChange={() => setRelevanceLevel('strict')}
            />
            <span>
              <strong>엄격</strong>
              <small>
                키워드와 의미 검색 모두에서 확인된 근거만 표시합니다.
              </small>
            </span>
          </label>
        </fieldset>

        <div className="field-group limit-field">
          <label htmlFor="result-limit">결과 수</label>
          <select
            id="result-limit"
            name="limit"
            value={limit}
            disabled={isPending}
            onChange={(event) => setLimit(Number(event.target.value))}
          >
            <option value="5">5개</option>
            <option value="10">10개</option>
            <option value="20">20개</option>
          </select>
        </div>

        <button
          className="primary-button search-button"
          type="submit"
          disabled={isPending}
        >
          검색
        </button>
      </form>

      <div className="search-feedback" aria-live="polite">
        {view.kind === 'pending' ? (
          <section
            className="loading-panel"
            role="status"
            aria-label="검색 진행 상태"
          >
            <p>근거를 검색하고 있습니다.</p>
            <div className="loading-placeholder" aria-hidden="true" />
          </section>
        ) : null}
        {view.kind === 'error' ? (
          <p className="search-error" role="alert">
            {view.message}
          </p>
        ) : null}
        {view.kind === 'no_evidence' ? (
          <section
            className="empty-results"
            aria-labelledby="empty-results-title"
          >
            <h2 id="empty-results-title" ref={resultHeadingRef} tabIndex={-1}>
              근거를 찾지 못했습니다
            </h2>
            <p>
              관련성 수준을 기본으로 바꾸거나 검색어를 더 구체적으로 작성한 뒤
              다시 검색해 주세요.
            </p>
          </section>
        ) : null}
        {view.kind === 'ok' ? (
          <EvidenceResults
            headingRef={resultHeadingRef}
            response={view.response}
          />
        ) : null}
      </div>
    </section>
  )
}
