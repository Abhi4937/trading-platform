import * as XLSX from 'xlsx';

// Generic single-sheet .xlsx export — converts an array of plain rows into a
// workbook and triggers a browser download. Caller is expected to pass rows
// that are already JSON-friendly (numbers, strings, nulls). For derived /
// scaled values, do the computation in the caller and stick them on the row
// objects.
export function exportRowsAsXlsx(
  filename: string,
  sheetName: string,
  rows: Record<string, unknown>[],
): void {
  if (!rows || rows.length === 0) {
    // Still create the file so the user gets feedback the click registered.
    const wb = XLSX.utils.book_new();
    const ws = XLSX.utils.aoa_to_sheet([['No data']]);
    XLSX.utils.book_append_sheet(wb, ws, sheetName);
    XLSX.writeFile(wb, filename);
    return;
  }
  const wb = XLSX.utils.book_new();
  const ws = XLSX.utils.json_to_sheet(rows);
  XLSX.utils.book_append_sheet(wb, ws, sheetName.slice(0, 31));  // Excel limit
  XLSX.writeFile(wb, filename);
}

// Small inline-styled button matching the dashboard look. Renders an icon +
// "Excel" label so it's consistent across all M7 tables.
import React from 'react';
export function ExcelButton({ onClick, disabled, title }: {
  onClick: () => void; disabled?: boolean; title?: string;
}) {
  return (
    <button onClick={onClick} disabled={disabled} title={title || 'Download as Excel'}
      style={{
        padding: '4px 10px', fontSize: 11, cursor: disabled ? 'not-allowed' : 'pointer',
        background: '#1f6feb22', color: '#cfd9e3',
        border: '1px solid #1a2d42', borderRadius: 4,
        whiteSpace: 'nowrap',
        opacity: disabled ? 0.4 : 1,
      }}>
      ⬇ Excel
    </button>
  );
}
