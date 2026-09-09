#!/usr/bin/env python3
"""Zoho Books-style finance ETL and MIS reporting pipeline.

Flow: CSV exports -> cleaning -> validation -> SQLite -> reporting CSVs.
The calculations are deterministic. AI/RAG can be added later for commentary.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, UTC
from pathlib import Path

import pandas as pd


FILE_CONFIG = {
    "sales_invoices.csv": {
        "table": "sales_invoices",
        "key": "invoice_id",
        "business_key": "invoice_number",
        "dates": ["invoice_date", "due_date"],
        "numbers": [
            "exchange_rate", "quantity", "rate", "discount", "tax_percentage",
            "tax_amount", "sub_total", "total", "balance",
        ],
    },
    "vendor_bills.csv": {
        "table": "vendor_bills",
        "key": "bill_id",
        "business_key": "bill_number",
        "dates": ["bill_date", "due_date"],
        "numbers": [
            "exchange_rate", "quantity", "rate", "tax_percentage", "tax_amount",
            "sub_total", "total", "balance",
        ],
    },
    "customer_payments.csv": {
        "table": "customer_payments",
        "key": "payment_id",
        "business_key": "payment_number",
        "dates": ["payment_date"],
        "numbers": ["amount_applied", "unused_amount"],
    },
    "vendor_payments.csv": {
        "table": "vendor_payments",
        "key": "payment_id",
        "business_key": "payment_number",
        "dates": ["payment_date"],
        "numbers": ["amount_applied"],
    },
    "expenses.csv": {
        "table": "expenses",
        "key": "expense_id",
        "business_key": "reference_number",
        "dates": ["expense_date"],
        "numbers": ["tax_percentage", "tax_amount", "sub_total", "total"],
    },
    "general_ledger.csv": {
        "table": "general_ledger",
        "key": "journal_number",
        "business_key": "journal_number",
        "dates": ["date"],
        "numbers": ["debit", "credit"],
    },
    "budget.csv": {
        "table": "budget",
        "key": None,
        "business_key": None,
        "dates": ["month"],
        "numbers": ["budget_amount"],
    },
    "field_mapping.csv": {
        "table": "field_mapping",
        "key": None,
        "business_key": None,
        "dates": [],
        "numbers": [],
    },
}


def snake_case(value: str) -> str:
    value = value.strip().replace("%", " percentage ")
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_").lower()


def text_value(value) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def load_and_clean(path: Path, config: dict) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = [snake_case(c) for c in df.columns]
    for col in df.columns:
        df[col] = df[col].map(text_value)

    for col in config["numbers"]:
        if col in df:
            cleaned = df[col].str.replace(",", "", regex=False).str.replace("₹", "", regex=False)
            df[col] = pd.to_numeric(cleaned, errors="coerce")

    for col in config["dates"]:
        if col in df:
            parsed = pd.to_datetime(df[col], errors="coerce")
            df[col] = parsed.dt.strftime("%Y-%m-%d")

    # Stable source lineage makes every SQL result traceable to the export row.
    df.insert(0, "etl_source_row_number", range(2, len(df) + 2))
    df.insert(0, "etl_source_file", path.name)
    df.insert(0, "etl_loaded_at_utc", datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
    return df


class Validator:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.counter = 0

    def add(self, severity: str, source_file: str, record_key: str, field: str,
            rule: str, observed, message: str) -> None:
        self.counter += 1
        self.rows.append({
            "exception_id": f"EXC-{self.counter:05d}",
            "severity": severity,
            "source_file": source_file,
            "record_key": text_value(record_key),
            "field": field,
            "rule": rule,
            "observed_value": text_value(observed),
            "message": message,
        })

    def frame(self) -> pd.DataFrame:
        columns = ["exception_id", "severity", "source_file", "record_key", "field",
                   "rule", "observed_value", "message"]
        return pd.DataFrame(self.rows, columns=columns)


def record_key(row: pd.Series, config: dict) -> str:
    for col in (config.get("key"), config.get("business_key")):
        if col and col in row and text_value(row[col]):
            return text_value(row[col])
    return f"source-row-{row.get('etl_source_row_number', '')}"


def validate_common(filename: str, df: pd.DataFrame, config: dict, v: Validator) -> None:
    key = config.get("key")
    if key and key in df and filename != "general_ledger.csv":
        missing = df[key].astype(str).str.strip().eq("")
        for _, row in df[missing].iterrows():
            v.add("ERROR", filename, record_key(row, config), key, "REQUIRED_KEY", "",
                  f"Required source key {key} is missing.")
        duplicated = df[key].ne("") & df[key].duplicated(keep=False)
        for _, row in df[duplicated].iterrows():
            v.add("ERROR", filename, record_key(row, config), key, "DUPLICATE_SOURCE_KEY", row[key],
                  f"Source key {row[key]} appears more than once.")

    business_key = config.get("business_key")
    if business_key and business_key in df and filename != "general_ledger.csv":
        duplicated = df[business_key].ne("") & df[business_key].duplicated(keep=False)
        for _, row in df[duplicated].iterrows():
            v.add("ERROR", filename, record_key(row, config), business_key,
                  "DUPLICATE_DOCUMENT_NUMBER", row[business_key],
                  f"Document number {row[business_key]} appears more than once.")

    for col in config["dates"]:
        if col in df:
            for _, row in df[df[col].isna()].iterrows():
                v.add("ERROR", filename, record_key(row, config), col, "INVALID_DATE", "",
                      f"{col} is blank or cannot be parsed as a date.")

    for col in config["numbers"]:
        if col in df:
            for _, row in df[df[col].isna()].iterrows():
                v.add("ERROR", filename, record_key(row, config), col, "INVALID_NUMBER", "",
                      f"{col} is blank or not numeric.")


def validate_documents(filename: str, df: pd.DataFrame, config: dict, v: Validator) -> None:
    date_col = "invoice_date" if filename == "sales_invoices.csv" else "bill_date"
    gst_col = "customer_gstin" if filename == "sales_invoices.csv" else "vendor_gstin"

    for _, row in df.iterrows():
        key = record_key(row, config)
        if text_value(row.get(gst_col, "")) == "":
            v.add("WARNING", filename, key, gst_col, "MISSING_GSTIN", "",
                  "GSTIN is missing; confirm GST treatment before posting or payment.")
        if pd.notna(row.get(date_col)) and pd.notna(row.get("due_date")):
            if row["due_date"] < row[date_col]:
                v.add("ERROR", filename, key, "due_date", "DUE_BEFORE_DOCUMENT_DATE",
                      row["due_date"], "Due date is earlier than the document date.")
        total = row.get("total")
        subtotal = row.get("sub_total")
        tax = row.get("tax_amount")
        balance = row.get("balance")
        if pd.notna(total) and pd.notna(subtotal) and pd.notna(tax):
            if abs(float(total) - float(subtotal) - float(tax)) > 0.02:
                v.add("ERROR", filename, key, "total", "TOTAL_MATH",
                      total, "Total does not equal subtotal plus tax.")
        if pd.notna(balance) and pd.notna(total) and (float(balance) < -0.01 or float(balance) > float(total) + 0.01):
            v.add("ERROR", filename, key, "balance", "BALANCE_RANGE", balance,
                  "Balance must be between zero and the document total.")
        if filename == "vendor_bills.csv":
            if text_value(row.get("purchase_order_number", "")) == "":
                v.add("WARNING", filename, key, "purchase_order_number", "MISSING_PO", "",
                      "Purchase order is missing; confirm whether this is a valid non-PO bill.")
            if text_value(row.get("grn_number", "")) == "":
                v.add("WARNING", filename, key, "grn_number", "MISSING_GRN", "",
                      "GRN is missing; complete three-way match before payment where applicable.")


def validate_relationships(frames: dict[str, pd.DataFrame], v: Validator) -> None:
    invoices = frames["sales_invoices"]
    bills = frames["vendor_bills"]
    cp = frames["customer_payments"]
    vp = frames["vendor_payments"]

    invoice_ids = set(invoices["invoice_id"])
    bill_ids = set(bills["bill_id"])
    for _, row in cp.iterrows():
        if row["invoice_id"] not in invoice_ids:
            v.add("ERROR", "customer_payments.csv", row["payment_id"], "invoice_id",
                  "ORPHAN_PAYMENT", row["invoice_id"], "Payment references an unknown invoice.")
    for _, row in vp.iterrows():
        if row["bill_id"] not in bill_ids:
            v.add("ERROR", "vendor_payments.csv", row["payment_id"], "bill_id",
                  "ORPHAN_PAYMENT", row["bill_id"], "Payment references an unknown vendor bill.")

    customer_applied = cp.groupby("invoice_id", as_index=True)["amount_applied"].sum()
    for _, row in invoices.iterrows():
        applied = float(customer_applied.get(row["invoice_id"], 0.0))
        expected = float(row["total"] - row["balance"])
        if abs(applied - expected) > 0.02:
            v.add("ERROR", "sales_invoices.csv", row["invoice_id"], "balance",
                  "PAYMENT_RECONCILIATION", f"applied={applied:.2f}; expected={expected:.2f}",
                  "Customer payments do not reconcile to invoice total less balance.")

    vendor_applied = vp.groupby("bill_id", as_index=True)["amount_applied"].sum()
    for _, row in bills.iterrows():
        applied = float(vendor_applied.get(row["bill_id"], 0.0))
        expected = float(row["total"] - row["balance"])
        if abs(applied - expected) > 0.02:
            v.add("ERROR", "vendor_bills.csv", row["bill_id"], "balance",
                  "PAYMENT_RECONCILIATION", f"applied={applied:.2f}; expected={expected:.2f}",
                  "Vendor payments do not reconcile to bill total less balance.")

    gl = frames["general_ledger"]
    journal = gl.groupby("journal_number", as_index=False).agg(debit=("debit", "sum"), credit=("credit", "sum"))
    journal["difference"] = journal["debit"] - journal["credit"]
    for _, row in journal[journal["difference"].abs() > 0.02].iterrows():
        v.add("ERROR", "general_ledger.csv", row["journal_number"], "debit_credit",
              "UNBALANCED_JOURNAL", row["difference"], "Journal debit and credit totals do not balance.")
    total_difference = float(gl["debit"].sum() - gl["credit"].sum())
    if abs(total_difference) > 0.02:
        v.add("CRITICAL", "general_ledger.csv", "FULL-LEDGER", "debit_credit",
              "UNBALANCED_LEDGER", total_difference, "General ledger debit and credit totals do not balance.")


def create_schema_and_views(conn: sqlite3.Connection, as_of_date: str) -> None:
    conn.executescript("""
        DROP TABLE IF EXISTS pipeline_config;
        CREATE TABLE pipeline_config (config_key TEXT PRIMARY KEY, config_value TEXT NOT NULL);

        DROP VIEW IF EXISTS vw_ar_ageing;
        CREATE VIEW vw_ar_ageing AS
        SELECT invoice_id, invoice_number, invoice_date, due_date, customer_id, customer_name,
               status, total, balance AS outstanding_amount,
               MAX(CAST(julianday((SELECT config_value FROM pipeline_config WHERE config_key='as_of_date'))
                    - julianday(due_date) AS INTEGER), 0) AS overdue_days,
               CASE
                 WHEN balance <= 0.01 THEN 'Closed'
                 WHEN date(due_date) >= date((SELECT config_value FROM pipeline_config WHERE config_key='as_of_date')) THEN 'Not Due'
                 WHEN julianday((SELECT config_value FROM pipeline_config WHERE config_key='as_of_date')) - julianday(due_date) <= 30 THEN '0-30'
                 WHEN julianday((SELECT config_value FROM pipeline_config WHERE config_key='as_of_date')) - julianday(due_date) <= 60 THEN '31-60'
                 WHEN julianday((SELECT config_value FROM pipeline_config WHERE config_key='as_of_date')) - julianday(due_date) <= 90 THEN '61-90'
                 ELSE '90+'
               END AS ageing_bucket
        FROM sales_invoices;

        DROP VIEW IF EXISTS vw_ap_ageing;
        CREATE VIEW vw_ap_ageing AS
        SELECT bill_id, bill_number, bill_date, due_date, vendor_id, vendor_name,
               status, total, balance AS outstanding_amount, department,
               MAX(CAST(julianday((SELECT config_value FROM pipeline_config WHERE config_key='as_of_date'))
                    - julianday(due_date) AS INTEGER), 0) AS overdue_days,
               CASE
                 WHEN balance <= 0.01 THEN 'Closed'
                 WHEN date(due_date) >= date((SELECT config_value FROM pipeline_config WHERE config_key='as_of_date')) THEN 'Not Due'
                 WHEN julianday((SELECT config_value FROM pipeline_config WHERE config_key='as_of_date')) - julianday(due_date) <= 30 THEN '0-30'
                 WHEN julianday((SELECT config_value FROM pipeline_config WHERE config_key='as_of_date')) - julianday(due_date) <= 60 THEN '31-60'
                 WHEN julianday((SELECT config_value FROM pipeline_config WHERE config_key='as_of_date')) - julianday(due_date) <= 90 THEN '61-90'
                 ELSE '90+'
               END AS ageing_bucket
        FROM vendor_bills;

        DROP VIEW IF EXISTS vw_monthly_pnl;
        CREATE VIEW vw_monthly_pnl AS
        SELECT substr(date,1,7) || '-01' AS month,
               department,
               ROUND(SUM(CASE WHEN account_type='Income' THEN credit-debit ELSE 0 END),2) AS revenue,
               ROUND(SUM(CASE WHEN account_type='Expense' THEN debit-credit ELSE 0 END),2) AS operating_expense,
               ROUND(SUM(CASE WHEN account_type='Income' THEN credit-debit ELSE 0 END)
                   - SUM(CASE WHEN account_type='Expense' THEN debit-credit ELSE 0 END),2) AS operating_profit
        FROM general_ledger
        GROUP BY substr(date,1,7), department;

        DROP VIEW IF EXISTS vw_budget_vs_actual;
        CREATE VIEW vw_budget_vs_actual AS
        WITH mapped_gl AS (
          SELECT date, department,
                 CASE
                   WHEN account_type='Income' THEN 'Sales Revenue'
                   WHEN account='Purchases' THEN 'Purchases'
                   WHEN account='Marketing Expense' THEN 'Marketing Expense'
                   WHEN account='Software Subscription' THEN 'Technology Expense'
                   ELSE 'Administration Expense'
                 END AS budget_account,
                 account_type, debit, credit
          FROM general_ledger
          WHERE account_type IN ('Income','Expense')
        ),
        actual AS (
          SELECT substr(date,1,7) || '-01' AS month, department, budget_account AS account,
                 ROUND(SUM(CASE WHEN account_type='Income' THEN credit-debit ELSE debit-credit END),2) AS actual_amount
          FROM mapped_gl
          GROUP BY substr(date,1,7), department, budget_account
        )
        SELECT b.month, b.department, b.account, b.budget_amount,
               COALESCE(a.actual_amount,0) AS actual_amount,
               ROUND(CASE WHEN b.account LIKE '%Revenue%'
                          THEN COALESCE(a.actual_amount,0)-b.budget_amount
                          ELSE b.budget_amount-COALESCE(a.actual_amount,0) END,2) AS favourable_variance,
               ROUND(CASE WHEN b.budget_amount=0 THEN NULL
                          WHEN b.account LIKE '%Revenue%'
                          THEN (COALESCE(a.actual_amount,0)-b.budget_amount)/b.budget_amount*100
                          ELSE (b.budget_amount-COALESCE(a.actual_amount,0))/b.budget_amount*100 END,2) AS favourable_variance_pct,
               CASE WHEN ABS(CASE WHEN b.account LIKE '%Revenue%'
                                  THEN COALESCE(a.actual_amount,0)-b.budget_amount
                                  ELSE b.budget_amount-COALESCE(a.actual_amount,0) END) < 0.01 THEN 'On Budget'
                    WHEN (CASE WHEN b.account LIKE '%Revenue%'
                               THEN COALESCE(a.actual_amount,0)-b.budget_amount
                               ELSE b.budget_amount-COALESCE(a.actual_amount,0) END) > 0 THEN 'Favourable'
                    ELSE 'Unfavourable' END AS variance_status
        FROM budget b
        LEFT JOIN actual a ON a.month=b.month AND a.department=b.department AND a.account=b.account;

        DROP VIEW IF EXISTS vw_mis_kpis;
        CREATE VIEW vw_mis_kpis AS
        SELECT 'Reporting Date' AS metric, (SELECT config_value FROM pipeline_config WHERE config_key='as_of_date') AS value
        UNION ALL SELECT 'Total Revenue', printf('%.2f', COALESCE(SUM(revenue),0)) FROM vw_monthly_pnl
        UNION ALL SELECT 'Operating Expense', printf('%.2f', COALESCE(SUM(operating_expense),0)) FROM vw_monthly_pnl
        UNION ALL SELECT 'Operating Profit', printf('%.2f', COALESCE(SUM(operating_profit),0)) FROM vw_monthly_pnl
        UNION ALL SELECT 'AR Outstanding', printf('%.2f', COALESCE(SUM(outstanding_amount),0)) FROM vw_ar_ageing
        UNION ALL SELECT 'AP Outstanding', printf('%.2f', COALESCE(SUM(outstanding_amount),0)) FROM vw_ap_ageing
        UNION ALL SELECT 'AR Overdue', printf('%.2f', COALESCE(SUM(CASE WHEN overdue_days>0 THEN outstanding_amount ELSE 0 END),0)) FROM vw_ar_ageing
        UNION ALL SELECT 'AP Overdue', printf('%.2f', COALESCE(SUM(CASE WHEN overdue_days>0 THEN outstanding_amount ELSE 0 END),0)) FROM vw_ap_ageing;
    """)
    conn.execute("INSERT INTO pipeline_config VALUES (?, ?)", ("as_of_date", as_of_date))
    conn.commit()


def export_query(conn: sqlite3.Connection, query: str, path: Path) -> int:
    frame = pd.read_sql_query(query, conn)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return len(frame)


def determine_as_of_date(frames: dict[str, pd.DataFrame], supplied: str | None) -> str:
    if supplied:
        return pd.Timestamp(supplied).strftime("%Y-%m-%d")
    dates: list[str] = []
    for table, columns in {
        "sales_invoices": ["invoice_date"],
        "vendor_bills": ["bill_date"],
        "customer_payments": ["payment_date"],
        "vendor_payments": ["payment_date"],
        "general_ledger": ["date"],
    }.items():
        for col in columns:
            dates.extend(frames[table][col].dropna().tolist())
    return max(dates)


def run(input_dir: Path, output_dir: Path, database_path: Path, supplied_as_of: str | None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    frames: dict[str, pd.DataFrame] = {}
    validator = Validator()
    for filename, config in FILE_CONFIG.items():
        path = input_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Required export not found: {path}")
        df = load_and_clean(path, config)
        frames[config["table"]] = df
        validate_common(filename, df, config, validator)
        if filename in {"sales_invoices.csv", "vendor_bills.csv"}:
            validate_documents(filename, df, config, validator)

    validate_relationships(frames, validator)
    exceptions = validator.frame()
    as_of_date = determine_as_of_date(frames, supplied_as_of)

    if database_path.exists():
        database_path.unlink()
    with sqlite3.connect(database_path) as conn:
        for table, frame in frames.items():
            frame.to_sql(table, conn, index=False, if_exists="replace")
        exceptions.to_sql("data_quality_exceptions", conn, index=False, if_exists="replace")
        create_schema_and_views(conn, as_of_date)

        report_rows = {
            "mis_kpis.csv": export_query(conn, "SELECT * FROM vw_mis_kpis", output_dir / "mis_kpis.csv"),
            "ar_ageing.csv": export_query(conn, "SELECT * FROM vw_ar_ageing ORDER BY overdue_days DESC, customer_name", output_dir / "ar_ageing.csv"),
            "ap_ageing.csv": export_query(conn, "SELECT * FROM vw_ap_ageing ORDER BY overdue_days DESC, vendor_name", output_dir / "ap_ageing.csv"),
            "monthly_pnl.csv": export_query(conn, "SELECT * FROM vw_monthly_pnl ORDER BY month, department", output_dir / "monthly_pnl.csv"),
            "budget_vs_actual.csv": export_query(conn, "SELECT * FROM vw_budget_vs_actual ORDER BY month, department, account", output_dir / "budget_vs_actual.csv"),
        }

    exceptions.to_csv(output_dir / "data_quality_exceptions.csv", index=False, encoding="utf-8-sig")
    severity_counts = exceptions["severity"].value_counts().to_dict() if len(exceptions) else {}
    summary = {
        "status": "SUCCESS",
        "run_timestamp_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "as_of_date": as_of_date,
        "input_directory": str(input_dir.resolve()),
        "database": str(database_path.resolve()),
        "source_rows": {table: int(len(frame)) for table, frame in frames.items()},
        "exception_count": int(len(exceptions)),
        "exceptions_by_severity": {k: int(v) for k, v in severity_counts.items()},
        "report_rows": report_rows,
    }
    (output_dir / "pipeline_run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Zoho-style finance ETL and MIS reporting.")
    parser.add_argument("--input-dir", type=Path, default=Path("data/raw"), help="Directory containing the eight Zoho CSV exports.")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Directory for reports and controls.")
    parser.add_argument("--database", type=Path, default=Path("output/finance_mis.db"), help="SQLite output database.")
    parser.add_argument("--as-of-date", help="MIS reporting date in YYYY-MM-DD format. Defaults to latest source date.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = run(args.input_dir, args.output_dir, args.database, args.as_of_date)
    print(json.dumps(result, indent=2))
