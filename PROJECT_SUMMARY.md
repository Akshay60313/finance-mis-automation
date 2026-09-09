# Project explanation for interviews

## 30-second explanation

I built an automated MIS pipeline using Zoho Books-style exports. Python standardises dates, numeric fields and master-data text, runs finance controls, and loads clean tables into SQLite. SQL views then produce AP and AR ageing, monthly P&L, budget-versus-actual reporting and management KPIs. Every record retains source-file and row-level lineage, and exceptions are separated for review rather than silently removed.

## CV bullet

Built an automated finance MIS pipeline integrating Zoho Books-style CSV exports, Python ETL and SQLite to generate AP/AR ageing, monthly P&L, budget-versus-actual reporting and data-quality exception controls.

## What is automated

- Repeated ingestion of the same eight export types
- Data-type cleaning and standardisation
- Duplicate, GSTIN, PO/GRN, payment and ledger-balance controls
- Traceable SQLite loading
- Refreshable SQL reporting views
- Recreated MIS CSV outputs on every run

## Current limitation

This is the deterministic accounting and MIS foundation. Power BI, scheduling and AI/RAG commentary are intentionally later stages. AI will explain approved results and retrieve policy evidence; it will not calculate official finance numbers.

