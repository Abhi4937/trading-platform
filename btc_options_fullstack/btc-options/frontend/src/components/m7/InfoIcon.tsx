import React from 'react';

// Small (i) glyph next to a label. Hover/focus → native tooltip;
// click → floating popover so touch users can read it too.
export function InfoIcon({ text, color = '#7a9bb5' }: { text: string; color?: string }) {
  const [open, setOpen] = React.useState(false);
  return (
    <span style={{ position: 'relative', display: 'inline-block' }}>
      <span
        role="button"
        tabIndex={0}
        title={text}
        aria-label={text}
        onClick={(e) => { e.stopPropagation(); setOpen(o => !o); }}
        onBlur={() => setOpen(false)}
        style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          width: 12, height: 12, marginLeft: 4,
          borderRadius: '50%',
          border: `1px solid ${color}`,
          color, background: 'transparent',
          fontSize: 9, fontStyle: 'italic', fontWeight: 700,
          cursor: 'help', verticalAlign: 'middle',
          userSelect: 'none',
        }}
      >i</span>
      {open && (
        <span style={{
          position: 'absolute', zIndex: 100,
          top: 'calc(100% + 4px)', left: '50%', transform: 'translateX(-50%)',
          background: '#0d1421', color: '#cfd9e3',
          border: '1px solid #1a2d42', borderRadius: 4,
          padding: '6px 8px', fontSize: 11, lineHeight: 1.4,
          width: 'max-content', maxWidth: 280, whiteSpace: 'normal',
          textAlign: 'left', fontWeight: 400, fontStyle: 'normal',
          boxShadow: '0 6px 18px rgba(0,0,0,0.6)',
        }}
          onClick={(e) => e.stopPropagation()}
        >{text}</span>
      )}
    </span>
  );
}
