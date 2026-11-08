import { Note } from '../components/Note';

/** Who is acting. A confirmation or an approval is recorded under a name, so it is asked once and kept for this visit. */
export function NameNote({ by, setBy }) {
  return (
    <Note tone="plain" title="Your name">
      <label className="field">
        <span className="sr-only">Your name</span>
        <input type="text" maxLength={60} value={by} placeholder="Recorded with what you confirm" autoComplete="name" onChange={(e) => setBy(e.target.value)} />
      </label>
    </Note>
  );
}
