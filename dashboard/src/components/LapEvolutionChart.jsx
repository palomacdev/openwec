import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Scatter, ComposedChart, ReferenceArea,
} from 'recharts'

function formatLapTime(seconds) {
  if (seconds == null) return '—'
  const m = Math.floor(seconds / 60)
  const s = (seconds % 60).toFixed(3)
  return `${m}:${s.padStart(6, '0')}`
}

export default function LapEvolutionChart({ laps, raceControl = [] }) {
  const data = laps
    .filter((l) => l.lap_time_s != null && l.lap_time_s < 600)
    .map((l) => ({
      lap: l.lap_number,
      time: l.lap_time_s,
      pit: l.crossing_finish_in_pit ? l.lap_time_s : null,
    }))

  if (data.length === 0) {
    return <div className="state-message">No lap data for this car.</div>
  }

  return (
    <ResponsiveContainer width="100%" height={280}>
      <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
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
          tickFormatter={formatLapTime}
          domain={['dataMin - 2', 'dataMax + 2']}
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
          formatter={(value, name) => [formatLapTime(value), name === 'pit' ? 'Pit lap' : 'Lap time']}
          labelFormatter={(lap) => `Lap ${lap}`}
        />
        <Line
          type="monotone"
          dataKey="time"
          stroke="var(--accent)"
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
        <Scatter dataKey="pit" fill="var(--hypercar)" shape="cross" />
      </ComposedChart>
    </ResponsiveContainer>
  )
}