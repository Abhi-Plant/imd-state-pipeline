"""Quality checks and period summaries. Flags problems; never fills or edits values."""

from __future__ import annotations

import numpy as np
import pandas as pd
import xarray as xr

PLAUSIBLE = {"rainfall": (0.0, 1000.0), "tmax": (-30.0, 55.0), "tmin": (-45.0, 45.0)}

# IMD standard seasons. Winter is Jan-Feb of the same calendar year, so no season spans two years.
SEASONS = {1: "Winter (JF)", 2: "Winter (JF)", 3: "Pre-monsoon (MAM)", 4: "Pre-monsoon (MAM)",
           5: "Pre-monsoon (MAM)", 6: "Monsoon (JJAS)", 7: "Monsoon (JJAS)", 8: "Monsoon (JJAS)",
           9: "Monsoon (JJAS)", 10: "Post-monsoon (OND)", 11: "Post-monsoon (OND)", 12: "Post-monsoon (OND)"}


def qc_variable(da: xr.DataArray, variable: str, mask: xr.DataArray) -> dict:
    """Checks over selected cells only. ``da`` already has source sentinels as NaN."""
    v = da.where(mask)
    n_cells = int(mask.sum())
    n_days = da.sizes["time"]
    total = n_cells * n_days
    valid = v.notnull() & mask
    n_valid = int(valid.sum())
    lo, hi = PLAUSIBLE[variable]
    below = int((v < lo).sum())
    above = int((v > hi).sum())
    cell_missing = (~valid & mask).sum("time")
    always_missing = int(((cell_missing == n_days) & mask).sum())
    day_missing = (~valid & mask).sum(("lat", "lon"))
    days_all_missing = pd.DatetimeIndex(da["time"].values[(day_missing == n_cells).values])
    days_some_missing = int(((day_missing > 0) & (day_missing < n_cells)).sum())
    out = {
        "variable": variable,
        "n_selected_cells": n_cells,
        "n_days": n_days,
        "n_cell_days": total,
        "n_missing_cell_days": total - n_valid,
        "missing_fraction": round((total - n_valid) / total, 6) if total else None,
        "cells_missing_every_day": always_missing,
        "days_missing_all_cells": len(days_all_missing),
        "days_missing_all_cells_list": [str(d.date()) for d in days_all_missing[:100]],
        "days_missing_some_cells": days_some_missing,
        f"values_below_{lo}": below,
        f"values_above_{hi}": above,
        "min": _f(v.min()), "max": _f(v.max()), "mean": _f(v.mean()),
        "filled_or_modified_values": 0,
    }
    if variable == "rainfall":
        out["negative_values"] = below
        allzero = ((v.fillna(0) == 0).all("time") & valid.any("time") & mask)
        out["cells_zero_every_day"] = int(allzero.sum())
        if out["cells_zero_every_day"] and n_days >= 365:
            out["note"] = ("Some cells report 0 mm on every day of a full year or more. In IMD data this "
                           "typically marks edge cells without gauge support; they are kept, not masked.")
    return out


def tmax_tmin_consistency(tmax: xr.DataArray, tmin: xr.DataArray) -> dict:
    tmax, tmin = xr.align(tmax, tmin, join="inner")
    both = tmax.notnull() & tmin.notnull()
    bad = (tmax < tmin) & both
    n_bad = int(bad.sum())
    days = pd.DatetimeIndex(tmax["time"].values[bad.any(("lat", "lon")).values])
    return {"n_compared_cell_days": int(both.sum()), "n_tmax_below_tmin": n_bad,
            "dates_with_tmax_below_tmin": [str(d.date()) for d in days[:100]],
            "action": "flagged only; values not changed"}


def _f(x) -> float | None:
    x = float(x)
    return None if np.isnan(x) else round(x, 4)


def summarise_series(s: pd.Series, variable: str, min_valid_fraction: float = 1.0) -> dict[str, pd.DataFrame]:
    """Monthly, seasonal and annual summaries of a daily series.

    Rainfall totals are reported only for complete periods (a total with
    missing days would be biased low). Temperature means require at least
    ``min_valid_fraction`` valid days. ``n_days``/``n_valid`` are always shown.
    """
    df = s.to_frame("value")
    df["year"] = df.index.year
    df["month"] = df.index.month
    df["season"] = df["month"].map(SEASONS)
    is_rain = variable == "rainfall"
    # Calendar length of each period, so a range that starts or ends mid-period is not reported as complete.
    cal = pd.DataFrame(index=pd.date_range(f"{df['year'].min()}-01-01", f"{df['year'].max()}-12-31", freq="D"))
    cal["year"], cal["month"] = cal.index.year, cal.index.month
    cal["season"] = cal["month"].map(SEASONS)

    def agg(keys):
        g = df.groupby(keys, sort=True)["value"]
        out = pd.DataFrame({"n_days": g.size(), "n_valid": g.count()})
        out["calendar_days"] = cal.groupby(keys).size().reindex(out.index)
        complete = out["n_valid"] == out["calendar_days"]
        if is_rain:
            out["total_mm"] = g.sum(min_count=1).where(complete)
            out["rainy_days_ge_2.5mm"] = g.apply(lambda x: (x >= 2.5).sum()).where(complete)
            out["max_daily_mm"] = g.max()
        else:
            ok = out["n_valid"] >= np.ceil(min_valid_fraction * out["calendar_days"])
            out["mean_degC"] = g.mean().where(ok)
            out["min_degC"] = g.min().where(ok)
            out["max_degC"] = g.max().where(ok)
        return out.reset_index()

    monthly = agg(["year", "month"])
    seasonal = agg(["year", "season"])
    annual = agg(["year"])
    for t in (monthly, seasonal, annual):
        t.insert(0, "variable", variable)
    return {"monthly": monthly, "seasonal": seasonal, "annual": annual}
