USE finance_mis;

-- One authoritative reporting date: the latest successful pipeline run.
CREATE OR REPLACE VIEW vw_reporting_date AS
SELECT COALESCE(MAX(reporting_date), CURRENT_DATE()) AS reporting_date
FROM etl_run_log
WHERE run_status = 'SUCCESS';

-- Accounts receivable ageing at invoice level.
CREATE OR REPLACE VIEW vw_ar_ageing AS
SELECT
    s.invoice_id,
    s.invoice_number,
    s.invoice_date,
    s.due_date,
    r.reporting_date,
    s.customer_id,
    s.customer_name,
    s.status,
    s.total,
    s.balance AS outstanding_amount,
    CASE
        WHEN s.balance <= 0.01 THEN 0
        ELSE GREATEST(DATEDIFF(r.reporting_date, s.due_date), 0)
    END AS overdue_days,
    CASE
        WHEN s.balance <= 0.01 THEN 'Closed'
        WHEN s.due_date >= r.reporting_date THEN 'Not Due'
        WHEN DATEDIFF(r.reporting_date, s.due_date) <= 30 THEN '0-30'
        WHEN DATEDIFF(r.reporting_date, s.due_date) <= 60 THEN '31-60'
        WHEN DATEDIFF(r.reporting_date, s.due_date) <= 90 THEN '61-90'
        ELSE '90+'
    END AS ageing_bucket
FROM sales_invoices AS s
CROSS JOIN vw_reporting_date AS r;

-- Accounts payable ageing at bill level.
CREATE OR REPLACE VIEW vw_ap_ageing AS
SELECT
    b.bill_id,
    b.bill_number,
    b.bill_date,
    b.due_date,
    r.reporting_date,
    b.vendor_id,
    b.vendor_name,
    b.department,
    b.status,
    b.total,
    b.balance AS outstanding_amount,
    CASE
        WHEN b.balance <= 0.01 THEN 0
        ELSE GREATEST(DATEDIFF(r.reporting_date, b.due_date), 0)
    END AS overdue_days,
    CASE
        WHEN b.balance <= 0.01 THEN 'Closed'
        WHEN b.due_date >= r.reporting_date THEN 'Not Due'
        WHEN DATEDIFF(r.reporting_date, b.due_date) <= 30 THEN '0-30'
        WHEN DATEDIFF(r.reporting_date, b.due_date) <= 60 THEN '31-60'
        WHEN DATEDIFF(r.reporting_date, b.due_date) <= 90 THEN '61-90'
        ELSE '90+'
    END AS ageing_bucket,
    CASE
        WHEN b.vendor_gstin IS NULL OR TRIM(b.vendor_gstin) = '' THEN 'Missing GSTIN'
        WHEN b.purchase_order_number IS NULL OR TRIM(b.purchase_order_number) = '' THEN 'Missing PO'
        WHEN b.grn_number IS NULL OR TRIM(b.grn_number) = '' THEN 'Missing GRN'
        ELSE 'Control Complete'
    END AS control_status
FROM vendor_bills AS b
CROSS JOIN vw_reporting_date AS r;

-- Monthly departmental P&L calculated from signed ledger movements.
CREATE OR REPLACE VIEW vw_monthly_pnl AS
SELECT
    CAST(DATE_FORMAT(transaction_date, '%Y-%m-01') AS DATE) AS report_month,
    COALESCE(NULLIF(TRIM(department), ''), 'Unallocated') AS department,
    ROUND(SUM(CASE WHEN account_type = 'Income' THEN credit - debit ELSE 0 END), 2) AS revenue,
    ROUND(SUM(CASE WHEN account_type = 'Expense' THEN debit - credit ELSE 0 END), 2) AS operating_expense,
    ROUND(
        SUM(CASE WHEN account_type = 'Income' THEN credit - debit ELSE 0 END)
        - SUM(CASE WHEN account_type = 'Expense' THEN debit - credit ELSE 0 END),
        2
    ) AS operating_profit
FROM general_ledger
WHERE account_type IN ('Income', 'Expense')
GROUP BY
    CAST(DATE_FORMAT(transaction_date, '%Y-%m-01') AS DATE),
    COALESCE(NULLIF(TRIM(department), ''), 'Unallocated');

-- Map detailed Zoho ledger accounts to management-budget categories.
CREATE OR REPLACE VIEW vw_actuals_budget_mapped AS
SELECT
    CAST(DATE_FORMAT(transaction_date, '%Y-%m-01') AS DATE) AS report_month,
    COALESCE(NULLIF(TRIM(department), ''), 'Unallocated') AS department,
    CASE
        WHEN account_type = 'Income' THEN 'Sales Revenue'
        WHEN account = 'Purchases' THEN 'Purchases'
        WHEN account = 'Marketing Expense' THEN 'Marketing Expense'
        WHEN account = 'Software Subscription' THEN 'Technology Expense'
        ELSE 'Administration Expense'
    END AS budget_account,
    ROUND(SUM(
        CASE
            WHEN account_type = 'Income' THEN credit - debit
            ELSE debit - credit
        END
    ), 2) AS actual_amount
FROM general_ledger
WHERE account_type IN ('Income', 'Expense')
GROUP BY
    CAST(DATE_FORMAT(transaction_date, '%Y-%m-01') AS DATE),
    COALESCE(NULLIF(TRIM(department), ''), 'Unallocated'),
    CASE
        WHEN account_type = 'Income' THEN 'Sales Revenue'
        WHEN account = 'Purchases' THEN 'Purchases'
        WHEN account = 'Marketing Expense' THEN 'Marketing Expense'
        WHEN account = 'Software Subscription' THEN 'Technology Expense'
        ELSE 'Administration Expense'
    END;

-- Budget versus actual, with finance-friendly favourable variance signs.
CREATE OR REPLACE VIEW vw_budget_vs_actual AS
SELECT
    b.budget_month AS report_month,
    b.department,
    b.account,
    b.version AS budget_version,
    b.budget_amount,
    COALESCE(a.actual_amount, 0) AS actual_amount,
    ROUND(
        CASE
            WHEN b.account = 'Sales Revenue'
                THEN COALESCE(a.actual_amount, 0) - b.budget_amount
            ELSE b.budget_amount - COALESCE(a.actual_amount, 0)
        END,
        2
    ) AS favourable_variance,
    ROUND(
        CASE
            WHEN b.budget_amount = 0 THEN NULL
            WHEN b.account = 'Sales Revenue'
                THEN (COALESCE(a.actual_amount, 0) - b.budget_amount) / b.budget_amount * 100
            ELSE (b.budget_amount - COALESCE(a.actual_amount, 0)) / b.budget_amount * 100
        END,
        2
    ) AS favourable_variance_pct,
    CASE
        WHEN ABS(
            CASE
                WHEN b.account = 'Sales Revenue'
                    THEN COALESCE(a.actual_amount, 0) - b.budget_amount
                ELSE b.budget_amount - COALESCE(a.actual_amount, 0)
            END
        ) < 0.01 THEN 'On Budget'
        WHEN
            CASE
                WHEN b.account = 'Sales Revenue'
                    THEN COALESCE(a.actual_amount, 0) - b.budget_amount
                ELSE b.budget_amount - COALESCE(a.actual_amount, 0)
            END > 0 THEN 'Favourable'
        ELSE 'Unfavourable'
    END AS variance_status
FROM budget AS b
LEFT JOIN vw_actuals_budget_mapped AS a
    ON a.report_month = b.budget_month
   AND a.department = b.department
   AND a.budget_account = b.account;

-- Vendor-level payable exposure.
CREATE OR REPLACE VIEW vw_vendor_outstanding AS
SELECT
    vendor_id,
    vendor_name,
    COUNT(CASE WHEN outstanding_amount > 0.01 THEN 1 END) AS open_bill_count,
    ROUND(SUM(outstanding_amount), 2) AS outstanding_amount,
    ROUND(SUM(CASE WHEN overdue_days > 0 THEN outstanding_amount ELSE 0 END), 2) AS overdue_amount,
    MAX(overdue_days) AS maximum_overdue_days
FROM vw_ap_ageing
GROUP BY vendor_id, vendor_name;

-- Customer-level receivable exposure.
CREATE OR REPLACE VIEW vw_customer_outstanding AS
SELECT
    customer_id,
    customer_name,
    COUNT(CASE WHEN outstanding_amount > 0.01 THEN 1 END) AS open_invoice_count,
    ROUND(SUM(outstanding_amount), 2) AS outstanding_amount,
    ROUND(SUM(CASE WHEN overdue_days > 0 THEN outstanding_amount ELSE 0 END), 2) AS overdue_amount,
    MAX(overdue_days) AS maximum_overdue_days
FROM vw_ar_ageing
GROUP BY customer_id, customer_name;

CREATE OR REPLACE VIEW vw_monthly_customer_collections AS
SELECT
    CAST(DATE_FORMAT(payment_date, '%Y-%m-01') AS DATE) AS report_month,
    ROUND(SUM(amount_applied), 2) AS customer_collections
FROM customer_payments
GROUP BY CAST(DATE_FORMAT(payment_date, '%Y-%m-01') AS DATE);

CREATE OR REPLACE VIEW vw_monthly_vendor_payments AS
SELECT
    CAST(DATE_FORMAT(payment_date, '%Y-%m-01') AS DATE) AS report_month,
    ROUND(SUM(amount_applied), 2) AS vendor_payments
FROM vendor_payments
GROUP BY CAST(DATE_FORMAT(payment_date, '%Y-%m-01') AS DATE);

CREATE OR REPLACE VIEW vw_cash_months AS
SELECT CAST(DATE_FORMAT(payment_date, '%Y-%m-01') AS DATE) AS report_month
FROM customer_payments
UNION
SELECT CAST(DATE_FORMAT(payment_date, '%Y-%m-01') AS DATE) AS report_month
FROM vendor_payments;

CREATE OR REPLACE VIEW vw_monthly_cash_activity AS
SELECT
    m.report_month,
    COALESCE(c.customer_collections, 0) AS cash_collected,
    COALESCE(v.vendor_payments, 0) AS vendor_payments,
    ROUND(COALESCE(c.customer_collections, 0) - COALESCE(v.vendor_payments, 0), 2) AS net_operating_cash_activity
FROM vw_cash_months AS m
LEFT JOIN vw_monthly_customer_collections AS c
    ON c.report_month = m.report_month
LEFT JOIN vw_monthly_vendor_payments AS v
    ON v.report_month = m.report_month;

-- Exception summary used by management reporting and refresh controls.
CREATE OR REPLACE VIEW vw_exception_summary AS
SELECT
    run_id,
    severity,
    validation_rule,
    COUNT(*) AS exception_count
FROM data_quality_exceptions
GROUP BY run_id, severity, validation_rule;

-- Headline measures in one Power BI / Excel-friendly dataset.
CREATE OR REPLACE VIEW vw_mis_kpis AS
SELECT r.reporting_date, 'Total Revenue' AS metric,
       ROUND(COALESCE(SUM(p.revenue), 0), 2) AS amount_value,
       CAST(NULL AS SIGNED) AS count_value, 1 AS display_order
FROM vw_reporting_date AS r
LEFT JOIN vw_monthly_pnl AS p ON 1 = 1
GROUP BY r.reporting_date
UNION ALL
SELECT r.reporting_date, 'Operating Expense',
       ROUND(COALESCE(SUM(p.operating_expense), 0), 2),
       CAST(NULL AS SIGNED), 2
FROM vw_reporting_date AS r
LEFT JOIN vw_monthly_pnl AS p ON 1 = 1
GROUP BY r.reporting_date
UNION ALL
SELECT r.reporting_date, 'Operating Profit',
       ROUND(COALESCE(SUM(p.operating_profit), 0), 2),
       CAST(NULL AS SIGNED), 3
FROM vw_reporting_date AS r
LEFT JOIN vw_monthly_pnl AS p ON 1 = 1
GROUP BY r.reporting_date
UNION ALL
SELECT r.reporting_date, 'AR Outstanding',
       ROUND(COALESCE(SUM(a.outstanding_amount), 0), 2),
       CAST(NULL AS SIGNED), 4
FROM vw_reporting_date AS r
LEFT JOIN vw_ar_ageing AS a ON 1 = 1
GROUP BY r.reporting_date
UNION ALL
SELECT r.reporting_date, 'AP Outstanding',
       ROUND(COALESCE(SUM(a.outstanding_amount), 0), 2),
       CAST(NULL AS SIGNED), 5
FROM vw_reporting_date AS r
LEFT JOIN vw_ap_ageing AS a ON 1 = 1
GROUP BY r.reporting_date
UNION ALL
SELECT r.reporting_date, 'AR Overdue',
       ROUND(COALESCE(SUM(CASE WHEN a.overdue_days > 0 THEN a.outstanding_amount ELSE 0 END), 0), 2),
       CAST(NULL AS SIGNED), 6
FROM vw_reporting_date AS r
LEFT JOIN vw_ar_ageing AS a ON 1 = 1
GROUP BY r.reporting_date
UNION ALL
SELECT r.reporting_date, 'AP Overdue',
       ROUND(COALESCE(SUM(CASE WHEN a.overdue_days > 0 THEN a.outstanding_amount ELSE 0 END), 0), 2),
       CAST(NULL AS SIGNED), 7
FROM vw_reporting_date AS r
LEFT JOIN vw_ap_ageing AS a ON 1 = 1
GROUP BY r.reporting_date
UNION ALL
SELECT r.reporting_date, 'Open AR Invoices',
       CAST(NULL AS DECIMAL(18,2)),
       COUNT(CASE WHEN a.outstanding_amount > 0.01 THEN 1 END), 8
FROM vw_reporting_date AS r
LEFT JOIN vw_ar_ageing AS a ON 1 = 1
GROUP BY r.reporting_date
UNION ALL
SELECT r.reporting_date, 'Open AP Bills',
       CAST(NULL AS DECIMAL(18,2)),
       COUNT(CASE WHEN a.outstanding_amount > 0.01 THEN 1 END), 9
FROM vw_reporting_date AS r
LEFT JOIN vw_ap_ageing AS a ON 1 = 1
GROUP BY r.reporting_date
UNION ALL
SELECT r.reporting_date, 'Data Exceptions',
       CAST(NULL AS DECIMAL(18,2)),
       COUNT(e.exception_id), 10
FROM vw_reporting_date AS r
LEFT JOIN data_quality_exceptions AS e ON 1 = 1
GROUP BY r.reporting_date;

-- Confirmation: this should return 14 view names.
SELECT table_name AS view_name
FROM information_schema.views
WHERE table_schema = 'finance_mis'
ORDER BY table_name;

