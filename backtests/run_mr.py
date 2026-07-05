"""단기 평균회귀 검증 → result_mr.md 본문. RSI-2·IBS × QQQ·TQQQ + 상관/편입 제안."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from backtests import portfolio_backtest as PB  # noqa: E402
from backtests import mean_reversion as MR  # noqa: E402
import backtests.benchmark_strategies as B  # noqa: E402

STRESS = [("2000-01-01", "2002-12-31", "닷컴"), ("2007-10-01", "2009-03-31", "GFC"),
          ("2020-02-01", "2020-06-30", "COVID"), ("2022-01-01", "2022-12-31", "2022")]


def pf(x):
    return f"{x:+.1%}" if isinstance(x, float) and x == x else "n/a"


def yearly_cagr_aftertax(nav):
    at = PB.aftertax_overlay(nav)
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    return (PB.norm(at).iloc[-1]) ** (1 / yrs) - 1


def metric_line(name, eq, info):
    m = compute(eq)
    at = yearly_cagr_aftertax(eq)
    return (f"| {name} | {pf(m['CAGR'])} | {pf(m['MDD'])} | {m['Sharpe']:.2f} | "
            f"{info['trades_per_yr']:.1f} | {info['avg_hold']:.1f} | {pf(info['win_rate'])} | "
            f"{pf(at)} |")


def main():
    print("# 단기 평균회귀 검증 — RSI-2 · IBS (QQQ · TQQQ)\n")
    print("> 매일 종가 판정, 롱온리 in/out, 200일선 위에서만 진입. 비용 편도 0.12%, 미보유 현금 SGOV. "
          "IBS-TQQQ는 실 고저가 필요 → 2010~ 실데이터.\n")

    # 기준 구성(대표): RSI-2 thr10/100%, IBS thr0.2/100%
    configs = [("RSI-2 · QQQ (thr10,100%)", "rsi2", "QQQ", 10, 1.0),
               ("RSI-2 · TQQQ (thr10,100%)", "rsi2", "TQQQ_SYNTH", 10, 1.0),
               ("IBS · QQQ (thr0.2,100%)", "ibs", "QQQ", 0.2, 1.0),
               ("IBS · TQQQ (thr0.2,100%)", "ibs", "TQQQ", 0.2, 1.0)]
    results = {}
    for name, kind, tk, thr, frac in configs:
        results[name] = MR.run_strategy(kind, tk, thr, frac)

    # 전체/2013~/2020~ (엣지 감쇠)
    print("## 종합 — 전체 / 2013~ / 2020~ (엣지 감쇠 확인) + 거래통계 + 세후\n")
    for tag, s0 in [("전체구간", None), ("2013~", "2013-01-01"), ("2020~", "2020-01-01")]:
        print(f"### {tag}\n")
        print("| 전략 | CAGR | MDD | 샤프 | 거래/년 | 평균보유일 | 승률 | 세후+파킹 CAGR |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|")
        for name, _, _, _, _ in configs:
            eq, info = results[name]
            sub = eq.loc[s0:] if s0 else eq
            # 서브기간 거래통계는 전체 기준 근사(대표), CAGR/MDD/샤프만 서브
            m = compute(sub); at = yearly_cagr_aftertax(sub)
            print(f"| {name} | {pf(m['CAGR'])} | {pf(m['MDD'])} | {m['Sharpe']:.2f} | "
                  f"{info['trades_per_yr']:.1f} | {info['avg_hold']:.1f} | {pf(info['win_rate'])} | "
                  f"{pf(at)} |")
        print()

    # 스트레스
    print("## 스트레스 4구간 (총수익 / MDD)\n")
    print("| 전략 | " + " | ".join(n for _, _, n in STRESS) + " |")
    print("|---" * (len(STRESS) + 1) + "|")
    for name, _, _, _, _ in configs:
        eq = results[name][0]
        cells = []
        for s, e, _ in STRESS:
            sub = eq.loc[s:e]
            cells.append(f"{pf(compute(sub)['TotalReturn'])} / {pf(compute(sub)['MDD'])}"
                         if len(sub) > 20 else "n/a")
        print(f"| {name} | " + " | ".join(cells) + " |")
    print()

    # 교란 히트맵
    print("## 파라미터 교란 히트맵 (CAGR / 샤프)\n")
    print("### RSI-2 · QQQ — 임계 × 투입비중\n")
    print("| RSI임계\\투입 | 100% | 50% |")
    print("|---|---|---|")
    for thr in (5, 10, 15):
        cells = []
        for frac in (1.0, 0.5):
            eq, _ = MR.run_strategy("rsi2", "QQQ", thr, frac)
            m = compute(eq)
            cells.append(f"{pf(m['CAGR'])} / {m['Sharpe']:.2f}")
        print(f"| {thr} | " + " | ".join(cells) + " |")
    print("\n### IBS · QQQ — 임계 × 투입비중\n")
    print("| IBS임계\\투입 | 100% | 50% |")
    print("|---|---|---|")
    for thr in (0.1, 0.2, 0.3):
        cells = []
        for frac in (1.0, 0.5):
            eq, _ = MR.run_strategy("ibs", "QQQ", thr, frac)
            m = compute(eq)
            cells.append(f"{pf(m['CAGR'])} / {m['Sharpe']:.2f}")
        print(f"| {thr} | " + " | ".join(cells) + " |")
    print()

    # 상관 분석
    print("## 상관 분석 — MR 일별수익 vs 코어·위성\n")
    spine = MR.load_ohlc("QQQ").loc[:"2026-07-01"].index
    core, _, sats = PB.build_sleeves(spine)
    sat = sats["위성(SOXL포함)"]
    cr = PB.norm(core).pct_change(); sr = PB.norm(sat).pct_change()
    print("| MR 전략 | vs 코어 | vs 위성 |")
    print("|---|---:|---:|")
    for name, _, _, _, _ in configs:
        eq = PB.norm(results[name][0]).reindex(spine).ffill()
        mr = eq.pct_change()
        both = pd.concat([mr, cr, sr], axis=1).dropna()
        print(f"| {name} | {both.corr().iloc[0,1]:.2f} | {both.corr().iloc[0,2]:.2f} |")
    print("\n→ 상관이 낮을수록(≈0) 포트폴리오 분산 효과 큼.\n")

    # 3번째 슬리브 편입 제안
    print("## 3번째 슬리브 편입 제안 (코어/추세위성/평균회귀 ± 금)\n")
    mr_q = PB.norm(results["RSI-2 · QQQ (thr10,100%)"][0])
    mr_t = PB.norm(results["RSI-2 · TQQQ (thr10,100%)"][0])
    base_8020 = PB.blend(core, sat, 0.80)
    gld = MR.load_ohlc("GLD_SYNTH")["Close"].reindex(spine).ffill()
    gold15 = B.static_portfolio({"OUR": .85, "GLD": .15}, {"OUR": base_8020, "GLD": gld}, spine, "A")
    goldmr = B.static_portfolio({"OUR": .80, "GLD": .10, "MR": .10},
                                 {"OUR": base_8020, "GLD": gld, "MR": mr_q}, spine, "A")
    print("| 구성 | CAGR | MDD | 샤프 | 닷컴 | GFC | 2022 |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    rows = [("① 80/20 (MR 없음)", base_8020),
            ("② 72/18/10 (+MR QQQ 10%)", PB.blend(base_8020, mr_q, 0.90)),
            ("③ 72/18/10 (+MR TQQQ 10%)", PB.blend(base_8020, mr_t, 0.90)),
            ("④ 80/20 + 금 15%(참고)", gold15),
            ("⑤ 68/17/금10/MR10(복합)", goldmr),
            ("· 100% MR(RSI-2 QQQ)", mr_q)]
    for lab, nav in rows:
        m = compute(nav)
        dot = compute(nav.loc["2000-01-01":"2002-12-31"])["TotalReturn"]
        gfc = compute(nav.loc["2007-10-01":"2009-03-31"])["TotalReturn"]
        y22 = compute(nav.loc["2022-01-01":"2022-12-31"])["TotalReturn"]
        print(f"| {lab} | {pf(m['CAGR'])} | {pf(m['MDD'])} | {m['Sharpe']:.2f} | "
              f"{pf(dot)} | {pf(gfc)} | {pf(y22)} |")
    print()


if __name__ == "__main__":
    main()
