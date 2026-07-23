import { useEffect, useState, useCallback } from 'react'
import { getEvent, getResults, getStints, getGaps, getCarLaps, getRaceControl } from '../api'
import Leaderboard from '../components/Leaderboard'
import StintChart from '../components/StintChart'
import LapEvolutionChart from '../components/LapEvolutionChart'
import GapChart from '../components/GapChart'
import RaceControl from '../components/RaceControl'
import { Link } from 'react-router-dom'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'
const API_KEY  = import.meta.env.VITE_API_KEY || ''

async function apiFetch(path, params = {}) {
  const url = new URL(BASE_URL + path)
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== '') url.searchParams.set(k, v)
  })
  const headers = {}
  if (API_KEY) headers['X-API-Key'] = API_KEY
  const res = await fetch(url, { headers })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

const SERIES_LIST = ['WEC', 'ELMS', 'ALMS', 'LEMANSCUP', 'IMSA']
const CLASSES     = ['HYPERCAR', 'LMP2', 'LMGT3', 'GTP', 'GTD', 'GTLM', 'DPi', 'LMP1']

// Default session — Le Mans 2026 Race
const DEFAULT = { series: 'WEC', year: 2026, eventId: 621, sessionId: 6556 }

export default function Dashboard() {
  // ── Selector state ────────────────────────────────────────
  const [series,   setSeries]   = useState(DEFAULT.series)
  const [seasons,  setSeasons]  = useState([])
  const [year,     setYear]     = useState(DEFAULT.year)
  const [events,   setEvents]   = useState([])
  const [eventId,  setEventId]  = useState(DEFAULT.eventId)
  const [sessions, setSessions] = useState([])
  const [sessionId, setSessionId] = useState(DEFAULT.sessionId)

  // ── Dashboard state ───────────────────────────────────────
  const [event,        setEvent]        = useState(null)
  const [results,      setResults]      = useState([])
  const [stints,       setStints]       = useState([])
  const [gaps,         setGaps]         = useState([])
  const [laps,         setLaps]         = useState([])
  const [raceControl,  setRaceControl]  = useState([])
  const [selectedClass, setSelectedClass] = useState('HYPERCAR')
  const [selectedCar,   setSelectedCar]   = useState(null)
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState(null)

  // ── Load seasons when series changes ─────────────────────
  useEffect(() => {
    apiFetch(`/series/${series}/seasons`)
      .then((data) => {
        const sorted = [...data].sort((a, b) => b.year - a.year)
        setSeasons(sorted)
        // Only reset year if current year not in new series
        const years = sorted.map(s => s.year)
        if (!years.includes(year)) setYear(sorted[0]?.year)
      })
      .catch(() => {})
  }, [series])

  // ── Load events when series/year changes ─────────────────
  useEffect(() => {
    if (!year) return
    apiFetch(`/series/${series}/seasons/${year}/events`)
      .then((data) => {
        setEvents(data)
        if (series === DEFAULT.series && year === DEFAULT.year) {
          setEventId(DEFAULT.eventId)
        } else {
          setEventId(data[0]?.id || null)
        }
      })
      .catch(() => {})
  }, [series, year])

  // ── Load sessions when event changes ─────────────────────
  useEffect(() => {
    if (!eventId) return
    apiFetch(`/series/${series}/seasons/${year}/events/${eventId}/sessions`)
      .then((data) => {
        setSessions(data)
        if (series === DEFAULT.series && year === DEFAULT.year && eventId === DEFAULT.eventId) {
          setSessionId(DEFAULT.sessionId)
        } else {
          // Auto-select first Race session
          const race = data.find(s =>
            ['Race', 'Race 1', 'Race 2'].includes(s.name) && s.session_type === 'Race'
          )
          setSessionId(race?.id || data[0]?.id || null)
        }
      })
      .catch(() => {})
  }, [series, year, eventId])

  // ── Load dashboard data when session changes ──────────────
  useEffect(() => {
    if (!sessionId) return
    let cancelled = false

    async function load() {
      try {
        setLoading(true)
        setError(null)
        const [eventData, resultsData] = await Promise.all([
          getEvent(eventId),
          getResults(sessionId),
        ])
        if (cancelled) return
        setEvent(eventData)
        setResults(resultsData)

        // Auto-select class leader
        const classOrder = ['HYPERCAR', 'GTP', 'LMP2', 'LMP1']
        let leaderClass = 'HYPERCAR'
        for (const cls of classOrder) {
          if (resultsData.some(r => r.car_class === cls)) {
            leaderClass = cls
            break
          }
        }
        setSelectedClass(leaderClass)

        const leader = resultsData.find(r => r.car_class === leaderClass && r.position === 1)
        if (leader) setSelectedCar(String(leader.car_number))

        getRaceControl(sessionId)
          .then(data => !cancelled && setRaceControl(data))
          .catch(() => !cancelled && setRaceControl([]))
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [sessionId, eventId])

  // ── Load stints/gaps when class changes ──────────────────
  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    async function load() {
      try {
        const [stintsData, gapsData] = await Promise.all([
          getStints(sessionId, { car_class: selectedClass }),
          getGaps(sessionId, { car_class: selectedClass, max_laps: 60 }),
        ])
        if (cancelled) return
        setStints(stintsData)
        setGaps(gapsData)
      } catch {
        if (!cancelled) { setStints([]); setGaps([]) }
      }
    }
    load()
    return () => { cancelled = true }
  }, [sessionId, selectedClass])

  // ── Load laps when car changes ────────────────────────────
  useEffect(() => {
    if (!selectedCar || !sessionId) return
    let cancelled = false
    getCarLaps(sessionId, selectedCar)
      .then(data => !cancelled && setLaps(data))
      .catch(() => !cancelled && setLaps([]))
    return () => { cancelled = true }
  }, [selectedCar, sessionId])

  const classResults    = results.filter(r => r.car_class === selectedClass)
  const winner          = results.find(r => r.position === 1)
  const totalLaps       = winner?.laps_completed
  const currentSession  = sessions.find(s => s.id === sessionId)
  const currentEvent    = events.find(e => e.id === eventId)
  const availableClasses = [...new Set(results.map(r => r.car_class).filter(Boolean))]

  return (
    <div className="dashboard">
      {/* Back link */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '12px 0 0', marginBottom: 0 }}>
        <Link to="/" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-dim)', letterSpacing: '0.08em' }}>
          ← OPENWEC.COM
        </Link>
      </div>

      {/* ── Session selector ── */}
      <div className="session-selector">
        <select className="select" value={series} onChange={e => setSeries(e.target.value)}>
          {SERIES_LIST.map(s => <option key={s} value={s}>{s}</option>)}
        </select>

        <select className="select" value={year} onChange={e => setYear(Number(e.target.value))}>
          {seasons.map(s => <option key={s.raw_id} value={s.year}>{s.label}</option>)}
        </select>

        <select className="select" value={eventId || ''} onChange={e => setEventId(Number(e.target.value))}>
          {events.map(e => (
            <option key={e.id} value={e.id}>
              {e.round ? `R${e.round} · ` : ''}{e.name}
            </option>
          ))}
        </select>

        <select className="select" value={sessionId || ''} onChange={e => setSessionId(Number(e.target.value))}>
          {sessions.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>

      {loading && <div className="state-message">Loading race data…</div>}
      {error && <div className="state-message error">Failed to load: {error}</div>}

      {!loading && !error && (
        <>
          {/* Header */}
          <div className="event-header">
            <div>
              <h1 className="event-title">{event?.name?.toUpperCase() || '—'}</h1>
              <div className="event-subtitle">
                {series} · {year} · {currentSession?.name}
              </div>
            </div>
            <div className="event-stats">
              <div className="stat">
                <div className="stat-value">{totalLaps ?? '—'}</div>
                <div className="stat-label">Laps</div>
              </div>
              <div className="stat">
                <div className="stat-value">#{winner?.car_number ?? '—'}</div>
                <div className="stat-label">Leader</div>
              </div>
              <div className="stat">
                <div className="stat-value">{winner?.vehicle?.split(' ')[0] ?? '—'}</div>
                <div className="stat-label">{winner?.team ?? ''}</div>
              </div>
            </div>
          </div>

          {/* Leaderboard */}
          <div className="panel">
            <div className="panel-header">
              <div className="panel-title">Classification</div>
              <select className="select" value={selectedClass} onChange={e => setSelectedClass(e.target.value)}>
                {availableClasses.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <Leaderboard results={classResults} onSelectCar={setSelectedCar} selectedCar={selectedCar} />
          </div>

          {/* Race Control */}
          <div className="panel">
            <div className="panel-header">
              <div className="panel-title">Race Control — SC / FCY Periods</div>
            </div>
            <RaceControl periods={raceControl} />
          </div>

          {/* Stint chart */}
          <div className="panel">
            <div className="panel-header">
              <div className="panel-title">
                Stint Strategy — <span className={`class-chip ${selectedClass}`}>{selectedClass}</span>
              </div>
            </div>
            {stints.length > 0
              ? <StintChart stints={stints} totalLaps={totalLaps} />
              : <div className="state-message">No stint data for this session.</div>}
          </div>

          {/* Lap evolution */}
          <div className="panel">
            <div className="panel-header">
              <div className="panel-title">Lap Evolution — Car #{selectedCar}</div>
              <select className="select" value={selectedCar || ''} onChange={e => setSelectedCar(e.target.value)}>
                {classResults.map(r => (
                  <option key={r.car_number} value={String(r.car_number)}>
                    #{r.car_number} — {r.team}
                  </option>
                ))}
              </select>
            </div>
            <LapEvolutionChart laps={laps} raceControl={raceControl} />
          </div>

          {/* Gap to leader */}
          <div className="panel">
            <div className="panel-header">
              <div className="panel-title">
                Gap to Leader — <span className={`class-chip ${selectedClass}`}>{selectedClass}</span>
                <span className="text-dim" style={{ marginLeft: 8, fontSize: 11 }}>(first 60 laps)</span>
              </div>
            </div>
            <GapChart gaps={gaps} raceControl={raceControl} />
          </div>
        </>
      )}
    </div>
  )
}