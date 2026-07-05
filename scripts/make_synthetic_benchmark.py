"""벤치마크용 합성 자산 (QQQ 거래일 스파인, 1999~) — 채권/금/해외.

- TLT_SYNTH : 20년 국채. ^TNX(10년 수익률) 듀레이션 모델 + 실 TLT(2002~).
- IEF_SYNTH : 7~10년 국채. 듀레이션 7.5 + 실 IEF(2002~).
- AGG_SYNTH : 종합채권(듀레이션 6 근사, 국채로 대체) + 실 AGG(2003~).
- TMF_SYNTH : 3x 장기국채 = 3×TLT − 드래그 + 실 TMF(2009~).
- GLD_SYNTH : 금. GC=F(2000-08~) + 실 GLD(2004~), 그 이전은 플랫.
- EFA_SYNTH : 선진 해외주식. 실 EFA(2001-08~), 그 이전은 플랫.

채권 총수익 근사: TR ≈ (전일수익률/252) − 듀레이션×Δ수익률 (^TNX를 공통 금리동인으로).
결과: data/{TLT,IEF,AGG,TMF,GLD,EFA}_SYNTH.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
TMF_EXP, BORROW = 0.0106, 0.02
TMF_DRAG = TMF_EXP / 252 + BORROW * 2 / 252


def load(t):
    return pd.read_csv(DATA / f"{t}.csv", index_col="Date", parse_dates=True)["Close"].dropna()


def splice(spine, synth_ret, real_close=None, base=100.0):
    r = synth_ret.reindex(spine)
    if real_close is not None:
        rr = real_close.pct_change().reindex(spine)
        r = r.where(rr.isna(), rr)
    r = r.fillna(0.0)
    return base * (1 + r).cumprod()


def bond_tr(y, dur):
    """y: 수익률(소수) 스파인 정렬. TR = 전일수익률/252 − dur×Δy."""
    dy = y.diff()
    return y.shift(1) / 252.0 - dur * dy


def save(name, s, note):
    df = s.to_frame("Close"); df.index.name = "Date"
    df.dropna().to_csv(DATA / f"{name}.csv")
    print(f"[ok] {name}: {note} | {s.dropna().index[0].date()}~{s.dropna().index[-1].date()}")


def main():
    qqq = load("QQQ"); spine = qqq.index
    y = (load("TNX") / 100.0).reindex(spine).ffill()      # 10년 수익률(소수)
    tr17 = bond_tr(y, 17.0); tr75 = bond_tr(y, 7.5); tr6 = bond_tr(y, 6.0)

    save("TLT_SYNTH", splice(spine, tr17, load("TLT")), "TNX듀레17 + 실TLT")
    save("IEF_SYNTH", splice(spine, tr75, load("IEF")), "TNX듀레7.5 + 실IEF")
    save("AGG_SYNTH", splice(spine, tr6, load("AGG")), "TNX듀레6 + 실AGG")
    save("TMF_SYNTH", splice(spine, 3 * tr17 - TMF_DRAG, load("TMF")), "3×TLT−드래그 + 실TMF")
    save("GLD_SYNTH", splice(spine, load("GC_F").pct_change(), load("GLD")), "GC=F + 실GLD(이전 플랫)")
    save("EFA_SYNTH", splice(spine, load("EFA").pct_change()), "실EFA(2001~, 이전 플랫)")

    # 검증: 실물 겹치는 구간 상관
    for nm, real in [("TLT_SYNTH", "TLT"), ("TMF_SYNTH", "TMF"), ("GLD_SYNTH", "GLD")]:
        syn = load(nm); rl = load(real)
        both = pd.concat([syn.pct_change(), rl.pct_change()], axis=1).dropna()
        print(f"  [검증] {nm} vs {real} 일수익률 상관 {both.corr().iloc[0,1]:.3f}")


if __name__ == "__main__":
    main()
