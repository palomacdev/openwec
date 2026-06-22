import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getResults } from '../api'
import SiteNav from '../components/SiteNav'
import SiteFooter from '../components/SiteFooter'

const SESSION_ID = 6556  // Le Mans 2026 Race

const CLASS_COLOR = {
  HYPERCAR: '#E63946',
  LMP2:     '#4895EF',
  LMGT3:    '#6FCF97',
}

function formatGap(gap) {
  if (gap == null) return 'LEADER'
  return `+${gap.toFixed(3)}s`
}

function formatLapTime(s) {
  if (!s) return '—'
  const m = Math.floor(s / 60)
  const sec = (s % 60).toFixed(3)
  return `${m}:${sec.padStart(6, '0')}`
}

export default function Home() {
  const [results, setResults] = useState([])
  const [loaded, setLoaded]   = useState(false)

  useEffect(() => {
    getResults(SESSION_ID)
      .then((data) => { setResults(data.slice(0, 10)); setLoaded(true) })
      .catch(() => setLoaded(true))
  }, [])

  return (
    <div className="home">
      {/* ── NAV ── */}
      <SiteNav />

      {/* ── HERO ── */}
      <section className="hero">
        <div className="hero-left">
          <div className="hero-eyebrow mono">ENDURANCE RACING DATA PLATFORM</div>
          <h1 className="hero-title">
            Every lap.<br />
            Every stint.<br />
            Every series.
          </h1>
          <p className="hero-sub">
            Open access to WEC, ELMS, ALMS, Le Mans Cup and IMSA data.
            REST API, Python SDK, live dashboard.
          </p>
          <div className="hero-ctas">
            <Link to="/explore" className="cta-primary">Explore the data →</Link>
            <a href="https://api.openwec.com/docs" className="cta-secondary" target="_blank" rel="noopener noreferrer">API docs</a>
          </div>
        </div>

        {/* Live timing tower */}
        <div className="hero-tower">
          <div className="tower-header mono">
            <span style={{ color: 'var(--accent)' }}>● LIVE</span>
            <span style={{ color: 'var(--text-dim)', marginLeft: 12 }}>24H LE MANS 2026 — FINAL</span>
          </div>
          <div className="tower-rows">
            {!loaded && [1,2,3,4,5,6,7,8,9,10].map(i => (
              <div key={i} className="tower-row skeleton" />
            ))}
            {loaded && results.map((r) => (
              <div key={r.car_number} className="tower-row">
                <span className="tower-pos mono">{r.position}</span>
                <span
                  className="tower-class-dot"
                  style={{ background: CLASS_COLOR[r.car_class] || 'var(--text-dim)' }}
                />
                <span className="tower-car mono">#{r.car_number}</span>
                <span className="tower-team">{r.team}</span>
                <span className="tower-gap mono">
                  {r.position === 1 ? 'LEADER' : formatGap(r.gap_to_first_s)}
                </span>
              </div>
            ))}
          </div>
          <div className="tower-footer mono">
            <Link to="/explore" style={{ color: 'var(--accent)', fontSize: 11 }}>
              View full explore →
            </Link>
          </div>
        </div>
      </section>

      {/* ── STATS ── */}
      <section className="stats-bar">
        {[
          { value: '5',       label: 'Series' },
          { value: '510+',    label: 'Events' },
          { value: '5,150',   label: 'Sessions' },
          { value: '1.77M+',  label: 'Laps' },
          { value: '2012',    label: 'Since' },
        ].map((s) => (
          <div key={s.label} className="stat-item">
            <div className="stat-big mono">{s.value}</div>
            <div className="stat-small">{s.label}</div>
          </div>
        ))}
      </section>

      {/* ── WHAT IS OPENWEC ── */}
      <section className="features">
        <div className="features-header">
          <div className="section-eyebrow mono">WHAT IS OPENWEC</div>
          <h2 className="section-title">Built for analysts, developers, and fans who want the real data.</h2>
        </div>
        <div className="features-grid">
          <div className="feature-card">
            <div className="feature-icon mono">API</div>
            <h3>REST API</h3>
            <p>
              Full access to races, sessions, lap times, stints, and analytics.
              Public endpoints need no key. Protected analytics endpoints require a free API key.
            </p>
            <a href="https://api.openwec.com/docs" className="feature-link" target="_blank" rel="noopener noreferrer">
              Browse the docs →
            </a>
          </div>
          <div className="feature-card">
            <div className="feature-icon mono">SDK</div>
            <h3>Python SDK</h3>
            <p>
              FastF1-inspired interface. Load any session in one line —
              results, laps, stints, and pace as pandas DataFrames, ready for analysis.
            </p>
            <a href="https://github.com/palomacdev/openwec" className="feature-link" target="_blank" rel="noopener noreferrer">
              View on GitHub →
            </a>
          </div>
          <div className="feature-card">
            <div className="feature-icon mono">VIZ</div>
            <h3>Live Dashboard</h3>
            <p>
              Timing tower, strategy charts, lap evolution, gap to leader.
              Race Control overlays for SC and FCY periods.
            </p>
            <Link to="/dashboard" className="feature-link">
              Open dashboard →
            </Link>
          </div>
        </div>
      </section>

      {/* ── SERIES ── */}
      <section className="series-bar">
        <div className="section-eyebrow mono" style={{ textAlign: 'center', marginBottom: 24 }}>COVERAGE</div>
        <div className="series-list">
          {['WEC', 'ELMS', 'ALMS', 'Le Mans Cup', 'IMSA'].map((s) => (
            <div key={s} className="series-badge mono">{s}</div>
          ))}
        </div>
      </section>

      {/* ── CTA ── */}
      <section className="cta-section">
        <div className="cta-box">
          <div className="section-eyebrow mono" style={{ marginBottom: 16 }}>GET STARTED</div>
          <h2 className="cta-title">Start exploring endurance racing data.</h2>
          <div className="cta-buttons">
            <Link to="/dashboard" className="cta-primary">Open Dashboard</Link>
            <a href="https://api.openwec.com/api/v1/series" className="cta-secondary" target="_blank" rel="noopener noreferrer">Try the API</a>
          </div>
          <div className="cta-hint mono">
            No key needed for public data.{' '}
            <a href="/api-keys">Request access</a> for analytics endpoints.
          </div>
        </div>
      </section>

      {/* ── FOOTER ── */}
      <SiteFooter />
    </div>
  )
}