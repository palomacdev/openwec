import { Routes, Route } from 'react-router-dom'
import Home from './pages/Home.jsx'
import About from './pages/About.jsx'
import Explore from './pages/Explore.jsx'
import ApiKeys from './pages/ApiKeys.jsx'
import Dashboard from './pages/Dashboard.jsx'
import DriverProfile from './pages/DriverProfile.jsx'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/about" element={<About />} />
      <Route path="/explore" element={<Explore />} />
      <Route path="/api-keys" element={<ApiKeys />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/drivers/:id" element={<DriverProfile />} />
    </Routes>
  )
}