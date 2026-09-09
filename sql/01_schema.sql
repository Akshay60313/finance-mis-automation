CREATE DATABASE IF NOT EXISTS finance_mis
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE finance_mis;

CREATE TABLE IF NOT EXISTS etl_run_log (
    run_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    started_at_utc DATETIME NOT NULL,
    completed_at_utc DATETIME NULL,
    reporting_date DATE NULL,
    run_status VARCHAR(20) NOT NULL,
    source_row_count INT DEFAULT 0,
    loaded_row_count INT DEFAULT 0,
    exception_count INT DEFAULT 0,
    error_message TEXT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sales_invoices (
    invoice_id VARCHAR(50) PRIMARY KEY,
    invoice_number VARCHAR(100) NOT NULL,
    invoice_date DATE NOT NULL,
    due_date DATE NOT NULL,
    customer_id VARCHAR(50) NOT NULL,
    customer_name VARCHAR(255) NOT NULL,
    customer_gstin VARCHAR(20) NULL,
    place_of_supply VARCHAR(100) NULL,
    status VARCHAR(40) NOT NULL,
    currency_code CHAR(3) NOT NULL,
    exchange_rate DECIMAL(18,6) NOT NULL,
    item_id VARCHAR(50) NULL,
    item_name VARCHAR(255) NULL,
    sku VARCHAR(100) NULL,
    sales_account VARCHAR(150) NULL,
    quantity DECIMAL(18,4) NULL,
    rate DECIMAL(18,2) NULL,
    discount DECIMAL(18,2) DEFAULT 0,
    tax_name VARCHAR(100) NULL,
    tax_percentage DECIMAL(9,4) DEFAULT 0,
    tax_amount DECIMAL(18,2) DEFAULT 0,
    sub_total DECIMAL(18,2) NOT NULL,
    total DECIMAL(18,2) NOT NULL,
    balance DECIMAL(18,2) NOT NULL,
    salesperson VARCHAR(150) NULL,
    branch VARCHAR(150) NULL,
    reference_number VARCHAR(150) NULL,
    notes TEXT NULL,
    etl_source_file VARCHAR(255) NOT NULL,
    etl_source_row_number INT NOT NULL,
    etl_loaded_at_utc DATETIME NOT NULL,
    INDEX idx_si_invoice_number (invoice_number),
    INDEX idx_si_customer_date (customer_id, invoice_date),
    INDEX idx_si_due_balance (due_date, balance)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS vendor_bills (
    bill_id VARCHAR(50) PRIMARY KEY,
    bill_number VARCHAR(100) NOT NULL,
    bill_date DATE NOT NULL,
    due_date DATE NOT NULL,
    vendor_id VARCHAR(50) NOT NULL,
    vendor_name VARCHAR(255) NOT NULL,
    vendor_gstin VARCHAR(20) NULL,
    source_of_supply VARCHAR(100) NULL,
    status VARCHAR(40) NOT NULL,
    currency_code CHAR(3) NOT NULL,
    exchange_rate DECIMAL(18,6) NOT NULL,
    expense_account VARCHAR(150) NULL,
    item_description VARCHAR(255) NULL,
    quantity DECIMAL(18,4) NULL,
    rate DECIMAL(18,2) NULL,
    tax_name VARCHAR(100) NULL,
    tax_percentage DECIMAL(9,4) DEFAULT 0,
    tax_amount DECIMAL(18,2) DEFAULT 0,
    sub_total DECIMAL(18,2) NOT NULL,
    total DECIMAL(18,2) NOT NULL,
    balance DECIMAL(18,2) NOT NULL,
    purchase_order_number VARCHAR(100) NULL,
    grn_number VARCHAR(100) NULL,
    department VARCHAR(150) NULL,
    branch VARCHAR(150) NULL,
    reference_number VARCHAR(150) NULL,
    notes TEXT NULL,
    etl_source_file VARCHAR(255) NOT NULL,
    etl_source_row_number INT NOT NULL,
    etl_loaded_at_utc DATETIME NOT NULL,
    INDEX idx_vb_bill_number (bill_number),
    INDEX idx_vb_vendor_date (vendor_id, bill_date),
    INDEX idx_vb_due_balance (due_date, balance)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS customer_payments (
    payment_id VARCHAR(50) PRIMARY KEY,
    payment_number VARCHAR(100) NOT NULL,
    payment_date DATE NOT NULL,
    customer_id VARCHAR(50) NOT NULL,
    customer_name VARCHAR(255) NOT NULL,
    invoice_id VARCHAR(50) NOT NULL,
    invoice_number VARCHAR(100) NOT NULL,
    amount_applied DECIMAL(18,2) NOT NULL,
    currency_code CHAR(3) NOT NULL,
    payment_mode VARCHAR(100) NULL,
    reference_number VARCHAR(150) NULL,
    deposit_to VARCHAR(150) NULL,
    unused_amount DECIMAL(18,2) DEFAULT 0,
    branch VARCHAR(150) NULL,
    notes TEXT NULL,
    etl_source_file VARCHAR(255) NOT NULL,
    etl_source_row_number INT NOT NULL,
    etl_loaded_at_utc DATETIME NOT NULL,
    INDEX idx_cp_invoice (invoice_id),
    INDEX idx_cp_customer_date (customer_id, payment_date),
    CONSTRAINT fk_cp_invoice FOREIGN KEY (invoice_id)
        REFERENCES sales_invoices(invoice_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS vendor_payments (
    payment_id VARCHAR(50) PRIMARY KEY,
    payment_number VARCHAR(100) NOT NULL,
    payment_date DATE NOT NULL,
    vendor_id VARCHAR(50) NOT NULL,
    vendor_name VARCHAR(255) NOT NULL,
    bill_id VARCHAR(50) NOT NULL,
    bill_number VARCHAR(100) NOT NULL,
    amount_applied DECIMAL(18,2) NOT NULL,
    currency_code CHAR(3) NOT NULL,
    payment_mode VARCHAR(100) NULL,
    reference_number VARCHAR(150) NULL,
    paid_through VARCHAR(150) NULL,
    branch VARCHAR(150) NULL,
    notes TEXT NULL,
    etl_source_file VARCHAR(255) NOT NULL,
    etl_source_row_number INT NOT NULL,
    etl_loaded_at_utc DATETIME NOT NULL,
    INDEX idx_vp_bill (bill_id),
    INDEX idx_vp_vendor_date (vendor_id, payment_date),
    CONSTRAINT fk_vp_bill FOREIGN KEY (bill_id)
        REFERENCES vendor_bills(bill_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS expenses (
    expense_id VARCHAR(50) PRIMARY KEY,
    expense_date DATE NOT NULL,
    expense_account VARCHAR(150) NOT NULL,
    paid_through VARCHAR(150) NULL,
    vendor_id VARCHAR(50) NULL,
    vendor_name VARCHAR(255) NULL,
    gst_treatment VARCHAR(150) NULL,
    tax_name VARCHAR(100) NULL,
    tax_percentage DECIMAL(9,4) DEFAULT 0,
    tax_amount DECIMAL(18,2) DEFAULT 0,
    sub_total DECIMAL(18,2) NOT NULL,
    total DECIMAL(18,2) NOT NULL,
    payment_mode VARCHAR(100) NULL,
    reference_number VARCHAR(150) NULL,
    department VARCHAR(150) NULL,
    branch VARCHAR(150) NULL,
    description TEXT NULL,
    etl_source_file VARCHAR(255) NOT NULL,
    etl_source_row_number INT NOT NULL,
    etl_loaded_at_utc DATETIME NOT NULL,
    INDEX idx_exp_date_account (expense_date, expense_account),
    INDEX idx_exp_department (department)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS general_ledger (
    gl_line_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    transaction_date DATE NOT NULL,
    journal_number VARCHAR(100) NOT NULL,
    transaction_type VARCHAR(100) NOT NULL,
    transaction_id VARCHAR(50) NOT NULL,
    reference_number VARCHAR(150) NULL,
    account VARCHAR(150) NOT NULL,
    account_type VARCHAR(100) NOT NULL,
    contact_id VARCHAR(50) NULL,
    contact_name VARCHAR(255) NULL,
    debit DECIMAL(18,2) DEFAULT 0,
    credit DECIMAL(18,2) DEFAULT 0,
    department VARCHAR(150) NULL,
    branch VARCHAR(150) NULL,
    description TEXT NULL,
    etl_source_file VARCHAR(255) NOT NULL,
    etl_source_row_number INT NOT NULL,
    etl_loaded_at_utc DATETIME NOT NULL,
    INDEX idx_gl_journal (journal_number),
    INDEX idx_gl_date_account (transaction_date, account),
    INDEX idx_gl_transaction (transaction_type, transaction_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS budget (
    budget_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    budget_month DATE NOT NULL,
    department VARCHAR(150) NOT NULL,
    account VARCHAR(150) NOT NULL,
    budget_amount DECIMAL(18,2) NOT NULL,
    currency_code CHAR(3) NOT NULL,
    version VARCHAR(100) NOT NULL,
    notes TEXT NULL,
    etl_source_file VARCHAR(255) NOT NULL,
    etl_source_row_number INT NOT NULL,
    etl_loaded_at_utc DATETIME NOT NULL,
    UNIQUE KEY uq_budget_line (budget_month, department, account, version),
    INDEX idx_budget_month_department (budget_month, department)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS field_mapping (
    mapping_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_file VARCHAR(255) NOT NULL,
    source_column VARCHAR(255) NOT NULL,
    target_sql_table VARCHAR(150) NOT NULL,
    target_sql_column VARCHAR(150) NOT NULL,
    data_type VARCHAR(50) NULL,
    required_flag VARCHAR(10) NULL,
    key_type VARCHAR(100) NULL,
    transformation_rule TEXT NULL,
    validation_rule TEXT NULL,
    purpose TEXT NULL,
    etl_source_file VARCHAR(255) NOT NULL,
    etl_source_row_number INT NOT NULL,
    etl_loaded_at_utc DATETIME NOT NULL,
    INDEX idx_mapping_source (source_file, source_column)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS data_quality_exceptions (
    exception_id VARCHAR(50) PRIMARY KEY,
    run_id BIGINT NULL,
    severity VARCHAR(20) NOT NULL,
    source_file VARCHAR(255) NOT NULL,
    record_key VARCHAR(150) NULL,
    field_name VARCHAR(150) NULL,
    validation_rule VARCHAR(100) NOT NULL,
    observed_value TEXT NULL,
    exception_message TEXT NOT NULL,
    created_at_utc DATETIME NOT NULL,
    INDEX idx_dq_severity_rule (severity, validation_rule),
    CONSTRAINT fk_dq_run FOREIGN KEY (run_id)
        REFERENCES etl_run_log(run_id)
) ENGINE=InnoDB;

SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'finance_mis'
ORDER BY table_name;
