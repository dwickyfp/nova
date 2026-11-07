"""A small TPC-H-shaped database for testing join discovery on a schema Nova did not design.

The eight tables, their keys and their nine single-column foreign keys follow the
TPC-H specification (``c_custkey``, ``o_custkey``, ``l_suppkey``…). Rows are
generated deterministically at about scale factor 0.01; the values are synthetic
and only the key structure matters. It prints SQL for the StarRocks MySQL client:

    python -m tests.benchmark.tpch_lab.seed > /tmp/tpch.sql

``GOLD`` is the set of joins a discovery run must find.
"""

from __future__ import annotations

import random
import sys

DATABASE = "NOVA_TPCH_LAB"

#: The TPC-H foreign keys that use one column (lineitem's composite key into
#: partsupp is the only one left out).
GOLD = [
    ("orders.o_custkey", "customer.c_custkey"),
    ("lineitem.l_orderkey", "orders.o_orderkey"),
    ("lineitem.l_partkey", "part.p_partkey"),
    ("lineitem.l_suppkey", "supplier.s_suppkey"),
    ("partsupp.ps_partkey", "part.p_partkey"),
    ("partsupp.ps_suppkey", "supplier.s_suppkey"),
    ("customer.c_nationkey", "nation.n_nationkey"),
    ("supplier.s_nationkey", "nation.n_nationkey"),
    ("nation.n_regionkey", "region.r_regionkey"),
]

REGIONS = ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"]
NATIONS = [
    ("ALGERIA", 0), ("ARGENTINA", 1), ("BRAZIL", 1), ("CANADA", 1), ("EGYPT", 4),
    ("ETHIOPIA", 0), ("FRANCE", 3), ("GERMANY", 3), ("INDIA", 2), ("INDONESIA", 2),
    ("IRAN", 4), ("IRAQ", 4), ("JAPAN", 2), ("JORDAN", 4), ("KENYA", 0), ("MOROCCO", 0),
    ("MOZAMBIQUE", 0), ("PERU", 1), ("CHINA", 2), ("ROMANIA", 3), ("SAUDI ARABIA", 4),
    ("VIETNAM", 2), ("RUSSIA", 3), ("UNITED KINGDOM", 3), ("UNITED STATES", 1),
]
SEGMENTS = ["AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"]
PRIORITIES = ["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED", "5-LOW"]
MODES = ["AIR", "FOB", "MAIL", "RAIL", "REG AIR", "SHIP", "TRUCK"]

SCALE = {"customer": 1500, "supplier": 100, "part": 2000, "orders": 15000}

DDL = {
    "region": "r_regionkey INT, r_name VARCHAR(25), r_comment VARCHAR(152)",
    "nation": "n_nationkey INT, n_name VARCHAR(25), n_regionkey INT, n_comment VARCHAR(152)",
    "supplier": ("s_suppkey INT, s_name VARCHAR(25), s_address VARCHAR(40), s_nationkey INT, "
                 "s_phone VARCHAR(15), s_acctbal DECIMAL(15,2), s_comment VARCHAR(101)"),
    "customer": ("c_custkey INT, c_name VARCHAR(25), c_address VARCHAR(40), c_nationkey INT, "
                 "c_phone VARCHAR(15), c_acctbal DECIMAL(15,2), c_mktsegment VARCHAR(10), "
                 "c_comment VARCHAR(117)"),
    "part": ("p_partkey INT, p_name VARCHAR(55), p_mfgr VARCHAR(25), p_brand VARCHAR(10), "
             "p_type VARCHAR(25), p_size INT, p_container VARCHAR(10), "
             "p_retailprice DECIMAL(15,2), p_comment VARCHAR(23)"),
    "partsupp": ("ps_partkey INT, ps_suppkey INT, ps_availqty INT, "
                 "ps_supplycost DECIMAL(15,2), ps_comment VARCHAR(199)"),
    "orders": ("o_orderkey INT, o_custkey INT, o_orderstatus VARCHAR(1), "
               "o_totalprice DECIMAL(15,2), o_orderdate DATE, o_orderpriority VARCHAR(15), "
               "o_clerk VARCHAR(15), o_shippriority INT, o_comment VARCHAR(79)"),
    "lineitem": ("l_orderkey INT, l_partkey INT, l_suppkey INT, l_linenumber INT, "
                 "l_quantity DECIMAL(15,2), l_extendedprice DECIMAL(15,2), "
                 "l_discount DECIMAL(15,2), l_tax DECIMAL(15,2), l_returnflag VARCHAR(1), "
                 "l_linestatus VARCHAR(1), l_shipdate DATE, l_shipmode VARCHAR(10), "
                 "l_comment VARCHAR(44)"),
}


def rows(rng: random.Random) -> dict[str, list[tuple]]:
    data: dict[str, list[tuple]] = {
        "region": [(i, name, f"region {i}") for i, name in enumerate(REGIONS)],
        "nation": [(i, name, region, f"nation {i}") for i, (name, region) in enumerate(NATIONS)],
    }
    data["supplier"] = [
        (k, f"Supplier#{k:09d}", f"addr {k}", rng.randrange(25), f"{10 + k % 25}-{k:07d}",
         round(rng.uniform(-999, 9999), 2), "supplier")
        for k in range(1, SCALE["supplier"] + 1)
    ]
    data["customer"] = [
        (k, f"Customer#{k:09d}", f"addr {k}", rng.randrange(25), f"{10 + k % 25}-{k:07d}",
         round(rng.uniform(-999, 9999), 2), rng.choice(SEGMENTS), "customer")
        for k in range(1, SCALE["customer"] + 1)
    ]
    data["part"] = [
        (k, f"part {k}", f"Manufacturer#{1 + k % 5}", f"Brand#{1 + k % 5}{1 + k % 5}",
         rng.choice(["STANDARD", "SMALL", "MEDIUM", "LARGE", "ECONOMY", "PROMO"]),
         rng.randint(1, 50), rng.choice(["SM CASE", "LG BOX", "MED BAG", "JUMBO PKG"]),
         round(900 + (k % 1000) / 10, 2), "part")
        for k in range(1, SCALE["part"] + 1)
    ]
    suppliers = SCALE["supplier"]
    data["partsupp"] = [
        (p, 1 + (p + i * (suppliers // 4 + (p - 1) // suppliers)) % suppliers,
         rng.randint(1, 9999), round(rng.uniform(1, 1000), 2), "partsupp")
        for p in range(1, SCALE["part"] + 1) for i in range(4)
    ]
    supply = {}
    for part, supplier, *_ in data["partsupp"]:
        supply.setdefault(part, []).append(supplier)
    data["orders"], data["lineitem"] = [], []
    # TPC-H leaves a third of customers without orders.
    active = [k for k in range(1, SCALE["customer"] + 1) if k % 3]
    for key in range(1, SCALE["orders"] + 1):
        day = f"2026-{1 + key % 9:02d}-{1 + key % 28:02d}"
        data["orders"].append((key, rng.choice(active), rng.choice("OFP"),
                               round(rng.uniform(900, 500000), 2), day, rng.choice(PRIORITIES),
                               f"Clerk#{rng.randint(1, 10):09d}", 0, "order"))
        for line in range(1, rng.randint(1, 7) + 1):
            part = rng.randint(1, SCALE["part"])
            data["lineitem"].append((
                key, part, rng.choice(supply[part]), line, rng.randint(1, 50),
                round(rng.uniform(900, 100000), 2), round(rng.uniform(0, 0.1), 2),
                round(rng.uniform(0, 0.08), 2), rng.choice("RAN"), rng.choice("OF"), day,
                rng.choice(MODES), "line"))
    return data


def literal(value: object) -> str:
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
    return str(value)


def main() -> None:
    out = sys.stdout
    out.write(f"CREATE DATABASE IF NOT EXISTS {DATABASE};\nUSE {DATABASE};\n")
    data = rows(random.Random(20261108))
    for table, columns in DDL.items():
        key = columns.split(" ", 1)[0]
        out.write(f"DROP TABLE IF EXISTS {table};\n")
        out.write(f"CREATE TABLE {table} ({columns}) DUPLICATE KEY({key}) "
                  f"DISTRIBUTED BY HASH({key}) BUCKETS 1 "
                  "PROPERTIES(\"replication_num\"=\"1\");\n")
        for start in range(0, len(data[table]), 2000):
            chunk = data[table][start:start + 2000]
            values = ",".join("(" + ",".join(literal(v) for v in row) + ")" for row in chunk)
            out.write(f"INSERT INTO {table} VALUES {values};\n")


if __name__ == "__main__":
    main()
