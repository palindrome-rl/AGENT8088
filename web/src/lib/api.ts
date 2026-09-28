/**
 * One place where a failed request becomes something a person can act on.
 *
 * Pages used to each do their own `if (!res.ok) throw new Error(...)`, which
 * produced three bad outcomes the audit caught:
 *   - a malformed body let `res.json()` reject, so the raw parser message
 *     ("Expected property name or '}' in JSON at position 1") became the
 *     user-facing error;
 *   - a bare `HTTP 500` with no subject, cause or next step;
 *   - a thrown error that some pages never rendered at all, so a failed load
 *     showed as an empty list.
 *
 * `apiFetch` always rejects with an `ApiError` carrying the four things an
 * error surface has to say: what failed, why, what to do next, and whether
 * retrying is safe. `ErrorCard` renders exactly those.
 */

export type ErrorKind = 'offline' | 'timeout' | 'auth' | 'notFound' | 'conflict'
  | 'invalid' | 'unavailable' | 'server' | 'malformed' | 'unknown'

export class ApiError extends Error {
  readonly kind: ErrorKind
  readonly status: number
  /** What the user should do next. */
  readonly nextAction: string
  /** Whether re-issuing this exact request is safe. */
  readonly retrySafe: boolean
  /** Human-readable cause, when the server or browser gave one. */
  readonly cause?: string

  constructor(init: {
    message: string; kind: ErrorKind; status: number
    nextAction: string; retrySafe: boolean; cause?: string
  }) {
    super(init.message)
    this.name = 'ApiError'
    this.kind = init.kind
    this.status = init.status
    this.nextAction = init.nextAction
    this.retrySafe = init.retrySafe
    this.cause = init.cause
  }
}

/** Was this request a read? Reads are always safe to retry. */
function isRead(method: string) {
  return method === 'GET' || method === 'HEAD'
}

function classify(status: number): ErrorKind {
  if (status === 401 || status === 403) return 'auth'
  if (status === 404) return 'notFound'
  if (status === 409) return 'conflict'
  if (status === 410) return 'notFound'
  if (status === 413 || status === 422 || status === 400) return 'invalid'
  if (status === 503) return 'unavailable'
  if (status >= 500) return 'server'
  return 'unknown'
}

const NEXT_ACTION: Record<ErrorKind, string> = {
  offline: 'Check that Agent8088 is still running, then try again.',
  timeout: 'The server took too long to answer. Try again, or check its logs if it keeps happening.',
  auth: 'Check the credentials for this provider in Settings → Config, then try again.',
  notFound: 'Reload to pick up the current list — this item may have been renamed or removed.',
  conflict: 'Nothing was changed. Adjust the name or state and try again.',
  invalid: 'Correct the highlighted value and submit again.',
  unavailable: 'This depends on something that is not running yet. Start it, then try again.',
  server: 'Nothing was saved. Try again, and check the Agent8088 server log if it persists.',
  malformed: 'The server sent a reply this page could not read. Reload, then check the server log.',
  unknown: 'Try again. If it keeps failing, check the Agent8088 server log.',
}

/** A retry that cannot make things worse. Writes that already failed are safe
 *  to re-send only when the server told us it changed nothing. */
const RETRY_SAFE: Record<ErrorKind, boolean> = {
  offline: true, timeout: true, auth: true, notFound: true, conflict: true,
  invalid: false, unavailable: true, server: true, malformed: true, unknown: true,
}

/** Pull the server's own message out of a body that may not even be JSON. */
async function serverMessage(response: Response): Promise<string> {
  const text = await response.text().catch(() => '')
  if (!text) return ''
  try {
    const body = JSON.parse(text) as { error?: string; detail?: unknown }
    if (typeof body.error === 'string') return body.error
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) {
      // FastAPI validation errors: surface the field, not the whole schema.
      return body.detail
        .map((d) => {
          const item = d as { loc?: unknown[]; msg?: string }
          const field = Array.isArray(item.loc) ? item.loc.slice(1).join('.') : ''
          return field ? `${field}: ${item.msg ?? 'invalid'}` : (item.msg ?? 'invalid')
        })
        .join('; ')
    }
  } catch {
    // Not JSON (an HTML error page, a proxy notice). Don't show markup.
    if (/^\s*</.test(text)) return ''
    return text.slice(0, 200)
  }
  return ''
}

export interface ApiFetchOptions extends RequestInit {
  /** What the caller was doing, used in the message: "Could not load tools". */
  action?: string
  timeoutMs?: number
}

export async function apiFetch<T>(url: string, options: ApiFetchOptions = {}): Promise<T> {
  const { action = 'complete that request', timeoutMs = 30_000, ...init } = options
  const method = (init.method ?? 'GET').toUpperCase()
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  let response: Response
  try {
    response = await fetch(url, { ...init, signal: init.signal ?? controller.signal })
  } catch (error) {
    clearTimeout(timer)
    const aborted = error instanceof DOMException && error.name === 'AbortError'
    const kind: ErrorKind = aborted ? 'timeout' : 'offline'
    throw new ApiError({
      kind,
      status: 0,
      message: aborted ? `Timed out trying to ${action}.` : `Could not reach the Agent8088 server to ${action}.`,
      cause: aborted
        ? `No response within ${Math.round(timeoutMs / 1000)}s.`
        : 'The browser could not open a connection — the server may have stopped.',
      nextAction: NEXT_ACTION[kind],
      retrySafe: isRead(method) || RETRY_SAFE[kind],
    })
  }
  clearTimeout(timer)

  return handled<T>(response, action, method)
}

/**
 * Apply the same status + parse handling to a Response the caller fetched
 * itself (a few pages need the raw Response first — for a confirmation
 * round-trip, or to read a blob).
 */
export async function handled<T>(
  response: Response,
  action = 'complete that request',
  method = 'GET',
): Promise<T> {
  await assertOk(response, action, method)

  // 204 and friends have nothing to parse.
  if (response.status === 204) return undefined as T

  const text = await response.text()
  if (!text) return undefined as T
  try {
    return JSON.parse(text) as T
  } catch {
    throw new ApiError({
      kind: 'malformed',
      status: response.status,
      message: `The server's reply to ${action} was not readable.`,
      cause: 'The response was not valid JSON.',
      nextAction: NEXT_ACTION.malformed,
      retrySafe: true,
    })
  }
}

/** Throw a fully-formed ApiError if `response` is a failure; otherwise return. */
export async function assertOk(
  response: Response,
  action = 'complete that request',
  method = 'GET',
): Promise<void> {
  if (response.ok) return
  const kind = classify(response.status)
  const detail = await serverMessage(response)
  throw new ApiError({
    kind,
    status: response.status,
    message: detail || `Could not ${action}.`,
    cause: detail
      ? `Server returned HTTP ${response.status}.`
      : `Server returned HTTP ${response.status} with no detail.`,
    nextAction: NEXT_ACTION[kind],
    retrySafe: isRead(method) || RETRY_SAFE[kind],
  })
}

/** Narrow anything React Query hands back into something ErrorCard can render. */
export function asApiError(error: unknown, action = 'complete that request'): ApiError {
  if (error instanceof ApiError) return error
  const message = error instanceof Error ? error.message : String(error)
  const looksLikeParseFailure = /json|unexpected token|expected property name/i.test(message)
  return new ApiError({
    kind: looksLikeParseFailure ? 'malformed' : 'unknown',
    status: 0,
    message: looksLikeParseFailure ? `The server's reply to ${action} was not readable.` : message,
    cause: looksLikeParseFailure ? 'The response was not valid JSON.' : undefined,
    nextAction: looksLikeParseFailure ? NEXT_ACTION.malformed : NEXT_ACTION.unknown,
    retrySafe: true,
  })
}
