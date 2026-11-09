'use strict';
// Day 35 (Google Cloud): delivery promises across the end of daylight saving time, written for Node.js 26 and run as
// a Cloud Run job on Google's nodejs26 runtime base image. Same computation as slots.py (the Python reference):
//   - Temporal (on by default in Node.js 26): exact-hour delivery promises, next-day pickups that keep the wall
//     clock, hours in the fall-back day, and the repeated 01:00 hour
//   - Math.sumPrecise: the exactly rounded total of the order lines (a plain reduce drifts by one ulp)
//   - RegExp.escape: find catalog names such as "M&M's (Peanut)" in order lines literally
//   - Uint8Array.fromBase64 + TextDecoder: decode the order data from the job's env var (UTF-8 names intact)
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

// Temporal: a local wall-clock time in a store's zone. PlainDateTime#toZonedDateTime uses disambiguation
// 'compatible', so an ambiguous 01:xx on the fall-back night takes the earlier (daylight) offset.
const zoned = (s, tz) => Temporal.PlainDateTime.from(s).toZonedDateTime(tz);
const minute = (pdt) => pdt.toString({ smallestUnit: 'minute' });
const stamp = (z) => minute(z.toPlainDateTime()) + z.offset;
const hoursBetween = (a, b) => (b.epochMilliseconds - a.epochMilliseconds) / 3600000;

const orders = attempt('Temporal orders', () => data.orders.map((o) => {
  const tz = data.stores[o.store];
  const placed = zoned(o.placed, tz);
  const due = placed.add({ hours: o.promise_hours });                       // exact elapsed hours
  const naiveDue = Temporal.PlainDateTime.from(o.placed).add({ hours: o.promise_hours });  // wall-clock maths
  const pickup = placed.add({ days: 1 });                                   // same wall-clock time tomorrow
  return {
    id: o.id,
    store: o.store,
    placed: stamp(placed),
    deliver_by: stamp(due),
    naive_deliver_by: minute(naiveDue),
    late_if_naive: !naiveDue.equals(due.toPlainDateTime()),
    next_day_pickup: stamp(pickup),
    hours_to_pickup: hoursBetween(placed, pickup),
    second_pass: zoned(due.toPlainDateTime().toString(), tz).offset !== due.offset,
  };
}));

const stores = attempt('Temporal stores', () => Object.fromEntries(Object.keys(data.stores).sort().map((sid) => {
  const tz = data.stores[sid];
  return [sid, {
    time_zone: tz,
    hours_in_day: zoned(data.day + 'T00:00', tz).hoursInDay,
    restock_hours: hoursBetween(zoned(data.restock_shift.start, tz), zoned(data.restock_shift.end, tz)),
  }];
})));

const prices = data.orders.flatMap((o) => o.lines.map((l) => l[1]));
const texts = data.orders.flatMap((o) => o.lines.map((l) => l[0]));
const totalPrecise = attempt('Math.sumPrecise', () => Math.sumPrecise(prices));
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
    errors,
    ms: Date.now() - t0,
  },
};
console.log('DAY35_RESULT ' + JSON.stringify(result));
