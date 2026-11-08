import { useEffect, useRef } from 'react';

const NAMES = ['journey', 'world', 'paper', 'mess', 'turn', 'name', 'number', 'atelier', 'planWorld', 'caption', 'hint', 'idea'];

const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const smooth = (t) => t * t * (3 - 2 * t);
const range = (v, a, b) => smooth(clamp((v - a) / (b - a)));

/**
 * The opening's camera: the scroll position drives how far the story has travelled (`target`), and what is drawn
 * eases toward it every frame. The geometry and timings are the approved ones. Everything it moves is reached
 * through refs, and every listener it adds is removed again.
 */
export function useOpeningMotion(paused) {
  const refs = useRef(Object.fromEntries(NAMES.map((n) => [n, { current: null }]))).current;
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const actions = useRef({ skip() {}, toTop() {} });

  useEffect(() => {
    const el = Object.fromEntries(NAMES.map((n) => [n, refs[n].current]));
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let target = 0;
    let current = 0;
    let atTarget = 0;
    let atCurrent = 0;
    let raf = 0;
    let last = 0;

    function draw(p, a) {
      const w = innerWidth;
      const mobile = w <= 700;
      const pan = range(p, 0.12, 0.59);
      const scale = mobile ? lerp(Math.min(w / 640, 0.72), Math.min(w / 890, 0.46), pan) : Math.min(w / 1330, 1.08);
      const x0 = mobile ? w * 0.5 - 660 * scale : w * 0.55 - 660 * scale;
      const x1 = mobile ? w * 0.5 - 1600 * scale : w * 0.5 - 1650 * scale;
      const bottom = mobile ? -65 : 20;
      el.world.style.transform = `translate3d(${lerp(x0, x1, pan)}px,${-pan * (mobile ? 145 : 100) + bottom}px,0) scale(${scale})`;
      el.world.style.opacity = 1;
      el.mess.style.opacity = 1 - range(p, 0.1, 0.27);
      el.mess.style.transform = `translate3d(${-pan * 55}px,0,0)`;
      el.turn.style.opacity = range(p, 0.47, 0.56);
      el.turn.style.transform = `translate3d(${(1 - pan) * 50}px,0,0)`;
      el.number.textContent = p < 0.28 ? '01 /' : '02 /';
      el.name.textContent = p < 0.28 ? 'THE BEAUTIFUL MESS' : 'A LITTLE HELP';
      document.documentElement.style.setProperty('--p', clamp(p / 0.64));

      // The writing paper arrives by document flow: it pins, then rises and widens.
      const entry = range(a, 0.17, 0.78);
      el.paper.style.width = `${lerp(mobile ? 44 : 31, mobile ? 88 : 48, entry)}%`;
      el.paper.style.transform = `translate(-50%,${lerp(innerHeight * 0.74, 0, entry)}px)`;
      el.paper.style.opacity = range(a, 0.18, 0.38);
      el.paper.inert = !reduce && entry < 0.98;
      el.caption.style.opacity = 1 - range(a, 0.2, 0.48);
      el.caption.style.transform = `translateY(${-entry * 18}px)`;
      el.planWorld.style.transform = `translateY(${-entry * (mobile ? 26 : 35)}px)`;
      el.hint.style.opacity = 1 - range(a, 0.63, 0.82);
    }

    function tick(t) {
      const dt = Math.min(t - last || 16, 40);
      last = t;
      const ease = 1 - Math.exp(-dt / 72);
      current = lerp(current, target, ease);
      atCurrent = lerp(atCurrent, atTarget, ease);
      if (Math.abs(current - target) < 0.0001) current = target;
      if (Math.abs(atCurrent - atTarget) < 0.0001) atCurrent = atTarget;
      draw(current, atCurrent);
      if (current !== target || atCurrent !== atTarget) raf = requestAnimationFrame(tick);
      else {
        raf = 0;
        last = 0;
      }
    }

    function update() {
      if (pausedRef.current) return;
      const mobile = innerWidth <= 700;
      target = clamp(-el.journey.getBoundingClientRect().top / (innerHeight * (mobile ? 2.3 : 2.7)), 0, 0.64);
      atTarget = clamp(-el.atelier.getBoundingClientRect().top / (el.atelier.offsetHeight - innerHeight));
      if (!raf && !reduce) raf = requestAnimationFrame(tick);
    }

    function onResize() {
      update();
      if (!reduce) draw(current, atCurrent);
    }

    let focusTimer = 0;
    actions.current = {
      skip() {
        if (reduce) {
          el.paper.scrollIntoView({ block: 'center' });
          el.paper.inert = false;
          el.idea.focus();
          return;
        }
        scrollTo({ top: el.atelier.offsetTop + (el.atelier.offsetHeight - innerHeight) * 0.85, behavior: 'smooth' });
        focusTimer = setTimeout(() => el.idea.focus({ preventScroll: true }), 1000);
      },
      toTop(e) {
        e.preventDefault();
        scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
      },
    };

    el.paper.inert = !reduce;
    addEventListener('scroll', update, { passive: true });
    addEventListener('resize', onResize);
    update();
    if (reduce) el.paper.inert = false;
    else draw(current, atCurrent);

    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(focusTimer);
      removeEventListener('scroll', update);
      removeEventListener('resize', onResize);
    };
  }, [refs]);

  return {
    refs,
    skip: () => actions.current.skip(),
    toTop: (e) => actions.current.toTop(e),
  };
}
