import { useLayoutEffect, useState } from 'react';

/** The thin line from the chosen bubble to the notes it opened. It follows both through scrolling and resizing. */
export function useConnector(rootRef, active) {
  const [path, setPath] = useState('');
  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!active || !root) { setPath(''); return undefined; }
    let frame = 0;
    function measure() {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const from = root.querySelector('.graph-node.chosen')?.getBoundingClientRect();
        const to = root.querySelector('.node-page')?.getBoundingClientRect();
        const box = root.getBoundingClientRect();
        if (!from || !to || innerWidth < 1000) return setPath('');
        const x = from.right - box.left;
        const y = from.top + from.height / 2 - box.top + root.scrollTop;
        const ex = to.left - box.left;
        const ey = to.top + 52 - box.top + root.scrollTop;
        setPath(`M ${x} ${y} C ${x + 40} ${y}, ${ex - 40} ${ey}, ${ex} ${ey}`);
      });
    }
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(root);
    const page = root.querySelector('.node-page');
    if (page) ro.observe(page);
    root.addEventListener('scroll', measure, { passive: true });
    addEventListener('resize', measure);
    return () => { cancelAnimationFrame(frame); ro.disconnect(); root.removeEventListener('scroll', measure); removeEventListener('resize', measure); };
  }, [rootRef, active]);
  return path;
}
