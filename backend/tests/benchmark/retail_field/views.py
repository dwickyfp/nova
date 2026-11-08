"""Semantic View definitions for the retail field-sales lab, in the production format."""

from __future__ import annotations

from tests.benchmark.retail_field.generate import CITIES, DATABASE
from tests.benchmark.retail_field.generate import PRODUCTS as CATALOG

#: Exact values the planner can match a user's words to ("Teh Melati" -> "Teh Melati 350ml").
PRODUCT_NAMES = [item[2] for item in CATALOG]
CATEGORIES = sorted({item[3] for item in CATALOG})
BRANDS = sorted({item[4] for item in CATALOG})
CITY_NAMES = list(CITIES)
DISTRICTS = [district for _, _, districts in CITIES.values() for district in districts]


def expression(sql: str) -> dict:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}


def field(
    name,
    datatype="String",
    *,
    dimension=True,
    time=False,
    description="",
    synonyms=(),
    sql=None,
    strategy="exact",
    samples=(),
):
    return {
        "name": name,
        "expression": expression(sql or name),
        "description": description,
        "datatype": datatype,
        "dimension": {"is_time": time} if dimension else {},
        "kind": None,
        "synonyms": list(synonyms),
        "sample_values": list(samples),
        "ai_context": None,
        "search_strategy": "range" if time else strategy,
    }


def metric(
    name,
    dataset,
    sql,
    *,
    time,
    description,
    unit=None,
    currency=None,
    synonyms=(),
    additivity="additive",
    datatype="Decimal",
):
    return {
        "name": name,
        "expression": expression(sql),
        "description": description,
        "datatype": datatype,
        "base_dataset": dataset,
        "grain": {},
        "additivity": additivity,
        "default_time_dimension": time,
        "allowed_dimensions": [],
        "non_additive_dimensions": [],
        "synonyms": list(synonyms),
        "supporting_domains": [],
        "format": None,
        "currency": currency,
        "unit": unit,
        "dependencies": [],
        "filters": [],
        "visibility": "public",
        "preferred_relationship_path": [],
        "ai_context": None,
    }


def dataset(name, key, fields, description):
    return {
        "name": name,
        "source": f"{DATABASE}.{name}",
        "description": description,
        "primary_key": key,
        "unique_keys": [],
        "grain": {"keys": key},
        "synonyms": [],
        "ai_context": None,
        "fields": fields,
    }


def relationship(source, target, column, target_column=None):
    return {
        "name": f"{source}_{target}_{column}",
        "from": source,
        "to": target,
        "from_columns": [column],
        "to_columns": [target_column or column],
        "cardinality": "many_to_one",
    }


PRODUCTS = dataset(
    "products",
    ["product_id"],
    [
        field("product_id", description="Kode produk."),
        field(
            "product_name",
            description="Nama produk lengkap dengan kemasan.",
            synonyms=["produk"],
            samples=PRODUCT_NAMES,
        ),
        field(
            "category", description="Kategori produk.", synonyms=["kategori"], samples=CATEGORIES
        ),
        field("brand", description="Merek produk.", synonyms=["merek"], samples=BRANDS),
        field("pack_size", description="Ukuran kemasan."),
    ],
    "Katalog produk minuman dan makanan ringan.",
)

OUTLETS = dataset(
    "outlets",
    ["outlet_id"],
    [
        field("outlet_id", description="Kode outlet."),
        field("outlet_name", description="Nama toko/outlet.", synonyms=["toko", "nama outlet"]),
        field(
            "channel",
            description="Jenis outlet: Supermarket, Minimarket, Warung, HoReCa, Apotek.",
            synonyms=["jenis toko", "channel"],
            samples=["Supermarket", "Minimarket", "Warung", "HoReCa", "Apotek"],
        ),
        field("tier", description="Kelas outlet A (besar), B, C (kecil)."),
        field("region", description="Wilayah penjualan.", synonyms=["wilayah", "region"]),
        field("city", description="Kota outlet.", synonyms=["kota"], samples=CITY_NAMES),
        field(
            "district",
            description="Kecamatan/area outlet.",
            synonyms=["kecamatan", "daerah"],
            samples=DISTRICTS,
        ),
        field("latitude", "Decimal", description="Lintang lokasi outlet (WGS84)."),
        field("longitude", "Decimal", description="Bujur lokasi outlet (WGS84)."),
        field("status", description="Status outlet: Active atau Closed."),
    ],
    "Outlet pelanggan dengan lokasi, kota, kecamatan, channel dan kelas.",
)


def employees(role_label):
    return dataset(
        "employees",
        ["employee_id"],
        [
            field(
                "employee_id",
                description=f"Kode pegawai ({role_label}); tampilkan employee_name untuk nama.",
            ),
            field(
                "employee_name",
                description=f"Nama {role_label}. Pakai ini saat menyebut {role_label}.",
                synonyms=[f"nama {role_label}", role_label],
            ),
            field("role", description="Jabatan: Salesman atau Driver."),
        ],
        "Pegawai lapangan: salesman dan driver.",
    )


RETAIL_SALES = {
    "version": "0.1.1",
    "name": "retail_sales",
    "description": (
        "Penjualan (sell-in) ke outlet per baris order: omzet, unit, diskon, retur, margin. "
        "Mata uang IDR; waktu bisnis Asia/Jakarta. Data 1 Mei - 4 Nov 2026."
    ),
    "ai_context": None,
    "entities": {},
    "hierarchies": {},
    "verified_queries": [],
    "named_filters": [],
    "conformed_dimensions": [],
    "question_routing_instructions": (
        "Omzet berarti net_revenue: setelah diskon dan setelah retur. Penjualan per produk, "
        "toko, kota, kecamatan, salesman, diskon dan retur ada di sini. Sebut salesman "
        "dengan employee_name, bukan kodenya."
    ),
    "query_generation_instructions": "Revenue is IDR. Use order_date for time.",
    "datasets": [
        dataset(
            "sales_orders",
            ["order_line_id"],
            [
                field("order_line_id", description="Baris order."),
                field("order_id", description="Nomor order."),
                field("invoice_no", description="Nomor invoice.", synonyms=["faktur"]),
                field(
                    "order_date",
                    "Date",
                    time=True,
                    description="Tanggal order.",
                    synonyms=["tanggal", "hari"],
                ),
                field("outlet_id", description="Outlet pemesan."),
                field("product_id", description="Produk yang dipesan."),
                field(
                    "salesman_id",
                    description="Kode salesman yang mencatat order; namanya ada di employee_name.",
                ),
                field("payment_method", description="Cara bayar.", synonyms=["pembayaran"]),
                field("status", description="Delivered atau Returned."),
                field("discount_pct", "Integer", description="Persen diskon baris order."),
                field("quantity", "Integer", dimension=False, description="quantity"),
                field("returned_qty", "Integer", dimension=False, description="returned_qty"),
                field("unit_price", "Decimal", dimension=False, description="unit_price"),
                field("gross_amount", "Decimal", dimension=False, description="gross_amount"),
                field("discount_amount", "Decimal", dimension=False, description="discount_amount"),
                field("net_amount", "Decimal", dimension=False, description="net_amount"),
                field("cost_amount", "Decimal", dimension=False, description="cost_amount"),
            ],
            "Baris order penjualan ke outlet.",
        ),
        PRODUCTS,
        OUTLETS,
        employees("salesman"),
    ],
    "relationships": [
        relationship("sales_orders", "products", "product_id"),
        relationship("sales_orders", "outlets", "outlet_id"),
        relationship("sales_orders", "employees", "salesman_id", "employee_id"),
    ],
    "metrics": [
        metric(
            "net_revenue",
            "sales_orders",
            # A returned line keeps its order value; only the returned share is taken off.
            "SUM(sales_orders.net_amount - sales_orders.net_amount * sales_orders.returned_qty"
            " / NULLIF(sales_orders.quantity, 0))",
            time="sales_orders.order_date",
            unit="IDR",
            currency="IDR",
            description=(
                "Omzet bersih: nilai order setelah diskon, dikurangi nilai unit yang diretur."
            ),
            synonyms=["omzet", "omzet bersih", "nilai penjualan", "pendapatan bersih", "revenue"],
        ),
        metric(
            "sales_before_returns",
            "sales_orders",
            "SUM(sales_orders.net_amount)",
            time="sales_orders.order_date",
            unit="IDR",
            currency="IDR",
            description="Nilai order setelah diskon, sebelum retur dikurangkan.",
            synonyms=["omzet sebelum retur", "nilai order"],
        ),
        metric(
            "returned_value",
            "sales_orders",
            "SUM(sales_orders.net_amount * sales_orders.returned_qty"
            " / NULLIF(sales_orders.quantity, 0))",
            time="sales_orders.order_date",
            unit="IDR",
            currency="IDR",
            description="Nilai unit yang diretur, setelah diskon.",
            synonyms=["nilai retur"],
        ),
        metric(
            "gross_revenue",
            "sales_orders",
            "SUM(sales_orders.gross_amount)",
            time="sales_orders.order_date",
            unit="IDR",
            currency="IDR",
            description="Penjualan kotor sebelum diskon.",
            synonyms=["penjualan kotor"],
        ),
        metric(
            "units_sold",
            "sales_orders",
            "SUM(sales_orders.quantity)",
            time="sales_orders.order_date",
            unit="unit",
            datatype="Integer",
            description="Unit terjual ke outlet.",
            synonyms=["unit terjual", "volume penjualan", "qty"],
        ),
        metric(
            "discount_amount",
            "sales_orders",
            "SUM(sales_orders.discount_amount)",
            time="sales_orders.order_date",
            unit="IDR",
            currency="IDR",
            description="Total nilai diskon.",
            synonyms=["diskon"],
        ),
        metric(
            "avg_discount_pct",
            "sales_orders",
            "AVG(sales_orders.discount_pct)",
            time="sales_orders.order_date",
            unit="percent",
            additivity="non_additive",
            description="Rata-rata persen diskon per baris order.",
            synonyms=["rata-rata diskon"],
        ),
        metric(
            "returned_units",
            "sales_orders",
            "SUM(sales_orders.returned_qty)",
            time="sales_orders.order_date",
            unit="unit",
            datatype="Integer",
            description="Unit yang diretur outlet.",
            synonyms=["retur"],
        ),
        metric(
            "return_rate",
            "sales_orders",
            "SUM(sales_orders.returned_qty) / NULLIF(SUM(sales_orders.quantity), 0)",
            time="sales_orders.order_date",
            unit="ratio",
            additivity="non_additive",
            description="Bagian unit yang diretur.",
            synonyms=["tingkat retur"],
        ),
        metric(
            "gross_margin",
            "sales_orders",
            # Net revenue after returns, minus the cost of the units kept: a returned
            # unit goes back to stock, so its cost is no longer a cost of sales.
            "SUM(sales_orders.net_amount - sales_orders.net_amount * sales_orders.returned_qty"
            " / NULLIF(sales_orders.quantity, 0))"
            " - SUM(sales_orders.cost_amount - sales_orders.cost_amount"
            " * sales_orders.returned_qty / NULLIF(sales_orders.quantity, 0))",
            time="sales_orders.order_date",
            unit="IDR",
            currency="IDR",
            description=(
                "Omzet bersih setelah retur dikurangi harga pokok unit yang tidak diretur."
            ),
            synonyms=["margin", "laba kotor"],
        ),
        metric(
            "order_count",
            "sales_orders",
            "COUNT(DISTINCT sales_orders.order_id)",
            time="sales_orders.order_date",
            unit="count",
            additivity="non_additive",
            datatype="Integer",
            description="Jumlah order.",
            synonyms=["jumlah order", "transaksi"],
        ),
        metric(
            "order_lines",
            "sales_orders",
            "COUNT(*)",
            time="sales_orders.order_date",
            unit="count",
            datatype="Integer",
            description="Jumlah baris order.",
        ),
        metric(
            "duplicate_invoice_lines",
            "sales_orders",
            "COUNT(*) - COUNT(DISTINCT CONCAT(sales_orders.invoice_no, '|', "
            "sales_orders.product_id))",
            time="sales_orders.order_date",
            unit="count",
            additivity="non_additive",
            datatype="Integer",
            description="Baris order yang mengulang produk yang sama pada invoice yang sama.",
            synonyms=["invoice ganda", "faktur ganda"],
        ),
        metric(
            "active_outlets",
            "sales_orders",
            "COUNT(DISTINCT sales_orders.outlet_id)",
            time="sales_orders.order_date",
            unit="count",
            additivity="non_additive",
            datatype="Integer",
            description="Outlet yang memesan.",
            synonyms=["outlet aktif"],
        ),
    ],
}

OUTLET_STOCK = {
    "version": "0.1.1",
    "name": "outlet_stock",
    "description": (
        "Stok harian per outlet dan produk: penjualan ke konsumen (sell-out), stok akhir, "
        "barang diterima, kehilangan penjualan dan hari stockout."
    ),
    "ai_context": None,
    "entities": {},
    "hierarchies": {},
    "verified_queries": [],
    "named_filters": [],
    "conformed_dimensions": [],
    "question_routing_instructions": "Stok, stockout, sell-out dan prediksi stok ada di sini.",
    "query_generation_instructions": "Use stock_date for time. Closing stock is per day.",
    "datasets": [
        dataset(
            "outlet_stock_daily",
            ["stock_date", "outlet_id", "product_id"],
            [
                field(
                    "stock_date",
                    "Date",
                    time=True,
                    description="Tanggal stok.",
                    synonyms=["tanggal"],
                ),
                field("outlet_id", description="Outlet."),
                field("product_id", description="Produk."),
                field("opening_qty", "Integer", dimension=False, description="opening_qty"),
                field("received_qty", "Integer", dimension=False, description="received_qty"),
                field("sold_qty", "Integer", dimension=False, description="sold_qty"),
                field("lost_sales_qty", "Integer", dimension=False, description="lost_sales_qty"),
                field("closing_qty", "Integer", dimension=False, description="closing_qty"),
                field("stockout_flag", "Integer", dimension=False, description="stockout_flag"),
            ],
            "Posisi stok harian per outlet dan produk.",
        ),
        PRODUCTS,
        OUTLETS,
    ],
    "relationships": [
        relationship("outlet_stock_daily", "products", "product_id"),
        relationship("outlet_stock_daily", "outlets", "outlet_id"),
    ],
    "metrics": [
        metric(
            "sell_out_units",
            "outlet_stock_daily",
            "SUM(outlet_stock_daily.sold_qty)",
            time="outlet_stock_daily.stock_date",
            unit="unit",
            datatype="Integer",
            description="Unit terjual ke konsumen di outlet.",
            synonyms=["sell-out", "penjualan toko"],
        ),
        metric(
            "lost_sales_units",
            "outlet_stock_daily",
            "SUM(outlet_stock_daily.lost_sales_qty)",
            time="outlet_stock_daily.stock_date",
            unit="unit",
            datatype="Integer",
            description="Permintaan yang tidak terlayani karena stok habis.",
            synonyms=["penjualan hilang"],
        ),
        metric(
            "received_units",
            "outlet_stock_daily",
            "SUM(outlet_stock_daily.received_qty)",
            time="outlet_stock_daily.stock_date",
            unit="unit",
            datatype="Integer",
            description="Unit yang benar-benar diterima outlet.",
            synonyms=["barang diterima"],
        ),
        metric(
            "closing_stock",
            "outlet_stock_daily",
            "SUM(outlet_stock_daily.closing_qty)",
            time="outlet_stock_daily.stock_date",
            unit="unit",
            datatype="Integer",
            additivity="non_additive",
            description="Stok akhir hari.",
            synonyms=["sisa stok", "stok akhir"],
        ),
        metric(
            "stockout_days",
            "outlet_stock_daily",
            "SUM(outlet_stock_daily.stockout_flag)",
            time="outlet_stock_daily.stock_date",
            unit="day",
            datatype="Integer",
            description="Hari outlet-produk kehabisan stok.",
            synonyms=["stockout", "stok habis"],
        ),
        metric(
            "stockout_rate",
            "outlet_stock_daily",
            "AVG(outlet_stock_daily.stockout_flag)",
            time="outlet_stock_daily.stock_date",
            unit="ratio",
            additivity="non_additive",
            description="Bagian hari dengan stok habis.",
            synonyms=["tingkat stockout"],
        ),
    ],
}

DELIVERY_ROUTES = {
    "version": "0.1.1",
    "name": "delivery_routes",
    "description": (
        "Rencana rute pengantaran harian per driver, urutan stop ke outlet, check-in GPS, "
        "dan rencana vs kiriman vs barang diterima per produk per outlet per hari."
    ),
    "ai_context": None,
    "entities": {},
    "hierarchies": {},
    "verified_queries": [],
    "named_filters": [],
    "conformed_dimensions": [],
    "question_routing_instructions": (
        "Rute, urutan kunjungan, jarak tempuh, kunjungan terlewat, check-in GPS, dan "
        "pembelian per produk per hari di setiap outlet ada di sini."
    ),
    "query_generation_instructions": (
        "Use plan_date for time. Coordinates are WGS84. Planned and actual distance "
        "belong to a whole route: filter its city with route_city and group drivers by "
        "driver_id, never by outlet fields. Purchase volume per outlet is "
        "planned_units grouped by outlet_name, latitude and longitude (items reach the "
        "outlet through their stop); filter the outlet's city."
    ),
    "datasets": [
        dataset(
            "route_stops",
            ["stop_id"],
            [
                field("stop_id", description="Stop pengantaran."),
                field("plan_id", description="Rencana rute."),
                field(
                    "plan_date",
                    "Date",
                    time=True,
                    description="Tanggal rute.",
                    synonyms=["tanggal"],
                ),
                field("stop_driver_id", sql="driver_id", description="Driver yang mencatat stop."),
                field(
                    "stop_seq",
                    "Integer",
                    description="Urutan stop dalam rute.",
                    synonyms=["urutan stop"],
                ),
                field("outlet_id", description="Outlet tujuan."),
                field(
                    "visit_status",
                    description="Visited, Skipped atau Failed.",
                    synonyms=["status kunjungan"],
                ),
                field("checkin_latitude", "Decimal", description="Lintang check-in GPS driver."),
                field("checkin_longitude", "Decimal", description="Bujur check-in GPS driver."),
                field(
                    "checkin_distance_m",
                    "Integer",
                    dimension=False,
                    description="checkin_distance_m",
                ),
            ],
            "Stop pengantaran per rute.",
        ),
        dataset(
            "route_plans",
            ["plan_id"],
            [
                field("plan_id", description="Rencana rute harian satu driver."),
                field("depot_id", description="Gudang asal."),
                field("vehicle_id", description="Kendaraan."),
                field(
                    "route_date", "Date", time=True, sql="plan_date", description="Tanggal rute."
                ),
                field(
                    "driver_id",
                    description="Driver rute; per driver berarti field ini.",
                    synonyms=["driver", "sopir"],
                ),
                field(
                    "route_city",
                    sql="city",
                    description="Kota asal rute (kota depot).",
                    samples=CITY_NAMES,
                ),
                field(
                    "planned_distance_km",
                    "Decimal",
                    dimension=False,
                    description="planned_distance_km",
                ),
                field(
                    "actual_distance_km",
                    "Decimal",
                    dimension=False,
                    description="actual_distance_km",
                ),
                field("planned_stops", "Integer", dimension=False, description="planned_stops"),
            ],
            "Rencana rute harian per driver.",
        ),
        dataset(
            "route_stop_items",
            ["stop_item_id"],
            [
                field("stop_item_id", description="Item stop."),
                field("stop_id", description="Stop."),
                field(
                    "delivery_date",
                    "Date",
                    time=True,
                    sql="plan_date",
                    description="Tanggal kirim.",
                ),
                field("outlet_id", description="Outlet."),
                field("product_id", description="Produk."),
                field("planned_qty", "Integer", dimension=False, description="planned_qty"),
                field("delivered_qty", "Integer", dimension=False, description="delivered_qty"),
                field("received_qty", "Integer", dimension=False, description="received_qty"),
            ],
            "Rencana vs kiriman vs barang diterima per produk di tiap stop.",
        ),
        PRODUCTS,
        OUTLETS,
        employees("driver"),
    ],
    "relationships": [
        relationship("route_stops", "route_plans", "plan_id"),
        relationship("route_stops", "outlets", "outlet_id"),
        relationship("route_stops", "employees", "stop_driver_id", "employee_id"),
        relationship("route_plans", "employees", "driver_id", "employee_id"),
        relationship("route_stop_items", "route_stops", "stop_id"),
        relationship("route_stop_items", "products", "product_id"),
    ],
    "metrics": [
        metric(
            "stop_count",
            "route_stops",
            "COUNT(*)",
            time="route_stops.plan_date",
            unit="count",
            datatype="Integer",
            description="Jumlah stop terjadwal.",
            synonyms=["jumlah kunjungan"],
        ),
        metric(
            "visited_stops",
            "route_stops",
            "SUM(CASE WHEN route_stops.visit_status = 'Visited' THEN 1 ELSE 0 END)",
            time="route_stops.plan_date",
            unit="count",
            datatype="Integer",
            description="Stop yang dikunjungi.",
            synonyms=["stop dikunjungi"],
        ),
        metric(
            "missed_stops",
            "route_stops",
            "SUM(CASE WHEN route_stops.visit_status <> 'Visited' THEN 1 ELSE 0 END)",
            time="route_stops.plan_date",
            unit="count",
            datatype="Integer",
            description="Stop terlewat atau gagal.",
            synonyms=["kunjungan terlewat"],
        ),
        metric(
            "avg_checkin_distance_m",
            "route_stops",
            "AVG(route_stops.checkin_distance_m)",
            time="route_stops.plan_date",
            unit="meter",
            additivity="non_additive",
            description="Rata-rata jarak check-in GPS ke lokasi outlet.",
            synonyms=["jarak check-in", "jarak gps"],
        ),
        metric(
            "far_checkins",
            "route_stops",
            "SUM(CASE WHEN route_stops.checkin_distance_m > 500 THEN 1 ELSE 0 END)",
            time="route_stops.plan_date",
            unit="count",
            datatype="Integer",
            description="Check-in lebih dari 500 m dari outlet.",
            synonyms=["check-in jauh"],
        ),
        metric(
            "planned_distance_km",
            "route_plans",
            "SUM(route_plans.planned_distance_km)",
            time="route_plans.route_date",
            unit="km",
            description="Jarak rute terencana.",
            synonyms=["jarak rencana"],
        ),
        metric(
            "actual_distance_km",
            "route_plans",
            "SUM(route_plans.actual_distance_km)",
            time="route_plans.route_date",
            unit="km",
            description="Jarak tempuh aktual.",
            synonyms=["jarak tempuh", "kilometer"],
        ),
        metric(
            "distance_gap_km",
            "route_plans",
            "SUM(route_plans.actual_distance_km) - SUM(route_plans.planned_distance_km)",
            time="route_plans.route_date",
            unit="km",
            description="Selisih jarak aktual dikurangi rencana; positif berarti lebih jauh.",
            synonyms=["selisih jarak", "deviasi jarak", "kelebihan jarak"],
        ),
        metric(
            "avg_distance_gap_km",
            "route_plans",
            "(SUM(route_plans.actual_distance_km) - SUM(route_plans.planned_distance_km))"
            " / NULLIF(COUNT(*), 0)",
            time="route_plans.route_date",
            unit="km",
            additivity="non_additive",
            description="Rata-rata selisih jarak aktual vs rencana per rute.",
            synonyms=["rata-rata selisih jarak", "rata-rata deviasi jarak"],
        ),
        metric(
            "route_count",
            "route_plans",
            "COUNT(*)",
            time="route_plans.route_date",
            unit="count",
            datatype="Integer",
            description="Jumlah rute harian.",
        ),
        metric(
            "planned_units",
            "route_stop_items",
            "SUM(route_stop_items.planned_qty)",
            time="route_stop_items.delivery_date",
            unit="unit",
            datatype="Integer",
            description="Unit yang direncanakan dikirim (pembelian outlet per hari).",
            synonyms=["pembelian", "volume pembelian", "rencana kirim"],
        ),
        metric(
            "delivered_units",
            "route_stop_items",
            "SUM(route_stop_items.delivered_qty)",
            time="route_stop_items.delivery_date",
            unit="unit",
            datatype="Integer",
            description="Unit yang dicatat terkirim driver.",
            synonyms=["unit terkirim", "barang terkirim"],
        ),
        metric(
            "received_units",
            "route_stop_items",
            "SUM(route_stop_items.received_qty)",
            time="route_stop_items.delivery_date",
            unit="unit",
            datatype="Integer",
            description="Unit yang benar-benar diterima outlet.",
            synonyms=["unit diterima"],
        ),
        metric(
            "delivery_gap_units",
            "route_stop_items",
            "SUM(route_stop_items.delivered_qty - route_stop_items.received_qty)",
            time="route_stop_items.delivery_date",
            unit="unit",
            datatype="Integer",
            description="Unit dicatat terkirim tetapi tidak diterima outlet.",
            synonyms=["selisih kirim", "barang hilang", "selisih barang terkirim"],
        ),
    ],
}

VIEWS = {
    "retail_sales": RETAIL_SALES,
    "outlet_stock": OUTLET_STOCK,
    "delivery_routes": DELIVERY_ROUTES,
}


def _allow_grouping(view: dict) -> None:
    """Non-additive metrics here are computed from base rows per group (AVG, COUNT
    DISTINCT, ratios), so any listed dimension may group them; they are never
    re-aggregated across groups."""
    names = [
        name
        for item in view["datasets"]
        for spec in item["fields"]
        if spec["dimension"]
        for name in (spec["name"], f"{item['name']}.{spec['name']}")
    ]
    for spec in view["metrics"]:
        if spec["additivity"] == "non_additive":
            spec["allowed_dimensions"] = sorted(set(names))


for _view in VIEWS.values():
    _allow_grouping(_view)
