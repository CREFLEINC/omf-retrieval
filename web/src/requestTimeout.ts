export const REQUEST_TIMEOUT_MS = 10_000

interface TimedRequest {
  controller: AbortController
  clear: () => void
  didTimeout: () => boolean
}

export const createTimedRequest = (): TimedRequest => {
  const controller = new AbortController()
  let timedOut = false
  const timeoutId = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, REQUEST_TIMEOUT_MS)

  return {
    controller,
    clear: () => window.clearTimeout(timeoutId),
    didTimeout: () => timedOut,
  }
}
