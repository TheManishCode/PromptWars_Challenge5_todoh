import { Children } from 'react';

const TILTS = [-1.1, 0.8, -0.5, 1, -0.8, 0.5];

/**
 * The notes beside a thought: paper cards on a board. They sit at a slight angle and settle straight when looked at.
 * The column layout is the browser's, so any number of notes of any height fit without measuring or overlapping.
 */
export function Collage({ children }) {
  return (
    <div className="collage">
      {Children.toArray(children).map((child, i) => (
        <div className="collage-cell" key={child.key ?? i} style={{ '--tilt': `${TILTS[i % TILTS.length]}deg`, '--i': i }}>
          {child}
        </div>
      ))}
    </div>
  );
}

/** One paper card. `tone` marks what kind of thing it holds: `vendor` (a business), `plain` (a line of fact), `action` (buttons only), `warn`. */
export function Note({ title, tag, tone = 'plain', busy = false, children, ...rest }) {
  return (
    <section className={`note note--${tone}${busy ? ' is-busy' : ''}`} aria-busy={busy || undefined} {...rest}>
      {tag ? <span className="note-tag">{tag}</span> : null}
      {title ? <h3 className="note-title">{title}</h3> : null}
      {children}
    </section>
  );
}

export function Notice({ tone = 'quiet', children }) {
  return <p className={`notice notice--${tone}`} role={tone === 'bad' ? 'alert' : undefined}>{children}</p>;
}
