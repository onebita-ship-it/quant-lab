"""전략 후보용 합성 자산 생성 (QQQ 거래일 스파인, 1999~).

- UPRO_SYNTH : S&P500 3x. SPY에서 3x 합성 + 실 UPRO(2009~) 스플라이스.
- SOXL_SYNTH : 반도체 3x. SOXX에서 3x 합성 + 실 SOXL(2010~). SOXX 상장(2001-07) 이전은 NaN.
- SQQQ_SYNTH : 나스닥100 -3x. QQQ에서 -3x 합성 + 실 SQQQ(2010~).
- SGOV_SYNTH : 초단기 국채(현금). ^IRX(13주 T-bill 연율%) 일할 + 실 SGOV(2020~).

레벨이 아니라 '일일수익률'을 이어붙여(실측 구간은 실측 수익률, 그 외 합성 수익률) 접합면
점프가 없다. 3x 드래그 모델은 make_synthetic_tqqq와 동일(EXP 0.95%/BORROW 2%).
결과: data/{UPRO,SOXL,SQQQ,SGOV}_SYNTH.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
EXPENSE, BORROW, LEV = 0.0095, 0.02, 3.0
DRAG = EXPENSE / 252 + BORROW * (LEV - 1) / 252


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
    qqq = load("QQQ"); spine = qqq.index
    spy = load("SPY"); soxx = load("SOXX")
    qqq_ret = qqq.pct_change()
    spy_ret = spy.pct_change().reindex(spine)
    soxx_ret = soxx.pct_change().reindex(spine)
    irx = load("IRX").reindex(spine).ffill()   # 연율 %
    print(f"  ^IRX 평균 {irx.mean():.2f}% (연율) → 현금 프록시")

    save("UPRO_SYNTH", splice(spine, LEV * spy_ret - DRAG, load("UPRO")), "SPY×3 + 실UPRO")
    save("SOXL_SYNTH", splice(spine, LEV * soxx_ret - DRAG, load("SOXL")), "SOXX×3 + 실SOXL")
    save("SQQQ_SYNTH", splice(spine, -LEV * qqq_ret - DRAG, load("SQQQ")), "QQQ×-3 + 실SQQQ")
    save("SGOV_SYNTH", splice(spine, irx / 100.0 / 252.0, load("SGOV")), "^IRX 일할 + 실SGOV")

    # 합성 SQQQ 검증: 실 SQQQ와 겹치는 구간 상관/누적 비교
    sq = load("SQQQ"); sqs = load("SQQQ_SYNTH").reindex(sq.index)
    both = pd.concat([sq.pct_change(), sqs.pct_change()], axis=1).dropna()
    print(f"  [검증] 합성SQQQ vs 실SQQQ 일수익률 상관 {both.corr().iloc[0,1]:.3f} "
          f"(2010~, 접합 구간이라 1.0 근처가 정상)")


if __name__ == "__main__":
    main()
