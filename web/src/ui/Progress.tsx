export function Progress({ value, paused = false, height }: { value: number | null; paused?: boolean; height?: number }) {
  const known = value !== null && Number.isFinite(value)
  const pct = known ? Math.min(100, Math.max(0, value)) : 0
  return (
    <div
      className={paused ? 'ia-progress ia-progress--paused' : 'ia-progress'}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={known ? pct : undefined}
      style={height ? { height } : undefined}
    >
      <div className="ia-progress__bar" style={{ width: `${pct}%` }} />
    </div>
  )
}
