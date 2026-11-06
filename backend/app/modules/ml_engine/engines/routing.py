"""Stop ordering for delivery routes: nearest neighbour, improved by 2-opt.

Each group (a driver, a day, or one of ``clusters`` delivery areas that k-means
forms from the stops' locations) is one open route that starts at the depot when
one is given, otherwise at its first listed stop. Distances are
great-circle kilometres; road networks are not modelled, so a result is a
visiting order and a relative saving, not a drive time.
"""

from __future__ import annotations

import math

import httpx
import numpy as np
import pandas as pd
import pyarrow as pa

from app.core.config import settings
from app.modules.ml_engine.engines.base import TrainingOutput
from app.modules.ml_engine.spec import (
    DataBudgetExceeded,
    InsufficientTrainingRows,
    InvalidMLSpec,
    MLExecutionSpec,
)

#: Stops per route. 2-opt is quadratic per pass; larger routes need a real solver.
MAX_STOPS_PER_ROUTE = 400
#: Improvement passes per route.
MAX_PASSES = 50
#: Stops one OSRM table request may carry (the public server's limit).
MAX_TABLE_POINTS = 100
OSRM_TIMEOUT_SECONDS = 10.0
EARTH_RADIUS_KM = 6371.0088


def optimize_routes(table: pa.Table, spec: MLExecutionSpec) -> TrainingOutput:
    latitude = str(spec.parameters.get("latitude_column") or "")
    longitude = str(spec.parameters.get("longitude_column") or "")
    stop = spec.row_identifier
    group = spec.series_column
    for column in (latitude, longitude, stop, group):
        if column and column not in table.column_names:
            raise InvalidMLSpec(f"Route column '{column}' is not present")
    if not latitude or not longitude:
        raise InvalidMLSpec("Route optimization needs latitude and longitude columns")
    frame = table.to_pandas()
    frame["_lat"] = pd.to_numeric(frame[latitude], errors="coerce")
    frame["_lon"] = pd.to_numeric(frame[longitude], errors="coerce")
    valid = frame["_lat"].between(-90, 90) & frame["_lon"].between(-180, 180)
    invalid = int((~valid).sum())
    frame = frame[valid]
    frame["_stop"] = frame[stop].astype(str) if stop else frame.index.astype(str)
    demand = spec.parameters.get("demand_column") or spec.target_column
    if demand and demand not in frame:
        raise InvalidMLSpec(f"Demand column '{demand}' is not present")
    frame["_demand"] = pd.to_numeric(frame[demand], errors="coerce").fillna(0) if demand else 0.0
    frame["_group"] = frame[group].astype(str) if group else "all"
    # A result grouped by day lists a stop once per day; a route visits it once,
    # carrying the stop's whole demand.
    # A stop is its name at its place: two outlets that share a name are two stops.
    before = len(frame)
    frame["_place"] = frame["_lat"].round(5).astype(str) + "," + frame["_lon"].round(5).astype(str)
    frame = frame.groupby(["_group", "_stop", "_place"], as_index=False, sort=False).agg(
        _lat=("_lat", "first"), _lon=("_lon", "first"), _demand=("_demand", "sum"),
    )
    collapsed = before - len(frame)
    places = frame.groupby("_stop")["_place"].transform("nunique")
    rank = frame.groupby("_stop")["_place"].rank(method="dense").astype(int)
    frame["_stop"] = [
        f"{name} ({int(position)})" if count > 1 else name
        for name, count, position in zip(frame["_stop"], places, rank, strict=True)
    ]
    if frame.empty:
        raise InsufficientTrainingRows("No stop has valid coordinates")
    clusters = int(spec.parameters.get("clusters") or 0)
    notes: list[str] = []
    # Grouping by the stop itself (each outlet its own "route") orders nothing, even
    # when two outlets share a name: the stops are grouped into delivery areas by
    # location instead.
    if group and len(frame) > 1 and (
        group == stop or frame.groupby("_group").size().mean() < 1.5
    ):
        notes.append(f"'{group}' names each stop, so stops were grouped into areas instead")
        frame["_group"] = "all"
        group = None
        clusters = clusters or _auto_clusters(frame["_stop"].nunique())
    if clusters:
        frame = _delivery_areas(frame, clusters)
        group = "area"
    depot = _depot(spec.parameters)
    results = []
    sources: list[str] = []
    totals = {"optimized_km": 0.0, "original_order_km": 0.0}
    for name, stops in frame.groupby("_group", sort=True):
        if len(stops) > MAX_STOPS_PER_ROUTE:
            raise DataBudgetExceeded(
                f"Route '{name}' has {len(stops)} stops; at most {MAX_STOPS_PER_ROUTE} per route"
            )
        points = stops[["_lat", "_lon"]].to_numpy(dtype=float)
        if depot is not None:
            points = np.vstack([depot, points])
        road = _road_matrix(points)
        sources.append("road" if road is not None else "great_circle")
        # Road distances differ by direction; the order is found on their average
        # (2-opt reverses segments), and every length is measured as driven.
        distances = road if road is not None else _distance_matrix(points)
        symmetric = (distances + distances.T) / 2
        given = list(range(len(points)))
        order = _two_opt(_nearest_neighbour(symmetric), symmetric)
        labels = (["Depot"] if depot is not None else []) + stops["_stop"].tolist()
        optimized_km = _length(order, distances)
        given_km = _length(given, distances)
        totals["optimized_km"] += optimized_km
        totals["original_order_km"] += given_km
        # One row per stop in visit order: a map joins them into the route, and each
        # row repeats its route's totals so a panel can list routes from any stop.
        demands = [None] * (1 if depot is not None else 0) + stops["_demand"].tolist()
        route_name = "Route" if group is None else str(name)
        saving = round(100 * (given_km - optimized_km) / given_km, 1) if given_km else 0.0
        travelled = 0.0
        for position, index in enumerate(order):
            leg = float(distances[order[position - 1], index]) if position else 0.0
            travelled += leg
            results.append({
                "route": route_name,
                "stop_seq": position + 1,
                "stop": labels[index],
                "latitude": round(float(points[index][0]), 6),
                "longitude": round(float(points[index][1]), 6),
                **({"demand": demands[index]} if demand else {}),
                "leg_km": round(leg, 2),
                "cumulative_km": round(travelled, 2),
                "route_stops": len(stops),
                "route_km": round(optimized_km, 2),
                "original_order_km": round(given_km, 2),
                "saving_pct": saving,
            })
    warnings = list(notes)
    if "great_circle" in sources and settings.ROUTING_OSRM_URL:
        warnings.append(
            f"Road distances were unavailable for {sources.count('great_circle')} route(s); "
            "those use straight-line distances"
        )
    if invalid:
        warnings.append(f"Skipped {invalid} row(s) without valid coordinates")
    if collapsed:
        warnings.append(f"Counted {collapsed} repeated stop row(s) once")
    metrics = {
        "selected_estimator": (
            f"kmeans(k={clusters})+nearest_neighbour+2opt" if clusters
            else "nearest_neighbour+2opt"
        ),
        "distance": (
            "road_km_osrm" if sources and set(sources) == {"road"}
            else "great_circle_km" if set(sources) == {"great_circle"} else "mixed"
        ),
        "route_count": int(frame["_group"].nunique()),
        "stop_count": int(len(frame)),
        "optimized_km": round(totals["optimized_km"], 2),
        "original_order_km": round(totals["original_order_km"], 2),
        "warnings": warnings,
    }
    return TrainingOutput(
        bundle={"task": "route_optimization"},
        engine="nova_routing",
        algorithm=(
            f"kmeans(k={clusters})+nearest_neighbour+2opt" if clusters
            else "nearest_neighbour+2opt"
        ),
        metrics=metrics,
        feature_columns=[latitude, longitude],
        training_rows=int(len(frame)),
        results=results,
    )


def _auto_clusters(stops: int) -> int:
    """Areas for stops with no stated grouping: about two dozen stops a route."""
    if stops < 4:
        return 0
    return max(2, min(20, round(math.sqrt(stops / 2)), stops // 2))


def _delivery_areas(frame: pd.DataFrame, clusters: int) -> pd.DataFrame:
    """Group stops into ``clusters`` areas by location (k-means on a local plane)."""
    from sklearn.cluster import KMeans

    from app.core.config import settings

    stops = frame.drop_duplicates(["_stop", "_place"]).copy()
    if not 2 <= clusters <= min(20, len(stops)):
        raise InvalidMLSpec(f"clusters must be between 2 and {min(20, len(stops))} for these stops")
    # Degrees of longitude shrink with latitude; scale them so areas are compact.
    scale = math.cos(math.radians(float(stops["_lat"].mean())))
    points = np.column_stack([stops["_lat"], stops["_lon"] * scale])
    labels = KMeans(n_clusters=clusters, random_state=settings.ML_RANDOM_SEED, n_init=10).fit(
        points
    ).labels_
    # Areas are numbered west to east so the same stops get the same names.
    centres = {label: float(stops["_lon"][labels == label].mean()) for label in set(labels)}
    west_to_east = sorted(centres, key=lambda label: centres[label])
    rank = {label: index + 1 for index, label in enumerate(west_to_east)}
    stops["_group"] = [f"Area {rank[label]}" for label in labels]
    return stops


def _depot(parameters: dict) -> np.ndarray | None:
    lat, lon = parameters.get("start_latitude"), parameters.get("start_longitude")
    if lat is None or lon is None:
        return None
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or math.isnan(lat + lon):
        raise InvalidMLSpec("start_latitude/start_longitude are not valid coordinates")
    return np.array([lat, lon])


def _road_matrix(points: np.ndarray) -> np.ndarray | None:
    """Driving distances (km) between every pair of stops, from OSRM's table service.

    None when no server is configured, the route is too long for one request, or the
    server does not answer with a complete matrix.
    """
    base = settings.ROUTING_OSRM_URL.rstrip("/")
    if not base or not 2 <= len(points) <= MAX_TABLE_POINTS:
        return None
    from app.common.ssrf_guard import resolve_and_validate_url

    path = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    try:
        resolve_and_validate_url(base, allow_private_network=True)
        response = httpx.get(
            f"{base}/table/v1/driving/{path}", params={"annotations": "distance"},
            timeout=OSRM_TIMEOUT_SECONDS,
        )
        body = response.json()
        if response.status_code != 200 or body.get("code") != "Ok":
            return None
        matrix = np.array(body["distances"], dtype=float) / 1000
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    if matrix.shape != (len(points), len(points)) or not np.isfinite(matrix).all():
        return None
    return matrix


def _distance_matrix(points: np.ndarray) -> np.ndarray:
    lat = np.radians(points[:, 0])[:, None]
    lon = np.radians(points[:, 1])[:, None]
    dlat = lat - lat.T
    dlon = lon - lon.T
    a = np.sin(dlat / 2) ** 2 + np.cos(lat) * np.cos(lat.T) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _nearest_neighbour(distances: np.ndarray) -> list[int]:
    remaining = set(range(1, len(distances)))
    order = [0]
    while remaining:
        last = order[-1]
        nearest = min(remaining, key=lambda index: distances[last, index])
        order.append(nearest)
        remaining.remove(nearest)
    return order


def _two_opt(order: list[int], distances: np.ndarray) -> list[int]:
    """Reverse segments while that shortens the open path; the start stays first."""
    best = list(order)
    for _ in range(MAX_PASSES):
        improved = False
        for i in range(1, len(best) - 1):
            for j in range(i + 1, len(best)):
                a, b = best[i - 1], best[i]
                c = best[j]
                d = best[j + 1] if j + 1 < len(best) else None
                before = distances[a, b] + (distances[c, d] if d is not None else 0.0)
                after = distances[a, c] + (distances[b, d] if d is not None else 0.0)
                if after < before - 1e-9:
                    best[i:j + 1] = reversed(best[i:j + 1])
                    improved = True
        if not improved:
            break
    return best


def _length(order: list[int], distances: np.ndarray) -> float:
    return float(sum(distances[a, b] for a, b in zip(order, order[1:], strict=False)))
