"""
bora_bora_100re_app.py
=======================
Separate Streamlit app: "How much PV + BESS does it take to HOLD 100% RE from the OTEC year to 2050?"

It reuses bora_bora_data / bora_bora_engine / bora_bora_export unchanged and adds only the block sizing in
bora_bora_100re.py. The original app (bora_bora_app.py) is not touched.

Run with:  streamlit run bora_bora_100re_app.py
"""

from io import BytesIO

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import bora_bora_100re as hold
import bora_bora_data as data
import bora_bora_engine as eng
import bora_bora_export

st.set_page_config(page_title="Bora Bora - Hold 100% RE", layout="wide")
st.markdown("""
<p style="font-size:2.2rem;font-weight:bold;color:#1f77b4;margin:0;text-align:center">
    Bora Bora - Hold 100% RE from the OTEC year to 2050
</p>
<p style="text-align:center;color:#666;margin-top:2px">
    What-if run for the project manager: size PV + BESS so RE stays at 100% in every year, rebuilt in blocks.
    Separate from the main planner.
</p>
""", unsafe_allow_html=True)

# ==============================================================================
# SIDEBAR
# ==============================================================================
with st.sidebar:
    with st.expander("🎯 100% RE plan (this app's question)", expanded=True):
        target_pct = st.number_input("RE target to hold (%)", value=100.0, min_value=50.0, max_value=100.0, step=0.5)
        first_year = st.number_input("First year that must be at target (OTEC year)", value=2032, step=1)
        hold_years = st.number_input("Years held AFTER the build year", value=5, min_value=0, max_value=15, step=1,
                                     help="5 -> a tranche built in 2032 must give 100% in 2032..2037, the next one is "
                                          "built in 2038 for 2038..2043, and so on. 0 = rebuild every single year.")
        merge_tail = st.checkbox("Fold a short last block into the previous one (no stand-alone build for the tail)",
                                 value=False,
                                 help="With 2032 / 5 yrs / 2050 the blocks are 2032-37, 2038-43, 2044-49 and a one-year "
                                      "2050 block. Ticking this makes the last block 2044-2050 instead.")
        pre_fixed_str = st.text_input("Builds BEFORE the first target year (format 'year:PV_MWp:BESS_MWh', comma-separated)",
                                      "2028:24:102",
                                      help="Pinned, not searched. Default is the 2028 tranche of the pathways already presented.")
        ref_fixed_str = st.text_input("Reference pathway builds, for comparison (same format)", "2028:24:102, 2050:8:178",
                                      help="Default = Pathway 2 as presented (91.9% -> 100% only in 2050). The cost of "
                                           "holding 100% early is shown as the difference to this.")
        show_ref = st.checkbox("Compare against the reference pathway", value=True)

    with st.expander("🔍 Sizing search grid", expanded=False):
        pv_max = st.number_input("Max PV addition per block (MWp)", value=150, step=10)
        pv_step = st.number_input("PV step (MWp)", value=2, min_value=1, step=1)
        bess_max = st.number_input("Max BESS addition per block (MWh)", value=1000, step=50)
        bess_step = st.number_input("BESS step (MWh)", value=5, min_value=1, step=1)
        st.caption("For every PV size the smallest BESS that holds the target for the whole block is found by "
                   "bisection, so a fine BESS step is cheap. PV step drives run time.")

    with st.expander("☀️ Solar PV (Agri)", expanded=False):
        pv_cf_upload = st.file_uploader("Hourly generation-factor profile (CSV) - required", type=["csv"], key="pv_upload")
        dc_ac_ratio = st.number_input("DC:AC ratio", value=1.3, step=0.05)
        pv_degradation_pct = st.number_input("Degradation rate (%/yr)", value=0.3, step=0.1, key="pv_deg")

    with st.expander("🏠 Rooftop PV", expanded=False):
        rooftop_enabled = st.checkbox("Include rooftop (exported-to-grid share only)", value=True)
        rooftop_existing_mwp = st.number_input("Existing capacity (MWp)", value=2.357, step=0.1, disabled=not rooftop_enabled)
        rooftop_ceiling_mwp = st.number_input("Ceiling capacity (MWp)", value=4.5, step=0.1, disabled=not rooftop_enabled)
        rooftop_ramp_start_year = st.number_input("Last year at existing capacity", value=2025, step=1, disabled=not rooftop_enabled)
        rooftop_ramp_mwp_per_year = st.number_input("New capacity added (MWp/yr)", value=0.143, step=0.01, format="%.3f",
                                                     disabled=not rooftop_enabled)
        rooftop_self_consumption_pct = st.number_input("Self-consumption offset (%)", value=93.0, step=1.0,
                                                         disabled=not rooftop_enabled) / 100

    with st.expander("🌊 OTEC", expanded=False):
        otec_enabled = st.checkbox("Include OTEC", value=True)
        otec_schedule_str = st.text_input("OTEC schedule ('year:mw', comma-separated)", "2032:2.6", disabled=not otec_enabled,
                                          help="2.6 MW matches the pathways already presented and the baseline "
                                               "trajectory files (the main app's own default is 1.2 MW).")
        otec_cf = st.number_input("Capacity factor", value=1.0, min_value=0.0, max_value=1.0, step=0.01, disabled=not otec_enabled)
        otec_degradation_pct = st.number_input("Degradation rate (%/yr)", value=0.0, step=0.1, disabled=not otec_enabled, key="otec_deg")

    with st.expander("🔋 BESS", expanded=False):
        bess_c_rate = st.number_input("Power:energy ratio (C-rate)", value=0.5, min_value=0.05, max_value=2.0, step=0.05)
        bess_charge_eff = st.number_input("Charge efficiency", value=0.95, min_value=0.5, max_value=1.0, step=0.01)
        bess_discharge_eff = st.number_input("Discharge efficiency", value=0.95, min_value=0.5, max_value=1.0, step=0.01)
        bess_degradation_pct = st.number_input("Degradation rate (%/yr)", value=1.5, step=0.1, key="bess_deg")
        c1, c2, c3 = st.columns(3)
        bess_initial_soc_pct = c1.number_input("Initial SoC (%)", value=50.0, min_value=0.0, max_value=100.0, step=1.0)
        bess_min_soc_pct = c2.number_input("Min SoC (%)", value=5.0, min_value=0.0, max_value=100.0, step=1.0)
        bess_max_soc_pct = c3.number_input("Max SoC (%)", value=95.0, min_value=0.0, max_value=100.0, step=1.0)

    with st.expander("🏝️ Demand - existing development (baseline)", expanded=False):
        baseline_shape_upload = st.file_uploader("Hourly demand shape, one calendar year (CSV) - required", type=["csv"], key="baseline_upload")
        annual_table_upload = st.file_uploader("Annual demand by year (CSV) - optional", type=["csv"], key="annual_upload")
        underlying_growth_beyond_pct = st.number_input("Growth beyond the last table year (%/yr)", value=0.0, step=0.1)

    with st.expander("🔌 Demand - EV chargers (land, excl. bus)", expanded=False):
        ev_land_upload = st.file_uploader("Day profile by year (CSV)", type=["csv"], key="ev_land_upload")
    with st.expander("⛴️ Demand - Marine transport (shore power)", expanded=False):
        marine_upload = st.file_uploader("Day profile by year (CSV)", type=["csv"], key="marine_upload")
    with st.expander("🏗️ Demand - Grande Vaitape", expanded=False):
        gv_commissioning_year = st.number_input("Commissioning year", value=2027, step=1, key="gv_year")
        gv_annual_mwh = st.number_input("Annual demand (MWh/yr)", value=4992.32, step=10.0, key="gv_annual")
        gv_shape_upload = st.file_uploader("Hourly day shape (CSV: hour, mw) - optional", type=["csv"], key="gv_upload")

    with st.expander("⚡ Grid", expanded=False):
        edt_penetration_cap_pct = st.number_input("EDT intermittent penetration cap (% of demand)", value=100.0,
                                                  min_value=0.0, max_value=200.0, step=5.0)

    with st.expander("💰 Economics", expanded=False):
        analysis_end_year = st.number_input("Analysis horizon end year", value=2050, step=1)
        nominal_discount_rate_pct = st.number_input("Nominal discount rate (%)", value=8.0, step=0.5)
        inflation_rate_pct = st.number_input("Inflation rate (%)", value=2.0, step=0.5)
        escalate_capex = st.checkbox("Escalate CAPEX with inflation to the year it is spent", value=True)
        construction_lead_years = st.number_input("Construction lead (years)", value=1, min_value=0, max_value=5, step=1)
        replacement_enabled = st.checkbox("Model end-of-life replacement inside the horizon", value=True)
        st.markdown("**PV**")
        c1, c2 = st.columns(2)
        pv_capex_per_mwp = c1.number_input("CAPEX ($/MWp)", value=1_200_000, step=50_000, key="pv_capex")
        pv_om_per_mwp_yr = c2.number_input("O&M ($/MWp/yr)", value=20_000, step=1_000, key="pv_om")
        pv_lifetime = st.number_input("Lifetime (years)", value=25, step=1, key="pv_life")
        st.markdown("**OTEC**")
        c1, c2 = st.columns(2)
        otec_capex_per_mw = c1.number_input("CAPEX ($/MW, variable)", value=63_138_462, step=500_000, key="otec_capex")
        otec_capex_fixed = c1.number_input("CAPEX ($, fixed per tranche)", value=0, step=1_000_000, key="otec_capex_fixed")
        otec_om_per_mw_yr = c2.number_input("O&M ($/MW/yr)", value=1_500_000, step=100_000, key="otec_om")
        otec_lifetime = st.number_input("Lifetime (years)", value=30, step=1, key="otec_life")
        st.markdown("**BESS**")
        c1, c2 = st.columns(2)
        bess_capex_per_mwh = c1.number_input("CAPEX ($/MWh installed)", value=100_000, step=25_000, key="bess_capex")
        bess_om_per_mwh_yr = c2.number_input("O&M ($/MWh/yr)", value=500, step=100, key="bess_om")
        bess_lifetime = st.number_input("Lifetime (years; 999 = no replacement in the horizon)", value=999, step=1, key="bess_life")

    run_button = st.button("🚀 Run", type="primary", use_container_width=True)


def _parse_otec(text):
    out = []
    for pair in text.split(","):
        pair = pair.strip()
        if not pair:
            continue
        y_str, mw_str = pair.split(":", 1)
        out.append((int(y_str.strip()), float(mw_str.strip())))
    return out


# ==============================================================================
# RUN
# ==============================================================================
if run_button:
    if bess_min_soc_pct >= bess_max_soc_pct:
        st.error("BESS Min SoC must be less than Max SoC.")
        st.stop()
    try:
        pre_fixed = hold.parse_year_pairs(pre_fixed_str, 2)
        ref_fixed = hold.parse_year_pairs(ref_fixed_str, 2)
        otec_schedule = _parse_otec(otec_schedule_str) if otec_enabled else []
    except ValueError as e:
        st.error(f"Could not read a text input: {e}")
        st.stop()
    if any(y >= int(first_year) for y in pre_fixed):
        st.error(f"'Builds BEFORE the first target year' must all be earlier than {int(first_year)}.")
        st.stop()
    if otec_enabled and not otec_schedule:
        st.error("OTEC is included but the schedule is empty.")
        st.stop()
    try:
        with st.spinner("Loading data..."):
            pv_cf = data.load_pv_cf(pv_cf_upload)
            baseline_demand = data.load_baseline_demand_shape_mw(baseline_shape_upload)
            underlying_table = data.load_annual_table(annual_table_upload, data.DEFAULT_UNDERLYING_DEMAND_MWH)
            ev_land_profiles = data.load_day_profiles_by_year(ev_land_upload)
            marine_profiles = data.load_day_profiles_by_year(marine_upload)
            combined_years = sorted(set(ev_land_profiles) | set(marine_profiles))
            ev_marine_profiles = {
                year: [a + b for a, b in zip(data.get_day_profile_for_year(ev_land_profiles, year),
                                              data.get_day_profile_for_year(marine_profiles, year))]
                for year in combined_years}
            gv_day_shape = data.load_single_day_shape(gv_shape_upload)
    except (ValueError, KeyError) as e:
        st.error(f"Problem reading an uploaded file: {e}")
        st.stop()

    input_warnings = data.collect_input_warnings(
        pv_cf=pv_cf, baseline_shape=baseline_demand, ev_marine_profiles=ev_marine_profiles,
        gv_day_shape=gv_day_shape, gv_annual_mwh=gv_annual_mwh, annual_table=underlying_table)

    lifetimes = ({"pv": int(pv_lifetime), "bess": int(bess_lifetime)} if replacement_enabled else {})
    otec_life = int(otec_lifetime) if replacement_enabled else None
    exogenous = [eng.Tranche("otec", y, otec_degradation_pct / 100, capacity_mw=mw, lifetime_years=otec_life)
                 for y, mw in otec_schedule]

    sim_kwargs = dict(
        pv_cf_hourly=pv_cf, wind_cf_hourly=None, dc_ac_ratio=dc_ac_ratio,
        bess_charge_eff=bess_charge_eff, bess_discharge_eff=bess_discharge_eff, otec_cf=otec_cf,
        edt_penetration_cap=edt_penetration_cap_pct / 100,
        baseline_demand_hourly_mw=baseline_demand, underlying_annual_table=underlying_table,
        ev_marine_day_profiles=ev_marine_profiles,
        gv_annual_mwh=gv_annual_mwh, gv_commissioning_year=int(gv_commissioning_year), gv_day_shape=gv_day_shape,
        rooftop_enabled=rooftop_enabled, rooftop_existing_mwp=rooftop_existing_mwp,
        rooftop_ceiling_mwp=rooftop_ceiling_mwp, rooftop_ramp_start_year=int(rooftop_ramp_start_year),
        rooftop_ramp_mwp_per_year=rooftop_ramp_mwp_per_year, rooftop_self_consumption_pct=rooftop_self_consumption_pct,
        bess_initial_soc_frac=bess_initial_soc_pct / 100, bess_min_soc_frac=bess_min_soc_pct / 100,
        bess_max_soc_frac=bess_max_soc_pct / 100, underlying_growth_beyond_last=underlying_growth_beyond_pct / 100)
    unit_costs = dict(
        pv_capex_per_mwp=pv_capex_per_mwp, pv_om_per_mwp_yr=pv_om_per_mwp_yr,
        wind_capex_per_mw=0.0, wind_om_per_mw_yr=0.0,
        otec_capex_per_mw=otec_capex_per_mw, otec_capex_fixed=otec_capex_fixed, otec_om_per_mw_yr=otec_om_per_mw_yr,
        bess_capex_per_mwh=bess_capex_per_mwh, bess_om_per_mwh_yr=bess_om_per_mwh_yr)
    econ_params = dict(
        nominal_discount_rate=nominal_discount_rate_pct / 100, inflation_rate=inflation_rate_pct / 100,
        analysis_end_year=int(analysis_end_year), escalate_capex=escalate_capex,
        construction_lead_years=int(construction_lead_years))
    start_year = 2026
    years = list(range(start_year, int(analysis_end_year) + 1))
    pv_degr, bess_degr = pv_degradation_pct / 100, bess_degradation_pct / 100

    blocks = hold.block_plan(int(first_year), int(hold_years), int(analysis_end_year), merge_tail)
    pv_candidates = list(range(0, int(pv_max) + 1, int(pv_step)))
    bess_candidates = list(range(0, int(bess_max) + 1, int(bess_step)))

    base = hold.build_base_schedule(exogenous, pre_fixed, pv_degr, bess_degr, bess_c_rate, lifetimes)
    bar = st.progress(0.0, text="Sizing blocks...")

    def _progress(bi, nb, i, n):
        bar.progress((bi + i / max(n, 1)) / nb,
                     text=f"Block {bi + 1}/{nb} ({blocks[bi][0]}-{blocks[bi][1]}): PV candidate {i + 1}/{n}")

    schedule, block_log, frontier = hold.plan_hold_re(
        base, blocks, target_pct, pv_candidates, bess_candidates, bess_c_rate, pv_degr, bess_degr, lifetimes,
        unit_costs, econ_params, sim_kwargs, start_year, progress=_progress)
    bar.empty()

    def _evaluate(sched):
        traj_df = pd.DataFrame(eng.simulate_trajectory(sched, years, **sim_kwargs))
        e = eng.compute_lifecycle_economics(sched, traj_df, unit_costs, nominal_discount_rate_pct / 100,
                                            inflation_rate_pct / 100, start_year, int(analysis_end_year),
                                            escalate_capex=escalate_capex,
                                            construction_lead_years=int(construction_lead_years))
        return traj_df, e

    with st.spinner("Running full trajectories and economics..."):
        traj_df, econ = _evaluate(schedule)
        ref = None
        if show_ref:
            ref_sched = hold.build_base_schedule(exogenous, ref_fixed, pv_degr, bess_degr, bess_c_rate, lifetimes)
            ref_traj, ref_econ = _evaluate(ref_sched)
            ref = dict(schedule=ref_sched, traj=ref_traj, econ=ref_econ)

    st.session_state.update(
        hr_done=True, hr_schedule=schedule, hr_log=block_log, hr_frontier=pd.DataFrame(frontier),
        hr_traj=traj_df, hr_econ=econ, hr_ref=ref, hr_blocks=blocks, hr_target=target_pct,
        hr_first_year=int(first_year), hr_warnings=input_warnings, hr_pre_fixed=pre_fixed,
        hr_sim_kwargs=sim_kwargs, hr_years=years,
        hr_export=dict(unit_costs=unit_costs, rate=nominal_discount_rate_pct / 100, infl=inflation_rate_pct / 100,
                       start=start_year, end=int(analysis_end_year), escalate=escalate_capex,
                       lead=int(construction_lead_years), lifetimes=dict(lifetimes, otec=otec_life),
                       degradation=dict(pv=pv_degr, bess=bess_degr, wind=0.0, otec=otec_degradation_pct / 100)),
        hr_flags=dict(pv_step=int(pv_step), bess_step=int(bess_step), pv_max=int(pv_max), bess_max=int(bess_max)),
        hr_hourly=None)

# ==============================================================================
# RESULTS
# ==============================================================================
if not st.session_state.get("hr_done"):
    st.info("Configure inputs in the sidebar (upload the PV profile and the demand files, same as the main planner), then click Run.")
    st.stop()

schedule = st.session_state["hr_schedule"]
block_log = st.session_state["hr_log"]
frontier_df = st.session_state["hr_frontier"]
traj_df = st.session_state["hr_traj"]
econ = st.session_state["hr_econ"]
ref = st.session_state["hr_ref"]
blocks = st.session_state["hr_blocks"]
target = st.session_state["hr_target"]
first_year_run = st.session_state["hr_first_year"]
flags = st.session_state["hr_flags"]
exp = st.session_state["hr_export"]

for w in st.session_state.get("hr_warnings", []):
    st.warning(w)

ok_rows = [l for l in block_log if l["status"] == "OK"]
bad_rows = [l for l in block_log if l["status"] != "OK"]
if bad_rows:
    b = bad_rows[0]
    st.error(f"No PV/BESS combination inside the search grid holds {target:g}% for the block {b['build_year']}-{b['hold_through']}. "
             f"Raise 'Max PV' / 'Max BESS' in the sizing grid. Blocks after it were not sized.")
if any(l.get("on_search_edge") for l in ok_rows):
    st.warning("At least one block's winner sits on the edge of the search grid (max PV or max BESS) - the true optimum "
               "may be beyond it. Widen the grid and re-run.")

hold_view = traj_df[traj_df["year"] >= first_year_run]
below = hold_view[hold_view["re_pct"] < target - 1e-6]
if not bad_rows:
    if below.empty:
        st.success(f"RE is at or above {target:g}% in every year {first_year_run}-{int(traj_df['year'].max())} "
                   f"(lowest {hold_view['re_pct'].min():.4f}%).")
    else:
        st.error(f"RE falls below {target:g}% in {', '.join(str(int(y)) for y in below['year'])}.")

# ---- headline numbers --------------------------------------------------------
pv_total = sum(t.capacity_mw for t in schedule.pv)
bess_total = sum(t.energy_mwh for t in schedule.bess)
curt_hold = hold_view["total_curtailment_mwh"].sum()
st.subheader("Headline")
m = st.columns(5)
m[0].metric("PV built (MWp, all builds)", f"{pv_total:,.0f}")
m[1].metric("BESS built (MWh, all builds)", f"{bess_total:,.0f}")
m[2].metric("NPC (lifecycle)", f"${econ['npv_cost'] / 1e6:,.1f}M")
m[3].metric("LCOE", f"${econ['lcoe_per_mwh']:,.1f}/MWh")
m[4].metric(f"Curtailed {first_year_run}-{int(traj_df['year'].max())}", f"{curt_hold / 1000:,.1f} GWh")
if ref is not None:
    r_hold = ref["traj"][ref["traj"]["year"] >= first_year_run]
    d = st.columns(5)
    r_pv = sum(t.capacity_mw for t in ref["schedule"].pv)
    r_bess = sum(t.energy_mwh for t in ref["schedule"].bess)
    d[0].metric("vs reference: PV (MWp)", f"{pv_total - r_pv:+,.0f}", help=f"Reference builds {r_pv:,.0f} MWp")
    d[1].metric("vs reference: BESS (MWh)", f"{bess_total - r_bess:+,.0f}", help=f"Reference builds {r_bess:,.0f} MWh")
    d[2].metric("vs reference: NPC", f"{(econ['npv_cost'] - ref['econ']['npv_cost']) / 1e6:+,.1f}M $",
                f"{(econ['npv_cost'] / ref['econ']['npv_cost'] - 1) * 100:+.1f}%", delta_color="off",
                help=f"Reference NPC ${ref['econ']['npv_cost'] / 1e6:,.1f}M")
    d[3].metric("vs reference: LCOE", f"{econ['lcoe_per_mwh'] - ref['econ']['lcoe_per_mwh']:+,.1f} $/MWh",
                f"{(econ['lcoe_per_mwh'] / ref['econ']['lcoe_per_mwh'] - 1) * 100:+.1f}%", delta_color="off",
                help=f"Reference LCOE ${ref['econ']['lcoe_per_mwh']:,.1f}/MWh")
    d[4].metric("vs reference: curtailed", f"{(curt_hold - r_hold['total_curtailment_mwh'].sum()) / 1000:+,.1f} GWh",
                help=f"Reference curtails {r_hold['total_curtailment_mwh'].sum() / 1000:,.1f} GWh over the same years")

# ---- block table -------------------------------------------------------------
st.subheader("What to build, and when")
st.caption("Each row is one build. It is sized so RE stays at the target in EVERY year from the build year to the last year of "
           "its block, after PV/BESS degradation and demand change. Addition NPC is that build's own lifecycle cost "
           "(escalated CAPEX + O&M, discounted to 2026). Blocks are solved in order, each given the earlier builds.")
rows = []
for l in block_log:
    if l["status"] != "OK":
        rows.append({"Build year": l["build_year"], "Holds target through": l["hold_through"], "Status": "INFEASIBLE in grid"})
        continue
    rows.append({
        "Build year": l["build_year"], "Holds target through": l["hold_through"],
        "PV added (MWp)": l["pv_add_mwp"], "BESS added (MWh)": l["bess_add_mwh"],
        "BESS power (MW)": l["bess_add_mwh"] * bess_c_rate, "Addition NPC ($M)": l["npc_add"] / 1e6,
        "Lowest RE% in block": l["min_re_pct"], "Curtailment % (build year)": l["curtailment_pct_build_year"],
        "Curtailment % (block)": l["curtailment_pct_window"], "Status": "OK"})
block_df = pd.DataFrame(rows)
st.dataframe(block_df.round(2), use_container_width=True, hide_index=True)
pre = st.session_state["hr_pre_fixed"]
if pre:
    st.caption("Pinned earlier builds (not searched): " + ", ".join(f"{y}: {a:g} MWp PV + {b:g} MWh BESS" for y, (a, b) in sorted(pre.items())))

# ---- charts ------------------------------------------------------------------
LAYOUT = dict(height=380, plot_bgcolor="white", paper_bgcolor="white", hovermode="x unified", xaxis_title="Year")
c1, c2 = st.columns(2)
with c1:
    st.markdown("**RE% by year**")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=traj_df["year"], y=traj_df["re_pct"], name="This plan", mode="lines+markers",
                              line=dict(color="#2E7D32", width=3)))
    if ref is not None:
        fig.add_trace(go.Scatter(x=ref["traj"]["year"], y=ref["traj"]["re_pct"], name="Reference", mode="lines",
                                  line=dict(color="#888", dash="dash")))
    fig.add_hline(y=target, line_dash="dot", line_color="red")
    fig.update_layout(yaxis_title="RE penetration (%)", **LAYOUT)
    st.plotly_chart(fig, use_container_width=True)
with c2:
    st.markdown("**Curtailment (% of PV + OTEC + rooftop generation)**")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=traj_df["year"], y=traj_df["curtailment_pct_of_re_gen"], name="This plan", marker_color="#E63946"))
    if ref is not None:
        fig.add_trace(go.Scatter(x=ref["traj"]["year"], y=ref["traj"]["curtailment_pct_of_re_gen"], name="Reference",
                                  mode="lines", line=dict(color="#333", dash="dash")))
    fig.update_layout(yaxis_title="Curtailment (%)", **LAYOUT)
    st.plotly_chart(fig, use_container_width=True)
c1, c2 = st.columns(2)
with c1:
    st.markdown("**Installed PV (MWp, after degradation)**")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=traj_df["year"], y=traj_df["pv_mwp"], name="This plan", mode="lines",
                              line=dict(color="#FDB462", width=3), fill="tozeroy"))
    if ref is not None:
        fig.add_trace(go.Scatter(x=ref["traj"]["year"], y=ref["traj"]["pv_mwp"], name="Reference", mode="lines",
                                  line=dict(color="#333", dash="dash")))
    fig.update_layout(yaxis_title="MWp", **LAYOUT)
    st.plotly_chart(fig, use_container_width=True)
with c2:
    st.markdown("**Installed BESS (MWh, after degradation)**")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=traj_df["year"], y=traj_df["bess_energy_mwh"], name="This plan", mode="lines",
                              line=dict(color="#6A3D9A", width=3), fill="tozeroy"))
    if ref is not None:
        fig.add_trace(go.Scatter(x=ref["traj"]["year"], y=ref["traj"]["bess_energy_mwh"], name="Reference", mode="lines",
                                  line=dict(color="#333", dash="dash")))
    fig.update_layout(yaxis_title="MWh", **LAYOUT)
    st.plotly_chart(fig, use_container_width=True)

# ---- year by year ------------------------------------------------------------
cols = {"year": "Year", "re_pct": "RE %", "pv_mwp": "PV (MWp)", "bess_energy_mwh": "BESS (MWh)",
        "otec_mw": "OTEC (MW)", "total_curtailment_mwh": "Curtailed (MWh)", "curtailment_pct_of_re_gen": "Curtailment %",
        "total_unmet_mwh": "Diesel / unmet (MWh)"}
yy = traj_df[list(cols)].rename(columns=cols)
if ref is not None:
    rr = ref["traj"][["year", "re_pct", "pv_mwp", "bess_energy_mwh", "total_curtailment_mwh", "curtailment_pct_of_re_gen"]]
    rr.columns = ["Year", "Ref RE %", "Ref PV (MWp)", "Ref BESS (MWh)", "Ref curtailed (MWh)", "Ref curtailment %"]
    yy = yy.merge(rr, on="Year")
with st.expander("Year-by-year table", expanded=False):
    st.dataframe(yy.round(2), use_container_width=True, hide_index=True)

# ---- integrity -----------------------------------------------------------------
st.subheader("Model integrity checks")
c1, c2, c3 = st.columns(3)
sim_hours = int(traj_df["bess_simultaneous_hours"].sum())
c1.metric("Hours with BESS charging AND discharging", sim_hours)
c2.metric("Max hourly energy-balance error (MW)", f"{traj_df['max_energy_balance_error_mw'].max():.1e}")
c3.metric("Max BESS SoC bookkeeping error (MWh)", f"{traj_df['soc_balance_error_mwh'].abs().max():.1e}")
if sim_hours > 0:
    st.error("Simultaneous BESS charge/discharge detected - do not use these results.")

# ---- economics -----------------------------------------------------------------
st.markdown("---")
st.subheader("Economics")
st.caption(f"NPV(costs) / NPV(project-served energy), both discounted at {exp['rate'] * 100:g}% nominal to 2026. CAPEX "
           f"{'escalated at ' + format(exp['infl'] * 100, 'g') + '%/yr' if exp['escalate'] else 'at flat 2026 prices'}, "
           f"booked {exp['lead']} yr before commissioning; no salvage credit at the horizon end (so late builds are penalised "
           f"in LCOE). Same method as the main planner.")
labels = {"pv": "PV", "otec": "OTEC", "bess": "BESS"}
econ_rows = []
for tech in ["pv", "otec", "bess"]:
    row = {"Technology": labels[tech], "Lifecycle cost ($M)": econ["cost_by_technology"][tech] / 1e6,
           "LCOE contribution ($/MWh)": econ["lcoe_contribution_by_technology"][tech]}
    if ref is not None:
        row["Reference cost ($M)"] = ref["econ"]["cost_by_technology"][tech] / 1e6
        row["Difference ($M)"] = row["Lifecycle cost ($M)"] - row["Reference cost ($M)"]
    econ_rows.append(row)
tot = {"Technology": "Total", "Lifecycle cost ($M)": econ["npv_cost"] / 1e6, "LCOE contribution ($/MWh)": econ["lcoe_per_mwh"]}
if ref is not None:
    tot["Reference cost ($M)"] = ref["econ"]["npv_cost"] / 1e6
    tot["Difference ($M)"] = tot["Lifecycle cost ($M)"] - tot["Reference cost ($M)"]
econ_rows.append(tot)
econ_df = pd.DataFrame(econ_rows)
st.dataframe(econ_df.round(2), use_container_width=True, hide_index=True)
cashflow_df = pd.DataFrame(econ["cashflow_rows"])
with st.expander("Year-by-year cash flow", expanded=False):
    st.dataframe(cashflow_df.round(2), use_container_width=True, hide_index=True)

# ---- frontier ------------------------------------------------------------------
st.markdown("---")
st.subheader("Sizing frontier (transparency)")
st.caption("For every PV size tried, the smallest BESS that holds the target for the whole block, and what that pair costs. "
           "The chosen row is the cheapest. Use it to answer 'what if we built more PV and less BESS?'.")
if frontier_df.empty:
    st.info("No frontier data.")
else:
    block_opts = [f"{int(a)}-{int(b)}" for a, b in blocks if ((frontier_df["build_year"] == a).any())]
    pick = st.selectbox("Block", block_opts)
    y0 = int(pick.split("-")[0])
    fv = frontier_df[frontier_df["build_year"] == y0].copy()
    feas = fv[fv["feasible"] == True]  # noqa: E712
    if not feas.empty:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=feas["pv_add_mwp"], y=feas["bess_add_mwh"], mode="lines+markers", name="Smallest feasible BESS",
                                  line=dict(color="#6A3D9A")))
        sel = feas[feas["selected"] == True]  # noqa: E712
        if not sel.empty:
            fig.add_trace(go.Scatter(x=sel["pv_add_mwp"], y=sel["bess_add_mwh"], mode="markers", name="Chosen (lowest NPC)",
                                      marker=dict(color="#E63946", size=14, symbol="star")))
        fig.update_layout(xaxis_title="PV added (MWp)", yaxis_title="BESS added (MWh)", height=360,
                           plot_bgcolor="white", paper_bgcolor="white")
        st.plotly_chart(fig, use_container_width=True)
    show = fv.rename(columns={"build_year": "Build year", "hold_through": "Holds through", "pv_add_mwp": "PV added (MWp)",
                              "bess_add_mwh": "Smallest BESS (MWh)", "feasible": "Feasible?", "npc_add": "Addition NPC ($)",
                              "min_re_pct": "Lowest RE%", "curtailment_pct_build_year": "Curtailment % (build yr)",
                              "curtailment_pct_window": "Curtailment % (block)", "selected": "Chosen?"})
    st.dataframe(show.round(2), use_container_width=True, hide_index=True)

# ---- downloads -------------------------------------------------------------------
st.markdown("---")
st.subheader("Downloads")
buf = BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    block_df.round(4).to_excel(writer, sheet_name="Build_Plan", index=False)
    traj_df.to_excel(writer, sheet_name="Trajectory_Plan", index=False)
    if ref is not None:
        ref["traj"].to_excel(writer, sheet_name="Trajectory_Reference", index=False)
    yy.round(4).to_excel(writer, sheet_name="Year_by_Year", index=False)
    econ_df.round(4).to_excel(writer, sheet_name="Economics", index=False)
    cashflow_df.to_excel(writer, sheet_name="LCOE_Cashflow", index=False)
    if not frontier_df.empty:
        frontier_df.to_excel(writer, sheet_name="Sizing_Frontier", index=False)
st.download_button("Download results (Excel)", data=buf.getvalue(), file_name="bora_bora_hold_100re_results.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
try:
    lcoe_xlsx = bora_bora_export.build_lcoe_workbook(
        schedule, traj_df, exp["unit_costs"], exp["rate"], exp["infl"], exp["start"], exp["end"], exp["escalate"],
        exp["lifetimes"], exp["degradation"], econ["lcoe_per_mwh"], econ["npv_cost"], construction_lead_years=exp["lead"])
    st.download_button("Download LCOE verification workbook (Excel, live formulas)", data=lcoe_xlsx,
                       file_name="bora_bora_hold_100re_lcoe_verification.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
except Exception as e:  # never let the export break the results page
    st.warning(f"LCOE verification workbook could not be built: {e}")

if st.button("Generate hourly dispatch workbook (one sheet per year, 8,760 hours each)"):
    with st.spinner("Simulating hourly dispatch for every year..."):
        hourly = eng.simulate_trajectory_hourly(schedule, st.session_state["hr_years"], **st.session_state["hr_sim_kwargs"])
        hbuf = BytesIO()
        with pd.ExcelWriter(hbuf, engine="openpyxl") as writer:
            for year, rws in hourly.items():
                pd.DataFrame(rws).to_excel(writer, sheet_name=f"Dispatch_{year}", index=False)
        st.session_state["hr_hourly"] = hbuf.getvalue()
if st.session_state.get("hr_hourly"):
    st.download_button("Download hourly dispatch (Excel)", data=st.session_state["hr_hourly"],
                       file_name="bora_bora_hold_100re_hourly_dispatch.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
