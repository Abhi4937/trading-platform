import React, { useRef } from 'react';

interface Props {
  simulationDate: string; // YYYY-MM-DD
  simulationTime: string; // HH:mm
  expiries: { date: string, label: string }[];
  selectedExpiry: string;
  minDate?: string;
  maxDate?: string;
  onDateChange: (date: string) => void;
  onTimeChange: (time: string) => void;
  onExpiryChange: (expiry: string) => void;
  onStep: (minutes: number) => void;
}

export const ReplayController: React.FC<Props> = ({
  simulationDate,
  simulationTime,
  expiries,
  selectedExpiry,
  minDate,
  maxDate,
  onDateChange,
  onTimeChange,
  onExpiryChange,
  onStep
}) => {
  const dateInputRef = useRef<HTMLInputElement>(null);
  const timeInputRef = useRef<HTMLInputElement>(null);

  // Format: Thu 12 Mar 26
  const formatDateDisplay = (dateStr: string) => {
    try {
      if (!dateStr) return 'Select Date';
      const d = new Date(dateStr);
      return d.toLocaleDateString('en-GB', {
        weekday: 'short',
        day: '2-digit',
        month: 'short',
        year: '2-digit'
      }).replace(/,/g, '');
    } catch {
      return dateStr;
    }
  };

  const timeToMinutes = (timeStr: string) => {
    const [h, m] = timeStr.split(':').map(Number);
    return (h || 0) * 60 + (m || 0);
  };

  const minutesToTime = (totalMinutes: number) => {
    const h = Math.floor(totalMinutes / 60);
    const m = totalMinutes % 60;
    return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`;
  };

  // Helper to trigger native picker
  const handleWrapperClick = (ref: React.RefObject<HTMLInputElement>) => {
    if (ref.current) {
      if ('showPicker' in ref.current) {
        (ref.current as any).showPicker();
      } else {
        ref.current.click();
      }
    }
  };

  return (
    <div className="replay-ctrl-bar-wrapper">
      <div className="replay-ctrl-bar">
        {/* Left: Minus Steps */}
        <div className="ctrl-steps">
          <button className="step-btn" onClick={() => onStep(-1440)}>-1d</button>
          <button className="step-btn" onClick={() => onStep(-60)}>-1h</button>
          <button className="step-btn" onClick={() => onStep(-30)}>-30m</button>
          <button className="step-btn" onClick={() => onStep(-15)}>-15m</button>
          <button className="step-btn" onClick={() => onStep(-5)}>-5m</button>
          <button className="step-btn" onClick={() => onStep(-1)}>-1m</button>
        </div>

        {/* Center: Pickers */}
        <div className="ctrl-pickers">
          <div className="picker-group">
            <label className="ctrl-label">Simulation Date</label>
            <div className="picker-input-wrap" onClick={() => handleWrapperClick(dateInputRef)}>
              <span className="picker-display">{formatDateDisplay(simulationDate)}</span>
              <input 
                ref={dateInputRef}
                type="date" 
                className="ghost-input" 
                value={simulationDate}
                min={minDate}
                max={maxDate}
                onChange={(e) => onDateChange(e.target.value)}
              />
            </div>
          </div>

          <div className="sep" />

          <div className="picker-group">
            <label className="ctrl-label">Time (IST)</label>
            <div className="picker-input-wrap" onClick={() => handleWrapperClick(timeInputRef)}>
              <span className="picker-display">{simulationTime}</span>
              <input 
                ref={timeInputRef}
                type="time" 
                className="ghost-input" 
                value={simulationTime}
                onChange={(e) => onTimeChange(e.target.value)}
              />
            </div>
          </div>

          <div className="sep" />

          <div className="picker-group">
            <label className="ctrl-label">Option Expiry</label>
            <select 
              className="sel-input"
              value={selectedExpiry}
              onChange={(e) => onExpiryChange(e.target.value)}
            >
              {expiries.map(exp => (
                <option key={exp.date} value={exp.date}>{exp.label}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Right: Plus Steps */}
        <div className="ctrl-steps">
          <button className="step-btn" onClick={() => onStep(1)}>+1m</button>
          <button className="step-btn" onClick={() => onStep(5)}>+5m</button>
          <button className="step-btn" onClick={() => onStep(15)}>+15m</button>
          <button className="step-btn" onClick={() => onStep(30)}>+30m</button>
          <button className="step-btn" onClick={() => onStep(60)}>+1h</button>
          <button className="step-btn" onClick={() => onStep(1440)}>+1d</button>
        </div>
      </div>

      {/* Restore the horizontal time slider with labels */}
      <div className="slider-container">
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px', fontSize: '10px', color: 'var(--text3)', fontFamily: 'var(--mono)' }}>
          <span>00:00</span>
          <span>23:59</span>
        </div>
        <input 
          type="range"
          className="replay-slider"
          min="0"
          max="1439"
          value={timeToMinutes(simulationTime)}
          onChange={(e) => onTimeChange(minutesToTime(parseInt(e.target.value)))}
        />
      </div>
    </div>
  );
};
