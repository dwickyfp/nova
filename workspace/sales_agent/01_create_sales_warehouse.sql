-- Nova Sales Agent warehouse
-- Synthetic, deterministic, credential-free, and safe to inspect in demos.
--
-- Guardrail: CREATE TABLE IF NOT EXISTS plus INSERTs would duplicate fact rows
-- on a second run. The first statement below therefore fails if NOVA_SALES
-- already exists. To rebuild, explicitly review and drop that database first.

CREATE DATABASE NOVA_SALES;
USE NOVA_SALES;

CREATE TABLE dim_locations (
    location_id INT NOT NULL,
    city VARCHAR(64) NOT NULL,
    province VARCHAR(64) NOT NULL,
    sales_region VARCHAR(32) NOT NULL,
    island_group VARCHAR(32) NOT NULL,
    market_tier VARCHAR(16) NOT NULL
) PRIMARY KEY(location_id)
DISTRIBUTED BY HASH(location_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

INSERT INTO dim_locations VALUES
    (1,  'Jakarta Selatan', 'DKI Jakarta',        'Jabodetabek', 'Jawa',                'Metro'),
    (2,  'Jakarta Barat',   'DKI Jakarta',        'Jabodetabek', 'Jawa',                'Metro'),
    (3,  'Tangerang',       'Banten',             'Jabodetabek', 'Jawa',                'Metro'),
    (4,  'Bekasi',          'Jawa Barat',         'Jabodetabek', 'Jawa',                'Metro'),
    (5,  'Bogor',           'Jawa Barat',         'Jabodetabek', 'Jawa',                'Urban'),
    (6,  'Bandung',         'Jawa Barat',         'Jawa Barat',   'Jawa',                'Metro'),
    (7,  'Semarang',        'Jawa Tengah',        'Jawa Tengah',  'Jawa',                'Urban'),
    (8,  'Yogyakarta',      'DI Yogyakarta',      'Jawa Tengah',  'Jawa',                'Urban'),
    (9,  'Surabaya',        'Jawa Timur',         'Jawa Timur',   'Jawa',                'Metro'),
    (10, 'Malang',          'Jawa Timur',         'Jawa Timur',   'Jawa',                'Urban'),
    (11, 'Medan',           'Sumatera Utara',     'Sumatera',     'Sumatera',            'Metro'),
    (12, 'Pekanbaru',       'Riau',               'Sumatera',     'Sumatera',            'Urban'),
    (13, 'Palembang',       'Sumatera Selatan',   'Sumatera',     'Sumatera',            'Urban'),
    (14, 'Denpasar',        'Bali',               'Bali Nusra',   'Bali-Nusa Tenggara',  'Urban'),
    (15, 'Makassar',        'Sulawesi Selatan',   'Sulawesi',     'Sulawesi',            'Metro'),
    (16, 'Balikpapan',      'Kalimantan Timur',   'Kalimantan',   'Kalimantan',           'Urban'),
    (17, 'Banjarmasin',     'Kalimantan Selatan', 'Kalimantan',   'Kalimantan',           'Urban'),
    (18, 'Pontianak',       'Kalimantan Barat',   'Kalimantan',   'Kalimantan',           'Urban');

CREATE TABLE dim_stores (
    store_id INT NOT NULL,
    store_code VARCHAR(16) NOT NULL,
    store_name VARCHAR(128) NOT NULL,
    location_id INT NOT NULL,
    store_format VARCHAR(32) NOT NULL,
    floor_area_sqm INT NOT NULL,
    opened_date DATE NOT NULL,
    manager_name VARCHAR(96) NOT NULL,
    active_flag BOOLEAN NOT NULL
) PRIMARY KEY(store_id)
DISTRIBUTED BY HASH(store_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

INSERT INTO dim_stores
SELECT
    v AS store_id,
    CONCAT('STR-', LPAD(CAST(v AS VARCHAR), 3, '0')) AS store_code,
    CONCAT(
        CASE MOD(v - 1, 4)
            WHEN 0 THEN 'Nova Flagship '
            WHEN 1 THEN 'Nova Experience '
            WHEN 2 THEN 'Nova Express '
            ELSE 'Nova Partner '
        END,
        l.city,
        ' ', CAST(1 + FLOOR((v - 1) / 18) AS VARCHAR)
    ) AS store_name,
    l.location_id,
    CASE MOD(v - 1, 4)
        WHEN 0 THEN 'Flagship'
        WHEN 1 THEN 'Experience Store'
        WHEN 2 THEN 'Express'
        ELSE 'Partner Outlet'
    END AS store_format,
    CASE MOD(v - 1, 4)
        WHEN 0 THEN 1800 + MOD(v * 37, 900)
        WHEN 1 THEN 700 + MOD(v * 31, 500)
        WHEN 2 THEN 180 + MOD(v * 17, 180)
        ELSE 90 + MOD(v * 13, 120)
    END AS floor_area_sqm,
    DATE_ADD(CAST('2016-01-01' AS DATE), INTERVAL MOD(v * 71, 2920) DAY) AS opened_date,
    CONCAT(
        CASE MOD(v * 7, 12)
            WHEN 0 THEN 'Aditya' WHEN 1 THEN 'Ayu' WHEN 2 THEN 'Bima'
            WHEN 3 THEN 'Citra' WHEN 4 THEN 'Dewi' WHEN 5 THEN 'Dimas'
            WHEN 6 THEN 'Farhan' WHEN 7 THEN 'Intan' WHEN 8 THEN 'Maya'
            WHEN 9 THEN 'Nadia' WHEN 10 THEN 'Raka' ELSE 'Sari'
        END,
        ' ',
        CASE MOD(v * 11, 10)
            WHEN 0 THEN 'Pratama' WHEN 1 THEN 'Santoso' WHEN 2 THEN 'Wijaya'
            WHEN 3 THEN 'Kusuma' WHEN 4 THEN 'Hidayat' WHEN 5 THEN 'Permata'
            WHEN 6 THEN 'Nugroho' WHEN 7 THEN 'Lestari' WHEN 8 THEN 'Saputra'
            ELSE 'Wibowo'
        END
    ) AS manager_name,
    CASE WHEN MOD(v, 23) = 0 THEN FALSE ELSE TRUE END AS active_flag
FROM TABLE(generate_series(1, 72)) AS t(v)
JOIN dim_locations l ON l.location_id = 1 + MOD(v * 5, 18);

CREATE TABLE dim_sales_reps (
    sales_rep_id INT NOT NULL,
    employee_code VARCHAR(16) NOT NULL,
    sales_rep_name VARCHAR(96) NOT NULL,
    store_id INT NOT NULL,
    territory VARCHAR(64) NOT NULL,
    team_name VARCHAR(64) NOT NULL,
    seniority VARCHAR(24) NOT NULL,
    hire_date DATE NOT NULL,
    monthly_base_salary DECIMAL(18,2) NOT NULL,
    commission_rate DECIMAL(7,4) NOT NULL,
    active_flag BOOLEAN NOT NULL
) PRIMARY KEY(sales_rep_id)
DISTRIBUTED BY HASH(sales_rep_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

INSERT INTO dim_sales_reps
SELECT
    v AS sales_rep_id,
    CONCAT('EMP-', LPAD(CAST(v AS VARCHAR), 5, '0')) AS employee_code,
    CONCAT(
        CASE MOD(v * 13, 20)
            WHEN 0 THEN 'Aditya' WHEN 1 THEN 'Ayu' WHEN 2 THEN 'Bagus'
            WHEN 3 THEN 'Bima' WHEN 4 THEN 'Citra' WHEN 5 THEN 'Dewi'
            WHEN 6 THEN 'Dimas' WHEN 7 THEN 'Fajar' WHEN 8 THEN 'Farhan'
            WHEN 9 THEN 'Gita' WHEN 10 THEN 'Indra' WHEN 11 THEN 'Intan'
            WHEN 12 THEN 'Maya' WHEN 13 THEN 'Nadia' WHEN 14 THEN 'Putri'
            WHEN 15 THEN 'Raka' WHEN 16 THEN 'Rani' WHEN 17 THEN 'Rizky'
            WHEN 18 THEN 'Sari' ELSE 'Yoga'
        END,
        ' ',
        CASE MOD(v * 17, 16)
            WHEN 0 THEN 'Pratama' WHEN 1 THEN 'Santoso' WHEN 2 THEN 'Wijaya'
            WHEN 3 THEN 'Kusuma' WHEN 4 THEN 'Hidayat' WHEN 5 THEN 'Permata'
            WHEN 6 THEN 'Nugroho' WHEN 7 THEN 'Lestari' WHEN 8 THEN 'Saputra'
            WHEN 9 THEN 'Wibowo' WHEN 10 THEN 'Ramadhan' WHEN 11 THEN 'Mahendra'
            WHEN 12 THEN 'Ananda' WHEN 13 THEN 'Kurniawan' WHEN 14 THEN 'Setiawan'
            ELSE 'Utami'
        END
    ) AS sales_rep_name,
    s.store_id,
    l.sales_region AS territory,
    CONCAT(l.sales_region, ' - Team ', CHAR(65 + MOD(v, 4))) AS team_name,
    CASE
        WHEN MOD(v * 19, 100) < 12 THEN 'Principal'
        WHEN MOD(v * 19, 100) < 34 THEN 'Senior'
        WHEN MOD(v * 19, 100) < 72 THEN 'Associate'
        ELSE 'Junior'
    END AS seniority,
    DATE_ADD(CAST('2017-01-01' AS DATE), INTERVAL MOD(v * 47, 3100) DAY) AS hire_date,
    CAST(5500000 + MOD(v * 7919, 8500000) AS DECIMAL(18,2)) AS monthly_base_salary,
    CAST(0.0080 + MOD(v * 23, 33) / 10000.0 AS DECIMAL(7,4)) AS commission_rate,
    CASE WHEN MOD(v, 41) = 0 THEN FALSE ELSE TRUE END AS active_flag
FROM TABLE(generate_series(1, 240)) AS t(v)
JOIN dim_stores s ON s.store_id = 1 + MOD(v * 7, 72)
JOIN dim_locations l ON l.location_id = s.location_id;

CREATE TABLE dim_customers (
    customer_id BIGINT NOT NULL,
    customer_code VARCHAR(24) NOT NULL,
    full_name VARCHAR(96) NOT NULL,
    email_address VARCHAR(160) NOT NULL,
    location_id INT NOT NULL,
    customer_type VARCHAR(24) NOT NULL,
    customer_segment VARCHAR(32) NOT NULL,
    loyalty_tier VARCHAR(16) NOT NULL,
    acquisition_channel VARCHAR(32) NOT NULL,
    join_date DATE NOT NULL,
    birth_year SMALLINT NULL,
    gender VARCHAR(16) NULL,
    marketing_opt_in BOOLEAN NOT NULL,
    credit_limit DECIMAL(18,2) NOT NULL
) PRIMARY KEY(customer_id)
DISTRIBUTED BY HASH(customer_id) BUCKETS 8
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

INSERT INTO dim_customers
SELECT
    v AS customer_id,
    CONCAT('CUST-', LPAD(CAST(v AS VARCHAR), 8, '0')) AS customer_code,
    CONCAT(
        CASE MOD(v * 23, 24)
            WHEN 0 THEN 'Aditya' WHEN 1 THEN 'Aisyah' WHEN 2 THEN 'Andi'
            WHEN 3 THEN 'Ayu' WHEN 4 THEN 'Bagus' WHEN 5 THEN 'Bima'
            WHEN 6 THEN 'Citra' WHEN 7 THEN 'Dewi' WHEN 8 THEN 'Dimas'
            WHEN 9 THEN 'Fajar' WHEN 10 THEN 'Farhan' WHEN 11 THEN 'Gita'
            WHEN 12 THEN 'Indra' WHEN 13 THEN 'Intan' WHEN 14 THEN 'Maya'
            WHEN 15 THEN 'Nadia' WHEN 16 THEN 'Putri' WHEN 17 THEN 'Raka'
            WHEN 18 THEN 'Rani' WHEN 19 THEN 'Rizky' WHEN 20 THEN 'Sari'
            WHEN 21 THEN 'Tiara' WHEN 22 THEN 'Wahyu' ELSE 'Yoga'
        END,
        ' ',
        CASE MOD(v * 31, 20)
            WHEN 0 THEN 'Pratama' WHEN 1 THEN 'Santoso' WHEN 2 THEN 'Wijaya'
            WHEN 3 THEN 'Kusuma' WHEN 4 THEN 'Hidayat' WHEN 5 THEN 'Permata'
            WHEN 6 THEN 'Nugroho' WHEN 7 THEN 'Lestari' WHEN 8 THEN 'Saputra'
            WHEN 9 THEN 'Wibowo' WHEN 10 THEN 'Ramadhan' WHEN 11 THEN 'Mahendra'
            WHEN 12 THEN 'Ananda' WHEN 13 THEN 'Kurniawan' WHEN 14 THEN 'Setiawan'
            WHEN 15 THEN 'Utami' WHEN 16 THEN 'Gunawan' WHEN 17 THEN 'Firmansyah'
            WHEN 18 THEN 'Purnama' ELSE 'Syahputra'
        END
    ) AS full_name,
    CONCAT('customer', LPAD(CAST(v AS VARCHAR), 8, '0'), '@example.test') AS email_address,
    1 + MOD(v * 29 + FLOOR(v / 97), 18) AS location_id,
    CASE WHEN MOD(v * 37, 100) < 7 THEN 'Business' ELSE 'Individual' END AS customer_type,
    CASE
        WHEN MOD(v * 41, 100) < 8 THEN 'Strategic'
        WHEN MOD(v * 41, 100) < 25 THEN 'High Value'
        WHEN MOD(v * 41, 100) < 67 THEN 'Core'
        ELSE 'Occasional'
    END AS customer_segment,
    CASE
        WHEN MOD(v * 43, 100) < 5 THEN 'Platinum'
        WHEN MOD(v * 43, 100) < 19 THEN 'Gold'
        WHEN MOD(v * 43, 100) < 48 THEN 'Silver'
        ELSE 'Bronze'
    END AS loyalty_tier,
    CASE MOD(v * 47, 7)
        WHEN 0 THEN 'Organic Search' WHEN 1 THEN 'Paid Social'
        WHEN 2 THEN 'Marketplace' WHEN 3 THEN 'Store Walk-in'
        WHEN 4 THEN 'Referral' WHEN 5 THEN 'Affiliate'
        ELSE 'Corporate Sales'
    END AS acquisition_channel,
    DATE_ADD(CAST('2018-01-01' AS DATE), INTERVAL MOD(v * 53, 2100) DAY) AS join_date,
    CASE WHEN MOD(v, 25) = 0 THEN NULL ELSE 1958 + MOD(v * 59, 47) END AS birth_year,
    CASE MOD(v * 61, 20)
        WHEN 0 THEN NULL
        WHEN 1 THEN 'Unspecified'
        WHEN 2 THEN 'Unspecified'
        WHEN 3 THEN 'Unspecified'
        WHEN 4 THEN 'Unspecified'
        WHEN 5 THEN 'Male'
        WHEN 6 THEN 'Male'
        WHEN 7 THEN 'Male'
        WHEN 8 THEN 'Male'
        WHEN 9 THEN 'Male'
        WHEN 10 THEN 'Male'
        WHEN 11 THEN 'Male'
        ELSE 'Female'
    END AS gender,
    CASE WHEN MOD(v * 67, 100) < 63 THEN TRUE ELSE FALSE END AS marketing_opt_in,
    CAST(
        CASE
            WHEN MOD(v * 41, 100) < 8 THEN 75000000 + MOD(v * 71, 125000000)
            WHEN MOD(v * 41, 100) < 25 THEN 25000000 + MOD(v * 71, 50000000)
            ELSE 5000000 + MOD(v * 71, 20000000)
        END AS DECIMAL(18,2)
    ) AS credit_limit
FROM TABLE(generate_series(1, 200000)) AS t(v);

CREATE TABLE dim_products (
    product_id INT NOT NULL,
    sku VARCHAR(24) NOT NULL,
    product_name VARCHAR(160) NOT NULL,
    category VARCHAR(48) NOT NULL,
    subcategory VARCHAR(64) NOT NULL,
    brand VARCHAR(48) NOT NULL,
    supplier_tier VARCHAR(24) NOT NULL,
    list_price DECIMAL(18,2) NOT NULL,
    unit_cost DECIMAL(18,2) NOT NULL,
    warranty_months SMALLINT NOT NULL,
    launch_date DATE NOT NULL,
    active_flag BOOLEAN NOT NULL
) PRIMARY KEY(product_id)
DISTRIBUTED BY HASH(product_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

INSERT INTO dim_products
WITH classified AS (
    SELECT
        v,
        CASE MOD(v - 1, 8)
            WHEN 0 THEN 'Electronics' WHEN 1 THEN 'Home Appliances'
            WHEN 2 THEN 'Computing' WHEN 3 THEN 'Mobile & Wearables'
            WHEN 4 THEN 'Audio & Entertainment' WHEN 5 THEN 'Furniture'
            WHEN 6 THEN 'Sports & Outdoor' ELSE 'Beauty & Personal Care'
        END AS category,
        CASE MOD(v * 7, 16)
            WHEN 0 THEN 'Television' WHEN 1 THEN 'Kitchen Appliances'
            WHEN 2 THEN 'Laptop' WHEN 3 THEN 'Smartphone'
            WHEN 4 THEN 'Headphone' WHEN 5 THEN 'Office Furniture'
            WHEN 6 THEN 'Fitness Equipment' WHEN 7 THEN 'Skin Care'
            WHEN 8 THEN 'Camera' WHEN 9 THEN 'Air Treatment'
            WHEN 10 THEN 'Computer Accessories' WHEN 11 THEN 'Smartwatch'
            WHEN 12 THEN 'Speaker' WHEN 13 THEN 'Home Furniture'
            WHEN 14 THEN 'Outdoor Gear' ELSE 'Hair Care'
        END AS subcategory,
        CASE MOD(v * 11, 12)
            WHEN 0 THEN 'Arunika' WHEN 1 THEN 'Borealis' WHEN 2 THEN 'Cakrawala'
            WHEN 3 THEN 'Daya' WHEN 4 THEN 'Elara' WHEN 5 THEN 'Forte'
            WHEN 6 THEN 'Garuda Tech' WHEN 7 THEN 'Harmoni' WHEN 8 THEN 'Indigo'
            WHEN 9 THEN 'Jelita' WHEN 10 THEN 'Kinetik' ELSE 'Lumina'
        END AS brand,
        CASE MOD(v - 1, 8)
            WHEN 0 THEN 2500000 + MOD(v * 7919, 17500000)
            WHEN 1 THEN 450000 + MOD(v * 7919, 7550000)
            WHEN 2 THEN 3500000 + MOD(v * 7919, 21500000)
            WHEN 3 THEN 1200000 + MOD(v * 7919, 18800000)
            WHEN 4 THEN 180000 + MOD(v * 7919, 9820000)
            WHEN 5 THEN 350000 + MOD(v * 7919, 14650000)
            WHEN 6 THEN 120000 + MOD(v * 7919, 7880000)
            ELSE 35000 + MOD(v * 7919, 2465000)
        END AS base_price
    FROM TABLE(generate_series(1, 2000)) AS t(v)
)
SELECT
    v AS product_id,
    CONCAT('SKU-', LPAD(CAST(v AS VARCHAR), 6, '0')) AS sku,
    CONCAT(brand, ' ', subcategory, ' ', LPAD(CAST(100 + MOD(v * 17, 900) AS VARCHAR), 3, '0')) AS product_name,
    category,
    subcategory,
    brand,
    CASE WHEN MOD(v * 13, 100) < 18 THEN 'Strategic'
         WHEN MOD(v * 13, 100) < 62 THEN 'Preferred'
         ELSE 'Standard' END AS supplier_tier,
    CAST(ROUND(base_price / 1000.0) * 1000 AS DECIMAL(18,2)) AS list_price,
    CAST(ROUND(base_price * (0.48 + MOD(v * 17, 31) / 100.0) / 1000.0) * 1000 AS DECIMAL(18,2)) AS unit_cost,
    CASE MOD(v, 5) WHEN 0 THEN 3 WHEN 1 THEN 6 WHEN 2 THEN 12 WHEN 3 THEN 18 ELSE 24 END AS warranty_months,
    DATE_ADD(CAST('2019-01-01' AS DATE), INTERVAL MOD(v * 79, 2600) DAY) AS launch_date,
    CASE WHEN MOD(v, 97) = 0 THEN FALSE ELSE TRUE END AS active_flag
FROM classified;

CREATE TABLE dim_campaigns (
    campaign_id INT NOT NULL,
    campaign_code VARCHAR(24) NOT NULL,
    campaign_name VARCHAR(128) NOT NULL,
    campaign_type VARCHAR(32) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    primary_channel VARCHAR(32) NOT NULL,
    discount_band VARCHAR(24) NOT NULL,
    budget_amount DECIMAL(18,2) NOT NULL
) PRIMARY KEY(campaign_id)
DISTRIBUTED BY HASH(campaign_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

INSERT INTO dim_campaigns
WITH campaign_dates AS (
    SELECT
        v,
        DATE_ADD(CAST('2023-01-01' AS DATE), INTERVAL (v - 1) MONTH) AS start_date
    FROM TABLE(generate_series(1, 48)) AS t(v)
)
SELECT
    v AS campaign_id,
    CONCAT('CMP-', DATE_FORMAT(start_date, '%Y%m')) AS campaign_code,
    CONCAT(
        CASE MONTH(start_date)
            WHEN 1 THEN 'New Year Refresh' WHEN 2 THEN 'February Rewards'
            WHEN 3 THEN 'Ramadan Early Access' WHEN 4 THEN 'Ramadan & Lebaran'
            WHEN 5 THEN 'Back to Routine' WHEN 6 THEN 'Mid-Year Festival'
            WHEN 7 THEN 'School & Productivity' WHEN 8 THEN 'Independence Sale'
            WHEN 9 THEN 'September Value Days' WHEN 10 THEN 'October Tech Week'
            WHEN 11 THEN '11.11 Mega Sale' ELSE '12.12 & Year End'
        END,
        ' ', CAST(YEAR(start_date) AS VARCHAR)
    ) AS campaign_name,
    CASE
        WHEN MONTH(start_date) IN (3, 4) THEN 'Religious Moment'
        WHEN MONTH(start_date) IN (6, 11, 12) THEN 'Mega Campaign'
        WHEN MONTH(start_date) IN (7, 8) THEN 'Seasonal'
        ELSE 'Always-on'
    END AS campaign_type,
    start_date,
    DATE_SUB(DATE_ADD(start_date, INTERVAL 1 MONTH), INTERVAL 1 DAY) AS end_date,
    CASE MOD(v, 4) WHEN 0 THEN 'Marketplace' WHEN 1 THEN 'Mobile App'
         WHEN 2 THEN 'Store' ELSE 'Website' END AS primary_channel,
    CASE WHEN MONTH(start_date) IN (3, 4, 11, 12) THEN 'High (15-30%)'
         WHEN MONTH(start_date) IN (6, 7, 8) THEN 'Medium (10-20%)'
         ELSE 'Tactical (5-15%)' END AS discount_band,
    CAST(
        (CASE WHEN MONTH(start_date) IN (3, 4, 11, 12) THEN 1800000000 ELSE 650000000 END)
        + MOD(v * 104729, 550000000) AS DECIMAL(18,2)
    ) AS budget_amount
FROM campaign_dates;

CREATE TABLE fact_sales (
    order_date DATE NOT NULL,
    sale_id BIGINT NOT NULL,
    order_timestamp DATETIME NOT NULL,
    order_number VARCHAR(32) NOT NULL,
    customer_id BIGINT NOT NULL,
    product_id INT NOT NULL,
    sales_rep_id INT NOT NULL,
    store_id INT NOT NULL,
    campaign_id INT NULL,
    sales_channel VARCHAR(32) NOT NULL,
    order_status VARCHAR(24) NOT NULL,
    payment_method VARCHAR(32) NOT NULL,
    fulfillment_type VARCHAR(32) NOT NULL,
    quantity SMALLINT NOT NULL,
    unit_price DECIMAL(18,2) NOT NULL,
    gross_amount DECIMAL(18,2) NOT NULL,
    discount_pct DECIMAL(7,4) NOT NULL,
    discount_amount DECIMAL(18,2) NOT NULL,
    net_revenue DECIMAL(18,2) NOT NULL,
    tax_amount DECIMAL(18,2) NOT NULL,
    shipping_fee DECIMAL(18,2) NOT NULL,
    total_paid DECIMAL(18,2) NOT NULL,
    cogs_amount DECIMAL(18,2) NOT NULL,
    gross_profit DECIMAL(18,2) NOT NULL,
    returned_flag BOOLEAN NOT NULL,
    return_amount DECIMAL(18,2) NOT NULL,
    currency_code CHAR(3) NOT NULL
) DUPLICATE KEY(order_date, sale_id)
PARTITION BY date_trunc('month', order_date)
DISTRIBUTED BY HASH(customer_id) BUCKETS 8
PROPERTIES("replication_num"="1");

INSERT INTO fact_sales
WITH generated AS (
    SELECT
        v,
        CAST(DATE_ADD(CAST('2023-01-01' AS DATE), INTERVAL MOD(v * 17 + FLOOR(v / 97) * 13, 1360) DAY) AS DATE) AS order_date,
        1 + MOD(v * 97 + FLOOR(v / 1000) * 13, 200000) AS customer_id,
        1 + MOD(v * 89 + FLOOR(v / 211), 2000) AS product_id,
        1 + MOD(v * 83 + FLOOR(v / 503), 240) AS sales_rep_id,
        1 + MOD(v * 79 + FLOOR(v / 307), 72) AS store_id,
        1 + MOD(v * 73, 5) AS quantity,
        MOD(v * 29 + FLOOR(v / 101), 1000) AS status_roll,
        MOD(v * 31 + FLOOR(v / 67), 100) AS channel_roll,
        MOD(v * 43 + FLOOR(v / 43), 100) AS campaign_roll
    FROM TABLE(generate_series(1, 2400000)) AS t(v)
), behavior AS (
    SELECT
        g.*,
        10 + MOD(g.product_id, 5) * 3 AS cancel_cutoff,
        15 + MOD(g.product_id, 8) * 4
            + CASE WHEN g.channel_roll BETWEEN 49 AND 92 THEN 6 ELSE 0 END AS return_band
    FROM generated g
), classified AS (
    SELECT
        g.*,
        CASE
            WHEN YEAR(order_date) = 2023 AND channel_roll < 35 THEN 'Store'
            WHEN YEAR(order_date) = 2023 AND channel_roll < 60 THEN 'Marketplace'
            WHEN YEAR(order_date) = 2023 AND channel_roll < 80 THEN 'Website'
            WHEN YEAR(order_date) = 2023 AND channel_roll < 92 THEN 'Mobile App'
            WHEN YEAR(order_date) = 2024 AND channel_roll < 30 THEN 'Store'
            WHEN YEAR(order_date) = 2024 AND channel_roll < 55 THEN 'Marketplace'
            WHEN YEAR(order_date) = 2024 AND channel_roll < 73 THEN 'Website'
            WHEN YEAR(order_date) = 2024 AND channel_roll < 93 THEN 'Mobile App'
            WHEN YEAR(order_date) >= 2025 AND channel_roll < 25 THEN 'Store'
            WHEN YEAR(order_date) >= 2025 AND channel_roll < 49 THEN 'Marketplace'
            WHEN YEAR(order_date) >= 2025 AND channel_roll < 65 THEN 'Website'
            WHEN YEAR(order_date) >= 2025 AND channel_roll < 93 THEN 'Mobile App'
            ELSE 'WhatsApp B2B'
        END AS sales_channel,
        CASE
            WHEN status_roll < cancel_cutoff THEN 'Cancelled'
            WHEN status_roll < cancel_cutoff + return_band THEN 'Returned'
            WHEN status_roll < cancel_cutoff + return_band + 12 THEN 'Pending'
            WHEN status_roll < cancel_cutoff + return_band + 37 THEN 'Processing'
            ELSE 'Completed'
        END AS order_status,
        CASE MOD(v * 37, 7)
            WHEN 0 THEN 'QRIS' WHEN 1 THEN 'Virtual Account'
            WHEN 2 THEN 'Credit Card' WHEN 3 THEN 'Debit Card'
            WHEN 4 THEN 'E-Wallet' WHEN 5 THEN 'PayLater'
            ELSE 'Bank Transfer'
        END AS payment_method,
        CASE
            WHEN campaign_roll < 44
            THEN ((YEAR(order_date) - 2023) * 12 + MONTH(order_date))
            ELSE NULL
        END AS campaign_id
    FROM behavior g
), priced AS (
    SELECT
        c.*,
        p.list_price,
        p.unit_cost,
        CAST(
            p.list_price *
            CASE YEAR(c.order_date)
                WHEN 2023 THEN 0.94 WHEN 2024 THEN 0.99
                WHEN 2025 THEN 1.05 ELSE 1.10
            END AS DECIMAL(18,2)
        ) AS historical_unit_price,
        CAST(
            CASE
                WHEN c.campaign_id IS NULL THEN MOD(c.v * 17, 5) / 100.0
                WHEN MONTH(c.order_date) IN (3, 4) THEN (12 + MOD(c.v * 17, 14)) / 100.0
                WHEN MONTH(c.order_date) IN (11, 12) THEN (10 + MOD(c.v * 17, 13)) / 100.0
                ELSE (5 + MOD(c.v * 17, 12)) / 100.0
            END AS DECIMAL(7,4)
        ) AS discount_pct
    FROM classified c
    JOIN dim_products p ON p.product_id = c.product_id
), amounts AS (
    SELECT
        p.*,
        CAST(p.historical_unit_price * p.quantity AS DECIMAL(18,2)) AS gross_amount,
        CAST(p.historical_unit_price * p.quantity * p.discount_pct AS DECIMAL(18,2)) AS discount_amount,
        CAST(p.historical_unit_price * p.quantity * (1 - p.discount_pct) AS DECIMAL(18,2)) AS net_revenue,
        CAST(
            p.unit_cost * p.quantity *
            CASE YEAR(p.order_date)
                WHEN 2023 THEN 0.91 WHEN 2024 THEN 0.95
                WHEN 2025 THEN 0.98 ELSE 1.02
            END AS DECIMAL(18,2)
        ) AS cogs_amount,
        CAST(
            CASE
                WHEN p.sales_channel = 'Store' THEN 0
                WHEN p.sales_channel = 'WhatsApp B2B' THEN 85000 + MOD(p.v * 101, 165000)
                WHEN p.historical_unit_price * p.quantity >= 5000000 THEN 0
                ELSE 12000 + MOD(p.v * 103, 38000)
            END AS DECIMAL(18,2)
        ) AS shipping_fee
    FROM priced p
)
SELECT
    order_date,
    v AS sale_id,
    SECONDS_ADD(CAST(order_date AS DATETIME), MOD(v * 71, 86400)) AS order_timestamp,
    CONCAT('ORD-', DATE_FORMAT(order_date, '%Y%m%d'), '-', LPAD(CAST(v AS VARCHAR), 10, '0')) AS order_number,
    customer_id,
    product_id,
    sales_rep_id,
    store_id,
    campaign_id,
    sales_channel,
    order_status,
    payment_method,
    CASE
        WHEN sales_channel = 'Store' THEN 'Take Home'
        WHEN sales_channel = 'WhatsApp B2B' THEN 'Scheduled Delivery'
        WHEN MOD(v * 107, 100) < 18 THEN 'Click & Collect'
        WHEN MOD(v * 107, 100) < 33 THEN 'Same Day'
        ELSE 'Standard Delivery'
    END AS fulfillment_type,
    quantity,
    historical_unit_price AS unit_price,
    gross_amount,
    discount_pct,
    discount_amount,
    CASE WHEN order_status = 'Cancelled' THEN CAST(0 AS DECIMAL(18,2)) ELSE net_revenue END AS net_revenue,
    CASE WHEN order_status = 'Cancelled' THEN CAST(0 AS DECIMAL(18,2)) ELSE CAST(net_revenue * 0.11 AS DECIMAL(18,2)) END AS tax_amount,
    CASE WHEN order_status = 'Cancelled' THEN CAST(0 AS DECIMAL(18,2)) ELSE shipping_fee END AS shipping_fee,
    CASE WHEN order_status = 'Cancelled' THEN CAST(0 AS DECIMAL(18,2)) ELSE CAST(net_revenue * 1.11 + shipping_fee AS DECIMAL(18,2)) END AS total_paid,
    CASE WHEN order_status = 'Cancelled' THEN CAST(0 AS DECIMAL(18,2)) ELSE cogs_amount END AS cogs_amount,
    CASE WHEN order_status = 'Cancelled' THEN CAST(0 AS DECIMAL(18,2)) ELSE CAST(net_revenue - cogs_amount AS DECIMAL(18,2)) END AS gross_profit,
    CASE WHEN order_status = 'Returned' THEN TRUE ELSE FALSE END AS returned_flag,
    CASE WHEN order_status = 'Returned' THEN net_revenue ELSE CAST(0 AS DECIMAL(18,2)) END AS return_amount,
    'IDR' AS currency_code
FROM amounts;

CREATE TABLE fact_returns (
    return_date DATE NOT NULL,
    return_id BIGINT NOT NULL,
    sale_id BIGINT NOT NULL,
    customer_id BIGINT NOT NULL,
    product_id INT NOT NULL,
    store_id INT NOT NULL,
    return_reason VARCHAR(64) NOT NULL,
    product_condition VARCHAR(32) NOT NULL,
    resolution VARCHAR(32) NOT NULL,
    refund_amount DECIMAL(18,2) NOT NULL,
    restocking_fee DECIMAL(18,2) NOT NULL,
    processing_days SMALLINT NOT NULL
) DUPLICATE KEY(return_date, return_id)
PARTITION BY date_trunc('month', return_date)
DISTRIBUTED BY HASH(customer_id) BUCKETS 4
PROPERTIES("replication_num"="1");

INSERT INTO fact_returns
SELECT
    DATE_ADD(s.order_date, INTERVAL (1 + MOD(s.sale_id * 11, 21)) DAY) AS return_date,
    s.sale_id AS return_id,
    s.sale_id,
    s.customer_id,
    s.product_id,
    s.store_id,
    CASE MOD(s.sale_id * 13, 8)
        WHEN 0 THEN 'Changed mind' WHEN 1 THEN 'Damaged in transit'
        WHEN 2 THEN 'Wrong item received' WHEN 3 THEN 'Product defect'
        WHEN 4 THEN 'Not as described' WHEN 5 THEN 'Size or fit issue'
        WHEN 6 THEN 'Late delivery' ELSE 'Compatibility issue'
    END AS return_reason,
    CASE MOD(s.sale_id * 17, 4)
        WHEN 0 THEN 'Sealed' WHEN 1 THEN 'Opened'
        WHEN 2 THEN 'Minor Damage' ELSE 'Defective'
    END AS product_condition,
    CASE MOD(s.sale_id * 19, 5)
        WHEN 0 THEN 'Replacement' WHEN 1 THEN 'Store Credit'
        ELSE 'Refund'
    END AS resolution,
    s.return_amount AS refund_amount,
    CAST(CASE WHEN MOD(s.sale_id, 10) < 3 THEN s.return_amount * 0.05 ELSE 0 END AS DECIMAL(18,2)) AS restocking_fee,
    1 + MOD(s.sale_id * 23, 10) AS processing_days
FROM fact_sales s
WHERE s.returned_flag = TRUE;

CREATE TABLE fact_sales_targets (
    target_month DATE NOT NULL,
    sales_rep_id INT NOT NULL,
    target_revenue DECIMAL(18,2) NOT NULL,
    target_units INT NOT NULL,
    target_new_customers INT NOT NULL,
    stretch_factor DECIMAL(7,4) NOT NULL
) PRIMARY KEY(target_month, sales_rep_id)
PARTITION BY date_trunc('year', target_month)
DISTRIBUTED BY HASH(sales_rep_id) BUCKETS 4
PROPERTIES("replication_num"="1", "enable_persistent_index"="true");

INSERT INTO fact_sales_targets
SELECT
    DATE_ADD(CAST('2023-01-01' AS DATE), INTERVAL (m - 1) MONTH) AS target_month,
    r.sales_rep_id,
    CAST(
        (3800000000 + MOD(r.sales_rep_id * 15485863 + m * 32452843, 1900000000)) *
        CASE WHEN MOD(m - 1, 12) + 1 IN (3, 4, 11, 12) THEN 1.06 ELSE 1.00 END *
        (1 + FLOOR((m - 1) / 12) * 0.015)
        AS DECIMAL(18,2)
    ) AS target_revenue,
    220 + MOD(r.sales_rep_id * 97 + m * 31, 480) AS target_units,
    18 + MOD(r.sales_rep_id * 43 + m * 17, 70) AS target_new_customers,
    CAST(1.00 + MOD(r.sales_rep_id * 29 + m, 21) / 100.0 AS DECIMAL(7,4)) AS stretch_factor
FROM dim_sales_reps r
CROSS JOIN TABLE(generate_series(1, 45)) AS months(m);

CREATE VIEW vw_customer_360 AS
SELECT
    c.customer_id,
    c.customer_code,
    c.full_name,
    c.customer_type,
    c.customer_segment,
    c.loyalty_tier,
    l.city,
    l.province,
    l.sales_region,
    c.join_date,
    COUNT(s.sale_id) AS lifetime_orders,
    SUM(CASE WHEN s.order_status <> 'Cancelled' THEN s.net_revenue ELSE 0 END) AS lifetime_revenue,
    SUM(s.gross_profit) AS lifetime_gross_profit,
    SUM(CASE WHEN s.returned_flag THEN 1 ELSE 0 END) AS returned_orders,
    MIN(s.order_date) AS first_order_date,
    MAX(s.order_date) AS last_order_date,
    COUNT(DISTINCT s.sales_channel) AS channels_used
FROM dim_customers c
JOIN dim_locations l ON l.location_id = c.location_id
LEFT JOIN fact_sales s ON s.customer_id = c.customer_id
GROUP BY
    c.customer_id, c.customer_code, c.full_name, c.customer_type,
    c.customer_segment, c.loyalty_tier, l.city, l.province,
    l.sales_region, c.join_date;

CREATE VIEW vw_sales_rep_monthly_performance AS
WITH actual AS (
    SELECT
        DATE_TRUNC('month', s.order_date) AS sales_month,
        s.sales_rep_id,
        SUM(CASE WHEN s.order_status <> 'Cancelled' THEN s.net_revenue ELSE 0 END) AS actual_revenue,
        SUM(s.gross_profit) AS actual_gross_profit,
        SUM(CASE WHEN s.order_status <> 'Cancelled' THEN s.quantity ELSE 0 END) AS actual_units,
        COUNT(DISTINCT CASE WHEN s.order_status <> 'Cancelled' THEN s.sale_id END) AS actual_orders,
        COUNT(DISTINCT CASE WHEN s.order_status <> 'Cancelled' THEN s.customer_id END) AS active_customers,
        SUM(CASE WHEN s.returned_flag THEN 1 ELSE 0 END) AS returned_orders
    FROM fact_sales s
    GROUP BY DATE_TRUNC('month', s.order_date), s.sales_rep_id
)
SELECT
    t.target_month AS sales_month,
    t.sales_rep_id,
    r.sales_rep_name,
    r.team_name,
    r.territory,
    r.seniority,
    COALESCE(a.actual_revenue, 0) AS actual_revenue,
    t.target_revenue,
    CAST(COALESCE(a.actual_revenue, 0) / NULLIF(t.target_revenue, 0) AS DECIMAL(12,4)) AS quota_attainment,
    COALESCE(a.actual_gross_profit, 0) AS actual_gross_profit,
    COALESCE(a.actual_units, 0) AS actual_units,
    t.target_units,
    COALESCE(a.actual_orders, 0) AS actual_orders,
    COALESCE(a.active_customers, 0) AS active_customers,
    COALESCE(a.returned_orders, 0) AS returned_orders
FROM fact_sales_targets t
JOIN dim_sales_reps r ON r.sales_rep_id = t.sales_rep_id
LEFT JOIN actual a
    ON a.sales_rep_id = t.sales_rep_id
   AND a.sales_month = t.target_month;

ANALYZE TABLE dim_customers WITH SYNC MODE;
ANALYZE TABLE dim_products WITH SYNC MODE;
ANALYZE TABLE fact_sales WITH SYNC MODE;
ANALYZE TABLE fact_returns WITH SYNC MODE;

SELECT 'NOVA_SALES warehouse created' AS status;
SELECT 'fact_sales' AS table_name, COUNT(*) AS row_count FROM fact_sales
UNION ALL SELECT 'fact_returns', COUNT(*) FROM fact_returns
UNION ALL SELECT 'dim_customers', COUNT(*) FROM dim_customers
UNION ALL SELECT 'dim_products', COUNT(*) FROM dim_products
UNION ALL SELECT 'dim_sales_reps', COUNT(*) FROM dim_sales_reps
UNION ALL SELECT 'dim_stores', COUNT(*) FROM dim_stores
UNION ALL SELECT 'dim_campaigns', COUNT(*) FROM dim_campaigns
UNION ALL SELECT 'fact_sales_targets', COUNT(*) FROM fact_sales_targets;
