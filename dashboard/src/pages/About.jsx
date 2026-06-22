import { Link } from 'react-router-dom'
import SiteNav from '../components/SiteNav'
import SiteFooter from '../components/SiteFooter'

const STACK = [
  { layer: 'Database',   tech: 'PostgreSQL + TimescaleDB' },
  { layer: 'API',        tech: 'FastAPI (Python)' },
  { layer: 'SDK',        tech: 'Python — pandas-native' },
  { layer: 'Dashboard',  tech: 'React + Recharts' },
  { layer: 'Hosting',    tech: 'Self-hosted, Docker' },
  { layer: 'Source',     tech: 'Al Kamel Systems timing exports' },
]

const TIMELINE = [
  { year: '2012', label: 'Earliest race in the dataset' },
  { year: '2014', label: 'Hypercar-era predecessors, LMP1 dominance' },
  { year: '2020', label: 'Hypercar class introduced' },
  { year: '2024', label: 'Multi-manufacturer Hypercar era' },
  { year: '2026', label: 'Current season — live in this dashboard' },
]

export default function About() {
  return (
    <div className="home">
      <SiteNav />

      {/* Header */}
      <section className="about-header">
        <div className="hero-eyebrow mono">ABOUT</div>
        <h1 className="about-title">
          Endurance racing generates an enormous amount of data.<br />
          Almost none of it is easy to use.
        </h1>
        <p className="about-lede">
          OpenWEC exists to fix that — a free, open platform for historical and
          current data from the FIA World Endurance Championship and its sister series.
        </p>
      </section>

      {/* The gap */}
      <section className="about-section">
        <div className="about-grid">
          <div className="about-col-label">
            <div className="section-eyebrow mono">THE GAP</div>
          </div>
          <div className="about-col-body">
            <p>
              Formula 1 has FastF1 — a mature open-source library that turned
              official timing data into a thriving ecosystem of analysis,
              visualization, and research. Endurance racing never got the same treatment.
            </p>
            <p>
              WEC, ELMS, ALMS, Le Mans Cup, and IMSA timing data exists, but it's
              scattered across inconsistent CSV exports, undocumented formats,
              and timing-system quirks that change from series to series — and
              sometimes from season to season within the same series.
            </p>
            <p>
              Multi-class racing, driver rotations, 24-hour strategy, pit windows,
              Safety Car cycles — endurance racing is arguably more complex than
              any other motorsport discipline. It deserves tooling that matches that complexity.
            </p>
          </div>
        </div>
      </section>

      {/* What it does */}
      <section className="about-section">
        <div className="about-grid">
          <div className="about-col-label">
            <div className="section-eyebrow mono">WHAT IT DOES</div>
          </div>
          <div className="about-col-body">
            <p>
              OpenWEC ingests raw timing exports, normalizes them into a single
              consistent schema, and exposes the result through a REST API and
              a Python SDK — full results, lap-by-lap data, stint detection,
              degradation rates, pit window estimates, and Safety Car / FCY periods,
              all queryable the same way regardless of which series or season they come from.
            </p>
            <p>
              Driver and team names are normalized and deduplicated across
              thousands of historical entries. Nationalities are enriched via
              Wikidata where available. Phantom sessions from timing-system
              artifacts are filtered out. The result is a dataset that's
              actually pleasant to query.
            </p>
          </div>
        </div>
      </section>

      {/* Stack */}
      <section className="about-section">
        <div className="about-grid">
          <div className="about-col-label">
            <div className="section-eyebrow mono">THE STACK</div>
          </div>
          <div className="about-col-body">
            <div className="spec-sheet">
              {STACK.map((s) => (
                <div className="spec-row" key={s.layer}>
                  <span className="spec-layer mono">{s.layer}</span>
                  <span className="spec-tech">{s.tech}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Coverage timeline */}
      <section className="about-section">
        <div className="about-grid">
          <div className="about-col-label">
            <div className="section-eyebrow mono">COVERAGE</div>
          </div>
          <div className="about-col-body">
            <div className="timeline">
              {TIMELINE.map((t) => (
                <div className="timeline-row" key={t.year}>
                  <span className="timeline-year mono">{t.year}</span>
                  <span className="timeline-dot" />
                  <span className="timeline-label">{t.label}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Philosophy */}
      <section className="about-section">
        <div className="about-grid">
          <div className="about-col-label">
            <div className="section-eyebrow mono">OPEN BY DESIGN</div>
          </div>
          <div className="about-col-body">
            <p>
              The public endpoints — series, seasons, events, sessions, results,
              driver and team profiles — require no API key. Lap-by-lap data and
              computed analytics (stints, pace, pit windows) require a free key,
              mainly to keep the database responsive under load.
            </p>
            <p>
              There's no advertising, no paywall, and no plan to add either.
              This is a data project first.
            </p>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="cta-section">
        <div className="cta-box">
          <div className="section-eyebrow mono" style={{ marginBottom: 16 }}>GET INVOLVED</div>
          <h2 className="cta-title">The dataset grows every race weekend.</h2>
          <div className="cta-buttons">
            <Link to="/dashboard" className="cta-primary">Explore the data</Link>
            <a href="https://github.com/palomacdev/openwec" className="cta-secondary" target="_blank" rel="noopener noreferrer">
              View on GitHub
            </a>
          </div>
        </div>
      </section>

      <SiteFooter />
    </div>
  )
}