"""외부 추가자금 전용 폭락 프로토콜 (포트폴리오와 분리).

규칙: 나스닥100(QQQ)이 최근1년 고점 대비 -30/-40/-50%(종가) 도달 시마다 **외부자금 1,000만원**을
      **QQQ 70% + TQQQ 30%** 로 투입(3등분 계단). 추세신호(200일선 기울기+스트릭5)가 **재점등하면
      해당 폭락분을 청산해 코어로 편입**(그 시점 가치 = 코어 유입액). 과거 폭락 3회에 적용.

포트폴리오 본진(코어+위성)과 **국면이 정반대일 때 작동**(본진=추세ON, 프로토콜=폭락 저점)해 보완적.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.crash_buyin import find_triggers, load, episode_of  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

TRANCHE = 1e7          # 1,000만원
W_QQQ, W_TQQQ = 0.70, 0.30


def blend_val(qqq, tqqq, d, t, tranche=TRANCHE):
    """d에 투입한 tranche가 t시점에 얼마인가(QQQ70/TQQQ30)."""
    q = W_QQQ * tranche * float(qqq.loc[:t].iloc[-1]) / float(qqq.loc[:d].iloc[-1])
    k = W_TQQQ * tranche * float(tqqq.loc[:t].iloc[-1]) / float(tqqq.loc[:d].iloc[-1])
    return q + k


def relight_date(trend, after):
    s = trend.loc[after:]
    on = s[s]
    return on.index[0] if len(on) else None


def won(x):
    return f"{x/1e8:.2f}억"


def main():
    qqq = load("QQQ")
    tqqq = load("TQQQ_SYNTH")
    trend = v6.trend_signal_v6(qqq, qqq.index, require_rising=True, confirm_days=5)
    trig = find_triggers(qqq)

    print("# 외부 추가자금 전용 폭락 프로토콜 (QQQ 70% + TQQQ 30%)\n")
    print(f"> QQQ 최근1년 고점 대비 -30/-40/-50%에서 외부 {won(TRANCHE)}씩 3계단 투입, "
          "신호 재점등 시 코어 편입. (포트폴리오 본진과 분리)\n")

    # 에피소드별 그룹
    from collections import defaultdict
    eps = defaultdict(list)
    for d, t, p in trig:
        eps[episode_of(d)].append((d, t, p))

    print("## 트리거별 (외부 1,000만원 → 이후 가치)\n")
    print("| 폭락 | 트리거일 | 티어 | 재점등일 | 재점등시 가치 | +3년 | +5년 | 현재 |")
    print("|---|---|---:|---|---:|---:|---:|---:|")
    tot_in = tot_relight = tot_now = 0.0
    ep_summary = {}
    for ep, items in eps.items():
        last_d = max(d for d, _, _ in items)
        rl = relight_date(trend, last_d)
        s_in = s_rl = s_now = 0.0
        for d, t, p in items:
            def g(years=None, to=None):
                if to is not None:
                    tt = to
                else:
                    tt = d + pd.DateOffset(years=years)
                    if tt > qqq.index[-1]:
                        return None
                return blend_val(qqq, tqqq, d, tt)
            v_rl = blend_val(qqq, tqqq, d, rl) if rl is not None else None
            v3, v5 = g(3), g(5)
            v_now = blend_val(qqq, tqqq, d, qqq.index[-1])
            fmt = lambda v: won(v) if v is not None else "—"  # noqa: E731
            print(f"| {ep} | {d.date()} | {int(t*100)}% | "
                  f"{rl.date() if rl is not None else '—'} | {fmt(v_rl)} | {fmt(v3)} | "
                  f"{fmt(v5)} | {won(v_now)} |")
            s_in += TRANCHE
            s_rl += v_rl if v_rl is not None else v_now
            s_now += v_now
        ep_summary[ep] = (s_in, s_rl, s_now, rl)
        tot_in += s_in; tot_relight += s_rl; tot_now += s_now

    print("\n## 폭락별 합계 (신호 재점등 시 코어 편입액 / 현재가치)\n")
    print("| 폭락 | 투입합계 | 재점등일 | 코어 편입액(재점등시) | 배수 | 현재가치 | 배수 |")
    print("|---|---:|---|---:|---:|---:|---:|")
    for ep, (s_in, s_rl, s_now, rl) in ep_summary.items():
        print(f"| {ep} | {won(s_in)} | {rl.date() if rl is not None else '—'} | "
              f"{won(s_rl)} | {s_rl/s_in:.2f}x | {won(s_now)} | {s_now/s_in:.2f}x |")
    print(f"\n**전체**: 투입 {won(tot_in)} → 재점등 편입 {won(tot_relight)} "
          f"({tot_relight/tot_in:.2f}x) · 현재 {won(tot_now)} ({tot_now/tot_in:.2f}x)\n")
    print("> '재점등시 가치'는 폭락 저점에서 산 뒤 추세 회복(신호 ON)까지 보유한 결과 = 코어로 넘길 금액.")
    print("> 미발동: COVID(종가 -28.6%)·2018 Q4(-22.8%)는 -30% 미달. TQQQ 30%는 합성(2010이전) 편향 주의.")


if __name__ == "__main__":
    main()
