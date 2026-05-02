import { useState } from "react";

const NAV_ITEMS = ["Backtest", "Algo Trade", "Signals", "Marketplace", "ClickTrade", "Webinars"];
const STRIKE_OPTIONS = [
  ...Array.from({length:20},(_,i)=>`ITM${20-i}`),
  "ATM",
  ...Array.from({length:30},(_,i)=>`OTM${i+1}`)
];
const STRIKE_CRITERIA = [
  "Strike Type","Premium Range","Closest Premium","Premium >=","Premium <=",
  "Straddle Width","% of ATM","Synthetic Future","ATM Straddle Premium %",
  "Closest Delta","Delta Range"
];
const EXPIRY_OPTS = ["Weekly","Next Weekly","Monthly","Next Monthly"];
const TRAILING_OPTS = ["Lock","Lock and Trail","Overall Trail SL"];
const OVERALL_SL_OPTS = ["Max Loss","Total Premium %"];
const OVERALL_TGT_OPTS = ["Max Profit","Total Premium %"];
const REENTRY_OPTS = ["RE ASAP","RE ASAP ↩","RE MOMENTUM","RE MOMENTUM ↩"];
const TABS = [
  {label:"Weekly & Monthly Expiries", sub:"NIFTY | SENSEX"},
  {label:"Monthly Only Expiry", sub:"MIDCPNIFTY | BANKNIFTY | FINNIFTY | BANKEX"},
  {label:"Stocks - Cash / F&O", sub:"ALL NIFTY 500 STOCKS"},
  {label:"Delta Exchange", sub:"BTCUSD | ETHUSD", badge:true},
];

const Toggle = ({ label, id }) => {
  const [on, setOn] = useState(false);
  return (
    <label className="flex items-center gap-2 text-xs cursor-pointer select-none">
      <span>{label}</span>
      <button
        onClick={() => setOn(v=>!v)}
        style={{
          width:36, height:20, borderRadius:10,
          background: on ? "#3b82f6" : "#9ca3af",
          position:"relative", border:"none", cursor:"pointer", transition:"background 0.2s"
        }}
      >
        <span style={{
          display:"block", width:14, height:14, borderRadius:7,
          background:"white", position:"absolute", top:3,
          left: on ? 19 : 3, transition:"left 0.2s"
        }}/>
      </button>
    </label>
  );
};

const RadioGroup = ({ options, value, onChange, name }) => (
  <div style={{display:"flex"}}>
    {options.map((opt, i) => (
      <label key={opt} style={{
        padding:"4px 10px", fontSize:11, cursor:"pointer",
        border:"1px solid #3b82f6",
        borderRadius: i===0?"4px 0 0 4px":i===options.length-1?"0 4px 4px 0":"0",
        borderLeft: i>0?"none":"1px solid #3b82f6",
        background: value===opt?"#3b82f6":"white",
        color: value===opt?"white":"#374151",
        transition:"all 0.15s"
      }}>
        <input type="radio" hidden name={name} value={opt} checked={value===opt} onChange={()=>onChange(opt)} />
        {opt}
      </label>
    ))}
  </div>
);

const Select = ({ options, style }) => (
  <div style={{position:"relative", display:"inline-block"}}>
    <select style={{
      appearance:"none", border:"1px solid #d1d5db", borderRadius:4,
      padding:"4px 28px 4px 8px", fontSize:11, cursor:"pointer",
      background:"white", color:"#374151", ...style
    }}>
      {options.map(o=><option key={o}>{o}</option>)}
    </select>
    <svg style={{position:"absolute",right:6,top:"50%",transform:"translateY(-50%)",pointerEvents:"none",width:12,height:12}} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M19 9l-7 7-7-7"/></svg>
  </div>
);

const SectionCard = ({ title, children, style }) => (
  <div style={{display:"flex",flexDirection:"column",gap:8,...style}}>
    {title && <h6 style={{fontSize:15,fontWeight:500,margin:0,color:"#111827"}}>{title}</h6>}
    <div style={{border:"1px solid #e5e7eb",borderRadius:8,background:"white",padding:20}}>
      {children}
    </div>
  </div>
);

const FieldRow = ({ label, children }) => (
  <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",gap:16,flexWrap:"wrap"}}>
    <label style={{fontSize:11,color:"#374151"}}>{label}</label>
    {children}
  </div>
);

export default function AlgotestBacktest() {
  const [activeTab, setActiveTab] = useState(0);
  const [index, setIndex] = useState("NIFTY");
  const [underlying, setUnderlying] = useState("Cash");
  const [strategyType, setStrategyType] = useState("Intraday");
  const [position, setPosition] = useState("Sell");
  const [optionType, setOptionType] = useState("Call");
  const [segment, setSegment] = useState("Options");
  const [squareOff, setSquareOff] = useState("Partial");
  const [strikeCriteria, setStrikeCriteria] = useState("Strike Type");
  const [strikeType, setStrikeType] = useState("ATM");
  const [expiry, setExpiry] = useState("Weekly");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [totalLot, setTotalLot] = useState(1);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // Navbar
  const Navbar = () => (
    <nav style={{
      position:"sticky",top:0,zIndex:30,height:48,
      background:"#1a1a2e",display:"flex",alignItems:"center",
      justifyContent:"space-between",padding:"0 16px",
      boxShadow:"0 2px 8px rgba(0,0,0,0.3)"
    }}>
      <a href="#" style={{display:"flex",alignItems:"center",gap:8,color:"white",textDecoration:"none"}}>
        <svg width="24" height="24" viewBox="0 0 24 25" fill="none">
          <circle cx="12" cy="12.97" r="12" fill="#2E5FDF"/>
          <path d="M12 0.97C5.37 0.97 0 6.35 0 12.97c0 1.55.29 3.03.83 4.39l7.48-6L13.61 17.36l5.19-5.09 2.9-2.95.7-2.35C20.5 3.37 16.39.97 12 .97z" fill="#5EC7DE"/>
          <path fillRule="evenodd" clipRule="evenodd" d="M22.9 8.62L13.74 18.01l-5.47-6.29-7.12 6.01-.45-.53 7.64-6.45 5.38 6.18L22.36 8.19l.54.43z" fill="white"/>
        </svg>
        <span style={{fontWeight:600,fontSize:15,letterSpacing:.5}}>AlgoTest</span>
      </a>
      <div style={{display:"flex",gap:4}}>
        {NAV_ITEMS.map(item => (
          <button key={item} style={{
            color:"#e5e7eb",background:"none",border:"none",
            padding:"6px 10px",fontSize:13,cursor:"pointer",borderRadius:4,
            fontFamily:"inherit"
          }} onMouseEnter={e=>e.target.style.background="rgba(255,255,255,0.1)"}
             onMouseLeave={e=>e.target.style.background="none"}>
            {item} ▾
          </button>
        ))}
      </div>
      <div style={{display:"flex",alignItems:"center",gap:12}}>
        <span style={{color:"#d1d5db",fontSize:13}}>Pricing</span>
        <span style={{color:"#d1d5db",fontSize:13}}>Broker Setup</span>
        <div style={{
          display:"flex",alignItems:"center",gap:8,
          background:"rgba(255,255,255,0.1)",borderRadius:20,
          padding:"2px 12px 2px 2px",cursor:"pointer"
        }}>
          <div style={{
            width:28,height:28,borderRadius:14,background:"#3b82f6",
            display:"flex",alignItems:"center",justifyContent:"center",
            color:"white",fontSize:11,fontWeight:600
          }}>Ab</div>
          <span style={{color:"#e5e7eb",fontSize:11}}>Credits: 0</span>
        </div>
      </div>
    </nav>
  );

  // Sidebar
  const Sidebar = () => (
    <div style={{
      width: sidebarOpen ? 192 : 0, overflow:"hidden",
      background:"#f3f4f6", borderRight:"1px solid #e5e7eb",
      transition:"width 0.3s", flexShrink:0, position:"relative"
    }}>
      <button onClick={()=>setSidebarOpen(v=>!v)} style={{
        position:"absolute",right:-16,top:32,zIndex:10,
        background:"white",border:"1px solid #e5e7eb",borderRadius:8,
        padding:4,cursor:"pointer",width:24,height:24,
        display:"flex",alignItems:"center",justifyContent:"center"
      }}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18.75 19.5l-7.5-7.5 7.5-7.5m-6 15L5.25 12l7.5-7.5"/>
        </svg>
      </button>
      <div style={{padding:"16px 0",minWidth:192}}>
        <div style={{padding:"8px 12px",borderBottom:"1px solid #e5e7eb"}}>
          <h6 style={{fontSize:12,fontWeight:600,margin:0,color:"#374151"}}>Saved Strategies</h6>
        </div>
        {["920Straddle","Signals (920 Simple)","Builder","Indicator"].map(name => (
          <button key={name} style={{
            width:"100%",textAlign:"left",padding:"8px 12px",
            display:"flex",alignItems:"center",gap:8,fontSize:12,
            background:"none",border:"none",borderBottom:"1px solid #e5e7eb",
            cursor:"pointer",color:"#374151",fontFamily:"inherit"
          }}>
            <span style={{
              width:16,height:16,border:"1px solid #d1d5db",borderRadius:3,
              display:"flex",alignItems:"center",justifyContent:"center",
              background:"#f3f4f6",flexShrink:0
            }}>
              <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M4.5 15.75l7.5-7.5 7.5 7.5"/>
              </svg>
            </span>
            {name}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <div style={{fontFamily:"system-ui,sans-serif",background:"#f9fafb",minHeight:"100vh",color:"#111827"}}>
      <Navbar />
      <div style={{display:"flex",height:"calc(100vh - 48px)"}}>
        <Sidebar />
        <div style={{flex:1,overflowY:"auto",paddingBottom:80}}>
          {/* Page header */}
          <div style={{
            position:"sticky",top:0,zIndex:11,background:"white",
            borderBottom:"1px solid #e5e7eb"
          }}>
            <div style={{padding:"12px 20px",display:"flex",alignItems:"center",justifyContent:"space-between",flexWrap:"wrap",gap:12}}>
              <div style={{display:"flex",alignItems:"center",gap:12}}>
                <h1 style={{fontSize:22,fontWeight:500,margin:0}}>Backtest</h1>
                <button style={{
                  border:"1px solid #d1d5db",borderRadius:4,padding:"3px 10px",
                  fontSize:11,cursor:"pointer",display:"flex",alignItems:"center",gap:4,
                  background:"white",color:"#374151"
                }}>
                  ↓ <span>Import .algtst</span>
                </button>
                <button style={{
                  border:"1px solid #d1d5db",borderRadius:4,padding:"3px 10px",
                  fontSize:11,cursor:"pointer",display:"flex",alignItems:"center",gap:4,
                  background:"white",color:"#3b82f6"
                }}>
                  ↑ <span>Export .algtst</span>
                </button>
                <button style={{
                  border:"1px solid #d1d5db",borderRadius:4,padding:"3px 10px",
                  fontSize:11,cursor:"pointer",display:"flex",alignItems:"center",gap:4,
                  background:"white",color:"#3b82f6"
                }}>
                  ↓ PDF
                </button>
              </div>
              <div style={{display:"flex",gap:20,fontSize:12}}>
                <div>
                  <div style={{color:"#6b7280",marginBottom:2}}>Credits Available</div>
                  <div style={{fontWeight:700,fontSize:14}}>0 <button style={{color:"#3b82f6",background:"none",border:"none",fontSize:11,cursor:"pointer",fontFamily:"inherit"}}>Add</button></div>
                </div>
                <div>
                  <div style={{color:"#6b7280",marginBottom:2}}>Backtests Remaining</div>
                  <div style={{fontWeight:500,fontSize:14}}>25 <button style={{color:"#3b82f6",background:"none",border:"none",fontSize:11,cursor:"pointer",fontFamily:"inherit"}}>Buy Backtests</button></div>
                </div>
              </div>
            </div>

            {/* Tabs */}
            <div style={{display:"flex",borderTop:"1px solid #e5e7eb"}}>
              {TABS.map((tab,i)=>(
                <button key={i} onClick={()=>setActiveTab(i)} style={{
                  flex:1,padding:"8px 16px",border:"none",
                  borderBottom: activeTab===i ? "2px solid #3b82f6" : "2px solid transparent",
                  background: activeTab===i ? "#eff6ff" : "white",
                  color: activeTab===i ? "#3b82f6" : "#6b7280",
                  cursor:"pointer",textAlign:"left",fontFamily:"inherit",
                  display:"flex",alignItems:"center",justifyContent:"space-between"
                }}>
                  <div>
                    <div style={{fontSize:13,fontWeight:activeTab===i?600:400}}>{tab.label}</div>
                    <div style={{fontSize:10,color:"#9ca3af",marginTop:2}}>{tab.sub}</div>
                  </div>
                  {tab.badge && (
                    <div style={{
                      background:"#f0fdf4",borderRadius:4,padding:"2px 6px",
                      fontSize:10,color:"#10b981",fontWeight:600
                    }}>New</div>
                  )}
                </button>
              ))}
            </div>
          </div>

          {/* Main content */}
          <div style={{padding:"20px"}}>
            <div style={{display:"grid",gridTemplateColumns:"5fr 6fr",gap:20}}>
              {/* Instrument settings */}
              <SectionCard title="Instrument settings">
                <div style={{display:"flex",flexDirection:"column",gap:16}}>
                  <FieldRow label="Index">
                    <Select options={["NIFTY","SENSEX","BANKNIFTY","MIDCPNIFTY","FINNIFTY"]} />
                  </FieldRow>
                  <FieldRow label="Underlying from">
                    <RadioGroup options={["Cash","Futures"]} value={underlying} onChange={setUnderlying} name="underlying" />
                  </FieldRow>
                </div>
              </SectionCard>

              {/* Entry settings */}
              <SectionCard title="Entry settings" style={{gridRow:"span 2"}}>
                <div style={{display:"flex",flexDirection:"column",gap:20}}>
                  <FieldRow label="Strategy Type">
                    <RadioGroup options={["Intraday","BTST","Positional"]} value={strategyType} onChange={setStrategyType} name="stratType" />
                  </FieldRow>

                  <div style={{display:"flex",gap:40,flexWrap:"wrap"}}>
                    <div style={{display:"flex",flexDirection:"column",gap:12}}>
                      <div style={{display:"flex",alignItems:"center",gap:8}}>
                        <label style={{fontSize:11,color:"#374151"}}>Entry Time</label>
                        <input type="time" min="09:16" max="15:30" style={{fontSize:11,border:"1px solid #d1d5db",borderRadius:4,padding:"3px 8px"}} />
                      </div>
                    </div>
                    <div style={{display:"flex",flexDirection:"column",gap:12}}>
                      <div style={{display:"flex",alignItems:"center",gap:8}}>
                        <label style={{fontSize:11,color:"#374151"}}>Exit Time</label>
                        <input type="time" min="09:16" max="15:30" style={{fontSize:11,border:"1px solid #d1d5db",borderRadius:4,padding:"3px 8px"}} />
                      </div>
                    </div>
                  </div>

                  <div style={{display:"flex",gap:20,flexWrap:"wrap",alignItems:"flex-start"}}>
                    <Toggle label="No re-entry after" />
                    <Toggle label="Overall Momentum" />
                  </div>
                </div>
              </SectionCard>

              {/* Legwise settings */}
              <SectionCard title="Legwise settings">
                <div style={{display:"flex",flexDirection:"column",gap:16}}>
                  <FieldRow label="Square Off">
                    <RadioGroup options={["Partial","Complete"]} value={squareOff} onChange={setSquareOff} name="squareOff" />
                  </FieldRow>
                  <label style={{display:"flex",alignItems:"center",gap:6,fontSize:11,cursor:"pointer"}}>
                    <input type="checkbox" />
                    Trail SL to Break-even price
                  </label>
                </div>
              </SectionCard>
            </div>

            {/* Leg Builder */}
            <div style={{marginTop:20,display:"flex",flexDirection:"column",gap:8}}>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                <h6 style={{fontSize:15,fontWeight:500,margin:0}}>Leg Builder</h6>
                <span style={{fontSize:14,fontWeight:600,color:"#3b82f6",cursor:"pointer"}}>Collapse</span>
              </div>
              <div style={{border:"1px solid #e5e7eb",borderRadius:8,background:"white",padding:20}}>
                <div style={{display:"flex",flexWrap:"wrap",gap:20,alignItems:"flex-start"}}>
                  <div style={{display:"flex",flexDirection:"column",gap:4}}>
                    <label style={{fontSize:11,color:"#374151"}}>Select segments</label>
                    <RadioGroup options={["Futures","Options"]} value={segment} onChange={setSegment} name="segment" />
                  </div>
                  <div style={{display:"flex",flexDirection:"column",gap:4}}>
                    <label style={{fontSize:11,color:"#374151"}}>Total Lot</label>
                    <input type="number" min="1" value={totalLot} onChange={e=>setTotalLot(e.target.value)}
                      style={{width:60,fontSize:11,border:"1px solid #d1d5db",borderRadius:4,padding:"4px 8px"}} />
                  </div>
                  <div style={{display:"flex",flexDirection:"column",gap:4}}>
                    <label style={{fontSize:11,color:"#374151"}}>Position</label>
                    <RadioGroup options={["Buy","Sell"]} value={position} onChange={setPosition} name="position" />
                  </div>
                  <div style={{display:"flex",flexDirection:"column",gap:4}}>
                    <label style={{fontSize:11,color:"#374151"}}>Option Type</label>
                    <RadioGroup options={["Call","Put"]} value={optionType} onChange={setOptionType} name="optionType" />
                  </div>
                  <div style={{display:"flex",flexDirection:"column",gap:4}}>
                    <label style={{fontSize:11,color:"#374151"}}>Expiry</label>
                    <Select options={EXPIRY_OPTS} />
                  </div>
                  <div style={{display:"flex",flexDirection:"column",gap:4}}>
                    <label style={{fontSize:11,color:"#374151"}}>Strike Criteria</label>
                    <Select options={STRIKE_CRITERIA} />
                  </div>
                  <div style={{display:"flex",flexDirection:"column",gap:4}}>
                    <label style={{fontSize:11,color:"#374151"}}>Strike Type</label>
                    <Select options={STRIKE_OPTIONS} />
                  </div>
                </div>
                <div style={{marginTop:20,display:"flex",justifyContent:"center"}}>
                  <button style={{
                    background:"#3b82f6",color:"white",border:"none",
                    borderRadius:4,padding:"7px 20px",fontSize:12,
                    cursor:"pointer",fontFamily:"inherit",fontWeight:500
                  }}>Add Leg</button>
                </div>
              </div>
            </div>

            {/* Overall strategy settings */}
            <div style={{marginTop:20,display:"flex",flexDirection:"column",gap:8}}>
              <h6 style={{fontSize:15,fontWeight:500,margin:0}}>Overall strategy settings</h6>
              <div style={{display:"flex",gap:16,flexWrap:"wrap"}}>
                {/* Overall Stop Loss */}
                <div style={{border:"1px solid #e5e7eb",borderRadius:8,background:"white",padding:20,minWidth:220,flex:1}}>
                  <Toggle label="Overall Stop Loss" />
                  <div style={{marginTop:12,opacity:0.4,pointerEvents:"none",display:"flex",flexDirection:"column",gap:8}}>
                    <Select options={OVERALL_SL_OPTS} style={{background:"#3b82f6",color:"white",border:"1px solid #3b82f6"}} />
                    <input type="number" min="1" style={{width:80,fontSize:11,border:"1px solid #d1d5db",borderRadius:4,padding:"4px 8px"}} />
                  </div>
                  <div style={{marginTop:12}}>
                    <Toggle label="Overall Re-entry on SL" />
                  </div>
                </div>

                {/* Overall Target */}
                <div style={{border:"1px solid #e5e7eb",borderRadius:8,background:"white",padding:20,minWidth:220,flex:1}}>
                  <Toggle label="Overall Target" />
                  <div style={{marginTop:12,opacity:0.4,pointerEvents:"none",display:"flex",flexDirection:"column",gap:8}}>
                    <Select options={OVERALL_TGT_OPTS} style={{background:"#3b82f6",color:"white",border:"1px solid #3b82f6"}} />
                    <input type="number" min="1" style={{width:80,fontSize:11,border:"1px solid #d1d5db",borderRadius:4,padding:"4px 8px"}} />
                  </div>
                  <div style={{marginTop:12}}>
                    <Toggle label="Overall Re-entry on Tgt" />
                  </div>
                </div>

                {/* Trailing Options */}
                <div style={{border:"1px solid #e5e7eb",borderRadius:8,background:"white",padding:20,flex:2,minWidth:280}}>
                  <Toggle label="Trailing Options" />
                  <div style={{marginTop:12,opacity:0.4,pointerEvents:"none",display:"flex",flexDirection:"column",gap:8}}>
                    <Select options={TRAILING_OPTS} style={{background:"#3b82f6",color:"white",border:"1px solid #3b82f6"}} />
                  </div>
                  <div style={{marginTop:16,display:"grid",gridTemplateColumns:"1fr 1fr 1fr 1fr",gap:8,alignItems:"center",opacity:0.8,pointerEvents:"none"}}>
                    <label style={{fontSize:11,fontWeight:500}}>If profit reaches</label>
                    <input type="number" style={{width:"100%",fontSize:11,border:"1px solid #d1d5db",borderRadius:4,padding:"4px 8px"}} />
                    <label style={{fontSize:11,fontWeight:500,textAlign:"center"}}>Lock profit</label>
                    <input type="number" style={{width:"100%",fontSize:11,border:"1px solid #d1d5db",borderRadius:4,padding:"4px 8px"}} />
                  </div>
                </div>
              </div>
            </div>

            {/* Date range */}
            <div style={{marginTop:20}}>
              <div style={{
                border:"1px solid #e5e7eb",borderRadius:8,background:"white",
                padding:"16px 20px",display:"flex",alignItems:"center",
                justifyContent:"space-between",flexWrap:"wrap",gap:16
              }}>
                <span style={{fontSize:14,color:"#374151"}}>Enter the duration of your backtest</span>
                <div style={{display:"flex",gap:20,alignItems:"center",flexWrap:"wrap"}}>
                  <div style={{display:"flex",alignItems:"center",gap:8}}>
                    <label style={{fontSize:11,fontWeight:500}}>Start Date</label>
                    <input type="date" value={startDate} onChange={e=>setStartDate(e.target.value)}
                      style={{fontSize:11,border:"1px solid #bfdbfe",borderRadius:4,padding:"3px 8px"}} />
                  </div>
                  <div style={{display:"flex",alignItems:"center",gap:8}}>
                    <label style={{fontSize:11,fontWeight:500}}>End Date</label>
                    <input type="date" value={endDate} onChange={e=>setEndDate(e.target.value)}
                      style={{fontSize:11,border:"1px solid #bfdbfe",borderRadius:4,padding:"3px 8px"}} />
                  </div>
                </div>
              </div>
            </div>

            {/* Info bar */}
            <div style={{
              marginTop:12,background:"#fffbeb",borderRadius:6,
              padding:"8px 16px",display:"flex",alignItems:"center",
              justifyContent:"flex-end",gap:6
            }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" strokeWidth="1.5">
                <path d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z"/>
              </svg>
              <span style={{fontSize:12,fontWeight:600,color:"#f59e0b"}}>Latest Backtest data is available for 29-Apr-26</span>
            </div>
          </div>
        </div>
      </div>

      {/* Sticky footer */}
      <div style={{
        position:"fixed",bottom:0,left:0,right:0,zIndex:20,
        background:"white",borderTop:"1px solid #e5e7eb",
        padding:"10px 36px",display:"flex",justifyContent:"flex-end",gap:12
      }}>
        <button style={{
          border:"1px solid #d1d5db",background:"white",color:"#374151",
          borderRadius:4,padding:"6px 16px",fontSize:12,cursor:"pointer",
          display:"flex",alignItems:"center",gap:6,fontFamily:"inherit"
        }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M15.2 3a2 2 0 011.4.6l3.8 3.8a2 2 0 01.6 1.4V19a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2z"/>
            <path d="M17 21v-7a1 1 0 00-1-1H8a1 1 0 00-1 1v7M7 3v4a1 1 0 001 1h7"/>
          </svg>
          Save Strategy
        </button>
        <button style={{
          background:"#22c55e",border:"1px solid #16a34a",color:"white",
          borderRadius:4,padding:"6px 20px",fontSize:12,cursor:"pointer",
          display:"flex",alignItems:"center",gap:6,fontFamily:"inherit",fontWeight:500
        }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
            <path fillRule="evenodd" d="M4.5 5.653c0-1.426 1.529-2.33 2.779-1.643l11.54 6.348c1.295.712 1.295 2.573 0 3.285L7.28 19.991c-1.25.687-2.779-.217-2.779-1.643V5.653z" clipRule="evenodd"/>
          </svg>
          Start Backtest
          <span style={{fontSize:9,opacity:0.8}}>(Shift+enter)</span>
        </button>
      </div>
    </div>
  );
}
