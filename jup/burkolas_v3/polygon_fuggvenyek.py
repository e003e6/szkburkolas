import osmnx as ox
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

from shapely.ops import nearest_points, unary_union, linemerge, snap, polygonize, polygonize_full
from shapely import make_valid
from shapely.geometry import LineString, Polygon, MultiPolygon, Point, GeometryCollection


def _safe_make_valid(g):
    if g is None or g.is_empty:
        return g
    try:
        from shapely import make_valid
        return make_valid(g)
    except Exception:
        try:
            return g.buffer(0)
        except Exception:
            return g


def extract_lines(geom):
    if geom is None or geom.is_empty:
        return []
    gt = geom.geom_type
    if gt == "LineString":
        return [geom]
    if gt == "MultiLineString":
        return list(geom.geoms)
    if gt == "GeometryCollection":
        out = []
        for g in geom.geoms:
            out.extend(extract_lines(g))
        return out
    return []


def clip_lines(lines, poly):
    out = []
    for ln in lines:
        if ln is None or ln.is_empty:
            continue
        cut = ln.intersection(poly)
        out.extend(extract_lines(cut))
    return [g for g in out if g is not None and not g.is_empty]


def endpoints_of_lines(lines):
    pts = []
    for ln in lines:
        if ln is None or ln.is_empty:
            continue
        c = list(ln.coords)
        if len(c) >= 2:
            pts.append(Point(c[0]))
            pts.append(Point(c[-1]))
    return pts


def dedup_points(points, eps):
    kept = []
    for p in points:
        if all(p.distance(q) > eps for q in kept):
            kept.append(p)
    return kept


def letoltes(PLACE):
    '''
    Letöltés és projektálás (úthálózat, lakott terület poligonok, hivatalos városhatár)
    '''

    G = ox.graph_from_place(PLACE, network_type="drive")
    Gp = ox.project_graph(G)
    nodes, edges = ox.graph_to_gdfs(Gp, nodes=True, edges=True)

    res = ox.features_from_place(PLACE, tags={"landuse": "residential"})
    res = res[res.geometry.type.isin(["Polygon", "MultiPolygon"])].copy()

    if res.empty:
        raise RuntimeError("Nincs landuse=residential poligon ehhez a PLACE-hez az OSM-ben.")

    res_p = res.to_crs(nodes.crs)

    place_gdf = ox.geocode_to_gdf(PLACE)

    if place_gdf.empty:
        raise RuntimeError("Nem lehet lekérni a hivatalos határt (geocode_to_gdf üres)")

    city_geom = _safe_make_valid(place_gdf.geometry.iloc[0])

    if city_geom is None or city_geom.is_empty:
        raise RuntimeError("A lekért városhatár geometria üres/hibás")

    city_boundary = gpd.GeoSeries([city_geom], crs=place_gdf.crs).to_crs(nodes.crs).iloc[0]
    city_boundary = _safe_make_valid(city_boundary)

    if city_boundary is None or city_boundary.is_empty:
        raise RuntimeError("A városhatár projekció után üres/hibás lett.")

    return Gp, nodes, edges, res_p, city_boundary


def vag_residential_city(res_p, city_boundary):
    '''
    Lakott terület + hivatalos városhatár vágás
    '''

    cut_geoms = []
    for g in res_p.geometry:
        g = _safe_make_valid(g)
        if g is None or g.is_empty:
            continue
        inter = g.intersection(city_boundary)
        if inter is None or inter.is_empty:
            continue
        inter = _safe_make_valid(inter)
        if inter is None or inter.is_empty:
            continue
        if inter.geom_type in ("Polygon", "MultiPolygon"):
            cut_geoms.append(inter)

    if not cut_geoms:
        raise RuntimeError("A városhatáron belül nem maradt residential poligon.")

    return gpd.GeoDataFrame(geometry=cut_geoms, crs=res_p.crs)


def res_area_es_boundary(res_cut, edges):
    '''
    Releváns lakott foltok kiválasztása az úthálózathoz
    '''

    roads_union = edges.geometry.union_all()

    polys = []
    for g in res_cut.geometry:
        if g.geom_type == "Polygon":
            polys.append(g)
        elif g.geom_type == "MultiPolygon":
            polys.extend(list(g.geoms))
    if not polys:
        raise RuntimeError("A residential geometriákból nem tudtam poligonokat kinyerni.")

    keep_polys = [p for p in polys
                  if np.isfinite(getattr(roads_union.intersection(p), "length", 0.0))
                  and getattr(roads_union.intersection(p), "length", 0.0) > 0]

    if not keep_polys:
        c = roads_union.centroid
        keep_polys = [p for p in polys if p.contains(c)]
        if not keep_polys:
            raise RuntimeError("Nem találtam olyan residential poligont, amihez az úthálózat tartozna.")

    res_area = unary_union(keep_polys)
    return res_area, res_area.boundary


def orange_gen(Gp, nodes, edges, MAX_EXT=200.0, EPS=0.25, MIN_SEG=0.1):
    '''
    NARANCS (dead-end -> következő utca)
    '''

    deg = dict(Gp.to_undirected().degree())
    dead = nodes.loc[[n for n, d in deg.items() if d == 1]].copy()

    def _pts(geom):
        if geom.is_empty:
            return []
        t = geom.geom_type
        if t == "Point":
            return [geom]
        if t == "MultiPoint":
            return list(geom.geoms)
        if t == "LineString":
            return [Point(geom.coords[0]), Point(geom.coords[-1])]
        if t in ("MultiLineString", "GeometryCollection"):
            out = []
            for gg in geom.geoms:
                out += _pts(gg)
            return out
        return []

    def _ray_from_deadend(node_id):
        pt = nodes.loc[node_id].geometry

        elist = list(Gp.edges(node_id, keys=True, data=True))
        if not elist:
            elist = list(Gp.in_edges(node_id, keys=True, data=True))
        if not elist:
            return None, None

        u, v, k, data = elist[0]
        geom = data.get("geometry")
        if geom is None:
            other = v if u == node_id else u
            geom = LineString([pt, nodes.loc[other].geometry])

        c = list(geom.coords)
        if len(c) < 2:
            return None, None

        a0, a1 = Point(c[0]), Point(c[-1])
        if pt.distance(a0) <= pt.distance(a1):
            a, b = c[0], c[1]
        else:
            a, b = c[-1], c[-2]

        dx, dy = (a[0] - b[0], a[1] - b[1])
        n = (dx * dx + dy * dy) ** 0.5
        if n == 0:
            return None, None

        far = Point(pt.x + dx / n * MAX_EXT, pt.y + dy / n * MAX_EXT)
        return pt, LineString([pt, far])

    sidx_edges = edges.sindex
    orange = []

    for node_id in dead.index:
        pt, ray = _ray_from_deadend(node_id)
        if ray is None:
            continue

        cand = edges.iloc[list(sidx_edges.intersection(ray.bounds))]

        best_p, best_s = None, np.inf
        for (eu, ev, ek), row in cand.iterrows():
            if node_id in (eu, ev):
                continue
            inter = ray.intersection(row.geometry)
            for p in _pts(inter):
                s = ray.project(p)
                if s <= EPS or s >= best_s:
                    continue
                best_s, best_p = s, p

        if best_p is not None:
            seg = LineString([pt, best_p])
            if seg.length > MIN_SEG:
                orange.append(seg)

    return gpd.GeoSeries(orange, crs=nodes.crs)


def blue_gen(nodes, boundary, DIST_LIM=100.0, MIN_SEG=0.1):
    '''
    KÉK (node -> lakóhatár, ha közel van)
    '''

    dists = nodes.geometry.distance(boundary)
    close_nodes = nodes[dists <= DIST_LIM]

    blue = []
    for _, row in close_nodes.iterrows():
        pt = row.geometry
        _, near = nearest_points(pt, boundary)
        if near is not None and not near.is_empty:
            seg = LineString([pt, near])
            if seg.length > MIN_SEG:
                blue.append(seg)

    return gpd.GeoSeries(blue, crs=nodes.crs)


def kapcsolas(edges, orange, blue, res_area, SNAP_TOL=3.0, STRIP_TOL=10.0, JOIN_TOL=12.0, DEDUP_EPS=1.0):

    boundary_line = res_area.boundary
    boundary_lines = extract_lines(boundary_line)

    if not boundary_lines:
        raise RuntimeError("Nem tudtam boundary vonalakat kinyerni (boundary_lines üres).")

    street_lines = [g for g in edges.geometry if g is not None and not g.is_empty]
    orange_lines = [g for g in orange.geometry if g is not None and not g.is_empty] if orange is not None and len(orange) else []
    blue_lines = [g for g in blue.geometry if g is not None and not g.is_empty] if blue is not None and len(blue) else []

    clipped_other = clip_lines(street_lines + orange_lines + blue_lines, res_area)

    connectors = []

    if clipped_other:
        other_union = unary_union(clipped_other)
        if other_union and not other_union.is_empty:

            other_snapped = snap(other_union, boundary_line, SNAP_TOL)
            snapped_lines = extract_lines(other_snapped)

            border_strip = boundary_line.buffer(STRIP_TOL)

            in_strip = []
            for ln in snapped_lines:
                in_strip.extend(extract_lines(ln.intersection(border_strip)))

            strip_endpoints = dedup_points(endpoints_of_lines(in_strip), DEDUP_EPS)

            for p in strip_endpoints:
                d = p.distance(boundary_line)
                if np.isfinite(d) and 1e-9 < d <= JOIN_TOL:
                    _, q = nearest_points(p, boundary_line)
                    if q is not None and not q.is_empty:
                        seg = LineString([p, q])
                        if seg.length > 1e-6:
                            connectors.append(seg)

            other_clean = other_snapped.difference(border_strip)
            clipped_other = [g for g in extract_lines(other_clean) if g is not None and not g.is_empty]

    all_final = clipped_other + connectors + boundary_lines
    if not all_final:
        raise RuntimeError("Nincs semmi a végső hálóhoz (all_final üres).")

    try:
        u = unary_union(all_final)
        u_lines = extract_lines(u)
        merged_geom = linemerge(u_lines) if u_lines else u
        final_lines = extract_lines(merged_geom) or u_lines or all_final
    except Exception as e:
        print("Union/merge hiba:", repr(e))
        final_lines = all_final

    return gpd.GeoSeries(final_lines, crs=edges.crs)


def egyesites(network_gs_proj, MIN_AREA=5000, MAX_STEPS=20000):
    '''
    MIN_AREA m2: ez alatt beolvasztjuk
    MAX_STEPS biztonsági limit (nagy hálónál se szálljon el)
    '''

    linework = unary_union([g for g in network_gs_proj.geometry if g is not None and not g.is_empty])

    # Iteratív dangle-eltávolítás a nyitott vonalvégek miatt, amiket a polygonize
    # slit-ként (zéró-szélességű) építene be a cella exterior gyűrűjébe.
    for _ in range(20):
        _, _, dangles, _ = polygonize_full(linework)
        if dangles.is_empty:
            break
        linework = unary_union(linework.difference(dangles))
    else:
        print("[egyesites] Figyelem: 20 iteráció után is maradtak dangle-ek.")

    polys = list(polygonize(linework))

    if not polys:
        raise RuntimeError("polygonize nem adott vissza poligonokat (nincs elég zárt hurok / noding probléma).")

    polygons_gdf = gpd.GeoDataFrame(geometry=polys, crs=network_gs_proj.crs).reset_index(drop=True)
    # buffer(0) lebegőpontos zajt adhat, ami a szomszédos cellák csúcs-illeszkedését
    # elrontja → make_valid a szelídebb alternatíva, megőrzi a koordinátákat
    polygons_gdf["geometry"] = polygons_gdf.geometry.apply(make_valid)
    polygons_gdf = polygons_gdf[polygons_gdf.geometry.type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)

    orig_union = unary_union(polygons_gdf.geometry)

    pg = polygons_gdf.copy().reset_index(drop=True)

    def shared_boundary_length(a, b):
        inter = a.boundary.intersection(b.boundary)
        return getattr(inter, "length", 0.0)

    steps = 0
    while steps < MAX_STEPS:
        steps += 1

        areas = pg.geometry.area
        small_idx = areas[areas < MIN_AREA].index.tolist()
        if not small_idx:
            break

        i = min(small_idx, key=lambda k: areas.iloc[k])
        gi = pg.geometry.iloc[i]

        sidx = pg.sindex
        cand = [j for j in sidx.intersection(gi.bounds) if j != i]

        best_j, best_len = None, 0.0
        for j in cand:
            L = shared_boundary_length(gi, pg.geometry.iloc[j])
            if L > best_len:
                best_len = L
                best_j = j

        if best_j is None or best_len <= 0:
            print(f"[STOP] Kicsi poligon ({i}, area={areas.iloc[i]:.6f}) nem talál valódi szomszédot közös éllel.")
            break

        pg.at[best_j, "geometry"] = make_valid(unary_union([gi, pg.geometry.iloc[best_j]]))
        pg = pg.drop(index=i).reset_index(drop=True)

    final_union = unary_union(pg.geometry)
    symdiff_area = float(orig_union.symmetric_difference(final_union).area)
    print("Ellenőrzés: symmetric_difference area (terület eltérés):", symdiff_area)

    return pg
