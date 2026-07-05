"""룰북 최종 검수 (1) — 카나리아 게이트 실데이터 교차검증 (2004~2026).

카나리아 공격 4자산 중 EFA(2001~)·EEM(2003~)·AGG(2003~)가 전부 '실데이터'인 구간에서
카나리아-AND 게이트 vs 기존(기울기+스트릭) 게이트 비교. 합성 프록시 의존 없이 재확인.
(코어 매매자산 TQQQ는 2004~2010만 합성 — 신호 검증이 목적이므로 무방.)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from backtests import tax_parking_backtest as TP  # noqa: E402
from backtests import portfolio_backtest as PB  # noqa: E402
import backtests.candidate_strategies as C  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

DATA = ROOT / "data"
ASOF = "2026-07-01"


def load_real(t):
    return pd.read_csv(DATA / f"{t}.csv", index_col="Date", parse_dates=True)["Close"].dropna()


def real_canary(spine, k=4):
    """공격 4자산(실물) 13612W 전부 양수면 True. 데이터 부족 자산은 판정 제외(허용)."""
    off = ["SPY", "EFA", "EEM", "AGG"]
    P = {a: load_real(a).reindex(spine).ffill() for a in off}
    raw = {a: load_real(a) for a in off}
    me = sorted(__import__("backtests.benchmark_strategies", fromlist=["_periods"])._periods(spine, "M"))
    vals = pd.Series(index=spine, dtype="float64")
    for d in me:
        block = False
        for a in off:
            h = raw[a].loc[:d]
            if len(h) >= 253 and C.w13612(h, d) < 0:
                block = True
        vals[d] = 0.0 if block else 1.0
    return vals.reindex(spine).ffill().fillna(1.0).astype(bool)


def core_nav(spine, gate):
    tq = pd.read_csv(DATA / "TQQQ_SYNTH.csv", index_col="Date", parse_dates=True)["Close"].reindex(spine).ffill()
    return PB.norm(TP.run_engine(tq, gate, 1.0, (), opts=TP.Opts("none", False, False, False))[0])


def pf(x):
    return f"{x:+.1%}" if isinstance(x, float) and x == x else "n/a"


def seg(nav, s, e=None):
    return compute(nav.loc[s:e] if e else nav.loc[s:])


def main():
    spine = load_real("QQQ").loc[:ASOF].index
    qqq = load_real("QQQ").loc[:ASOF]
    trend = v6.trend_signal_v6(qqq, spine, require_rising=True, confirm_days=5).astype(bool)
    canary = real_canary(spine, k=4)
    both = trend & canary

    core_a = core_nav(spine, trend)
    core_c = core_nav(spine, both)

    print("### 부록 A. 카나리아 게이트 실데이터 교차검증 (2004~2026, EFA/EEM/AGG 전부 실데이터)\n")
    print("| 구간 | 게이트 | CAGR | MDD | 샤프 |")
    print("|---|---|---:|---:|---:|")
    windows = [("2004~2026 전체", "2004-01-01", None), ("2013~2026", "2013-01-01", None),
               ("GFC 2007-10~2009-03", "2007-10-01", "2009-03-31"),
               ("COVID 2020-02~2020-06", "2020-02-01", "2020-06-30"),
               ("2022 약세장", "2022-01-01", "2022-12-31")]
    for wlab, s, e in windows:
        for glab, nav in [("(a) 기존", core_a), ("(c) +카나리아 AND", core_c)]:
            m = seg(nav, s, e)
            tag = " · ".join([f"{pf(m['TotalReturn'])}" if e else f"{pf(m['CAGR'])}"])
            print(f"| {wlab} | {glab} | "
                  f"{pf(m['CAGR']) if not e else pf(m['TotalReturn'])+'(총)'} | "
                  f"{pf(m['MDD'])} | {m['Sharpe']:.2f} |")
    print()
    # 카나리아 발동(진입차단) 통계
    off_days = (~canary.loc["2004-01-01":]).mean()
    print(f"> 2004~2026 카나리아 '진입 차단' 비중: 전체 거래일의 **{off_days:.0%}**. "
          "(공격 4자산 중 하나라도 13612W 음수인 날)\n")


if __name__ == "__main__":
    main()
