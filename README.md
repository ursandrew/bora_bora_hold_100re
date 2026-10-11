# Bora Bora - Hold 100% RE Planner

Separate what-if app for P-001341 (Bora Bora net-zero advisory). It answers one question from the
project manager:

> How much PV + BESS does it take to reach **100% RE in the OTEC year (2032)** and **hold it** to 2050,
> rebuilding in blocks, and what does that cost in money and curtailment?

It is a standalone copy of the planner's engine with one extra module. The original capacity planner
(`bora_bora_app.py`) is not needed and is not modified.

## The problem it solves

1. Size the PV + BESS built in the OTEC year (default 2032) so RE = 100% in that year.
2. Over-build it so 100% is also held for the next N years (default 5, i.e. 2032-2037). The extra covers PV/BESS
   degradation and demand change.
3. At the next block start (2038) add a new tranche that holds 100% for 2038-2043; repeat (2044-2049) to the horizon.
   The last block is cut at 2050 (a one-year 2050 build), or folded into 2044-2050 with a sidebar checkbox.

Pre-2032 builds (default 2028: 24 MWp + 102 MWh) are pinned, not searched.

## How a block is sized

For every candidate PV addition, the smallest BESS addition that keeps RE >= target in **every year of the block** is
found by bisection on the BESS grid (more BESS never hurts for a fixed PV size). The lowest-NPC (PV, BESS) pair on that
frontier wins. Each candidate is simulated year by year with the battery charge chained from year to year, exactly like
the final trajectory, so the RE% in the sizing table equals the RE% in the results.

Blocks are solved in order, each given the earlier builds (greedy by block, not a joint optimisation). If a block cannot
be met inside the search grid, solving stops there and the app says so. A warning appears when a winner sits on the edge
of the grid (max PV or max BESS) - widen the grid and re-run.

## What you get

- Headline: PV / BESS built, NPC, LCOE, curtailed energy, and the **difference to a reference pathway**
  (default: Pathway 2 = 2028: 24 MWp + 102 MWh, 2050: 8 MWp + 178 MWh).
- Build table: build year, years held, PV and BESS added, addition NPC, lowest RE% in the block, curtailment %.
- Charts: RE%, curtailment %, installed PV, installed BESS (plan vs reference).
- Economics by technology (same cash-flow NPC / LCOE method as the main planner, with the difference to the reference).
- Sizing frontier per block (what PV/BESS mix also works, and what it costs).
- Downloads: results workbook, LCOE verification workbook (live formulas), optional hourly dispatch workbook.
- Integrity checks: simultaneous charge/discharge hours (must be 0), energy-balance error, SoC bookkeeping error.

## Running it

```bash
pip install -r requirements.txt
streamlit run bora_bora_100re_app.py
```

Streamlit Community Cloud: set the main file to `bora_bora_100re_app.py`.

Upload in the sidebar (same files as the main planner): hourly PV generation-factor CSV (required), baseline hourly
demand shape CSV (required), and optionally the annual demand table, EV and marine day profiles, Grande Vaitape shape.
Nothing is bundled, so no data files are needed in the repo. Wind is not modelled in this app.

## Defaults worth checking before you quote numbers

- OTEC: 2032 at **2.6 MW** (matches the presented pathways; the main planner's own default is 1.2 MW - confirm which is right).
- Costs: PV $1.2M/MWp, BESS $100k/MWh, OTEC $63.14M/MW - the same defaults the baseline trajectory files were run with.
  PV, BESS and OTEC costs remain placeholders until validated figures are available.
- Discount 8%, inflation 2%, CAPEX escalated, 1-year construction lead, no salvage value at 2050.
- BESS: C-rate 0.5, 95/95% efficiency, 1.5%/yr fade, 50% start SoC, 5-95% window, no replacement (lifetime 999).

## Reading the results

- Holding 100% **every hour** of the year means zero diesel hours; the size is set by the worst few hours of one
  representative weather year. Expect a very large oversize and heavy curtailment - that is the finding, not a bug.
- No salvage credit at 2050, so a late build (e.g. a stand-alone 2050 block) is penalised in LCOE.
- Curtailment is reported as a % of PV + OTEC + exported-rooftop generation, as in the main planner.
- 100% means total served = total demand (tolerance 1e-6 pp); the check is applied to each year of each block.

## Files

- `bora_bora_100re_app.py` - Streamlit UI for this question.
- `bora_bora_100re.py` - block plan, window feasibility check, bisection sizing (calls the engine, adds no physics).
- `bora_bora_engine.py` - vintage-tracked dispatch, economics, compliance (identical to the main planner's).
- `bora_bora_data.py` - CSV loaders and input guards (identical to the main planner's).
- `bora_bora_export.py` - LCOE verification workbook builder (identical to the main planner's).
