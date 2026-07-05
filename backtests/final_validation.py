"""최종 견고안 확정 스위트 (1) — 파라미터 교란 + 교차자산 + 서브기간.

최종 견고안: 기울기 필터(가격>200MA·200MA 상승, 판정 SL일) + 확정 스트릭 CD일
             + 분할 D / 익절 TP / 쿼터손절, 쿨다운·변동성필터 OFF, 100% 투입.
기준값: SL=20, CD=5, D=40, TP=0.15.

출력: 마크다운 그리드(Sharpe/CAGR) + reports/에 히트맵 PNG 저장.
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
EXPENSE, BORROW, LEV = 0.0095, 0.02, 3.0


def load_close(t):
    df = pd.read_csv(DATA_DIR / f"{t}.csv", index_col="Date", parse_dates=True)
    return df["Close"].dropna()


def synth_3x(under: pd.Series) -> pd.Series:
    """under(1x) 종가 → 3배 레버리지 합성 종가 (make_synthetic_tqqq와 동일 수식)."""
    r = under.pct_change().fillna(0.0)
    lr = LEV * r - EXPENSE / 252 - BORROW * (LEV - 1) / 252
    return pd.Series(100 * np.cumprod(1 + lr), index=under.index)


def fparams(D=40, TP=0.15):
    return v6.Params(divisions=D, take_profit_pct=TP, exhaust_action="quarter",
                     use_trend_filter=True, reentry_cooldown_days=0, use_vol_filter=False)


def run_final(close, under, D=40, TP=0.15, SL=20, CD=5):
    trend = v6.trend_signal_v6(under, close.index, require_rising=True,
                               slope_lookback=SL, confirm_days=CD)
    res = v6.run(close, fparams(D, TP), trend_ok=trend)
    return compute(res.equity, res.cycles)


def pctf(x):
    return f"{x:.1%}" if isinstance(x, (int, float)) and x == x else "n/a"


def print_grid(title, rows, cols, rlabel, clabel, M, fmt):
    print(f"\n**{title}**  (행={rlabel}, 열={clabel})\n")
    print("| " + rlabel + r" \ " + clabel + " | " + " | ".join(str(c) for c in cols) + " |")
    print("|" + "---|" * (len(cols) + 1))
    for i, r in enumerate(rows):
        print(f"| **{r}** | " + " | ".join(fmt(M[i][j]) for j in range(len(cols))) + " |")


def heatmap_png(M, rows, cols, rlabel, clabel, title, fname, base_ij):
    A = np.array(M, dtype=float)
    fig, ax = plt.subplots(figsize=(1.1 * len(cols) + 2, 0.8 * len(rows) + 2))
    im = ax.imshow(A, cmap="viridis", aspect="auto")
    ax.set_xticks(range(len(cols))); ax.set_xticklabels(cols)
    ax.set_yticks(range(len(rows))); ax.set_yticklabels(rows)
    ax.set_xlabel(clabel); ax.set_ylabel(rlabel); ax.set_title(title)
    for i in range(len(rows)):
        for j in range(len(cols)):
            ax.text(j, i, f"{A[i][j]:.2f}", ha="center", va="center",
                    color="white" if A[i][j] < (A.max() + A.min()) / 2 else "black", fontsize=8)
    bi, bj = base_ij
    ax.add_patch(plt.Rectangle((bj - 0.5, bi - 0.5), 1, 1, fill=False, edgecolor="red", lw=2))
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    REPORT_DIR.mkdir(exist_ok=True)
    fig.savefig(REPORT_DIR / fname, dpi=110)
    plt.close(fig)


def perturbation(close, under):
    print("\n# 1. 파라미터 교란 테스트 (TQQQ_SYNTH 전체구간)")

    # 그리드 A: 기울기판정(SL) x 스트릭(CD)
    SLs = [10, 15, 20, 25, 30]; CDs = [3, 4, 5, 6, 7]
    Sh = [[0] * len(CDs) for _ in SLs]; Cg = [[0] * len(CDs) for _ in SLs]
    for i, sl in enumerate(SLs):
        for j, cd in enumerate(CDs):
            m = run_final(close, under, SL=sl, CD=cd)
            Sh[i][j] = m["Sharpe"]; Cg[i][j] = m["CAGR"]
    print_grid("A. 샤프 — 기울기판정일 × 스트릭 (분할40/익절15% 고정)", SLs, CDs,
               "SL", "CD", Sh, lambda x: f"{x:.2f}")
    print_grid("A. CAGR — 기울기판정일 × 스트릭", SLs, CDs, "SL", "CD", Cg, pctf)
    heatmap_png(Sh, SLs, CDs, "기울기판정일(SL)", "스트릭(CD)",
                "샤프: SL x CD (기준 SL20/CD5)", "heat_sl_cd.png", (2, 2))

    # 그리드 B: 분할(D) x 익절(TP)
    Ds = [30, 35, 40, 45, 50]; TPs = [0.12, 0.13, 0.14, 0.15, 0.16, 0.17, 0.18]
    Sh2 = [[0] * len(TPs) for _ in Ds]; Cg2 = [[0] * len(TPs) for _ in Ds]
    for i, d in enumerate(Ds):
        for j, tp in enumerate(TPs):
            m = run_final(close, under, D=d, TP=tp)
            Sh2[i][j] = m["Sharpe"]; Cg2[i][j] = m["CAGR"]
    print_grid("B. 샤프 — 분할 × 익절 (SL20/CD5 고정)", Ds, [f"{int(t*100)}%" for t in TPs],
               "분할", "익절", Sh2, lambda x: f"{x:.2f}")
    print_grid("B. CAGR — 분할 × 익절", Ds, [f"{int(t*100)}%" for t in TPs],
               "분할", "익절", Cg2, pctf)
    heatmap_png(Sh2, Ds, [f"{int(t*100)}%" for t in TPs], "분할(D)", "익절(TP)",
                "샤프: 분할 x 익절 (기준 40/15%)", "heat_div_tp.png", (2, 3))


def crossasset():
    print("\n# 2. 교차자산 검증 (S&P500 3배 = SPY로 UPRO 합성)")
    spy = load_close("SPY"); qqq = load_close("QQQ")
    tqqq = load_close("TQQQ_SYNTH")
    upro = synth_3x(spy)

    rows = [
        ("나스닥3배 TQQQ_SYNTH — 최종견고안", run_final(tqqq, qqq), tqqq),
        ("S&P3배 UPRO(합성) — 최종견고안", run_final(upro, spy), upro),
    ]
    print("\n| 자산/전략 | 기간 | CAGR | MDD | 샤프 | 총수익 | 승률 |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for label, m, series in rows:
        yrs = f"{series.index[0].year}~{series.index[-1].year}"
        print(f"| {label} | {yrs} | {pctf(m['CAGR'])} | {pctf(m['MDD'])} | {m['Sharpe']:.2f} | "
              f"{pctf(m['TotalReturn'])} | {pctf(m.get('WinRate', float('nan')))} |")
    # UPRO Buy&Hold
    bh = compute(upro / upro.iloc[0] * 40_000.0)
    print(f"| S&P3배 UPRO — Buy&Hold | {upro.index[0].year}~{upro.index[-1].year} | "
          f"{pctf(bh['CAGR'])} | {pctf(bh['MDD'])} | {bh['Sharpe']:.2f} | {pctf(bh['TotalReturn'])} | — |")

    print("\n**UPRO 스트레스 (최종견고안 vs Buy&Hold)**\n")
    print("| 구간 | 최종견고안 총수익/MDD | Buy&Hold 총수익/MDD |")
    print("|---|---:|---:|")
    for s, e, name in [("2000-01-01", "2002-12-31", "닷컴"), ("2007-10-01", "2009-03-31", "GFC"),
                       ("2020-01-01", "2020-06-30", "COVID"), ("2022-01-01", "2022-12-31", "2022")]:
        sl = upro.loc[s:e]
        mf = run_final(sl, spy)
        mb = compute(sl / sl.iloc[0] * 40_000.0)
        print(f"| {name} | {pctf(mf['TotalReturn'])} / {pctf(mf['MDD'])} | "
              f"{pctf(mb['TotalReturn'])} / {pctf(mb['MDD'])} |")


def subperiod():
    print("\n# 3. 서브기간 검증 (TQQQ_SYNTH)")
    tqqq = load_close("TQQQ_SYNTH"); qqq = load_close("QQQ")
    print("\n| 기간 | 최종견고안 CAGR/MDD/샤프 | Buy&Hold CAGR/MDD |")
    print("|---|---:|---:|")
    for s, e, name in [("1999-01-01", "2012-12-31", "전반부 1999~2012"),
                       ("2013-01-01", "2026-12-31", "후반부 2013~2026")]:
        sl = tqqq.loc[s:e]
        mf = run_final(sl, qqq)
        mb = compute(sl / sl.iloc[0] * 40_000.0)
        print(f"| {name} | {pctf(mf['CAGR'])} / {pctf(mf['MDD'])} / {mf['Sharpe']:.2f} | "
              f"{pctf(mb['CAGR'])} / {pctf(mb['MDD'])} |")


def main():
    tqqq = load_close("TQQQ_SYNTH"); qqq = load_close("QQQ")
    perturbation(tqqq, qqq)
    crossasset()
    subperiod()
    print("\n[저장] reports/heat_sl_cd.png, reports/heat_div_tp.png")


if __name__ == "__main__":
    main()
