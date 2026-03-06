import React, { useEffect, useRef, useState, useCallback } from 'react';
import { api } from '../../services/api';

type LogFile = 'api' | 'errors';
type Filter = 'ALL' | 'INBOUND' | 'OUTBOUND' | 'CACHE HIT' | 'CACHE MISS' | 'ERROR';

const FILTERS: Filter[] = ['ALL', 'INBOUND', 'OUTBOUND', 'CACHE HIT', 'CACHE MISS', 'ERROR'];
const LINE_OPTIONS = [100, 200, 500];
const REFRESH_INTERVAL = 5000;

function levelColor(line: string): string {
  if (line.includes('ERROR'))        return '#ff5555';
  if (line.includes('WARNING'))      return '#ffb86c';
  if (line.includes('CACHE  HIT'))   return '#50fa7b';
  if (line.includes('CACHE  MISS'))  return '#ff9580';
  if (line.includes('DELTA  GET'))   return '#8be9fd';
  if (line.includes('  api  '))      return '#bd93f9';
  return '#6272a4';
}

function matchesFilter(line: string, filter: Filter): boolean {
  switch (filter) {
    case 'ALL':        return true;
    case 'INBOUND':    return line.includes('  api  ');
    case 'OUTBOUND':   return line.includes('DELTA  GET');
    case 'CACHE HIT':  return line.includes('CACHE  HIT');
    case 'CACHE MISS': return line.includes('CACHE  MISS');
    case 'ERROR':      return line.includes('ERROR') || line.includes('WARNING');
    default:           return true;
  }
}

function filterBorderColor(f: Filter): string {
  switch (f) {
    case 'INBOUND':    return '#bd93f9';
    case 'OUTBOUND':   return '#8be9fd';
    case 'CACHE HIT':  return '#50fa7b';
    case 'CACHE MISS': return '#ff9580';
    case 'ERROR':      return '#ff5555';
    default:           return '#30363d';
  }
}

export function LogViewer() {
  const [logFile, setLogFile]       = useState<LogFile>('api');
  const [lineCount, setLineCount]   = useState(200);
  const [filter, setFilter]         = useState<Filter>('ALL');
  const [lines, setLines]           = useState<string[]>([]);
  const [loading, setLoading]       = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [search, setSearch]         = useState('');
  const prevLengthRef               = useRef(0);
  const bottomRef                   = useRef<HTMLDivElement>(null);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getLogs(logFile, lineCount);
      setLines(res.lines);
      setLastUpdated(new Date());
    } catch {
      setLines(['[Error] Could not fetch logs — is the backend running?']);
    } finally {
      setLoading(false);
    }
  }, [logFile, lineCount]);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(fetchLogs, REFRESH_INTERVAL);
    return () => clearInterval(id);
  }, [autoRefresh, fetchLogs]);

  // Only auto-scroll when new lines actually arrive
  useEffect(() => {
    if (lines.length > prevLengthRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
    prevLengthRef.current = lines.length;
  }, [lines]);

  const filtered = lines.filter(l =>
    matchesFilter(l, filter) &&
    (search === '' || l.toLowerCase().includes(search.toLowerCase()))
  );

  const counts = {
    INBOUND:    lines.filter(l => matchesFilter(l, 'INBOUND')).length,
    OUTBOUND:   lines.filter(l => matchesFilter(l, 'OUTBOUND')).length,
    'CACHE HIT':  lines.filter(l => matchesFilter(l, 'CACHE HIT')).length,
    'CACHE MISS': lines.filter(l => matchesFilter(l, 'CACHE MISS')).length,
    ERROR:      lines.filter(l => matchesFilter(l, 'ERROR')).length,
  };

  return (
    <div style={styles.container}>
      {/* Toolbar */}
      <div style={styles.toolbar}>
        {/* File selector */}
        <div style={styles.group}>
          {(['api', 'errors'] as LogFile[]).map(f => (
            <button
              key={f}
              style={{ ...styles.pill, ...(logFile === f ? styles.pillActive : {}) }}
              onClick={() => setLogFile(f)}
            >
              {f === 'api' ? 'api.log' : 'errors.log'}
            </button>
          ))}
        </div>

        <div style={styles.divider} />

        {/* Filters */}
        <div style={styles.group}>
          {FILTERS.map(f => (
            <button
              key={f}
              style={{
                ...styles.pill,
                ...(filter === f ? { ...styles.pillActive, borderColor: filterBorderColor(f) } : { borderColor: filterBorderColor(f) }),
              }}
              onClick={() => setFilter(f)}
              title={f !== 'ALL' ? `${counts[f as keyof typeof counts] ?? lines.length} entries` : `${lines.length} entries`}
            >
              {f}
              {f !== 'ALL' && (
                <span style={styles.badge}>
                  {counts[f as keyof typeof counts] ?? 0}
                </span>
              )}
            </button>
          ))}
        </div>

        <div style={styles.divider} />

        {/* Search */}
        <input
          style={styles.search}
          placeholder="Search..."
          value={search}
          onChange={e => setSearch(e.target.value)}
        />

        {/* Lines */}
        <select
          style={styles.select}
          value={lineCount}
          onChange={e => setLineCount(Number(e.target.value))}
        >
          {LINE_OPTIONS.map(n => <option key={n} value={n}>Last {n}</option>)}
        </select>

        {/* Auto refresh toggle */}
        <button
          style={{ ...styles.pill, ...(autoRefresh ? styles.pillActive : {}) }}
          onClick={() => setAutoRefresh(v => !v)}
        >
          {autoRefresh ? '⏵ Live' : '⏸ Paused'}
        </button>

        {/* Manual refresh */}
        <button style={styles.pill} onClick={fetchLogs} disabled={loading}>
          {loading ? '...' : '↻'}
        </button>

        {lastUpdated && (
          <span style={styles.meta}>
            {filtered.length} lines · {lastUpdated.toLocaleTimeString()}
          </span>
        )}
      </div>

      {/* Legend */}
      <div style={styles.legend}>
        <span style={{ color: '#bd93f9' }}>■ Inbound</span>
        <span style={{ color: '#8be9fd' }}>■ Outbound (Delta)</span>
        <span style={{ color: '#50fa7b' }}>■ Cache HIT</span>
        <span style={{ color: '#ff9580' }}>■ Cache MISS</span>
        <span style={{ color: '#ff5555' }}>■ Error</span>
        <span style={{ color: '#ffb86c' }}>■ Warning</span>
      </div>

      {/* Log output */}
      <div style={styles.output}>
        {filtered.length === 0 ? (
          <div style={styles.empty}>No log entries found.</div>
        ) : (
          filtered.map((line, i) => (
            <div key={i} style={{ ...styles.line, color: levelColor(line) }}>
              {line}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    background: '#0d1117',
    fontFamily: "'JetBrains Mono', 'Fira Code', 'Courier New', monospace",
    fontSize: 12,
    borderRadius: 8,
    overflow: 'hidden',
    border: '1px solid #30363d',
  },
  toolbar: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: 6,
    padding: '8px 12px',
    background: '#161b22',
    borderBottom: '1px solid #30363d',
  },
  legend: {
    display: 'flex',
    gap: 16,
    padding: '4px 12px',
    background: '#161b22',
    borderBottom: '1px solid #30363d',
    fontSize: 11,
  },
  group: {
    display: 'flex',
    gap: 4,
  },
  divider: {
    width: 1,
    height: 20,
    background: '#30363d',
    margin: '0 2px',
  },
  pill: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    padding: '3px 10px',
    borderRadius: 4,
    border: '1px solid #30363d',
    background: '#21262d',
    color: '#8b949e',
    cursor: 'pointer',
    fontSize: 11,
    fontFamily: 'inherit',
  },
  pillActive: {
    background: '#1f6feb',
    color: '#fff',
    borderColor: '#388bfd',
  },
  badge: {
    background: '#30363d',
    borderRadius: 8,
    padding: '0 5px',
    fontSize: 10,
    color: '#8b949e',
  },
  search: {
    padding: '3px 8px',
    borderRadius: 4,
    border: '1px solid #30363d',
    background: '#21262d',
    color: '#f0f6fc',
    fontSize: 11,
    fontFamily: 'inherit',
    width: 140,
    outline: 'none',
  },
  select: {
    padding: '3px 6px',
    borderRadius: 4,
    border: '1px solid #30363d',
    background: '#21262d',
    color: '#8b949e',
    fontSize: 11,
    fontFamily: 'inherit',
    outline: 'none',
  },
  meta: {
    color: '#484f58',
    fontSize: 11,
    marginLeft: 4,
  },
  output: {
    flex: 1,
    overflowY: 'auto',
    padding: '8px 12px',
  },
  line: {
    padding: '1px 0',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-all',
    lineHeight: 1.6,
  },
  empty: {
    color: '#484f58',
    padding: 20,
    textAlign: 'center',
  },
};
