#!/usr/bin/env python3
"""Generate a formatted Excel MIS pack from the MySQL reporting views.

Default mode reads MySQL credentials from .env. An optional SQLite demo mode
allows the portfolio workbook to be generated without a running MySQL server.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo


NAVY = "17365D"
BLUE = "1F4E78"
TEAL = "00A6A6"
LIGHT_BLUE = "DDEBF7"
PALE_TEAL = "DDEFEF"
PALE_GREEN = "E2F0D9"
PALE_RED = "FCE4D6"
PALE_YELLOW = "FFF2CC"
WHITE = "FFFFFF"
GREY = "667085"
THIN_GREY = Side(style="thin", color="D0D5DD")
MONEY_FORMAT = '₹#,##0.00;[Red]-₹#,##0.00'
INTEGER_FORMAT = '#,##0'
DATE_FORMAT = 'dd-mmm-yyyy'


MYSQL_QUERIES = {
    "MIS KPIs": "SELECT * FROM vw_mis_kpis ORDER BY display_order",
    "P&L MIS": """
        SELECT report_month,
               ROUND(SUM(revenue),2) AS revenue,
               ROUND(SUM(operating_expense),2) AS operating_expense,
               ROUND(SUM(operating_profit),2) AS operating_profit
        FROM vw_monthly_pnl
        GROUP BY report_month
        ORDER BY report_month
    """,
    "Budget vs Actual": "SELECT * FROM vw_budget_vs_actual ORDER BY report_month, department, account",
    "AP Ageing": "SELECT * FROM vw_ap_ageing ORDER BY overdue_days DESC, vendor_name, bill_number",
    "AR Ageing": "SELECT * FROM vw_ar_ageing ORDER BY overdue_days DESC, customer_name, invoice_number",
    "Vendor Summary": "SELECT * FROM vw_vendor_outstanding ORDER BY overdue_amount DESC, vendor_name",
    "Customer Summary": "SELECT * FROM vw_customer_outstanding ORDER BY overdue_amount DESC, customer_name",
    "Cash Activity": "SELECT * FROM vw_monthly_cash_activity ORDER BY report_month",
    "Exceptions": """
        SELECT exception_id, run_id, severity, source_file, record_key, field_name,
               validation_rule, observed_value, exception_message, created_at_utc
        FROM data_quality_exceptions
        ORDER BY FIELD(severity,'CRITICAL','ERROR','WARNING'), validation_rule, record_key
    """,
    "Refresh Log": """
        SELECT run_id, started_at_utc, completed_at_utc, reporting_date, run_status,
               source_row_count, loaded_row_count, exception_count, error_message
        FROM etl_run_log
        ORDER BY run_id DESC
    """,
}


def load_mysql_data() -> dict[str, pd.DataFrame]:
    from sqlalchemy import text
    from run_pipeline_mysql import create_mysql_engine

    engine = create_mysql_engine()
    try:
        with engine.connect() as connection:
            return {
                name: pd.read_sql_query(text(query), connection)
                for name, query in MYSQL_QUERIES.items()
            }
    finally:
        engine.dispose()


def load_sqlite_demo(database: Path) -> dict[str, pd.DataFrame]:
    """Build equivalent datasets from the bundled offline SQLite demo."""
    with sqlite3.connect(database) as connection:
        report_date = pd.read_sql_query(
            "SELECT config_value FROM pipeline_config WHERE config_key='as_of_date'", connection
        ).iloc[0, 0]
        pnl_detail = pd.read_sql_query("SELECT * FROM vw_monthly_pnl", connection)
        pnl = pnl_detail.groupby("month", as_index=False)[
            ["revenue", "operating_expense", "operating_profit"]
        ].sum().rename(columns={"month": "report_month"})

        bva = pd.read_sql_query("SELECT * FROM vw_budget_vs_actual", connection).rename(
            columns={"month": "report_month"}
        )
        ap = pd.read_sql_query("SELECT * FROM vw_ap_ageing", connection)
        ar = pd.read_sql_query("SELECT * FROM vw_ar_ageing", connection)
        ap.insert(4, "reporting_date", report_date)
        ar.insert(4, "reporting_date", report_date)
        if "control_status" not in ap:
            ap["control_status"] = "Control Complete"

        vendor = ap.groupby(["vendor_id", "vendor_name"], as_index=False).agg(
            open_bill_count=("outstanding_amount", lambda x: int((x > 0.01).sum())),
            outstanding_amount=("outstanding_amount", "sum"),
            overdue_amount=("outstanding_amount", lambda x: float(x[ap.loc[x.index, "overdue_days"] > 0].sum())),
            maximum_overdue_days=("overdue_days", "max"),
        ).sort_values(["overdue_amount", "vendor_name"], ascending=[False, True])

        customer = ar.groupby(["customer_id", "customer_name"], as_index=False).agg(
            open_invoice_count=("outstanding_amount", lambda x: int((x > 0.01).sum())),
            outstanding_amount=("outstanding_amount", "sum"),
            overdue_amount=("outstanding_amount", lambda x: float(x[ar.loc[x.index, "overdue_days"] > 0].sum())),
            maximum_overdue_days=("overdue_days", "max"),
        ).sort_values(["overdue_amount", "customer_name"], ascending=[False, True])

        collections = pd.read_sql_query(
            "SELECT substr(payment_date,1,7)||'-01' report_month, SUM(amount_applied) cash_collected "
            "FROM customer_payments GROUP BY substr(payment_date,1,7)", connection
        )
        payments = pd.read_sql_query(
            "SELECT substr(payment_date,1,7)||'-01' report_month, SUM(amount_applied) vendor_payments "
            "FROM vendor_payments GROUP BY substr(payment_date,1,7)", connection
        )
        cash = collections.merge(payments, on="report_month", how="outer").fillna(0)
        cash["net_operating_cash_activity"] = cash["cash_collected"] - cash["vendor_payments"]
        cash = cash.sort_values("report_month")

        exceptions = pd.read_sql_query("SELECT * FROM data_quality_exceptions", connection).rename(columns={
            "field": "field_name", "rule": "validation_rule", "message": "exception_message"
        })
        exceptions.insert(1, "run_id", 1)
        exceptions["created_at_utc"] = datetime.now(UTC).replace(tzinfo=None, microsecond=0)

        kpi_pairs = [
            ("Total Revenue", pnl["revenue"].sum(), None),
            ("Operating Expense", pnl["operating_expense"].sum(), None),
            ("Operating Profit", pnl["operating_profit"].sum(), None),
            ("AR Outstanding", ar["outstanding_amount"].sum(), None),
            ("AP Outstanding", ap["outstanding_amount"].sum(), None),
            ("AR Overdue", ar.loc[ar["overdue_days"] > 0, "outstanding_amount"].sum(), None),
            ("AP Overdue", ap.loc[ap["overdue_days"] > 0, "outstanding_amount"].sum(), None),
            ("Open AR Invoices", None, int((ar["outstanding_amount"] > 0.01).sum())),
            ("Open AP Bills", None, int((ap["outstanding_amount"] > 0.01).sum())),
            ("Data Exceptions", None, len(exceptions)),
        ]
        kpis = pd.DataFrame([
            {"reporting_date": report_date, "metric": metric, "amount_value": amount,
             "count_value": count, "display_order": order}
            for order, (metric, amount, count) in enumerate(kpi_pairs, start=1)
        ])

        refresh = pd.DataFrame([{
            "run_id": 1,
            "started_at_utc": datetime.now(UTC).replace(tzinfo=None, microsecond=0),
            "completed_at_utc": datetime.now(UTC).replace(tzinfo=None, microsecond=0),
            "reporting_date": report_date,
            "run_status": "SUCCESS",
            "source_row_count": sum(pd.read_sql_query(f"SELECT COUNT(*) n FROM {table}", connection).iloc[0, 0]
                                    for table in ["sales_invoices", "vendor_bills", "customer_payments",
                                                  "vendor_payments", "expenses", "general_ledger", "budget",
                                                  "field_mapping"]),
            "loaded_row_count": None,
            "exception_count": len(exceptions),
            "error_message": None,
        }])

    return {
        "MIS KPIs": kpis,
        "P&L MIS": pnl,
        "Budget vs Actual": bva,
        "AP Ageing": ap,
        "AR Ageing": ar,
        "Vendor Summary": vendor,
        "Customer Summary": customer,
        "Cash Activity": cash,
        "Exceptions": exceptions,
        "Refresh Log": refresh,
    }


def clean_header(value: str) -> str:
    return str(value).replace("_", " ").title().replace("P&L", "P&L").replace("Ar ", "AR ").replace("Ap ", "AP ")


def python_value(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def is_money_column(column: str) -> bool:
    terms = ("amount", "revenue", "expense", "profit", "budget", "actual", "variance", "cash", "total", "balance")
    return any(term in column.lower() for term in terms) and "count" not in column.lower()


def style_title(ws, title: str, subtitle: str, end_column: int) -> None:
    end_column = max(end_column, 6)
    ws.merge_cells(start_row=1, start_column=1, end_row=2, end_column=end_column)
    cell = ws.cell(1, 1, title)
    cell.fill = PatternFill("solid", fgColor=NAVY)
    cell.font = Font(color=WHITE, bold=True, size=18)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 24
    ws.row_dimensions[2].height = 10
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=end_column)
    note = ws.cell(3, 1, subtitle)
    note.font = Font(color=GREY, italic=True, size=10)
    note.alignment = Alignment(horizontal="left")


def add_dataframe_sheet(wb: Workbook, name: str, frame: pd.DataFrame, subtitle: str) -> None:
    ws = wb.create_sheet(name)
    style_title(ws, name, subtitle, len(frame.columns))
    header_row = 5
    for column_index, column in enumerate(frame.columns, start=1):
        cell = ws.cell(header_row, column_index, clean_header(column))
        cell.fill = PatternFill("solid", fgColor=BLUE)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=THIN_GREY)

    for row_index, row in enumerate(frame.itertuples(index=False, name=None), start=header_row + 1):
        for column_index, value in enumerate(row, start=1):
            cell = ws.cell(row_index, column_index, python_value(value))
            cell.border = Border(bottom=THIN_GREY)
            if row_index % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F7F9FC")

    if len(frame):
        end_row = header_row + len(frame)
        end_col = len(frame.columns)
        table_name = "tbl_" + re.sub(r"[^A-Za-z0-9]", "", name)
        table = Table(displayName=table_name[:250], ref=f"A{header_row}:{get_column_letter(end_col)}{end_row}")
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2", showFirstColumn=False, showLastColumn=False,
            showRowStripes=True, showColumnStripes=False,
        )
        ws.add_table(table)

        for column_index, column in enumerate(frame.columns, start=1):
            for row_index in range(header_row + 1, end_row + 1):
                cell = ws.cell(row_index, column_index)
                if is_money_column(column):
                    cell.number_format = MONEY_FORMAT
                elif "date" in column.lower() or "month" in column.lower() or column.endswith("_at_utc"):
                    cell.number_format = DATE_FORMAT
                elif "count" in column.lower() or "days" in column.lower() or column in {"run_id", "display_order"}:
                    cell.number_format = INTEGER_FORMAT

        if name == "Budget vs Actual" and "variance_status" in frame.columns:
            status_col = list(frame.columns).index("variance_status") + 1
            status_letter = get_column_letter(status_col)
            ws.conditional_formatting.add(
                f"{status_letter}{header_row+1}:{status_letter}{end_row}",
                FormulaRule(formula=[f'${status_letter}{header_row+1}="Unfavourable"'], fill=PatternFill("solid", fgColor=PALE_RED)),
            )
            ws.conditional_formatting.add(
                f"{status_letter}{header_row+1}:{status_letter}{end_row}",
                FormulaRule(formula=[f'${status_letter}{header_row+1}="Favourable"'], fill=PatternFill("solid", fgColor=PALE_GREEN)),
            )
        if name in {"AP Ageing", "AR Ageing"} and "overdue_days" in frame.columns:
            days_col = list(frame.columns).index("overdue_days") + 1
            letter = get_column_letter(days_col)
            ws.conditional_formatting.add(
                f"{letter}{header_row+1}:{letter}{end_row}",
                CellIsRule(operator="greaterThan", formula=["90"], fill=PatternFill("solid", fgColor=PALE_RED)),
            )
        if name == "Exceptions" and "severity" in frame.columns:
            sev_col = list(frame.columns).index("severity") + 1
            letter = get_column_letter(sev_col)
            ws.conditional_formatting.add(
                f"{letter}{header_row+1}:{letter}{end_row}",
                FormulaRule(formula=[f'OR(${letter}{header_row+1}="CRITICAL",${letter}{header_row+1}="ERROR")'],
                            fill=PatternFill("solid", fgColor=PALE_RED)),
            )

    for column_index, column in enumerate(frame.columns, start=1):
        values = [clean_header(column)] + ["" if pd.isna(v) else str(v) for v in frame[column].head(200)]
        width = min(max(len(v) for v in values) + 2, 42)
        ws.column_dimensions[get_column_letter(column_index)].width = max(width, 11)
    ws.freeze_panes = "A6"
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(max(1,len(frame.columns)))}{header_row + len(frame)}"
    ws.sheet_view.showGridLines = False
    ws.auto_filter.ref = None if len(frame) == 0 else ws.auto_filter.ref
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_area = f"A1:{get_column_letter(max(6, len(frame.columns)))}{min(max(header_row + len(frame), 12), 30)}"


def add_executive_summary(wb: Workbook, data: dict[str, pd.DataFrame]) -> None:
    ws = wb.active
    ws.title = "Executive Summary"
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A4"
    for col in range(1, 12):
        ws.column_dimensions[get_column_letter(col)].width = 16

    ws.merge_cells("A1:K2")
    title = ws["A1"]
    title.value = "AUTOMATED FINANCE MIS"
    title.fill = PatternFill("solid", fgColor=NAVY)
    title.font = Font(color=WHITE, bold=True, size=22)
    title.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 28

    kpis = data["MIS KPIs"].sort_values("display_order")
    reporting_date = kpis.iloc[0]["reporting_date"] if len(kpis) else None
    ws["A3"] = "Reporting date"
    ws["B3"] = python_value(reporting_date)
    ws["B3"].number_format = DATE_FORMAT
    ws["D3"] = "Generated"
    ws["E3"] = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    ws["E3"].number_format = "dd-mmm-yyyy hh:mm"
    ws["H3"] = "Source"
    ws["I3"] = "MySQL reporting views"

    for index, (_, row) in enumerate(kpis.head(10).iterrows()):
        card_row = 5 if index < 5 else 9
        card_col = 1 + (index % 5) * 2
        ws.merge_cells(start_row=card_row, start_column=card_col, end_row=card_row, end_column=card_col + 1)
        ws.merge_cells(start_row=card_row + 1, start_column=card_col, end_row=card_row + 2, end_column=card_col + 1)
        label = ws.cell(card_row, card_col, row["metric"])
        label.fill = PatternFill("solid", fgColor=BLUE)
        label.font = Font(color=WHITE, bold=True, size=10)
        label.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        value = ws.cell(card_row + 1, card_col)
        amount = row.get("amount_value")
        count = row.get("count_value")
        value.value = python_value(amount if pd.notna(amount) else count)
        value.fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        value.font = Font(color=NAVY, bold=True, size=16)
        value.alignment = Alignment(horizontal="center", vertical="center")
        value.number_format = MONEY_FORMAT if pd.notna(amount) else INTEGER_FORMAT

    def summary_table(start_row: int, start_col: int, title_text: str, frame: pd.DataFrame,
                      name_col: str, value_col: str) -> None:
        ws.merge_cells(start_row=start_row, start_column=start_col, end_row=start_row, end_column=start_col + 3)
        cell = ws.cell(start_row, start_col, title_text)
        cell.fill = PatternFill("solid", fgColor=TEAL)
        cell.font = Font(color=WHITE, bold=True)
        headers = [clean_header(name_col), "Outstanding", "Overdue", "Max Days"]
        for j, header in enumerate(headers, start=start_col):
            h = ws.cell(start_row + 1, j, header)
            h.fill = PatternFill("solid", fgColor=BLUE)
            h.font = Font(color=WHITE, bold=True)
        count_col = "maximum_overdue_days"
        for i, (_, row) in enumerate(frame.head(8).iterrows(), start=start_row + 2):
            values = [row[name_col], row[value_col], row["overdue_amount"], row[count_col]]
            for offset, value in enumerate(values):
                cell = ws.cell(i, start_col + offset, python_value(value))
                if offset in (1, 2):
                    cell.number_format = MONEY_FORMAT
                elif offset == 3:
                    cell.number_format = INTEGER_FORMAT
                if i % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="F7F9FC")

    summary_table(14, 1, "TOP OVERDUE CUSTOMERS", data["Customer Summary"],
                  "customer_name", "outstanding_amount")
    summary_table(14, 7, "TOP OVERDUE VENDORS", data["Vendor Summary"],
                  "vendor_name", "outstanding_amount")

    ws.merge_cells("A26:K26")
    ws["A26"] = "Control note: All figures are calculated in SQL. Exceptions remain visible for finance review and are not silently removed."
    ws["A26"].fill = PatternFill("solid", fgColor=PALE_YELLOW)
    ws["A26"].font = Font(color=NAVY, italic=True)
    ws["A26"].alignment = Alignment(wrap_text=True)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_area = "A1:K27"


def add_pnl_chart(ws, row_count: int) -> None:
    if row_count < 1:
        return
    chart = LineChart()
    chart.title = "Monthly Revenue, Expense and Operating Profit"
    chart.y_axis.title = "INR"
    chart.x_axis.title = "Month"
    data = Reference(ws, min_col=2, max_col=4, min_row=5, max_row=5 + row_count)
    categories = Reference(ws, min_col=1, min_row=6, max_row=5 + row_count)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(categories)
    chart.height = 8
    chart.width = 16
    chart.style = 13
    ws.add_chart(chart, "F5")
    ws.print_area = "A1:N20"


def build_workbook(data: dict[str, pd.DataFrame], output: Path) -> Path:
    wb = Workbook()
    add_executive_summary(wb, data)
    subtitle = "Source: finance_mis MySQL reporting views | Refresh by rerunning generate_excel_mis.py"
    for name in ["P&L MIS", "Budget vs Actual", "AP Ageing", "AR Ageing",
                 "Vendor Summary", "Customer Summary", "Cash Activity", "Exceptions", "Refresh Log"]:
        add_dataframe_sheet(wb, name, data[name], subtitle)
    add_pnl_chart(wb["P&L MIS"], len(data["P&L MIS"]))
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    output.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output)
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the automated Excel MIS management pack.")
    parser.add_argument("--output", type=Path, default=Path("output/Automated_Finance_MIS.xlsx"))
    parser.add_argument("--demo-sqlite", type=Path, help="Use the bundled SQLite database instead of MySQL.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    datasets = load_sqlite_demo(args.demo_sqlite) if args.demo_sqlite else load_mysql_data()
    path = build_workbook(datasets, args.output)
    print(f"Excel MIS generated successfully: {path.resolve()}")
