import { useEffect, useRef } from 'react';
import { Art } from '../components/Art';
import { NODES, wires } from './nodes';

const HOVER_MS = 360; // a mouse passing over a bubble does not open it; resting on it does

/** The cap, the bubbles of thought and the open head: one drawing. Each bubble is a real button. */
export function Graph({ active, onSelect, badges = {} }) {
  const hover = useRef(0);
  useEffect(() => () => clearTimeout(hover.current), []);

  function move(e, i) {
    if (!e.key.startsWith('Arrow')) return;
    e.preventDefault();
    clearTimeout(hover.current);
    const step = e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 1;
    const next = NODES[(i + step + NODES.length) % NODES.length].id;
    onSelect(next);
    document.querySelector(`[data-node="${next}"]`)?.focus({ preventScroll: true });
  }

  return (
    <div className="thinker">
      <Art name="thinker-cap" className="thinker-cap" alt="" />
      <div className="graph-world">
        <svg className="graph-wires" viewBox="0 0 360 340" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <filter id="thought-goo" x="-15%" y="-15%" width="130%" height="130%">
              <feGaussianBlur in="SourceGraphic" stdDeviation="7" result="blur" />
              <feColorMatrix in="blur" type="matrix" values="1 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0 18 -7" />
            </filter>
          </defs>
          <g className="goo" filter="url(#thought-goo)">
            {wires.map(([a, b]) => <path key={`${a}${b}`} d={`M${a[0]} ${a[1]} L${b[0]} ${b[1]}`} />)}
            {NODES.map(({ id, at: [x, y, r] }) => <circle key={id} cx={x} cy={y} r={r} />)}
          </g>
          <g className="bubble-centres">
            {NODES.map(({ id, at: [x, y, r] }) => <circle key={id} cx={x} cy={y} r={r * 0.86} />)}
          </g>
        </svg>
        {NODES.map(({ id, label, at: [x, y, r], mark }, i) => (
          <button
            key={id}
            type="button"
            className={`graph-node${active === id ? ' chosen' : ''}`}
            style={{ left: `${x / 3.6}%`, top: `${y / 3.4}%`, width: `${(r * 2) / 3.6}%`, height: `${(r * 2) / 3.4}%` }}
            data-node={id}
            title={label}
            aria-label={`${label}${badges[id] ? `, ${badges[id]}` : ''}. Open notes.`}
            aria-pressed={active === id}
            onPointerEnter={(e) => {
              if (e.pointerType !== 'mouse') return;
              clearTimeout(hover.current);
              hover.current = setTimeout(() => onSelect(id), HOVER_MS);
            }}
            onPointerLeave={() => clearTimeout(hover.current)}
            onClick={() => { clearTimeout(hover.current); onSelect(id); }}
            onKeyDown={(e) => move(e, i)}
          >
            <span className="node-disc">
              <svg viewBox="0 0 32 32" aria-hidden="true"><path d={mark} /></svg>
            </span>
            {badges[id] ? <span className="node-badge" aria-hidden="true">{badges[id]}</span> : null}
          </button>
        ))}
      </div>
      <Art name="thinker-head-open" className="thinker-head" alt="" />
    </div>
  );
}
