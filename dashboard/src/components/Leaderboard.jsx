function formatTime(totalSeconds) {
  if (totalSeconds == null) return '—'
  const h = Math.floor(totalSeconds / 3600)
  const m = Math.floor((totalSeconds % 3600) / 60)
  const s = (totalSeconds % 60).toFixed(3)
  return `${h}:${String(m).padStart(2, '0')}:${s.padStart(6, '0')}`
}

function formatGap(gap) {
  if (gap == null) return '—'
  return `+${gap.toFixed(3)}`
}

function formatLapTime(seconds) {
  if (seconds == null) return '—'
  const m = Math.floor(seconds / 60)
  const s = (seconds % 60).toFixed(3)
  return `${m}:${s.padStart(6, '0')}`
}

export default function Leaderboard({ results, onSelectCar, selectedCar }) {
  return (
    <table className="leaderboard">
      <thead>
        <tr>
          <th>Pos</th>
          <th>Class</th>
          <th>Car</th>
          <th>Team / Drivers</th>
          <th>Vehicle</th>
          <th style={{ textAlign: 'right' }}>Total Time</th>
          <th style={{ textAlign: 'right' }}>Gap</th>
          <th style={{ textAlign: 'right' }}>Fastest Lap</th>
        </tr>
      </thead>
      <tbody>
        {results.map((r) => (
          <tr
            key={r.car_number}
            onClick={() => onSelectCar?.(r.car_number)}
            style={{
              cursor: onSelectCar ? 'pointer' : 'default',
              background: selectedCar === r.car_number ? 'var(--surface-2)' : undefined,
            }}
          >
            <td className="pos-digit">{r.position ?? '—'}</td>
            <td>
              {r.car_class && (
                <span className={`class-chip ${r.car_class}`}>{r.car_class}</span>
              )}
            </td>
            <td>
              <span className="car-number">#{r.car_number}</span>
            </td>
            <td>
              <div className="team-cell">
                <span className="team-name">{r.team || '—'}</span>
                
                {r.drivers && (
                  <span className="drivers">
                   {r.drivers
                     .map(d => `${d.first_name} ${d.last_name}`)
                     .join(' / ')}
                  </span>
                )}
              </div>
            </td>
            <td className="text-dim">{r.vehicle || '—'}</td>
            <td className="mono" style={{ textAlign: 'right' }}>
              {formatTime(r.total_time_s)}
            </td>
            <td
              className={`gap-cell ${r.position === 1 ? 'leader' : ''}`}
              style={{ textAlign: 'right' }}
            >
              {r.position === 1 ? 'LEADER' : formatGap(r.gap_to_first_s)}
            </td>
            <td className="mono" style={{ textAlign: 'right' }}>
              {formatLapTime(r.fl_time_s)}
              {r.fl_lap_number != null && (
                <span className="text-dim"> (L{r.fl_lap_number})</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}