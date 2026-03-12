import React from 'react';

interface Props {
  simulationDate: string; // YYYY-MM-DD
  simulationTime: string; // HH:mm
  expiries: { date: string, label: string }[];
  selectedExpiry: string;
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
  onDateChange,
  onTimeChange,
  onExpiryChange,
  onStep
}) => {
  // Format: Thu 12 Mar 26
  const formatDate = (dateStr: string) => {
    try {
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

  return (
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
          <div className="picker-input-wrap">
            <span className="picker-display">{formatDate(simulationDate)}</span>
            <input 
              type="date" 
              className="ghost-input" 
              value={simulationDate}
              onChange={(e) => onDateChange(e.target.value)}
            />
          </div>
        </div>

        <div className="sep" />

        <div className="picker-group">
          <label className="ctrl-label">Time (IST)</label>
          <div className="picker-input-wrap">
            <span className="picker-display">{simulationTime}</span>
            <input 
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
  );
};
