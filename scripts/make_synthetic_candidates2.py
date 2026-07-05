"""후보 전략용 합성 자산 — 채권(LQD/SHY/TIP)·해외(VEU/EEM/SCZ). QQQ 스파인, 1999~.

채권: ^TNX/^FVX 듀레이션 모델 + 실물 스플라이스(신용/실질금리 무시 근사).
  - LQD_SYNTH : 회사채, 듀레이션 8.5 (^TNX)
  - SHY_SYNTH : 1~3년 국채, 듀레이션 1.9 (^FVX 5년)
  - TIP_SYNTH : 물가채, 듀레이션 7 (^TNX 명목 근사)
해외주식: 실물 + 상장 이전은 **EFA(선진 해외)로 프록시**, EFA 이전(2001)은 플랫.
  - VEU_SYNTH : 전세계 ex-US (EFA 프록시가 양호)
  - EEM_SYNTH : 신흥국 (EFA 프록시는 거침 — 표에 합성 명시)
  - SCZ_SYNTH : 해외 소형 (EFA 프록시는 거침 — 표에 합성 명시)

결과: data/{LQD,SHY,TIP,VEU,EEM,SCZ}_SYNTH.csv
"""
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"


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
    dy = y.diff()
    return y.shift(1) / 252.0 - dur * dy


def save(name, s, note):
    df = s.to_frame("Close"); df.index.name = "Date"
    df.dropna().to_csv(DATA / f"{name}.csv")
    print(f"[ok] {name}: {note} | {s.dropna().index[0].date()}~{s.dropna().index[-1].date()}")


def main():
    spine = load("QQQ").index
    ytnx = (load("TNX") / 100.0).reindex(spine).ffill()
    yfvx = (load("FVX") / 100.0).reindex(spine).ffill()
    efa = load("EFA")               # 해외 프록시(실물 2001~, 이전 플랫)

    save("LQD_SYNTH", splice(spine, bond_tr(ytnx, 8.5), load("LQD")), "TNX듀레8.5 + 실LQD")
    save("SHY_SYNTH", splice(spine, bond_tr(yfvx, 1.9), load("SHY")), "FVX듀레1.9 + 실SHY")
    save("TIP_SYNTH", splice(spine, bond_tr(ytnx, 7.0), load("TIP")), "TNX듀레7 + 실TIP")
    save("VEU_SYNTH", splice(spine, efa.pct_change(), load("VEU")), "EFA프록시 + 실VEU(2007~)")
    save("EEM_SYNTH", splice(spine, efa.pct_change(), load("EEM")), "EFA프록시 + 실EEM(2003~)")
    save("SCZ_SYNTH", splice(spine, efa.pct_change(), load("SCZ")), "EFA프록시 + 실SCZ(2007~)")

    for nm, real in [("LQD_SYNTH", "LQD"), ("TIP_SYNTH", "TIP"), ("VEU_SYNTH", "VEU")]:
        both = pd.concat([load(nm).pct_change(), load(real).pct_change()], axis=1).dropna()
        print(f"  [검증] {nm} vs {real} 상관 {both.corr().iloc[0,1]:.3f}")


if __name__ == "__main__":
    main()
