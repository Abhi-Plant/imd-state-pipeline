"""Select the requested date range and account for every requested day."""

from __future__ import annotations

from datetime import date

import pandas as pd
import xarray as xr


class DuplicateDates(ValueError):
    pass


def requested_years(start: date, end: date) -> list[int]:
    return list(range(start.year, end.year + 1))


def combine_years(parts: list[xr.DataArray]) -> xr.DataArray:
    """Concatenate yearly arrays; identical duplicate dates are dropped, conflicting ones raise."""
    if not parts:
        raise ValueError("no data to combine")
    da = xr.concat(sorted(parts, key=lambda p: p["time"].values[0]), dim="time")
    idx = pd.DatetimeIndex(da["time"].values)
    dup = idx.duplicated(keep="first")
    if dup.any():
        for d in idx[dup].unique():
            block = da.sel(time=d)
            if not all(block.isel(time=0).equals(block.isel(time=i)) for i in range(1, block.sizes["time"])):
                raise DuplicateDates(f"date {d.date()} appears in more than one input with different values")
        da = da.isel(time=~dup)
    return da


def slice_time(da: xr.DataArray, start: date, end: date) -> tuple[xr.DataArray, dict]:
    out = da.sel(time=slice(pd.Timestamp(start), pd.Timestamp(end)))
    expected = pd.date_range(start, end, freq="D")
    present = pd.DatetimeIndex(out["time"].values)
    missing = expected.difference(present)
    report = {
        "requested_start": str(start),
        "requested_end": str(end),
        "available_start": str(present.min().date()) if len(present) else None,
        "available_end": str(present.max().date()) if len(present) else None,
        "expected_days": len(expected),
        "present_days": len(present),
        "missing_days_count": len(missing),
        "missing_day_ranges": _ranges(missing),
        "duplicate_dates": int(present.duplicated().sum()),
        "regular_daily_steps": bool(len(present) < 2 or (present.to_series().diff().dropna() == pd.Timedelta("1D")).all()),
        "leap_days_present": int(((present.month == 2) & (present.day == 29)).sum()),
        "leap_days_expected": int(((expected.month == 2) & (expected.day == 29)).sum()),
        "time_zone": "dates as labelled by IMD (IST); no time-zone shift applied",
    }
    return out, report


def _ranges(idx: pd.DatetimeIndex) -> list[list[str]]:
    if len(idx) == 0:
        return []
    out, start, prev = [], idx[0], idx[0]
    for d in idx[1:]:
        if d - prev != pd.Timedelta("1D"):
            out.append([str(start.date()), str(prev.date())])
            start = d
        prev = d
    out.append([str(start.date()), str(prev.date())])
    return out
