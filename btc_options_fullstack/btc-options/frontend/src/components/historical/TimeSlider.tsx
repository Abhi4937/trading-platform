import React, { useState, useEffect } from 'react';

interface Props {
  date: string;
  onTimeChange: (timestamp: number) => void;
}

export const TimeSlider: React.FC<Props> = ({ date, onTimeChange }) => {
  const [sliderValue, setSliderValue] = useState(0); // minutes from start of day

  const TOTAL_MINUTES = 24 * 60;

  useEffect(() => {
    const baseTime = new Date(`${date}T00:00:00Z`).getTime();
    const currentTimestamp = Math.floor(baseTime / 1000) + sliderValue * 60;
    
    const timer = setTimeout(() => {
      onTimeChange(currentTimestamp);
    }, 100);
    
    return () => clearTimeout(timer);
  }, [sliderValue, date, onTimeChange]);

  const formatTime = (minutes: number) => {
    const h = Math.floor(minutes / 60).toString().padStart(2, '0');
    const m = (minutes % 60).toString().padStart(2, '0');
    return `${h}:${m} UTC`;
  };

  return (
    <div className="time-slider-card">
      <div className="time-slider-header">
        <span className="time-limit">00:00</span>
        <span className="time-display">{formatTime(sliderValue)}</span>
        <span className="time-limit">23:59</span>
      </div>
      <input
        type="range"
        min="0"
        max={TOTAL_MINUTES - 1}
        value={sliderValue}
        onChange={(e) => setSliderValue(parseInt(e.target.value))}
        className="slider-input"
      />
    </div>
  );
};
