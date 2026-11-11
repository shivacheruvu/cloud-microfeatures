"""Day 37 task file (serverless spark_python_task), used by all three cloud jobs.

  task_restock.py receive <name>                 # upstream receiving jobs: just finish successfully
  task_restock.py plan '<upstream_completions>'  # downstream: print the completions the trigger handed over
"""
import json
import sys

if __name__ == "__main__":
    if sys.argv[1] == "receive":
        print(f"DAY37_RECEIVED {sys.argv[2]}")
    else:
        raw = sys.argv[2] if len(sys.argv) > 2 else "[]"
        jobs = json.loads(raw or "[]")
        print("DAY37_COMPLETIONS " + json.dumps([{"job_id": str(c.get("job_id")), "run_id": str(c.get("run_id")),
                                                  "termination_code": c.get("termination_code")} for c in jobs]))
