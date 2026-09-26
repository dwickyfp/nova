"""Frozen synthetic development/holdout cases; no customer data or model-written labels."""

CATALOGS = {
    "agents": {
        "finance": (
            "Finance: owns recognized revenue, gross margin, budgets and financial reporting. "
            "Not responsible for invoice fraud audits or advertising attribution."
        ),
        "marketing": "Marketing: owns campaign performance, conversion funnel, advertising spend "
        "and attribution. Not responsible for audited accounting revenue.",
        "finance_audit": "Finance Audit: owns duplicate invoice detection, ledger reconciliation, "
        "control failures and financial compliance audits. Not routine revenue reporting.",
        "support": "Support: owns tickets, resolution times, service satisfaction and escalations.",
    },
    "semantic_view": {
        "ledger": "Financial ledger. Metrics: recognized_revenue (booked revenue after refunds), "
        "gross_margin. Dimensions: fiscal_month, legal_entity, channel.",
        "campaigns": "Campaign attribution. Metrics: attributed_revenue (marketing attribution, "
        "not booked revenue), conversion_rate, ad_spend. Dimensions: campaign, channel, day.",
        "invoices": "Audit invoices. Metrics: duplicate_invoice_count, unbalanced_entry_count. "
        "Dimensions: vendor, invoice_month, legal_entity.",
    },
    "tools_skills": {
        "tool:query_execute": "Execute read-only SQL for current database facts. Do not run for "
        "explanations or SQL drafting.",
        "tool:ml_execute": "Run ML classification, regression, forecast, clustering or anomaly "
        "detection on authorized SQL. Not general explanations of ML.",
        "tool:data_to_chart": "Render a chart from an existing result table or a table supplied "
        "by the user. Do not invent data.",
        "tool:validate_sql": "Parse and validate drafted SQL without executing it.",
        "skill:sql-reference": "Instructions for writing correct StarRocks SQL queries and DDL.",
        "skill:create-user": "Instructions for drafting or performing CREATE USER and role grants. "
        "Do not request passwords in conversation.",
        "skill:stage-query": "Instructions for querying staged files with Nova @stage syntax.",
    },
}

# Expected labels are committed before running the live benchmark. A null workload/view
# denotes abstention, not permission to guess. ML auto delegates selection to data validation.
CASES = [
    ("dev-w1", "workload", "Halo, apa kabar?", "light"),
    (
        "dev-w2",
        "workload",
        "Mengapa margin turun saat revenue naik? Audit lintas finance dan marketing.",
        "heavy",
    ),
    ("dev-w3", "workload", "Translate 'the order has shipped' into Indonesian.", "light"),
    (
        "dev-w4",
        "workload",
        "Predict demand for next quarter and validate forecast errors.",
        "heavy",
    ),
    (
        "dev-a1",
        "agents",
        "Audit invoice duplikat dan pelanggaran kontrol pembayaran.",
        ["finance_audit"],
    ),
    ("dev-a2", "agents", "Berapa recognized revenue per legal entity bulan lalu?", ["finance"]),
    ("dev-a3", "agents", "Evaluate ad campaign conversion attribution.", ["marketing"]),
    ("dev-a4", "agents", "Apa resep nasi goreng?", []),
    ("dev-s1", "semantic_view", "Booked revenue after refunds by legal entity.", "ledger"),
    ("dev-s2", "semantic_view", "Jumlah invoice duplikat per vendor.", "invoices"),
    ("dev-s3", "semantic_view", "Ad spend and conversion rate per campaign.", "campaigns"),
    ("dev-s4", "semantic_view", "Berapa stok gudang per SKU?", None),
    (
        "dev-t1",
        "tools_skills",
        "Tulis SQL SELECT untuk @orders.data.csv, validasi saja jangan jalankan.",
        ["tool:validate_sql", "skill:sql-reference", "skill:stage-query"],
    ),
    (
        "dev-t2",
        "tools_skills",
        "Jalankan klasifikasi churn dari tabel pelanggan yang sudah disiapkan.",
        ["tool:ml_execute"],
    ),
    (
        "dev-t3",
        "tools_skills",
        "Render a bar chart of this result table: month Jan Feb, total 10 20.",
        ["tool:data_to_chart"],
    ),
    ("dev-t4", "tools_skills", "Apa kabar?", []),
    (
        "dev-m1",
        "ml:classification",
        "Klasifikasi churn, pilih yang paling akurat lewat validasi.",
        "auto",
    ),
    ("dev-m2", "ml:classification", "Use logistic regression for churn.", "logistic"),
    ("dev-m3", "ml:forecast", "Gunakan ARIMA untuk forecast penjualan.", "auto_arima"),
    ("dev-m4", "ml:anomaly_detection", "Detect anomalies using isolation forest.", "iforest"),
    (
        "hold-w1",
        "workload",
        "Ringkas kalimat ini: pelanggan meminta pengiriman lebih cepat.",
        "light",
    ),
    (
        "hold-w2",
        "workload",
        "Rekonsiliasi ledger vs invoices dan jelaskan mismatch antar entitas.",
        "heavy",
    ),
    ("hold-w3", "workload", "Show the total number of orders today.", "light"),
    (
        "hold-w4",
        "workload",
        "Investigate why the experiment increased conversion but reduced margin.",
        "heavy",
    ),
    (
        "hold-w5",
        "workload",
        "Buat SQL kompleks dengan window functions, validasi grain dan double counting.",
        "heavy",
    ),
    (
        "hold-w6",
        "workload",
        "Explain the difference between a table and a view in two sentences.",
        "light",
    ),
    (
        "hold-a1",
        "agents",
        "Tolong periksa pembayaran invoice yang tercatat dua kali.",
        ["finance_audit"],
    ),
    (
        "hold-a2",
        "agents",
        "Compare campaign conversion with booked revenue; get both domain owners.",
        ["finance", "marketing"],
    ),
    (
        "hold-a3",
        "agents",
        "Finance audit is the meeting title. The actual task is campaign attribution only.",
        ["marketing"],
    ),
    (
        "hold-a4",
        "agents",
        "Audit kepatuhan pencatatan ledger, bukan membuat laporan omzet rutin.",
        ["finance_audit"],
    ),
    ("hold-a5", "agents", "How long do support tickets take to resolve?", ["support"]),
    ("hold-a6", "agents", "Tuliskan puisi tentang hujan.", []),
    (
        "hold-s1",
        "semantic_view",
        "Recognized revenue net of refunds, grouped by channel.",
        "ledger",
    ),
    ("hold-s2", "semantic_view", "Revenue yang diatribusikan iklan per campaign.", "campaigns"),
    ("hold-s3", "semantic_view", "Berapa revenue?", None),
    (
        "hold-s4",
        "semantic_view",
        "Audit counts of unbalanced entries per legal entity.",
        "invoices",
    ),
    ("hold-s5", "semantic_view", "Give conversion rate and duplicate invoices in one view.", None),
    ("hold-s6", "semantic_view", "Customer satisfaction per support team.", None),
    (
        "hold-t1",
        "tools_skills",
        "Explain logistic regression in plain language; do not train a model.",
        [],
    ),
    (
        "hold-t2",
        "tools_skills",
        "Tulis CREATE USER analis dan grant role analyst. Hanya draft SQL dan cek sintaksnya.",
        ["tool:validate_sql", "skill:sql-reference", "skill:create-user"],
    ),
    (
        "hold-t3",
        "tools_skills",
        "Query jumlah pesanan hari ini dari database.",
        ["tool:query_execute"],
    ),
    (
        "hold-t4",
        "tools_skills",
        "Cluster pelanggan dari data SQL menjadi segmen menggunakan runtime ML.",
        ["tool:ml_execute"],
    ),
    (
        "hold-t5",
        "tools_skills",
        "Grafikkan hasil sebelumnya: region West East, sales 7 9.",
        ["tool:data_to_chart"],
    ),
    (
        "hold-t6",
        "tools_skills",
        "Buat SELECT untuk file @sales.monthly.parquet tanpa mengeksekusi, cek SQL.",
        ["tool:validate_sql", "skill:sql-reference", "skill:stage-query"],
    ),
    (
        "hold-m1",
        "ml:regression",
        "Need ridge regression with interpretable coefficients for price.",
        "ridge",
    ),
    (
        "hold-m2",
        "ml:forecast",
        "Ramalkan omzet 12 bulan; bandingkan kandidat berdasarkan error validasi.",
        "auto",
    ),
    (
        "hold-m3",
        "ml:clustering",
        "Segment customers as accurately as possible; no algorithm preference.",
        "auto",
    ),
    (
        "hold-m4",
        "ml:clustering",
        "Gunakan minibatch k-means untuk segmentasi pelanggan.",
        "minibatch_kmeans",
    ),
    (
        "hold-m5",
        "ml:classification",
        "Train a random forest classifier for fraud detection.",
        "random_forest",
    ),
    (
        "hold-m6",
        "ml:anomaly_detection",
        "Cari pencilan transaksi, pilih algoritma melalui evaluasi data.",
        "auto",
    ),
]
