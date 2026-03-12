import React, { useState, useEffect } from 'react';

interface Props {
  date: string;
  initialTimestamp: number;
  onTimeChange: (timestamp: number) => void;
}

export const TimeSlider: React.FC<Props> = ({ date, initialTimestamp, onTimeChange }) => {
  const [sliderValue, setSliderValue] = useState(0); // minutes from start of day

  useEffect(() => {
    if (initialTimestamp > 0) {
      const baseTime = new Date(`${date}T00:00:00Z`).getTime();
      const diffMinutes = Math.floor((initialTimestamp * 1000 - baseTime) / (60 * 1000));
      setSliderValue(Math.max(0, Math.min(24 * 60 - 1, diffMinutes)));
    }
  }, [initialTimestamp, date]);

  useEffect(() => {
    const baseTime = new Date(`${date}T00:00:00Z`).getTime();
    const currentTimestamp = Math.floor(baseTime / 1000) + sliderValue * 60;
    
    const timer = setTimeout(() => {
      onTimeChange(currentTimestamp);
    }, 50);
    
    return () => clearTimeout(timer);
  }, [sliderValue, date, onTimeChange]);

  const formatTimeIST = (minutes: number) => {
    // minutes are in UTC relative to the start of the day
    // To show IST, add 5h 30m (330 minutes)
    const istMinutes = (minutes + 330) % (24 * 60);
    const h = Math.floor(istMinutes / 60).toString().padStart(2, '0');
    const m = (istMinutes % 60).toString().padStart(2, '0');
    return `${h}:${m} IST`;
  };

  const formatTimeUTC = (minutes: number) => {
    const h = Math.floor(minutes / 60).toString().padStart(2, '0');
    const m = (minutes % 60).toString().padStart(2, '0');
    return `${h}:${m} UTC`;
  };

  return (
    <div className="time-slider-card" style={{ margin: '0', background: 'transparent', border: 'none', padding: '0' }}>
      <div className="time-slider-header">
        <span className="time-limit">{formatTimeIST(0)}</span>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
           <span className="time-display">{formatTimeIST(sliderValue)}</span>
           <span style={{ fontSize: '10px', color: 'var(--text3)' }}>({formatTimeUTC(sliderValue)})</span>
        </div>
        <span className="time-limit">{formatTimeIST(24 * 60 - 1)}</span>
      </div>
      <input
        type="range"
        min="0"
        max={24 * 60 - 1}
        value={sliderValue}
        onChange={(e) => setSliderValue(parseInt(e.target.value))}
        className="slider-input"
      />
    </div>
  );
};
