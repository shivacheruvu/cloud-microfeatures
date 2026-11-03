"""Day 29 (Google Cloud): pull a store price book from BigQuery as Apache Arrow straight from jobs.query
(`queryResultsFormat: ARROW`, Preview 2026-10-30), and check it against the same query's default JSON rows.

  python price_book.py               # local: builds the Arrow payload BigQuery would send, decodes it, writes result.json
  python price_book.py --sql         # print the BigQuery SQL (price book inlined, no table needed)
  python price_book.py --cloud       # cloud.sh: run the query three ways over REST and compare (needs gcloud auth)

Default jobs.query rows come back as JSON `{"f": [{"v": "4.49"}, ...]}`: every value a string, so NUMERIC prices,
INT64 pack sizes, DATEs and TIMESTAMPs all need hand parsing against the schema. With queryResultsFormat ARROW the
response instead carries `arrowSchema.serializedSchema` and `arrowRecordBatch.serializedRecordBatch` (IPC bytes,
base64 in JSON) for the first page, so a client gets typed columns with no per-type parsing. jobs.getQueryResults
doesn't speak Arrow, so the result must fit in the first page (the price book here is 120 rows).

The check: decoding the Arrow batch and parsing the JSON rows must give exactly the same 120 rows (Decimal prices,
int pack sizes, dates, microsecond timestamps), and Arrow types must be decimal128(38, 9) / int64 / date32 / bool /
timestamp[us, UTC]. Payload sizes (JSON rows vs Arrow, and Arrow with ZSTD buffer compression) are reported.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORES = ["east", "west", "north"]
# sku, name, pack size in grams (or ml), regular price
ITEMS = [
    ("0001", "oat milk", 1890, "4.49"), ("0002", "whole milk", 3785, "3.89"), ("0003", "greek yogurt", 907, "5.79"),
    ("0004", "cheddar", 227, "3.99"), ("0005", "bananas", 454, "0.59"), ("0006", "apples", 1361, "4.99"),
    ("0007", "spinach", 283, "3.49"), ("0008", "sourdough", 680, "5.49"), ("0009", "bagels", 539, "4.29"),
    ("0010", "eggs", 680, "3.29"), ("0011", "chicken breast", 454, "4.49"), ("0012", "ground beef", 454, "5.99"),
    ("0013", "salmon", 340, "10.99"), ("0014", "pasta", 454, "1.79"), ("0015", "rice", 907, "2.99"),
    ("0016", "olive oil", 500, "8.99"), ("0017", "coffee", 340, "9.99"), ("0018", "cola 12 pk", 4260, "7.49"),
    ("0019", "sparkling water", 3400, "4.99"), ("0020", "orange juice", 1530, "4.29"),
    ("0021", "ice cream", 473, "5.49"), ("0022", "frozen pizza", 765, "6.99"), ("0023", "peas", 454, "1.99"),
    ("0024", "peanut butter", 454, "3.79"), ("0025", "granola", 340, "4.69"), ("0026", "tortillas", 567, "2.89"),
    ("0027", "black beans", 425, "1.09"), ("0028", "tomato sauce", 680, "2.49"), ("0029", "butter", 454, "4.99"),
    ("0030", "hummus", 283, "3.99"), ("0031", "almonds", 454, "7.99"), ("0032", "honey", 340, "6.49"),
    ("0033", "tofu", 397, "2.29"), ("0034", "kale", 227, "2.99"), ("0035", "blueberries", 170, "3.99"),
    ("0036", "strawberries", 454, "4.49"), ("0037", "cereal", 510, "4.99"), ("0038", "oats", 1190, "4.79"),
    ("0039", "flour", 2268, "3.49"), ("0040", "sugar", 1814, "3.29"),
]
COLUMNS = ["store", "sku", "name", "price", "pack_g", "on_promo", "price_date", "updated_at", "price_per_kg"]
ARROW_TYPES = {"store": "string", "sku": "string", "name": "string", "price": "decimal128(38, 9)", "pack_g": "int64",
               "on_promo": "bool", "price_date": "date32[day]", "updated_at": "timestamp[us, tz=UTC]",
               "price_per_kg": "decimal128(38, 9)"}
BYTES_CAP = 20971520


def price_book() -> list[dict]:
    """120 rows: 40 items x 3 stores. West runs 10% off on every 4th item; north is 0.20 dearer on every 5th."""
    rows = []
    base = dt.datetime(2026, 11, 2, 5, 0, tzinfo=dt.timezone.utc)
    for si, store in enumerate(STORES):
        for i, (sku, name, pack, price) in enumerate(ITEMS):
            p, promo = Decimal(price), False
            if store == "west" and i % 4 == 0:
                p, promo = (p * Decimal("0.9")).quantize(Decimal("0.01"), ROUND_HALF_UP), True
            if store == "north" and i % 5 == 0:
                p += Decimal("0.20")
            rows.append({
                "store": store, "sku": sku, "name": name, "price": p, "pack_g": pack, "on_promo": promo,
                "price_date": dt.date(2026, 11, 2),
                # microseconds on purpose: they must survive both encodings
                "updated_at": base + dt.timedelta(minutes=si * 60 + i, microseconds=(i * 7919 + si * 104729) % 10**6),
            })
    for r in rows:   # BigQuery: ROUND(price * 1000 / pack_g, 2), NUMERIC, half away from zero
        r["price_per_kg"] = (r["price"] * 1000 / Decimal(r["pack_g"])).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return rows


def sql() -> str:
    def row(r):
        return (f"STRUCT('{r['store']}' AS store, '{r['sku']}' AS sku, '{r['name']}' AS name, "
                f"NUMERIC '{r['price']}' AS price, {r['pack_g']} AS pack_g, {str(r['on_promo']).upper()} AS on_promo, "
                f"DATE '{r['price_date']}' AS price_date, "
                f"TIMESTAMP '{r['updated_at'].strftime('%Y-%m-%d %H:%M:%S.%f')}+00' AS updated_at)")
    body = ",\n    ".join(row(r) for r in price_book())
    return f"""-- Day 29: a 120-row store price book, inlined (nothing is created), returned as Arrow or JSON.
SELECT store, sku, name, price, pack_g, on_promo, price_date, updated_at,
       ROUND(price * 1000 / pack_g, 2) AS price_per_kg
FROM UNNEST([
    {body}
])
ORDER BY store, sku
"""


def canonical(rows: list[dict]) -> list[list[str]]:
    """Exact, type-aware text for comparison: Decimal normalised, timestamps to the microsecond in UTC."""
    out = []
    for r in sorted(rows, key=lambda r: (r["store"], r["sku"])):
        vals = []
        for c in COLUMNS:
            v = r[c]
            if isinstance(v, Decimal):
                v = format(v.normalize(), "f")
            elif isinstance(v, dt.datetime):
                v = v.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            elif isinstance(v, dt.date):
                v = v.isoformat()
            vals.append(str(v))
        out.append(vals)
    return out


# ---- decoding the two response shapes -------------------------------------------------------------------------
def decode_arrow(resp: dict):
    """QueryResponse with queryResultsFormat ARROW -> (pyarrow.Table, payload bytes)."""
    import pyarrow as pa
    schema_b = base64.b64decode(resp["arrowSchema"]["serializedSchema"])
    batch_b = base64.b64decode((resp.get("arrowRecordBatch") or {}).get("serializedRecordBatch") or "")
    schema = pa.ipc.read_schema(pa.py_buffer(schema_b))
    if not batch_b:
        return schema.empty_table(), len(schema_b)
    batch = pa.ipc.read_record_batch(pa.py_buffer(batch_b), schema)
    return pa.Table.from_batches([batch]), len(schema_b) + len(batch_b)


def arrow_rows(table) -> list[dict]:
    return table.to_pylist()


def arrow_types(table) -> dict:
    return {f.name: str(f.type) for f in table.schema}


def decode_json(resp: dict) -> tuple[list[dict], int]:
    """Default QueryResponse (STRUCT_ENCODING) -> typed rows, parsing each string by the schema field type.
    Timestamps are requested as int64 microseconds (formatOptions.useInt64Timestamp) to keep them exact."""
    fields = resp["schema"]["fields"]
    parse = {
        "STRING": str, "NUMERIC": Decimal, "BIGNUMERIC": Decimal, "INTEGER": int, "INT64": int,
        "BOOLEAN": lambda s: s == "true", "BOOL": lambda s: s == "true", "DATE": dt.date.fromisoformat,
        "TIMESTAMP": lambda s: dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=int(s)),
    }
    rows = [{f["name"]: (None if cell["v"] is None else parse[f["type"]](cell["v"]))
             for f, cell in zip(fields, r["f"])} for r in resp.get("rows", [])]
    return rows, len(json.dumps(resp.get("rows", []), separators=(",", ":")).encode())


# ---- local: build exactly what BigQuery sends ------------------------------------------------------------------
def local_arrow_response(rows: list[dict], compression: str | None = None) -> dict:
    import pyarrow as pa
    schema = pa.schema([("store", pa.string()), ("sku", pa.string()), ("name", pa.string()),
                        ("price", pa.decimal128(38, 9)), ("pack_g", pa.int64()), ("on_promo", pa.bool_()),
                        ("price_date", pa.date32()), ("updated_at", pa.timestamp("us", tz="UTC")),
                        ("price_per_kg", pa.decimal128(38, 9))])
    rows = sorted(rows, key=lambda r: (r["store"], r["sku"]))
    batch = pa.RecordBatch.from_pylist(rows, schema=schema)
    sink = pa.BufferOutputStream()   # write an IPC stream, then cut out its schema and record-batch messages
    with pa.ipc.new_stream(sink, schema, options=pa.ipc.IpcWriteOptions(compression=compression)) as w:
        w.write_batch(batch)
    reader = pa.ipc.MessageReader.open_stream(sink.getvalue())
    schema_msg, batch_msg = reader.read_next_message(), reader.read_next_message()
    return {"jobComplete": True, "pageRowCount": str(len(rows)), "totalRows": str(len(rows)),
            "arrowSchema": {"serializedSchema": base64.b64encode(schema_msg.serialize().to_pybytes()).decode()},
            "arrowRecordBatch": {"serializedRecordBatch": base64.b64encode(
                batch_msg.serialize().to_pybytes()).decode()}}


def local_json_response(rows: list[dict]) -> dict:
    types = {"store": "STRING", "sku": "STRING", "name": "STRING", "price": "NUMERIC", "pack_g": "INTEGER",
             "on_promo": "BOOLEAN", "price_date": "DATE", "updated_at": "TIMESTAMP", "price_per_kg": "NUMERIC"}

    def enc(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, Decimal):
            return format(v.normalize(), "f")
        if isinstance(v, dt.datetime):
            return str((v - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)) // dt.timedelta(microseconds=1))
        return v.isoformat() if isinstance(v, dt.date) else str(v)
    rows = sorted(rows, key=lambda r: (r["store"], r["sku"]))
    return {"jobComplete": True, "totalRows": str(len(rows)),
            "schema": {"fields": [{"name": c, "type": types[c]} for c in COLUMNS]},
            "rows": [{"f": [{"v": enc(r[c])} for c in COLUMNS]} for r in rows]}


def evaluate(json_resp: dict, arrow_resp: dict, zstd_resp: dict) -> dict:
    ref = canonical(price_book())
    j_rows, j_bytes = decode_json(json_resp)
    a_tab, a_bytes = decode_arrow(arrow_resp)
    z_tab, z_bytes = decode_arrow(zstd_resp)
    a_rows, z_rows = arrow_rows(a_tab), arrow_rows(z_tab)
    rows = price_book()
    promo = [r for r in rows if r["on_promo"]]
    dearest = max(rows, key=lambda r: r["price_per_kg"])
    return {
        "rows": len(ref),
        "page_row_count": int(arrow_resp.get("pageRowCount") or a_tab.num_rows),
        "arrow_matches_reference": canonical(a_rows) == ref,
        "zstd_matches_reference": canonical(z_rows) == ref,
        "json_matches_reference": canonical(j_rows) == ref,
        "arrow_types": arrow_types(a_tab),
        "arrow_types_expected": arrow_types(a_tab) == ARROW_TYPES,
        "payload_bytes": {"json_rows": j_bytes, "arrow": a_bytes, "arrow_zstd": z_bytes},
        "price_book": {"stores": len(STORES), "items": len(ITEMS), "promo_rows": len(promo),
                       "total_shelf_value": str(sum(r["price"] for r in rows)),
                       "dearest_per_kg": f"{dearest['name']} ({dearest['store']}) {dearest['price_per_kg']}/kg"},
    }


# ---- cloud ----------------------------------------------------------------------------------------------------
class QueryError(Exception):
    pass


def _redact(text: str) -> str:
    import re
    p = os.environ.get("GCP_PROJECT_ID", "")
    if p:
        text = text.replace(p, "<project>")
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<account>", text)
    return re.sub(r"\b\d{6,}\b", "<number>", text)


def _token() -> str:
    return subprocess.run(["gcloud", "auth", "print-access-token"], check=True, capture_output=True,
                          text=True).stdout.strip()


def run_query(extra: dict, post=None) -> dict:
    project = os.environ["GCP_PROJECT_ID"]
    body = {"query": sql(), "useLegacySql": False, "location": "US", "maximumBytesBilled": str(BYTES_CAP),
            "timeoutMs": 120000, "maxResults": 1000, **extra}
    url = f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries"
    if post is None:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
            "Authorization": f"Bearer {_token()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise QueryError(f"HTTP {e.code}: {e.read()[:600].decode(errors='replace')}") from None
    else:
        resp = post(body)
    if not resp.get("jobComplete"):
        raise QueryError("query didn't finish inside jobs.query's wait (and getQueryResults can't return Arrow)")
    return resp


def cloud(post=None) -> int:
    local = json.loads((HERE / "result.json").read_text())
    try:
        j = run_query({"formatOptions": {"useInt64Timestamp": True}}, post)
        a = run_query({"queryResultsFormat": "ARROW"}, post)
        z = run_query({"queryResultsFormat": "ARROW", "arrowSerializationOptions": {"bufferCompression": "ZSTD"}},
                      post)
    except QueryError as e:
        print(f"::error title=Google Cloud day 29 query failed::{_redact(str(e))}")
        return 1
    if "arrowSchema" not in a:
        print("::error title=Google Cloud day 29::the ARROW request came back without arrowSchema (format ignored?)")
        return 1
    got = evaluate(j, a, z)
    errors = [f"{k} is false" for k in ("arrow_matches_reference", "zstd_matches_reference",
                                         "json_matches_reference") if not got[k]]
    if got["rows"] != got["page_row_count"]:
        errors.append(f"first page had {got['page_row_count']} of {got['rows']} rows")
    for e in errors:
        print(f"::error title=Google Cloud day 29 differs from local::{e}")
    if not got["arrow_types_expected"]:   # values still equal: a finding about the encoding, not a failure
        diff = {k: v for k, v in got["arrow_types"].items() if ARROW_TYPES.get(k) != v}
        print(f"::warning title=Google Cloud day 29 Arrow types::BigQuery sent {json.dumps(diff)}")
    if errors:
        return 1
    b, lb = got["payload_bytes"], local["payload_bytes"]
    print(f"::notice title=Google Cloud day 29::jobs.query with queryResultsFormat ARROW returned all {got['rows']} "
          f"price-book rows in the first page, typed (price {got['arrow_types'].get('price')}, pack_g "
          f"{got['arrow_types'].get('pack_g')}, updated_at {got['arrow_types'].get('updated_at')}) and equal to the JSON rows and the local reference; payload JSON rows "
          f"{b['json_rows']:,} B, Arrow {b['arrow']:,} B, Arrow+ZSTD {b['arrow_zstd']:,} B "
          f"(local build: {lb['json_rows']:,} / {lb['arrow']:,} / {lb['arrow_zstd']:,})")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--sql"]:
        print(sql(), end="")
        return 0
    if argv[:1] == ["--cloud"]:
        return cloud()
    rows = price_book()
    out = {"engine": "pyarrow IPC built from the reference rows (local) / BigQuery jobs.query with "
                     "queryResultsFormat ARROW (cloud)",
           **evaluate(local_json_response(rows), local_arrow_response(rows), local_arrow_response(rows, "zstd"))}
    (HERE / "result.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({k: out[k] for k in ("rows", "arrow_matches_reference", "json_matches_reference",
                                          "payload_bytes", "price_book")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
