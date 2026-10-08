-- A Unity Catalog Python UDF that checks a grocery barcode (GTIN-8 / UPC-A / EAN-13 / GTIN-14) with the GS1
-- check-digit rule. New and GA in Databricks (2026-09-23): scalar Python UDFs with a named HANDLER, which on
-- serverless SQL warehouses needs ENVIRONMENT (environment_version = '6') or above.
CREATE OR REPLACE FUNCTION {schema}.gtin_status(code STRING)
RETURNS STRING
LANGUAGE PYTHON
HANDLER 'gtin_status_handler'
ENVIRONMENT (
  environment_version = '6'
)
AS $$
# Runs once when the Python environment loads the UDF.
GTIN_LENGTHS = (8, 12, 13, 14)


def gtin_status_handler(code):
    if code is None:
        return None
    digits = code.strip()
    if not (digits.isascii() and digits.isdigit()):
        return "not_numeric"
    if len(digits) not in GTIN_LENGTHS:
        return "bad_length"
    body, check = digits[:-1], int(digits[-1])
    # GS1: from the right, weights 3, 1, 3, 1, ... over the digits before the check digit.
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return "valid" if (10 - total % 10) % 10 == check else "bad_check_digit"
$$
