import pandas as pd
import geopandas as gpd
import random
import matplotlib.pyplot as plt
import colorsys

from shapely.ops import unary_union, split, polygonize
from shapely import make_valid, set_precision
from shapely.geometry import LineString, MultiPolygon, Polygon



def _hex_from_rgb01(r, g, b):
    return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

def _distinct_hex_colors(n, s=0.62, v1=0.92, v2=0.78):
    """
    n db jól elkülönülő szín.
    - Hue: egyenletes elosztás + golden ratio léptetés (jó szórás)
    - V: váltogatva (v1/v2), hogy nagy n-nél is szétváljanak
    """
    if n <= 0:
        return []
    golden = 0.618033988749895  # golden ratio conjugate
    h = 0.0
    out = []
    for i in range(n):
        h = (h + golden) % 1.0
        v = v1 if (i % 2 == 0) else v2
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        out.append(_hex_from_rgb01(r, g, b))
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
      - ha több szavazókör van egy poligonon belül -> print és skip
      - ha egyetlen szavazókör van -> results (GeoDataFrame) sorba menti:
          geometry (poligon), szavazokorid, color
    '''

    # Biztonsági ellenőrzés
    if gdf.crs is None or gdf_szigetek.crs is None:
        raise ValueError("Mindkét GeoDataFrame-nek kell legyen CRS-e")

    # CRS egységesítés
    if gdf.crs != gdf_szigetek.crs:
        gdf = gdf.to_crs(gdf_szigetek.crs)

    # Ebbe gyűjtjük a "jó" poligonokat (amiknél 1 db szavazókör azonosítható)
    rows = []

    # Végigmegyünk az összes poligonon

    for poly_idx, poly_row in gdf_szigetek.iterrows():
        polygon_geom = poly_row.geometry

        # Pontok a poligonon belül
        inside_mask = gdf.within(polygon_geom)
        points_inside = gdf[inside_mask].copy()

        # Ha nincs pont, csak jelezzük és megyünk tovább
        if len(points_inside) == 0:
            # print(f"\nPoligon {poly_idx}: NINCS benne pont.")
            rows.append({"szavazokorid": None, "color": None, "geometry": polygon_geom})
            continue

        # print(f"\nPoligon {poly_idx}: {len(points_inside)} pont található benne.")

        # Egyedi szavazókörök a poligonon belül
        unique_szavazokorok = points_inside["szavazokorid"].dropna().unique()

        if len(unique_szavazokorok) != 1:
            # print("több szavazókörhöz tartozik")

            # meghívom a poly-n a rekúriv függvényt
            rows_darabok = polygon_tobb_szavazokor(polygon_geom, points_inside, max_depth=max_depth)
            rows.extend(rows_darabok)
            continue

        # Ha ide jutunk, akkor pontosan 1 szavazókör van
        szavazokorid_value = unique_szavazokorok[0]

        # Color: azonos (a szavazókörhöz)
        color_value = points_inside.iloc[0]["color"]

        # Mentjük a poligont a hozzárendelt szavazokorid-val és colorral
        rows.append({
            "szavazokorid": szavazokorid_value,
            "color": color_value,
            "geometry": polygon_geom
        })

    # Results GeoDataFrame
    results = gpd.GeoDataFrame(rows, geometry="geometry", crs=gdf_szigetek.crs)

    return results









# segéd függvények

def felez(poly):
    """
    A poligont kettévágja a centroidon átmenő vágással (a hosszabb bbox tengely mentén).

    A cut line csúcspontjai új koordinátákat vezetnek be (metszéspontok), amik a
    cella eredeti 1mm rácsától eltérhetnek. set_precision-nel visszasnappeljük
    a rácsra, hogy a szomszédos (NEM split) cellákkal az illeszkedés megmaradjon.
    """

    minx, miny, maxx, maxy = poly.bounds
    cx, cy = poly.centroid.x, poly.centroid.y

    # Hosszabb irány kiválasztása
    if (maxx - minx) >= (maxy - miny):
        vago = LineString([(cx, miny - 1), (cx, maxy + 1)])
    else:
        vago = LineString([(minx - 1, cy), (maxx + 1, cy)])

    darabok = list(split(poly, vago).geoms)

    if len(darabok) < 2:
        return [poly]

    # Cut line új csúcsait a közös 1mm rácsra snappeljük
    snapped = []
    for d in darabok:
        s = set_precision(d, grid_size=0.01)
        if s is not None and not s.is_empty and s.geom_type in ("Polygon", "MultiPolygon"):
            snapped.append(s)

    return snapped if snapped else [poly]


def pontok_poligonban(pts_gdf, poly):
    """
    Pontok szűrése poligonra
    """
    return pts_gdf[pts_gdf.within(poly)].copy()


def szavazokorok_szama(pts_gdf):
    """
    Hány különböző szavazokorid van a pontok között?
    """
    return pts_gdf["szavazokorid"].dropna().unique()


def polygon_tobb_szavazokor(polygon_geom, points_inside, max_depth=25):
    '''
    Több szavazókörös poligon "szétszedése" felezéssel.

    Paraméterek:
      - polygon_geom: a poligon geometriája (shapely Polygon)
      - points_inside: GeoDataFrame, a poligonon belüli pontok (gdf szűrt része)

    Működés:
    - Egy feldolgozási sorban (queue) tartjuk azokat a poligonokat, amik még kevertek
    - Minden körben: poligon felezése a mértani közepén
    - A felekre újraszűrjük a pontokat:
        - 0 pont -> üres poligon, nem bontjuk tovább
        - 1 db szavazokorid -> nem bontjuk tovább, megvan a legkisebb egyedi poly
        - több szavazokorid -> visszakerül a sorba, és újra felezzük
    '''

    rows = []
    queue = [(polygon_geom, points_inside, 0)]  # (poly, pts, depth)

    # ameddig van nem egységes besorolású poligon
    while queue:
        poly, pts, depth = queue.pop()  # kiveszünk egy polyt

        # ne legyen végtelen ciklus: ha túl mélyre mentünk inkább hadjuk
        if depth >= max_depth:
            print('Elérte a poly a mélységi szintet!')
            # rows.append({"szavazokorid":None, "color":None, "geometry":poly})
            continue

        # 1) felezés
        darabok = felez(poly)

        # 2) gyerekpoligonok értékelése
        for darab in darabok:
            darab_pts = pontok_poligonban(pts, darab)  # lekérdezem a darabban lévő pontokat

            # ha 0 pont van benne
            if len(darab_pts) == 0:
                rows.append({"szavazokorid": None, "color": None, "geometry": darab})
                continue

            # megnézem hogy egyediek e szkid-k
            uniq = szavazokorok_szama(darab_pts)

            # csak 1 szavazókör -> eredmény ezt kell!!!
            if len(uniq) == 1:
                # mentem a polyt
                rows.append({
                    "szavazokorid": uniq[0],
                    "color": darab_pts.iloc[0]["color"],
                    "geometry": darab
                })
                continue

            # több szavazókör -> vissza a sorba, újra felezésre
            queue.append((darab, darab_pts, depth + 1))

    return rows






def ures_polyk_besorolasa(results, *, max_iters=10):
    """
    Üres (szavazokorid=NaN/None) cellák besorolása szomszédokhoz.

    Iterációs megközelítés a teljes lefedettség érdekében:
      1. Minden körben megpróbálunk minden üres cellát besorolni: közös-ÉL
         szomszédok közül a leggyakoribb szavazokorid nyer.
      2. Ha egy körben NEM sikerül semelyik maradék üres cellát besorolni
         (nincs szomszédjuk címkézett cellával), lazítunk:
         először csak sarokponton érintkező szomszédokat is megengedünk,
         majd utolsó esélyként a legközelebbi címkézett cellát (centroid
         távolság alapján) rendeljük hozzá.
      3. Ez garantálja, hogy nincs maradék None cella a kimenetben → nem
         jelennek meg üres "lyukak" a sarkokban és a szavazóköri poligonok
         belsejében.
    """

    out = results.copy()

    def pick_winner(neighbors):
        counts = neighbors["szavazokorid"].value_counts()
        winner_id = counts.index[0]
        winner_color = neighbors.loc[
            neighbors["szavazokorid"] == winner_id, "color"
        ].iloc[0]
        return winner_id, winner_color

    for iteration in range(max_iters):
        missing_idxs = out.index[out["szavazokorid"].isna()].tolist()
        if not missing_idxs:
            break

        sindex = out.sindex
        assigned_this_round = 0

        for idx in missing_idxs:
            geom = out.at[idx, "geometry"]
            candidate_idxs = list(sindex.intersection(geom.bounds))
            candidates = out.loc[candidate_idxs]

            # 1. próba: közös ÉL (hossz > 1 cm)
            edge_neighbors = candidates[
                candidates.geometry.apply(
                    lambda g: g.boundary.intersection(geom.boundary).length > 0.01
                ) & candidates["szavazokorid"].notna()
            ]
            if len(edge_neighbors) > 0:
                wid, wcol = pick_winner(edge_neighbors)
                out.at[idx, "szavazokorid"] = wid
                out.at[idx, "color"] = wcol
                assigned_this_round += 1

        if assigned_this_round > 0:
            continue

        # Nem sikerült edge-szomszéddal előrelépni → fallback: sarokérintés
        missing_idxs = out.index[out["szavazokorid"].isna()].tolist()
        if not missing_idxs:
            break

        sindex = out.sindex
        for idx in missing_idxs:
            geom = out.at[idx, "geometry"]
            candidate_idxs = list(sindex.intersection(geom.bounds))
            candidates = out.loc[candidate_idxs]

            # 2. próba: bármilyen érintés (sarokpont is)
            touch_neighbors = candidates[
                candidates.geometry.apply(lambda g: g.intersects(geom) and g is not geom)
                & candidates["szavazokorid"].notna()
            ]
            if len(touch_neighbors) > 0:
                wid, wcol = pick_winner(touch_neighbors)
                out.at[idx, "szavazokorid"] = wid
                out.at[idx, "color"] = wcol
                assigned_this_round += 1

        if assigned_this_round > 0:
            continue

        # 3. próba (utolsó esély): legközelebbi címkézett cella centroid alapján
        missing_idxs = out.index[out["szavazokorid"].isna()].tolist()
        labeled = out[out["szavazokorid"].notna()]
        if len(labeled) == 0 or not missing_idxs:
            break

        for idx in missing_idxs:
            geom = out.at[idx, "geometry"]
            c = geom.centroid
            dists = labeled.geometry.apply(lambda g: g.centroid.distance(c))
            nearest = labeled.loc[dists.idxmin()]
            out.at[idx, "szavazokorid"] = nearest["szavazokorid"]
            out.at[idx, "color"] = nearest["color"]

        break

    return out









def _split_pinch_point_preserving_holes(poly):
    """Figura-8 (self-touching) Polygon szétbontása külön polygonokra,
    a belső gyűrűket (lyukakat) visszaillesztve a megfelelő külső részbe."""
    if poly.exterior.is_simple:
        return poly
    outer_parts = list(polygonize(poly.exterior))
    if not outer_parts:
        return poly
    interiors = list(poly.interiors)
    if not interiors:
        return outer_parts[0] if len(outer_parts) == 1 else MultiPolygon(outer_parts)
    result = []
    for op in outer_parts:
        holes = [list(ring.coords) for ring in interiors if op.contains(Polygon(ring))]
        result.append(Polygon(list(op.exterior.coords), holes))
    return result[0] if len(result) == 1 else MultiPolygon(result)


def _clean_polygonal(geom):
    """Biztosítja, hogy a geom tiszta Polygon/MultiPolygon — nincsenek
    LineString komponensek (lógó vonal), nincs figura-8."""
    if geom is None or geom.is_empty:
        return None

    # GeometryCollection: csak a polygonális részeket tartjuk meg
    if geom.geom_type == "GeometryCollection":
        parts = []
        for g in geom.geoms:
            if g.geom_type == "Polygon":
                parts.append(g)
            elif g.geom_type == "MultiPolygon":
                parts.extend(list(g.geoms))
        if not parts:
            return None
        geom = parts[0] if len(parts) == 1 else MultiPolygon(parts)

    if geom.geom_type == "Polygon":
        return _split_pinch_point_preserving_holes(geom)

    if geom.geom_type == "MultiPolygon":
        pieces = []
        for p in geom.geoms:
            c = _split_pinch_point_preserving_holes(p)
            if c is None or c.is_empty:
                continue
            if c.geom_type == "MultiPolygon":
                pieces.extend(list(c.geoms))
            else:
                pieces.append(c)
        if not pieces:
            return None
        return pieces[0] if len(pieces) == 1 else MultiPolygon(pieces)

    return None


def polygonok_egyesitese(results, *, max_parts=1, **_ignored):
    '''
    Szavazókörönkénti unió.

    Minden cella az `egyesites()` + `felez()` után 1cm rácson van → élben
    érintkező szomszédos cellák pontosan osztoznak csúcsokon. A unió
    előtt set_precision-nel újra szinkronizálunk a biztonság kedvéért.

    - unary_union szavazókörönként
    - make_valid (figura-8 splitting, érvényes topológia)
    - GeometryCollection / LineString komponens szűrés (nincs lógó vonal)
    - Pinch-point külső gyűrűn: szétbontás lyuk-megőrzéssel
    - Lyukak (másik szavazókör cellái által elfoglalt belső terület)
      érintetlenül maradnak — átfedés kizárt
    '''
    out_rows = []

    for szkid, grp in results.groupby("szavazokorid", dropna=False):
        color = grp["color"].iloc[0] if "color" in grp.columns else None

        geoms = []
        for g in grp.geometry:
            if g is None or g.is_empty:
                continue
            g = set_precision(g, grid_size=0.01)
            if g is None or g.is_empty:
                continue
            if g.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            geoms.append(g)

        if not geoms:
            continue

        geom = geoms[0] if len(geoms) == 1 else unary_union(geoms)
        geom = make_valid(geom)
        geom = _clean_polygonal(geom)

        if geom is None or geom.is_empty:
            continue

        out_rows.append({
            "szavazokorid": szkid,
            "color": color,
            "geometry": geom,
        })

    return gpd.GeoDataFrame(out_rows, geometry="geometry", crs=results.crs)

