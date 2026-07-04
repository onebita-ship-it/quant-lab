"""성과지표 계산: CAGR, MDD, 샤프, 사이클 승률, 최장 하락기간."""
import numpy as np
import pandas as pd


def compute(equity: pd.Series, cycles: list | None = None) -> dict:
    ret = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0

    peak = equity.cummax()
    dd = equity / peak - 1
    mdd = dd.min()

    # 최장 underwater 기간 (전고점 회복까지 걸린 최대 일수)
    underwater = dd < 0
    longest, cur, prev_date = pd.Timedelta(0), pd.Timedelta(0), None
    start = None
    for date, uw in underwater.items():
        if uw and start is None:
            start = date
        elif not uw and start is not None:
            longest = max(longest, date - start)
            start = None
    if start is not None:
        longest = max(longest, underwater.index[-1] - start)

    sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0.0

    out = {
        "CAGR": cagr,
        "MDD": mdd,
        "Sharpe": sharpe,
        "TotalReturn": equity.iloc[-1] / equity.iloc[0] - 1,
        "LongestUnderwaterDays": longest.days,
        "Years": round(years, 2),
    }
    if cycles:
        closed = [c for c in cycles if c.reason != "eof"]
        wins = [c for c in closed if c.pnl_pct > 0]
        out["Cycles"] = len(closed)
        out["WinRate"] = len(wins) / len(closed) if closed else float("nan")
        out["AvgCycleDays"] = np.mean([c.days for c in closed]) if closed else float("nan")
        out["AvgCyclePnl"] = np.mean([c.pnl_pct for c in closed]) if closed else float("nan")
    return out


def fmt(m: dict) -> str:
    lines = []
    for k, v in m.items():
        if k in ("CAGR", "MDD", "TotalReturn", "WinRate", "AvgCyclePnl") and not np.isnan(v):
            lines.append(f"  {k:<22}{v:>10.2%}")
        elif isinstance(v, float):
            lines.append(f"  {k:<22}{v:>10.2f}")
        else:
            lines.append(f"  {k:<22}{v:>10}")
    return "\n".join(lines)
