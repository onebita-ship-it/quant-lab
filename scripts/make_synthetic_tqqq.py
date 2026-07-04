"""QQQ 실데이터에서 가상 TQQQ를 합성한다 (1999년~).

TQQQ는 2010년 상장이라 닷컴버블(2000)·금융위기(2008) 구간이 없다.
3배 레버리지 ETF의 일일 리밸런싱 구조를 수식으로 재현해 과거 구간을 채운다:

    tqqq_ret = 3 * qqq_ret - expense/252 - borrow_cost * 2/252

- expense: TQQQ 운용보수 연 0.95%
- borrow_cost: 3배를 만들기 위해 2배만큼 빌리는 비용(단기금리 근사, 기본 연 2%)

실제 TQQQ가 존재하는 2010-02-11 이후 구간은 실데이터를 그대로 쓰고,
그 이전만 합성값을 이어붙인다. 결과: data/TQQQ_SYNTH.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EXPENSE = 0.0095
BORROW = 0.02
LEVERAGE = 3.0


def main() -> None:
    qqq = pd.read_csv(DATA_DIR / "QQQ.csv", index_col="Date", parse_dates=True)
    ret = qqq["Close"].pct_change().fillna(0.0)
    synth_ret = LEVERAGE * ret - EXPENSE / 252 - BORROW * (LEVERAGE - 1) / 252

    tqqq_path = DATA_DIR / "TQQQ.csv"
    if tqqq_path.exists():
        real = pd.read_csv(tqqq_path, index_col="Date", parse_dates=True)
        cut = real.index[0]
        pre = synth_ret[synth_ret.index < cut]
        # 합성 구간 종가를 실제 TQQQ 시작가에 이어붙이기 (뒤에서 앞으로 역산)
        pre_close = real["Close"].iloc[0] / np.cumprod(1 + pre[::-1]).values
        pre_close = pre_close[::-1]
        synth = pd.DataFrame({"Close": pre_close}, index=pre.index)
        for col in ["Open", "High", "Low"]:
            synth[col] = synth["Close"]
        synth["Volume"] = 0
        out = pd.concat([synth, real])
        src = f"합성({len(synth)}일) + 실데이터({len(real)}일)"
    else:
        close = 100 * np.cumprod(1 + synth_ret)
        out = pd.DataFrame({"Close": close}, index=synth_ret.index)
        for col in ["Open", "High", "Low"]:
            out[col] = out["Close"]
        out["Volume"] = 0
        src = f"전구간 합성({len(out)}일)"

    out.index.name = "Date"
    out_path = DATA_DIR / "TQQQ_SYNTH.csv"
    out[["Open", "High", "Low", "Close", "Volume"]].to_csv(out_path)
    print(f"[ok] TQQQ_SYNTH: {src} → {out_path}")
    print(f"     기간: {out.index[0].date()} ~ {out.index[-1].date()}")


if __name__ == "__main__":
    main()
