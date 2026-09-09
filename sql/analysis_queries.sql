-- Headline MIS KPIs
SELECT * FROM vw_mis_kpis;

-- Vendor-wise AP outstanding and overdue exposure
SELECT vendor_name,
       ROUND(SUM(outstanding_amount), 2) AS outstanding_amount,
       ROUND(SUM(CASE WHEN overdue_days > 0 THEN outstanding_amount ELSE 0 END), 2) AS overdue_amount,
       COUNT(CASE WHEN outstanding_amount > 0 THEN 1 END) AS open_bill_count
FROM vw_ap_ageing
GROUP BY vendor_name
HAVING SUM(outstanding_amount) > 0
ORDER BY overdue_amount DESC;

-- Customer-wise AR collection priority
SELECT customer_name,
       ROUND(SUM(outstanding_amount), 2) AS outstanding_amount,
       MAX(overdue_days) AS maximum_overdue_days
FROM vw_ar_ageing
GROUP BY customer_name
HAVING SUM(outstanding_amount) > 0
ORDER BY outstanding_amount DESC;

-- AP ageing-bucket summary
SELECT ageing_bucket,
       COUNT(*) AS bill_count,
       ROUND(SUM(outstanding_amount), 2) AS outstanding_amount
FROM vw_ap_ageing
GROUP BY ageing_bucket
ORDER BY CASE ageing_bucket
           WHEN 'Not Due' THEN 1 WHEN '0-30' THEN 2 WHEN '31-60' THEN 3
           WHEN '61-90' THEN 4 WHEN '90+' THEN 5 ELSE 6 END;

-- Largest unfavourable budget variances
SELECT month, department, account, budget_amount, actual_amount,
       favourable_variance, favourable_variance_pct
FROM vw_budget_vs_actual
WHERE variance_status = 'Unfavourable'
ORDER BY favourable_variance ASC
LIMIT 20;

-- Data-quality exception summary
SELECT severity, rule, COUNT(*) AS exception_count
FROM data_quality_exceptions
GROUP BY severity, rule
ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'ERROR' THEN 2 ELSE 3 END,
         exception_count DESC;

