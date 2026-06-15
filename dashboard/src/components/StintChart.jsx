const STINT_COLORS = [
  '#FFB000', '#4895EF', '#6FCF97', '#E63946', '#9D4EDD',
  '#F4A261', '#48BFE3', '#80FFDB', '#FF6B6B', '#C77DFF',
]

export default function StintChart({ stints, totalLaps }) {
  // Group by car
  const byCar = {}
  for (const s of stints) {
    byCar[s.car_number] = byCar[s.car_number] || []
    byCar[s.car_number].push(s)
  }

  const cars = Object.keys(byCar).sort((a, b) => {
    const an = parseInt(a, 10)
    const bn = parseInt(b, 10)
    return (isNaN(an) ? 999 : an) - (isNaN(bn) ? 999 : bn)
  })

  const maxLap = totalLaps || Math.max(
    ...stints.map((s) => s.end_lap),
    1
  )

  return (
    <div>
      {cars.map((car) => {
        const carStints = [...byCar[car]].sort((a, b) => a.stint_number - b.stint_number)
        return (
          <div className="stint-row" key={car}>
            <div className="stint-row-label">#{car}</div>
            <div className="stint-track">
              {carStints.map((s) => {
                const widthPct = ((s.end_lap - s.start_lap + 1) / maxLap) * 100
                return (
                  <div
                    key={s.stint_number}
                    className="stint-segment"
                    style={{
                      width: `${widthPct}%`,
                      background: STINT_COLORS[(s.stint_number - 1) % STINT_COLORS.length],
                    }}
                    title={`Stint ${s.stint_number} — laps ${s.start_lap}-${s.end_lap} (${s.tyre_age_laps} laps on tyre)\nBaseline: ${s.baseline_pace_s?.toFixed(3) ?? '—'}s · Deg: ${s.degradation_s_per_lap?.toFixed(4) ?? '—'}s/lap`}
                  />
                )
              })}
            </div>
          </div>
        )
      })}
      <div className="stint-axis">
        <span>Lap 1</span>
        <span>Lap {maxLap}</span>
      </div>
    </div>
  )
}