"""v10 최종 실험 — (1) 카나리아 게이트 (2) 방어 바스켓. 승자 조합으로 최종 배분 재산출.

(1) 코어 진입 필터: (a)기울기+스트릭 (b)13612W 카나리아 (c)둘 다 AND.
(2) 신호 OFF 구간: SGOV만 vs {SHY,IEF,금} 1개월 모멘텀 1위 회전.
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
import backtests.benchmark_strategies as B  # noqa: E402
import backtests.candidate_strategies as C  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

ASOF = "2026-07-01"
COST = 0.0012
STRESS = [("2000-01-01", "2002-12-31", "닷컴"), ("2007-10-01", "2009-03-31", "GFC"),
          ("2020-02-01", "2020-06-30", "COVID"), ("2022-01-01", "2022-12-31", "2022")]


def pf(x):
    return f"{x:+.1%}" if isinstance(x, float) and x == x else "n/a"


def canary_gate(spine, k=4):
    """공격 4자산 13612W 중 최소 k개 양수면 진입 허용(True). 월말 판정, 일별 ffill."""
    off = ["SPY", "EFA", "EEM", "AGG"]
    P = {a: B.R(a) for a in off}
    me = sorted(B._periods(spine, "M"))
    vals = pd.Series(index=spine, dtype="float64")
    for d in me:
        npos = sum(1 for a in off if C.w13612(P[a], d) > 0)
        vals[d] = 1.0 if npos >= k else 0.0
    vals = vals.reindex(spine).ffill()
    return vals.fillna(1.0).astype(bool)      # 워밍업 이전은 허용(True)


def defensive_basket(spine):
    """SHY/IEF/GLD 1개월 모멘텀 1위 월 회전 NAV."""
    pool = {a: B.R(a) for a in ["SHY", "IEF", "GLD"]}

    def sel(d):
        return max(pool, key=lambda a: C.r1m(pool[a], d))
    nav, _ = C.monthly_rotation(spine, pool, sel, COST)
    return PB.norm(nav)


def switcher(spine, on_sig, off_nav, cost=COST):
    """ON→TQQQ / OFF→off_nav. 신호 1일 지연."""
    tq = PB.norm(B.R("TQQQ").reindex(spine).ffill())
    on = on_sig.reindex(spine).astype(bool).shift(1).fillna(False)
    tr = tq.pct_change().fillna(0.0); orr = PB.norm(off_nav).pct_change().fillna(0.0)
    V, prev, out = 1.0, None, []
    for d in spine:
        want = "ON" if bool(on[d]) else "OFF"
        if prev is not None and want != prev:
            V *= (1 - cost)
        V *= (1 + (tr[d] if want == "ON" else orr[d]))
        out.append((d, V)); prev = want
    s = pd.Series(dict(out)); s.index = pd.DatetimeIndex(s.index)
    return s


def core_nav(spine, gate):
    tq = B.R("TQQQ").reindex(spine).ffill()
    return PB.norm(TP.run_engine(tq, gate, 1.0, (), opts=TP.Opts("none", False, False, False))[0])


def line(nav):
    m = compute(nav)
    cells = [pf(m["CAGR"]), pf(m["MDD"]), f"{m['Sharpe']:.2f}"]
    for s, e, _ in STRESS:
        mm = compute(nav.loc[s:e])
        cells.append(f"{pf(mm['TotalReturn'])}/{pf(mm['MDD'])}")
    return cells


def main():
    spine = B.load("QQQ").loc[:ASOF].index
    qqq = B.load("QQQ").loc[:ASOF]
    trend = v6.trend_signal_v6(qqq, spine, require_rising=True, confirm_days=5)
    canary = canary_gate(spine, k=4)
    both = (trend.astype(bool) & canary)

    print("# v10 최종 실험 — 카나리아 게이트 · 방어 바스켓\n")
    print("> 코어=최종 견고안(무한매수) · 데이터 `*_SYNTH` 1999~2026 · 비용 편도 0.12%.\n")

    # ===== Part 1: 카나리아 게이트 =====
    print("## 1. 카나리아 게이트 (코어 진입 필터 3버전)\n")
    print("| 게이트 | CAGR | MDD | 샤프 | 닷컴 | GFC | COVID | 2022 |")
    print("|---|---:|---:|---:|---|---|---|---|")
    gates = [("(a) 기울기+스트릭(현행)", trend.astype(bool)),
             ("(b) 13612W 카나리아", canary),
             ("(c) 둘 다 (AND)", both)]
    cores = {}
    for lab, g in gates:
        nav = core_nav(spine, g); cores[lab] = nav
        print(f"| {lab} | " + " | ".join(line(nav)) + " |")
    print()

    print("### 교란 — 카나리아 엄격도(4자산 중 k개 양수 요구) × 현행필터 AND\n")
    print("| k(요구 양수) | 단독 CAGR/샤프 | +기울기스트릭 AND CAGR/샤프/MDD |")
    print("|---|---|---|")
    for k in (2, 3, 4):
        can_k = canary_gate(spine, k=k)
        solo = compute(core_nav(spine, can_k))
        comb = compute(core_nav(spine, trend.astype(bool) & can_k))
        print(f"| {k}/4 | {pf(solo['CAGR'])} / {solo['Sharpe']:.2f} | "
              f"{pf(comb['CAGR'])} / {comb['Sharpe']:.2f} / {pf(comb['MDD'])} |")
    print()

    # ===== Part 2: 방어 바스켓 =====
    print("## 2. 방어 바스켓 (신호 OFF 보유자산)\n")
    sgov = PB.norm(B.R("SGOV").reindex(spine).ffill())
    basket = defensive_basket(spine)
    print("### 2a. 방어자산 단독 + 신호 OFF일 연환산\n")
    off_mask = ~trend.reindex(spine).astype(bool)
    def off_ann(nav):
        r = PB.norm(nav).pct_change().reindex(spine)[off_mask].dropna()
        return (1 + r).prod() ** (252 / len(r)) - 1 if len(r) > 2 else float("nan")
    print("| 방어자산 | 전체 CAGR | MDD | 샤프 | **신호 OFF일 연환산** |")
    print("|---|---:|---:|---:|---:|")
    for lab, nav in [("SGOV(현행)", sgov), ("방어 바스켓(SHY/IEF/금)", basket)]:
        m = compute(nav)
        print(f"| {lab} | {pf(m['CAGR'])} | {pf(m['MDD'])} | {m['Sharpe']:.2f} | {pf(off_ann(nav))} |")
    print()

    print("### 2b. 통합 스위처 (ON→TQQQ / OFF→방어자산)\n")
    print("| OFF 보유 | CAGR | MDD | 샤프 | 닷컴 | GFC | COVID | 2022 |")
    print("|---|---:|---:|---:|---|---|---|---|")
    sw_sgov = switcher(spine, trend.astype(bool), sgov)
    sw_bask = switcher(spine, trend.astype(bool), basket)
    for lab, nav in [("SGOV(현행)", sw_sgov), ("방어 바스켓", sw_bask)]:
        print(f"| {lab} | " + " | ".join(line(nav)) + " |")
    print()

    # ===== Part 3: 승자 조합 최종 배분 =====
    print("## 3. 승자 조합 최종 배분\n")
    print("> 승자: 코어 게이트 = **(c) 기울기+스트릭 AND 13612W 카나리아** (일봉 백스톱+월간 breadth), "
          "OFF 보유 = **SGOV 유지**(바스켓 기각). 아래는 이 승자 코어로 재산출.\n")
    _, _, sats = PB.build_sleeves(spine)          # 위성만 사용(코어는 v10 게이트로 교체)
    sat = sats["위성(SOXL포함)"]
    core_v10 = core_nav(spine, both)              # 카나리아-AND 코어
    core_old = cores["(a) 기울기+스트릭(현행)"]
    base_old = PB.blend(core_old, sat, 0.80)
    base = PB.blend(core_v10, sat, 0.80)
    gld = B.R("GLD").reindex(spine).ffill()
    from backtests.mean_reversion import run_strategy
    mr_q = PB.norm(run_strategy("rsi2", "QQQ", 10, 1.0)[0])
    opt1 = B.static_portfolio({"OUR": .85, "GLD": .15}, {"OUR": base, "GLD": gld}, spine, "A")
    opt2 = B.static_portfolio({"OUR": .80, "GLD": .10, "MR": .10},
                              {"OUR": base, "GLD": gld, "MR": mr_q}, spine, "A")
    print("| 배분안 | CAGR | MDD | 샤프 | 닷컴 | GFC | COVID | 2022 |")
    print("|---|---:|---:|---:|---|---|---|---|")
    for lab, nav in [("(참고) 현행 코어 80/20+금15", B.static_portfolio(
                          {"OUR": .85, "GLD": .15}, {"OUR": base_old, "GLD": gld}, spine, "A")),
                     ("A. [v10] 코어68/위성17/금15", opt1),
                     ("B. [v10] 코어64/위성16/금10/MR10", opt2)]:
        print(f"| {lab} | " + " | ".join(line(nav)) + " |")
    print()


if __name__ == "__main__":
    main()
