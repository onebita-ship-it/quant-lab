"""SOXL 왜곡 감사 — B안(50/50+금15)·A안(80/20+금15), 카나리아 코어.

4조건으로 재산출:
  ① 현행         — 전체구간(~2026 상반기), 위성 SOXL 포함
  ② 2025말 절단  — 2026 상반기 제외, 위성 SOXL 포함
  ③ SOXL 제외    — 전체구간, 위성 TQQQ/UPRO만
  ④ 둘 다        — 2025말 절단 + SOXL 제외 = '왜곡을 다 걷어낸 바닥 기대치'

세전 CAGR / 세후+파킹 CAGR / MDD / 샤프 (+ 스트레스 4구간 MDD). MC 반토막은 별도 스크립트.
세후 = 양도세 22% 오버레이(공제 250만/년). 파킹(SGOV)은 카나리아 코어 OFF 구간 룰에 내재.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from backtests import portfolio_backtest as PB  # noqa: E402
import backtests.benchmark_strategies as B  # noqa: E402
from backtests.v10_experiments import canary_gate, core_nav  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

ASOF = "2026-07-01"
CUT = "2025-12-31"
GOLD = 0.15
ALLOCS = [(0.80, "A안 (80/20 + 금15)"), (0.50, "B안 (50/50 + 금15)")]
STRESS = [("2000-01-01", "2002-12-31", "닷컴"), ("2007-10-01", "2009-03-31", "GFC"),
          ("2020-02-01", "2020-06-30", "COVID"), ("2022-01-01", "2022-12-31", "2022")]
# 조건: (라벨, SOXL 포함?, 절단?)
CONDS = [("① 현행", True, False),
         ("② 2025말 절단", True, True),
         ("③ SOXL 제외", False, False),
         ("④ 둘 다 (바닥 기대치)", False, True)]


def pf(x):
    return f"{x:+.1%}" if isinstance(x, float) and x == x else "n/a"


def aftertax_cagr(nav):
    at = PB.aftertax_overlay(nav)
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    return PB.norm(at).iloc[-1] ** (1 / yrs) - 1


def build_full_navs():
    """전체구간 NAV를 (배분, SOXL포함) 4종 생성. 절단은 이 NAV를 슬라이스."""
    spine = B.load("QQQ").loc[:ASOF].index
    qqq = B.load("QQQ").loc[:ASOF]
    trend = v6.trend_signal_v6(qqq, spine, require_rising=True, confirm_days=5).astype(bool)
    gate = trend & canary_gate(spine, k=4)
    core = core_nav(spine, gate)
    _, _, sats = PB.build_sleeves(spine)
    gld = B.R("GLD").reindex(spine).ffill()
    navs = {}
    for soxl, tag in [(True, "위성(SOXL포함)"), (False, "위성(SOXL제외)")]:
        sat = sats[tag]
        for w, _ in ALLOCS:
            risky = PB.blend(core, sat, w)
            nav = B.static_portfolio({"OUR": 1 - GOLD, "GLD": GOLD},
                                     {"OUR": risky, "GLD": gld}, spine, "A")
            navs[(w, soxl)] = PB.norm(nav)
    return navs


def main():
    navs = build_full_navs()

    print("# SOXL 왜곡 감사 — B안(50/50)·A안(80/20), 카나리아 코어 · 금15% 오버레이\n")
    print("> 코어=v10 카나리아 게이트(기울기+스트릭 AND 13612W) · 위성=v9 엔진A · 연 리밸런스 "
          "· 비용 편도 0.12% · 데이터 `*_SYNTH` 1999~\n")
    print("> **④ = 2026 상반기 제외 + SOXL 제외 = 재량·최근급등 왜곡을 다 걷어낸 '바닥 기대치'**\n")

    for w, alab in ALLOCS:
        print(f"## {alab}\n")
        print("| 조건 | 세전 CAGR | 세후+파킹 CAGR | MDD | 샤프 | 닷컴 MDD | GFC | COVID | 2022 |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for clab, soxl, cut in CONDS:
            nav = navs[(w, soxl)]
            if cut:
                nav = PB.norm(nav.loc[:CUT])
            m = compute(nav)
            st = []
            for s, e, _ in STRESS:
                sub = nav.loc[s:e]
                st.append(pf(compute(sub)['MDD']) if len(sub) > 5 else "n/a")
            print(f"| {clab} | {pf(m['CAGR'])} | {pf(aftertax_cagr(nav))} | {pf(m['MDD'])} | "
                  f"{m['Sharpe']:.2f} | " + " | ".join(st) + " |")
        print()


if __name__ == "__main__":
    main()
