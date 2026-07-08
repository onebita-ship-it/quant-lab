/**
 * run_signals.mjs — signals.gs 를 Node 에서 실행해 패리티 감사용 출력을 만든다.
 *
 * 사용: node run_signals.mjs <payload.json>   (결과 JSON 을 stdout 으로)
 * payload = {
 *   cfg: {...},                      // journal.common.DEFAULT_CONFIG 대응
 *   universe: [{ticker,cls,index}],  // config/universe.txt
 *   csv: { QQQ: 'data/QQQ.csv', ... },
 *   asofGrid: ['YYYY-MM-DD', ...],   // latest_signal 비교 기준일
 *   monthsGrid: ['YYYY-MM-DD', ...], // canary_status 비교 기준일
 *   satGrid: ['YYYY-MM-DD', ...],    // satellite_status 비교 기준일
 *   trades: [...]                    // replayCore 교차검증용 (없으면 생략)
 * }
 */
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const code = fs.readFileSync(path.join(here, '..', 'signals.gs'), 'utf8');
const sandbox = { module: { exports: {} }, Math, Date, JSON };
vm.runInNewContext(code, sandbox);
const G = sandbox.module.exports;

function readCsvSeries(csvPath) {
  const text = fs.readFileSync(csvPath, 'utf8').trim();
  const lines = text.split(/\r?\n/);
  const header = lines[0].split(',');
  const di = header.indexOf('Date');
  const ci = header.indexOf('Close');
  if (di < 0 || ci < 0) throw new Error(csvPath + ': Date/Close 열 없음');
  const dates = [], closes = [];
  for (let i = 1; i < lines.length; i++) {
    const parts = lines[i].split(',');
    const c = parseFloat(parts[ci]);
    if (!parts[di] || !isFinite(c)) continue;
    dates.push(parts[di].slice(0, 10));
    closes.push(c);
  }
  return { dates, closes };
}

const payload = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const cfg = payload.cfg;
const S = {};
for (const [t, p] of Object.entries(payload.csv)) S[t] = readCsvSeries(p);

const out = {};
const qqq = S[cfg.signal_ticker];

// 1) 추세 시리즈 전 구간
{
  const sig = G.signalSeries(qqq, cfg);
  out.trend = {
    dates: sig.dates,
    ma: sig.ma,
    above: sig.above,
    rising: sig.rising,
    ok: sig.ok,
    entryOk: sig.entryOk,
  };
}

// 2) latest_signal (asofGrid)
out.latest = (payload.asofGrid || []).map((asof) => {
  const r = G.latestSignal(qqq, cfg, asof);
  return { asof, date: r.date, close: r.close, ma: r.ma, above: r.above, rising: r.rising, streak: r.streak, entryOk: r.entryOk };
});

// 3) 카나리아 (monthsGrid)
out.canary = (payload.monthsGrid || []).map((asof) => {
  const c = G.canaryStatus(S, asof);
  return { asof, ok: c.ok, cutoff: c.cutoff, assets: c.assets };
});

// 4) 위성 (satGrid)
out.satellite = (payload.satGrid || []).map((asof) => {
  const s = G.satelliteStatus(payload.universe, S, cfg, asof);
  return { asof, target: s.target, assets: s.assets };
});

// 5) 월말/요일 판정 (신호 티커 전 거래일)
out.calendar = qqq.dates.map((d) => ({ date: d, monthEnd: G.isMonthEnd(d), weekday: G.weekdayOf(d) }));

// 6) 리플레이 교차검증
function replayWithChecks(trades, rcfg) {
  const st = G.replayCore(trades, rcfg);
  const st2 = G.defaultState(rcfg);
  const rowChecks = trades.map((t) => {
    const isNew = t.action === 'buy' && !st2.cycleActive;
    let entrySig = null;
    if (isNew) entrySig = G.latestSignal(qqq, rcfg, t.date);
    const chk = G.checkTradeRow(t, {
      stBefore: st2, cfg: rcfg, refClose: t.refClose === undefined ? null : t.refClose, entrySig,
      canaryOk: t.canaryOk === undefined ? null : t.canaryOk, isNewCycle: isNew,
    });
    G.applyTrade(st2, t, rcfg);
    return { date: t.date, action: t.action, checks: chk.checks };
  });
  return { state: st, rowChecks };
}

if (payload.trades) {
  const r = replayWithChecks(payload.trades, cfg);
  out.replay = r.state;
  out.rowChecks = r.rowChecks;
}

// 6b) 리저브 시나리오 (deploy_frac<1 설정으로 별도 리플레이)
if (payload.reserve) {
  const rcfg = payload.reserve.cfg;
  out.reserve = replayWithChecks(payload.reserve.trades, rcfg);
}

// 6b') 리저브 미발동 판정 (상태 전이는 의도된 차이라 준수 판정만 비교)
if (payload.reserveNegative) {
  const rn = payload.reserveNegative;
  const chk = G.checkTradeRow(rn.trade, {
    stBefore: G.defaultState(rn.cfg), cfg: rn.cfg, refClose: rn.trade.refClose,
    entrySig: null, canaryOk: null, isNewCycle: false,
  });
  out.reserveNegative = chk.checks;
}

// 6c) 오늘 할 일 처방 + 파킹 (daily_check.py 와 교차검증)
if (payload.prescribeCases) {
  out.prescribe = payload.prescribeCases.map((c) => {
    const sig = G.latestSignal(qqq, cfg, c.asof);
    const canary = G.canaryStatus(S, c.asof);
    const entryOk = sig.entryOk && canary.ok;
    const tq = G.sliceUpTo(S[cfg.trade_ticker], c.asof);
    const price = tq.closes[tq.closes.length - 1];
    const priceDate = tq.dates[tq.dates.length - 1];
    const rx = G.prescribe(c.state, cfg, entryOk, sig.entryOk, canary.ok, price);
    const park = G.parkingInfo(c.state, cfg, G.weekdayOf(priceDate));
    return {
      asof: c.asof, code: rx.code, title: rx.title,
      oneBuy: rx.oneBuy === undefined ? null : rx.oneBuy,
      estShares: rx.estShares === undefined ? null : rx.estShares,
      perDay: rx.perDay === undefined ? null : rx.perDay,
      left: rx.left === undefined ? null : rx.left,
      shares: rx.shares === undefined ? null : rx.shares,
      parking: { isFriday: park.isFriday, next5: park.next5, oneBuy: park.oneBuy },
    };
  });
}

// 7) deploy_reserve 전이 자가검증 (기록=진실 원칙: 조건 미충족이어도 기록대로 이체 + 위반 플래그)
{
  const c2 = Object.assign({}, cfg, { deploy_frac: 0.67 });
  const st = G.defaultState(c2);
  const before = st.reserve;
  G.applyTrade(st, { date: '2026-01-02', action: 'deploy_reserve', shares: 0, price: 0, fee: null }, c2);
  out.reserveSelfTest = {
    injectedHalf: Math.abs(st.reserve - before / 2) < 0.02 && Math.abs(st.cash - (c2.total_capital * 0.67 + before / 2)) < 0.02,
    tierFired: st.reserveTiersFired.length === 1,
  };
}

process.stdout.write(JSON.stringify(out));
