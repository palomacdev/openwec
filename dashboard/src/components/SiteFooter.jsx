import { Link } from 'react-router-dom'

export default function SiteFooter() {
  return (
    <footer className="home-footer">
      <div className="footer-logo mono">
        <span style={{ color: 'var(--accent)' }}>OPEN</span>WEC
      </div>
      <div className="footer-links">
        <Link to="/explore">Explore</Link>
        <Link to="/about">About</Link>
        <a href="https://api.openwec.com/docs" target="_blank" rel="noopener noreferrer">API</a>
        <a href="https://github.com/palomacdev/openwec" target="_blank" rel="noopener noreferrer">GitHub</a>
        <Link to="/dashboard">Dashboard</Link>
        <Link to="/api-keys" style={{ color: 'var(--accent)' }}>Get API Key</Link>
      </div>
      <div className="footer-copy mono">
        Data sourced from Al Kamel Systems. Not affiliated with ACO or FIA.
      </div>
    </footer>
  )
}