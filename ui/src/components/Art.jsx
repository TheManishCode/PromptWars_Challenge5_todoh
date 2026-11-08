// The illustrations, found by name. All of them are drawn here as inline SVG: line art in the page's ink colour on white, authored for this project
// (see docs/ATTRIBUTIONS.md). Nothing is loaded from a file, so nothing about them can be missing, borrowed or blocked.
const INK = '#151515';
const line = { fill: 'none', stroke: INK, strokeWidth: 2.4, strokeLinecap: 'round', strokeLinejoin: 'round' };
const solid = { fill: '#fff', stroke: INK, strokeWidth: 2.4, strokeLinecap: 'round', strokeLinejoin: 'round' };
const dot = (cx, cy, r = 2) => <circle cx={cx} cy={cy} r={r} fill={INK} />;

const drawings = {
  // the tangle of a plan that is still in your head: a loose knot with the guest list, the budget and the rest caught in it
  'story-knot': ['0 0 640 480', (
    <>
      <path {...line} strokeWidth="3" d="M70 300C40 200 150 120 260 150S470 90 540 190 520 380 400 400 150 430 120 330 250 210 330 250 420 330 360 360 230 330 250 270" />
      <path {...line} d="M110 380C60 300 120 190 230 190S400 140 470 230 560 330 470 380 280 430 200 380 160 270 260 250" />
      <path {...line} d="M180 120C260 60 380 70 430 130S560 250 500 320" />
      <g transform="rotate(-9 170 215)"><rect {...solid} x="120" y="175" width="100" height="72" rx="4" /><path {...line} strokeWidth="1.8" d="M134 197h70M134 213h54M134 229h62" /></g>
      <g transform="rotate(7 440 150)"><rect {...solid} x="395" y="115" width="94" height="70" rx="4" /><path {...line} strokeWidth="1.8" d="M410 138h62M410 154h44" /><circle {...line} strokeWidth="1.8" cx="466" cy="164" r="8" /></g>
      <g transform="rotate(-4 330 330)"><rect {...solid} x="285" y="298" width="96" height="68" rx="4" /><path {...line} strokeWidth="1.8" d="M299 320h66M299 336h40" /></g>
    </>
  )],
  // someone at ease: the calm after the knot is undone
  'story-relaxed': ['0 0 360 420', (
    <>
      <circle {...solid} cx="170" cy="86" r="42" />
      <path {...line} d="M150 80q6-6 12 0M184 80q6-6 12 0M156 104q16 14 34 0" />
      <path {...line} d="M132 70q10-34 44-32t38 32" />
      <path {...solid} d="M140 124c-30 24-44 78-40 130l130 4c4-52-8-102-40-134" />
      <path {...solid} d="M104 250c-30 40-50 96-30 130h104c10-24-8-52-44-60M230 258c34 30 70 60 76 120h-96" />
      <path {...line} d="M138 142C110 120 86 96 84 64M222 142c28-18 40-40 48-66" />
      <path {...line} strokeWidth="1.8" d="M48 404h270" />
      {dot(150, 82)}
    </>
  )],
  // the table that the party is really about
  'story-table-sketch': ['0 0 340 290', (
    <>
      <ellipse {...solid} cx="170" cy="160" rx="120" ry="48" />
      <path {...line} d="M60 168v74M280 168v74M170 208v62" />
      <ellipse {...solid} cx="120" cy="150" rx="22" ry="8" /><ellipse {...solid} cx="210" cy="152" rx="22" ry="8" />
      <path {...solid} d="M160 120h20l-4 22h-12z" />
      <path {...line} d="M170 120V92M170 92q-16-10-10-26M170 92q16-10 10-26" />
      <circle {...solid} cx="56" cy="92" r="20" /><path {...line} d="M30 132c4-18 48-18 52 0" />
      <circle {...solid} cx="284" cy="92" r="20" /><path {...line} d="M258 132c4-18 48-18 52 0" />
      <circle {...solid} cx="170" cy="34" r="16" />
      <path {...line} strokeWidth="1.8" d="M20 276h300" />
    </>
  )],
  'story-char-cat': ['0 0 67 111', (
    <>
      <path {...solid} d="M12 40L10 12l14 12h20L58 12 56 40c6 22-4 32-23 32S6 62 12 40z" />
      <path {...line} d="M24 44h0M42 44h0M28 56q5 5 11 0M10 50l-8-2M10 55l-8 3M56 50l8-2M56 55l8 3" /><path {...solid} d="M22 72c-6 14-6 28 0 34h24c6-6 6-20 0-34" />
      <path {...line} d="M46 98q14-6 12-24" />{dot(24, 44)}{dot(42, 44)}
    </>
  )],
  'story-char-frog': ['0 0 63 95', (
    <>
      <circle {...solid} cx="18" cy="16" r="11" /><circle {...solid} cx="45" cy="16" r="11" />{dot(18, 16, 3)}{dot(45, 16, 3)}
      <path {...solid} d="M8 28c-6 14 0 26 10 30h27c10-4 16-16 10-30-12-8-35-8-47 0z" />
      <path {...line} d="M20 42q12 8 24 0" />
      <path {...solid} d="M16 58c-8 10-10 24-4 30h14M47 58c8 10 10 24 4 30H37M26 62h12v26H26z" />
    </>
  )],
  'story-char-robot': ['0 0 55 82', (
    <>
      <path {...line} d="M27 4v10" />{dot(27, 4, 3)}
      <rect {...solid} x="8" y="14" width="39" height="28" rx="5" />
      <circle {...line} cx="19" cy="28" r="4" /><circle {...line} cx="36" cy="28" r="4" /><path {...line} d="M20 37h16" />
      <rect {...solid} x="12" y="46" width="31" height="24" rx="3" />
      <path {...line} d="M12 52H4M43 52h8M20 70v8M35 70v8M20 58h15" />
    </>
  )],
  'story-char-tallbird': ['0 0 57 109', (
    <>
      <path {...solid} d="M20 6c10-6 22 0 22 12 0 6-4 10-8 12l-1 16c14 8 20 28 14 44-4 10-14 14-26 14s-18-6-18-18c0-16 8-30 20-36l-2-16c-6-2-8-8-6-14z" />
      <path {...line} d="M40 14l12 4-12 4M28 96v10M40 96v10" />{dot(30, 16)}
    </>
  )],
  'story-char-duck': ['0 0 52 94', (
    <>
      <circle {...solid} cx="26" cy="20" r="14" /><path {...solid} d="M36 20l14 4-14 5z" />{dot(24, 16)}
      <path {...solid} d="M12 40c-8 20 0 46 14 46s24-22 14-46z" />
      <path {...line} d="M20 86l-3 6h10M32 86l-3 6h10" />
    </>
  )],
  'story-char-music': ['0 0 71 100', (
    <>
      <circle {...solid} cx="30" cy="26" r="16" />{dot(24, 24)}{dot(36, 24)}<path {...line} d="M22 32q8 7 16 0" />
      <path {...solid} d="M16 44c-6 14-4 34 4 46h20c8-12 10-32 4-46" />
      <path {...line} d="M54 14v36" /><ellipse {...solid} cx="48" cy="52" rx="8" ry="5" /><path {...line} d="M54 14q10 2 12 14" />
    </>
  )],
  // the mascot of the findings room: a round head under a party hat, thinking
  'thinker-cap': ['0 0 230 135', (
    <>
      <path {...solid} d="M115 8L52 124h126z" />
      <path {...line} d="M72 92q43 14 86 0M88 62q27 10 54 0" />
      <circle {...solid} cx="115" cy="10" r="8" />
    </>
  )],
  'thinker-head-open': ['0 0 230 202', (
    <>
      <path {...solid} d="M24 90C24 36 68 6 115 6s91 30 91 84-40 106-91 106S24 144 24 90z" />
      <path {...line} d="M72 70q10-12 20 0M138 70q10-12 20 0" />{dot(82, 84, 4)}{dot(148, 84, 4)}
      <path {...solid} d="M86 126q29 34 58 0z" />
      <path {...line} strokeWidth="1.8" d="M42 40q-14 8-20 24M188 40q14 8 20 24" />
    </>
  )],
  'thinker-head-closed': ['0 0 230 280', (
    <>
      <path {...solid} d="M115 6L66 98h98z" /><circle {...solid} cx="115" cy="8" r="7" />
      <path {...solid} d="M26 176C26 124 66 92 115 92s89 32 89 84-38 100-89 100S26 228 26 176z" />
      <path {...line} d="M72 166q12 10 24 0M134 166q12 10 24 0" />
      <path {...line} d="M92 214q23 16 46 0" />
    </>
  )],
};

export function Art({ name, alt = '', ...props }) {
  const d = drawings[name];
  if (!d) return null;
  return (
    <svg viewBox={d[0]} role={alt ? 'img' : undefined} aria-label={alt || undefined} aria-hidden={alt ? undefined : 'true'} focusable="false" {...props}>
      {d[1]}
    </svg>
  );
}
