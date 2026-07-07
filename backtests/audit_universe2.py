"""유니버스 확장 감사 — KORU·TECL·TNA·EDC·YINN (result_universe2.md 입력).

연구 트랙 감사. 현행 위성 유니버스 {TQQQ, UPRO, SOXL}에 후보를 하나씩(및 전부) 추가해
**확정 B안(코어 42.5 / 위성 42.5 / 금 15 = 코어/위성 50/50 + 금15, 카나리아 코어)** 을 재산출.

조건:
  ① 전체구간 (~2026-07-01)
  ② 2025말 절단 — 2026 상반기 급등 왜곡 제거 (SOXL 감사와 동일한 최근성 검사)

지표:
  - B안 포트: 세전 CAGR / 세후+파킹 CAGR / MDD / 샤프 (+ 스트레스 4구간 MDD)
  - 위성 슬리브 단독(0/100): CAGR / MDD / 샤프
  - 위성 보유일 비중: 전체구간 vs 2025-01~ (후보가 최근에만 뽑히는지 = 최근성 의존)

합성: {후보}_SYNTH = 원지수×3 − 드래그 + 실물 스플라이스 (scripts/make_synthetic_universe2.py).
KORU 합성은 실물보다 후함(겹침 13년 누적 1.85x vs 1.49x) → KORU 결과는 상방 편향임을 감안.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from backtests import portfolio_backtest as PB  # noqa: E402
import backtests.benchmark_strategies as B  # noqa: E402
from backtests import engine_v9 as E9  # noqa: E402
from backtests.v10_experiments import canary_gate, core_nav  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

ASOF = "2026-07-01"
CUT = "2025-12-31"
CUT2 = "2024-12-31"      # 2025 한국 랠리까지 제거하는 절단 (KORU 최근성 검사)
RECENT = "2025-01-01"
GOLD = 0.15
W_CORE = 0.50           # B안: 코어/위성 50/50
STRESS = [("2000-01-01", "2002-12-31", "닷컴"), ("2007-10-01", "2009-03-31", "GFC"),
          ("2020-02-01", "2020-06-30", "COVID"), ("2022-01-01", "2022-12-31", "2022")]

BASE = [("TQQQ", "core", "QQQ"), ("UPRO", "core", "SPY"), ("SOXL", "satellite", "SOXX")]
CANDS = [("KORU", "EWY"), ("TECL", "XLK"), ("TNA", "IWM"), ("EDC", "EEM"), ("YINN", "FXI")]


def pf(x):
    return f"{x:+.1%}" if isinstance(x, float) and x == x else "n/a"


def aftertax_cagr(nav):
    at = PB.aftertax_overlay(nav)
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    return PB.norm(at).iloc[-1] ** (1 / yrs) - 1


def build_sat(spine, uni):
    assets = [t for t, _, _ in uni]
    meta = {t: {"class": c, "index": i} for t, c, i in uni}
    prices, sig, mom, dd = E9.build_inputs(assets, meta, spine)
    nav, info = E9.run_2engine(spine, prices, sig, mom, meta, dd, use_engineB=False)
    return PB.norm(nav), info


def to_bnav(core, sat, gld, spine):
    risky = PB.blend(core, sat, W_CORE)
    nav = B.static_portfolio({"OUR": 1 - GOLD, "GLD": GOLD},
                             {"OUR": risky, "GLD": gld}, spine, "A")
    return PB.norm(nav)


def held_share(held, tickers, start=None):
    h = held.loc[start:] if start else held
    vc = h.value_counts(normalize=True)
    return {t: float(vc.get(t, 0.0)) for t in tickers}


def build_koru_adj():
    """KORU 합성 상방 편향 보정판 → data/KORU_ADJ_SYNTH.csv (원지수 EWY).

    순수 합성(EWY×3−드래그)이 실 KORU 겹침 구간에서 누적 1.85x vs 1.49x로 후하다.
    그 초과분을 일할 드래그로 환산해 '합성 구간(실물 이전)'에만 추가로 차감.
    """
    data = ROOT / "data"
    ewy_ret = B.load("EWY").pct_change()
    spine = B.load("QQQ").index
    drag = 0.0095 / 252 + 0.02 * 2 / 252
    synth_ret = (3.0 * ewy_ret - drag).reindex(spine)
    real = B.load("KORU")
    real_ret = real.pct_change().reindex(spine)
    both = pd.concat([synth_ret, real_ret], axis=1).dropna()
    cum_s = float((1 + both.iloc[:, 0]).prod())
    cum_r = float((1 + both.iloc[:, 1]).prod())
    adj = (cum_r / cum_s) ** (1 / len(both)) - 1        # 일할 보정(음수)
    pre = synth_ret[synth_ret.index < real.index[0]] + adj
    r = pre.reindex(spine)
    rr = real_ret
    r = r.where(rr.isna(), rr)
    first = r.first_valid_index()
    price = 100.0 * (1 + r.loc[first:].fillna(0.0)).cumprod()
    out = price.to_frame("Close")
    out.index.name = "Date"
    out.to_csv(data / "KORU_ADJ_SYNTH.csv")
    return adj * 252


def main():
    spine = B.load("QQQ").loc[:ASOF].index
    qqq = B.load("QQQ").loc[:ASOF]
    trend = v6.trend_signal_v6(qqq, spine, require_rising=True, confirm_days=5).astype(bool)
    gate = trend & canary_gate(spine, k=4)
    core = core_nav(spine, gate)
    gld = B.R("GLD").reindex(spine).ffill()

    print("# 유니버스 확장 감사 — KORU·TECL·TNA·EDC·YINN (B안 50/50+금15, 카나리아 코어)\n")
    print("> 후보를 현행 위성 유니버스 {TQQQ,UPRO,SOXL}에 추가해 재산출 · 비용 편도 0.12% "
          "· 데이터 `*_SYNTH` (원지수 상장일부터)\n")

    # ---- 후보 프로필 ----
    print("## 0. 후보 프로필 (합성 3x, Buy&Hold 전기간)\n")
    print("| 후보 | 원지수 | 데이터 시작 | B&H CAGR | B&H MDD | 비고 |")
    print("|---|---|---|---:|---:|---|")
    notes = {"KORU": "합성이 실물보다 후함(상방 편향)", "TECL": "상관 0.995", "TNA": "상관 0.999",
             "EDC": "상관 0.997", "YINN": "상관 0.975"}
    for t, ix in CANDS:
        s = E9.resolve_price(t).dropna()
        m = compute(PB.norm(s))
        print(f"| {t} | {ix} | {s.index[0].date()} | {pf(m['CAGR'])} | {pf(m['MDD'])} | {notes[t]} |")
    print()

    # ---- 변형 유니버스 ----
    adj_yr = build_koru_adj()
    print(f"> KORU 보정판: 합성 구간(2000~2013)에 연 {adj_yr:+.2%} 추가 드래그 "
          f"(실물 대비 상방 편향 제거)\n")
    variants = [("현행 (기준)", BASE)]
    variants += [(f"+{t}", BASE + [(t, "satellite", ix)]) for t, ix in CANDS]
    variants += [("+KORU(보정)", BASE + [("KORU_ADJ", "satellite", "EWY")]),
                 ("+전부(5종)", BASE + [(t, "satellite", ix) for t, ix in CANDS])]

    rows = []
    for lab, uni in variants:
        sat, info = build_sat(spine, uni)
        nav1 = to_bnav(core, sat, gld, spine)
        nav2 = PB.norm(nav1.loc[:CUT])
        rows.append((lab, uni, sat, info, nav1, nav2))

    # ---- 표 1: B안 포트 전체구간 ① ----
    print("## 1. B안 포트폴리오 — ① 전체구간 (~2026-07)\n")
    print("| 유니버스 | 세전 CAGR | 세후+파킹 | MDD | 샤프 | 닷컴 MDD | GFC | COVID | 2022 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for lab, uni, sat, info, nav1, nav2 in rows:
        m = compute(nav1)
        st = []
        for s, e, _ in STRESS:
            sub = nav1.loc[s:e]
            st.append(pf(compute(sub)["MDD"]) if len(sub) > 5 else "n/a")
        print(f"| {lab} | {pf(m['CAGR'])} | {pf(aftertax_cagr(nav1))} | {pf(m['MDD'])} | "
              f"{m['Sharpe']:.2f} | " + " | ".join(st) + " |")
    print()

    # ---- 표 2: B안 포트 절단 ②·③ ----
    print("## 2. B안 포트폴리오 — ② 2025말 절단 · ③ 2024말 절단 (최근 랠리 왜곡 제거)\n")
    print("| 유니버스 | ② 세전 | ② 세후+파킹 | ② 샤프 | ③ 세전 | ③ 세후+파킹 | ③ 샤프 |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for lab, uni, sat, info, nav1, nav2 in rows:
        m2 = compute(nav2)
        nav3 = PB.norm(nav1.loc[:CUT2])
        m3 = compute(nav3)
        print(f"| {lab} | {pf(m2['CAGR'])} | {pf(aftertax_cagr(nav2))} | {m2['Sharpe']:.2f} | "
              f"{pf(m3['CAGR'])} | {pf(aftertax_cagr(nav3))} | {m3['Sharpe']:.2f} |")
    print()

    # ---- 표 3: 위성 슬리브 단독 + 보유일 비중 ----
    print("## 3. 위성 슬리브 단독 (0/100) + 후보 보유일 비중\n")
    print("| 유니버스 | ① CAGR | ① MDD | ① 샤프 | ② CAGR | ② 샤프 | 후보 보유일(전체) | 후보 보유일(2025~) |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for lab, uni, sat, info, nav1, nav2 in rows:
        m1 = compute(sat)
        m2 = compute(PB.norm(sat.loc[:CUT]))
        cand_t = [t for t, c, _ in uni if (t, c) not in [(b[0], b[1]) for b in BASE]]
        if cand_t:
            sh_all = held_share(info["held"], cand_t)
            sh_rec = held_share(info["held"], cand_t, start=RECENT)
            f_all = " ".join(f"{t} {v:.0%}" for t, v in sh_all.items())
            f_rec = " ".join(f"{t} {v:.0%}" for t, v in sh_rec.items())
        else:
            f_all = f_rec = "—"
        print(f"| {lab} | {pf(m1['CAGR'])} | {pf(m1['MDD'])} | {m1['Sharpe']:.2f} | "
              f"{pf(m2['CAGR'])} | {m2['Sharpe']:.2f} | {f_all} | {f_rec} |")
    print()

    # ---- 표 4: 전체 보유 구성 (기준 vs +전부) ----
    print("## 4. 위성 보유일 구성 (전체구간)\n")
    for lab in ("현행 (기준)", "+전부(5종)"):
        info = next(r[3] for r in rows if r[0] == lab)
        vc = info["held"].value_counts(normalize=True).sort_values(ascending=False)
        comp = " · ".join(f"{k} {v:.0%}" for k, v in vc.items() if v >= 0.005)
        print(f"- **{lab}**: {comp}")
    print()

    # ---- 표 5: KORU 시대별 보유·기여 ----
    print("## 5. KORU 딥다이브 — 시대별 보유일 비중 (+KORU 유니버스)\n")
    info_k = next(r[3] for r in rows if r[0] == "+KORU")
    held_k = info_k["held"]
    eras = [("2000-05", "2012-12-31", "2000~2012 (합성)"),
            ("2013-01-01", "2019-12-31", "2013~2019"),
            ("2020-01-01", "2024-12-31", "2020~2024"),
            ("2025-01-01", None, "2025~2026H1")]
    print("| 구간 | KORU 보유일 비중 |")
    print("|---|---:|")
    for s, e, lab in eras:
        h = held_k.loc[s:e] if e else held_k.loc[s:]
        vc = h.value_counts(normalize=True)
        print(f"| {lab} | {float(vc.get('KORU', 0.0)):.0%} |")
    print()


if __name__ == "__main__":
    main()
