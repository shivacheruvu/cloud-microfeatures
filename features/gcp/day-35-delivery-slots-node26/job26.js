'use strict';
// Day 35 (Google Cloud): delivery promises across the end of daylight saving time, written for Node.js 26 and run as
// a Cloud Run job on Google's nodejs26 runtime base image. Same computation as slots.py (the Python reference):
//   - RegExp.escape: find catalog names such as "M&M's (Peanut)" in order lines literally
//   - Uint8Array.fromBase64 + TextDecoder: decode the order data from the job's env var (UTF-8 names intact)
//   - time zones: Temporal when the runtime has it, otherwise Intl.DateTimeFormat (the first cloud run, 2026-11-08,
//     showed Node.js 26 on this image has no Temporal global and no Math.sumPrecise); runtime.tz_engine says which
//   - order-line total: Math.sumPrecise when present, otherwise an exact integer-cents sum
// The job also probes whether Temporal is available behind the --harmony-temporal V8 flag.
// Every section is guarded so one missing feature is reported instead of hiding the others. ASCII only: the job's
// command line decodes this file with atob().
const t0 = Date.now();
const features = {
  'Temporal': typeof globalThis.Temporal,
  'Math.sumPrecise': typeof Math.sumPrecise,
  'RegExp.escape': typeof RegExp.escape,
  'Uint8Array.fromBase64': typeof Uint8Array.fromBase64,
};
const errors = {};
function attempt(name, fn) {
  try {
    return fn();
  } catch (e) {
    errors[name] = String((e && e.message) || e).slice(0, 300);
    return null;
  }
}

const raw = process.env.DAY35_DATA || '';
const data = attempt('Uint8Array.fromBase64', () => JSON.parse(new TextDecoder().decode(Uint8Array.fromBase64(raw))))
  || JSON.parse(Buffer.from(raw, 'base64').toString('utf8'));

// ---- time zones -----------------------------------------------------------------------------------------------
// Temporal path: PlainDateTime#toZonedDateTime uses disambiguation 'compatible', so an ambiguous 01:xx on the
// fall-back night takes the earlier (daylight) offset. The Intl path implements the same rule.
const HOUR = 3600000;
const pad = (n, w = 2) => String(Math.abs(n)).padStart(w, '0');
const fmtCache = new Map();
function wallParts(ms, tz) {           // epoch ms -> wall clock fields in tz (Intl)
  if (!fmtCache.has(tz)) {
    fmtCache.set(tz, new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric',
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }));
  }
  const p = Object.fromEntries(fmtCache.get(tz).formatToParts(new Date(ms)).map((x) => [x.type, Number(x.value)]));
  return Date.UTC(p.year, p.month - 1, p.day, p.hour % 24, p.minute, p.second);
}
const offsetMs = (ms, tz) => wallParts(ms, tz) - ms;
const parseWall = (s) => {             // 'YYYY-MM-DDTHH:MM' -> the same wall clock as a UTC epoch
  const [d, t] = s.split('T');
  const [y, mo, da] = d.split('-').map(Number);
  const [h, mi] = t.split(':').map(Number);
  return Date.UTC(y, mo - 1, da, h, mi);
};
const wallString = (w) => {
  const d = new Date(w);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
};
const offsetString = (o) => `${o < 0 ? '-' : '+'}${pad(Math.trunc(Math.abs(o) / HOUR))}:${pad(Math.abs(o) % HOUR / 60000)}`;
function wallToEpoch(w, tz) {          // 'compatible': earlier of two candidates; in a gap, use the offset before it
  const before = offsetMs(w - 24 * HOUR, tz);
  const after = offsetMs(w + 24 * HOUR, tz);
  const valid = [...new Set([before, after])].map((o) => w - o).filter((t) => wallParts(t, tz) === w);
  return valid.length ? Math.min(...valid) : w - before;
}

const intlZone = {
  name: 'Intl.DateTimeFormat',
  zoned: (s, tz) => ({ ms: wallToEpoch(parseWall(s), tz), tz }),
  addHours: (z, h) => ({ ms: z.ms + h * HOUR, tz: z.tz }),
  addDays: (z, n) => ({ ms: wallToEpoch(wallParts(z.ms, z.tz) + n * 24 * HOUR, z.tz), tz: z.tz }),
  wall: (z) => wallString(wallParts(z.ms, z.tz)),
  stamp: (z) => wallString(wallParts(z.ms, z.tz)) + offsetString(offsetMs(z.ms, z.tz)),
  ms: (z) => z.ms,
  hoursInDay: (day, tz) => (wallToEpoch(parseWall(day + 'T00:00') + 24 * HOUR, tz) - wallToEpoch(parseWall(day + 'T00:00'), tz)) / HOUR,
};
const temporalZone = {
  name: 'Temporal',
  zoned: (s, tz) => Temporal.PlainDateTime.from(s).toZonedDateTime(tz),
  addHours: (z, h) => z.add({ hours: h }),
  addDays: (z, n) => z.add({ days: n }),
  wall: (z) => z.toPlainDateTime().toString({ smallestUnit: 'minute' }),
  stamp: (z) => z.toPlainDateTime().toString({ smallestUnit: 'minute' }) + z.offset,
  ms: (z) => z.epochMilliseconds,
  hoursInDay: (day, tz) => Temporal.PlainDateTime.from(day + 'T00:00').toZonedDateTime(tz).hoursInDay,
};
const hasTemporal = typeof globalThis.Temporal === 'object';
const naivePlus = (s, h) => wallString(parseWall(s) + h * HOUR);   // wall-clock maths, ignores DST

function zoneFigures(Z) {
  const hoursBetween = (a, b) => (Z.ms(b) - Z.ms(a)) / HOUR;
    const orders = data.orders.map((o) => {
    const tz = data.stores[o.store];
    const placed = Z.zoned(o.placed, tz);
    const due = Z.addHours(placed, o.promise_hours);          // exact elapsed hours
    const naiveDue = naivePlus(o.placed, o.promise_hours);    // wall-clock maths
    const pickup = Z.addDays(placed, 1);                      // same wall-clock time tomorrow
    return {
      id: o.id,
      store: o.store,
      placed: Z.stamp(placed),
      deliver_by: Z.stamp(due),
      naive_deliver_by: naiveDue,
      late_if_naive: naiveDue !== Z.wall(due),
      next_day_pickup: Z.stamp(pickup),
      hours_to_pickup: hoursBetween(placed, pickup),
      second_pass: Z.stamp(Z.zoned(Z.wall(due), tz)) !== Z.stamp(due),
    };
  });
  const stores = Object.fromEntries(Object.keys(data.stores).sort().map((sid) => {
    const tz = data.stores[sid];
    return [sid, {
      time_zone: tz,
      hours_in_day: Z.hoursInDay(data.day, tz),
      restock_hours: hoursBetween(Z.zoned(data.restock_shift.start, tz), Z.zoned(data.restock_shift.end, tz)),
    }];
  }));
  return { orders, stores };
}

// Temporal when the runtime has it; Intl always (as the fallback, and to cross-check Temporal).
const viaIntl = attempt('time zones (Intl)', () => zoneFigures(intlZone));
let temporalNote = hasTemporal ? null : 'no Temporal global';
let viaTemporal = null;
if (hasTemporal) {
  try {
    viaTemporal = zoneFigures(temporalZone);
  } catch (e) {
    temporalNote = 'Temporal threw: ' + String((e && e.message) || e).slice(0, 200);
  }
}
const zone = viaTemporal || viaIntl;
const tzEngine = viaTemporal ? 'Temporal' : intlZone.name;
const enginesAgree = viaTemporal && viaIntl ? JSON.stringify(viaTemporal) === JSON.stringify(viaIntl) : null;
const orders = zone && zone.orders;
const stores = zone && zone.stores;

const prices = data.orders.flatMap((o) => o.lines.map((l) => l[1]));
const texts = data.orders.flatMap((o) => o.lines.map((l) => l[0]));
const sumEngine = typeof Math.sumPrecise === 'function' ? 'Math.sumPrecise' : 'integer cents';
const totalPrecise = attempt('exact total', () => (sumEngine === 'Math.sumPrecise' ? Math.sumPrecise(prices)
  : prices.reduce((a, p) => a + Math.round(p * 100), 0) / 100));

// Is Temporal there behind a V8 flag? (report only)
const probe = attempt('temporal flag probe', () => {
  const r = require('child_process').spawnSync(process.execPath, ['--harmony-temporal', '-e',
    'console.log(typeof Temporal)'], { encoding: 'utf8', timeout: 20000 });
  return (r.stdout || '').trim() || `exit ${r.status}: ${(r.stderr || '').trim().slice(0, 160)}`;
});
const nameMatches = attempt('RegExp.escape', () => data.catalog_names.map((name) => {
  const exact = new RegExp(RegExp.escape(name), 'i');
  const loose = new RegExp(name, 'i');
  return [name, texts.filter((t) => exact.test(t)).length, texts.filter((t) => loose.test(t)).length];
}));

const result = {
  orders: orders && orders.map(({ second_pass, ...rest }) => rest),
  stores,
  late_if_naive: orders && orders.filter((o) => o.late_if_naive).length,
  deliveries_in_repeated_hour: orders && orders.filter((o) => o.second_pass).length,
  total_naive: prices.reduce((a, b) => a + b, 0),
  total_precise: totalPrecise,
  name_matches: nameMatches,
  utf8_names: texts.filter((t) => /[^\x00-\x7f]/.test(t)),
  runtime: {
    node: process.versions.node,
    v8: process.versions.v8,
    features,
    tz_engine: tzEngine,
    temporal_note: temporalNote,
    engines_agree: enginesAgree,
    sum_engine: sumEngine,
    temporal_with_harmony_flag: probe,
    errors,
    ms: Date.now() - t0,
  },
};
console.log('DAY35_RESULT ' + JSON.stringify(result));
