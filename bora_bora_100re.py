"""
bora_bora_100re.py
===================
"Hold 100% RE" block sizing for the Bora Bora planner. Sits ON TOP of the existing
bora_bora_engine / bora_bora_data modules, which are imported unchanged.

Problem statement (project manager's request)
---------------------------------------------
  1. Size the PV + BESS built in the OTEC year (default 2032) so RE = 100% in that year.
  2. Size it so 100% RE is also held for the next N years (default 5 -> 2032..2037), i.e. the
     tranche is over-built up front to absorb PV/BESS degradation and demand changes.
  3. At the next block start (2038) add a new tranche that holds 100% for the next N years
     (2038..2043), and so on until the horizon end (2044..2049, then 2050).

How a block is sized
--------------------
For every candidate PV addition (MWp) the smallest BESS addition (MWh) that keeps RE >= target in
EVERY year of the block is found by bisection on the BESS grid (more BESS never hurts a fixed-PV
design, so feasibility is monotonic in BESS). The lowest-NPC (PV, BESS) pair on that "feasible
frontier" wins. Blocks are solved in order, each given the tranches already locked in
(greedy block by block, not a joint optimisation over all blocks).

Each candidate is simulated year by year with the battery state of charge chained from year to
year (exactly what the final trajectory does), so the RE% reported for a block in the sizing
table equals the RE% in the final trajectory.

Nothing here changes the dispatch or cost logic; it only calls simulate_year() and tranche_npv().
"""

from dataclasses import dataclass, field

import bora_bora_engine as eng
from bora_bora_engine import Tranche, TrancheSchedule, RE_TOL_PP


# ----------------------------------------------------------------------------
# Block plan
# ----------------------------------------------------------------------------

def block_plan(first_year, hold_years, end_year, merge_short_tail=False):
    """Returns [(build_year, last_year_held), ...].

    hold_years = number of years held AFTER the build year, so 2032 with hold 5 covers 2032..2037
    and the next block starts 2038. The last block is cut at end_year (default plan for 2032 / 5 /
    2050: 2032-37, 2038-43, 2044-49, 2050-50). With merge_short_tail the trailing short block is
    folded into the previous one (2044-50) instead of being its own build."""
    span = int(hold_years) + 1
    blocks = []
    y = int(first_year)
    while y <= end_year:
        blocks.append([y, min(y + span - 1, end_year)])
        y += span
    if merge_short_tail and len(blocks) > 1 and (blocks[-1][1] - blocks[-1][0] + 1) < span:
        blocks[-2][1] = blocks[-1][1]
        blocks.pop()
    return [tuple(b) for b in blocks]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _carry_soc(schedule, start_year, up_to_year_exclusive, sim_kwargs):
    """BESS charge the locked-in fleet carries into `up_to_year_exclusive` (None if no year before it)."""
    soc = None
    for y in range(start_year, up_to_year_exclusive):
        r = eng.simulate_year(y, schedule, initial_soc_mwh=soc, **sim_kwargs)
        soc = r["ending_soc_mwh"]
    return soc


def _with_additions(schedule, year, pv_add, bess_add, pv_degr, bess_degr, c_rate, lifetimes):
    t = schedule.clone()
    if pv_add > 0:
        t.pv.append(Tranche("pv", year, pv_degr, capacity_mw=pv_add, lifetime_years=lifetimes.get("pv")))
    if bess_add > 0:
        t.bess.append(Tranche("bess", year, bess_degr, power_mw=bess_add * c_rate, energy_mwh=bess_add,
                              lifetime_years=lifetimes.get("bess")))
    return t


def run_window(schedule, soc_in, y0, y1, required_pct, sim_kwargs, fail_fast=True):
    """Simulates y0..y1 with the BESS charge chained year to year. Returns a dict with ok, the lowest RE%
    in the window and where it occurs, and curtailment statistics. With fail_fast the run stops at the first
    year below target (the figures are then partial and only `ok` should be used)."""
    soc = soc_in
    min_re, min_year = 1e9, y0
    curt_mwh = gen_mwh = 0.0
    per_year = []
    ok = True
    for y in range(y0, y1 + 1):
        r = eng.simulate_year(y, schedule, initial_soc_mwh=soc, **sim_kwargs)
        soc = r["ending_soc_mwh"]
        if r["re_pct"] < min_re:
            min_re, min_year = r["re_pct"], y
        curt_mwh += r["total_curtailment_mwh"]
        gen_mwh += (r["pv_gen_mwh"] + r["wind_gen_mwh"] + r["otec_gen_mwh"] + r["rooftop_gen_exported_mwh"])
        per_year.append(r)
        if r["re_pct"] < required_pct - RE_TOL_PP:
            ok = False
            if fail_fast:
                break
    return {"ok": ok, "min_re_pct": float(min_re), "min_re_year": min_year,
            "curtailment_pct_window": (curt_mwh / gen_mwh * 100) if gen_mwh > 0 else 0.0,
            "curtailment_pct_build_year": per_year[0]["curtailment_pct_of_re_gen"] if per_year else 0.0,
            "years": per_year}


# ----------------------------------------------------------------------------
# One block
# ----------------------------------------------------------------------------

def size_block(schedule, y0, y1, required_pct, pv_candidates, bess_candidates, c_rate,
               pv_degr, bess_degr, lifetimes, unit_costs, econ, sim_kwargs, start_year,
               progress=None):
    """Finds the lowest-NPC (PV, BESS) addition at y0 that holds required_pct in every year y0..y1.
    Returns (best_or_None, frontier_rows). frontier_rows has one row per PV candidate: the smallest
    feasible BESS (or 'INFEASIBLE up to the BESS search max')."""
    rate, infl = econ["nominal_discount_rate"], econ["inflation_rate"]
    end_year = int(econ["analysis_end_year"])
    escalate = econ.get("escalate_capex", True)
    lead = int(econ.get("construction_lead_years", 0))
    soc_in = _carry_soc(schedule, start_year, y0, sim_kwargs)

    def unit_npc(tech):
        if tech == "pv":
            u = Tranche("pv", y0, pv_degr, capacity_mw=1.0, lifetime_years=lifetimes.get("pv"))
        else:
            u = Tranche("bess", y0, bess_degr, power_mw=c_rate, energy_mwh=1.0, lifetime_years=lifetimes.get("bess"))
        return eng.tranche_npv(u, unit_costs, rate, infl, start_year, end_year, escalate, lead)

    pv_npc, bess_npc = unit_npc("pv"), unit_npc("bess")
    n_bess = len(bess_candidates)

    def feasible(pv_add, bess_add):
        trial = _with_additions(schedule, y0, pv_add, bess_add, pv_degr, bess_degr, c_rate, lifetimes)
        return run_window(trial, soc_in, y0, y1, required_pct, sim_kwargs, fail_fast=True)["ok"]

    frontier, best = [], None
    for i, pv_add in enumerate(pv_candidates):
        if progress:
            progress(i, len(pv_candidates))
        row = {"build_year": y0, "hold_through": y1, "pv_add_mwp": pv_add}
        if not feasible(pv_add, bess_candidates[-1]):
            row.update({"bess_add_mwh": None, "feasible": False})
            frontier.append(row)
            continue
        lo, hi = 0, n_bess - 1                     # hi is known feasible; find the smallest feasible index
        while lo < hi:
            mid = (lo + hi) // 2
            if feasible(pv_add, bess_candidates[mid]):
                hi = mid
            else:
                lo = mid + 1
        bess_add = bess_candidates[lo]
        trial = _with_additions(schedule, y0, pv_add, bess_add, pv_degr, bess_degr, c_rate, lifetimes)
        full = run_window(trial, soc_in, y0, y1, required_pct, sim_kwargs, fail_fast=False)
        npc_add = pv_add * pv_npc + bess_add * bess_npc
        row.update({"bess_add_mwh": bess_add, "feasible": full["ok"], "npc_add": npc_add,
                    "min_re_pct": full["min_re_pct"], "curtailment_pct_build_year": full["curtailment_pct_build_year"],
                    "curtailment_pct_window": full["curtailment_pct_window"], "selected": False})
        frontier.append(row)
        if full["ok"] and (best is None or npc_add < best["npc_add"] - 1e-9):
            best = dict(row, _index=len(frontier) - 1)

    if best is not None:
        frontier[best["_index"]]["selected"] = True
        best["selected"] = True
        best.pop("_index")
        a = best["pv_add_mwp"], best["bess_add_mwh"]
        edge = (a[0] == pv_candidates[-1]) or (a[1] == bess_candidates[-1])
        best["on_search_edge"] = bool(edge and (pv_candidates[-1] > 0))
    return best, frontier


# ----------------------------------------------------------------------------
# Whole plan
# ----------------------------------------------------------------------------

def parse_year_pairs(text, n_values):
    """'2028:24:102, 2050:8:178' -> {2028: (24.0, 102.0), 2050: (8.0, 178.0)} (n_values numbers after the year)."""
    out = {}
    for item in (text or "").split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) != n_values + 1:
            raise ValueError(f"'{item}' should look like year{':value' * n_values}")
        out[int(parts[0])] = tuple(float(p) for p in parts[1:])
    return out


def build_base_schedule(exogenous_tranches, fixed_additions, pv_degr, bess_degr, c_rate, lifetimes):
    """OTEC (exogenous) plus any fixed {year: (PV_MWp, BESS_MWh)} builds."""
    s = TrancheSchedule()
    for t in exogenous_tranches:
        getattr(s, t.technology).append(t)
    for year, (pv_add, bess_add) in sorted(fixed_additions.items()):
        if pv_add > 0:
            s.pv.append(Tranche("pv", int(year), pv_degr, capacity_mw=pv_add, lifetime_years=lifetimes.get("pv")))
        if bess_add > 0:
            s.bess.append(Tranche("bess", int(year), bess_degr, power_mw=bess_add * c_rate, energy_mwh=bess_add,
                                  lifetime_years=lifetimes.get("bess")))
    return s


def plan_hold_re(base_schedule, blocks, target_pct, pv_candidates, bess_candidates, c_rate,
                 pv_degr, bess_degr, lifetimes, unit_costs, econ, sim_kwargs, start_year, progress=None):
    """Solves the blocks in order. Returns (schedule, block_log, frontier_rows). If a block has no feasible
    answer inside the search grid, solving stops there (later blocks depend on it) and its log row says INFEASIBLE."""
    schedule = base_schedule.clone()
    log, frontier_all = [], []
    for bi, (y0, y1) in enumerate(blocks):
        best, frontier = size_block(
            schedule, y0, y1, target_pct, pv_candidates, bess_candidates, c_rate, pv_degr, bess_degr, lifetimes,
            unit_costs, econ, sim_kwargs, start_year,
            progress=(lambda i, n, bi=bi: progress(bi, len(blocks), i, n)) if progress else None)
        frontier_all.extend(frontier)
        if best is None:
            log.append({"build_year": y0, "hold_through": y1, "status": "INFEASIBLE"})
            break
        pv_add, bess_add = best["pv_add_mwp"], best["bess_add_mwh"]
        if pv_add > 0:
            schedule.pv.append(Tranche("pv", y0, pv_degr, capacity_mw=pv_add, lifetime_years=lifetimes.get("pv")))
        if bess_add > 0:
            schedule.bess.append(Tranche("bess", y0, bess_degr, power_mw=bess_add * c_rate, energy_mwh=bess_add,
                                         lifetime_years=lifetimes.get("bess")))
        log.append({"status": "OK", **best})
    return schedule, log, frontier_all
