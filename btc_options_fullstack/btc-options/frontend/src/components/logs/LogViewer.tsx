import React, { useEffect, useRef, useState, useCallback } from 'react';
import { api } from '../../services/api';

type LogFile = 'api' | 'errors';
type Filter = 'ALL' | 'INFO' | 'ERROR' | 'CACHE' | 'DELTA';

const FILTERS: Filter[] = ['ALL', 'INFO', 'ERROR', 'CACHE', 'DELTA'];
const LINE_OPTIONS = [100, 200, 500];
const REFRESH_INTERVAL = 5000;

function levelColor(line: string): string {
  if (line.includes('ERROR'))             return '#ff5555';
  if (line.includes('WARNING'))           return '#ffb86c';
  if (line.includes('CACHE  HIT'))        return '#50fa7b';
  if (line.includes('CACHE  MISS'))       return '#ff9580';
  if (line.includes('DELTA'))             return '#8be9fd';
  if (line.includes('INFO'))              return '#f8f8f2';
  return '#6272a4';
}

function matchesFilter(line: string, filter: Filter): boolean {
  if (filter === 'ALL')   return true;
  if (filter === 'ERROR') return line.includes('ERROR');
  if (filter === 'INFO')  return line.includes('INFO') && !line.includes('CACHE') && !line.includes('DELTA');
  if (filter === 'CACHE') return line.includes('CACHE');
  if (filter === 'DELTA') return line.includes('DELTA');
  return true;
}

export function LogViewer() {
  const [logFile, setLogFile] = useState<LogFile>('api');
  const [lineCount, setLineCount] = useState(200);
  const [filter, setFilter] = useState<Filter>('ALL');
  const [lines, setLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [search, setSearch] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [lines]);

  const filtered = lines.filter(l =>
    matchesFilter(l, filter) &&
    (search === '' || l.toLowerCase().includes(search.toLowerCase()))
  );

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

        {/* Filters */}
        <div style={styles.group}>
          {FILTERS.map(f => (
            <button
              key={f}
              style={{ ...styles.pill, ...(filter === f ? styles.pillActive : {}), ...filterColor(f) }}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>

        {/* Search */}
        <input
          style={styles.search}
          placeholder="Search logs..."
          value={search}
          onChange={e => setSearch(e.target.value)}
        />

        {/* Lines */}
        <select
          style={styles.select}
          value={lineCount}
          onChange={e => setLineCount(Number(e.target.value))}
        >
          {LINE_OPTIONS.map(n => <option key={n} value={n}>Last {n} lines</option>)}
        </select>

        {/* Auto refresh */}
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

function filterColor(f: Filter): React.CSSProperties {
  if (f === 'ERROR') return { borderColor: '#ff5555' };
  if (f === 'CACHE') return { borderColor: '#50fa7b' };
  if (f === 'DELTA') return { borderColor: '#8be9fd' };
  return {};
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
  group: {
    display: 'flex',
    gap: 4,
  },
  pill: {
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
  search: {
    padding: '3px 8px',
    borderRadius: 4,
    border: '1px solid #30363d',
    background: '#21262d',
    color: '#f0f6fc',
    fontSize: 11,
    fontFamily: 'inherit',
    width: 160,
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
