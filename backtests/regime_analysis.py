"""QQQ 1999~2026 국면 분류 + 2009 이전/이후 통계.

국면(3): 200일선 + 20일 실현변동성(연율)로 상호배타 분류.
  - 고변동(High-vol): 실현변동성 > 전체표본 75%ile  (최우선)
  - 강세(Bull)      : 그 외 & 종가 >= 200일선
  - 약세(Bear)      : 그 외 & 종가 < 200일선

산출: 국면별 체류비중·평균 지속일·일일 전환행렬, 하락장(고점-저점) 깊이·회복속도.
2009-01-01 기준 이전(1999~2008)/이후(2009~2026)로 나눠 비교.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
SPLIT = "2009-01-01"
REG = ["강세", "약세", "고변동"]


def load(t):
    return pd.read_csv(DATA / f"{t}.csv", index_col="Date", parse_dates=True)["Close"].dropna()


def classify(qqq):
    ma = qqq.rolling(200).mean()
    vol = qqq.pct_change().rolling(20).std() * np.sqrt(252)
    thr = vol.quantile(0.75)
    reg = pd.Series(index=qqq.index, dtype=object)
    for d in qqq.index:
        v, m, p = vol.get(d), ma.get(d), qqq.get(d)
        if pd.isna(m) or pd.isna(v):
            reg[d] = np.nan
        elif v > thr:
            reg[d] = "고변동"
        elif p >= m:
            reg[d] = "강세"
        else:
            reg[d] = "약세"
    return reg.dropna(), thr, vol


def episode_durations(reg):
    """연속 동일 국면 런의 길이 리스트(국면별)."""
    runs = {r: [] for r in REG}
    if len(reg) == 0:
        return runs
    cur, n = reg.iloc[0], 1
    for r in reg.iloc[1:]:
        if r == cur:
            n += 1
        else:
            runs[cur].append(n); cur, n = r, 1
    runs[cur].append(n)
    return runs


def transition_matrix(reg):
    idx = {r: i for i, r in enumerate(REG)}
    M = np.zeros((3, 3))
    vals = reg.values
    for a, b in zip(vals[:-1], vals[1:]):
        M[idx[a], idx[b]] += 1
    row = M.sum(axis=1, keepdims=True)
    P = np.divide(M, row, out=np.zeros_like(M), where=row > 0)
    return P


def drawdowns(qqq, decline=0.20, rebound=0.20):
    """지그재그(±20% 반전)로 '서로 다른' 하락장을 분리 검출.

    고점에서 -decline% 하락하면 하락장 시작, 저점에서 +rebound% 반등하면 하락장 종료.
    → 닷컴·GFC가 각각 별개 에피소드로 잡힌다(ATH 기준이면 닷컴 미회복에 GFC가 묻힘).
    회복일 = 저점→직전 고점가 재돌파까지(완전회복). 미회복이면 표본끝까지.
    """
    vals = list(qqq.items())
    eps = []
    peak_d, peak = vals[0]
    trough_d, trough = vals[0]
    state = "up"
    for d, p in vals:
        if state == "up":
            if p >= peak:
                peak, peak_d = p, d
            elif p <= peak * (1 - decline):
                state, trough, trough_d = "down", p, d
        else:
            if p <= trough:
                trough, trough_d = p, d
            elif p >= trough * (1 + rebound):
                eps.append(_finish_ep(qqq, peak, peak_d, trough, trough_d))
                state = "up"; peak, peak_d = p, d
    if state == "down":
        eps.append(_finish_ep(qqq, peak, peak_d, trough, trough_d))
    return eps


def _finish_ep(qqq, peak, peak_d, trough, trough_d):
    after = qqq.loc[trough_d:]
    regain = after[after >= peak]
    if len(regain):
        rec_d = regain.index[0]; recovered = True
    else:
        rec_d = qqq.index[-1]; recovered = False
    return {"peak_d": peak_d, "trough_d": trough_d, "depth": trough / peak - 1,
            "decline_days": (trough_d - peak_d).days,
            "recover_days": (rec_d - trough_d).days, "recovered": recovered}


def stats_block(reg, qqq, label):
    print(f"### {label}\n")
    n = len(reg)
    print("**국면별 체류비중 · 평균 지속일**\n")
    print("| 국면 | 체류비중 | 에피소드수 | 평균 지속(거래일) | 최장 |")
    print("|---|---:|---:|---:|---:|")
    runs = episode_durations(reg)
    for r in REG:
        share = (reg == r).sum() / n
        rr = runs[r]
        avg = np.mean(rr) if rr else 0
        mx = max(rr) if rr else 0
        print(f"| {r} | {share:.1%} | {len(rr)} | {avg:.0f} | {mx} |")
    print("\n**일일 전환행렬 P(내일 열 | 오늘 행)**\n")
    P = transition_matrix(reg)
    print("| 오늘\\내일 | " + " | ".join(REG) + " | 기대체류(일) |")
    print("|---|" + "---:|" * 4)
    for i, r in enumerate(REG):
        stay = 1 / (1 - P[i, i]) if P[i, i] < 1 else float("inf")
        print(f"| {r} | " + " | ".join(f"{P[i,j]:.1%}" for j in range(3)) +
              f" | {stay:.0f} |")
    print()


def main():
    qqq = load("QQQ")
    reg, thr, vol = classify(qqq)
    print("# QQQ 국면 분류 & 2009 이전/이후 통계 (1999~2026)\n")
    print(f"> 분류: 고변동=20일 실현변동성(연율) > {thr:.0%}(전체 75%ile) 최우선, "
          "그 외 강세(≥200일선)/약세(<200일선)\n")

    print("## 1. 국면 통계\n")
    stats_block(reg, qqq, "전체 (1999~2026)")
    stats_block(reg.loc[:SPLIT], qqq.loc[:SPLIT], "2009 이전 (1999~2008)")
    stats_block(reg.loc[SPLIT:], qqq.loc[SPLIT:], "2009 이후 (2009~2026)")

    print("## 2. 하락장 깊이 · 회복속도 (지그재그 ±20%로 분리한 하락장)\n")
    eps = drawdowns(qqq)
    print("| 고점일 | 저점일 | 깊이 | 하락(일) | 회복(일) | 회복됨 | 시대 |")
    print("|---|---|---:|---:|---:|:--:|---|")
    for e in eps:
        era = "이전" if e["trough_d"] < pd.Timestamp(SPLIT) else "이후"
        rec = "✅" if e["recovered"] else "미회복"
        print(f"| {e['peak_d'].date()} | {e['trough_d'].date()} | {e['depth']:.1%} | "
              f"{e['decline_days']} | {e['recover_days']} | {rec} | {era} |")

    print("\n**시대별 하락장 요약 (≥20%)**\n")
    print("| 시대 | 하락장 수 | 평균 깊이 | 최악 깊이 | 평균 하락일 | 평균 회복일(회복분만) |")
    print("|---|---:|---:|---:|---:|---:|")
    for era, lo, hi in [("2009 이전", "1999", SPLIT), ("2009 이후", SPLIT, "2027")]:
        sub = [e for e in eps if pd.Timestamp(lo) <= e["trough_d"] < pd.Timestamp(hi)]
        if not sub:
            print(f"| {era} | 0 | — | — | — | — |"); continue
        depths = [e["depth"] for e in sub]
        dec = [e["decline_days"] for e in sub]
        rec = [e["recover_days"] for e in sub if e["recovered"]]
        print(f"| {era} | {len(sub)} | {np.mean(depths):.1%} | {min(depths):.1%} | "
              f"{np.mean(dec):.0f} | {(np.mean(rec) if rec else float('nan')):.0f} |")
    print()


if __name__ == "__main__":
    main()
