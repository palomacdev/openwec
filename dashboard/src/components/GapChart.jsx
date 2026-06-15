import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceArea,
} from 'recharts'

const LINE_COLORS = [
  '#FFB000', '#4895EF', '#6FCF97', '#E63946', '#9D4EDD',
  '#F4A261', '#48BFE3', '#80FFDB', '#FF6B6B', '#C77DFF',
]

export default function GapChart({ gaps, raceControl = [] }) {
  // gaps: [{lap_number, car_number, car_class, lap_time_s, cumulative_s}, ...]
  const byLap = {}
  const cars = new Set()

  for (const row of gaps) {
    if (row.cumulative_s == null) continue
    cars.add(row.car_number)
    byLap[row.lap_number] = byLap[row.lap_number] || { lap: row.lap_number }
    byLap[row.lap_number][row.car_number] = row.cumulative_s
  }

  const carList = [...cars]
  const lapNumbers = Object.keys(byLap).map(Number).sort((a, b) => a - b)

  // Leader = min cumulative per lap
  const data = lapNumbers.map((lap) => {
    const row = byLap[lap]
    const values = carList.map((c) => row[c]).filter((v) => v != null)
    const leader = Math.min(...values)
    const out = { lap }
    for (const c of carList) {
      out[c] = row[c] != null ? row[c] - leader : null
    }
    return out
  })

  if (data.length === 0) {
    return <div className="state-message">No gap data available.</div>
  }

  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
        {raceControl.map((p, i) => (
          <ReferenceArea
            key={i}
            x1={p.start_lap}
            x2={p.end_lap}
            fill="#FFFFFF"
            fillOpacity={0.06}
            stroke="none"
          />
        ))}
        <XAxis
          dataKey="lap"
          tick={{ fill: 'var(--text-dim)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
          stroke="var(--border)"
          label={{ value: 'Lap', position: 'insideBottom', offset: -2, fill: 'var(--text-dim)', fontSize: 11 }}
        />
        <YAxis
          tick={{ fill: 'var(--text-dim)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
          stroke="var(--border)"
          label={{ value: 'Gap (s)', angle: -90, position: 'insideLeft', fill: 'var(--text-dim)', fontSize: 11 }}
        />
        <Tooltip
          contentStyle={{
            background: 'var(--surface-2)',
            border: '1px solid var(--border)',
            borderRadius: 4,
            fontFamily: 'var(--font-mono)',
            fontSize: 12,
          }}
          labelStyle={{ color: 'var(--text-dim)' }}
          labelFormatter={(lap) => `Lap ${lap}`}
          formatter={(value, name) => [`+${value.toFixed(1)}s`, `#${name}`]}
        />
        <Legend
          wrapperStyle={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-dim)' }}
        />
        {carList.map((car, i) => (
          <Line
            key={car}
            type="monotone"
            dataKey={car}
            name={car}
            stroke={LINE_COLORS[i % LINE_COLORS.length]}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}