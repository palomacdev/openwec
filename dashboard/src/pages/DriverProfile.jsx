import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import SiteNav from '../components/SiteNav'
import SiteFooter from '../components/SiteFooter'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

async function apiFetch(path) {
  const res = await fetch(BASE_URL + path)
  if (!res.ok) throw new Error(`${res.status} ${path}`)
  return res.json()
}

const COUNTRY_FLAGS = {
  PRT: '🇵🇹', GBR: '🇬🇧', ITA: '🇮🇹', FRA: '🇫🇷', DEU: '🇩🇪',
  JPN: '🇯🇵', BRA: '🇧🇷', CHE: '🇨🇭', NLD: '🇳🇱', AUS: '🇦🇺',
  MEX: '🇲🇽', DNK: '🇩🇰', AUT: '🇦🇹', ESP: '🇪🇸', FIN: '🇫🇮',
  SWE: '🇸🇪', BEL: '🇧🇪', USA: '🇺🇸', CAN: '🇨🇦', CHN: '🇨🇳',
  KOR: '🇰🇷', ARG: '🇦🇷', NZL: '🇳🇿', ZAF: '🇿🇦', NOR: '🇳🇴',
  POL: '🇵🇱', HUN: '🇭🇺', MCO: '🇲🇨', LUX: '🇱🇺', IRL: '🇮🇪',
  QAT: '🇶🇦', ARE: '🇦🇪', THA: '🇹🇭', IDN: '🇮🇩', CZE: '🇨🇿',
}

const RATING_COLOR = {
  Platinum: '#E5E4E2',
  Gold:     '#FFB000',
  Silver:   '#C0C0C0',
  Bronze:   '#CD7F32',
}

function formatDate(dateStr) {
  if (!dateStr) return '—'
  return dateStr.slice(0, 10)
}

export default function DriverProfile() {
  const { id } = useParams()
  const [profile,  setProfile]  = useState(null)
  const [results,  setResults]  = useState([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setLoading(true)
        const [profileData, resultsData] = await Promise.all([
          apiFetch(`/drivers/${id}`),
          apiFetch(`/drivers/${id}/results?limit=100`),
        ])
        if (cancelled) return
        setProfile(profileData)
        setResults(resultsData)
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [id])

  if (loading) return (
    <div className="home">
      <SiteNav />
      <div className="state-message">Loading driver profile…</div>
    </div>
  )

  if (error) return (
    <div className="home">
      <SiteNav />
      <div className="state-message error">Failed to load driver: {error}</div>
    </div>
  )

  const flag   = COUNTRY_FLAGS[profile.country] || ''
  const rating = profile.imsa_driver_rating
  const wins   = results.filter(r => r.position === 1).length
  const podiums = results.filter(r => r.position <= 3).length

  return (
    <div className="home">
      <SiteNav />

      {/* Header */}
      <section className="driver-header">
        <div>
          <div className="hero-eyebrow mono">DRIVER PROFILE</div>
          <h1 className="driver-name">
            {flag && <span style={{ marginRight: 12 }}>{flag}</span>}
            {profile.first_name} {profile.last_name}
          </h1>
          <div className="driver-meta">
            {profile.country && (
              <span className="driver-tag mono">{profile.country}</span>
            )}
            {rating && (
              <span
                className="driver-tag mono"
                style={{ color: RATING_COLOR[rating] || 'var(--text-dim)', borderColor: RATING_COLOR[rating] || 'var(--border)' }}
              >
                IMSA {rating}
              </span>
            )}
            {profile.series?.map(s => (
              <span key={s} className="driver-tag mono">{s}</span>
            ))}
          </div>
        </div>
      </section>

      {/* Career stats */}
      <section className="stats-bar" style={{ marginBottom: 40 }}>
        {[
          { value: profile.total_races,            label: 'Races' },
          { value: wins,                            label: 'Wins' },
          { value: podiums,                         label: 'Podiums' },
          { value: profile.classes?.filter(Boolean).length || 0, label: 'Classes' },
          { value: formatDate(profile.first_race),  label: 'First race' },
          { value: formatDate(profile.last_race),   label: 'Last race' },
        ].map((s) => (
          <div key={s.label} className="stat-item">
            <div className="stat-big mono">{s.value ?? '—'}</div>
            <div className="stat-small">{s.label}</div>
          </div>
        ))}
      </section>

      {/* Race history */}
      <div className="panel">
        <div className="panel-header">
          <div className="panel-title">Race History</div>
          <span className="mono text-dim" style={{ fontSize: 12 }}>
            {results.length} races
          </span>
        </div>
        <table className="leaderboard">
          <thead>
            <tr>
              <th>Series</th>
              <th>Season</th>
              <th>Event</th>
              <th>Car</th>
              <th>Class</th>
              <th>Team</th>
              <th style={{ textAlign: 'right' }}>Pos</th>
              <th style={{ textAlign: 'right' }}>Laps</th>
            </tr>
          </thead>
          <tbody>
            {results.map((r, i) => (
              <tr key={i}>
                <td className="mono" style={{ fontSize: 11 }}>{r.series}</td>
                <td className="mono text-dim" style={{ fontSize: 11 }}>{r.season}</td>
                <td style={{ fontSize: 12 }}>{r.event}</td>
                <td><span className="car-number">#{r.car_number}</span></td>
                <td>
                  {r.car_class && (
                    <span className={`class-chip ${r.car_class}`}>{r.car_class}</span>
                  )}
                </td>
                <td className="text-dim" style={{ fontSize: 11 }}>{r.team || '—'}</td>
                <td className="mono" style={{ textAlign: 'right', fontWeight: r.position === 1 ? 700 : 400, color: r.position === 1 ? 'var(--accent)' : 'var(--text)' }}>
                  {r.position ?? '—'}
                </td>
                <td className="mono text-dim" style={{ textAlign: 'right', fontSize: 11 }}>
                  {r.laps_completed ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ marginTop: 60 }}>
        <SiteFooter />
      </div>
    </div>
  )
}