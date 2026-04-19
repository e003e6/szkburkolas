import pandas as pd
import geopandas as gpd
import colorsys

from shapely.ops import unary_union, split, polygonize
from shapely import make_valid
from shapely.geometry import LineString, MultiPolygon


def _distinct_hex_colors(n, s=0.62, v1=0.92, v2=0.78):
    """
    n db jól elkülönülő szín.
    Hue: golden ratio léptetés; V: váltogatva (v1/v2), hogy nagy n-nél is szétváljanak.
    """
    if n <= 0:
        return []
    golden = 0.618033988749895
    h = 0.0
    out = []
    for i in range(n):
        h = (h + golden) % 1.0
        v = v1 if (i % 2 == 0) else v2
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        out.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return out


def add_color_to_gdf(gdf):
    ids = list(gdf["szavazokorid"].unique())
    n = len(ids)
    print('Szavazókörök száma', n)
    palette = _distinct_hex_colors(n, s=0.60, v1=0.92, v2=0.80)
    color_map = dict(zip(ids, palette))
    gdf = gdf.copy()
    gdf["color"] = gdf["szavazokorid"].map(color_map)
    return gdf


def pontok_polygonban(gdf, gdf_szigetek, max_depth=25):
    '''
    Végigmegy minden poligonon, megkeresi a pontokat, és:
      - ha több szavazókör van egy poligonon belül -> rekurzív felezés
      - ha egyetlen szavazókör van -> results sorba menti
    '''

    if gdf.crs is None or gdf_szigetek.crs is None:
        raise ValueError("Mindkét GeoDataFrame-nek kell legyen CRS-e")

    if gdf.crs != gdf_szigetek.crs:
        gdf = gdf.to_crs(gdf_szigetek.crs)

    rows = []

    for poly_idx, poly_row in gdf_szigetek.iterrows():
        polygon_geom = poly_row.geometry

        points_inside = gdf[gdf.within(polygon_geom)].copy()

        if len(points_inside) == 0:
            rows.append({"szavazokorid": None, "color": None, "geometry": polygon_geom})
            continue

        unique_szavazokorok = points_inside["szavazokorid"].dropna().unique()

        if len(unique_szavazokorok) != 1:
            rows.extend(polygon_tobb_szavazokor(polygon_geom, points_inside, max_depth=max_depth))
            continue

        rows.append({
            "szavazokorid": unique_szavazokorok[0],
            "color": points_inside.iloc[0]["color"],
            "geometry": polygon_geom
        })

    return gpd.GeoDataFrame(rows, geometry="geometry", crs=gdf_szigetek.crs)


def felez(poly):
    """
    A poligont kettévágja a centroidon átmenő vágással (a hosszabb bbox tengely mentén).
    """
    minx, miny, maxx, maxy = poly.bounds
    cx, cy = poly.centroid.x, poly.centroid.y

    if (maxx - minx) >= (maxy - miny):
        vago = LineString([(cx, miny - 1), (cx, maxy + 1)])
    else:
        vago = LineString([(minx - 1, cy), (maxx + 1, cy)])

    darabok = list(split(poly, vago).geoms)
    return darabok if len(darabok) >= 2 else [poly]


def polygon_tobb_szavazokor(polygon_geom, points_inside, max_depth=25):
    '''
    Több szavazókörös poligon szétbontása felezéssel (stack-alapú DFS).

    Működés:
    - Minden körben: poligon felezése a centroidon átmenő vágással (hosszabb tengely)
    - A felekre újraszűrjük a pontokat:
        - 0 pont -> üres poligon, nem bontjuk tovább
        - 1 szavazokorid -> kész
        - több szavazokorid -> vissza a stackbe
    '''

    rows = []
    stack = [(polygon_geom, points_inside, 0)]

    while stack:
        poly, pts, depth = stack.pop()

        if depth >= max_depth:
            print('Elérte a poly a mélységi szintet!')
            continue

        for darab in felez(poly):
            darab_pts = pts[pts.within(darab)].copy()

            if len(darab_pts) == 0:
                rows.append({"szavazokorid": None, "color": None, "geometry": darab})
                continue

            uniq = darab_pts["szavazokorid"].dropna().unique()

            if len(uniq) == 1:
                rows.append({
                    "szavazokorid": uniq[0],
                    "color": darab_pts.iloc[0]["color"],
                    "geometry": darab
                })
                continue

            stack.append((darab, darab_pts, depth + 1))

    return rows


def ures_polyk_besorolasa(results):
    """
    Azokat a sorokat kezeli, ahol szavazokorid hiányzik (NaN/None):
      - megkeresi a szomszédos poligonokat (touches: közös határ/pont érintés)
      - a szomszédok szavazokorid-jai közül a leggyakoribbat választja
    """

    out = results.copy()
    sindex = out.sindex

    missing_idxs = out.index[out["szavazokorid"].isna()].tolist()

    for idx in missing_idxs:
        geom = out.at[idx, "geometry"]

        # touches() sarokponton érintkező szomszédot is elfogad → figura-8 union → gap/overlap
        # Csak azok a szomszédok kellenek, amelyekkel közös ÉL van (hossz > 0)
        candidate_idxs = list(sindex.intersection(geom.bounds))
        candidates = out.loc[candidate_idxs]

        neighbors_labeled = candidates[
            candidates.geometry.apply(
                lambda g: g.boundary.intersection(geom.boundary).length > 0.01
            ) & candidates["szavazokorid"].notna()
        ]

        if len(neighbors_labeled) == 0:
            continue

        counts = neighbors_labeled["szavazokorid"].value_counts()
        winner_szavazokorid = counts.index[0]

        winner_color = neighbors_labeled.loc[
            neighbors_labeled["szavazokorid"] == winner_szavazokorid, "color"
        ].iloc[0]

        out.at[idx, "szavazokorid"] = winner_szavazokorid
        out.at[idx, "color"] = winner_color

    return out


def rebuild_coverage(gdf):
    '''
    Cella-halmazt tisztán újra-topológizál coverage-ként.

    Gyökér probléma: a felez() split sub-nanométeres FP-ULP drift-et hagy a
    splittelt cella és a nem-splittelt szomszéd közös élén (~1e-8 m² mikro-
    átfedés). Ez az unary_union kimenetén 0-szélességű slit-ként mutatkozik →
    QGIS-ben „lógó random vonalak".

    Megoldás: a cellák külső határvonalainak unary_union-ja NODING-ot csinál
    (minden kvázi-koincidens metszéspont BYTE-PONTOS csúccsá válik). Ebből a
    tiszta linework-ből polygonize új, coverage-szerű cellákat ad vissza.
    A szkid / color attribútumokat centroid-alapján öröklik az eredetiből.

    Megjegyzés: a rebuild utáni cellaszám változhat (összeolvadnak mikro-
    sliverek, új cellák keletkezhetnek a noding által létrehozott csúcsokon).
    Ez topológiailag helyes.
    '''
    if len(gdf) == 0:
        return gdf.copy()

    boundaries = unary_union([g.boundary for g in gdf.geometry if g is not None and not g.is_empty])
    new_cells = list(polygonize(boundaries))

    if not new_cells:
        return gdf.copy()

    sidx = gdf.sindex
    rows = []
    for cell in new_cells:
        rp = cell.representative_point()
        cand = list(sidx.intersection(rp.bounds))
        found = None
        for j in cand:
            if gdf.geometry.iloc[j].contains(rp):
                found = j
                break
        if found is None and cand:
            found = min(cand, key=lambda j: gdf.geometry.iloc[j].distance(rp))
        if found is None:
            continue
        rows.append({
            "szavazokorid": gdf["szavazokorid"].iloc[found],
            "color": gdf["color"].iloc[found] if "color" in gdf.columns else None,
            "geometry": cell,
        })
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=gdf.crs)


def polygonok_egyesitese(results, **_ignored):
    '''
    Szavazókörönkénti unió. rebuild_coverage után a bemenet tiszta coverage,
    így unary_union nem gyárt 0-szélességű slit-eket.
    '''

    out_rows = []

    for szkid, grp in results.groupby("szavazokorid", dropna=False):
        color = grp["color"].iloc[0] if "color" in grp.columns else None
        geom = unary_union(list(grp.geometry))
        geom = make_valid(geom)
        out_rows.append({"szavazokorid": szkid, "color": color, "geometry": geom})

    return gpd.GeoDataFrame(out_rows, geometry="geometry", crs=results.crs)
