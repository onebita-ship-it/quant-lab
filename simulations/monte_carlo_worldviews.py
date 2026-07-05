"""3가지 세계관 몬테카를로 — 최종 견고안 vs TQQQ Buy&Hold, 5년 분포.

레짐 스위칭 생성기(2상태: 강세=종가≥200MA / 약세=<200MA)로 QQQ 경로를 만든다.
상태별 '실제 일수익률 풀'에서 가중 샘플링(팻테일 보존) + 상태전이 마르코프.

세계관(같은 생성기, 파라미터만 다름 → 사과 대 사과):
  ① 과거 전체 균등   : 전체표본 균등가중으로 전이·수익 추정.
  ② 최근10년 지수가중 : 최근일수록 큰 지수가중(반감기 3년)으로 추정 → 최근(저곰장) 국면 강조.
  ③ 새 시대(레짐전환): ①에서 P(강세→약세)×0.5(곰장 전환확률 절반), P(약세→강세)×2(회복 2배).

각 세계관에서 최종 견고안(기울기+스트릭5·40분할·익절15%·쿼터손절, 100%)과 TQQQ B&H의
5년 총수익 분포·MDD·손실확률 비교. + '새 시대 맞을 때 vs 틀릴 때' 2×2 매트릭스.

[한계] 과거 상태별 수익분포 유지 가정. 2상태 근사(고변동은 각 상태에 흡수).
사용: python simulations/monte_carlo_worldviews.py --years 5 --paths 2000
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests import tax_parking_backtest as TP  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

DATA = ROOT / "data"
EXP, BORROW, LEV = 0.0095, 0.02, 3.0
DRAG = EXP / 252 + BORROW * (LEV - 1) / 252
WARMUP = 250


def estimate(qqq, weights):
    ma = qqq.rolling(200).mean()
    state = (qqq < ma).astype(float)      # 0 강세, 1 약세
    ret = qqq.pct_change()
    df = pd.DataFrame({"s": state, "r": ret, "w": weights}).dropna()
    s = df["s"].values.astype(int); r = df["r"].values; w = df["w"].values
    P = np.zeros((2, 2))
    for i in range(len(s) - 1):
        P[s[i], s[i + 1]] += w[i]
    P /= P.sum(1, keepdims=True)
    pools = {}
    for st in (0, 1):
        m = s == st
        rw = w[m] / w[m].sum()
        pools[st] = (r[m], rw)
    return P, pools


def new_era(P):
    """곰장 전환확률 절반 + 회복 2배."""
    Q = P.copy()
    Q[0, 1] = P[0, 1] * 0.5
    Q[0, 0] = 1 - Q[0, 1]
    Q[1, 0] = min(0.99, P[1, 0] * 2.0)
    Q[1, 1] = 1 - Q[1, 0]
    return Q


def sim_returns(P, pools, n_total, n_paths, rng):
    states = np.zeros(n_paths, dtype=int)
    R = np.empty((n_paths, n_total))
    br, bw = pools[0]; kr, kw = pools[1]
    for t in range(n_total):
        u = rng.random(n_paths)
        bull = states == 0
        nxt = states.copy()
        nxt[bull & (u < P[0, 1])] = 1
        nxt[(~bull) & (u < P[1, 0])] = 0
        states = nxt
        b0 = states == 0
        n0 = int(b0.sum())
        r = np.empty(n_paths)
        if n0:
            r[b0] = rng.choice(br, size=n0, p=bw)
        if n_paths - n0:
            r[~b0] = rng.choice(kr, size=n_paths - n0, p=kw)
        R[:, t] = r
    return R


def run_world(name, P, pools, args, rng, dates):
    full_dates, trade_dates = dates
    R = sim_returns(P, pools, WARMUP + args.years * 252, args.paths, rng)
    filt_r, filt_m, bh_r, bh_m = [], [], [], []
    p = TP.Opts("none", False, False, False)
    for i in range(args.paths):
        fr = R[i]
        qc = pd.Series(100 * np.cumprod(1 + fr), index=full_dates)
        trend = v6.trend_signal_v6(qc, trade_dates, require_rising=True, confirm_days=5)
        tq = pd.Series(100 * np.cumprod(1 + (LEV * fr[WARMUP:] - DRAG)), index=trade_dates)
        eq, cyc, _ = TP.run_engine(tq, trend, 1.0, (), opts=p)
        filt_r.append(eq.iloc[-1] / eq.iloc[0] - 1)
        filt_m.append((eq / eq.cummax() - 1).min())
        bh_r.append(tq.iloc[-1] / tq.iloc[0] - 1)
        bh_m.append((tq / tq.cummax() - 1).min())
    return {"filt": (np.array(filt_r), np.array(filt_m)),
            "bh": (np.array(bh_r), np.array(bh_m))}


def summ(r, m, years):
    q = lambda a, x: float(np.percentile(a, x))  # noqa: E731
    med = q(r, 50)
    return {"cagr": (1 + med) ** (1 / years) - 1, "med": med, "p5": q(r, 5),
            "p95": q(r, 95), "mdd": q(m, 50), "mdd5": q(m, 5),
            "loss": float(np.mean(r < 0)), "half": float(np.mean(r <= -0.5))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--paths", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--halflife", type=float, default=756.0, help="②지수가중 반감기(거래일)")
    args = ap.parse_args()

    qqq = pd.read_csv(DATA / "QQQ.csv", index_col="Date", parse_dates=True)["Close"].dropna()
    n = len(qqq)
    w_uniform = pd.Series(1.0, index=qqq.index)
    tau = args.halflife / np.log(2)
    w_exp = pd.Series(np.exp(-(np.arange(n)[::-1]) / tau), index=qqq.index)

    P1, pools1 = estimate(qqq, w_uniform)
    P2, pools2 = estimate(qqq, w_exp)
    P3 = new_era(P1)

    full_dates = pd.bdate_range("2100-01-01", periods=WARMUP + args.years * 252)
    dates = (full_dates, full_dates[WARMUP:])
    rng = np.random.default_rng(args.seed)

    def ann_bear(P):  # 강세→약세 확률 & 약세 기대지속
        return P[0, 1], (1 / P[1, 0] if P[1, 0] > 0 else float("inf"))

    print(f"# 3가지 세계관 몬테카를로 (최종 견고안 vs TQQQ B&H, {args.years}년×{args.paths}경로)\n")
    print("> 레짐 스위칭 생성기(강세=종가≥200MA / 약세=<200MA), 상태별 실제 일수익률 가중샘플링.\n")
    print("**세계관별 레짐 파라미터**\n")
    print("| 세계관 | P(강세→약세)/일 | 약세 기대지속(일) | 강세 기대지속(일) |")
    print("|---|---:|---:|---:|")
    for nm, P in [("① 과거 전체 균등", P1), ("② 최근10년 지수가중", P2), ("③ 새 시대(레짐전환)", P3)]:
        pbb = P[0, 1]; bear_dur = 1 / P[1, 0]; bull_dur = 1 / P[0, 1]
        print(f"| {nm} | {pbb:.3%} | {bear_dur:.0f} | {bull_dur:.0f} |")
    print()

    worlds = [("① 과거 전체 균등", P1, pools1), ("② 최근10년 지수가중", P2, pools2),
              ("③ 새 시대(레짐전환)", P3, pools1)]
    results = {}
    for nm, P, pools in worlds:
        results[nm] = run_world(nm, P, pools, args, rng, dates)
        print(f"  [done] {nm}", file=sys.stderr)

    # 분포 비교
    print("## 세계관별 5년 분포\n")
    print("| 세계관 | 전략 | CAGR중앙 | 총수익중앙 | 5%ile | MDD중앙 | 손실확률 | 반토막확률 |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    pf = lambda x: f"{x:.1%}"  # noqa: E731
    for nm, _, _ in worlds:
        for tag, lab in [("filt", "최종 견고안"), ("bh", "TQQQ B&H")]:
            r, m = results[nm][tag]; s = summ(r, m, args.years)
            print(f"| {nm} | {lab} | {pf(s['cagr'])} | {pf(s['med'])} | {pf(s['p5'])} | "
                  f"{pf(s['mdd'])} | {pf(s['loss'])} | {pf(s['half'])} |")
    print()

    # 2×2 매트릭스
    correct = "③ 새 시대(레짐전환)"; wrong = "① 과거 전체 균등"
    print("## '새 시대 가정이 맞을 때 vs 틀릴 때' 2×2 (CAGR중앙 / MDD중앙 / 반토막확률)\n")
    print("| | 필터 전략(최종 견고안) | 필터 없음(TQQQ B&H) |")
    print("|---|---|---|")
    for row_lab, world in [("**새 시대 맞음**(③)", correct), ("**새 시대 틀림**(① 과거처럼)", wrong)]:
        cells = []
        for tag in ("filt", "bh"):
            r, m = results[world][tag]; s = summ(r, m, args.years)
            cells.append(f"{pf(s['cagr'])} / {pf(s['mdd'])} / 반토막 {pf(s['half'])}")
        print(f"| {row_lab} | {cells[0]} | {cells[1]} |")
    print("\n[주의] 과거 상태별 수익분포 유지 가정. 2상태 근사. 레짐전환은 전이확률만 바꿨을 뿐 "
          "상태 내 수익 크기는 과거 그대로(새 시대가 '더 순한 곰장'까지면 낙관 과소).")


if __name__ == "__main__":
    main()
