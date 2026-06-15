const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'
const API_KEY  = import.meta.env.VITE_API_KEY || ''

async function get(path, params = {}) {
  const url = new URL(BASE_URL + path)
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== '') url.searchParams.set(k, v)
  })

  const headers = {}
  if (API_KEY) headers['X-API-Key'] = API_KEY

  const res = await fetch(url, { headers })
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText} — ${path}`)
  }
  return res.json()
}

export const getEvent      = (eventId)              => get(`/events/${eventId}`)
export const getResults    = (sessionId)            => get(`/sessions/${sessionId}/results`)
export const getStints     = (sessionId, params)    => get(`/sessions/${sessionId}/stints`, params)
export const getPace       = (sessionId, params)    => get(`/sessions/${sessionId}/pace`, params)
export const getGaps       = (sessionId, params)    => get(`/sessions/${sessionId}/gaps`, params)
export const getCarLaps    = (sessionId, car)       => get(`/sessions/${sessionId}/laps/${car}`)
export const getRaceControl = (sessionId)           => get(`/sessions/${sessionId}/race-control`)