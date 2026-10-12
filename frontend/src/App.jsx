import { useState, useEffect, useRef, useMemo } from "react"
import axios from "axios"
import { MapContainer, TileLayer, Marker, Popup, Polyline, GeoJSON } from "react-leaflet"
import "leaflet/dist/leaflet.css"
import L from "leaflet"

// same-origin in production (Worker), proxied to localhost:8000 in dev
const API = "/api"
const TEAL = "#1D9E75"
const RED = "#E24B4A"
const AMBER = "#BA7517"
const BLUE = "#2F6FED"
const DARK = "#0f0f0e"
const HEADER_BG = "#ffffff"
const GRAY = "#000"
const SURFACE = "#f8f8f6"

function getConditions() {
  const now = new Date()
  const hour = now.getHours()
  const day = now.getDay()
  const month = now.getMonth() + 1
  const dayType = day === 0 || day === 6 ? "Weekend" : "Weekday"
  const season = [11, 12, 1, 2, 3].includes(month) ? "Cold Season" : "Warm Season"
  let timeWindow = "6pm–12am"
  if (hour < 6) timeWindow = "12am–6am"
  else if (hour < 12) timeWindow = "6am–12pm"
  else if (hour < 18) timeWindow = "12pm–6pm"
  return { dayType, season, timeWindow, label: `${dayType} · ${season} · ${timeWindow}` }
}

function normalizeWeights(raw) {
  const total = Object.values(raw).reduce((a, b) => a + b, 0)
  if (total <= 0) return raw
  return Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, v / total]))
}

// keeps the weight sliders always summing to 1: when one slider moves, the
// change is absorbed by the others, proportional to their current share
function rebalanceWeights(current, changedKey, rawNewValue) {
  const newValue = Math.max(0, Math.min(1, rawNewValue))
  const others = Object.keys(current).filter(k => k !== changedKey)
  const othersSum = others.reduce((sum, k) => sum + current[k], 0)
  const remaining = 1 - newValue
  const next = { ...current, [changedKey]: newValue }
  if (othersSum <= 0) {
    others.forEach(k => { next[k] = remaining / others.length })
  } else {
    others.forEach(k => { next[k] = Math.max(0, (current[k] / othersSum) * remaining) })
  }
  return next
}

function Tag({ label, color }) {
  const colors = {
    green: { bg: "#e8f5ee", text: "#0F6E56", border: "#9FE1CB" },
    red: { bg: "#FCEBEB", text: "#A32D2D", border: "#F5B8B8" },
    amber: { bg: "#FFF8ED", text: "#854F0B", border: "#FADBA0" },
    gray: { bg: "#f0f0ee", text: "#000", border: "#e0e0de" },
  }
  const c = colors[color] || colors.gray
  return (
    <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 3, background: c.bg, color: c.text, border: `0.5px solid ${c.border}`, whiteSpace: "nowrap", letterSpacing: "0.01em" }}>
      {label}
    </span>
  )
}

function StatBlock({ label, value, sub, revenue, color, last }) {
  return (
    <div style={{ borderRight: last ? "none" : "0.5px solid rgba(0,0,0,0.08)", padding: "0 14px", minWidth: 76, flexShrink: 0 }}>
      <div style={{ fontSize: 8, letterSpacing: "0.03em", color: DARK, marginBottom: 2, fontWeight: 500, whiteSpace: "nowrap" }}>{label}</div>
      <div style={{ fontSize: revenue ? 15 : 18, fontWeight: 700, color: color || DARK, lineHeight: 1, marginBottom: 1 }}>{value}</div>
      <div style={{ fontSize: 9, color: DARK, whiteSpace: "nowrap", visibility: sub ? "visible" : "hidden" }}>{sub || " "}</div>
      {revenue && <div style={{ fontSize: 10, fontWeight: 700, color: TEAL, whiteSpace: "nowrap", marginTop: 1 }}>{revenue}</div>}
    </div>
  )
}

function AssignmentCard({ assignment, index, selected, onClick, batteryPct, autonomyMiles }) {
  const isRequired = assignment.is_required || false
  const isProactive = !isRequired
  const isUrgent = isRequired
  const score = assignment.score || 0
  const hours = assignment.hours_sitting || 0
  const battery = batteryPct !== undefined && batteryPct !== null ? batteryPct : assignment.autonomy2_pct
  const [detailsOpen, setDetailsOpen] = useState(false)
  const barColor = score >= 75 ? TEAL : score >= 50 ? AMBER : RED
  const accentColor = isUrgent ? RED : index < 10 ? RED : index < 20 ? AMBER : TEAL
  const demandColor = assignment.demand_label?.includes("High")
    ? RED
    : assignment.demand_label?.includes("Medium")
    ? AMBER
    : GRAY

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
              background: isRequired ? "#FCEBEB" : "#FFF8ED",
              color: isRequired ? "#A32D2D" : "#854F0B",
              border: `0.5px solid ${isRequired ? "#F5B8B8" : "#FADBA0"}`
            }}>
              {isRequired ? "REQUIRED MOVE" : "PROACTIVE MOVE"}
            </span>
          </div>

          <div style={{ fontSize: 12, fontWeight: 500, color: DARK, marginBottom: 6 }}>
            {assignment.origin_neighborhood ? `${assignment.origin_neighborhood} → ` : ""}{assignment.neighborhood}
            {assignment.drop_off_maps_link && (
              <a href={assignment.drop_off_maps_link} target="_blank" rel="noreferrer"
                onClick={e => e.stopPropagation()}
                style={{ color: TEAL, textDecoration: "none", fontSize: 10, marginLeft: 6 }}>
              </a>
            )}
          </div>

          <div style={{ fontSize: 10, color: GRAY, marginBottom: 12, lineHeight: 1.5 }}>
            {assignment.demand_label && (
              <span style={{ fontWeight: 600, color: demandColor }}>{assignment.demand_label}</span>
            )}
            {assignment.drive_time ? ` · ${assignment.drive_time} min drive · ${assignment.drive_distance} mi` : ""}
            {autonomyMiles !== undefined && autonomyMiles !== null ? ` · ${autonomyMiles.toFixed(1)} mi range` : ""}
            {hours > 0 ? ` · ${Math.round(hours)}h sitting` : ""}
          </div>

          {assignment.charger_label && (
            <div style={{ fontSize: 10, color: GRAY, marginBottom: 8, lineHeight: 1.4, display: "flex", alignItems: "flex-start", gap: 4 }}>
              <span>⚡</span>
              <span style={{ fontStyle: "italic" }}>{assignment.charger_label}</span>
            </div>
          )}

          <div
            onClick={e => { e.stopPropagation(); setDetailsOpen(o => !o) }}
            style={{ fontSize: 9, fontWeight: 600, color: TEAL, cursor: "pointer", marginBottom: 8, userSelect: "none", display: "inline-flex", alignItems: "center", gap: 4, letterSpacing: "0.02em" }}
          >
            <span style={{ fontSize: 8 }}>{detailsOpen ? "▾" : "▸"}</span>
            Additional Information
          </div>

          {detailsOpen && (
            <>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginBottom: 8 }}>
                {assignment.is_fulfillment && <Tag label="Fulfillment vehicle" color="green" />}
                {assignment.demand_label?.includes("High") && <Tag label="High demand" color="green" />}
                {hours > 0 && <Tag label={`${Math.round(hours)}h sitting`} color={isRequired ? "red" : "amber"} />}
                {battery !== undefined && battery !== null && (
                  <Tag
                    label={`Battery ${battery}%`}
                    color={battery >= 80 ? "green" : battery >= 40 ? "amber" : "red"}
                  />
                )}
                {assignment.relocation_cost_usd != null && (
                  <Tag label={`$${assignment.relocation_cost_usd.toFixed(2)} to move`} color="green" />
                )}
                {assignment.net_value_usd !== null && assignment.net_value_usd !== undefined && (
                  <Tag
                    label={`${assignment.worth_it ? "+" : ""}$${assignment.net_value_usd.toFixed(2)} net`}
                    color={assignment.worth_it ? "green" : "red"}
                  />
                )}
                {assignment.value_estimated && (
                  <span style={{ fontSize: 9, color: AMBER, marginLeft: 4 }}>*est.</span>
                )}
              </div>

              {assignment.score_breakdown && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 8, fontWeight: 700, color: GRAY, letterSpacing: "0.05em", marginBottom: 4 }}>
                    SCORE BREAKDOWN
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {[
                      ["Demand", assignment.score_breakdown.demand],
                      ["Charger", assignment.score_breakdown.charger],
                      ["Autonomy", assignment.score_breakdown.autonomy],
                      ["Demand intensity (annual)", assignment.score_breakdown.annual_demand],
                      ["Relo cost", assignment.score_breakdown.relocation_cost],
                    ].map(([label, val]) => (
                      val !== null && val !== undefined ? (
                        <span key={label} style={{ fontSize: 9, padding: "2px 6px", borderRadius: 3, background: SURFACE, color: GRAY, border: "0.5px solid #e5e5e2" }}>
                          {label}: {val}
                        </span>
                      ) : null
                    ))}
                  </div>
                </div>
              )}

              <div style={{ height: 2, background: "#eee", borderRadius: 2 }}>
                <div style={{ width: `${Math.min(score, 100)}%`, height: "100%", background: barColor, borderRadius: 2, transition: "width 0.5s ease" }} />
              </div>
            </>
          )}
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
          Current Location
        </a>
        <a href={assignment.drop_off_maps_link} target="_blank" rel="noreferrer"
          onClick={e => e.stopPropagation()}
          style={{ flex: 1, fontSize: 10, textAlign: "center", padding: "5px 0", background: TEAL, borderRadius: 4, border: `0.5px solid ${TEAL}`, color: "#fff", textDecoration: "none", fontWeight: 600 }}>
          Drop-off Location
        </a>
      </div>
    </div>
  )
}

export default function App() {
  const [assignments, setAssignments] = useState([])
  const [generatedAt, setGeneratedAt] = useState(null)
  const [vehicles, setVehicles] = useState([])
  const [chargers, setChargers] = useState([])
  const [serviceArea, setServiceArea] = useState(null)
  const [loading, setLoading] = useState(false)
  const [panelOpen, setPanelOpen] = useState(true)
  const [selectedAssignment, setSelectedAssignment] = useState(null)
  const [route, setRoute] = useState([])
  // fallback mirrors core_functions/config.py defaults, normalized to sum to 1,
  // until /weights/defaults loads, so the sliders never show made-up numbers
  const DEFAULT_WEIGHTS = normalizeWeights({ demand: 0.5, charger: 0.14, tripRate: 0.1, battery: 0.26, relocationCost: 0.25 })
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS)
  const [proactiveOn, setProactiveOn] = useState(true)
  const [proactiveThresholdHours, setProactiveThresholdHours] = useState(16)
  const [tempF, setTempF] = useState(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [infoOpen, setInfoOpen] = useState(false)
  const [neighborhoodFilter, setNeighborhoodFilter] = useState("")
  const [originNeighborhoodFilter, setOriginNeighborhoodFilter] = useState("")
  const [showChargers, setShowChargers] = useState(true)
  const [showAssignedVehicles, setShowAssignedVehicles] = useState(true)
  const [showUnassignedVehicles, setShowUnassignedVehicles] = useState(true)
  const mapRef = useRef(null)
  const conditions = getConditions()

  const urgentAssignments = assignments.filter(a => a.is_required)
  const proactiveAssignments = assignments.filter(a => !a.is_required)
  const assignmentVehicleNames = new Set(assignments.map(a => a.vehicle))
  const neighborhoods = [...new Set(assignments.map(a => a.neighborhood).filter(Boolean))].sort()
  const originNeighborhoods = [...new Set(assignments.map(a => a.origin_neighborhood).filter(Boolean))].sort()
  const matchesNeighborhoodFilters = a =>
    (!neighborhoodFilter || a.neighborhood === neighborhoodFilter) &&
    (!originNeighborhoodFilter || a.origin_neighborhood === originNeighborhoodFilter)
  const filteredUrgentAssignments = urgentAssignments.filter(matchesNeighborhoodFilters)
  const filteredProactiveAssignments = proactiveAssignments.filter(matchesNeighborhoodFilters)
  const requiredNetRevenue = urgentAssignments.reduce(
    (sum, a) => sum + (a.net_value_usd || 0), 0
  )
  const proactiveNetRevenue = proactiveAssignments.reduce(
    (sum, a) => sum + (a.net_value_usd || 0), 0
  )
  const totalNetRevenue = requiredNetRevenue + proactiveNetRevenue
  const totalGrossRevenue = assignments.reduce(
    (sum, a) => sum + (a.net_value_usd || 0) + (a.relocation_cost_usd || 0), 0
  )

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
      const shape = decodePolyline(res.data.trip.legs[0].shape)
      setRoute(shape)
      mapRef.current?.flyToBounds(shape, { padding: [60, 60], duration: 1.5 })
    } catch { setRoute([]) }
  }

  function handleSelect(a) {
    if (selectedAssignment?.vehicle === a.vehicle) {
      setSelectedAssignment(null)
      setRoute([])
    } else {
      setSelectedAssignment(a)
      if (a.current_lat && a.drop_off_lat) {
        mapRef.current?.flyToBounds([[a.current_lat, a.current_lon], [a.drop_off_lat, a.drop_off_lon]], { padding: [60, 60], duration: 1.5 })
      }
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
          w_trip: weights.tripRate,
          w_battery: weights.battery,
          w_relo: weights.relocationCost,
          sitting_hours: proactiveThresholdHours
        }
      })
      setAssignments(res.data.assignments)
      setGeneratedAt(res.data.generated_at)
    } catch (e) { console.error(e) }
    setLoading(false)
  }

  useEffect(() => {
    // deployed: show the last run so the map isn't empty before anyone
    // hits "Run optimizer". Skipped in dev, where there's no cached run behind
    // this and a page load would kick off a full optimizer run.
    if (import.meta.env.PROD) {
      axios.get(`${API}/assignments`).then(r => {
        setAssignments(r.data.assignments || [])
        setGeneratedAt(r.data.generated_at)
      }).catch(() => { })
    }
    axios.get(`${API}/vehicles`).then(r => setVehicles(r.data)).catch(() => { })
    axios.get(`${API}/chargers`).then(r => setChargers(r.data)).catch(() => { })
    axios.get(`${API}/service-area`).then(r => setServiceArea(r.data)).catch(() => { })
    axios.get(`${API}/weather`).then(r => setTempF(r.data.temp_f)).catch(() => { })
    axios.get(`${API}/weights/defaults`).then(r => setWeights(normalizeWeights({
      demand: r.data.demand,
      charger: r.data.charger,
      tripRate: r.data.annual_demand,
      battery: r.data.battery,
      relocationCost: r.data.relo_cost,
    }))).catch(() => { })
  }, [])

  // the map's Leaflet instance caches its pixel size at mount time, so toggling
  // the assignments pane (which resizes this container) leaves it stale unless
  // we force a recalculation after the layout settles
  useEffect(() => {
    const id = setTimeout(() => mapRef.current?.invalidateSize(), 50)
    return () => clearTimeout(id)
  }, [panelOpen])

  function makeVehicleIcon(isAssigned, selected = false) {
    const w = selected ? 26 : 22
    const h = Math.round(w * (409 / 312))
    const src = selected ? "/evie-selected.png" : "/evie.png"
    const filter = isAssigned ? "none" : "grayscale(1) opacity(0.55)"
    return L.divIcon({
      className: "",
      html: `<img src="${src}" style="width:${w}px;height:${h}px;filter:${filter};display:block" />`,
      iconSize: [w, h],
      iconAnchor: [w / 2, h]
    })
  }

  function generateChargerSVG(leftColor, rightColor) {
    const svg = `
      <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22">
        <defs>
          <filter id="shadow" x="-50%" y="-50%" width="200%" height="200%">
            <feDropShadow dx="0" dy="1" stdDeviation="1.2" flood-opacity="0.3"/>
          </filter>
          <clipPath id="half"><circle cx="11" cy="11" r="8"/></clipPath>
        </defs>
        <g clip-path="url(#half)">
          <rect x="0" y="0" width="11" height="22" fill="${leftColor}"/>
          <rect x="11" y="0" width="11" height="22" fill="${rightColor}"/>
        </g>
        <circle cx="11" cy="11" r="8" fill="none" stroke="white" stroke-width="2" filter="url(#shadow)"/>
      </svg>`
    return "data:image/svg+xml;base64," + btoa(svg)
  }

  function makeChargerIcon(c) {
    const colors = c.available >= 2 ? [TEAL, TEAL] : c.available === 1 ? [TEAL, AMBER] : [AMBER, AMBER]
    return L.icon({
      iconUrl: generateChargerSVG(...colors),
      iconSize: [22, 22],
      iconAnchor: [11, 11]
    })
  }

  function generatePinSVG(color, w = 22, h = 30) {
    const svg = `
      <svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 26 36">
        <defs>
          <filter id="shadow" x="-50%" y="-50%" width="200%" height="200%">
            <feDropShadow dx="0" dy="1" stdDeviation="1.2" flood-opacity="0.35"/>
          </filter>
        </defs>
        <path d="M13 0C5.8 0 0 5.8 0 13c0 9.5 13 23 13 23s13-13.5 13-23C26 5.8 20.2 0 13 0z" fill="${color}" filter="url(#shadow)"/>
        <circle cx="13" cy="13" r="5.5" fill="white"/>
      </svg>`
    return "data:image/svg+xml;base64," + btoa(svg)
  }

  function makeDropOffIcon() {
    const w = 20, h = 27
    return L.icon({
      iconUrl: generatePinSVG(RED, w, h),
      iconSize: [w, h],
      iconAnchor: [w / 2, h],
      popupAnchor: [0, -h + 6]
    })
  }

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", minHeight: "100vh", display: "flex", flexDirection: "column", overflow: "hidden", height: "100vh" }}>

      <div style={{ background: "#fbbf24", color: "#1a1a1a", fontSize: 10, fontWeight: 600, padding: "2px 12px", textAlign: "center", letterSpacing: "0.02em", flexShrink: 0 }}>
        PROTOTYPE -- this tool is in active testing. Data and behavior may change without notice.
      </div>

      <div style={{ background: HEADER_BG, borderBottom: "0.5px solid rgba(0,0,0,0.08)", padding: "0 20px", display: "flex", alignItems: "center", gap: 10, height: 64, flexShrink: 0 }}>
        <img src="/evie_logo.webp" alt="evie" style={{ height: 52 }} />
        <span style={{ color: "rgba(0,0,0,0.15)", fontSize: 14 }}>/</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: DARK, letterSpacing: "-0.01em" }}>Free-Float Fleet Relocation Optimizer</span>
        <div style={{ position: "relative" }}>
          <button
            onClick={() => setInfoOpen(o => !o)}
            title="About this tool"
            style={{
              width: 20, height: 20, borderRadius: "50%", border: `1.5px solid ${TEAL}`,
              background: infoOpen ? TEAL : "#fff", color: infoOpen ? "#fff" : TEAL,
              fontSize: 11, fontWeight: 700, fontFamily: "system-ui, sans-serif", padding: 0, cursor: "pointer",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >i</button>
          {infoOpen && (
            <div style={{
              position: "absolute", top: 26, left: 0, width: 300, background: "#fff",
              border: "0.5px solid rgba(0,0,0,0.12)", borderRadius: 6, boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
              padding: "12px 14px", zIndex: 2000, fontSize: 11, color: DARK, lineHeight: 1.5,
            }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                <span style={{ fontWeight: 600 }}>About this tool</span>
                <button
                  onClick={() => setInfoOpen(false)}
                  title="Close"
                  style={{
                    width: 18, height: 18, border: "none", background: "none", color: GRAY,
                    fontSize: 14, lineHeight: "18px", padding: 0, cursor: "pointer",
                  }}
                >×</button>
              </div>
              <p style={{ margin: "0 0 8px" }}>
                The optimizer scores where each vehicle should be relocated using demand, charger
                proximity, trip rate, battery level, and relocation cost, weighted by the sliders
                in Fleet Settings, then assigns vehicles to the highest-scoring spots.
              </p>
              <p style={{ margin: "0 0 8px" }}>
                Each assignment is a recommended move: pick up a vehicle from where it's sitting
                idle and drop it at a spot with better demand or charging access. Fleet can use
                this list to prioritize which vehicles to physically relocate first.
              </p>
              <p style={{ margin: 0 }}>
                Shows the last run. Click "Run Optimizer" for an updated result.
              </p>
            </div>
          )}
        </div>
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
          {generatedAt && (
            <span style={{ fontSize: 10, color: GRAY, letterSpacing: "0.02em" }}>
              run {new Date(generatedAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
          <span style={{ fontSize: 10, color: DARK, letterSpacing: "0.02em" }}>{conditions.label}</span>
          {tempF != null && (
            <span style={{ fontSize: 10, color: DARK, letterSpacing: "0.02em" }}>
              {Math.round(tempF)}°F
            </span>
          )}
        </div>
      </div>

      <div style={{ background: "#fff", borderBottom: "0.5px solid #ebebea", padding: "6px 20px", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
        <button onClick={() => setSidebarOpen(o => !o)} style={{ fontSize: 11, fontWeight: 600, padding: 0, border: "none", background: "none", cursor: "pointer", color: sidebarOpen ? TEAL : GRAY, letterSpacing: "0.02em", display: "flex", alignItems: "center", gap: 4, transition: "color 0.15s" }}>
          {sidebarOpen ? "‹ Hide Fleet Settings" : "Fleet Settings ›"}
        </button>
        <button onClick={() => setPanelOpen(p => !p)} style={{ fontSize: 11, fontWeight: 600, padding: 0, border: "none", background: "none", cursor: "pointer", color: panelOpen ? TEAL : GRAY, letterSpacing: "0.02em", display: "flex", alignItems: "center", gap: 4, transition: "color 0.15s" }}>
          {panelOpen ? "Map View ›" : "‹ View Assignments"}
        </button>
      </div>

      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>

        {sidebarOpen && (
        <div style={{ width: 214, background: "rgba(255,255,255,0.5)", backdropFilter: "blur(6px)", WebkitBackdropFilter: "blur(6px)", borderRight: "0.5px solid #ebebea", padding: "16px 14px", overflowY: "auto", display: "flex", flexDirection: "column", gap: 18, flexShrink: 0 }}>

          <div>
            <div style={{ fontSize: 9, fontWeight: 700, color: "#000", letterSpacing: "0.1em", marginBottom: 8 }}>FLEET CONSTRAINTS</div>
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
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 8, opacity: proactiveOn ? 1 : 0.4 }}>
              <span style={{ fontSize: 11, color: GRAY }}>Sitting threshold (hrs)</span>
              <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
                <button
                  disabled={!proactiveOn}
                  onClick={() => setProactiveThresholdHours(h => Math.max(1, h - 1))}
                  style={{ width: 20, height: 20, fontSize: 12, fontWeight: 700, borderRadius: 4, cursor: proactiveOn ? "pointer" : "not-allowed", background: SURFACE, color: DARK, border: "0.5px solid #e8e8e6", lineHeight: 1, padding: 0 }}
                >
                  −
                </button>
                <input
                  type="number"
                  min={1}
                  max={71}
                  value={proactiveThresholdHours}
                  disabled={!proactiveOn}
                  onChange={e => {
                    const v = parseInt(e.target.value, 10)
                    if (!Number.isNaN(v)) setProactiveThresholdHours(Math.min(71, Math.max(1, v)))
                  }}
                  style={{ width: 34, fontSize: 11, fontWeight: 700, textAlign: "center", borderRadius: 4, border: "0.5px solid #e8e8e6", background: "#fff", color: DARK, padding: "2px 0" }}
                />
                <button
                  disabled={!proactiveOn}
                  onClick={() => setProactiveThresholdHours(h => Math.min(71, h + 1))}
                  style={{ width: 20, height: 20, fontSize: 12, fontWeight: 700, borderRadius: 4, cursor: proactiveOn ? "pointer" : "not-allowed", background: SURFACE, color: DARK, border: "0.5px solid #e8e8e6", lineHeight: 1, padding: 0 }}
                >
                  +
                </button>
              </div>
            </div>
          </div>

          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
              <span style={{ fontSize: 9, fontWeight: 700, color: "#000", letterSpacing: "0.1em" }}>SCORING WEIGHTS</span>
              <span onClick={() => setWeights(DEFAULT_WEIGHTS)} style={{ fontSize: 9, color: TEAL, cursor: "pointer", fontWeight: 600 }}>
                Reset
              </span>
            </div>
            {[
              ["Demand intensity (current)", "demand", false],
              ["Demand intensity (annual)", "tripRate", false],
              ["Charger proximity", "charger", false],
              ["Battery level", "battery", false],
              ["Relocation cost", "relocationCost", true],
            ].map(([label, key, isPenalty]) => (
              <div key={key} style={{ marginBottom: 11 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: GRAY, marginBottom: 4 }}>
                  <span>{label}</span>
                  <span style={{ fontWeight: 700, color: isPenalty ? RED : (weights[key] > DEFAULT_WEIGHTS[key] ? TEAL : DARK) }}>
                    {isPenalty ? "−" : ""}{Math.round(weights[key] * 100)}%
                  </span>
                </div>
                <input type="range" min={0} max={1} value={weights[key]} step={0.01}
                  onChange={e => setWeights(w => rebalanceWeights(w, key, +e.target.value))}
                  style={{ width: "100%", accentColor: isPenalty ? RED : TEAL }} />
              </div>
            ))}
          </div>

          <div>
            <div style={{ fontSize: 9, fontWeight: 700, color: "#000", letterSpacing: "0.1em", marginBottom: 8 }}>SHIFT CONTEXT</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: DARK, padding: "6px 10px", background: SURFACE, borderRadius: 6, border: "0.5px solid #e8e8e6", marginBottom: 4 }}>{conditions.timeWindow}</div>
            <div style={{ fontSize: 10, color: GRAY }}>{conditions.dayType} · {conditions.season}</div>
          </div>
        </div>
        )}

        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>

          <div style={{ background: HEADER_BG, borderBottom: "0.5px solid rgba(0,0,0,0.08)", padding: "5px 20px 5px 0", display: "flex", alignItems: "flex-end", flexShrink: 0, gap: 0, overflowX: "auto" }}>
            <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flex: 1, minWidth: "fit-content" }}>
              <StatBlock label="ASSIGNED" value={assignments.length} sub="vehicles" color={TEAL} />
              <StatBlock label="REQUIRED MOVES" value={urgentAssignments.length} sub="vehicles" color={RED} />
              <StatBlock label="PROACTIVE MOVES" value={proactiveAssignments.length} sub="vehicles" color={AMBER} />
              <StatBlock label="EST. GROSS DAILY REVENUE" value={`$${Math.round(totalGrossRevenue)}`} sub="combined, before net" color={TEAL} />
              <StatBlock label="EST. NET DAILY REVENUE" value={`${totalNetRevenue >= 0 ? "+" : ""}$${Math.round(totalNetRevenue)}`} sub="combined" color={TEAL} />
              <StatBlock label="FLEET" value={vehicles.length} sub="vehicles available" />
              <StatBlock label="CHARGERS" value={chargers.filter(c => c.has_available).length} sub="open now" color={AMBER} last />
            </div>
            {selectedAssignment && (
              <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10, paddingLeft: 20, borderLeft: "0.5px solid rgba(0,0,0,0.08)", flexShrink: 0 }}>
                <div>
                  <div style={{ fontSize: 9, color: DARK, letterSpacing: "0.08em", marginBottom: 2 }}>ROUTE ACTIVE</div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: TEAL }}>{selectedAssignment.vehicle} → {selectedAssignment.neighborhood}</div>
                </div>
                <button onClick={() => { setSelectedAssignment(null); setRoute([]) }} style={{ fontSize: 10, padding: "3px 10px", borderRadius: 4, border: `0.5px solid ${TEAL}`, background: "transparent", color: TEAL, cursor: "pointer" }}>Clear</button>
              </div>
            )}
          </div>

          <div style={{ flex: 1, minWidth: 0, overflow: "hidden", position: "relative" }}>
            <div style={{ position: "absolute", top: 12, left: 12, zIndex: 1000, background: "rgba(255,255,255,0.2)", backdropFilter: "blur(3px)", WebkitBackdropFilter: "blur(3px)", borderRadius: 8, border: "0.5px solid #e5e5e2", boxShadow: "0 2px 10px rgba(0,0,0,0.08)", padding: "8px 14px", display: "flex", alignItems: "center", gap: 16 }}>
              <span style={{ fontSize: 9, fontWeight: 700, color: "#000", letterSpacing: "0.1em", whiteSpace: "nowrap" }}>SHOW ON MAP</span>
              {[
                ["Chargers", showChargers, setShowChargers],
                ["Assigned vehicles", showAssignedVehicles, setShowAssignedVehicles],
                ["Unassigned vehicles", showUnassignedVehicles, setShowUnassignedVehicles],
              ].map(([label, on, setOn]) => (
                <label key={label} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: on ? DARK : GRAY, cursor: "pointer", whiteSpace: "nowrap", transition: "color 0.15s" }}>
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => setOn(o => !o)}
                    style={{ width: 13, height: 13, margin: 0, accentColor: TEAL, cursor: "pointer" }}
                  />
                  {label}
                </label>
              ))}
            </div>
            <div style={{ position: "absolute", bottom: 12, left: 12, zIndex: 1000, background: "rgba(255,255,255,0.2)", backdropFilter: "blur(3px)", WebkitBackdropFilter: "blur(3px)", borderRadius: 8, border: "0.5px solid #e5e5e2", boxShadow: "0 2px 10px rgba(0,0,0,0.08)", padding: "10px 14px", display: "flex", flexWrap: "wrap", gap: "6px 16px", alignItems: "center", maxWidth: "calc(100% - 24px)" }}>
              <span style={{ fontSize: 9, fontWeight: 700, color: "#000", letterSpacing: "0.1em" }}>MAP KEY</span>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, color: GRAY }}>
                <img src="/evie.png" style={{ width: 14, height: 18, flexShrink: 0 }} />
                Vehicle is assigned
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, color: GRAY }}>
                <img src="/evie.png" style={{ width: 14, height: 18, filter: "grayscale(1) opacity(0.55)", flexShrink: 0 }} />
                Vehicle is not assigned
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, color: GRAY }}>
                <img src={generatePinSVG(RED, 11, 15)} style={{ width: 11, height: 15, flexShrink: 0 }} />
                Drop-off pod
              </div>
              {[[[TEAL, TEAL], "2+ open"], [[TEAL, AMBER], "1 open"], [[AMBER, AMBER], "none"]].map(([colors, label]) => (
                <div key={label} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, color: GRAY }}>
                  <img src={generateChargerSVG(...colors)} style={{ width: 12, height: 12, flexShrink: 0 }} />
                  Charger(s) = {label}
                </div>
              ))}
              <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, color: GRAY }}>
                <div style={{ width: 14, height: 3, borderRadius: 2, background: TEAL, opacity: 0.5, flexShrink: 0 }} />
                Service area
              </div>
            </div>
            <MapContainer ref={mapRef} center={[44.97, -93.27]} zoom={12} style={{ width: "100%", height: "100%" }} zoomControl={false}>
              <TileLayer
                url="https://api.mapbox.com/styles/v1/mapbox/streets-v12/tiles/{z}/{x}/{y}{r}?access_token=REDACTED_MAPBOX_TOKEN"
                tileSize={512}
                zoomOffset={-1}
                attribution='© <a href="https://www.mapbox.com/about/maps/">Mapbox</a> © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              />

              {serviceArea && serviceArea.features && serviceArea.features.length > 0 && (
                <GeoJSON
                  data={serviceArea}
                  style={{ color: TEAL, weight: 2, opacity: 0.5, fillColor: TEAL, fillOpacity: 0.08 }}
                />
              )}

              {route.length > 0 && (
                <Polyline positions={route} color={BLUE} weight={5} opacity={0.85} />
              )}

              {showChargers && !selectedAssignment && chargers.map((c, i) => c.lat && c.lon ? (
                <Marker key={`c-${i}`} position={[c.lat, c.lon]} icon={makeChargerIcon(c)}>
                  <Popup><strong>{c.name}</strong><br />Available: {c.available} / {c.total_plugs}</Popup>
                </Marker>
              ) : null)}

              {vehicles
                .filter(v => selectedAssignment
                  ? v.name === selectedAssignment.vehicle
                  : (assignmentVehicleNames.has(v.name) ? showAssignedVehicles : showUnassignedVehicles))
                .map((v, i) => {
                  const isAssigned = assignmentVehicleNames.has(v.name)
                  const isSelected = selectedAssignment?.vehicle === v.name
                  return v.lat && v.lon ? (
                    <Marker
                      key={`v-${i}`}
                      position={[v.lat, v.lon]}
                      icon={makeVehicleIcon(isAssigned, isSelected)}
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
          <div style={{ width: 340, background: "rgba(255,255,255,0.5)", backdropFilter: "blur(6px)", WebkitBackdropFilter: "blur(6px)", borderLeft: "0.5px solid #ebebea", display: "flex", flexDirection: "column", flexShrink: 0, overflow: "hidden" }}>

            <div style={{ padding: "10px 16px", background: "#fff", borderBottom: "0.5px solid #ebebea", fontSize: 9, fontWeight: 700, color: "#000", letterSpacing: "0.1em", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span>ASSIGNMENT OUTPUT · {assignments.length}</span>
              {loading && <span style={{ color: TEAL, fontWeight: 600, fontSize: 9, letterSpacing: "0.06em" }}>RUNNING...</span>}
            </div>

            <div style={{ padding: "8px 16px", background: "#fff", borderBottom: "0.5px solid #ebebea", flexShrink: 0, display: "flex", flexDirection: "column", gap: 6 }}>
              <select
                value={originNeighborhoodFilter}
                onChange={e => setOriginNeighborhoodFilter(e.target.value)}
                style={{ width: "100%", fontSize: 11, padding: "5px 8px", borderRadius: 4, border: "0.5px solid #e5e5e2", background: SURFACE, color: DARK, cursor: "pointer" }}
              >
                <option value="">All origin neighborhoods</option>
                {originNeighborhoods.map(n => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
              <select
                value={neighborhoodFilter}
                onChange={e => setNeighborhoodFilter(e.target.value)}
                style={{ width: "100%", fontSize: 11, padding: "5px 8px", borderRadius: 4, border: "0.5px solid #e5e5e2", background: SURFACE, color: DARK, cursor: "pointer" }}
              >
                <option value="">All destination neighborhoods</option>
                {neighborhoods.map(n => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </div>

            <div style={{ overflowY: "auto", flex: 1 }}>
              {!loading && assignments.length === 0 && (
                <div style={{ textAlign: "center", padding: "60px 20px" }}>
                  <div style={{ fontSize: 32, marginBottom: 12 }}>🚗</div>
                  <div style={{ fontSize: 12, fontWeight: 500, color: "#000", marginBottom: 4 }}>No assignments yet</div>
                  <div style={{ fontSize: 11, color: "#000" }}>Set weights and run the optimizer</div>
                </div>
              )}

              {!loading && assignments.length > 0 && (neighborhoodFilter || originNeighborhoodFilter) && filteredUrgentAssignments.length === 0 && filteredProactiveAssignments.length === 0 && (
                <div style={{ textAlign: "center", padding: "60px 20px" }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: "#000", marginBottom: 4 }}>
                    No assignments{originNeighborhoodFilter ? ` from ${originNeighborhoodFilter}` : ""}{neighborhoodFilter ? ` to ${neighborhoodFilter}` : ""}
                  </div>
                  <div style={{ fontSize: 11, color: "#000" }}>Try a different neighborhood</div>
                </div>
              )}

              {filteredUrgentAssignments.length > 0 && (
                <>
                  <div style={{ padding: "8px 16px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, background: "#fff8f8", borderBottom: "0.5px solid #fce8e8", borderTop: "0.5px solid #fce8e8" }}>
                    <span style={{ fontSize: 8, fontWeight: 700, color: "#A32D2D", letterSpacing: "0.1em", whiteSpace: "nowrap" }}>
                      ⚠ REQUIRED RELOCATIONS · {filteredUrgentAssignments.length}
                    </span>
                  </div>
                  {filteredUrgentAssignments.map((a, i) => (
                    <AssignmentCard
                      key={i}
                      assignment={a}
                      index={i}
                      selected={selectedAssignment?.vehicle === a.vehicle}
                      onClick={() => handleSelect(a)}
                      batteryPct={vehicleByName[a.vehicle]?.autonomy2_pct}
                      autonomyMiles={vehicleByName[a.vehicle]?.autonomy_miles}
                    />
                  ))}
                </>
              )}

              {filteredProactiveAssignments.length > 0 && (
                <>
                  <div style={{ padding: "8px 16px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, background: "#fffaf0", borderBottom: "0.5px solid #fadba0", borderTop: "0.5px solid #fadba0" }}>
                    <span style={{ fontSize: 8, fontWeight: 700, color: "#854F0B", letterSpacing: "0.1em", whiteSpace: "nowrap" }}>
                      ⏱ PROACTIVE MOVES · {filteredProactiveAssignments.length}
                    </span>
                  </div>
                  {filteredProactiveAssignments.map((a, i) => (
                    <AssignmentCard
                      key={i}
                      assignment={a}
                      index={i}
                      selected={selectedAssignment?.vehicle === a.vehicle}
                      onClick={() => handleSelect(a)}
                      batteryPct={vehicleByName[a.vehicle]?.autonomy2_pct}
                      autonomyMiles={vehicleByName[a.vehicle]?.autonomy_miles}
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