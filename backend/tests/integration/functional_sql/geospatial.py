from __future__ import annotations

import math
import struct
from dataclasses import replace

from .contracts import Case, EffectCheck, FunctionSignature
from .functions import argument_types, quote

SOURCE = (
    "https://github.com/StarRocks/starrocks/blob/"
    "4a9848edf03f5c936dac664b2d52527f48e72eb0/be/src/exprs/geo_functions.cpp"
)
S2_SOURCE = "https://github.com/google/s2geometry/blob/v0.9.0/src/s2/s2earth.h"
EARTH_RADIUS_METERS = 6_371_010.0
ORIGIN_POINT_BYTES = struct.pack("<BBddd", 0, 1, 1.0, 0.0, 0.0)
POINT = "POINT (106.8272 -6.1754)"
LINE = "LINESTRING (0 0, 1 1, 2 1)"
POLYGON = "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))"
HOLED_POLYGON = "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0), (4 4, 6 4, 6 6, 4 6, 4 4))"
LOCATIONS = (
    (1, "Jakarta", 106.8272, -6.1754),
    (2, "Bandung", 107.6191, -6.9175),
    (3, "Surabaya", 112.7521, -7.2575),
    (4, "Origin", 0.0, 0.0),
    (5, "East", 179.0, 0.0),
    (6, "West", -179.0, 0.0),
)


def haversine_meters(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (lon1, lat1, lon2, lat2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + (
        math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def geospatial_case(function: FunctionSignature) -> Case | None:
    name, types = function.name, argument_types(function.signature)
    if function.kind != "Scalar":
        return None
    expression, expected, oracle, column_type = None, None, "equals", 253
    effects = []
    if name == "st_point" and types == ["DOUBLE", "DOUBLE"]:
        expression = "st_astext(st_point(CAST(106.8272 AS DOUBLE),CAST(-6.1754 AS DOUBLE)))"
        expected = [[POINT]]
        effects = [
            EffectCheck(
                "SELECT st_astext(st_point(180,90)),st_astext(st_point(-180,-90))",
                [["POINT (180 90)", "POINT (-180 -90)"]],
            ),
            EffectCheck(
                "SELECT st_point(181,0),st_point(0,91),st_point(-181,0),st_point(0,-91)",
                [[None] * 4],
            ),
            EffectCheck("SELECT st_point(NULL,0),st_point(0,NULL)", [[None] * 2]),
        ]
    elif name in {"st_x", "st_y"} and types == ["VARCHAR"]:
        expression = f"{name}(st_point(106.8272,-6.1754))"
        expected, oracle, column_type = (106.8272 if name == "st_x" else -6.1754), "number", 5
        effects = [
            EffectCheck(
                f"SELECT {name}(NULL),{name}(''),{name}('not encoded geometry'),"
                f"{name}(st_linefromtext({quote(LINE)}))",
                [[None] * 4],
            )
        ]
    elif name in {"st_astext", "st_aswkt"} and types == ["VARCHAR"]:
        expression, expected = f"{name}(st_point(106.8272,-6.1754))", [[POINT]]
        effects = [
            EffectCheck(f"SELECT {name}(st_linefromtext({quote(LINE)}))", [[LINE]]),
            EffectCheck(f"SELECT {name}(st_polygon({quote(POLYGON)}))", [[POLYGON]]),
            EffectCheck(
                f"SELECT {name}(NULL),{name}(''),{name}('not encoded geometry')", [[None] * 3]
            ),
        ]
    elif name in {"st_geometryfromtext", "st_geomfromtext"} and types == ["VARCHAR"]:
        expression, expected = f"st_astext({name}({quote(POINT)}))", [[POINT]]
        effects = [
            EffectCheck(f"SELECT st_astext({name}({quote(LINE)}))", [[LINE]]),
            EffectCheck(f"SELECT st_astext({name}({quote(POLYGON)}))", [[POLYGON]]),
            EffectCheck(
                f"SELECT {name}(NULL),{name}(''),{name}('POINT (181 0)'),"
                f"{name}('POINT (0 91)'),{name}('POINT (1)')",
                [[None] * 5],
            ),
        ]
    elif name in {"st_linefromtext", "st_linestringfromtext"} and types == ["VARCHAR"]:
        expression, expected = f"st_astext({name}({quote(LINE)}))", [[LINE]]
        effects = [
            EffectCheck(
                f"SELECT {name}(NULL),{name}(''),{name}('POINT (0 0)'),"
                f"{name}('LINESTRING (0 0)'),{name}('LINESTRING (0 0, 181 0)')",
                [[None] * 5],
            )
        ]
    elif name in {"st_polygon", "st_polyfromtext", "st_polygonfromtext"} and types == ["VARCHAR"]:
        expression, expected = f"st_astext({name}({quote(POLYGON)}))", [[POLYGON]]
        effects = [
            EffectCheck(
                f"SELECT {name}(NULL),{name}(''),{name}('POINT (0 0)'),"
                f"{name}('POLYGON ((0 0, 10 0, 10 10, 0 10))'),"
                f"{name}('POLYGON ((0 0, 10 10, 0 10, 10 0, 0 0))')",
                [[None] * 5],
            )
        ]
    elif name == "st_circle" and types == ["DOUBLE"] * 3:
        expression, expected = "st_astext(st_circle(0,0,1000))", [["CIRCLE ((0 0), 1000)"]]
        effects = [
            EffectCheck(
                "SELECT st_contains(st_circle(0,0,1000),st_point(0,0)),"
                "st_contains(st_circle(0,0,1000),st_point(1,0))",
                [[1, 0]],
            ),
            EffectCheck(
                "SELECT st_circle(NULL,0,1000),st_circle(0,NULL,1000),"
                "st_circle(0,0,NULL),st_circle(181,0,1000),st_circle(0,91,1000)",
                [[None] * 5],
            ),
            # S2 represents a negative radius as a valid empty cap.
            EffectCheck("SELECT st_contains(st_circle(0,0,-1),st_point(0,0))", [[0]]),
        ]
    elif name == "st_contains" and types == ["VARCHAR"] * 2:
        expression = f"st_contains(st_polygon({quote(POLYGON)}),st_point(5,5))"
        expected, column_type = [[1]], 1
        effects = [
            EffectCheck(f"SELECT st_contains(st_polygon({quote(POLYGON)}),st_point(50,50))", [[0]]),
            EffectCheck(
                f"SELECT st_contains(st_polygon({quote(HOLED_POLYGON)}),st_point(5,5)),"
                f"st_contains(st_polygon({quote(HOLED_POLYGON)}),st_point(2,2))",
                [[0, 1]],
            ),
            EffectCheck(
                f"SELECT st_contains(st_polygon({quote(POLYGON)}),"
                "st_linefromtext('LINESTRING (2 2, 3 3)')),"
                f"st_contains(st_polygon({quote(POLYGON)}),"
                "st_polygon('POLYGON ((2 2, 3 2, 3 3, 2 3, 2 2))'))",
                [[1, 1]],
            ),
            EffectCheck(
                "SELECT st_contains(NULL,st_point(0,0)),"
                "st_contains(st_circle(0,0,1),NULL),"
                "st_contains('',st_point(0,0)),st_contains(st_point(0,0),'')",
                [[None] * 4],
            ),
        ]
    elif name == "st_distance_sphere" and types == ["DOUBLE"] * 4:
        expression = "st_distance_sphere(106.8272,-6.1754,107.6191,-6.9175)"
        expected = haversine_meters(106.8272, -6.1754, 107.6191, -6.9175)
        oracle, column_type = "number", 5
        pairs = [(0, 0, 1, 0), (179, 0, -179, 0), (0, 90, 0, -90), (0, 0, 0, 0)]
        effects = [
            EffectCheck(
                "SELECT " + ",".join(f"st_distance_sphere({','.join(map(str, p))})" for p in pairs),
                [[haversine_meters(*pair) for pair in pairs]],
                "numeric_rows",
            ),
            EffectCheck(
                "SELECT st_distance_sphere(181,0,0,0),st_distance_sphere(0,91,0,0),"
                "st_distance_sphere(0,0,-181,0),st_distance_sphere(0,0,0,-91),"
                "st_distance_sphere(NULL,0,0,0),st_distance_sphere(0,NULL,0,0),"
                "st_distance_sphere(0,0,NULL,0),st_distance_sphere(0,0,0,NULL)",
                [[None] * 8],
            ),
        ]
    if expression is None:
        return None
    return Case(
        f"function.{function.key}",
        "geospatial",
        "SELECT " + expression,
        expected,
        oracle,
        signatures=(function.key,),
        effects=tuple(effects),
        expected_column_types=(column_type,),
        contract_source=SOURCE,
    )


def geospatial_data_cases() -> list[Case]:
    values = ",".join(
        f"({key},{quote(name)},{lon},{lat},st_point({lon},{lat}))"
        for key, name, lon, lat in LOCATIONS
    )
    setup = (
        "CREATE TABLE geo_locations (id INT NOT NULL,name VARCHAR(32),lon DOUBLE,lat DOUBLE,"
        "geometry VARCHAR(4096)) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
        "PROPERTIES('replication_num'='1')",
        "INSERT INTO geo_locations VALUES " + values,
    )
    distances = [
        [key, name, haversine_meters(106.8272, -6.1754, lon, lat)]
        for key, name, lon, lat in LOCATIONS
    ]
    origin_bytes = ORIGIN_POINT_BYTES
    cases = [
        Case(
            "geo.persisted_coordinates",
            "geospatial",
            "SELECT id,name,st_x(geometry),st_y(geometry) FROM geo_locations ORDER BY id",
            [[key, name, lon, lat] for key, name, lon, lat in LOCATIONS],
            "numeric_rows",
            setup=setup,
            expected_column_types=(3, 253, 5, 5),
        ),
        Case(
            "geo.persisted_wkt",
            "geospatial",
            "SELECT id,st_astext(geometry)=st_astext(st_point(lon,lat)) "
            "FROM geo_locations ORDER BY id",
            [[key, 1] for key, *_ in LOCATIONS],
        ),
        Case(
            "geo.nearest_location",
            "geospatial",
            "SELECT id,name,st_distance_sphere(106.8272,-6.1754,lon,lat) AS meters "
            "FROM geo_locations ORDER BY meters,id",
            sorted(distances, key=lambda row: (row[2], row[0])),
            "numeric_rows",
            expected_column_types=(3, 253, 5),
        ),
        Case(
            "geo.distance_self_join",
            "geospatial",
            "SELECT a.id,b.id,st_distance_sphere(a.lon,a.lat,b.lon,b.lat) "
            "FROM geo_locations a JOIN geo_locations b ON a.id<b.id "
            "WHERE a.id<=3 AND b.id<=3 ORDER BY a.id,b.id",
            [
                [a[0], b[0], haversine_meters(a[2], a[3], b[2], b[3])]
                for a in LOCATIONS[:3]
                for b in LOCATIONS[:3]
                if a[0] < b[0]
            ],
            "numeric_rows",
        ),
        Case(
            "geo.distance_symmetry",
            "geospatial",
            "SELECT id,st_distance_sphere(106.8272,-6.1754,lon,lat) "
            "-st_distance_sphere(lon,lat,106.8272,-6.1754) FROM geo_locations ORDER BY id",
            [[key, 0.0] for key, *_ in LOCATIONS],
            "numeric_rows",
        ),
        Case(
            "geo.circle_filter",
            "geospatial",
            "SELECT id,name FROM geo_locations "
            "WHERE st_contains(st_circle(106.8272,-6.1754,150000),geometry) ORDER BY id",
            [[1, "Jakarta"], [2, "Bandung"]],
        ),
        Case(
            "geo.polygon_join",
            "geospatial",
            "SELECT z.id,p.id FROM geo_regions z JOIN geo_locations p "
            "ON st_contains(z.geometry,p.geometry) ORDER BY z.id,p.id",
            [[1, 1], [1, 2], [1, 3], [2, 4]],
            setup=(
                "CREATE TABLE geo_regions (id INT NOT NULL,geometry VARCHAR(4096)) "
                "DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1 "
                "PROPERTIES('replication_num'='1')",
                "INSERT INTO geo_regions VALUES "
                "(1,st_polygon('POLYGON ((105 -9, 115 -9, 115 -5, 105 -5, 105 -9))')),"
                "(2,st_circle(0,0,1000))",
            ),
        ),
        Case(
            "geo.raw_point_transport",
            "geospatial",
            "SELECT st_point(0,0)",
            [[{"hex": origin_bytes.hex()}]],
            expected_column_types=(253,),
        ),
        Case(
            "geo.binary_parameter_roundtrip",
            "geospatial",
            "SELECT st_astext(unhex(%s)),st_x(unhex(%s)),st_y(unhex(%s))",
            [["POINT (0 0)", 0.0, 0.0]],
            parameters=(origin_bytes.hex(),) * 3,
            expected_column_types=(253, 5, 5),
        ),
        Case(
            "geo.column_invalid_and_null",
            "geospatial",
            "SELECT n,st_astext(st_geomfromtext(CASE n WHEN 0 THEN 'POINT (0 0)' "
            "WHEN 1 THEN 'POINT (181 0)' WHEN 2 THEN 'POINT (0 91)' "
            "WHEN 3 THEN '' WHEN 4 THEN 'bad WKT' ELSE NULL END)) FROM numbers ORDER BY n",
            [[0, "POINT (0 0)"], [1, None], [2, None], [3, None], [4, None], [5, None]],
        ),
        Case(
            "geo.column_polygon_hole",
            "geospatial",
            f"SELECT n,st_contains(st_polygon({quote(HOLED_POLYGON)}),"
            "st_point(n,n)) FROM numbers WHERE n IN (2,5) ORDER BY n",
            [[2, 1], [5, 0]],
        ),
        Case(
            "geo.line_duplicate_vertices",
            "geospatial",
            "SELECT st_astext(st_linefromtext('LINESTRING (0 0, 0 0, 1 1)'))",
            [["LINESTRING (0 0, 1 1)"]],
        ),
        Case(
            "geo.unsupported_wkt_shapes",
            "geospatial",
            "SELECT st_geomfromtext('POINT EMPTY'),st_geomfromtext('POINT Z (0 0 1)'),"
            "st_geomfromtext('MULTIPOINT ((0 0),(1 1))'),"
            "st_geomfromtext('MULTIPOLYGON (((0 0,1 0,1 1,0 0)))'),"
            "st_geomfromtext('GEOMETRYCOLLECTION (POINT (0 0))')",
            [[None] * 5],
            contract_source=SOURCE.replace("exprs/geo_functions.cpp", "geo/wkt_yacc.y"),
        ),
        Case(
            "geo.empty_table_metadata",
            "geospatial",
            "SELECT st_x(geometry),st_astext(geometry),st_contains(geometry,geometry) "
            "FROM geo_locations WHERE FALSE",
            [],
            expected_column_types=(5, 253, 1),
        ),
    ]
    return [replace(case, contract_source=case.contract_source or SOURCE) for case in cases]
