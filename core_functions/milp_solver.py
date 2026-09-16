"""Solves the vehicle-to-block assignment as a MILP (via pulp), maximizing
total score subject to: one relocation per vehicle, a capacity cap per
neighborhood by demand tier, and charger plug capacity for vehicles that
need charging.
"""

import pulp
from core_functions.config import MIN_AUTONOMY_LEVEL
from apis.open_street_map import print_api_call_summary


def solve_milp(
    scores,
    evies,
    blocks,
    block_id_to_neighborhood,
    neighborhood_capacities,
    chargers,
    neighborhood_occupancy=None,
    vehicle_current_neighborhood=None,
):
    neighborhood_occupancy = neighborhood_occupancy or {}
    vehicle_current_neighborhood = vehicle_current_neighborhood or {}
    prob = pulp.LpProblem("Fleet_Relocation", pulp.LpMaximize)
    x = {
        (v, b): pulp.LpVariable(f"x_{v}_{b}", cat="Binary") for (v, b) in scores.keys()
    }
    prob += pulp.lpSum(scores[(v, b)] * x[(v, b)] for (v, b) in x)

    # each vehicle assigned at most once
    vehicle_names = {v["name"] for v in evies}
    for v in vehicle_names:
        prob += pulp.lpSum(x[(v, b)] for (vv, b) in x if vv == v) <= 1

    # neighborhood-level cap by demand tier
    blocks_by_neighborhood = {}
    for b in blocks:
        nbhd = block_id_to_neighborhood.get(b["block_id"])
        if nbhd:
            blocks_by_neighborhood.setdefault(nbhd, []).append(b["block_id"])
    # net occupancy change per neighborhood = arrivals from elsewhere minus
    # departures to elsewhere. A reassignment within the same neighborhood
    # nets to zero, so a vehicle leaving a full neighborhood correctly frees
    # the slot a vehicle arriving in the same solve needs.
    net_change = {nbhd: 0 for nbhd in blocks_by_neighborhood}
    for v, b in x:
        origin_nbhd = vehicle_current_neighborhood.get(v)
        dest_nbhd = block_id_to_neighborhood.get(b)
        if dest_nbhd is None or dest_nbhd == origin_nbhd:
            continue
        net_change[dest_nbhd] = net_change.get(dest_nbhd, 0) + x[(v, b)]
        if origin_nbhd in net_change:
            net_change[origin_nbhd] = net_change[origin_nbhd] - x[(v, b)]

    total_cap = 0
    total_available = 0
    full_neighborhoods = []
    for nbhd in blocks_by_neighborhood:
        cap = neighborhood_capacities.get(nbhd, 1)
        occupied = neighborhood_occupancy.get(nbhd, 0)
        # floor headroom at 0: an already-over-capacity neighborhood (real fleet
        # clustering routinely exceeds the tier cap) must not demand net outflow
        # the run's candidates can't supply -- that makes the whole MILP infeasible.
        # Flooring just freezes further net inflow instead of forcing an exodus.
        available = max(cap - occupied, 0)
        prob += net_change[nbhd] <= available
        total_cap += cap
        total_available += available
        if available == 0:
            full_neighborhoods.append(nbhd)
    print(
        f"Neighborhood capacity: {total_cap} slots total, {total_available} open after "
        f"current occupancy ({len(full_neighborhoods)} neighborhood(s) full: {', '.join(full_neighborhoods) or 'none'})"
    )

    # charger plug capacity, only for vehicles that actually need charging
    charger_capacity = {}
    for c in chargers:
        nearest_block = min(
            blocks,
            key=lambda b: (b["lat"] - c["lat"]) ** 2 + (b["lon"] - c["lon"]) ** 2,
        )
        block_id = nearest_block["block_id"]
        charger_capacity[block_id] = charger_capacity.get(block_id, 0) + c["available"]

    needs_charging = {
        v["name"]
        for v in evies
        if (v.get("autonomy", 0) or 0) * 0.621371 < MIN_AUTONOMY_LEVEL
    }

    # ensure every block gets a plug-capacity constraint, even ones with 0 available plugs
    all_block_ids = {b["block_id"] for b in blocks}
    for b_id in all_block_ids:
        plug_count = charger_capacity.get(b_id, 0)
        prob += (
            pulp.lpSum(
                x[(v, bb)] for (v, bb) in x if bb == b_id and v in needs_charging
            )
            <= plug_count
        )

    prob.solve()
    assignments = [(v, b, scores[(v, b)]) for (v, b) in x if x[(v, b)].varValue == 1]
    print(f"\nStatus: {pulp.LpStatus[prob.status]}")
    print(f"Vehicles relocated: {len(assignments)}")
    print_api_call_summary()
    return assignments
