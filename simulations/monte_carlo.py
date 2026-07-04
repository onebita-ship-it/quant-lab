"""몬테카를로 미래 시뮬레이션.

과거 일수익률을 블록 부트스트랩(기본 20일 블록)으로 재표집해 미래 N년 경로를
수천 개 만들고, 각 경로에서 무한매수법을 돌려 결과 분포를 본다.

블록 부트스트랩을 쓰는 이유: 일별 독립 추출은 변동성 군집(폭락이 몰려오는 성질)을
없애버려 위험을 과소평가한다.

[한계] 이 시뮬레이션은 "과거 수익률 분포가 미래에도 유지된다"는 가정 위에 있다.
2008·2022급 폭락이 더 자주/크게 오면 실제 위험은 이보다 크다.

사용:
  python simulations/monte_carlo.py --ticker TQQQ --years 5 --paths 2000
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from strategies.infinite_buying import Params, run  # noqa: E402

DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"


def bootstrap_paths(returns: np.ndarray, n_days: int, n_paths: int,
                    block: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n_blocks = n_days // block + 1
    starts = rng.integers(0, len(returns) - block, size=(n_paths, n_blocks))
    idx = starts[:, :, None] + np.arange(block)[None, None, :]
    return returns[idx].reshape(n_paths, -1)[:, :n_days]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="TQQQ")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--paths", type=int, default=2000)
    ap.add_argument("--block", type=int, default=20)
    ap.add_argument("--divisions", type=int, default=40)
    ap.add_argument("--take-profit", type=float, default=0.10)
    ap.add_argument("--exhaust", choices=["sell", "hold"], default="sell")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    df = pd.read_csv(DATA_DIR / f"{args.ticker}.csv", index_col="Date", parse_dates=True)
    hist_ret = df["Close"].pct_change().dropna().values
    n_days = args.years * 252
    paths = bootstrap_paths(hist_ret, n_days, args.paths, args.block, args.seed)
    dates = pd.bdate_range("2100-01-01", periods=n_days)  # 가상 달력

    p = Params(divisions=args.divisions, take_profit_pct=args.take_profit,
               exhaust_action=args.exhaust)
    finals, mdds = [], []
    for i in range(args.paths):
        close = pd.Series(100 * np.cumprod(1 + paths[i]), index=dates)
        res = run(close, p)
        m = compute(res.equity)
        finals.append(m["TotalReturn"])
        mdds.append(m["MDD"])

    finals, mdds = np.array(finals), np.array(mdds)
    q = lambda a, x: np.percentile(a, x)  # noqa: E731
    print(f"\n=== 몬테카를로 {args.paths}경로 × {args.years}년 | {args.ticker} "
          f"| 분할={p.divisions} 익절={p.take_profit_pct:.0%} 소진시={p.exhaust_action} ===")
    print(f"총수익률   5% / 50% / 95%: {q(finals,5):>8.1%} / {q(finals,50):>8.1%} / {q(finals,95):>8.1%}")
    print(f"MDD        5% / 50% / 95%: {q(mdds,5):>8.1%} / {q(mdds,50):>8.1%} / {q(mdds,95):>8.1%}")
    print(f"손실 확률 (최종 원금 미만): {np.mean(finals < 0):.1%}")
    print(f"반토막 확률 (총수익 -50% 이하): {np.mean(finals <= -0.5):.1%}")
    print("[주의] 과거 분포 유지 가정. 별도 폭락 스트레스 테스트 병행 권장.")

    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / f"mc_{args.ticker}_{args.years}y.csv"
    pd.DataFrame({"TotalReturn": finals, "MDD": mdds}).to_csv(out, index=False)
    print(f"[저장] {out}")


if __name__ == "__main__":
    main()
