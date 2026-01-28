import pandas as pd
import geopandas as gpd
import random
import matplotlib.pyplot as plt
import colorsys

from shapely.ops import unary_union, split
from shapely.geometry import LineString



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
    """

    minx, miny, maxx, maxy = poly.bounds
    cx, cy = poly.centroid.x, poly.centroid.y

    # Hosszabb irány kiválasztása
    if (maxx - minx) >= (maxy - miny):
        # függőleges vágás (x = cx)
        vago = LineString([(cx, miny - 1), (cx, maxy + 1)])
    else:
        # vízszintes vágás (y = cy)
        vago = LineString([(minx - 1, cy), (maxx + 1, cy)])

    darabok = list(split(poly, vago).geoms)

    # Ha valamiért nem sikerült a vágás akkor visszaadjuk egyben
    return darabok if len(darabok) >= 2 else [poly]


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






def ures_polyk_besorolasa(results):
    """
    Azokat a sorokat kezeli, ahol szavazokorid hiányzik (NaN/None):
      - megkeresi a szomszédos poligonokat (touches: közös határ/pont érintés)
      - a szomszédok szavazokorid-jai közül a leggyakoribbat választja
      - beírja a hiányzó szavazokorid-t és a hozzá tartozó color-t (a nyertes szomszéd első colorja)

    Megjegyzés:
      - a "szomszéd" itt: geometriailag érintkező poligon (touches)
      - döntetlen esetén: a leggyakoribbak közül az első (deterministikus sorrend szerint) kerül kiválasztásra
    """

    out = results.copy()

    # Spatial index gyorsításhoz (olvasható marad, de nem lassú)
    sindex = out.sindex

    # Hiányzó szavazokorid sorok indexei (NaN is ide esik)
    missing_idxs = out.index[out["szavazokorid"].isna()].tolist()

    for idx in missing_idxs:
        geom = out.at[idx, "geometry"]

        # Jelöltek: bbox alapján (sindex), majd pontos szűrés touches-szal
        candidate_idxs = list(sindex.intersection(geom.bounds))
        candidates = out.loc[candidate_idxs]

        neighbors = candidates[candidates.geometry.touches(geom)]

        # Csak azok a szomszédok kellenek, ahol van szavazokorid
        neighbors_labeled = neighbors[neighbors["szavazokorid"].notna()]

        if len(neighbors_labeled) == 0:
            # nincs kitől örökölni -> marad NaN/None
            continue

        # Szavazokorid többség meghatározása
        counts = neighbors_labeled["szavazokorid"].value_counts()

        winner_szavazokorid = counts.index[0]

        # Color átvétele: az első olyan szomszédból, amelyik a nyertes szavazokorid
        winner_color = neighbors_labeled.loc[
            neighbors_labeled["szavazokorid"] == winner_szavazokorid, "color"
        ].iloc[0]

        out.at[idx, "szavazokorid"] = winner_szavazokorid
        out.at[idx, "color"] = winner_color

    return out









def polygonok_egyesitese(results, *, max_parts = 1, start_tol = 0.1, grow_factor = 2, max_tol = 50):
    '''
    Szavazókörönként egyetlen *Polygon*-t kényszerít ki úgy, hogy a különálló részeket
    toleranciás "ragasztással" összeköti (buffer+/-).

    - start_tol: kezdő ragasztási távolság (CRS egységben, EOV -> méter)
    - grow_factor: ha még mindig több part, ennyivel szorozzuk a tol-t
    - max_tol: biztonsági plafon, nehogy elszálljon

    FIGYELEM: ez torzít (hidakat képez), de cserébe 1 Polygon lesz.
    '''

    out_rows = []

    for szkid, grp in results.groupby("szavazokorid", dropna=False):
        color = grp["color"].iloc[0] if "color" in grp.columns else None

        geom = unary_union(list(grp.geometry))
        tol = start_tol

        # addig "ragasztunk", amíg el nem érjük a kívánt parts számot (1)
        while True:
            if geom.geom_type == "Polygon":
                break

            if geom.geom_type == "MultiPolygon":
                parts = len(geom.geoms)
                if parts <= max_parts:
                    break
            else:
                # ha valami más (ritka), kilépünk
                break

            if tol > max_tol:
                # nem sikerült 1 poligonná kényszeríteni a plafonon belül
                break

            # closing: növeszt -> összeragad -> visszahúz
            geom = geom.buffer(tol).buffer(-tol)
            tol *= grow_factor

        # Ha még mindig MultiPolygon, itt dönthetsz: hagyod MultiPolygonként (1 geometria),
        # vagy kényszeríted burkolóval (convex hull). Most: visszaadjuk, ami lett.
        out_rows.append({
            "szavazokorid": szkid,
            "color": color,
            "geometry": geom
        })

    return gpd.GeoDataFrame(out_rows, geometry="geometry", crs=results.crs)

