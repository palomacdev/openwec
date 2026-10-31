// OpenWEC — API client for the public dashboard.
//
// There is no credential here, and there must never be one. Vite inlines
// build-time variables into the bundle, so a key placed in this file is served
// in plain text to every visitor. The endpoints below are public by decision;
// the API meters them per client address instead of per key.

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

export class ApiError extends Error {
  constructor(status, message, path, retryAfter = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status      // 0 when the request never reached the API
    this.path = path
    this.retryAfter = retryAfter
  }
}

function describe(status, detail, retryAfter) {
  if (status === 429) {
    const espera = retryAfter
      ? `Try again in ${retryAfter} second${retryAfter === 1 ? '' : 's'}.`
      : 'Try again in a minute.'
    return `Too many requests — the public API limits how often data can be loaded. ${espera}`
  }
  if (status === 401 || status === 403) {
    // The dashboard only calls public routes, so this means the API changed
    // under it. Say so plainly rather than asking the visitor for a key.
    return 'This data is no longer public. The dashboard needs updating — please report it.'
  }
  if (status === 404) return 'No data for this selection yet.'
  if (status >= 500) return 'The OpenWEC API is having trouble. Try again in a moment.'
  return detail || `Request failed (${status}).`
}

async function get(path, params = {}) {
  const url = new URL(BASE_URL + path)
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== '') url.searchParams.set(k, v)
  })

  let res
  try {
    res = await fetch(url)
  } catch {
    throw new ApiError(0, 'Could not reach the OpenWEC API. Check your connection and try again.', path)
  }

  if (!res.ok) {
    const cabecalho = Number(res.headers.get('Retry-After'))
    const retryAfter = Number.isFinite(cabecalho) && cabecalho > 0 ? cabecalho : null
    let detail = null
    try {
      detail = (await res.json())?.detail
    } catch {
      // A proxy error page is not JSON. The status is enough to explain it.
    }
    throw new ApiError(res.status, describe(res.status, detail, retryAfter), path, retryAfter)
  }

  return res.json()
}

async function post(path, body) {
  let res
  try {
    res = await fetch(BASE_URL + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Could not reach the OpenWEC API. Check your connection and try again.', path)
  }

  let data = null
  try {
    data = await res.json()
  } catch {
    // Same as above: a non-JSON body is still a usable status.
  }

  if (!res.ok) {
    const cabecalho = Number(res.headers.get('Retry-After'))
    const retryAfter = Number.isFinite(cabecalho) && cabecalho > 0 ? cabecalho : null
    throw new ApiError(res.status, describe(res.status, data?.detail, retryAfter), path, retryAfter)
  }

  return data
}

// ── Catalogue ────────────────────────────────────────────────
export const getSeasons  = (series)                 => get(`/series/${series}/seasons`)
export const getEvents   = (series, year)           => get(`/series/${series}/seasons/${year}/events`)
export const getSessions = (series, year, eventId)  => get(`/series/${series}/seasons/${year}/events/${eventId}/sessions`)
export const getEvent    = (eventId)                => get(`/events/${eventId}`)

// ── Session data ─────────────────────────────────────────────
export const getResults     = (sessionId)         => get(`/sessions/${sessionId}/results`)
export const getStints      = (sessionId, params) => get(`/sessions/${sessionId}/stints`, params)
export const getGaps        = (sessionId, params) => get(`/sessions/${sessionId}/gaps`, params)
export const getCarLaps     = (sessionId, car)    => get(`/sessions/${sessionId}/laps/${car}`)
export const getRaceControl = (sessionId)         => get(`/sessions/${sessionId}/race-control`)

// ── Drivers ──────────────────────────────────────────────────
export const getDriver        = (driverId)         => get(`/drivers/${driverId}`)
export const getDriverResults = (driverId, limit = 100) => get(`/drivers/${driverId}/results`, { limit })

// ── API keys ─────────────────────────────────────────────────
// The one public write. Rate limited per address by the API, so a 429 here is
// expected under abuse and is reported like any other.
export const requestApiKey = (form) => post('/api-keys/request', form)

// `pace`, `pit-window` and `drivers/{id}/consistency` are deliberately absent:
// they still require a key, and a public bundle has none to give.
