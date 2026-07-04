"""시세 데이터 수집 스크립트.

기본: yfinance로 TQQQ/QQQ/SPY 일봉을 받아 data/에 CSV 캐시.
--synthetic: 인터넷 없이 파이프라인 테스트용 합성 데이터 생성 (GBM 기반).

이미 캐시된 파일이 있으면 다시 받지 않는다 (--force로 강제 갱신).
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TICKERS = {"TQQQ": "2010-02-11", "QQQ": "1999-03-10", "SPY": "1993-01-29"}


def download_real(force: bool = False) -> None:
    import yfinance as yf

    DATA_DIR.mkdir(exist_ok=True)
    for ticker, start in TICKERS.items():
        out = DATA_DIR / f"{ticker}.csv"
        if out.exists() and not force:
            print(f"[skip] {out} 이미 존재 (--force로 갱신)")
            continue
        df = yf.download(ticker, start=start, progress=False, auto_adjust=True)
        if df.empty:
            print(f"[error] {ticker} 다운로드 실패", file=sys.stderr)
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index.name = "Date"
        df[["Open", "High", "Low", "Close", "Volume"]].to_csv(out)
        print(f"[ok] {ticker}: {len(df)}일 → {out}")


def make_synthetic(seed: int = 42) -> None:
    """QQQ 성격의 GBM 경로를 만들고, TQQQ는 그 일수익률의 3배로 합성.

    실데이터가 아니므로 전략 코드 동작 확인(파이프라인 테스트) 용도로만 사용.
    """
    rng = np.random.default_rng(seed)
    n_days = 252 * 15  # 15년치
    dates = pd.bdate_range("2010-01-04", periods=n_days)

    # QQQ 근사: 연 수익 12%, 연 변동성 22% + 가끔 급락(fat tail)
    mu, sigma = 0.12 / 252, 0.22 / np.sqrt(252)
    ret = rng.normal(mu, sigma, n_days)
    crashes = rng.random(n_days) < 0.001
    ret[crashes] -= rng.uniform(0.03, 0.07, crashes.sum())

    DATA_DIR.mkdir(exist_ok=True)
    for name, mult in [("QQQ", 1.0), ("TQQQ", 3.0)]:
        daily = mult * ret - (0.0095 / 252 if mult > 1 else 0.0)
        close = 100 * np.cumprod(1 + daily)
        df = pd.DataFrame(
            {
                "Open": close * (1 + rng.normal(0, 0.002, n_days)),
                "High": close * (1 + np.abs(rng.normal(0, 0.006, n_days))),
                "Low": close * (1 - np.abs(rng.normal(0, 0.006, n_days))),
                "Close": close,
                "Volume": rng.integers(1_000_000, 50_000_000, n_days),
            },
            index=dates,
        )
        df.index.name = "Date"
        out = DATA_DIR / f"{name}.csv"
        df.to_csv(out)
        print(f"[ok] 합성 {name}: {len(df)}일 → {out}")
    print("[주의] 합성 데이터는 코드 검증용입니다. 실제 결론은 실데이터로 내세요.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--synthetic", action="store_true", help="오프라인 합성 데이터 생성")
    p.add_argument("--force", action="store_true", help="캐시 무시하고 다시 다운로드")
    args = p.parse_args()
    if args.synthetic:
        make_synthetic()
    else:
        download_real(force=args.force)
