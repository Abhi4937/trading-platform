import React, { useState, useEffect } from 'react';

interface Props {
  date: string;
  onTimeChange: (timestamp: number) => void;
}

export const TimeSlider: React.FC<Props> = ({ date, onTimeChange }) => {
  const [sliderValue, setSliderValue] = useState(0); // minutes from start of day

  // Total minutes in a day
  const TOTAL_MINUTES = 24 * 60;

  useEffect(() => {
    // When slider changes, convert to unix timestamp and notify parent
    // Base time is start of day UTC
    const baseTime = new Date(`${date}T00:00:00Z`).getTime();
    const currentTimestamp = Math.floor(baseTime / 1000) + sliderValue * 60;
    
    // Add debounce to prevent too many API calls
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
    <div className="flex flex-col gap-2 w-full max-w-xl mx-auto my-4 p-4 bg-[#080e16] border border-[#1a2d42] rounded-lg">
      <div className="flex justify-between items-center text-sm font-mono text-[#00d4ff]">
        <span>00:00</span>
        <span className="text-xl font-bold text-[#e2eaf4]">{formatTime(sliderValue)}</span>
        <span>23:59</span>
      </div>
      <input
        type="range"
        min="0"
        max={TOTAL_MINUTES - 1}
        value={sliderValue}
        onChange={(e) => setSliderValue(parseInt(e.target.value))}
        className="w-full h-2 bg-[#1a2d42] rounded-lg appearance-none cursor-pointer accent-[#00d4ff]"
      />
    </div>
  );
};
