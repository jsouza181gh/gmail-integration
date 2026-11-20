export function Switch({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  label: string
  description?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      className="ia-switch switch-btn"
      onClick={() => onChange(!checked)}
    >
      <span className="ia-switch__track">
        <span className="ia-switch__knob" />
      </span>
      <span className="switch-btn__text">
        <span className="h-heading">{label}</span>
        {description ? <span className="t-small ia-muted">{description}</span> : null}
      </span>
    </button>
  )
}
