const FLAG_COLOR = {
  'Other': '#FFB000',  // Safety Car — amber
  'FCY':   '#F4A261',  // Full Course Yellow — lighter amber
}

export default function RaceControl({ periods }) {
  if (!periods || periods.length === 0) {
    return <div className="state-message">No SC/FCY periods — race was green throughout.</div>
  }

  return (
    <div className="race-control-list">
      {periods.map((p, i) => (
        <div className="rc-row" key={i}>
          <span
            className="rc-dot"
            style={{ background: FLAG_COLOR[p.flag] || 'var(--text-dim)' }}
          />
          <span className="rc-label">{p.label}</span>
          <span className="rc-laps mono">
            Lap {p.start_lap}
            {p.end_lap !== p.start_lap ? `–${p.end_lap}` : ''}
          </span>
          <span className="rc-duration mono text-dim">
            {p.duration_laps} {p.duration_laps === 1 ? 'lap' : 'laps'}
          </span>
        </div>
      ))}
    </div>
  )
}