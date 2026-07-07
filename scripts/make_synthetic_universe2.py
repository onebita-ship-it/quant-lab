"""유니버스 확장 감사용 합성 3배 ETF 생성 (result_universe2.md 입력).

후보 5종을 원지수(1배 ETF)에서 3배 합성 + 실물 스플라이스 (SOXL_SYNTH와 동일 방법론):

    synth_ret = 3 × 원지수_ret − EXPENSE/252 − BORROW×2/252   (EXP 0.95%, BORROW 2%)

  - KORU_SYNTH : 한국 3x   ← EWY (2000-05~) + 실 KORU(2013~)
  - TECL_SYNTH : 기술 3x   ← XLK (1998-12~) + 실 TECL(2008-12~)
  - TNA_SYNTH  : 소형주 3x ← IWM (2000-05~) + 실 TNA(2008-11~)
  - EDC_SYNTH  : 신흥국 3x ← EEM (2003-04~) + 실 EDC(2008-12~)
  - YINN_SYNTH : 중국 3x   ← FXI (2004-10~) + 실 YINN(2009-12~)

원지수 상장 이전은 NaN(합성 안 함 — SOXL의 SOXX 전례). 검증: 겹침 구간에서
'순수 합성(3x 모델)' vs 실물 일수익률 상관 — 모델이 실물을 얼마나 재현하는지.
결과: data/{KORU,TECL,TNA,EDC,YINN}_SYNTH.csv
"""
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
EXPENSE, BORROW, LEV = 0.0095, 0.02, 3.0
DRAG = EXPENSE / 252 + BORROW * (LEV - 1) / 252

# (레버리지 티커, 원지수 티커)
PAIRS = [("KORU", "EWY"), ("TECL", "XLK"), ("TNA", "IWM"),
         ("EDC", "EEM"), ("YINN", "FXI")]


def load(t):
    return pd.read_csv(DATA / f"{t}.csv", index_col="Date", parse_dates=True)["Close"].dropna()


def splice(spine, synth_ret, real_close=None, base=100.0):
    """일일수익률 스파인(합성) 위에 실측 수익률을 덮어 접합 → 연속 레벨."""
    r = synth_ret.reindex(spine)
    if real_close is not None:
        rr = real_close.pct_change().reindex(spine)
        r = r.where(rr.isna(), rr)
    first = r.first_valid_index()
    r2 = r.loc[first:].fillna(0.0)
    price = base * (1 + r2).cumprod()
    out = pd.Series(index=spine, dtype=float, name="Close")
    out.loc[first:] = price.values
    return out


def save(name, series, note):
    df = series.to_frame("Close")
    df.index.name = "Date"
    df.dropna().to_csv(DATA / f"{name}.csv")
    s = series.dropna()
    print(f"[ok] {name}: {note} | {s.index[0].date()} ~ {s.index[-1].date()} n={len(s)}")


def main():
    spine = load("QQQ").index
    for lev_tk, und_tk in PAIRS:
        und_ret = load(und_tk).pct_change().reindex(spine)
        synth_ret = LEV * und_ret - DRAG
        real = load(lev_tk)
        save(f"{lev_tk}_SYNTH", splice(spine, synth_ret, real),
             f"{und_tk}×3 + 실{lev_tk}")
        # 검증: 겹침 구간에서 '순수 합성' vs 실물 일수익률 (스플라이스 전 모델 품질)
        both = pd.concat([synth_ret.rename("synth"),
                          real.pct_change().reindex(spine).rename("real")], axis=1).dropna()
        if len(both) > 100:
            corr = both.corr().iloc[0, 1]
            cum_s = (1 + both["synth"]).prod()
            cum_r = (1 + both["real"]).prod()
            print(f"  [검증] 순수합성 vs 실{lev_tk} ({both.index[0].date()}~): "
                  f"상관 {corr:.3f} · 누적배율 합성 {cum_s:.2f}x vs 실물 {cum_r:.2f}x")


if __name__ == "__main__":
    main()
