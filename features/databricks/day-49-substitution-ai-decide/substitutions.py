"""Day 49 (Databricks): out-of-stock substitution calls for grocery pickers with **ai_decide** (Beta, Databricks
platform release notes, October 2026): one inference per row answers several questions over the same `state`, a
label (`choice`), a likelihood (`noul`) and a rubric score (`score`), returned as one VARIANT.

Twelve Thanksgiving orders have an item out of stock and a proposed substitute ("Gluten-free stuffing mix" ->
"Classic stuffing mix", "Unsalted butter" -> "Salted butter", "Large eggs, 12 count" -> "18 count"). The picker app
needs, per pair: accept / ask the customer / reject, whether the substitute brings an allergen the ordered item
doesn't have, and how close the two products are. One ai_decide call per row answers all three:

  verdict        choice  accept | ask_customer | reject
  new_allergen   noul    probability the substitute adds an allergen
  closeness      score   0 different product .. 2 practically identical

The store's rule is written down in the question criteria and implemented here as a local reference (`rule`), which
must reproduce the hand-labelled answer key in data/substitutions.csv. Model answers can't be reproduced locally, so
the cloud check is structural and agreement is reported.

  python substitutions.py                # local: rule vs answer key, writes result.json
  python substitutions.py --sql          # print the Databricks SQL
  python substitutions.py --databricks   # serverless SQL warehouse via the Statement Execution API (cloud.sh)

Must hold (else the run fails): 12 rows; each has a non-null response and no error_message, a verdict choice that is
one of the three labels, a new_allergen probability in [0, 1] and a closeness score in [0, 2]. Reported: verdicts and
allergen flags (probability > 0.5) equal to the key; a disagreement is a warning (a finding about the model).
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABELS = ("accept", "ask_customer", "reject")
QUESTIONS = {
    "verdict": {
        "type": "choice",
        "instructions": "A grocery item in an online order is out of stock. Should the picker use the proposed "
                        "substitute?",
        "criteria": {
            "accept": "Same kind of product and same variety, flavor or texture; only the brand, organic status or "
                      "a size within 20 percent differs; price at most 20 percent higher; no new allergen.",
            "ask_customer": "Same kind of product but a different variety, flavor or texture, or the size or price "
                            "differs by more than 20 percent; no new allergen.",
            "reject": "A different kind of product, or the substitute contains an allergen that the ordered item "
                      "does not contain.",
        },
    },
    "new_allergen": {
        "type": "noul",
        "instructions": "Does the substitute contain any allergen that is not in the allergen list of the ordered "
                        "item? Use only the two allergen lists given.",
    },
    "closeness": {
        "type": "score",
        "instructions": "How close is the substitute to the ordered item for a shopper?",
        "criteria": ["Different product", "Same product, different variety", "Practically identical"],
    },
}
QUESTIONS_JSON = json.dumps(QUESTIONS, separators=(",", ":"))


def pairs() -> list[dict]:
    with open(HERE / "data" / "substitutions.csv", newline="") as f:
        out = []
        for r in csv.DictReader(f):
            for k in ("sub_no", "ordered_price", "sub_price"):
                r[k] = int(r[k])
            for k in ("ordered_size", "sub_size"):
                r[k] = float(r[k])
            for k in ("ordered_allergens", "sub_allergens"):
                r[k] = [a for a in r[k].split(";") if a]
            r["new_allergen"] = r["new_allergen"] == "1"
            out.append(r)
        return out


def rule(p: dict) -> tuple[str, bool]:
    """The store's substitution rule on the structured fields: (verdict, new allergen)."""
    new = bool(set(p["sub_allergens"]) - set(p["ordered_allergens"]))
    if new or p["sub_kind"] != p["ordered_kind"]:
        return "reject", new
    if (p["sub_variant"] == p["ordered_variant"] and abs(p["sub_size"] / p["ordered_size"] - 1) <= 0.2
            and p["sub_price"] <= p["ordered_price"] * 1.2):
        return "accept", new
    return "ask_customer", new


def state(p: dict) -> str:
    """What the model sees: names (sizes are in them), prices and the two allergen lists."""
    return json.dumps({"ordered_item": p["ordered"], "ordered_price_usd": p["ordered_price"] / 100,
                       "ordered_allergens": p["ordered_allergens"] or ["none"],
                       "substitute": p["substitute"], "substitute_price_usd": p["sub_price"] / 100,
                       "substitute_allergens": p["sub_allergens"] or ["none"]})


def _lit(s: str) -> str:
    """A Databricks SQL string literal. Backslashes and quotes are refused rather than escaped."""
    if "\\" in s or "'" in s:
        raise ValueError("backslash or quote in a SQL literal")
    return "'" + s + "'"


def sql() -> str:
    rows = ",\n    ".join(f"({p['sub_no']}, {_lit(state(p))})" for p in pairs())
    return f"""-- Day 49: substitution calls with ai_decide (Beta): verdict (choice), new_allergen (noul), closeness (score)
SELECT sub_no, to_json(ai_decide(state, {_lit(QUESTIONS_JSON)})) AS decision
FROM VALUES
    {rows}
  AS t(sub_no, state)
ORDER BY sub_no"""


def parse(rows: list[list]) -> dict[int, dict | None]:
    out = {}
    for r in rows:
        try:
            out[int(r[0])] = json.loads(r[1]) if r[1] else None
        except (TypeError, ValueError, IndexError):
            continue
    return out


def answers(d: dict | None) -> dict | None:
    try:
        a = d["response"]["answers"]
        return {"verdict": a["verdict"]["choice"], "p_new_allergen": float(a["new_allergen"]["probability"]),
                "closeness": float(a["closeness"]["score"])}
    except (TypeError, KeyError, ValueError):
        return None


def check(got: dict[int, dict | None]) -> list[str]:
    problems = []
    want = [p["sub_no"] for p in pairs()]
    if sorted(got) != want:
        problems.append(f"rows back {sorted(got)}, expected {want}")
    for n in want:
        d = got.get(n)
        if n not in got:
            continue
        if not isinstance(d, dict) or d.get("error_message"):
            problems.append(f"{n}: error {str((d or {}).get('error_message'))[:160]}")
            continue
        a = answers(d)
        if a is None:
            problems.append(f"{n}: response without verdict/new_allergen/closeness answers")
            continue
        if a["verdict"] not in LABELS:
            problems.append(f"{n}: verdict {a['verdict']!r} is not a label")
        if not 0 <= a["p_new_allergen"] <= 1:
            problems.append(f"{n}: new_allergen probability {a['p_new_allergen']}")
        if not 0 <= a["closeness"] <= len(QUESTIONS["closeness"]["criteria"]) - 1:
            problems.append(f"{n}: closeness score {a['closeness']}")
    return problems


def score(got: dict[int, dict | None]) -> dict:
    key = {p["sub_no"]: p for p in pairs()}
    a = {n: answers(got.get(n)) for n in key}
    wrong_verdict = [n for n in key if not a[n] or a[n]["verdict"] != key[n]["verdict"]]
    wrong_allergen = [n for n in key if not a[n] or (a[n]["p_new_allergen"] > 0.5) != key[n]["new_allergen"]]
    close = {v: [a[n]["closeness"] for n in key if a[n] and key[n]["verdict"] == v] for v in LABELS}
    return {"verdicts_right": len(key) - len(wrong_verdict), "allergen_flags_right": len(key) - len(wrong_allergen),
            "rows": len(key), "wrong_verdict": {n: a[n]["verdict"] if a[n] else None for n in wrong_verdict},
            "wrong_allergen": wrong_allergen,
            "mean_closeness": {v: round(sum(c) / len(c), 2) if c else None for v, c in close.items()}}


# ---------- Databricks SQL (Statement Execution API)
def _redact(text: str) -> str:
    host = os.environ.get("DATABRICKS_HOST", "").removeprefix("https://").rstrip("/")
    if host:
        text = text.replace(host, "<host>")
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "<user>", text)
    return re.sub(r"\b\d{7,}\b", "<id>", text)


class Refused(Exception):
    pass


def _api(method: str, path: str, body: dict | None = None) -> dict:
    host = os.environ["DATABRICKS_HOST"].rstrip("/")
    if not host.startswith("https://"):
        host = "https://" + host
    req = urllib.request.Request(host + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {os.environ['DATABRICKS_TOKEN']}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise Refused(f"{method} {path.split('?')[0]} {e.code}: {e.read()[:300].decode(errors='replace')}")


def statement(wh: str, text: str) -> list[list]:
    res = _api("POST", "/api/2.0/sql/statements",
               {"warehouse_id": wh, "statement": text, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    for _ in range(84):  # up to ~7 more minutes: warehouse start plus model inference
        if res["status"]["state"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(5)
        res = _api("GET", f"/api/2.0/sql/statements/{res['statement_id']}")
    if res["status"]["state"] != "SUCCEEDED":
        msg = res["status"].get("error", {}).get("message", "")
        raise RuntimeError(f"{res['status']['state']}: {msg[:450]}".replace("\n", " "))
    return res.get("result", {}).get("data_array", []) or []


def compute_refused(text: str) -> bool:
    t = text.lower()
    return ("feature_disabled" in t or "not active" in t or "cancelled" in t
            or "could not be processed by the warehouse" in t)


def preview_off(text: str) -> bool:
    t = text.lower()
    return "ai_decide" in t and ("unresolved_routine" in t or "cannot be found" in t or "not enabled" in t
                                 or "preview" in t)


def run_databricks() -> int:
    title = "Databricks day 49"
    try:
        whs = _api("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
        if not whs:
            print(f"::error title={title}::no SQL warehouse in the workspace")
            return 1
        wh = next((w for w in whs if w.get("enable_serverless_compute")), whs[0])["id"]
        got = parse(statement(wh, sql()))
    except (Refused, RuntimeError) as e:
        msg = _redact(str(e))
        if compute_refused(msg):
            print(f"::warning title={title}::the workspace refused ({msg[:300]}); the rule and answer key were "
                  "checked locally only")
            return 0
        if preview_off(msg):
            print(f"::warning title={title}::ai_decide is not available ({msg[:300]}); it is Beta and a workspace "
                  "admin must turn it on on the Previews page. Checked locally only")
            return 0
        print(f"::error title={title} failed::{msg[:700]}")
        return 1
    problems = check(got)
    if problems:
        print(f"::error title={title} check failed::{_redact('; '.join(problems))[:800]}")
        return 1
    s = score(got)
    print(f"::notice title={title}::ai_decide answered 3 questions for {s['rows']} substitutions in one call per row. "
          f"Verdicts equal to the store rule {s['verdicts_right']}/{s['rows']}, new-allergen flags "
          f"{s['allergen_flags_right']}/{s['rows']}, mean closeness by verdict {json.dumps(s['mean_closeness'])}")
    if s["wrong_verdict"] or s["wrong_allergen"]:
        print(f"::warning title={title}::ai_decide differs from the key: verdicts {json.dumps(s['wrong_verdict'])}, "
              f"allergen flags on {s['wrong_allergen']}")
    print("DAY49_SCORES " + json.dumps(s))
    return 0


def summarise() -> dict:
    ps = pairs()
    verdicts = [rule(p)[0] for p in ps]
    return {"substitutions": len(ps), "questions": list(QUESTIONS), "calls_per_row": 1,
            "rule_matches_key": sum(rule(p) == (p["verdict"], p["new_allergen"]) for p in ps),
            "verdicts": {v: verdicts.count(v) for v in LABELS},
            "new_allergen": sum(p["new_allergen"] for p in ps)}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--local"
    if mode == "--sql":
        print(sql())
    elif mode == "--databricks":
        raise SystemExit(run_databricks())
    else:
        s = summarise()
        (HERE / "result.json").write_text(json.dumps(
            {"engine": "store rule vs hand-labelled key (local) / ai_decide on a serverless SQL warehouse (cloud)",
             **s}, indent=2) + "\n")
        print(json.dumps(s, indent=2))
