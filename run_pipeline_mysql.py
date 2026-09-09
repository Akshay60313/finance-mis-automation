#!/usr/bin/env python3
"""Load validated Zoho Books-style CSV exports into MySQL.

This file deliberately reuses the cleaning and finance-control functions from
run_pipeline.py so SQLite and MySQL produce the same validated source data.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine

from run_pipeline import (
    FILE_CONFIG,
    Validator,
    determine_as_of_date,
    load_and_clean,
    validate_common,
    validate_documents,
    validate_relationships,
)


LOAD_ORDER = [
    "sales_invoices",
    "vendor_bills",
    "customer_payments",
    "vendor_payments",
    "expenses",
    "general_ledger",
    "budget",
    "field_mapping",
]

DELETE_ORDER = [
    "data_quality_exceptions",
    "customer_payments",
    "vendor_payments",
    "general_ledger",
    "expenses",
    "sales_invoices",
    "vendor_bills",
    "budget",
    "field_mapping",
]


def utc_now_naive() -> datetime:
    """Return UTC time without timezone information for MySQL DATETIME."""
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def create_mysql_engine() -> Engine:
    """Read credentials from .env and create a safe SQLAlchemy engine."""
    load_dotenv()
    settings = {
        "host": os.getenv("MYSQL_HOST"),
        "port": os.getenv("MYSQL_PORT", "3306"),
        "database": os.getenv("MYSQL_DATABASE"),
        "username": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
    }
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise RuntimeError(f"Missing .env settings: {', '.join(missing)}")

    url = URL.create(
        drivername="mysql+mysqlconnector",
        username=settings["username"],
        password=settings["password"],
        host=settings["host"],
        port=int(settings["port"]),
        database=settings["database"],
    )
    return create_engine(url, pool_pre_ping=True, future=True)


def prepare_for_mysql(table: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Map generic cleaned columns to the explicit MySQL schema."""
    frame = frame.copy()
    rename = {"subtotal": "sub_total"}
    if table == "general_ledger":
        rename["date"] = "transaction_date"
    elif table == "budget":
        rename["month"] = "budget_month"
    elif table == "field_mapping":
        rename["required"] = "required_flag"
    frame = frame.rename(columns=rename)

    for column in frame.columns:
        if column.endswith("_date") or column in {"transaction_date", "budget_month"}:
            frame[column] = pd.to_datetime(frame[column], errors="coerce").dt.date
        elif column == "etl_loaded_at_utc":
            frame[column] = pd.to_datetime(frame[column], errors="coerce", utc=True).dt.tz_convert(None)

    object_columns = frame.select_dtypes(include="object").columns
    frame[object_columns] = frame[object_columns].replace("", None)
    return frame


def build_validated_frames(input_dir: Path) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Load all required exports and run the same controls as the SQLite MVP."""
    frames: dict[str, pd.DataFrame] = {}
    validator = Validator()

    for filename, config in FILE_CONFIG.items():
        source = input_dir / filename
        if not source.exists():
            raise FileNotFoundError(f"Required Zoho export is missing: {source}")
        frame = load_and_clean(source, config)
        frames[config["table"]] = frame
        validate_common(filename, frame, config, validator)
        if filename in {"sales_invoices.csv", "vendor_bills.csv"}:
            validate_documents(filename, frame, config, validator)

    validate_relationships(frames, validator)
    return frames, validator.frame()


def start_run(engine: Engine, reporting_date: str, source_rows: int) -> int:
    with engine.begin() as connection:
        result = connection.execute(
            text("""
                INSERT INTO etl_run_log
                    (started_at_utc, reporting_date, run_status, source_row_count)
                VALUES (:started, :reporting_date, 'RUNNING', :source_rows)
            """),
            {
                "started": utc_now_naive(),
                "reporting_date": reporting_date,
                "source_rows": source_rows,
            },
        )
        return int(result.lastrowid)


def finish_run(engine: Engine, run_id: int, status: str, loaded_rows: int,
               exception_count: int, error_message: str | None = None) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("""
                UPDATE etl_run_log
                SET completed_at_utc=:completed,
                    run_status=:status,
                    loaded_row_count=:loaded_rows,
                    exception_count=:exception_count,
                    error_message=:error_message
                WHERE run_id=:run_id
            """),
            {
                "completed": utc_now_naive(),
                "status": status,
                "loaded_rows": loaded_rows,
                "exception_count": exception_count,
                "error_message": error_message,
                "run_id": run_id,
            },
        )


def load_mysql(input_dir: Path, supplied_as_of: str | None) -> dict:
    frames, exceptions = build_validated_frames(input_dir)
    reporting_date = determine_as_of_date(frames, supplied_as_of)
    engine = create_mysql_engine()

    with engine.connect() as connection:
        database, version = connection.execute(text("SELECT DATABASE(), VERSION()")) .one()

    source_rows = sum(len(frame) for frame in frames.values())
    run_id = start_run(engine, reporting_date, source_rows)

    try:
        prepared = {table: prepare_for_mysql(table, frame) for table, frame in frames.items()}
        mysql_exceptions = exceptions.rename(columns={
            "field": "field_name",
            "rule": "validation_rule",
            "message": "exception_message",
        }).copy()
        mysql_exceptions.insert(1, "run_id", run_id)
        mysql_exceptions["created_at_utc"] = utc_now_naive()

        # One transaction: either every table refreshes or none of them do.
        with engine.begin() as connection:
            for table in DELETE_ORDER:
                connection.execute(text(f"DELETE FROM `{table}`"))
            for table in LOAD_ORDER:
                prepared[table].to_sql(
                    table,
                    con=connection,
                    if_exists="append",
                    index=False,
                    chunksize=500,
                    method="multi",
                )
            if not mysql_exceptions.empty:
                mysql_exceptions.to_sql(
                    "data_quality_exceptions",
                    con=connection,
                    if_exists="append",
                    index=False,
                    chunksize=500,
                    method="multi",
                )

        loaded_rows = source_rows + len(exceptions)
        finish_run(engine, run_id, "SUCCESS", loaded_rows, len(exceptions))

        with engine.connect() as connection:
            table_counts = {
                table: int(connection.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one())
                for table in LOAD_ORDER
            }
            gl_difference = float(connection.execute(text(
                "SELECT ROUND(COALESCE(SUM(debit),0)-COALESCE(SUM(credit),0),2) FROM general_ledger"
            )).scalar_one())

        return {
            "status": "SUCCESS",
            "run_id": run_id,
            "database": database,
            "mysql_version": version,
            "reporting_date": reporting_date,
            "table_rows": table_counts,
            "exception_count": len(exceptions),
            "general_ledger_difference": gl_difference,
        }
    except Exception as exc:
        finish_run(engine, run_id, "FAILED", 0, len(exceptions), str(exc)[:4000])
        raise
    finally:
        engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load Zoho-style CSV exports into MySQL.")
    parser.add_argument("--input-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--as-of-date", help="Optional reporting date in YYYY-MM-DD format.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = load_mysql(args.input_dir, args.as_of_date)
    print(json.dumps(result, indent=2, default=str))
