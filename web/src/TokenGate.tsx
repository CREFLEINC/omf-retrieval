import { useEffect, useRef, useState } from 'react'

import { createTimedRequest } from './requestTimeout'

interface TokenGateProps {
  onConnected: (token: string) => void
}

const READINESS_ERROR_MESSAGES: Readonly<Record<number, string>> = {
  401: '토큰을 확인한 뒤 다시 시도해 주세요.',
  403: 'OMF 정보 조회 권한이 없습니다. 관리자에게 권한을 요청해 주세요.',
  503: '서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.',
}

const DEFAULT_SERVICE_ERROR =
  '서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.'
const NETWORK_ERROR =
  '서비스에 연결할 수 없습니다. 네트워크 상태를 확인한 뒤 다시 시도해 주세요.'
const TIMEOUT_ERROR = '연결 확인 시간이 초과되었습니다. 다시 시도해 주세요.'

export const TokenGate = ({
  onConnected,
}: TokenGateProps): React.JSX.Element => {
  const [tokenInput, setTokenInput] = useState('')
  const [isPending, setIsPending] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const isRequestPending = useRef(false)
  const requestControllerRef = useRef<AbortController | null>(null)
  const isMountedRef = useRef(true)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    isMountedRef.current = true

    return () => {
      isMountedRef.current = false
      requestControllerRef.current?.abort()
    }
  }, [])

  const handleSubmit = async (
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> => {
    event.preventDefault()

    if (isRequestPending.current) {
      return
    }

    isRequestPending.current = true
    setIsPending(true)
    setErrorMessage(null)
    const timedRequest = createTimedRequest()
    requestControllerRef.current = timedRequest.controller

    try {
      const response = await fetch('/health/ready', {
        method: 'GET',
        headers: { Authorization: `Bearer ${tokenInput}` },
        signal: timedRequest.controller.signal,
      })

      if (response.status === 200) {
        const connectedToken = tokenInput
        setTokenInput('')
        onConnected(connectedToken)
        return
      }

      setErrorMessage(
        READINESS_ERROR_MESSAGES[response.status] ?? DEFAULT_SERVICE_ERROR,
      )
      inputRef.current?.focus()
    } catch {
      if (!isMountedRef.current) {
        return
      }
      setErrorMessage(timedRequest.didTimeout() ? TIMEOUT_ERROR : NETWORK_ERROR)
      inputRef.current?.focus()
    } finally {
      timedRequest.clear()
      if (requestControllerRef.current === timedRequest.controller) {
        requestControllerRef.current = null
        isRequestPending.current = false
        if (isMountedRef.current) {
          setIsPending(false)
        }
      }
    }
  }

  const errorDescriptionId = errorMessage === null ? '' : 'token-error'
  const inputDescriptionIds = ['token-help', errorDescriptionId]
    .filter(Boolean)
    .join(' ')

  return (
    <section className="connection-panel" aria-labelledby="connection-title">
      <div className="connection-heading">
        <h2 id="connection-title">조회 서비스에 연결</h2>
        <p>
          발급받은 접근 토큰으로 연결하세요. 토큰은 이 화면을 사용하는 동안에만
          메모리에 보관됩니다.
        </p>
      </div>

      <form className="token-form" autoComplete="off" onSubmit={handleSubmit}>
        <div className="field-group">
          <label htmlFor="access-token">접근 토큰</label>
          <input
            ref={inputRef}
            id="access-token"
            type="password"
            value={tokenInput}
            autoComplete="off"
            autoCapitalize="none"
            aria-describedby={inputDescriptionIds}
            aria-invalid={errorMessage !== null}
            disabled={isPending}
            required
            spellCheck={false}
            onChange={(event) => setTokenInput(event.target.value)}
          />
          <p id="token-help" className="field-help">
            붙여넣기를 사용할 수 있습니다. 새로고침하거나 브라우저를 닫으면 다시
            입력해야 합니다.
          </p>
          {errorMessage === null ? null : (
            <p id="token-error" className="field-error" role="alert">
              {errorMessage}
            </p>
          )}
        </div>

        <button className="primary-button" type="submit" disabled={isPending}>
          연결
        </button>
        {isPending ? (
          <p className="pending-status" role="status" aria-live="polite">
            연결 확인 중입니다.
          </p>
        ) : null}
      </form>
    </section>
  )
}
