"""
Pre-process the AOI shapefile into shared-border corridors for pairwise
border-zone products (e.g. Dynamic World land-use histogram along the
border between two touching polygons).

Runs on the raw shapefile rather than the run's aoi_prepped.parquet, since
that file may be simplified to a tolerance driven by other products in the
same run (e.g. MODIS 500m) — border geometry needs its own tolerance,
independent of whatever else is selected, rather than reusing aoi_prepped's.

Pipeline:
  1. Load + normalise CRS + assign region_id (same conventions as
     preprocess_aoi.py).
  2. Reproject to an equal-area metric CRS.
  3. Find candidate touching pairs via a tolerance-buffered spatial join —
     real shapefiles rarely share exact vertices, so strict touches() misses
     genuine neighbours separated by small digitization gaps.
  4. For each candidate pair, snap one geometry onto the other within the
     same tolerance and intersect boundaries to recover the true shared
     line. Snapping is done on geometry simplified to SNAP_SIMPLIFY_TOLERANCE_M
     — full-resolution admin polygons (some GAUL coastlines run to hundreds
     of thousands of vertices) make snap() pathologically slow, and the
     pipeline's own precision floor is already 10m (corridor width, min
     border length, Dynamic World's pixel size), so sub-metre coastline
     detail buys nothing downstream.
  5. Discard pairs whose shared line is too short to be a real edge (vs. a
     corner touch).
  6. Buffer the shared line into a sampling corridor and write one row per
     pair keyed by a synthetic region_id, so the rest of the pipeline
     (worker_geojson.py, geojson_to_parquet.py, merge_parquet.py) can treat
     each border pair exactly like any other region.
"""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import geopandas as gpd
from shapely.ops import transform, snap

try:
    LOG_FILE = snakemake.log[0] if snakemake.log else "preprocess_borders.log"
except NameError:
    LOG_FILE = "preprocess_borders.log"


def log_progress(message):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True) if os.path.dirname(LOG_FILE) else None
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat()}] {message}\n")
        f.flush()


# Equal-area projection so metre tolerances are physically meaningful
# regardless of latitude (matches preprocess_aoi.py).
_METRIC_CRS = 'EPSG:6933'

# How close two polygons must be (in metres) to be treated as touching.
# Closes small digitization gaps/slivers common in real-world shapefiles.
ADJACENCY_TOLERANCE_M = 2.0

# Shared borders shorter than this (metres) are corner touches, not real
# edges — too short to hold a stable weekly histogram at 10m resolution.
MIN_BORDER_LENGTH_M = 10.0

# Buffer distance applied to each side of the shared border line. With
# Dynamic World's 10m pixels, 10m each side gives a ~20m-wide corridor —
# roughly 1 pixel of coverage on either side of the boundary.
CORRIDOR_HALF_WIDTH_M = 10.0

# Tolerance used to simplify geometry before snap()/intersection() during
# adjacency resolution. snap() cost scales with vertex count on both sides,
# and full-resolution coastlines (some GAUL provinces run six figures) make
# it pathologically slow — one Kalimantan pair took 9+ minutes at native
# resolution vs ~10s at this tolerance, with the resulting shared-border
# length changing by <0.01%. Matches the pipeline's own 10m precision floor
# (CORRIDOR_HALF_WIDTH_M, MIN_BORDER_LENGTH_M, Dynamic World's pixel size),
# so it costs no real accuracy downstream.
SNAP_SIMPLIFY_TOLERANCE_M = 10.0


shp_path  = snakemake.input.shp
out_path  = snakemake.output.aoi
id_column = (getattr(snakemake.params, "id_column", None) or "").strip() or None

log_progress(f"Loading AOI from {shp_path}")
if Path(shp_path).suffix.lower() in {".parquet", ".geoparquet"}:
    gdf = gpd.read_parquet(shp_path)
else:
    gdf = gpd.read_file(shp_path)
log_progress(f"Loaded {len(gdf)} features")

# Normalise CRS to EPSG:4326
if gdf.crs is None:
    gdf = gdf.set_crs("EPSG:4326")
else:
    gdf = gdf.to_crs("EPSG:4326")

# Assign and deduplicate region_id (mirrors preprocess_aoi.py).
if 'region_id' not in gdf.columns:
    if id_column and id_column in gdf.columns:
        region_col = id_column
        log_progress(f"Using user-specified ID column: {region_col!r}")
    else:
        if id_column:
            log_progress(f"WARNING: Specified ID column {id_column!r} not found in file; falling back to auto-detection")
        id_candidates = ['ADMIN', 'NAME', 'ISO_A3', 'NAME_LONG', 'id', 'fid']
        region_col = next((c for c in id_candidates if c in gdf.columns), None)
        if region_col:
            log_progress(f"Auto-detected ID column: {region_col!r}")
        else:
            log_progress("No ID column found; using row index as region_id")
    gdf['region_id'] = gdf[region_col].astype(str) if region_col else gdf.index.astype(str)

if gdf['region_id'].duplicated().any():
    counts = {}
    new_ids = []
    for rid in gdf['region_id']:
        if rid in counts:
            counts[rid] += 1
            new_ids.append(f"{rid}_{counts[rid]}")
        else:
            counts[rid] = 0
            new_ids.append(rid)
    gdf['region_id'] = new_ids

# Repair topology before any adjacency testing.
gdf['geometry'] = gdf.geometry.buffer(0)
gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()].reset_index(drop=True)

gdf_metric = gdf.to_crs(_METRIC_CRS)
log_progress(f"Reprojected to {_METRIC_CRS} for adjacency testing")

# Candidate touching pairs: buffer by the adjacency tolerance and self-join.
buffered = gdf_metric.copy()
buffered['geometry'] = gdf_metric.geometry.buffer(ADJACENCY_TOLERANCE_M)
joined = gpd.sjoin(
    buffered[['region_id', 'geometry']],
    buffered[['region_id', 'geometry']],
    predicate='intersects',
    how='inner',
)
joined = joined[joined['region_id_left'] < joined['region_id_right']]
log_progress(f"Found {len(joined)} candidate touching pair(s) within {ADJACENCY_TOLERANCE_M}m tolerance")

# Simplified purely for the snap()/intersection() step below — candidate
# pair-finding above already ran on full-resolution geometry, since that
# step is cheap and touches the adjacency threshold directly.
simplified_geom_by_id = dict(zip(
    gdf_metric['region_id'],
    gdf_metric.geometry.simplify(SNAP_SIMPLIFY_TOLERANCE_M, preserve_topology=True),
))


def _process_pair(id_a, id_b):
    """Snap geom_a onto geom_b within the adjacency tolerance to close small
    gaps, then intersect boundaries to recover the true shared line.

    Runs in a worker thread: GEOS releases the GIL during snap()/
    intersection()/buffer(), so pairs genuinely run concurrently across
    cores instead of serialising behind Python's GIL.
    """
    geom_a, geom_b = simplified_geom_by_id[id_a], simplified_geom_by_id[id_b]
    snapped_a = snap(geom_a, geom_b, ADJACENCY_TOLERANCE_M)
    shared_line = snapped_a.boundary.intersection(geom_b.boundary)

    if shared_line.is_empty or shared_line.length < MIN_BORDER_LENGTH_M:
        return None

    corridor = shared_line.buffer(CORRIDOR_HALF_WIDTH_M)
    if corridor.is_empty:
        return None

    return {
        'region_id': f"{id_a}__{id_b}",
        'region_id_a': id_a,
        'region_id_b': id_b,
        'shared_border_length_m': shared_line.length,
        'geometry': corridor,
    }


pairs = list(zip(joined['region_id_left'], joined['region_id_right']))
rows = []
_PROGRESS_EVERY = 25
_MAX_WORKERS = os.cpu_count() or 4
with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
    futures = [executor.submit(_process_pair, id_a, id_b) for id_a, id_b in pairs]
    for i, future in enumerate(as_completed(futures), start=1):
        if i % _PROGRESS_EVERY == 0:
            log_progress(f"  processed {i}/{len(futures)} candidate pair(s)")
        result = future.result()
        if result is not None:
            rows.append(result)

log_progress(f"Kept {len(rows)} pair(s) after minimum-border-length filter ({MIN_BORDER_LENGTH_M}m)")

borders = gpd.GeoDataFrame(rows, geometry='geometry', crs=_METRIC_CRS)

# Reproject back to 4326 — everything downstream (GEE upload, chunk workers)
# expects WGS84 geometries.
borders = borders.to_crs("EPSG:4326")

# Repair any self-intersections introduced by buffer()+to_crs().
borders['geometry'] = borders.geometry.buffer(0)
borders = borders[~borders.geometry.is_empty & borders.geometry.notna()]

# Strip Z coordinates — ee.Geometry() only accepts 2D (lon, lat) pairs.
if len(borders) and borders.geometry.has_z.any():
    borders['geometry'] = borders.geometry.apply(
        lambda g: transform(lambda x, y, z=None: (x, y), g) if g.has_z else g
    )

os.makedirs(os.path.dirname(out_path), exist_ok=True)
borders.to_parquet(out_path)
log_progress(f"Written prepped border corridors: {out_path} ({len(borders)} pairs)")
