/** A small dot that breathes while something is on its way. Still under reduced motion. */
export function Pulse({ children }) {
  return (
    <span className="pulse">
      <i className="pulse-dot" aria-hidden="true" />
      {children}
    </span>
  );
}
