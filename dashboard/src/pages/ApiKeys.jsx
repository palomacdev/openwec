import { useState } from 'react'
import SiteNav from '../components/SiteNav'
import SiteFooter from '../components/SiteFooter'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

const ENDPOINTS = [
  { path: '/series',                          auth: false, label: 'List all series' },
  { path: '/series/{key}/seasons',            auth: false, label: 'List seasons' },
  { path: '/sessions/{id}/results',           auth: false, label: 'Race results' },
  { path: '/drivers/{id}',                    auth: false, label: 'Driver profile' },
  { path: '/teams/{id}',                      auth: false, label: 'Team profile' },
  { path: '/sessions/{id}/laps/{car}',        auth: true,  label: 'Lap-by-lap data' },
  { path: '/sessions/{id}/stints',            auth: true,  label: 'Stint analysis' },
  { path: '/sessions/{id}/pace',              auth: true,  label: 'Pace comparison' },
  { path: '/sessions/{id}/pit-window',        auth: true,  label: 'Pit window estimator' },
  { path: '/sessions/{id}/gaps',              auth: true,  label: 'Gap to leader' },
  { path: '/sessions/{id}/race-control',      auth: true,  label: 'SC / FCY periods' },
  { path: '/drivers/{id}/consistency',        auth: true,  label: 'Driver consistency' },
]

export default function ApiKeys() {
  const [form, setForm]       = useState({ name: '', email: '', intended_use: '' })
  const [status, setStatus]   = useState(null)   // null | 'loading' | 'success' | 'error'
  const [apiKey, setApiKey]   = useState(null)
  const [copied, setCopied]   = useState(false)
  const [error, setError]     = useState(null)

  function handleChange(e) {
    setForm({ ...form, [e.target.name]: e.target.value })
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (!form.name || !form.email) return
    setStatus('loading')
    setError(null)
    try {
      const res = await fetch(`${BASE_URL}/api-keys/request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Request failed')
      setApiKey(data.api_key)
      setStatus('success')
    } catch (err) {
      setError(err.message)
      setStatus('error')
    }
  }

  function copyKey() {
    navigator.clipboard.writeText(apiKey)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="home">
      <SiteNav />

      {/* Header */}
      <section style={{ marginBottom: 56 }}>
        <div className="hero-eyebrow mono">API ACCESS</div>
        <h1 className="about-title">Request an API key.</h1>
        <p className="about-lede">
          Public endpoints (series, sessions, results, driver and team profiles)
          need no key. Lap-by-lap data and analytics require a free key —
          issued immediately, active after review.
        </p>
      </section>

      <div className="apikey-layout">

        {/* Endpoint table */}
        <div className="endpoint-table">
          <div className="section-eyebrow mono" style={{ marginBottom: 16 }}>ENDPOINTS</div>
          {ENDPOINTS.map((ep) => (
            <div className="endpoint-row" key={ep.path}>
              <span className={`endpoint-badge mono ${ep.auth ? 'auth' : 'public'}`}>
                {ep.auth ? 'KEY' : 'FREE'}
              </span>
              <span className="endpoint-label">{ep.label}</span>
              <span className="endpoint-path mono">{ep.path}</span>
            </div>
          ))}
        </div>

        {/* Form */}
        <div className="apikey-form-wrap">
          {status !== 'success' ? (
            <div className="panel">
              <div className="panel-title" style={{ marginBottom: 24 }}>Request access</div>

              <form onSubmit={handleSubmit} className="apikey-form">
                <div className="form-group">
                  <label className="form-label mono">Name</label>
                  <input
                    className="form-input"
                    name="name"
                    value={form.name}
                    onChange={handleChange}
                    placeholder="Your name"
                    required
                  />
                </div>
                <div className="form-group">
                  <label className="form-label mono">Email</label>
                  <input
                    className="form-input"
                    type="email"
                    name="email"
                    value={form.email}
                    onChange={handleChange}
                    placeholder="you@example.com"
                    required
                  />
                </div>
                <div className="form-group">
                  <label className="form-label mono">Intended use <span className="text-dim">(optional)</span></label>
                  <textarea
                    className="form-input form-textarea"
                    name="intended_use"
                    value={form.intended_use}
                    onChange={handleChange}
                    placeholder="Research, personal project, data journalism…"
                    rows={3}
                  />
                </div>

                {error && (
                  <div className="form-error mono">{error}</div>
                )}

                <button
                  type="submit"
                  className="cta-primary"
                  style={{ width: '100%', textAlign: 'center', marginTop: 8 }}
                  disabled={status === 'loading'}
                >
                  {status === 'loading' ? 'Requesting…' : 'Request API key →'}
                </button>
              </form>

              <p className="form-note mono">
                Keys are generated immediately and become active after manual review —
                usually within 24 hours. The key will not be shown again after you leave this page.
              </p>
            </div>
          ) : (
            <div className="panel">
              <div className="key-success">
                <div className="key-success-icon">✓</div>
                <div className="panel-title" style={{ marginBottom: 8 }}>Key generated</div>
                <p className="form-note mono" style={{ marginBottom: 24 }}>
                  Save this key now — it will not be shown again.
                  It will start working automatically once approved (usually within 24h).
                </p>
                <div className="key-display">
                  <code className="mono key-value">{apiKey}</code>
                  <button className="key-copy-btn mono" onClick={copyKey}>
                    {copied ? '✓ Copied' : 'Copy'}
                  </button>
                </div>
                <div className="key-usage-hint mono">
                  Usage: <span style={{ color: 'var(--text-dim)' }}>X-API-Key: {apiKey}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      <div style={{ marginTop: 80 }}>
        <SiteFooter />
      </div>
    </div>
  )
}