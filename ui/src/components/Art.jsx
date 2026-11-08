// The illustrations, found by name. A drawing that is not in this checkout (the two reference-* files are kept out of git
// until their licence is confirmed) simply is not drawn: nothing else on the page depends on it.
const files = import.meta.glob('../../art/*.webp', { eager: true, query: '?url', import: 'default' });
const byName = Object.fromEntries(Object.entries(files).map(([path, url]) => [path.split('/').pop().replace('.webp', ''), url]));

export const artUrl = (name) => byName[name];

export function Art({ name, alt = '', ...props }) {
  const src = byName[name];
  return src ? <img src={src} alt={alt} draggable={false} {...props} /> : null;
}
