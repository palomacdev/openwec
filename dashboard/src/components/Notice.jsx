/**
 * Informational notice. Two shapes from the same component:
 *
 *   variant="banner"  full-bleed strip, mounted once above the routes
 *   variant="inline"  card inside a page, aligned with the surrounding content
 *
 * Advisory only — uses the amber accent, not the red reserved for errors
 * (.state-message.error, .form-error).
 */
export default function Notice({ title, variant = 'inline', children }) {
  return (
    <div className={`notice notice-${variant}`} role="status">
      <div className="notice-inner">
        <span className="notice-dot" aria-hidden="true" />
        <div className="notice-body">
          <div className="notice-title mono">{title}</div>
          <p className="notice-text">{children}</p>
        </div>
      </div>
    </div>
  )
}
