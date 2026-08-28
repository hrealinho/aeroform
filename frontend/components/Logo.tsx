/** The same mark as the favicon, so the tab and the sidebar agree. */
export default function Logo({size = 22}: {size?: number}) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden focusable="false">
      <path d="M4 23.5 L11 14.5 L15.5 19 L21.5 8 L28 23.5 Z" fill="currentColor" />
      <path d="M21.5 8 L28 23.5 L15.5 23.5 Z" fill="var(--accent-mark)" />
    </svg>
  );
}
