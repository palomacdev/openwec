import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import SiteNav from '../components/SiteNav'
import SiteFooter from '../components/SiteFooter'

function formatDrivers(drivers) {
  if (!drivers || !Array.isArray(drivers)) return ''
  return drivers
    .sort((a, b) => (a.slot || 0) - (b.slot || 0))
    .map(d => `${d.first_name || ''} ${d.last_name || ''}`.trim())
    .join(' / ')
}

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

async function apiFetch(path) {
  const res = await fetch(BASE_URL + path)
  if (!res.ok) throw new Error(`${res.status} ${path}`)
  return res.json()
}

const CLASS_COLOR = {
  HYPERCAR:    '#E63946',
  LMP2:        '#4895EF',
  LMGT3:       '#6FCF97',
  GTP:         '#E63946',
  GTD:         '#6FCF97',
  'LMGTE Pro': '#F4A261',
  'LMGTE Am':  '#F4A261',
  GTLM:        '#6FCF97',
  DPi:         '#4895EF',
}

function formatLapTime(s) {
  if (!s) return '—'
  const m = Math.floor(s / 60)
  const sec = (s % 60).toFixed(3)
  return `${m}:${sec.padStart(6, '0')}`
}

function formatGap(gap) {
  if (gap == null) return '—'
  return `+${gap.toFixed(3)}s`
}

const SERIES = ['WEC', 'ELMS', 'ALMS', 'LEMANSCUP', 'IMSA']
const SESSION_RACE_TYPES = ['Race', 'Race 1', 'Race 2']

export default function Explore() {
  const [series,   setSeries]   = useState('WEC')
  const [seasons,  setSeasons]  = useState([])
  const [season,   setSeason]   = useState(null)
  const [events,   setEvents]   = useState([])
  const [event,    setEvent]    = useState(null)
  const [sessions, setSessions] = useState([])
  const [session,  setSession]  = useState(null)
  const [results,  setResults]  = useState([])
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)

  // Load seasons when series changes
  useEffect(() => {
    setSeason(null); setEvents([]); setEvent(null)
    setSessions([]); setSession(null); setResults([])
    setError(null)
    apiFetch(`/series/${series}/seasons`)
      .then((data) => {
        const sorted = [...data].sort((a, b) => b.year - a.year)
        setSeasons(sorted)
        if (sorted.length) setSeason(sorted[0])
      })
      .catch((e) => setError(e.message))
  }, [series])

  // Load events when season changes
  useEffect(() => {
    if (!season) return
    setEvent(null); setSessions([]); setSession(null); setResults([])
    apiFetch(`/series/${series}/seasons/${season.year}/events`)
      .then((data) => setEvents(data))
      .catch((e) => setError(e.message))
  }, [season])

  // Load sessions when event changes
  useEffect(() => {
    if (!event) return
    setSession(null); setResults([])
    apiFetch(`/series/${series}/seasons/${season.year}/events/${event.id}/sessions`)
      .then((data) => {
        setSessions(data)
        // Auto-select first Race session
        const race = data.find(s => SESSION_RACE_TYPES.some(t => s.name === t))
        if (race) setSession(race)
        else if (data.length) setSession(data[0])
      })
      .catch((e) => setError(e.message))
  }, [event])

  // Load results when session changes
  useEffect(() => {
    if (!session) return
    setLoading(true); setResults([])
    apiFetch(`/sessions/${session.id}/results`)
      .then((data) => setResults(data))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [session])

  const isRaceSession = session && SESSION_RACE_TYPES.some(t => session.name === t)

  return (
    <div className="home">
      <SiteNav />

      {/* Header */}
      <section style={{ marginBottom: 48 }}>
        <div className="hero-eyebrow mono">EXPLORE DATA</div>
        <h1 className="about-title">Browse races, sessions, and results.</h1>
        <p className="about-lede">
          Select a series, season, event, and session to view the classification.
          Public data — no API key needed.
        </p>
      </section>

      {/* Selectors */}
      <section className="explore-selectors">
        {/* Series */}
        <div className="explore-selector-group">
          <div className="selector-label mono">Series</div>
          <div className="selector-pills">
            {SERIES.map((s) => (
              <button
                key={s}
                className={`selector-pill ${series === s ? 'active' : ''}`}
                onClick={() => setSeries(s)}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        {/* Season */}
        {seasons.length > 0 && (
          <div className="explore-selector-group">
            <div className="selector-label mono">Season</div>
            <div className="selector-pills">
              {seasons.map((s) => (
                <button
                  key={s.raw_id}
                  className={`selector-pill ${season?.raw_id === s.raw_id ? 'active' : ''}`}
                  onClick={() => setSeason(s)}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Event */}
        {events.length > 0 && (
          <div className="explore-selector-group">
            <div className="selector-label mono">Event</div>
            <select
              className="select"
              value={event?.id || ''}
              onChange={(e) => {
                const found = events.find(ev => String(ev.id) === e.target.value)
                setEvent(found || null)
              }}
            >
              <option value="">Select event…</option>
              {events.map((ev) => (
                <option key={ev.id} value={ev.id}>
                  {ev.round ? `R${ev.round} — ` : ''}{ev.name}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Session */}
        {sessions.length > 0 && (
          <div className="explore-selector-group">
            <div className="selector-label mono">Session</div>
            <div className="selector-pills">
              {sessions.map((s) => (
                <button
                  key={s.id}
                  className={`selector-pill ${session?.id === s.id ? 'active' : ''}`}
                  onClick={() => setSession(s)}
                >
                  {s.name}
                </button>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* Results */}
      {session && (
        <section style={{ marginTop: 48 }}>
          <div className="panel">
            <div className="panel-header">
              <div className="panel-title">
                {event?.name} — {session.name}
                {session.session_type && (
                  <span className="text-dim" style={{ marginLeft: 8, fontWeight: 400 }}>
                    {session.session_type}
                  </span>
                )}
              </div>
              {isRaceSession && (
                <Link
                  to="/dashboard"
                  className="cta-primary"
                  style={{ fontSize: 11, padding: '6px 14px' }}
                >
                  Open in Dashboard →
                </Link>
              )}
            </div>

            {error && <div className="state-message error">{error}</div>}

            {loading && <div className="state-message">Loading results…</div>}

            {!loading && results.length === 0 && (
              <div className="state-message">No results for this session.</div>
            )}

            {!loading && results.length > 0 && (
              <table className="leaderboard">
                <thead>
                  <tr>
                    <th>Pos</th>
                    <th>Class</th>
                    <th>Car</th>
                    <th>Team / Drivers</th>
                    <th>Vehicle</th>
                    <th style={{ textAlign: 'right' }}>Laps</th>
                    <th style={{ textAlign: 'right' }}>Gap</th>
                    <th style={{ textAlign: 'right' }}>Best Lap</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r) => (
                    <tr key={r.car_number}>
                      <td className="pos-digit">{r.position ?? '—'}</td>
                      <td>
                        {r.car_class && (
                          <span
                            className="class-chip"
                            style={{ color: CLASS_COLOR[r.car_class] || 'var(--text-dim)' }}
                          >
                            {r.car_class}
                          </span>
                        )}
                      </td>
                      <td><span className="car-number">#{r.car_number}</span></td>
                      <td>
                        <div className="team-cell">
                          <span className="team-name">{r.team || '—'}</span>
                          {r.drivers && <span className="drivers">{formatDrivers(r.drivers)}</span>}
                        </div>
                      </td>
                      <td className="text-dim">{r.vehicle || '—'}</td>
                      <td className="mono" style={{ textAlign: 'right' }}>
                        {r.laps_completed ?? '—'}
                      </td>
                      <td className="mono text-dim" style={{ textAlign: 'right' }}>
                        {r.position === 1 ? (
                          <span style={{ color: 'var(--accent)' }}>LEADER</span>
                        ) : formatGap(r.gap_to_first_s)}
                      </td>
                      <td className="mono" style={{ textAlign: 'right' }}>
                        {formatLapTime(r.fl_time_s)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      )}

      <div style={{ marginTop: 80 }}>
        <SiteFooter />
      </div>
    </div>
  )
}