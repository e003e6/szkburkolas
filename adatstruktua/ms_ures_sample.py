"""
Microsoft Open Buildings adatrések detektálása Magyarországon — raszter-szintű
csúsztatott-ablak módszerrel.

Probléma: a Microsoft dataset chunk-jain BELÜL vannak nagy, várost méretű foltok
ahol az ML-pipeline nem dolgozott fel sub-tile-okat. A Microsoft erről nem ad
metaadatot. Detektálás: ahol OSM-ben jelen vannak épületek de MS-ben SEMMI sincs
egy lokális környezetben → adatrés.

Módszer (z=14 raszter + 3×3 csúsztatott ablak):
  1. Minden OSM és MS épület-középpontot z=14 web-mercator cellába sorolunk
     (~1.2–1.5 km cella Magyarország szélességén).
  2. Két sűrű 2D számláló rácsot építünk: `osm_grid` és `ms_grid`.
  3. Mindkét rácson (2r+1)×(2r+1) csúsztatott ablak-összeget veszünk → `osm_local`,
     `ms_local`. r=1 mellett az ablak ~3.6–4.5 km.
  4. Gap maszk: `osm_local >= OSM_LAKOTT_KUSZOB ÉS ms_local == 0`.
     Az `ms_local == 0` feltétel a kulcs: az adott cella *környezetében* sincs
     MS épület, nem csak a cellában — így a gap-régió határán fekvő apró falvak
     is bekerülnek (a régi cella-szintű ms_n == 0 ezeket kihagyta, mert a 10 km-es
     cella másik részén volt egy-két MS épület).
  5. Magyarország-határ szűrés cella-középpont alapján.
  6. 8-szomszédság komponens-címkézés (scipy.ndimage.label) → klaszterek.

Nincs minimum-klaszter méretszűrés: minden gap-cella bekerül a kimenetbe.
"""

import os
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import box
from shapely.ops import unary_union
from scipy.ndimage import label  # 8-szomszédság komponens-címkézés


# --- Bemenetek / kimenet ---------------------------------------------------
MS_PARQUET  = "../data/work_data/ms_minden_epulet.parquet"
OSM_PARQUET = "../data/work_data/osm_minden_epulet.parquet"
HU_BORDER   = "../data/work_data/hungary_border.geojson"
OUT_GPKG    = "../data/test_data/ms_adatresek.gpkg"

# --- Hangolható konstansok -------------------------------------------------
RACS_ZOOM         = 14       # web-mercator zoom (~1.2–1.5 km cella)
ABLAK_SUGAR       = 1        # 3×3 csúsztatott ablak (2r+1 cella oldal); r=2 → 5×5
OSM_LAKOTT_KUSZOB = 3        # min OSM épület az ablakban (nem cellában)
METRIKUS_CRS_HU   = 23700    # EOV — területszámításhoz km²-ben


# ---------------------------------------------------------------------------
# Helper-ek — tile-matematika és sliding-window összeg
# ---------------------------------------------------------------------------
def _lonlat_to_tile(lon, lat, z=RACS_ZOOM):
    """Vektorizált slippy-map tile-XY."""
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    n = 2.0 ** z
    xtile = np.floor((lon + 180.0) / 360.0 * n).astype(np.int64)
    ytile = np.floor((1.0 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2.0 * n).astype(np.int64)
    xtile = np.clip(xtile, 0, int(n) - 1)
    ytile = np.clip(ytile, 0, int(n) - 1)
    return xtile, ytile


def _tile_to_bounds(xtile, ytile, z=RACS_ZOOM):
    """Egy tile bbox-a EPSG:4326-ban (lon_min, lat_min, lon_max, lat_max)."""
    n = 2.0 ** z
    lon_min = xtile / n * 360.0 - 180.0
    lon_max = (xtile + 1) / n * 360.0 - 180.0
    lat_max = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * ytile / n))))
    lat_min = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (ytile + 1) / n))))
    return float(lon_min), float(lat_min), float(lon_max), float(lat_max)


def _tile_centers(xtiles, ytiles, z=RACS_ZOOM):
    """Vektorizált cella-középpontok EPSG:4326-ban (lon_c, lat_c)."""
    xtiles = np.asarray(xtiles, dtype=np.float64)
    ytiles = np.asarray(ytiles, dtype=np.float64)
    n = 2.0 ** z
    lon_c = (xtiles + 0.5) / n * 360.0 - 180.0
    lat_c = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (ytiles + 0.5) / n))))
    return lon_c, lat_c


def _box_sum(grid, r):
    """
    (2r+1)×(2r+1) csúsztatott ablak-összeg integrált-kép (cumsum) trükkel.
    Nulla-padding a szélén → ugyanaz a shape mint `grid`.
    Tisztán numpy, O(H·W).
    """
    h, w = grid.shape
    cum = np.zeros((h + 1, w + 1), dtype=np.int64)
    cum[1:, 1:] = grid.astype(np.int64).cumsum(0).cumsum(1)

    i = np.arange(h)
    j = np.arange(w)
    i0 = np.maximum(i - r, 0)
    i1 = np.minimum(i + r + 1, h)
    j0 = np.maximum(j - r, 0)
    j1 = np.minimum(j + r + 1, w)

    return (cum[i1[:, None], j1[None, :]]
            - cum[i0[:, None], j1[None, :]]
            - cum[i1[:, None], j0[None, :]]
            + cum[i0[:, None], j0[None, :]])


# ---------------------------------------------------------------------------
# Fő pipeline
# ---------------------------------------------------------------------------
def ms_ures_sample():
    # 1) BEOLVASÁS
    print("MS és OSM középpontok beolvasása...")
    ms = gpd.read_parquet(MS_PARQUET)
    # ms_building_feldolgozas.py már EPSG:4326-os centroidot ír a 'kozeppont' oszlopba;
    # gpd.read_parquet ezt nem aktív geometriaként hozza vissza -> explicit GeoSeries.
    ms_pts = gpd.GeoSeries(ms["kozeppont"], crs=4326)

    osm = gpd.read_parquet(OSM_PARQUET)
    if osm.crs is None:
        osm = osm.set_crs(4326)
    elif osm.crs.to_epsg() != 4326:
        osm = osm.to_crs(4326)
    osm_pts = osm.geometry.centroid

    border = gpd.read_file(HU_BORDER).to_crs(4326)
    border_geom = unary_union(border.geometry.values)

    print(f"  MS pontok:  {len(ms_pts):,}")
    print(f"  OSM pontok: {len(osm_pts):,}")

    # 2) TILE XY mindkét pontfelhőre
    ms_x,  ms_y  = _lonlat_to_tile(ms_pts.x.to_numpy(),  ms_pts.y.to_numpy())
    osm_x, osm_y = _lonlat_to_tile(osm_pts.x.to_numpy(), osm_pts.y.to_numpy())

    # 3) MAGYARORSZÁG BBOX TILE-KOORDINÁTÁKBAN — sűrű rácshoz korlátozzuk
    lon_min, lat_min, lon_max, lat_max = border_geom.bounds
    # tile-y délről nőj: lat_max → kicsi y, lat_min → nagy y
    bx, by = _lonlat_to_tile(
        np.array([lon_min, lon_max]),
        np.array([lat_max, lat_min]),
    )
    x_min, x_max = int(bx.min()), int(bx.max())
    y_min, y_max = int(by.min()), int(by.max())
    # szegély-pad a sliding ablak miatt — a határszéli cellák ablaka ne csonkuljon
    pad = ABLAK_SUGAR
    x_min -= pad; x_max += pad
    y_min -= pad; y_max += pad
    W = x_max - x_min + 1
    H = y_max - y_min + 1
    print(f"  Rács bbox: {W} × {H} cella (z={RACS_ZOOM})")

    # 4) SŰRŰ 2D SZÁMLÁLÓ RÁCSOK — bincount a (y,x) lineáris indexen
    def _grid(xs, ys):
        mask = (xs >= x_min) & (xs <= x_max) & (ys >= y_min) & (ys <= y_max)
        xs2 = (xs[mask] - x_min).astype(np.int64)
        ys2 = (ys[mask] - y_min).astype(np.int64)
        flat = ys2 * W + xs2
        return np.bincount(flat, minlength=H * W).reshape(H, W)

    ms_grid  = _grid(ms_x,  ms_y)
    osm_grid = _grid(osm_x, osm_y)

    # 5) CSÚSZTATOTT ABLAK-ÖSSZEG
    osm_local = _box_sum(osm_grid, ABLAK_SUGAR)
    ms_local  = _box_sum(ms_grid,  ABLAK_SUGAR)

    # 6) GAP MASZK — a kulcs feltétel: ms_local == 0 (NEM cellaszintű ms_n == 0)
    gap_mask = (osm_local >= OSM_LAKOTT_KUSZOB) & (ms_local == 0)
    print(f"  Gap-jelölt cella (sliding window): {int(gap_mask.sum()):,}")

    # 7) MAGYARORSZÁG-HATÁR SZŰRÉS cella-középpont alapján
    ys_idx, xs_idx = np.indices((H, W))
    lon_c, lat_c = _tile_centers((xs_idx + x_min).ravel(), (ys_idx + y_min).ravel())
    pts = gpd.GeoSeries(gpd.points_from_xy(lon_c, lat_c), crs=4326)
    inside_mask = pts.within(border_geom).values.reshape(H, W)
    gap_mask &= inside_mask
    print(f"  Gap cella Magyarországon: {int(gap_mask.sum()):,}")

    # 8) 8-SZOMSZÉDSÁG KOMPONENS-CÍMKÉZÉS — minden komponens marad, méret-szűrés NINCS
    labels, n_comp = label(gap_mask, structure=np.ones((3, 3), dtype=int))
    print(f"  Komponensek: {n_comp}")

    # 9) CELLA-POLIGONOK ÉPÍTÉSE
    gy_idx = np.where(gap_mask)
    abs_x = gy_idx[1] + x_min
    abs_y = gy_idx[0] + y_min
    cell_boxes = [box(*_tile_to_bounds(int(abs_x[i]), int(abs_y[i])))
                  for i in range(len(abs_x))]

    gyanus_gdf = gpd.GeoDataFrame({
        "tile_x":     abs_x.astype(np.int64),
        "tile_y":     abs_y.astype(np.int64),
        "osm_n":      osm_grid[gy_idx].astype(np.int64),    # csak ez a cella
        "osm_window": osm_local[gy_idx].astype(np.int64),   # 3×3 ablakösszeg
        "cluster_id": labels[gy_idx].astype(np.int64),
    }, geometry=cell_boxes, crs=4326)

    # 10) KOMPONENSENKÉNTI DISSOLVE — minden komponens kimegy (nincs min-cella szűrő)
    rows = []
    for cid, sub in gyanus_gdf.groupby("cluster_id", sort=True):
        rows.append({
            "cluster_id":  int(cid),
            "cella_szam":  int(len(sub)),
            "osm_n_total": int(sub["osm_n"].sum()),
            "geometry":    unary_union(sub.geometry.values),
        })
    klaszterek = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)
    if len(klaszterek) > 0:
        klaszterek["terulet_km2"] = klaszterek.to_crs(METRIKUS_CRS_HU).area / 1_000_000.0
    else:
        klaszterek["terulet_km2"] = pd.Series(dtype="float64")
    klaszterek = klaszterek[["cluster_id", "cella_szam", "osm_n_total", "terulet_km2", "geometry"]]

    # 11) EXPORT — két layer egy gpkg-ban
    os.makedirs(os.path.dirname(OUT_GPKG), exist_ok=True)
    # GPKG layer-szinten append-elne újraíráskor; régi fájlt eldobjuk
    if os.path.exists(OUT_GPKG):
        os.remove(OUT_GPKG)
    klaszterek.to_file(OUT_GPKG, driver="GPKG", layer="klaszterek")
    gyanus_gdf.to_file(OUT_GPKG, driver="GPKG", layer="gyanus_cellak")

    print(f"Kész — {OUT_GPKG}")
    print(f"  klaszterek:    {len(klaszterek)}")
    print(f"  gyanus_cellak: {len(gyanus_gdf):,}")


if __name__ == "__main__":
    ms_ures_sample()
