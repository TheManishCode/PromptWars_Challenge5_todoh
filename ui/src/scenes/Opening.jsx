import { useState } from 'react';
import { Art } from '../components/Art';
import { useOpeningMotion } from './useOpeningMotion';

/**
 * The illustrated opening: a scroll-driven story that ends on the box where the idea is written.
 * The artwork, lettering and camera movement are the approved ones; only the wiring is new.
 * `paused` stops the scroll choreography while a later scene is on screen.
 */
export function Opening({ onIdea, busy, error, paused, initialText = '' }) {
  const [text, setText] = useState(initialText);
  const { refs: r, skip, toTop } = useOpeningMotion(paused);

  function submit(e) {
    e.preventDefault();
    if (text.trim() && !busy) onIdea(text);
  }

  return (
    <>
        <header>
          <a href="#" className="brand" aria-label="to;doh, back to the start" onClick={toTop}>
            to;doh
            <span className="brand-dot" />
          </a>
          <span className="edition">A LITTLE LESS TANGLE.</span>
          <button className="skip" type="button" onClick={skip}>
            Tell us your idea
            <span aria-hidden="true">↗</span>
          </button>
        </header>
        <main>
          <section ref={r.journey} className="journey" aria-label="From tangled plans to a clear brief">
            <div className="stage">
              <div className="chapter">
                <span ref={r.number} className="chapter-number">01 /</span>
                <span ref={r.name} id="chapter-name">THE BEAUTIFUL MESS</span>
              </div>
              <div ref={r.mess} className="copy copy-mess">
                <h1>
                  Big plans.
                  <br />
                  <span>Little tangles.</span>
                </h1>
                <p>Good times shouldn&apos;t be this much work.</p>
              </div>
              <div ref={r.turn} className="copy copy-turn">
                <p className="eyebrow">MAKE ROOM FOR THE GOOD PART.</p>
                <h2>
                  A little help.
                  <br />
                  A lot more life.
                </h2>
              </div>
              <div ref={r.world} className="world" aria-hidden="true">
                <svg className="landscape" viewBox="0 0 2400 760" fill="none">
                  <defs>
                    <pattern id="dots" width={7} height={7} patternUnits="userSpaceOnUse">
                      <circle cx="1" cy="1" r=".75" fill="#151515" />
                    </pattern>
                  </defs>
                  <path className="ground-fill" d="M-80 625 Q190 604 340 619 Q580 647 780 625 Q1100 602 1260 625 Q1450 641 1690 620 Q1970 602 2470 630 L2470 820 L-80 820Z" fill="url(#dots)" />
                  <path d="M-80 625 Q190 604 340 619 Q580 647 780 625 Q1100 602 1260 625 Q1450 641 1690 620 Q1970 602 2470 630" stroke="currentColor" strokeWidth="2.2" />
                  <path className="thread-shadow" d="M650 581 C770 530 755 681 874 637 S988 548 1065 596 S1142 665 1225 615 S1320 594 1398 617 C1555 663 1588 556 1666 594 S1784 683 1907 613 S2064 582 2220 608" stroke="#fff" strokeWidth="11" />
                  <path className="thread" d="M650 581 C770 530 755 681 874 637 S988 548 1065 596 S1142 665 1225 615 S1320 594 1398 617 C1555 663 1588 556 1666 594 S1784 683 1907 613 S2064 582 2220 608" stroke="currentColor" strokeWidth="3.3" strokeLinecap="round" />
                  <g stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M1000 618l-4-15m12 14 3-10m8 12 8-16 M1496 621l-5-14m16 14 2-10 M2190 619l4-14m6 14 7-9" />
                    <path d="M930 433q-13-24-37-13m37 13q9-28 30-20" />
                    <path d="M2160 414q-13-24-37-13m37 13q9-28 30-20" />
                  </g>
                </svg>
                <div className="mess-illustration">
                  <Art name="story-knot" alt="" />
                  <span className="scribble-label sl-one">THE GUEST LIST</span>
                  <span className="scribble-label sl-two">THE BUDGET</span>
                  <span className="scribble-label sl-three">EVERYTHING ELSE</span>
                  <svg className="ink-marks" viewBox="0 0 760 520">
                    <path d="M85 285q-30-20-42-8m47-23-29-25 M650 310q35-14 39-5m-34-25 24-18 M352 38l-5-28m23 34 10-25" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
                  </svg>
                </div>
                <div className="welcome-world">
                  <div className="world-word">
                    to;doh
                    <svg className="word-grain" width="100%" height="100%" aria-hidden="true">
                      <defs>
                        <pattern id="grain" width={6} height={6} patternUnits="userSpaceOnUse">
                          <circle cx="2" cy="2" r=".65" fill="white" />
                        </pattern>
                        <mask id="letter-mask">
                          <text x="0" y="83%" fill="white" fontFamily="Arial Black,Arial,sans-serif" fontSize="250" fontWeight="900" letterSpacing="-19">to;doh</text>
                        </mask>
                      </defs>
                    </svg>
                  </div>
                  <Art name="story-char-cat" className="cast cast-cat" alt="" />
                  <Art name="story-char-frog" className="cast cast-frog" alt="" />
                  <Art name="story-char-robot" className="cast cast-robot" alt="" />
                  <Art name="story-char-tallbird" className="cast cast-bird" alt="" />
                  <Art name="story-char-duck" className="cast cast-duck" alt="" />
                  <Art name="story-relaxed" className="calm" alt="" />
                  <div className="world-sign">
                    YOU BRING THE PEOPLE.
                    <br />
                    WE&apos;LL HELP WITH THE PLAN.
                  </div>
                </div>
              </div>
              <div className="bottom-note">
                <span className="scroll-invite">
                  SCROLL TO UNTANGLE
                  <span aria-hidden="true">↓</span>
                </span>
                <span className="direction-label">NOTHING IS SENT UNTIL YOU SAY SO</span>
              </div>
              <div className="progress" aria-hidden="true">
                <span />
              </div>
            </div>
          </section>
          <section ref={r.atelier} className="atelier" aria-label="A place to plan">
            <div className="atelier-stage">
              <div className="atelier-top">
                <span>03 / A PLACE TO THINK</span>
                <span>YOUR IDEAS BELONG HERE.</span>
              </div>
              <div ref={r.planWorld} className="planning-world" aria-hidden="true">
                <svg className="desk-lines" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice">
                  <defs>
                    <pattern id="desk-dots" width={9} height={9} patternUnits="userSpaceOnUse">
                      <circle cx="1" cy="1" r=".65" fill="#171717" />
                    </pattern>
                  </defs>
                  <path d="M0 540 Q300 510 630 555 T1440 535 L1440 900 H0Z" fill="url(#desk-dots)" opacity=".2" />
                  <path d="M-40 570 Q340 530 650 575 T1490 553" fill="none" stroke="#171717" strokeWidth="2" />
                  <path className="thought-thread" d="M335 584 C555 551 365 318 571 349 S718 519 885 438 S1028 281 1240 320" fill="none" stroke="#171717" strokeWidth="2" />
                  <path d="M1160 270l40 4-14-31m-15 21 30 10" fill="none" stroke="#171717" strokeWidth="2" />
                </svg>
                <Art name="story-relaxed" className="thinking-person" alt="" />
                <div className="idea-board">
                  <span className="board-pin" />
                  <span className="board-note">THE GOOD PART.</span>
                  <Art name="story-table-sketch" alt="" />
                  <span className="board-caption">PEOPLE. FOOD. A REASON TO MEET.</span>
                </div>
                <div className="notes-stack">
                  <span className="mini-note note-a">
                    People
                    <br />
                    <i>all together.</i>
                  </span>
                  <span className="mini-note note-b">
                    A place
                    <br />
                    <i>that feels right.</i>
                  </span>
                  <span className="mini-note note-c">
                    A plan
                    <br />
                    <i>with room to breathe.</i>
                  </span>
                </div>
                <Art name="story-char-music" className="desk-helper" alt="" />
                <svg className="desk-scribble" viewBox="0 0 200 100">
                  <path d="M10 60q60-70 95-21t76-18M50 65q60-40 105 3" fill="none" stroke="currentColor" strokeWidth="2" />
                </svg>
              </div>
              <p ref={r.caption} className="atelier-caption">
                An idea in your head.
                <br />
                <em>A little space to make it happen.</em>
              </p>
              <div ref={r.paper} className="workspace-input" id="brief">
                <div className="form-content">
                  <p className="eyebrow">YOUR IDEAS, WITH A LITTLE ROOM TO GROW.</p>
                  <h2>What are we planning?</h2>
                  <form id="idea-form" onSubmit={submit}>
                    <label htmlFor="idea">Tell us a little about your event</label>
                    <textarea
                      id="idea"
                      ref={r.idea}
                      rows={2}
                      maxLength={2000}
                      placeholder="A birthday for 50 in Pune. The date, budget, and whatever is on your mind."
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) submit(e); }}
                      aria-describedby={error ? 'idea-error' : undefined}
                      aria-invalid={error ? true : undefined}
                      required
                    />
                    <div className="form-bottom">
                      <span>Messy thoughts welcome. Press Enter to send, Shift+Enter for a new line.</span>
                      <button type="submit" disabled={busy}>
                        {busy ? 'Reading…' : 'Send for review'}
                        <span aria-hidden="true">↗</span>
                      </button>
                    </div>
                    <p className="form-error" id="idea-error" role="alert">{error}</p>
                  </form>
                </div>
              </div>
              <div className="atelier-bottom">
                <span ref={r.hint} className="atelier-hint">KEEP SCROLLING. MAKE SOME ROOM. ↓</span>
                <span>NOTHING IS SENT UNTIL YOU SAY SO</span>
              </div>
            </div>
          </section>
        </main>
    </>
  );
}
