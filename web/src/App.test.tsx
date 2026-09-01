import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import { isSearchResponse } from './searchTypes'

const TOKEN = 'unit-test-secret-token'
const REQUEST_TIMEOUT_MS = 10_000

const createResponse = (status: number): Response =>
  ({
    ok: status >= 200 && status < 300,
    status,
  }) as Response

const createJsonResponse = (body: unknown, status = 200): Response =>
  ({
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  }) as unknown as Response

const createRejectedJsonResponse = (error: unknown): Response =>
  ({
    ok: true,
    status: 200,
    json: vi.fn().mockRejectedValue(error),
  }) as unknown as Response

const OK_RESPONSE = {
  request_id: 'request-1',
  status: 'ok',
  index: {
    run_id: '427f2c4a-ab06-486a-9801-4bde3ef17d63',
    commit_sha: 'a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2',
  },
  search_policy: {
    policy_id: '10e86bbd-55b9-457c-9f73-0ca29d09625b',
    config_hash:
      'b1758182ed1bef3f87017cd5db45aa0e9829e785004545ddf48a9bc2be4b21bb',
  },
  evidence_items: [
    {
      rank: 1,
      heading_path: ['OMF 설계', '검색 정책'],
      matches: [
        {
          excerpt: '첫 번째 근거 발췌문입니다.',
          line_start: 10,
          line_end: 14,
          keyword_rank: 2,
          vector_rank: 1,
          rrf_score: 0.0325,
        },
        {
          excerpt: '두 번째 근거 발췌문입니다.',
          line_start: 20,
          line_end: 22,
          keyword_rank: null,
          vector_rank: 3,
          rrf_score: 0.0158,
        },
      ],
      origins: [
        {
          source_path: 'design/wiki/policy/search.md',
          content_hash: 'a'.repeat(64),
        },
        {
          source_path: 'design/wiki/archive/very-long-policy-source.md',
          content_hash: 'b'.repeat(64),
        },
      ],
    },
  ],
} as const

const NO_EVIDENCE_RESPONSE = {
  ...OK_RESPONSE,
  status: 'no_evidence',
  evidence_items: [],
} as const

const submitToken = async (token = TOKEN): Promise<void> => {
  const user = userEvent.setup()

  await user.type(screen.getByLabelText('접근 토큰'), token)
  await user.click(screen.getByRole('button', { name: '연결' }))
}

const createAbortablePendingResponse = (
  signal: AbortSignal | undefined,
): Promise<Response> =>
  new Promise<Response>((_resolve, reject) => {
    signal?.addEventListener('abort', () => {
      reject(new DOMException('The operation was aborted.', 'AbortError'))
    })
  })

const createResponseWithAbortablePendingBody = (
  signal: AbortSignal | undefined,
): Response =>
  ({
    ok: true,
    status: 200,
    json: vi.fn().mockImplementation(
      () =>
        new Promise<unknown>((_resolve, reject) => {
          signal?.addEventListener('abort', () => {
            reject(new DOMException('The operation was aborted.', 'AbortError'))
          })
        }),
    ),
  }) as unknown as Response

describe('App token gate', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('renders the semantic product shell', () => {
    render(<App />)

    expect(screen.getByRole('banner')).toBeInTheDocument()
    const main = screen.getByRole('main')
    expect(main).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { level: 1, name: 'OMF 정보 조회' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', {
        name: '신뢰할 수 있는 근거를 한곳에서',
      }),
    ).not.toBeInTheDocument()
    expect(main.querySelector('section')).toHaveClass('connection-panel')
  })

  it('renders a labelled password input without exposing typed token text or blocking paste', () => {
    render(<App />)

    const input = screen.getByLabelText('접근 토큰')
    const pasteEvent = new Event('paste', { bubbles: true, cancelable: true })

    expect(input).toHaveAttribute('type', 'password')
    expect(input).toHaveAttribute('autocomplete', 'off')
    expect(screen.queryByText(TOKEN)).not.toBeInTheDocument()

    fireEvent(input, pasteEvent)

    expect(pasteEvent.defaultPrevented).toBe(false)
  })

  it('checks readiness with the exact bearer header and never places the token in the URL or body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(createResponse(200))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    await submitToken()

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledWith('/health/ready', {
      method: 'GET',
      headers: { Authorization: `Bearer ${TOKEN}` },
      signal: expect.any(AbortSignal),
    })
    expect(fetchMock.mock.calls[0]?.[0]).not.toContain(TOKEN)
    expect(fetchMock.mock.calls[0]?.[1]).not.toHaveProperty('body')
  })

  it('disables submission, announces progress, and blocks duplicate requests while pending', async () => {
    let resolveRequest: ((response: Response) => void) | undefined
    const fetchMock = vi.fn().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveRequest = resolve
        }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.type(screen.getByLabelText('접근 토큰'), TOKEN)
    const submitButton = screen.getByRole('button', { name: '연결' })
    await user.click(submitButton)

    expect(submitButton).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('연결 확인 중')

    await user.click(submitButton)
    expect(fetchMock).toHaveBeenCalledOnce()

    resolveRequest?.(createResponse(200))
    await screen.findByText('연결됨')
  })

  it('times out a stalled readiness request and permits retry', async () => {
    vi.useFakeTimers()
    const fetchMock = vi
      .fn()
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) =>
        createAbortablePendingResponse(init?.signal ?? undefined),
      )
      .mockResolvedValueOnce(createResponse(200))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    fireEvent.change(screen.getByLabelText('접근 토큰'), {
      target: { value: TOKEN },
    })
    fireEvent.submit(
      screen.getByRole('button', { name: '연결' }).closest('form')!,
    )

    await act(() => vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS))

    expect(screen.getByRole('alert')).toHaveTextContent(
      '연결 확인 시간이 초과되었습니다. 다시 시도해 주세요.',
    )
    expect(screen.getByRole('button', { name: '연결' })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: '연결' }))
    await act(async () => undefined)

    expect(screen.getByText('연결됨')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('recovers from a readiness failure when mounted in React Strict Mode', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    )

    await submitToken()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '서비스에 연결할 수 없습니다.',
    )
    expect(screen.getByRole('button', { name: '연결' })).toBeEnabled()
  })

  it('aborts a stalled readiness request when the token gate unmounts', () => {
    const fetchMock = vi
      .fn()
      .mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
        createAbortablePendingResponse(init?.signal ?? undefined),
      )
    vi.stubGlobal('fetch', fetchMock)
    const mounted = render(<App />)

    fireEvent.change(screen.getByLabelText('접근 토큰'), {
      target: { value: TOKEN },
    })
    fireEvent.submit(
      screen.getByRole('button', { name: '연결' }).closest('form')!,
    )
    const requestSignal = fetchMock.mock.calls[0]?.[1]?.signal as
      AbortSignal | undefined

    expect(requestSignal).toBeDefined()
    expect(requestSignal?.aborted).toBe(false)

    mounted.unmount()

    expect(requestSignal?.aborted).toBe(true)
  })

  it('removes the form after success, shows only connected status, and leaves Web Storage untouched', async () => {
    const storageSet = vi.spyOn(Storage.prototype, 'setItem')
    const storageGet = vi.spyOn(Storage.prototype, 'getItem')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(createResponse(200)))
    render(<App />)

    await submitToken()

    expect(await screen.findByText('연결됨')).toBeInTheDocument()
    expect(screen.queryByLabelText('접근 토큰')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '연결' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByText(TOKEN)).not.toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(TOKEN)
    expect(storageSet).not.toHaveBeenCalled()
    expect(storageGet).not.toHaveBeenCalled()
  })

  it('renders the approved search controls with safe defaults after connecting', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(createResponse(200)))
    render(<App />)

    await submitToken()

    expect(await screen.findByLabelText('검색어')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /기본/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /엄격/ })).not.toBeChecked()
    expect(screen.getByLabelText('결과 수')).toHaveValue('5')
    expect(screen.getByRole('button', { name: '검색' })).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '토큰 변경' }),
    ).toBeInTheDocument()
  })

  it.each([
    ['default', '5'],
    ['strict', '20'],
  ])(
    'posts an exact %s search request with limit %s',
    async (relevanceLevel, limit) => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(createResponse(200))
        .mockResolvedValueOnce(createJsonResponse(NO_EVIDENCE_RESPONSE))
      vi.stubGlobal('fetch', fetchMock)
      const user = userEvent.setup()
      render(<App />)

      await submitToken()
      await user.type(await screen.findByLabelText('검색어'), '  검색 정책  ')
      if (relevanceLevel === 'strict') {
        await user.click(screen.getByRole('radio', { name: /엄격/ }))
      }
      await user.selectOptions(screen.getByLabelText('결과 수'), limit)
      await user.click(screen.getByRole('button', { name: '검색' }))

      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(fetchMock).toHaveBeenLastCalledWith('/v1/search', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${TOKEN}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query: '검색 정책',
          limit: Number(limit),
          relevance_level: relevanceLevel,
        }),
        signal: expect.any(AbortSignal),
      })
      expect(fetchMock.mock.calls[1]?.[0]).not.toContain(TOKEN)
      expect(fetchMock.mock.calls[1]?.[1]?.body).not.toContain(TOKEN)
    },
  )

  it('rejects a blank query without fetching and focuses the field error', async () => {
    const fetchMock = vi.fn().mockResolvedValue(createResponse(200))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await submitToken()
    const queryInput = await screen.findByLabelText('검색어')
    await user.type(queryInput, '   ')
    await user.click(screen.getByRole('button', { name: '검색' }))

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(queryInput).toHaveFocus()
    expect(screen.getByRole('alert')).toHaveTextContent(
      '검색어를 입력해 주세요.',
    )
  })

  it('disables every search control, announces progress, and blocks duplicate submission', async () => {
    let resolveSearch: ((response: Response) => void) | undefined
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(createResponse(200))
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            resolveSearch = resolve
          }),
      )
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await submitToken()
    await user.type(await screen.findByLabelText('검색어'), '정책')
    const submitButton = screen.getByRole('button', { name: '검색' })
    await user.click(submitButton)

    expect(screen.getByLabelText('검색어')).toBeDisabled()
    expect(screen.getByRole('radio', { name: /기본/ })).toBeDisabled()
    expect(screen.getByRole('radio', { name: /엄격/ })).toBeDisabled()
    expect(screen.getByLabelText('결과 수')).toBeDisabled()
    expect(submitButton).toBeDisabled()
    expect(
      screen.getByRole('status', { name: '검색 진행 상태' }),
    ).toHaveTextContent('근거를 검색하고 있습니다.')

    await user.click(submitButton)
    expect(fetchMock).toHaveBeenCalledTimes(2)

    resolveSearch?.(createJsonResponse(NO_EVIDENCE_RESPONSE))
    expect(
      await screen.findByText(/관련성 수준을 기본으로/),
    ).toBeInTheDocument()
  })

  it('times out a stalled search and permits retry without losing the query', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(createResponse(200))
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) =>
        createAbortablePendingResponse(init?.signal ?? undefined),
      )
      .mockResolvedValueOnce(createJsonResponse(NO_EVIDENCE_RESPONSE))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    fireEvent.change(screen.getByLabelText('접근 토큰'), {
      target: { value: TOKEN },
    })
    fireEvent.submit(
      screen.getByRole('button', { name: '연결' }).closest('form')!,
    )
    expect(await screen.findByText('연결됨')).toBeInTheDocument()

    vi.useFakeTimers()
    fireEvent.change(screen.getByLabelText('검색어'), {
      target: { value: '검색 정책' },
    })
    fireEvent.submit(
      screen.getByRole('button', { name: '검색' }).closest('form')!,
    )

    expect(screen.getByRole('button', { name: '토큰 변경' })).toBeEnabled()
    await act(() => vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS))

    expect(screen.getByRole('alert')).toHaveTextContent(
      '검색 요청 시간이 초과되었습니다. 다시 시도해 주세요.',
    )
    expect(screen.getByLabelText('검색어')).toHaveValue('검색 정책')
    expect(screen.getByRole('button', { name: '검색' })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: '검색' }))
    await act(async () => undefined)

    expect(
      screen.getByRole('heading', { name: '근거를 찾지 못했습니다' }),
    ).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('times out while reading a stalled response body and permits retry without losing the query', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(createResponse(200))
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) =>
        Promise.resolve(
          createResponseWithAbortablePendingBody(init?.signal ?? undefined),
        ),
      )
      .mockResolvedValueOnce(createJsonResponse(NO_EVIDENCE_RESPONSE))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)

    fireEvent.change(screen.getByLabelText('접근 토큰'), {
      target: { value: TOKEN },
    })
    fireEvent.submit(
      screen.getByRole('button', { name: '연결' }).closest('form')!,
    )
    expect(await screen.findByText('연결됨')).toBeInTheDocument()

    vi.useFakeTimers()
    fireEvent.change(screen.getByLabelText('검색어'), {
      target: { value: '검색 정책' },
    })
    fireEvent.submit(
      screen.getByRole('button', { name: '검색' }).closest('form')!,
    )

    await act(() => vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS))

    expect(screen.getByRole('alert')).toHaveTextContent(
      '검색 요청 시간이 초과되었습니다. 다시 시도해 주세요.',
    )
    expect(screen.getByLabelText('검색어')).toHaveValue('검색 정책')
    expect(screen.getByRole('button', { name: '검색' })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: '검색' }))
    await act(async () => undefined)

    expect(
      screen.getByRole('heading', { name: '근거를 찾지 못했습니다' }),
    ).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('keeps token change available and aborts a pending search on disconnect', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(createResponse(200))
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) =>
        createAbortablePendingResponse(init?.signal ?? undefined),
      )
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await submitToken()
    await user.type(await screen.findByLabelText('검색어'), '검색 정책')
    await user.click(screen.getByRole('button', { name: '검색' }))
    const requestSignal = fetchMock.mock.calls[1]?.[1]?.signal as
      AbortSignal | undefined

    expect(requestSignal).toBeDefined()
    expect(screen.getByRole('button', { name: '토큰 변경' })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: '토큰 변경' }))

    expect(requestSignal?.aborted).toBe(true)
    expect(screen.getByLabelText('접근 토큰')).toHaveValue('')
    expect(screen.queryByLabelText('검색어')).not.toBeInTheDocument()
  })

  it('renders every evidence match, origin, rank, and reproducibility coordinate', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(createResponse(200))
      .mockResolvedValueOnce(createJsonResponse(OK_RESPONSE))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await submitToken()
    await user.type(await screen.findByLabelText('검색어'), '검색 정책')
    await user.click(screen.getByRole('button', { name: '검색' }))

    const resultHeading = await screen.findByRole('heading', {
      name: '검색 결과 1건',
    })
    expect(resultHeading).not.toHaveFocus()
    expect(screen.getByText('근거 순위 1위')).toBeInTheDocument()
    expect(screen.getByText('OMF 설계 / 검색 정책')).toBeInTheDocument()
    expect(screen.getByText('첫 번째 근거 발췌문입니다.')).toBeInTheDocument()
    expect(screen.getByText('두 번째 근거 발췌문입니다.')).toBeInTheDocument()
    expect(screen.getByText('10–14행')).toBeInTheDocument()
    expect(screen.getByText('20–22행')).toBeInTheDocument()
    expect(screen.getByText('키워드 순위 2위')).toBeInTheDocument()
    expect(screen.getByText('의미 검색 순위 1위')).toBeInTheDocument()
    expect(screen.getByText('키워드 순위 없음')).toBeInTheDocument()
    expect(screen.getByText('의미 검색 순위 3위')).toBeInTheDocument()
    expect(screen.getByText('통합 순위 점수(RRF) 0.0325')).toBeInTheDocument()
    expect(screen.getByText('통합 순위 점수(RRF) 0.0158')).toBeInTheDocument()
    expect(screen.getByText('design/wiki/policy/search.md')).toBeInTheDocument()
    expect(
      screen.getByText('design/wiki/archive/very-long-policy-source.md'),
    ).toBeInTheDocument()
    expect(screen.getByText('a'.repeat(64))).toBeInTheDocument()
    expect(screen.getByText('b'.repeat(64))).toBeInTheDocument()

    await user.click(screen.getByText('검색 재현 정보'))
    expect(screen.getByText(OK_RESPONSE.index.run_id)).toBeInTheDocument()
    expect(screen.getByText(OK_RESPONSE.index.commit_sha)).toBeInTheDocument()
    expect(
      screen.getByText(OK_RESPONSE.search_policy.policy_id),
    ).toBeInTheDocument()
    expect(
      screen.getByText(OK_RESPONSE.search_policy.config_hash),
    ).toBeInTheDocument()
  })

  it('shows actionable guidance instead of an empty result list', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(createResponse(200))
        .mockResolvedValueOnce(createJsonResponse(NO_EVIDENCE_RESPONSE)),
    )
    const user = userEvent.setup()
    render(<App />)

    await submitToken()
    await user.type(await screen.findByLabelText('검색어'), '없는 근거')
    await user.click(screen.getByRole('radio', { name: /엄격/ }))
    await user.click(screen.getByRole('button', { name: '검색' }))

    expect(
      await screen.findByRole('heading', { name: '근거를 찾지 못했습니다' }),
    ).not.toHaveFocus()
    expect(screen.getByText(/관련성 수준을 기본으로/)).toBeInTheDocument()
    expect(screen.getByText(/검색어를 더 구체적으로/)).toBeInTheDocument()
    expect(
      screen.getByRole('status', { name: '검색 완료, 결과 0건' }),
    ).toHaveTextContent('검색 완료, 결과 0건')
    expect(screen.queryByText('근거 순위 1위')).not.toBeInTheDocument()
  })

  it('clears an unauthorized token and cannot search again without reconnecting', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(createResponse(200))
      .mockResolvedValueOnce(createResponse(401))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await submitToken()
    await user.type(await screen.findByLabelText('검색어'), '정책')
    await user.click(screen.getByRole('button', { name: '검색' }))

    const tokenInput = await screen.findByLabelText('접근 토큰')
    expect(tokenInput).toHaveValue('')
    expect(screen.queryByLabelText('검색어')).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(document.body).not.toHaveTextContent(TOKEN)
  })

  it.each([
    [
      403,
      'OMF 정보 조회 권한이 없습니다. 관리자에게 권한을 요청한 뒤 다시 시도해 주세요.',
    ],
    [
      409,
      '활성화된 검색 색인이 없습니다. 관리자에게 서비스 상태를 확인해 달라고 요청해 주세요.',
    ],
    [
      422,
      '검색 요청을 처리할 수 없습니다. 검색어와 설정을 확인한 뒤 다시 시도해 주세요.',
    ],
    [503, '검색 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.'],
  ])(
    'shows a sanitized recoverable search message for HTTP %s',
    async (status, expectedMessage) => {
      const consoleError = vi
        .spyOn(console, 'error')
        .mockImplementation(() => undefined)
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(createResponse(200))
        .mockResolvedValueOnce(createResponse(status))
        .mockResolvedValueOnce(createJsonResponse(NO_EVIDENCE_RESPONSE))
      vi.stubGlobal('fetch', fetchMock)
      const user = userEvent.setup()
      render(<App />)

      await submitToken()
      await user.type(await screen.findByLabelText('검색어'), '정책')
      await user.click(screen.getByRole('button', { name: '검색' }))

      expect(await screen.findByRole('alert')).toHaveTextContent(
        expectedMessage,
      )
      expect(document.body).not.toHaveTextContent(TOKEN)
      expect(consoleError).not.toHaveBeenCalled()

      await user.click(screen.getByRole('button', { name: '검색' }))
      expect(fetchMock).toHaveBeenCalledTimes(3)
    },
  )

  it('sanitizes network failures and permits retry without logging failure details', async () => {
    const consoleError = vi
      .spyOn(console, 'error')
      .mockImplementation(() => undefined)
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(createResponse(200))
      .mockRejectedValueOnce(new Error(`network failed: ${TOKEN}`))
      .mockResolvedValueOnce(createJsonResponse(NO_EVIDENCE_RESPONSE))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await submitToken()
    await user.type(await screen.findByLabelText('검색어'), '정책')
    await user.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '검색 서비스에 연결할 수 없습니다. 네트워크 상태를 확인한 뒤 다시 시도해 주세요.',
    )
    expect(document.body).not.toHaveTextContent(TOKEN)
    expect(consoleError).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '검색' }))
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('treats malformed successful JSON as a generic safe failure', async () => {
    const consoleError = vi
      .spyOn(console, 'error')
      .mockImplementation(() => undefined)
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(createResponse(200))
        .mockResolvedValueOnce(
          createJsonResponse({ detail: `unsafe ${TOKEN}`, status: 'ok' }),
        ),
    )
    const user = userEvent.setup()
    render(<App />)

    await submitToken()
    await user.type(await screen.findByLabelText('검색어'), '정책')
    await user.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '검색 결과를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.',
    )
    expect(document.body).not.toHaveTextContent(TOKEN)
    expect(consoleError).not.toHaveBeenCalled()
  })

  it('keeps an invalid JSON body classified as a generic safe failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(createResponse(200))
        .mockResolvedValueOnce(
          createRejectedJsonResponse(new SyntaxError('Unexpected token')),
        ),
    )
    const user = userEvent.setup()
    render(<App />)

    await submitToken()
    await user.type(await screen.findByLabelText('검색어'), '검색 정책')
    await user.click(screen.getByRole('button', { name: '검색' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '검색 결과를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.',
    )
    expect(screen.getByRole('button', { name: '검색' })).toBeEnabled()
  })

  it('rejects both directions of the status and evidence invariant', () => {
    expect(
      isSearchResponse({
        ...OK_RESPONSE,
        evidence_items: [],
      }),
    ).toBe(false)
    expect(
      isSearchResponse({
        ...OK_RESPONSE,
        status: 'no_evidence',
      }),
    ).toBe(false)
  })

  it('announces only a short completion status outside the result tree', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(createResponse(200))
      .mockResolvedValueOnce(createJsonResponse(OK_RESPONSE))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await submitToken()
    await user.type(await screen.findByLabelText('검색어'), '검색 정책')
    await user.click(screen.getByRole('button', { name: '검색' }))

    const resultHeading = await screen.findByRole('heading', {
      name: '검색 결과 1건',
    })
    const completionStatus = screen.getByRole('status', {
      name: '검색 완료, 결과 1건',
    })

    expect(completionStatus).toHaveTextContent('검색 완료, 결과 1건')
    expect(resultHeading).not.toHaveFocus()
    expect(resultHeading.closest('[aria-live]')).toBeNull()
    expect(
      screen.getByText('첫 번째 근거 발췌문입니다.').closest('[aria-live]'),
    ).toBeNull()
  })

  it('replaces completed results on repeated search and never persists the token', async () => {
    const storageSet = vi.spyOn(Storage.prototype, 'setItem')
    const storageGet = vi.spyOn(Storage.prototype, 'getItem')
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(createResponse(200))
      .mockResolvedValueOnce(createJsonResponse(OK_RESPONSE))
      .mockResolvedValueOnce(createJsonResponse(NO_EVIDENCE_RESPONSE))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await submitToken()
    await user.type(await screen.findByLabelText('검색어'), '첫 검색')
    await user.click(screen.getByRole('button', { name: '검색' }))
    expect(
      await screen.findByText('첫 번째 근거 발췌문입니다.'),
    ).toBeInTheDocument()

    await user.clear(screen.getByLabelText('검색어'))
    await user.type(screen.getByLabelText('검색어'), '두 번째 검색')
    await user.click(screen.getByRole('button', { name: '검색' }))

    expect(
      await screen.findByRole('heading', { name: '근거를 찾지 못했습니다' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByText('첫 번째 근거 발췌문입니다.'),
    ).not.toBeInTheDocument()
    expect(storageSet).not.toHaveBeenCalled()
    expect(storageGet).not.toHaveBeenCalled()
  })

  it('clears the connected token and requires a new token after disconnect', async () => {
    const fetchMock = vi.fn().mockResolvedValue(createResponse(200))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await submitToken()
    await screen.findByText('연결됨')
    await user.click(screen.getByRole('button', { name: '토큰 변경' }))

    expect(screen.queryByText('연결됨')).not.toBeInTheDocument()
    const tokenInput = screen.getByLabelText('접근 토큰')
    expect(tokenInput).toHaveValue('')

    const replacementToken = 'replacement-token'
    await user.type(tokenInput, replacementToken)
    await user.click(screen.getByRole('button', { name: '연결' }))

    expect(fetchMock).toHaveBeenLastCalledWith('/health/ready', {
      method: 'GET',
      headers: { Authorization: `Bearer ${replacementToken}` },
      signal: expect.any(AbortSignal),
    })
    expect(fetchMock.mock.calls.at(-1)?.[1]).not.toEqual(
      expect.objectContaining({
        headers: { Authorization: `Bearer ${TOKEN}` },
      }),
    )
  })

  it('does not restore a token after a fresh mount or touch Web Storage', async () => {
    const storageSet = vi.spyOn(Storage.prototype, 'setItem')
    const storageGet = vi.spyOn(Storage.prototype, 'getItem')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(createResponse(200)))
    const firstMount = render(<App />)

    await submitToken()
    await screen.findByText('연결됨')
    firstMount.unmount()
    render(<App />)

    expect(screen.getByLabelText('접근 토큰')).toHaveValue('')
    expect(screen.queryByText('연결됨')).not.toBeInTheDocument()
    expect(storageSet).not.toHaveBeenCalled()
    expect(storageGet).not.toHaveBeenCalled()
  })

  it.each([
    [401, '토큰을 확인한 뒤 다시 시도해 주세요.'],
    [403, 'OMF 정보 조회 권한이 없습니다. 관리자에게 권한을 요청해 주세요.'],
    [503, '서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.'],
  ])(
    'shows a sanitized inline recovery message for HTTP %s',
    async (status, expectedMessage) => {
      const consoleError = vi
        .spyOn(console, 'error')
        .mockImplementation(() => undefined)
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(createResponse(status)))
      render(<App />)

      await submitToken()

      expect(await screen.findByRole('alert')).toHaveTextContent(
        expectedMessage,
      )
      expect(screen.getByRole('alert')).not.toHaveTextContent(TOKEN)
      expect(document.body).not.toHaveTextContent(TOKEN)
      expect(consoleError).not.toHaveBeenCalled()
    },
  )

  it('shows a sanitized retry message for a network failure without logging the token', async () => {
    const consoleError = vi
      .spyOn(console, 'error')
      .mockImplementation(() => undefined)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error(`failed: ${TOKEN}`)),
    )
    render(<App />)

    await submitToken()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      '서비스에 연결할 수 없습니다. 네트워크 상태를 확인한 뒤 다시 시도해 주세요.',
    )
    expect(document.body).not.toHaveTextContent(TOKEN)
    expect(consoleError).not.toHaveBeenCalled()
  })

  it('supports keyboard focus and native enter-key form submission', async () => {
    const fetchMock = vi.fn().mockResolvedValue(createResponse(200))
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<App />)

    await user.tab()
    expect(screen.getByRole('link', { name: '본문 바로가기' })).toHaveFocus()
    await user.tab()
    expect(screen.getByLabelText('접근 토큰')).toHaveFocus()

    await user.type(screen.getByLabelText('접근 토큰'), `${TOKEN}{Enter}`)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce())
    expect(await screen.findByText('연결됨')).toBeInTheDocument()
  })
})
