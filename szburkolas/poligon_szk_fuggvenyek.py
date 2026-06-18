import pandas as pd
import geopandas as gpd
import colorsys
import numpy as np

from shapely.ops import unary_union, split, polygonize
from shapely import make_valid
from shapely.geometry import LineString, MultiPolygon
from sklearn.svm import LinearSVC


LINEAR_SPLIT_MIN_MARGIN = 1.2
LINEAR_SPLIT_MIN_POINTS_PER_CLASS = 2
LINEAR_SPLIT_MIN_ABS_MAJORITY = 2
LINEAR_SPLIT_MAX_ITER_SVC = 10000
LINEAR_SPLIT_RANDOM_STATE = 0


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


def pontok_polygonban(gdf, gdf_szigetek, max_depth=3, skip_felezes=False):
    '''
    Végigmegy minden poligonon, megkeresi a pontokat, és:
      - ha több szavazókör van egy poligonon belül -> rekurzív felezés
          (skip_felezes=True esetén: többségi szavazókör kap hozzárendelést, nem bontjuk)
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
            if skip_felezes:
                counts = points_inside["szavazokorid"].dropna().value_counts()
                winner = counts.index[0]
                winner_color = points_inside.loc[
                    points_inside["szavazokorid"] == winner, "color"
                ].iloc[0]
                rows.append({"szavazokorid": winner, "color": winner_color, "geometry": polygon_geom})
            else:
                rows.extend(polygon_szkid_linearis_vagas(polygon_geom, points_inside))
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


def _besorol_oldalra(points_inside, side_geom):
    if side_geom is None or side_geom.is_empty:
        return None, 0, 0, None
    inside = points_inside[points_inside.within(side_geom)]
    if len(inside) == 0:
        return None, 0, 0, None
    counts = inside["szavazokorid"].dropna().value_counts()
    if len(counts) == 0:
        return None, 0, 0, None
    majority_szkid = counts.index[0]
    majority_count = int(counts.iloc[0])
    second_count = int(counts.iloc[1]) if len(counts) > 1 else 0
    color = inside.loc[inside["szavazokorid"] == majority_szkid, "color"].iloc[0]
    return majority_szkid, majority_count, second_count, color


def _osszes_szkid_tobbsegre(polygon_geom, points_inside):
    counts = points_inside["szavazokorid"].dropna().value_counts()
    winner = counts.index[0]
    winner_color = points_inside.loc[points_inside["szavazokorid"] == winner, "color"].iloc[0]
    return [{"szavazokorid": winner, "color": winner_color, "geometry": polygon_geom}]


def polygon_szkid_linearis_vagas(polygon_geom, points_inside, min_margin=LINEAR_SPLIT_MIN_MARGIN):
    '''
    Vegyes-szkid poligon egyetlen lineáris vágása.

    A top-2 szkid pontjait LinearSVC szeparálja; a kapott egyenessel a poligont
    ketté vágjuk. Mindkét oldalon a többségi/kisebbségi szkid arányt a min_margin
    küszöbhöz mérjük. Ha bármelyik oldal nem felel meg → reject: a teljes
    poligont a globális többségi szkid-hez soroljuk.

    Visszatérés: list[dict] 1 vagy 2 elemmel (szavazokorid, color, geometry).
    '''

    counts = points_inside["szavazokorid"].dropna().value_counts()

    if len(counts) < 2:
        return _osszes_szkid_tobbsegre(polygon_geom, points_inside)

    szkid_A = counts.index[0]
    szkid_B = counts.index[1]

    if counts.iloc[0] < LINEAR_SPLIT_MIN_POINTS_PER_CLASS or counts.iloc[1] < LINEAR_SPLIT_MIN_POINTS_PER_CLASS:
        print(f"[linear_split] elutasítva (túl kevés pont a top-2 szkid valamelyikén: {counts.iloc[0]}/{counts.iloc[1]})")
        return _osszes_szkid_tobbsegre(polygon_geom, points_inside)

    train_mask = points_inside["szavazokorid"].isin([szkid_A, szkid_B])
    train_pts = points_inside.loc[train_mask]
    coords = np.array([(g.x, g.y) for g in train_pts.geometry], dtype=float)
    labels = (train_pts["szavazokorid"].values == szkid_B).astype(int)

    mean_x = float(coords[:, 0].mean())
    mean_y = float(coords[:, 1].mean())
    X_centered = coords - np.array([mean_x, mean_y])

    try:
        svc = LinearSVC(
            class_weight="balanced",
            max_iter=LINEAR_SPLIT_MAX_ITER_SVC,
            random_state=LINEAR_SPLIT_RANDOM_STATE,
            dual="auto",
        )
        svc.fit(X_centered, labels)
    except Exception as e:
        print(f"[linear_split] elutasítva (SVC hiba: {e})")
        return _osszes_szkid_tobbsegre(polygon_geom, points_inside)

    w = svc.coef_[0]
    b = float(svc.intercept_[0])
    w0, w1 = float(w[0]), float(w[1])

    if abs(w0) < 1e-12 and abs(w1) < 1e-12:
        print("[linear_split] elutasítva (degenerált szeparátor)")
        return _osszes_szkid_tobbsegre(polygon_geom, points_inside)

    minx, miny, maxx, maxy = polygon_geom.bounds
    diag = ((maxx - minx) ** 2 + (maxy - miny) ** 2) ** 0.5
    ext = max(diag * 10, 100.0)

    if abs(w1) >= abs(w0):
        x1c, x2c = -ext, ext
        y1c = -(w0 * x1c + b) / w1
        y2c = -(w0 * x2c + b) / w1
    else:
        y1c, y2c = -ext, ext
        x1c = -(w1 * y1c + b) / w0
        x2c = -(w1 * y2c + b) / w0

    p1 = (x1c + mean_x, y1c + mean_y)
    p2 = (x2c + mean_x, y2c + mean_y)
    vago = LineString([p1, p2])

    try:
        result = split(polygon_geom, vago)
        parts = [make_valid(g) for g in result.geoms if g.geom_type == "Polygon" and not g.is_empty]
    except Exception as e:
        print(f"[linear_split] elutasítva (split hiba: {e})")
        return _osszes_szkid_tobbsegre(polygon_geom, points_inside)

    if len(parts) < 2:
        print("[linear_split] elutasítva (vonal nem vágta ketté a poligont)")
        return _osszes_szkid_tobbsegre(polygon_geom, points_inside)

    pos_parts, neg_parts = [], []
    for g in parts:
        c = g.representative_point()
        sign_val = w0 * (c.x - mean_x) + w1 * (c.y - mean_y) + b
        (pos_parts if sign_val >= 0 else neg_parts).append(g)

    if not pos_parts or not neg_parts:
        print("[linear_split] elutasítva (minden darab azonos oldalra esett)")
        return _osszes_szkid_tobbsegre(polygon_geom, points_inside)

    side_pos = unary_union(pos_parts) if len(pos_parts) > 1 else pos_parts[0]
    side_neg = unary_union(neg_parts) if len(neg_parts) > 1 else neg_parts[0]

    maj_p, n_p, second_p, color_p = _besorol_oldalra(points_inside, side_pos)
    maj_n, n_n, second_n, color_n = _besorol_oldalra(points_inside, side_neg)

    def _oldal_elfogadhato(majority_count, second_count):
        if majority_count == 0:
            return True
        if majority_count < LINEAR_SPLIT_MIN_ABS_MAJORITY:
            return False
        if second_count == 0:
            return True
        return (majority_count / second_count) >= min_margin

    ok_pos = _oldal_elfogadhato(n_p, second_p)
    ok_neg = _oldal_elfogadhato(n_n, second_n)

    if not (ok_pos and ok_neg):
        ratio_p = (n_p / max(second_p, 1)) if n_p > 0 else 0
        ratio_n = (n_n / max(second_n, 1)) if n_n > 0 else 0
        print(f"[linear_split] elutasítva (margó nem elég: pos {n_p}/{second_p}={ratio_p:.2f}, neg {n_n}/{second_n}={ratio_n:.2f})")
        return _osszes_szkid_tobbsegre(polygon_geom, points_inside)

    ratio_p = (n_p / max(second_p, 1)) if n_p > 0 else float("inf")
    ratio_n = (n_n / max(second_n, 1)) if n_n > 0 else float("inf")
    print(f"[linear_split] elfogadva (pos {n_p}/{second_p}={ratio_p}, neg {n_n}/{second_n}={ratio_n})")

    return [
        {"szavazokorid": maj_p, "color": color_p, "geometry": side_pos},
        {"szavazokorid": maj_n, "color": color_n, "geometry": side_neg},
    ]


def ures_polyk_besorolasa(results):
    """
    Azokat a sorokat kezeli, ahol szavazokorid hiányzik (NaN/None):
      - megkeresi a közös éllel érintkező, már címkézett szomszédokat
      - abba a szomszédba olvasztja (annak szkid-jét örökli), amellyel a
        LEGHOSSZABB a közös határa
    Több menetben iterál — egymás mellett lévő üres poligonok láncát is feltölti,
    amíg egyik sem marad besorolatlan (vagy elszigetelt).
    """

    out = results.copy()
    sindex = out.sindex

    while True:
        missing_idxs = out.index[out["szavazokorid"].isna()].tolist()
        if not missing_idxs:
            break

        filled_this_round = 0
        for idx in missing_idxs:
            geom = out.at[idx, "geometry"]

            candidate_idxs = list(sindex.intersection(geom.bounds))
            candidates = out.loc[candidate_idxs]

            # Csak azok a szomszédok kellenek, amelyekkel közös ÉL van (hossz > 0),
            # a pontérintkezés (figura-8) nem szomszédság. A közös határ hosszát
            # is eltároljuk, hogy a leghosszabb élű szomszédot válasszuk.
            shared_len = candidates.geometry.apply(
                lambda g: g.boundary.intersection(geom.boundary).length
            )
            neighbors_labeled = candidates[
                (shared_len > 0.01) & candidates["szavazokorid"].notna()
            ]

            if len(neighbors_labeled) == 0:
                continue

            # A leghosszabb közös határú címkézett szomszéd nyer (nem gyakoriság).
            winner_idx = shared_len.loc[neighbors_labeled.index].idxmax()

            out.at[idx, "szavazokorid"] = out.at[winner_idx, "szavazokorid"]
            out.at[idx, "color"] = out.at[winner_idx, "color"]
            filled_this_round += 1

        if filled_this_round == 0:
            # egyik üres poligon sem talált címkézett szomszédot → elszigetelt
            remaining = out["szavazokorid"].isna().sum()
            print(f"[ures_polyk_besorolasa] {remaining} cella marad besorolatlanul (elszigetelt, nincs címkézett szomszéd)")
            break

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
