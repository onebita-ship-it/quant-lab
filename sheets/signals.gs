/**
 * signals.gs — 확정 룰북(result_final.md §0) 계산 로직 (순수 함수만).
 *
 * ⚠️ 방화벽: 이 파일은 journal/common.py 를 1:1 로 옮긴 것이다. 수식·오프셋·부등호를
 *    임의로 바꾸지 말 것. 변경 시 반드시 sheets/parity/ 패리티 감사를 다시 통과해야 한다.
 *
 * Apps Script API(SpreadsheetApp 등)를 일절 쓰지 않으므로 Node.js 에서도 그대로 실행된다
 * (패리티 검증: sheets/parity/run_signals.mjs).
 *
 * 데이터 구조:
 *   series = { dates: ['YYYY-MM-DD', ...오름차순], closes: [Number, ...] }
 *   universe = [{ ticker, cls, index }]  // config/universe.txt 와 동일
 */

var RULEBOOK_DEFAULTS = {
  trade_ticker: 'TQQQ',
  signal_ticker: 'QQQ',
  divisions: 40,
  take_profit_pct: 0.15,
  fee_pct: 0.0007,
  slippage_pct: 0.0005,
  ma_window: 200,
  slope_lookback: 20,
  confirm_days: 5,
  quarter_days: 4,
  total_capital: 40000.0,
  deploy_frac: 1.0,
  reserve_triggers: [-0.30, -0.50],
};

var CANARY_TICKERS = ['SPY', 'EFA', 'EEM', 'AGG'];

// ---------- 공용 유틸 ----------

function round2(x) { return Math.round(x * 100) / 100; }
function round6(x) { return Math.round(x * 1e6) / 1e6; }

function buyCost(cfg) { return 1 + cfg.fee_pct + cfg.slippage_pct; }   // common.buy_cost
function sellCost(cfg) { return 1 - cfg.fee_pct - cfg.slippage_pct; }  // common.sell_cost

/** series 를 dateStr(포함) 이하로 자른다 — pandas .loc[:asof] 대응. */
function sliceUpTo(series, dateStr) {
  if (!dateStr) return series;
  var n = 0;
  while (n < series.dates.length && series.dates[n] <= dateStr) n++;
  return { dates: series.dates.slice(0, n), closes: series.closes.slice(0, n) };
}

/** 'YYYY-MM-DD' → UTC Date (시간대 오염 방지용으로 항상 UTC 고정). */
function parseDate(s) {
  var p = s.split('-');
  return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
}

function formatDate(d) {
  var m = d.getUTCMonth() + 1, day = d.getUTCDate();
  return d.getUTCFullYear() + '-' + (m < 10 ? '0' : '') + m + '-' + (day < 10 ? '0' : '') + day;
}

/** asof 가 속한 달의 '지난달 말일'(달력일) — common.canary_status 의 cutoff 와 동일. */
function prevMonthEndCutoff(asofDateStr) {
  var d = parseDate(asofDateStr);
  return formatDate(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 0)));
}

/** 다음 영업일(주말만 건너뜀)이 다른 달이면 월말 — common.is_month_end 와 동일 근사. */
function isMonthEnd(dateStr) {
  var d = parseDate(dateStr);
  var next = new Date(d.getTime());
  do {
    next.setUTCDate(next.getUTCDate() + 1);
  } while (next.getUTCDay() === 0 || next.getUTCDay() === 6); // 일=0, 토=6
  return next.getUTCMonth() !== d.getUTCMonth();
}

// ---------- ① 추세 필터 (200일선 3조건) ----------

/**
 * 진입허용 판정 시리즈 — common.signal_series 와 동일.
 *   ma[i]     = 200일 단순이동평균 (200개 미만이면 null)
 *   above[i]  = close >= ma            (ma 가 null 이면 false — pandas NaN 비교와 동일)
 *   rising[i] = ma >= ma[20일 전]       (어느 한쪽 null 이면 false)
 *   ok[i]     = above && rising
 *   entryOk[i]= 직전 confirm_days(5)일 연속 ok  (5개 미만 구간은 false — fillna(False))
 */
function signalSeries(series, cfg) {
  var closes = series.closes, n = closes.length;
  var w = cfg.ma_window, lb = cfg.slope_lookback, cd = cfg.confirm_days;
  var ma = new Array(n).fill(null);
  var sum = 0;
  for (var i = 0; i < n; i++) {
    sum += closes[i];
    if (i >= w) sum -= closes[i - w];
    if (i >= w - 1) ma[i] = sum / w;
  }
  var above = new Array(n), rising = new Array(n), ok = new Array(n), entryOk = new Array(n);
  for (var j = 0; j < n; j++) {
    above[j] = ma[j] !== null && closes[j] >= ma[j];
    rising[j] = ma[j] !== null && j >= lb && ma[j - lb] !== null && ma[j] >= ma[j - lb];
    ok[j] = above[j] && rising[j];
    var all = j >= cd - 1;
    for (var k = 0; all && k < cd; k++) all = ok[j - k];
    entryOk[j] = !!all;
  }
  return { dates: series.dates, closes: closes, ma: ma, above: above, rising: rising, ok: ok, entryOk: entryOk };
}

/** 마지막 날짜 기준 신호 요약 — common.latest_signal 과 동일. */
function latestSignal(series, cfg, asof) {
  var s = asof ? sliceUpTo(series, asof) : series;
  var sig = signalSeries(s, cfg);
  var i = sig.dates.length - 1;
  var streak = 0;
  for (var k = i; k >= 0 && sig.ok[k]; k--) streak++;
  return {
    date: sig.dates[i],
    close: sig.closes[i],
    ma: sig.ma[i],
    above: sig.above[i],
    rising: sig.rising[i],
    streak: streak,
    confirmDays: cfg.confirm_days,
    entryOk: sig.entryOk[i],
  };
}

// ---------- ① 13612W 카나리아 ----------

/** 13612W 모멘텀 — common.w13612 와 동일 거래일 오프셋. 253일 미만이면 null. */
function w13612(closes) {
  var n = closes.length;
  if (n < 253) return null;
  var last = closes[n - 1];
  var r1 = last / closes[n - 22] - 1;
  var r3 = last / closes[n - 64] - 1;
  var r6 = last / closes[n - 127] - 1;
  var r12 = last / closes[n - 253] - 1;
  return 12 * r1 + 4 * r3 + 2 * r6 + r12;
}

/**
 * 카나리아 게이트 — common.canary_status 와 동일.
 * seriesByTicker: { SPY: series, EFA: series, ... }. 데이터가 아예 없으면 fail-safe 차단.
 * 이력 253일 미만 자산은 판정에서 제외(백테스트와 동일), 판정 대상 중 하나라도 음수면 차단.
 */
function canaryStatus(seriesByTicker, asofDateStr, tickers) {
  tickers = tickers || CANARY_TICKERS;
  var cutoff = prevMonthEndCutoff(asofDateStr);
  var rows = [], ok = true;
  for (var i = 0; i < tickers.length; i++) {
    var t = tickers[i];
    var s = seriesByTicker[t];
    if (!s || !s.dates.length) {
      rows.push({ ticker: t, mom: null, date: null, missing: true });
      ok = false; // 판정 불가 → 진입 차단
      continue;
    }
    var h = sliceUpTo(s, cutoff);
    var v = w13612(h.closes);
    rows.push({ ticker: t, mom: v, date: h.dates.length ? h.dates[h.dates.length - 1] : null, missing: false });
    if (v !== null && v < 0) ok = false;
  }
  return { ok: ok, assets: rows, cutoff: cutoff };
}

// ---------- ⑧ 위성 (v9 엔진 A) ----------

/** 3·6개월 블렌드 모멘텀 — common.blended_mom 과 동일. 데이터 부족 시 null. */
function blendedMom(closes, shortN, longN) {
  shortN = shortN || 63;
  longN = longN || 126;
  var n = closes.length;
  if (n < longN + 1) return null;
  var last = closes[n - 1];
  return 0.5 * (last / closes[n - 1 - shortN] - 1) + 0.5 * (last / closes[n - 1 - longN] - 1);
}

/**
 * 위성 오늘 신호 — common.satellite_status 와 동일 (상태 없이 판정만).
 * 각 유니버스 자산의 원지수 추세 ON/OFF 와 매매 티커의 블렌드 모멘텀을 계산,
 * 타깃 = ON 중 모멘텀 1위 (없으면 SGOV).
 */
function satelliteStatus(universe, seriesByTicker, cfg, asof) {
  var rows = [];
  for (var i = 0; i < universe.length; i++) {
    var u = universe[i];
    var idxS = seriesByTicker[u.index];
    var pxS = seriesByTicker[u.ticker];
    if (!idxS || !idxS.dates.length || !pxS || !pxS.dates.length) {
      rows.push({ ticker: u.ticker, index: u.index, on: null, streak: 0, mom: null, missing: true });
      continue;
    }
    if (asof) {
      idxS = sliceUpTo(idxS, asof);
      pxS = sliceUpTo(pxS, asof);
    }
    var sig = latestSignal(idxS, cfg);
    rows.push({
      ticker: u.ticker, index: u.index, on: sig.entryOk, streak: sig.streak,
      mom: blendedMom(pxS.closes), missing: false,
    });
  }
  var on = rows.filter(function (r) { return r.on && r.mom !== null; });
  var target = 'SGOV';
  if (on.length) {
    var best = on[0];
    for (var k = 1; k < on.length; k++) if (on[k].mom > best.mom) best = on[k];
    target = best.ticker;
  }
  return { assets: rows, target: target };
}

// ---------- 코어 사이클 상태 재구성 (매매기록 리플레이) ----------

/** 초기 상태 — common.default_state 와 동일. */
function defaultState(cfg) {
  var total = cfg.total_capital, frac = cfg.deploy_frac;
  return {
    cash: round2(total * frac),
    reserve: round2(total * (1 - frac)),
    shares: 0.0,
    invested: 0.0,
    buysDone: 0,
    oneBuy: 0.0,
    cycleActive: false,
    cycleStart: null,
    cycleProceeds: 0.0,
    cycleSeq: 0,
    currentCycleId: null,
    liquidating: false,
    liqLeft: 0,
    liqPerDay: 0.0,
    peakEquity: round2(total),
    reserveTiersFired: [],
    cyclesClosed: [],
  };
}

function equityOf(state, price) { // common.equity
  return state.reserve + state.cash + state.shares * price;
}

function closeCycle(state, dateStr, reason) { // log_trade._close_cycle
  var pnl = state.invested ? state.cycleProceeds / state.invested - 1 : 0.0;
  state.cyclesClosed.push({
    id: state.currentCycleId, start: state.cycleStart, end: dateStr,
    invested: round2(state.invested), proceeds: round2(state.cycleProceeds),
    pnlPct: Math.round(pnl * 1e4) / 1e4, reason: reason,
  });
  state.shares = 0.0;
  state.invested = 0.0;
  state.buysDone = 0;
  state.oneBuy = 0.0;
  state.cycleActive = false;
  state.cycleProceeds = 0.0;
  state.liquidating = false;
  state.liqLeft = 0;
  state.liqPerDay = 0.0;
}

/**
 * 매매기록 한 행만큼 상태 전이 — log_trade.py 의 상태 갱신과 동일 규칙 (st 를 제자리 갱신).
 * 새 사이클은 명시 플래그 없이 '사이클 비활성 상태에서의 buy'로 자동 판정한다.
 *
 * t: { date:'YYYY-MM-DD', action:'buy'|'take_profit'|'quarter'|'deploy_reserve',
 *      shares:Number, price:Number, fee:Number|null, refClose:Number|null }
 */
function applyTrade(st, t, cfg) {
  var div = cfg.divisions;
  var fee = (t.fee !== null && t.fee !== undefined && t.fee !== '')
    ? Number(t.fee) : round2(t.shares * t.price * cfg.fee_pct);

  if (t.action === 'deploy_reserve') {
    // log_trade: 남은 트리거 중 첫 단계 절반 편입 (발동 조건 판정은 checkTradeRow 에서)
    for (var r = 0; r < cfg.reserve_triggers.length; r++) {
      var key = Math.round(cfg.reserve_triggers[r] * 100) + '%';
      if (st.reserveTiersFired.indexOf(key) < 0 && st.reserve > 1e-6) {
        var inject = round2(st.reserve * 0.5);
        st.cash += inject;
        st.reserve -= inject;
        st.reserveTiersFired.push(key);
        break;
      }
    }
  } else if (t.action === 'buy') {
    if (!st.cycleActive) { // --new-cycle 에 해당
      st.cycleActive = true;
      st.cycleStart = t.date;
      st.cycleSeq += 1;
      st.currentCycleId = st.cycleSeq;
      st.cycleProceeds = 0.0;
      st.oneBuy = round2(st.cash / div);
      st.buysDone = 0;
    }
    var spend = t.shares * t.price + fee;
    st.cash -= spend;
    st.shares += t.shares;
    st.invested += spend;
    st.buysDone += 1;
  } else if (t.action === 'take_profit') {
    var proceeds = t.shares * t.price - fee;
    st.cash += proceeds;
    st.cycleProceeds += proceeds;
    st.shares -= t.shares;
    closeCycle(st, t.date, 'take_profit');
  } else if (t.action === 'quarter') {
    if (!st.liquidating) {
      st.liquidating = true;
      st.liqPerDay = round6(st.shares / cfg.quarter_days);
      st.liqLeft = cfg.quarter_days;
    }
    var pr = t.shares * t.price - fee;
    st.cash += pr;
    st.cycleProceeds += pr;
    st.shares -= t.shares;
    st.liqLeft -= 1;
    if (st.liqLeft <= 0 || st.shares <= 1e-6) closeCycle(st, t.date, 'exhausted');
  } else if (t.action === 'sell') {
    // 룰북에 없는 임의 매도 — '기록 = 실제로 한 일'이므로 상태에는 그대로 반영하고
    // 준수 판정(checkTradeRow)이 위반으로 표시한다. 무시하면 유령 보유가 생겨 이후
    // 익절/쿼터 판단이 전부 틀어진다.
    var pr2 = t.shares * t.price - fee;
    st.cash += pr2;
    st.cycleProceeds += pr2;
    st.shares -= t.shares;
    if (st.cycleActive && st.shares <= 1e-6) closeCycle(st, t.date, 'manual_sell');
  }
  if (t.refClose) { // 고점 갱신(log_trade._finish) — 기준종가가 있는 행만
    st.peakEquity = round2(Math.max(st.peakEquity, equityOf(st, t.refClose)));
  }
  return st;
}

/**
 * 코어 슬리브 매매기록 전체를 처음부터 재생해 현재 상태를 만든다
 * (state.json 대신 '기록 = 유일한 진실').
 * trades 는 날짜·입력 순서 오름차순이어야 한다.
 */
function replayCore(trades, cfg) {
  var st = defaultState(cfg);
  for (var i = 0; i < trades.length; i++) applyTrade(st, trades[i], cfg);
  return st;
}

/**
 * 매매기록 한 행의 규칙 준수 검증 — log_trade.py 의 checks 와 동일 + §0 ① 충실화.
 * (log_trade.py 는 신규 진입 시 추세만 검사하지만, 룰북 ①은 '추세 AND 카나리아'이므로
 *  여기서는 카나리아도 함께 검사한다.)
 *
 * ctx: { stBefore, cfg, refClose, entrySig, canaryOk, isNewCycle }
 * 반환: { checks: {이름: bool}, notes: [..] }
 */
function checkTradeRow(t, ctx) {
  var cfg = ctx.cfg, st = ctx.stBefore, div = cfg.divisions, tp = cfg.take_profit_pct;
  var checks = {}, notes = [];
  var fee = (t.fee !== null && t.fee !== undefined && t.fee !== '')
    ? Number(t.fee) : round2(t.shares * t.price * cfg.fee_pct);

  if (t.action === 'buy') {
    if (ctx.isNewCycle) {
      checks['신규진입_추세충족'] = !!(ctx.entrySig && ctx.entrySig.entryOk);
      checks['신규진입_카나리아통과'] = !!ctx.canaryOk;
      if (!checks['신규진입_추세충족']) notes.push('⚠️ 추세 미충족인데 새 사이클 진입 — 규칙 위반');
      if (!checks['신규진입_카나리아통과']) notes.push('⚠️ 카나리아 차단인데 새 사이클 진입 — 규칙 위반');
    }
    var prescribed = ctx.isNewCycle ? round2(st.cash / div) : st.oneBuy;
    var spend = t.shares * t.price + fee;
    checks['1회분금액_일치(±5%)'] = prescribed ? Math.abs(spend - prescribed) <= 0.05 * prescribed : false;
    checks['회차한도_분할내'] = (ctx.isNewCycle ? 0 : st.buysDone) < div;
    if (!checks['회차한도_분할내']) notes.push('⚠️ 이미 ' + div + '회분 소진 — 추가 매수는 규칙상 없음');
  } else if (t.action === 'take_profit') {
    var valAtFill = t.shares * t.price * sellCost(cfg);
    checks['익절조건(+' + Math.round(tp * 100) + '%)'] = valAtFill >= st.invested * (1 + tp);
    checks['전량매도'] = Math.abs(t.shares - st.shares) <= Math.max(1e-6, 0.01 * st.shares);
  } else if (t.action === 'quarter') {
    checks['소진후청산(' + div + '회분)'] = st.buysDone >= div;
    var perDay = st.liquidating ? st.liqPerDay : round6(st.shares / cfg.quarter_days);
    checks['1/4수량_일치(±5%)'] = perDay ? Math.abs(t.shares - perDay) <= 0.05 * perDay : false;
  } else if (t.action === 'deploy_reserve') {
    if (ctx.refClose === null || ctx.refClose === undefined) {
      // 기준종가 없이 낙폭을 판정하면 보유주식이 0달러로 평가돼 가짜 발동이 되므로 판정 보류
      checks['리저브발동조건(고점대비)'] = false;
      notes.push('⚠️ 기준종가 없음 — 낙폭 판정 불가(시세 이력 확인)');
    } else {
      var eq = equityOf(st, ctx.refClose);
      var dd = st.peakEquity ? eq / st.peakEquity - 1 : 0.0;
      var fired = false;
      for (var r = 0; r < cfg.reserve_triggers.length; r++) {
        var key = Math.round(cfg.reserve_triggers[r] * 100) + '%';
        if (st.reserveTiersFired.indexOf(key) < 0 && st.reserve > 1e-6 && dd <= cfg.reserve_triggers[r]) {
          fired = true;
          break;
        }
      }
      checks['리저브발동조건(고점대비)'] = fired;
    }
  } else {
    // 코어 슬리브에 룰북(§0)이 정의하지 않은 행동(sell 등) — 기록은 반영하되 위반 표시
    checks['룰북정의행동(코어)'] = false;
    notes.push('⚠️ 코어에 정의되지 않은 행동(' + t.action + ') — §0 위반. 상태에는 기록대로 반영됨');
  }
  return { checks: checks, notes: notes };
}

// ---------- 오늘 할 일 처방 (daily_check.py 의 판단 로직) ----------

/**
 * daily_check.py [오늘 할 일] 과 동일한 판단.
 * 반환 { code, title, detail } — code: TAKE_PROFIT | QUARTER | BUY | NEW_CYCLE | WAIT
 */
function prescribe(state, cfg, entryOk, trendOk, canaryOk, price) {
  var div = cfg.divisions, tp = cfg.take_profit_pct;
  if (state.cycleActive) {
    var val = state.shares * price * sellCost(cfg);
    if (state.shares > 0 && val >= state.invested * (1 + tp) && !state.liquidating) {
      return {
        code: 'TAKE_PROFIT',
        shares: state.shares,
        title: '🎯 익절! 전량 매도 (' + state.shares.toFixed(4) + '주)',
        detail: '평가액이 평단 대비 +' + Math.round(tp * 100) + '% 도달 → 전량 매도 후 매매기록에 take_profit 기입',
      };
    }
    if (state.liquidating || state.buysDone >= div) {
      var per = state.liquidating ? state.liqPerDay : state.shares / cfg.quarter_days;
      var left = state.liquidating ? state.liqLeft : cfg.quarter_days;
      return {
        code: 'QUARTER',
        perDay: per,
        left: left,
        title: '🔻 쿼터손절: 오늘 ' + per.toFixed(4) + '주 매도 (남은 ' + left + '일)',
        detail: div + '회분 소진 → ' + cfg.quarter_days + '일 분할청산. 체결 후 매매기록에 quarter 기입',
      };
    }
    var one = state.oneBuy;
    var est = one / (price * buyCost(cfg));
    return {
      code: 'BUY',
      oneBuy: one,
      estShares: est,
      title: '🟩 정규 매수: 1회분 $' + one.toFixed(2) + ' ≈ ' + est.toFixed(4) + '주 (' + (state.buysDone + 1) + '/' + div + '회차)',
      detail: '종가 부근 매수 후 매매기록에 buy 기입',
    };
  }
  if (entryOk && state.cash > 1e-6) {
    var one2 = state.cash / div;
    var est2 = one2 / (price * buyCost(cfg));
    return {
      code: 'NEW_CYCLE',
      oneBuy: one2,
      estShares: est2,
      title: '🟩 새 사이클 시작 + 1회차 매수: 1회분 $' + one2.toFixed(2) + ' (= 현금/' + div + ') ≈ ' + est2.toFixed(4) + '주',
      detail: '체결 후 매매기록에 buy 기입 (새 사이클은 자동 인식)',
    };
  }
  var why = !trendOk ? '추세 미충족' : (!canaryOk ? '카나리아 차단' : '운용현금 없음');
  return { code: 'WAIT', title: '⏸ 진입 대기 — ' + why + '. 오늘 주문 없음.', detail: '' };
}

/** 파킹(⑦) 안내 — daily_check.py 와 동일. weekday: 0=월 … 4=금 (거래일 기준). */
function parkingInfo(state, cfg, weekday) {
  var one = (state.cycleActive && state.oneBuy > 0) ? state.oneBuy : state.cash / cfg.divisions;
  return {
    holdSgov: state.cash > 1e-6 || state.reserve > 1e-6,
    isFriday: weekday === 4,
    next5: 5 * one,
    oneBuy: one,
  };
}

/** 'YYYY-MM-DD' 의 요일 (0=월 … 6=일) — pandas .weekday() 와 동일. */
function weekdayOf(dateStr) {
  return (parseDate(dateStr).getUTCDay() + 6) % 7;
}

// Node.js(패리티 하네스)에서 불러 쓸 수 있게 내보내기 — Apps Script 에서는 무시됨.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    RULEBOOK_DEFAULTS: RULEBOOK_DEFAULTS,
    CANARY_TICKERS: CANARY_TICKERS,
    round2: round2,
    round6: round6,
    buyCost: buyCost,
    sellCost: sellCost,
    sliceUpTo: sliceUpTo,
    prevMonthEndCutoff: prevMonthEndCutoff,
    isMonthEnd: isMonthEnd,
    signalSeries: signalSeries,
    latestSignal: latestSignal,
    w13612: w13612,
    canaryStatus: canaryStatus,
    blendedMom: blendedMom,
    satelliteStatus: satelliteStatus,
    defaultState: defaultState,
    equityOf: equityOf,
    applyTrade: applyTrade,
    replayCore: replayCore,
    checkTradeRow: checkTradeRow,
    prescribe: prescribe,
    parkingInfo: parkingInfo,
    weekdayOf: weekdayOf,
  };
}
