import { useEffect, useState } from 'react'
import { getEvent, getResults, getStints, getGaps, getCarLaps, getRaceControl } from './api'
import Leaderboard from './components/Leaderboard'
import StintChart from './components/StintChart'
import LapEvolutionChart from './components/LapEvolutionChart'
import GapChart from './components/GapChart'
import RaceControl from './components/RaceControl'

// Le Mans 2026 — WEC Round, Race session
const EVENT_ID   = 621
const SESSION_ID = 6556
const CLASSES    = ['HYPERCAR', 'LMP2', 'LMGT3']

export default function App() {
  const [event, setEvent]     = useState(null)
  const [results, setResults] = useState([])
  const [stints, setStints]   = useState([])
  const [gaps, setGaps]       = useState([])
  const [laps, setLaps]       = useState([])
  const [raceControl, setRaceControl] = useState([])

  const [selectedClass, setSelectedClass] = useState('HYPERCAR')
  const [selectedCar, setSelectedCar]     = useState(null)

  const [error, setError]     = useState(null)
  const [loading, setLoading] = useState(true)

  // Initial load
  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setLoading(true)
        const [eventData, resultsData] = await Promise.all([
          getEvent(EVENT_ID),
          getResults(SESSION_ID),
        ])
        if (cancelled) return
        setEvent(eventData)
        setResults(resultsData)

        // Race control (SC/FCY periods) — non-critical, fail silently
        getRaceControl(SESSION_ID)
          .then((data) => !cancelled && setRaceControl(data))
          .catch(() => !cancelled && setRaceControl([]))

        // Default selected car = class leader
        const leader = resultsData.find((r) => r.car_class === 'HYPERCAR' && r.position === 1)
        if (leader) setSelectedCar(leader.car_number)
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  // Stints + gaps when class changes
  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const [stintsData, gapsData] = await Promise.all([
          getStints(SESSION_ID, { car_class: selectedClass }),
          getGaps(SESSION_ID, { car_class: selectedClass, max_laps: 60 }),
        ])
        if (cancelled) return
        setStints(stintsData)
        setGaps(gapsData)
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    }
    load()
    return () => { cancelled = true }
  }, [selectedClass])

  // Laps when selected car changes
  useEffect(() => {
    if (!selectedCar) return
    let cancelled = false
    async function load() {
      try {
        const lapsData = await getCarLaps(SESSION_ID, selectedCar)
        if (!cancelled) setLaps(lapsData)
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    }
    load()
    return () => { cancelled = true }
  }, [selectedCar])

  if (loading) {
    return <div className="state-message">Loading race data…</div>
  }

  if (error) {
    return (
      <div className="state-message error">
        Failed to load data: {error}
        <br />
        Make sure the OpenWEC API is running at localhost:8000.
      </div>
    )
  }

  const classResults = results.filter((r) => r.car_class === selectedClass)
  const winner = results.find((r) => r.position === 1)
  const totalLaps = winner?.laps_completed

  return (
    <div className="dashboard">
      {/* Header */}
      <div className="event-header">
        <div>
          <h1 className="event-title">{event?.name?.toUpperCase() || 'RACE'}</h1>
          <div className="event-subtitle">
            {event?.series} · {event?.season} · ROUND {event?.round ?? '—'}
          </div>
        </div>
        <div className="event-stats">
          <div className="stat">
            <div className="stat-value">{totalLaps ?? '—'}</div>
            <div className="stat-label">Laps</div>
          </div>
          <div className="stat">
            <div className="stat-value">#{winner?.car_number ?? '—'}</div>
            <div className="stat-label">Winner</div>
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
          <select
            className="select"
            value={selectedClass}
            onChange={(e) => setSelectedClass(e.target.value)}
          >
            {CLASSES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
        <Leaderboard
          results={classResults}
          onSelectCar={setSelectedCar}
          selectedCar={selectedCar}
        />
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
        {stints.length > 0 ? (
          <StintChart stints={stints} totalLaps={totalLaps} />
        ) : (
          <div className="state-message">Loading stints…</div>
        )}
      </div>

      {/* Lap evolution */}
      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">
            Lap Evolution — Car #{selectedCar}
          </div>
          <select
            className="select"
            value={selectedCar || ''}
            onChange={(e) => setSelectedCar(e.target.value)}
          >
            {classResults.map((r) => (
              <option key={r.car_number} value={r.car_number}>
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
    </div>
  )
}