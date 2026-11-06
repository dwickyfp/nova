"""Seeded retail field-sales dataset: products, outlets, sell-in, stock and delivery routes.

The data is deliberately uneven. Demand differs by city, district, channel and
outlet tier, follows weekly rhythm, holidays and a price change, and carries
injected anomalies and fraud. What was injected is written to ``truth.json``
next to the data, never into a table, so a check can tell what Studio found.

    uv run python -m tests.benchmark.retail_field.generate --out /tmp/retail_field
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 20261106
START = date(2026, 5, 1)
END = date(2026, 11, 4)
DATABASE = "NOVA_RETAIL_FIELD"

#: City -> (region, depot coordinate, districts with centre coordinates).
CITIES = {
    "Jakarta": (
        "Jawa Barat & DKI",
        (-6.2000, 106.8167),
        {
            "Jakarta Selatan": (-6.2615, 106.8106),
            "Jakarta Pusat": (-6.1865, 106.8341),
            "Jakarta Barat": (-6.1674, 106.7637),
            "Jakarta Timur": (-6.2250, 106.9004),
            "Jakarta Utara": (-6.1381, 106.8636),
        },
    ),
    "Bandung": (
        "Jawa Barat & DKI",
        (-6.9147, 107.6098),
        {
            "Coblong": (-6.8915, 107.6107),
            "Sukajadi": (-6.8857, 107.5906),
            "Lengkong": (-6.9342, 107.6235),
            "Antapani": (-6.9147, 107.6585),
        },
    ),
    "Surabaya": (
        "Jawa Timur",
        (-7.2575, 112.7521),
        {
            "Gubeng": (-7.2756, 112.7505),
            "Tegalsari": (-7.2700, 112.7380),
            "Wonokromo": (-7.3017, 112.7380),
            "Rungkut": (-7.3260, 112.7890),
        },
    ),
    "Medan": (
        "Sumatera",
        (3.5952, 98.6722),
        {
            "Medan Baru": (3.5730, 98.6560),
            "Medan Petisah": (3.5880, 98.6680),
            "Medan Johor": (3.5280, 98.6720),
        },
    ),
    "Makassar": (
        "Sulawesi",
        (-5.1477, 119.4327),
        {
            "Panakkukang": (-5.1450, 119.4560),
            "Tamalate": (-5.1910, 119.4110),
            "Rappocini": (-5.1640, 119.4340),
        },
    ),
}
OUTLETS_PER_CITY = {"Jakarta": 34, "Bandung": 20, "Surabaya": 22, "Medan": 16, "Makassar": 14}

#: id, sku, name, category, brand, pack, price, cost, base daily units per outlet.
PRODUCTS = [
    (
        "P01",
        "KSA-250",
        "Kopi Susu Gula Aren 250ml",
        "Minuman Kopi",
        "Nusantara Brew",
        "250ml",
        9500,
        5600,
        7.0,
    ),
    ("P02", "TMJ-350", "Teh Melati 350ml", "Minuman Teh", "Daun Emas", "350ml", 5000, 2900, 8.0),
    (
        "P03",
        "AMN-600",
        "Air Mineral 600ml",
        "Air Mineral",
        "Tirta Murni",
        "600ml",
        3500,
        1700,
        14.0,
    ),
    ("P04", "JJR-1L", "Jus Jeruk 1L", "Jus", "Segar Buah", "1L", 18500, 11800, 1.6),
    ("P05", "SUC-200", "Susu UHT Cokelat 200ml", "Susu", "Lembah Hijau", "200ml", 6500, 4100, 6.0),
    (
        "P06",
        "KSB-80",
        "Keripik Singkong Balado 80g",
        "Makanan Ringan",
        "Renyah Rasa",
        "80g",
        8000,
        4600,
        4.5,
    ),
    ("P07", "BGD-120", "Biskuit Gandum 120g", "Biskuit", "Gandum Emas", "120g", 11000, 6900, 3.2),
    ("P08", "MIG-85", "Mie Instan Goreng 85g", "Mie Instan", "Mie Kita", "85g", 3200, 2200, 16.0),
    ("P09", "WCK-110", "Wafer Cokelat 110g", "Biskuit", "Renyah Rasa", "110g", 7500, 4400, 3.8),
    (
        "P10",
        "ISO-500",
        "Minuman Isotonik 500ml",
        "Minuman Isotonik",
        "Ion Plus",
        "500ml",
        7000,
        3900,
        5.0,
    ),
    (
        "P11",
        "KHS-25",
        "Kopi Hitam Sachet 25g",
        "Minuman Kopi",
        "Nusantara Brew",
        "25g",
        1500,
        800,
        18.0,
    ),
    ("P12", "YGS-180", "Yogurt Stroberi 180ml", "Susu", "Lembah Hijau", "180ml", 9000, 5800, 2.4),
]
PRODUCT_A = "P01"  # strongest in Jakarta Selatan and Coblong
PRODUCT_B = "P02"  # weak in Medan: ghost deliveries, stockouts and a competitor launch

CHANNELS = {  # demand factor, visits per week, carries premium products
    "Supermarket": (2.6, 3, True),
    "Minimarket": (1.6, 3, True),
    "Warung": (0.7, 2, False),
    "HoReCa": (1.1, 2, True),
    "Apotek": (0.5, 1, False),
}
CITY_FACTOR = {"Jakarta": 1.35, "Bandung": 1.05, "Surabaya": 1.15, "Medan": 0.9, "Makassar": 0.8}
#: Product appetite per city or district beyond the city factor.
AFFINITY = {
    ("P01", "Jakarta Selatan"): 2.4,
    ("P01", "Coblong"): 2.0,
    ("P01", "Jakarta Pusat"): 1.5,
    ("P01", "Makassar"): 0.45,
    ("P01", "Medan"): 0.7,
    ("P02", "Medan"): 1.6,
    ("P02", "Surabaya"): 1.3,
    ("P03", "Makassar"): 1.4,
    ("P08", "Surabaya"): 1.4,
    ("P08", "Medan"): 1.3,
    ("P10", "Jakarta Utara"): 1.6,
    ("P12", "Jakarta Selatan"): 1.7,
    ("P04", "Bandung"): 1.4,
}
HOLIDAYS = {date(2026, 5, 27): 1.5, date(2026, 8, 17): 1.7, date(2026, 6, 16): 1.25}
FIRST_NAMES = [
    "Budi",
    "Siti",
    "Agus",
    "Dewi",
    "Rudi",
    "Rina",
    "Andi",
    "Wulan",
    "Hendra",
    "Maya",
    "Joko",
    "Fitri",
    "Bayu",
    "Lestari",
    "Dimas",
    "Ayu",
    "Fajar",
    "Indah",
    "Rizki",
    "Sari",
    "Yusuf",
    "Nadia",
    "Teguh",
    "Putri",
]
LAST_NAMES = [
    "Santoso",
    "Wijaya",
    "Pratama",
    "Saputra",
    "Hidayat",
    "Lubis",
    "Siregar",
    "Nasution",
    "Halim",
    "Rahman",
    "Gunawan",
    "Kusuma",
    "Tanjung",
    "Sitompul",
]
OUTLET_WORDS = {
    "Supermarket": ["Super", "Grand", "Mega"],
    "Minimarket": ["Mart", "Mini", "Express"],
    "Warung": ["Warung", "Toko", "Kios"],
    "HoReCa": ["Kafe", "Resto", "Hotel"],
    "Apotek": ["Apotek", "Farma"],
}


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(h))


def poisson(rng: random.Random, mean: float) -> int:
    if mean <= 0:
        return 0
    if mean > 30:
        return max(0, round(rng.gauss(mean, math.sqrt(mean))))
    limit, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def days() -> list[date]:
    return [START + timedelta(days=i) for i in range((END - START).days + 1)]


def build() -> tuple[dict[str, list[dict]], dict]:
    rng = random.Random(SEED)
    tables: dict[str, list[dict]] = {
        name: []
        for name in (
            "products",
            "depots",
            "employees",
            "outlets",
            "market_events",
            "sales_orders",
            "outlet_stock_daily",
            "route_plans",
            "route_stops",
            "route_stop_items",
        )
    }
    truth: dict = {"seed": SEED, "window": [START.isoformat(), END.isoformat()], "scenarios": []}

    for pid, sku, name, category, brand, pack, price, cost, _ in PRODUCTS:
        tables["products"].append(
            {
                "product_id": pid,
                "sku": sku,
                "product_name": name,
                "category": category,
                "brand": brand,
                "pack_size": pack,
                "list_price": price,
                "unit_cost": cost,
                "launch_date": "2024-01-15",
            }
        )

    employees: dict[str, list[dict]] = {}
    serial = 0
    for city, (region, depot, _) in CITIES.items():
        depot_id = f"DEP-{city[:3].upper()}"
        tables["depots"].append(
            {
                "depot_id": depot_id,
                "depot_name": f"Gudang {city}",
                "city": city,
                "region": region,
                "latitude": depot[0],
                "longitude": depot[1],
            }
        )
        staff = []
        sales_count = 3 if city in {"Jakarta", "Surabaya"} else 2
        for role, count in (("Salesman", sales_count), ("Driver", sales_count)):
            for _ in range(count):
                serial += 1
                person = {
                    "employee_id": f"EMP-{serial:02d}",
                    "employee_name": f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
                    "role": role,
                    "city": city,
                    "depot_id": depot_id,
                    "hire_date": (
                        date(2021, 1, 1) + timedelta(days=rng.randint(0, 1400))
                    ).isoformat(),
                }
                staff.append(person)
                tables["employees"].append(person)
        employees[city] = staff

    outlets: list[dict] = []
    for city, (region, _, districts) in CITIES.items():
        names = list(districts)
        for index in range(OUTLETS_PER_CITY[city]):
            district = names[index % len(names)]
            channel = rng.choices(list(CHANNELS), weights=[1, 3, 4, 1.5, 0.6])[0]
            tier = rng.choices(["A", "B", "C"], weights=[1, 2, 2.2])[0]
            centre = districts[district]
            words = OUTLET_WORDS[channel]
            outlet = {
                "outlet_id": f"OUT-{city[:3].upper()}-{index + 1:03d}",
                "outlet_name": (
                    f"{rng.choice(words)} {rng.choice(FIRST_NAMES)} {district.split()[-1]}"
                ),
                "channel": channel,
                "tier": tier,
                "region": region,
                "city": city,
                "district": district,
                "latitude": round(centre[0] + rng.uniform(-0.022, 0.022), 6),
                "longitude": round(centre[1] + rng.uniform(-0.022, 0.022), 6),
                "depot_id": f"DEP-{city[:3].upper()}",
                "opened_date": (
                    date(2019, 1, 1) + timedelta(days=rng.randint(0, 2000))
                ).isoformat(),
                "status": "Active",
            }
            outlets.append(outlet)
    # A Bandung supermarket closes for renovation mid-August.
    closed = next(o for o in outlets if o["city"] == "Bandung" and o["channel"] != "Apotek")
    closed["status"] = "Closed"
    closure = date(2026, 8, 12)
    truth["scenarios"].append(
        {
            "id": "A-closure",
            "kind": "anomaly",
            "outlet_id": closed["outlet_id"],
            "from": closure.isoformat(),
            "note": "Outlet closed for renovation; its sales stop.",
        }
    )
    tables["outlets"] = outlets

    # Market events: context the analyst can cite.
    competitor_day = date(2026, 9, 14)
    tables["market_events"] = [
        {
            "event_id": "EV-01",
            "event_date": "2026-05-27",
            "city": "Semua",
            "event_type": "Hari Raya",
            "category": "Semua",
            "description": "Idul Adha: permintaan naik di semua kota.",
        },
        {
            "event_id": "EV-02",
            "event_date": "2026-08-17",
            "city": "Semua",
            "event_type": "Hari Raya",
            "category": "Makanan Ringan",
            "description": "HUT RI: lonjakan makanan ringan dan air mineral.",
        },
        {
            "event_id": "EV-03",
            "event_date": "2026-09-01",
            "city": "Semua",
            "event_type": "Perubahan Harga",
            "category": "Minuman Kopi",
            "description": "Harga Kopi Susu Gula Aren naik 10%.",
        },
        {
            "event_id": "EV-04",
            "event_date": competitor_day.isoformat(),
            "city": "Medan",
            "event_type": "Kompetitor",
            "category": "Minuman Teh",
            "description": "Kompetitor meluncurkan teh botol promo beli 2 gratis 1 di Medan.",
        },
        {
            "event_id": "EV-05",
            "event_date": "2026-10-20",
            "city": "Surabaya",
            "event_type": "Cuaca",
            "category": "Minuman Isotonik",
            "description": "Gelombang panas: isotonik dan air mineral naik.",
        },
    ]
    truth["scenarios"].append(
        {
            "id": "B-teh-medan",
            "kind": "explained_decline",
            "product_id": PRODUCT_B,
            "city": "Medan",
            "from": "2026-09-01",
            "drivers": [
                "ghost deliveries by EMP fraud driver cause stockouts",
                "competitor launch 2026-09-14",
            ],
        }
    )

    carries = {}
    for outlet in outlets:
        premium = CHANNELS[outlet["channel"]][2]
        carries[outlet["outlet_id"]] = [
            p[0]
            for p in PRODUCTS
            if (premium or p[0] not in {"P04", "P12"}) and rng.random() > 0.08
        ]

    # Visit schedule: each outlet has weekdays when the salesman and driver come.
    schedule: dict[str, set[int]] = {}
    for outlet in outlets:
        per_week = CHANNELS[outlet["channel"]][1] + (1 if outlet["tier"] == "A" else 0)
        schedule[outlet["outlet_id"]] = set(rng.sample(range(6), min(per_week, 6)))

    salesman_of: dict[str, str] = {}
    driver_of_city: dict[str, list[dict]] = {}
    for city in CITIES:
        sellers = [e for e in employees[city] if e["role"] == "Salesman"]
        drivers = [e for e in employees[city] if e["role"] == "Driver"]
        driver_of_city[city] = drivers
        city_outlets = [o for o in outlets if o["city"] == city]
        for i, outlet in enumerate(sorted(city_outlets, key=lambda o: o["longitude"])):
            salesman_of[outlet["outlet_id"]] = sellers[i * len(sellers) // len(city_outlets)][
                "employee_id"
            ]

    # Fraud actors.
    phantom_rep = next(e for e in employees["Surabaya"] if e["role"] == "Salesman")["employee_id"]
    discount_rep = next(e for e in employees["Medan"] if e["role"] == "Salesman")["employee_id"]
    ghost_driver = driver_of_city["Medan"][0]["employee_id"]
    dup_rep = next(e for e in employees["Jakarta"] if e["role"] == "Salesman")["employee_id"]
    phantom_outlets = [
        o["outlet_id"] for o in outlets if salesman_of[o["outlet_id"]] == phantom_rep
    ][:3]
    discount_outlets = [
        o["outlet_id"] for o in outlets if salesman_of[o["outlet_id"]] == discount_rep
    ][:2]
    truth["scenarios"] += [
        {
            "id": "F1-phantom-orders",
            "kind": "fraud",
            "employee_id": phantom_rep,
            "outlets": phantom_outlets,
            "window": ["2026-09-20", "2026-10-31"],
            "note": "Inflated month-end orders, returned within days (commission gaming).",
        },
        {
            "id": "F2-excess-discount",
            "kind": "fraud",
            "employee_id": discount_rep,
            "outlets": discount_outlets,
            "window": ["2026-08-01", END.isoformat()],
            "note": "Discounts of 25-35% against a 0-8% norm (likely kickback).",
        },
        {
            "id": "F3-ghost-delivery",
            "kind": "fraud",
            "employee_id": ghost_driver,
            "product_id": PRODUCT_B,
            "window": ["2026-09-01", END.isoformat()],
            "note": (
                "Stops marked delivered with check-in GPS kilometres away; outlets receive nothing."
            ),
        },
        {
            "id": "F4-duplicate-invoice",
            "kind": "fraud",
            "employee_id": dup_rep,
            "note": "Same invoice number booked twice at one outlet.",
        },
    ]

    stock = {
        (o["outlet_id"], p): rng.randint(10, 40) for o in outlets for p in carries[o["outlet_id"]]
    }
    base_units = {p[0]: p[8] for p in PRODUCTS}
    line_serial = order_serial = stop_serial = item_serial = 0
    duplicated = 0
    for day in days():
        weekday = day.weekday()
        week_factor = [0.95, 0.9, 0.95, 1.0, 1.1, 1.3, 1.2][weekday]
        trend = 1 + 0.0009 * (day - START).days
        visits_by_city: dict[str, list[dict]] = {city: [] for city in CITIES}
        for outlet in outlets:
            oid = outlet["outlet_id"]
            if outlet["status"] == "Closed" and day >= closure:
                continue
            city = outlet["city"]
            tier_factor = {"A": 1.6, "B": 1.0, "C": 0.6}[outlet["tier"]]
            for product in carries[oid]:
                mean = (
                    base_units[product]
                    * CHANNELS[outlet["channel"]][0]
                    * tier_factor
                    * CITY_FACTOR[city]
                    * week_factor
                    * trend
                    * HOLIDAYS.get(day, 1.0)
                )
                mean *= AFFINITY.get(
                    (product, outlet["district"]), AFFINITY.get((product, city), 1.0)
                )
                if product == PRODUCT_A and day >= date(2026, 9, 1):
                    mean *= 0.88  # price rise
                if (
                    product in {"P10", "P03"}
                    and city == "Surabaya"
                    and date(2026, 10, 20) <= day <= date(2026, 10, 31)
                ):
                    mean *= 1.6
                if product == PRODUCT_B and city == "Medan" and day >= competitor_day:
                    mean *= 0.5
                demand = poisson(rng, mean)
                on_hand = stock[(oid, product)]
                sold = min(on_hand, demand)
                stock[(oid, product)] = on_hand - sold
                tables["outlet_stock_daily"].append(
                    {
                        "stock_date": day.isoformat(),
                        "outlet_id": oid,
                        "product_id": product,
                        "opening_qty": on_hand,
                        "received_qty": 0,
                        "sold_qty": sold,
                        "lost_sales_qty": demand - sold,
                        "closing_qty": on_hand - sold,
                        "stockout_flag": int(on_hand - sold == 0 and demand > 0),
                    }
                )
            if weekday in schedule[oid] and weekday != 6:
                visits_by_city[city].append(outlet)

        stock_rows_today = {
            (row["outlet_id"], row["product_id"]): row
            for row in tables["outlet_stock_daily"][
                -sum(len(carries[o["outlet_id"]]) for o in outlets) :
            ]
            if row["stock_date"] == day.isoformat()
        }
        for city, visits in visits_by_city.items():
            if not visits:
                continue
            depot = CITIES[city][1]
            drivers = driver_of_city[city]
            chunks = [visits[i :: len(drivers)] for i in range(len(drivers))]
            for driver, chunk in zip(drivers, chunks, strict=False):
                if not chunk:
                    continue
                inefficient = driver["employee_id"] == drivers[-1]["employee_id"]
                ordered = list(chunk)
                if inefficient:
                    rng.shuffle(ordered)  # this driver ignores the planned sequence order
                else:
                    route, here, left = [], depot, list(chunk)
                    while left:
                        nxt = min(
                            left, key=lambda o: haversine_km(here, (o["latitude"], o["longitude"]))
                        )
                        route.append(nxt)
                        left.remove(nxt)
                        here = (nxt["latitude"], nxt["longitude"])
                    ordered = route
                planned_km = actual_km = 0.0
                here = depot
                for o in ordered:
                    leg = haversine_km(here, (o["latitude"], o["longitude"])) * 1.3
                    planned_km += leg
                    here = (o["latitude"], o["longitude"])
                planned_km += haversine_km(here, depot) * 1.3
                actual_km = planned_km * rng.uniform(1.02, 1.12) * (1.25 if inefficient else 1.0)
                plan_id = f"RP-{day.strftime('%Y%m%d')}-{driver['employee_id']}"
                start = datetime.combine(day, datetime.min.time()) + timedelta(
                    hours=7, minutes=rng.randint(0, 40)
                )
                clock = start
                tables["route_plans"].append(
                    {
                        "plan_id": plan_id,
                        "plan_date": day.isoformat(),
                        "depot_id": f"DEP-{city[:3].upper()}",
                        "city": city,
                        "driver_id": driver["employee_id"],
                        "vehicle_id": f"VH-{driver['employee_id'][-2:]}",
                        "planned_stops": len(ordered),
                        "planned_distance_km": round(planned_km, 2),
                        "actual_distance_km": round(actual_km, 2),
                        "start_time": start.isoformat(sep=" "),
                        "status": "Completed",
                    }
                )
                for seq, outlet in enumerate(ordered, start=1):
                    oid = outlet["outlet_id"]
                    stop_serial += 1
                    clock += timedelta(minutes=rng.randint(14, 32))
                    ghost = (
                        driver["employee_id"] == ghost_driver
                        and day >= date(2026, 9, 1)
                        and rng.random() < 0.7
                    )
                    status = (
                        "Visited"
                        if rng.random() > 0.04 or ghost
                        else rng.choice(["Skipped", "Failed"])
                    )
                    if ghost:
                        lat = outlet["latitude"] + rng.uniform(0.03, 0.06) * rng.choice([-1, 1])
                        lon = outlet["longitude"] + rng.uniform(0.03, 0.06) * rng.choice([-1, 1])
                    else:
                        lat = outlet["latitude"] + rng.uniform(-0.0006, 0.0006)
                        lon = outlet["longitude"] + rng.uniform(-0.0006, 0.0006)
                    stop_id = f"ST-{stop_serial:06d}"
                    gps_m = round(
                        haversine_km((lat, lon), (outlet["latitude"], outlet["longitude"])) * 1000
                    )
                    tables["route_stops"].append(
                        {
                            "stop_id": stop_id,
                            "plan_id": plan_id,
                            "plan_date": day.isoformat(),
                            "driver_id": driver["employee_id"],
                            "stop_seq": seq,
                            "outlet_id": oid,
                            "planned_arrival": (start + timedelta(minutes=22 * seq)).isoformat(
                                sep=" "
                            ),
                            "actual_arrival": clock.isoformat(sep=" "),
                            "visit_status": status,
                            "checkin_latitude": round(lat, 6),
                            "checkin_longitude": round(lon, 6),
                            "checkin_distance_m": gps_m,
                        }
                    )
                    if status != "Visited":
                        continue
                    order_serial += 1
                    order_id = f"SO-{day.strftime('%y%m%d')}-{order_serial:05d}"
                    invoice = f"INV-{order_serial:06d}"
                    rep = salesman_of[oid]
                    for product in carries[oid]:
                        row = stock_rows_today.get((oid, product))
                        if row is None:
                            continue
                        target = max(
                            12, round(base_units[product] * 4 * CHANNELS[outlet["channel"]][0])
                        )
                        if product == PRODUCT_B and city == "Medan" and day >= competitor_day:
                            target = round(
                                target * 0.55
                            )  # outlets cut orders after the competitor promo
                        qty = max(0, target - row["closing_qty"])
                        qty = round(qty * rng.uniform(0.85, 1.15))
                        if qty <= 0:
                            continue
                        returned = 0
                        status_line = "Delivered"
                        if (
                            rep == phantom_rep
                            and oid in phantom_outlets
                            and date(2026, 9, 20) <= day <= date(2026, 10, 31)
                            and day.day >= 24
                        ):
                            qty *= rng.randint(4, 7)
                            returned = round(qty * rng.uniform(0.7, 0.9))
                            status_line = "Returned"
                        discount = rng.choice([0, 0, 0, 2, 3, 5, 5, 8])
                        if (
                            rep == discount_rep
                            and oid in discount_outlets
                            and day >= date(2026, 8, 1)
                        ):
                            discount = rng.randint(25, 35)
                        price = next(p[6] for p in PRODUCTS if p[0] == product)
                        if product == PRODUCT_A and day >= date(2026, 9, 1):
                            price = round(price * 1.1)
                        cost = next(p[7] for p in PRODUCTS if p[0] == product)
                        gross = qty * price
                        discount_amount = round(gross * discount / 100)
                        delivered = qty
                        if ghost and product == PRODUCT_B:
                            received = 0  # recorded as delivered, never reached the shelf
                        elif ghost:
                            received = round(qty * 0.3)
                        else:
                            received = qty - returned
                        stock[(oid, product)] += received
                        row["received_qty"] += received
                        row["closing_qty"] += received
                        line_serial += 1
                        line = {
                            "order_line_id": f"SOL-{line_serial:07d}",
                            "order_id": order_id,
                            "invoice_no": invoice,
                            "order_date": day.isoformat(),
                            "order_time": clock.isoformat(sep=" "),
                            "outlet_id": oid,
                            "product_id": product,
                            "salesman_id": rep,
                            "quantity": qty,
                            "unit_price": price,
                            "discount_pct": discount,
                            "gross_amount": gross,
                            "discount_amount": discount_amount,
                            "net_amount": gross - discount_amount,
                            "cost_amount": qty * cost,
                            "returned_qty": returned,
                            "payment_method": rng.choices(
                                ["Tunai", "Transfer", "Tempo 14 hari"], [5, 3, 2]
                            )[0],
                            "status": status_line,
                        }
                        tables["sales_orders"].append(line)
                        item_serial += 1
                        tables["route_stop_items"].append(
                            {
                                "stop_item_id": f"SI-{item_serial:07d}",
                                "stop_id": stop_id,
                                "plan_id": plan_id,
                                "plan_date": day.isoformat(),
                                "outlet_id": oid,
                                "product_id": product,
                                "planned_qty": qty,
                                "delivered_qty": delivered,
                                "received_qty": received,
                            }
                        )
                        if rep == dup_rep and duplicated < 9 and rng.random() < 0.002:
                            duplicated += 1
                            line_serial += 1
                            tables["sales_orders"].append(
                                {
                                    **line,
                                    "order_line_id": f"SOL-{line_serial:07d}",
                                    "order_time": (clock + timedelta(minutes=3)).isoformat(sep=" "),
                                }
                            )
    truth["scenarios"][-1]["duplicates"] = duplicated
    truth["rows"] = {name: len(rows) for name, rows in tables.items()}
    return tables, truth


def write(directory: Path) -> dict:
    tables, truth = build()
    directory.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with (directory / f"{name}.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (directory / "truth.json").write_text(json.dumps(truth, indent=2))
    return truth


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    truth = write(parser.parse_args().out)
    print(json.dumps(truth["rows"], indent=2))


if __name__ == "__main__":
    main()
