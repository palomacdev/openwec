import { Routes, Route } from 'react-router-dom'
import Home from './pages/Home.jsx'
import About from './pages/About.jsx'
import Explore from './pages/Explore.jsx'
import ApiKeys from './pages/ApiKeys.jsx'
import Dashboard from './pages/Dashboard.jsx'
import DriverProfile from './pages/DriverProfile.jsx'
import Notice from './components/Notice.jsx'

export default function App() {
  return (
    <>
      {/* Site-wide. Dashboard does not render SiteNav, so the notice is mounted
          above the routes to reach every page, including /dashboard. */}
      <Notice title="Maintenance notice" variant="banner">
        We&apos;re currently improving OpenWEC. Some historical data may be temporarily
        inconsistent while we work on data quality. Public dashboards remain available.
      </Notice>

      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/about" element={<About />} />
        <Route path="/explore" element={<Explore />} />
        <Route path="/api-keys" element={<ApiKeys />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/drivers/:id" element={<DriverProfile />} />
      </Routes>
    </>
  )
}