import { useEffect, useId, useRef, useState } from 'react';
import { Art } from '../components/Art';
import { EDITABLE, fieldLabel, rows } from '../lib/brief';

/** What the card says in the speech bubble, given where the conversation is. */
function speech({ ready, editing, answer, question }) {
  if (!ready) return question || 'Tell me a little more.';
  if (editing) return 'Change what I got wrong.';
  return answer === 'yes' ? 'All right. Your call.' : 'Does this look right?';
}

function OpenQuestions({ turn, busy, onPick }) {
  const open = turn.draft?.unresolved || [];
  if (!open.length) return null;
  return (
    <div className="open-questions">
      {open.map((q) => {
        const choice = (turn.choices || []).find((c) => c.key === q.key);
        return (
          <fieldset key={q.key} className="open-question">
            <legend>{q.question}</legend>
            {choice ? (
              <div className="chips">
                {choice.labels.map((label, i) => (
                  // the number of the option is sent, not the words, so nothing is read twice
                  <button key={label} type="button" className="chip" disabled={busy} onClick={() => onPick(label, { key: q.key, pick: i })}>
                    {label}
                  </button>
                ))}
              </div>
            ) : (
              <p className="quiet">Type your answer in the box below.</p>
            )}
          </fieldset>
        );
      })}
    </div>
  );
}

function FieldRow({ row, index, editing, value, onChange, error }) {
  const id = useId();
  const type = EDITABLE[row.key];
  const typed = editing && type;
  return (
    <div className="json-line" style={{ '--i': index }}>
      <label className="json-key" htmlFor={typed ? id : undefined}>{row.label}</label>
      <span className="json-value">
        {typed ? (
          <input id={id} type={type} min={type === 'number' ? 1 : undefined} value={value} onChange={(e) => onChange(e.target.value)} aria-invalid={error ? true : undefined} required />
        ) : (
          <output htmlFor={id}>{row.value}</output>
        )}
      </span>
    </div>
  );
}

/** Missing facts show as null: the card says what it does not know instead of leaving a gap. */
function missingRows(turn, shown) {
  const have = new Set(shown.map((r) => r.key));
  return (turn.missing || []).filter((k) => !have.has(k) && k in EDITABLE).map((key) => ({ key, label: fieldLabel(key), value: 'Not given', raw: null, editable: true }));
}

export function Review({ brief, onDone, onBack }) {
  const { turn, busy, error, read, edit, messages } = brief;
  const [answer, setAnswer] = useState(null);
  const [typed, setTyped] = useState({});
  const [more, setMore] = useState('');
  const yesRef = useRef(null);
  const ready = Boolean(turn?.ready);
  const editing = answer === 'no' || !ready;

  const shown = rows(turn.draft);
  const all = [...shown, ...missingRows(turn, shown)];
  const valueOf = (row) => (row.key in typed ? typed[row.key] : row.raw == null ? '' : String(row.raw));
  const dirty = Object.keys(typed).filter((k) => String(typed[k]) !== String(turn.draft[k] ?? ''));
  // this page plans from vendors who answered, so "no vendor records for this city" does not apply to it
  const notes = [...(turn.flags?.length ? [`Please double-check: ${turn.flags.join(', ').replace(/_/g, ' ')}`] : []), ...(turn.notes || []).filter((n) => !/vendor records for/i.test(n))];

  useEffect(() => {
    // the person's next move is the first thing they can act on
    const target = ready ? yesRef.current : document.querySelector('.open-question .chip, .json-value input, #more-words');
    target?.focus({ preventScroll: true });
  }, [ready, turn?.staged_id]);

  async function save() {
    const patch = {};
    for (const k of dirty) patch[k] = EDITABLE[k] === 'number' ? Number(typed[k]) : typed[k];
    if (await edit(patch)) setTyped({});
  }

  async function addWords(e) {
    e.preventDefault();
    if (await read(more)) setMore('');
  }

  function arrows(e) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    document.getElementById(e.key === 'ArrowLeft' ? 'answer-yes' : 'answer-no')?.focus();
  }

  return (
    <>
      <div className="scene-top">
        <a className="wordmark" href="/" aria-label="to;doh home">to;doh</a>
        <span>02 / THE HUMAN PART</span>
        <button id="return-idea" type="button" onClick={onBack}>Back to your words ↗</button>
      </div>

      <div className={`review-stage${editing ? ' editing' : ''}`}>
        <div className="data-island">
          <p className="data-heading">WHAT I READ FROM YOUR WORDS</p>
          <h2>Your idea.<br /><em>Your call.</em></h2>

          <form id="json-review" onSubmit={(e) => { e.preventDefault(); if (dirty.length) save(); }} noValidate>
            <div className="json-lines">
              {all.map((row, i) => (
                <FieldRow key={row.key} row={row} index={i} editing={editing} value={valueOf(row)} onChange={(v) => setTyped((t) => ({ ...t, [row.key]: v }))} />
              ))}
            </div>
            {editing && dirty.length > 0 && (
              <div className="review-save">
                <button type="submit" className="btn btn--small" disabled={busy}>{busy ? 'Saving…' : 'Save changes'}</button>
                <button type="button" className="linkish" onClick={() => setTyped({})}>Discard</button>
              </div>
            )}
          </form>

          {turn.changes?.length > 0 && <ul className="plain-list changes" aria-label="What changed">{turn.changes.map((c) => <li key={c}>{c}</li>)}</ul>}
          {notes.length > 0 && <ul className="plain-list">{notes.map((n) => <li key={n}>{n}</li>)}</ul>}
          <OpenQuestions turn={turn} busy={busy} onPick={read} />

          {editing && (
            <form className="more-words" onSubmit={addWords}>
              <label htmlFor="more-words">Say it in your own words</label>
              <div className="more-row">
                <textarea id="more-words" rows={2} maxLength={2000} value={more} onChange={(e) => setMore(e.target.value)} placeholder="Make it 80 guests. The date is the 20th. Two guests are allergic to peanuts." />
                <button type="submit" className="btn btn--small" disabled={busy || !more.trim()}>{busy ? 'Reading…' : 'Update'}</button>
              </div>
            </form>
          )}

          <p className="review-status" role="status">
            Allergy and access words stay unverified until a vendor confirms them in writing. Nothing is sent, booked or paid from this page.
          </p>
          <p className="error-review" role="alert">{error}</p>
        </div>

        <div className="review-question">
          <div className={`speech${!ready && (turn.question || '').length > 60 ? ' speech--long' : ''}`}>{speech({ ready, editing, answer, question: turn.question })}</div>
          <svg className="speech-arrow" viewBox="0 0 24 34" aria-hidden="true"><path d="M5 2c12 3 16 14 9 27M14 29l-6-5M14 29l5-6" /></svg>
          <Art name="reference-head-closed" className="review-mascot" alt="" />
          {ready ? (
            <div className="review-options" role="group" aria-label="Is this right?" onKeyDown={arrows}>
              <button ref={yesRef} className="word-choice" id="answer-yes" type="button" aria-pressed={answer === 'yes'} onClick={() => setAnswer('yes')}>Yes</button>
              <button className="word-choice" id="answer-no" type="button" aria-pressed={answer === 'no'} onClick={() => setAnswer('no')}>No</button>
            </div>
          ) : null}
          <button className="plain-done" id="review-done" type="button" disabled={!ready || dirty.length > 0 || answer !== 'yes'} onClick={onDone} aria-describedby="done-why">
            Done
          </button>
          <p className="choice-hint" id="done-why">
            {!ready ? 'Answer the question first.' : dirty.length > 0 ? 'Save your changes, then say Yes.' : answer !== 'yes' ? 'Say Yes when this is right. Choose No to change it.' : 'Ready when you are.'}
          </p>
        </div>
      </div>

      <div className="review-footer">
        <span>Nothing leaves this page until you approve each message.</span>
        <details className="original-words">
          <summary>Your original words ↗</summary>
          <p>{messages.join('\n\n')}</p>
        </details>
      </div>
    </>
  );
}
