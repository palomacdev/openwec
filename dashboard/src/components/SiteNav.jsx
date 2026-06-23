import { Link } from 'react-router-dom'

export default function SiteNav() {
  return (
    <nav className="home-nav">
      <Link to="/" className="home-nav-logo" style={{ display: 'flex' }}>
        <span className="mono" style={{ color: 'var(--accent)', fontWeight: 700, fontSize: 15, letterSpacing: '0.12em' }}>OPEN</span>
        <span className="mono" style={{ color: 'var(--text)', fontWeight: 700, fontSize: 15, letterSpacing: '0.12em' }}>WEC</span>
      </Link>
      <div className="home-nav-links">
        <Link to="/explore">Explore</Link>
        <Link to="/about">About</Link>
        <a href="https://api.openwec.com/docs" target="_blank" rel="noopener noreferrer">API</a>
        <a href="https://github.com/your-username/openwec" target="_blank" rel="noopener noreferrer">GitHub</a>
        <Link to="/dashboard">Dashboard</Link>
        <Link to="/api-keys" style={{ color: 'var(--accent)' }}>Get API Key</Link>
      </div>
    </nav>
  )
}