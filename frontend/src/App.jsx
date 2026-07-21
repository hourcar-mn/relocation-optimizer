import { useState, useEffect, useRef, useMemo } from "react"
import axios from "axios"
import { MapContainer, TileLayer, Marker, Popup, Polyline, GeoJSON } from "react-leaflet"
import "leaflet/dist/leaflet.css"
import L from "leaflet"

const API = "http://localhost:8000"
const TEAL = "#1D9E75"
const RED = "#E24B4A"
const AMBER = "#BA7517"
const DARK = "#0f0f0e"
const GRAY = "#888"
const SURFACE = "#f8f8f6"

function getConditions() {
  const now = new Date()
  const hour = now.getHours()
  const day = now.getDay()
  const month = now.getMonth() + 1
  const dayType = day === 0 || day === 6 ? "Weekend" : "Weekday"
  const season = [11, 12, 1, 2, 3].includes(month) ? "Cold Season" : "Warm Season"
  let timeWindow = "7pm–12am"
  if (hour < 6) timeWindow = "12am–6am"
  else if (hour < 10) timeWindow = "6am–10am"
  else if (hour < 15) timeWindow = "10am–3pm"
  else if (hour < 19) timeWindow = "3pm–7pm"
  return { dayType, season, timeWindow, label: `${dayType} · ${season} · ${timeWindow}` }
}

function Tag({ label, color }) {
  const colors = {
    green: { bg: "#e8f5ee", text: "#0F6E56", border: "#9FE1CB" },
    red: { bg: "#FCEBEB", text: "#A32D2D", border: "#F5B8B8" },
    amber: { bg: "#FFF8ED", text: "#854F0B", border: "#FADBA0" },
    gray: { bg: "#f0f0ee", text: "#555", border: "#e0e0de" },
  }
  const c = colors[color] || colors.gray
  return (
    <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 3, background: c.bg, color: c.text, border: `0.5px solid ${c.border}`, whiteSpace: "nowrap", letterSpacing: "0.01em" }}>
      {label}
    </span>
  )
}

function StatBlock({ label, value, sub, color, last }) {
  return (
    <div style={{ borderRight: last ? "none" : "0.5px solid rgba(255,255,255,0.08)", padding: "0 20px", minWidth: 90 }}>
      <div style={{ fontSize: 9, letterSpacing: "0.1em", color: "rgba(255,255,255,0.4)", marginBottom: 3, fontWeight: 500 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color: color || "#fff", lineHeight: 1, marginBottom: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: "rgba(255,255,255,0.35)" }}>{sub}</div>}
    </div>
  )
}

function AssignmentCard({ assignment, index, selected, onClick, batteryPct }) {
  const isUrgent = assignment.is_urgent || assignment.is_hard || false
  const score = assignment.score || 0
  const hours = assignment.hours_sitting || 0
  const battery = batteryPct !== undefined && batteryPct !== null ? batteryPct : assignment.autonomy2_pct
  const barColor = score >= 75 ? TEAL : score >= 50 ? AMBER : RED
  const accentColor = isUrgent ? RED : index < 10 ? RED : index < 20 ? AMBER : TEAL

  return (
    <div
      onClick={onClick}
      style={{
        borderLeft: `3px solid ${accentColor}`,
        borderBottom: "0.5px solid #ebebea",
        padding: "14px 16px",
        cursor: "pointer",
        background: selected ? "#f0fdf8" : "#fff",
        transition: "background 0.15s, transform 0.1s",
        transform: selected ? "translateX(2px)" : "none",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ flex: 1, marginRight: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 5 }}>
            <span style={{ fontWeight: 600, fontSize: 13, color: DARK, letterSpacing: "-0.01em" }}>{assignment.vehicle}</span>
            <span style={{
              fontSize: 9, padding: "2px 7px", borderRadius: 3, fontWeight: 600, letterSpacing: "0.04em",
              background: isUrgent ? "#FCEBEB" : "#EEF7FF",
              color: isUrgent ? "#A32D2D" : "#185FA5",
              border: `0.5px solid ${isUrgent ? "#F5B8B8" : "#B8D8F5"}`
            }}>
            </span>
          </div>

          <div style={{ fontSize: 12, fontWeight: 500, color: DARK, marginBottom: 2 }}>
            → {assignment.neighborhood}
            {assignment.drop_off_maps_link && (
              <a href={assignment.drop_off_maps_link} target="_blank" rel="noreferrer"
                onClick={e => e.stopPropagation()}
                style={{ color: TEAL, textDecoration: "none", fontSize: 10, marginLeft: 6 }}>
              </a>
            )}
          </div>

          <div style={{ fontSize: 10, color: GRAY, marginBottom: 8, lineHeight: 1.5 }}>
            {assignment.demand_label}
            {assignment.drive_time ? ` · ${assignment.drive_time} min drive · ${assignment.drive_distance} mi` : ""}
          </div>

          <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 8 }}>
            {assignment.demand_label?.includes("High") && <Tag label="High demand" color="green" />}
            {hours >= 72 && <Tag label={`${Math.round(hours)}h sitting`} color="red" />}
            {hours > 0 && hours < 72 && <Tag label={`${Math.round(hours)}h sitting`} color="gray" />}
            {battery !== undefined && battery !== null && (
              <Tag
                label={`Battery ${battery}%`}
                color={battery >= 80 ? "green" : battery >= 40 ? "amber" : "red"}
              />
            )}
          </div>

          {assignment.charger_label && (
            <div style={{ fontSize: 10, color: GRAY, marginBottom: 10, fontStyle: "italic", lineHeight: 1.4 }}>
              {assignment.charger_label}
            </div>
          )}

          {assignment.score_breakdown && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 10 }}>
              {[
                ["Demand", assignment.score_breakdown.demand],
                ["Charger", assignment.score_breakdown.charger],
                ["Autonomy", assignment.score_breakdown.autonomy],
                ["Trip rate", assignment.score_breakdown.trip_rate],
                ["Relo cost", assignment.score_breakdown.relocation_cost],
              ].map(([label, val]) => (
                val !== null && val !== undefined ? (
                  <span key={label} style={{ fontSize: 9, padding: "2px 6px", borderRadius: 3, background: SURFACE, color: GRAY, border: "0.5px solid #e5e5e2" }}>
                    {label}: {val}
                  </span>
                ) : null
              ))}
            </div>
          )}

          <div style={{ height: 2, background: "#eee", borderRadius: 2 }}>
            <div style={{ width: `${Math.min(score, 100)}%`, height: "100%", background: barColor, borderRadius: 2, transition: "width 0.5s ease" }} />
          </div>
        </div>

        <div style={{ textAlign: "right", flexShrink: 0, paddingTop: 2 }}>
          <div style={{ fontSize: 30, fontWeight: 800, color: barColor, lineHeight: 1, letterSpacing: "-0.02em" }}>{Math.round(score)}</div>
          <div style={{ fontSize: 9, color: GRAY, letterSpacing: "0.04em" }}>/100</div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 5, marginTop: 10 }}>
        <a href={assignment.current_maps_link} target="_blank" rel="noreferrer"
          onClick={e => e.stopPropagation()}
          style={{ flex: 1, fontSize: 10, textAlign: "center", padding: "5px 0", background: SURFACE, borderRadius: 4, border: "0.5px solid #e5e5e2", color: DARK, textDecoration: "none", fontWeight: 500 }}>
          Current
        </a>
        <a href={assignment.drop_off_maps_link} target="_blank" rel="noreferrer"
          onClick={e => e.stopPropagation()}
          style={{ flex: 1, fontSize: 10, textAlign: "center", padding: "5px 0", background: TEAL, borderRadius: 4, border: `0.5px solid ${TEAL}`, color: "#fff", textDecoration: "none", fontWeight: 600 }}>
          Drop-off
        </a>
      </div>
    </div>
  )
}

export default function App() {
  const [assignments, setAssignments] = useState([])
  const [vehicles, setVehicles] = useState([])
  const [chargers, setChargers] = useState([])
  const [serviceArea, setServiceArea] = useState(null)
  const [loading, setLoading] = useState(false)
  const [panelOpen, setPanelOpen] = useState(true)
  const [selectedAssignment, setSelectedAssignment] = useState(null)
  const [route, setRoute] = useState([])
  const [weights, setWeights] = useState({ demand: 6, charger: 5, micro: 3, tripRate: 3, battery: 2, sitting: 4 })
  const [proactiveOn, setProactiveOn] = useState(true)
  const [activeEvents, setActiveEvents] = useState([])
  const conditions = getConditions()

  const urgentAssignments = assignments.filter(a => a.is_urgent || a.is_hard)
  const proactiveAssignments = assignments.filter(a => !a.is_urgent && !a.is_hard)
  const assignmentVehicleNames = new Set(assignments.map(a => a.vehicle))

  const vehicleByName = useMemo(
    () => Object.fromEntries(vehicles.map(v => [v.name, v])),
    [vehicles]
  )

  function decodePolyline(encoded) {
    let index = 0, lat = 0, lng = 0
    const result = []
    while (index < encoded.length) {
      let b, shift = 0, r = 0
      do { b = encoded.charCodeAt(index++) - 63; r |= (b & 0x1f) << shift; shift += 5 } while (b >= 0x20)
      lat += r & 1 ? ~(r >> 1) : r >> 1; shift = 0; r = 0
      do { b = encoded.charCodeAt(index++) - 63; r |= (b & 0x1f) << shift; shift += 5 } while (b >= 0x20)
      lng += r & 1 ? ~(r >> 1) : r >> 1
      result.push([lat / 1e6, lng / 1e6])
    }
    return result
  }

  async function fetchRoute(a) {
    if (!a.current_lat || !a.drop_off_lat) return
    try {
      const res = await axios.post("https://valhalla1.openstreetmap.de/route", {
        locations: [
          { lat: a.current_lat, lon: a.current_lon },
          { lat: a.drop_off_lat, lon: a.drop_off_lon }
        ],
        costing: "auto",
        directions_options: { units: "miles" }
      }, { headers: { "User-Agent": "HourCar-Evie-Optimizer/1.0" } })
      setRoute(decodePolyline(res.data.trip.legs[0].shape))
    } catch { setRoute([]) }
  }

  function handleSelect(a) {
    if (selectedAssignment?.vehicle === a.vehicle) {
      setSelectedAssignment(null)
      setRoute([])
    } else {
      setSelectedAssignment(a)
      fetchRoute(a)
    }
  }

  async function runOptimizer() {
    setLoading(true)
    setSelectedAssignment(null)
    setRoute([])
    try {
      const res = await axios.get(`${API}/assignments`, {
        params: {
          w_demand: weights.demand,
          w_charger: weights.charger,
          w_micro: weights.micro,
          w_trip: weights.tripRate,
          w_battery: weights.battery,
          w_sitting: weights.sitting
        }
      })
      setAssignments(res.data)
    } catch (e) { console.error(e) }
    setLoading(false)
  }

  useEffect(() => {
    axios.get(`${API}/vehicles`).then(r => setVehicles(r.data)).catch(() => { })
    axios.get(`${API}/chargers`).then(r => setChargers(r.data)).catch(() => { })
    axios.get(`${API}/service-area`).then(r => setServiceArea(r.data)).catch(() => { })
  }, [])

  function makeCircleIcon(color, size = 10, selected = false) {
    const s = selected ? size + 5 : size
    return L.divIcon({
      className: "",
      html: `<div style="width:${s}px;height:${s}px;border-radius:50%;background:${color};border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,0.3)"></div>`,
      iconSize: [s, s],
      iconAnchor: [s / 2, s / 2]
    })
  }

  function makeChargerIcon(c) {
    const color = c.available >= 2 ? "#1D9E75" : c.available === 1 ? "#BA7517" : "#E24B4A"
    return L.divIcon({
      className: "",
      html: `<div style="display:flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;background:${color};border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,0.25)"><svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg></div>`,
      iconSize: [16, 16],
      iconAnchor: [8, 8]
    })
  }

  function makeDropOffIcon() {
    return L.divIcon({
      className: "",
      html: `<div style="width:16px;height:16px;border-radius:50%;background:${TEAL};border:3px solid white;box-shadow:0 2px 8px rgba(29,158,117,0.5)"></div>`,
      iconSize: [16, 16],
      iconAnchor: [8, 8]
    })
  }

  const EVENTS = ["Vikings", "Twins", "Concert", "Timberwolves", "Race"]

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", minHeight: "100vh", display: "flex", flexDirection: "column", overflow: "hidden", height: "100vh" }}>

      <div style={{ background: "#fbbf24", color: "#1a1a1a", fontSize: 11, fontWeight: 600, padding: "5px 12px", textAlign: "center", letterSpacing: "0.02em", flexShrink: 0 }}>
        PROTOTYPE — this tool is in active testing. Data and behavior may change without notice.
      </div>

      <div style={{ background: DARK, borderBottom: "0.5px solid rgba(255,255,255,0.08)", padding: "0 20px", display: "flex", alignItems: "center", gap: 10, height: 44, flexShrink: 0 }}>
        <span style={{ fontSize: 20, color: "#35579a", letterSpacing: "0.1em", fontWeight: 600 }}>evie</span>
        <span style={{ color: "rgba(255,255,255,0.15)", fontSize: 14 }}>/</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: "#fff", letterSpacing: "-0.01em" }}>Free-Float Fleet Relocation Optimizer</span>
        <span style={{ fontSize: 11, color: "rgba(255,255,255,0.3)", marginLeft: 2 }}></span>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 16 }}>
          <button
            onClick={runOptimizer}
            disabled={loading}
            style={{
              padding: "6px 14px",
              fontSize: 11,
              fontWeight: 600,
              background: loading ? "rgba(29,158,117,0.15)" : TEAL,
              color: loading ? TEAL : "#fff",
              border: `1px solid ${TEAL}`,
              borderRadius: 5,
              cursor: loading ? "not-allowed" : "pointer",
              letterSpacing: "0.03em",
              transition: "opacity 0.15s, transform 0.1s",
              opacity: loading ? 0.8 : 1,
              whiteSpace: "nowrap",
            }}
            onMouseDown={e => { if (!loading) e.currentTarget.style.transform = "scale(0.96)" }}
            onMouseUp={e => { if (!loading) e.currentTarget.style.transform = "scale(1)" }}
            onMouseLeave={e => { e.currentTarget.style.transform = "scale(1)" }}
          >
            {loading ? "Running…" : "Run Optimizer"}
          </button>
          <span style={{ fontSize: 10, color: "rgba(255,255,255,0.35)", letterSpacing: "0.02em" }}>{conditions.label}</span>
          <button onClick={() => setPanelOpen(p => !p)} style={{ fontSize: 10, padding: "4px 12px", borderRadius: 4, border: "0.5px solid rgba(255,255,255,0.15)", background: "rgba(255,255,255,0.06)", cursor: "pointer", color: "rgba(255,255,255,0.5)", letterSpacing: "0.02em" }}>
            {panelOpen ? "Map View " : "View Assignments"}
          </button>
        </div>
      </div>

      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>

        <div style={{ width: 214, background: "#fff", borderRight: "0.5px solid #ebebea", padding: "16px 14px", overflowY: "auto", display: "flex", flexDirection: "column", gap: 18, flexShrink: 0 }}>

          <div>
            <div style={{ fontSize: 9, fontWeight: 700, color: "#bbb", letterSpacing: "0.1em", marginBottom: 8 }}>SHIFT CONTEXT</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: DARK, padding: "6px 10px", background: SURFACE, borderRadius: 6, border: "0.5px solid #e8e8e6", marginBottom: 4 }}>{conditions.timeWindow}</div>
            <div style={{ fontSize: 10, color: GRAY }}>{conditions.dayType} · {conditions.season}</div>
          </div>

          <div>
            <div style={{ fontSize: 9, fontWeight: 700, color: "#bbb", letterSpacing: "0.1em", marginBottom: 8 }}>ACTIVE EVENTS</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              {EVENTS.map(e => {
                const active = activeEvents.includes(e)
                return (
                  <span key={e} onClick={() => setActiveEvents(prev => active ? prev.filter(x => x !== e) : [...prev, e])}
                    style={{ fontSize: 10, padding: "3px 9px", borderRadius: 4, cursor: "pointer", fontWeight: active ? 600 : 400, background: active ? "#E1F5EE" : SURFACE, color: active ? "#0F6E56" : GRAY, border: `0.5px solid ${active ? "#9FE1CB" : "#e8e8e6"}`, transition: "all 0.15s" }}>
                    {e}
                  </span>
                )
              })}
            </div>
          </div>

          <div>
            <div style={{ fontSize: 9, fontWeight: 700, color: "#bbb", letterSpacing: "0.1em", marginBottom: 8 }}>FLEET CONSTRAINTS</div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span style={{ fontSize: 11, color: GRAY }}>Proactive moves</span>
              <div style={{ display: "flex", gap: 3 }}>
                {["On", "Off"].map(opt => (
                  <button key={opt} onClick={() => setProactiveOn(opt === "On")} style={{ fontSize: 10, padding: "3px 9px", borderRadius: 4, cursor: "pointer", fontWeight: (opt === "On") === proactiveOn ? 600 : 400, background: (opt === "On") === proactiveOn ? "#E1F5EE" : SURFACE, color: (opt === "On") === proactiveOn ? "#0F6E56" : GRAY, border: `0.5px solid ${(opt === "On") === proactiveOn ? "#9FE1CB" : "#e8e8e6"}`, transition: "all 0.15s" }}>
                    {opt}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div>
            <div style={{ fontSize: 9, fontWeight: 700, color: "#bbb", letterSpacing: "0.1em", marginBottom: 10 }}>SCORING WEIGHTS</div>
            {[
              ["Demand intensity", "demand"],
              ["Charger proximity", "charger"],
              ["Micro-location", "micro"],
              ["Trip rate", "tripRate"],
              ["Battery health", "battery"],
            ].map(([label, key]) => (
              <div key={key} style={{ marginBottom: 11 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: GRAY, marginBottom: 4 }}>
                  <span>{label}</span>
                  <span style={{ fontWeight: 700, color: weights[key] > 5 ? TEAL : DARK }}>{weights[key]}</span>
                </div>
                <input type="range" min={0} max={10} value={weights[key]} step={1}
                  onChange={e => setWeights(w => ({ ...w, [key]: +e.target.value }))}
                  style={{ width: "100%", accentColor: TEAL }} />
              </div>
            ))}
          </div>

          <div>
            <div style={{ fontSize: 9, fontWeight: 700, color: "#bbb", letterSpacing: "0.1em", marginBottom: 10 }}>MAP KEY</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 10, color: GRAY }}>
              <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <div style={{ width: 10, height: 10, borderRadius: "50%", background: "#378ADD", border: "2px solid white", boxShadow: "0 1px 3px rgba(0,0,0,0.2)", flexShrink: 0 }} />
                Vehicle is assigned
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <div style={{ width: 10, height: 10, borderRadius: "50%", background: "#ccc", border: "2px solid white", flexShrink: 0 }} />
                Vehicle is unassigned
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <div style={{ width: 12, height: 12, borderRadius: "50%", background: TEAL, border: "2px solid white", boxShadow: "0 2px 6px rgba(29,158,117,0.4)", flexShrink: 0 }} />
                Drop-off pod
              </div>
              {[["#1D9E75", "2+ open"], ["#BA7517", "1 open"], ["#E24B4A", "none"]].map(([color, label]) => (
                <div key={label} style={{ display: "flex", alignItems: "center", gap: 7 }}>
                  <div style={{ width: 14, height: 14, borderRadius: "50%", background: color, border: "2px solid white", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                    <svg width="7" height="7" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
                    </svg>
                  </div>
                  Charger(s) = {label}
                </div>
              ))}
              <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <div style={{ width: 14, height: 3, borderRadius: 2, background: TEAL, opacity: 0.5, flexShrink: 0 }} />
                Service area
              </div>
            </div>
          </div>
        </div>

        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>

          <div style={{ background: DARK, borderBottom: "0.5px solid rgba(255,255,255,0.08)", padding: "10px 20px 10px 0", display: "flex", alignItems: "flex-end", flexShrink: 0, gap: 0 }}>
            <StatBlock label="ASSIGNED" value={assignments.length} sub="vehicles" color={TEAL} />
            <StatBlock label="HARD MOVES" value={urgentAssignments.length} sub="72h+ sitting" color={RED} />
            <StatBlock label="PROACTIVE" value={proactiveAssignments.length} sub="early relocation" color="#5DCAA5" />
            <StatBlock label="FLEET" value={vehicles.length} sub="available" />
            <StatBlock label="CHARGERS" value={chargers.filter(c => c.has_available).length} sub="open now" color={AMBER} last />
            {selectedAssignment && (
              <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10, paddingLeft: 20, borderLeft: "0.5px solid rgba(255,255,255,0.08)" }}>
                <div>
                  <div style={{ fontSize: 9, color: "rgba(255,255,255,0.4)", letterSpacing: "0.08em", marginBottom: 2 }}>ROUTE ACTIVE</div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: TEAL }}>{selectedAssignment.vehicle} → {selectedAssignment.neighborhood}</div>
                </div>
                <button onClick={() => { setSelectedAssignment(null); setRoute([]) }} style={{ fontSize: 10, padding: "3px 10px", borderRadius: 4, border: `0.5px solid ${TEAL}`, background: "transparent", color: TEAL, cursor: "pointer" }}>Clear</button>
              </div>
            )}
          </div>

          <div style={{ flex: 1, overflow: "hidden" }}>
            <MapContainer center={[44.97, -93.27]} zoom={12} style={{ width: "100%", height: "100%" }} zoomControl={false}>
              <TileLayer
                url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
                attribution='© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/attributions">CARTO</a>'
              />

              {serviceArea && serviceArea.features && serviceArea.features.length > 0 && (
                <GeoJSON
                  data={serviceArea}
                  style={{ color: TEAL, weight: 2, opacity: 0.5, fillColor: TEAL, fillOpacity: 0.03 }}
                />
              )}

              {route.length > 0 && (
                <Polyline positions={route} color={TEAL} weight={5} opacity={0.85} />
              )}

              {chargers.map((c, i) => c.lat && c.lon ? (
                <Marker key={`c-${i}`} position={[c.lat, c.lon]} icon={makeChargerIcon(c)}>
                  <Popup><strong>{c.name}</strong><br />Available: {c.available} / {c.total_plugs}</Popup>
                </Marker>
              ) : null)}

              {vehicles
                .filter(v => !selectedAssignment || v.name === selectedAssignment.vehicle)
                .map((v, i) => {
                  const isAssigned = assignmentVehicleNames.has(v.name)
                  const isSelected = selectedAssignment?.vehicle === v.name
                  const color = isAssigned ? "#378ADD" : "#ccc"
                  return v.lat && v.lon ? (
                    <Marker
                      key={`v-${i}`}
                      position={[v.lat, v.lon]}
                      icon={makeCircleIcon(color, 10, isSelected)}
                      eventHandlers={{ click: () => { const a = assignments.find(a => a.vehicle === v.name); if (a) handleSelect(a) } }}
                    >
                      <Popup>
                        <strong>{v.name}</strong><br />
                        {v.autonomy_miles?.toFixed(1)} mi · {v.tier}<br />
                        {isAssigned ? "✓ Assigned" : "Not assigned"}
                      </Popup>
                    </Marker>
                  ) : null
                })}

              {selectedAssignment?.drop_off_lat && (
                <Marker position={[selectedAssignment.drop_off_lat, selectedAssignment.drop_off_lon]} icon={makeDropOffIcon()}>
                  <Popup>
                    <strong>Drop-off · {selectedAssignment.neighborhood}</strong><br />
                    {selectedAssignment.nearby}
                  </Popup>
                </Marker>
              )}
            </MapContainer>
          </div>
        </div>

        {panelOpen && (
          <div style={{ width: 340, background: "#fff", borderLeft: "0.5px solid #ebebea", display: "flex", flexDirection: "column", flexShrink: 0, overflow: "hidden" }}>

            <div style={{ padding: "10px 16px", background: "#fff", borderBottom: "0.5px solid #ebebea", fontSize: 9, fontWeight: 700, color: "#bbb", letterSpacing: "0.1em", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span>ASSIGNMENT OUTPUT · {assignments.length}</span>
              {loading && <span style={{ color: TEAL, fontWeight: 600, fontSize: 9, letterSpacing: "0.06em" }}>RUNNING...</span>}
            </div>

            <div style={{ overflowY: "auto", flex: 1 }}>
              {!loading && assignments.length === 0 && (
                <div style={{ textAlign: "center", padding: "60px 20px" }}>
                  <div style={{ fontSize: 32, marginBottom: 12 }}>🚗</div>
                  <div style={{ fontSize: 12, fontWeight: 500, color: "#aaa", marginBottom: 4 }}>No assignments yet</div>
                  <div style={{ fontSize: 11, color: "#ccc" }}>Set weights and run the optimizer</div>
                </div>
              )}

              {urgentAssignments.length > 0 && (
                <>
                  <div style={{ padding: "8px 16px", fontSize: 9, fontWeight: 700, color: "#A32D2D", letterSpacing: "0.1em", background: "#fff8f8", borderBottom: "0.5px solid #fce8e8", borderTop: "0.5px solid #fce8e8" }}>
                    ⚠ HARD RELOCATIONS · {urgentAssignments.length}
                  </div>
                  {urgentAssignments.map((a, i) => (
                    <AssignmentCard
                      key={i}
                      assignment={a}
                      index={i}
                      selected={selectedAssignment?.vehicle === a.vehicle}
                      onClick={() => handleSelect(a)}
                      batteryPct={vehicleByName[a.vehicle]?.autonomy2_pct}
                    />
                  ))}
                </>
              )}

              {proactiveAssignments.length > 0 && (
                <>
                  <div style={{ padding: "8px 16px", fontSize: 9, fontWeight: 700, color: "#0F6E56", letterSpacing: "0.1em", background: "#f8fdfb", borderBottom: "0.5px solid #e1f5ee", borderTop: "0.5px solid #e1f5ee" }}>
                    ↗ PROACTIVE MOVES · {proactiveAssignments.length}
                  </div>
                  {proactiveAssignments.map((a, i) => (
                    <AssignmentCard
                      key={i}
                      assignment={a}
                      index={i}
                      selected={selectedAssignment?.vehicle === a.vehicle}
                      onClick={() => handleSelect(a)}
                      batteryPct={vehicleByName[a.vehicle]?.autonomy2_pct}
                    />
                  ))}
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}