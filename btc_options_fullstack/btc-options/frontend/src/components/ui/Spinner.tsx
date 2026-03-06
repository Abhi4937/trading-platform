export function Spinner({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size} height={size}
      viewBox="0 0 24 24" fill="none"
      style={{ animation: 'spin 0.7s linear infinite' }}
      className="spinner"
    >
      <circle cx="12" cy="12" r="10" stroke="#1a2d42" strokeWidth="3" />
      <path d="M12 2a10 10 0 0 1 10 10" stroke="#00d4ff" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
