import osmnx as ox
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

from shapely.ops import unary_union, linemerge, snap, polygonize, polygonize_full, nearest_points, split as shp_split
from shapely.strtree import STRtree
from shapely import make_valid, set_precision
from shapely.geometry import LineString, Polygon, MultiPolygon, Point, GeometryCollection


def _safe_make_valid(g):
    if g is None or g.is_empty:
        return g
    try:
        return make_valid(g)
    except Exception:
        try:
            return g.buffer(0)
        except Exception:
            return g


def _pts(geom):
    '''Pontok kinyerése metszésgeometria bármely típusából.'''
    if geom is None or geom.is_empty:
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

    keep_polys = [p for p in polys if roads_union.intersection(p).length > 0]

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


def blue_gen(Gp, nodes, boundary, DIST_LIM=100.0, MIN_SEG=0.1, EPS=0.25, RAY_MULT=1.5):
    '''
    KÉK (node -> lakóhatár, ha közel van)

    A csomópontot NEM a legközelebbi határponthoz kötjük, hanem az onnan induló
    utcát hosszabbítjuk meg az utca irányában DIST_LIM*RAY_MULT hosszig, és a
    sugár első határ-metszéspontjához. Ha egyik incidens utca sem metszi a
    határt a cap-en belül -> nem képzünk kék vonalat ebből a csomópontból.
    '''

    Gu = Gp.to_undirected()
    dists = nodes.geometry.distance(boundary)
    close_nodes = nodes[dists <= DIST_LIM]
    MAX_RAY = DIST_LIM * RAY_MULT

    blue = []
    for node_id in close_nodes.index:
        pt = nodes.loc[node_id].geometry
        best_p, best_s = None, np.inf

        for u, v, k, data in Gu.edges(node_id, keys=True, data=True):
            geom = data.get("geometry")
            if geom is None:
                other = v if u == node_id else u
                geom = LineString([pt, nodes.loc[other].geometry])

            c = list(geom.coords)
            if len(c) < 2:
                continue

            a0, a1 = Point(c[0]), Point(c[-1])
            if pt.distance(a0) <= pt.distance(a1):
                a, b = c[0], c[1]
            else:
                a, b = c[-1], c[-2]

            dx, dy = (a[0] - b[0], a[1] - b[1])
            n = (dx * dx + dy * dy) ** 0.5
            if n == 0:
                continue

            far = Point(pt.x + dx / n * MAX_RAY, pt.y + dy / n * MAX_RAY)
            ray = LineString([pt, far])

            inter = ray.intersection(boundary)
            for p in _pts(inter):
                s = ray.project(p)
                if s <= EPS or s >= best_s:
                    continue
                best_s, best_p = s, p

        if best_p is not None:
            seg = LineString([pt, best_p])
            if seg.length > MIN_SEG:
                blue.append(seg)

    return gpd.GeoSeries(blue, crs=nodes.crs)


def red_gen(res_area, edges, orange, blue, MAX_RAY=1000.0, INSIDE_EPS=0.5, EPS=0.25, MIN_SEG=0.1, SHARP_MAX_ANGLE_DEG=150.0):
    '''
    VÖRÖS (lakott területi határ-él befelé meghosszabbítás)

    A polygon határ MINDEN csúcsában mindkét szomszédos él folytatását kipróbálja
    a polygon BELSEJE felé. Ha a folytatás tényleg befelé mutat (probe contains),
    egy MAX_RAY cap-pel sugarat lő és megáll az ELSŐ találatnál: utca / orange /
    blue vagy MÁSIK határvonal. Konkáv csúcsoknál (pl. kinyúló városrész alapja)
    ez befelé nyúló "vágóvonalakat" képez — segít elvágni a kinyúlás környékén
    a látszólag nem párhuzamos, de lényegében határ-közeli utcákat.
    '''

    boundary_line = res_area.boundary
    boundary_lines = extract_lines(boundary_line)

    street_lines = [g for g in edges.geometry if g is not None and not g.is_empty]
    orange_lines = [g for g in orange.geometry if g is not None and not g.is_empty] if orange is not None and len(orange) else []
    blue_lines = [g for g in blue.geometry if g is not None and not g.is_empty] if blue is not None and len(blue) else []

    target_lines = street_lines + orange_lines + blue_lines + boundary_lines
    if not target_lines:
        return gpd.GeoSeries([], crs=edges.crs)

    target_union = unary_union(target_lines)

    cos_threshold = np.cos(np.radians(SHARP_MAX_ANGLE_DEG))

    rings = []
    if res_area.geom_type == "Polygon":
        rings.append(res_area.exterior)
        rings.extend(res_area.interiors)
    elif res_area.geom_type == "MultiPolygon":
        for p in res_area.geoms:
            rings.append(p.exterior)
            rings.extend(p.interiors)

    red = []
    for ring in rings:
        coords = list(ring.coords)
        n = len(coords) - 1
        if n < 3:
            continue

        for i in range(n):
            prev = coords[(i - 1) % n]
            curr = coords[i]
            nxt = coords[(i + 1) % n]

            vp_x = prev[0] - curr[0]
            vp_y = prev[1] - curr[1]
            vn_x = nxt[0] - curr[0]
            vn_y = nxt[1] - curr[1]
            vp_len = (vp_x * vp_x + vp_y * vp_y) ** 0.5
            vn_len = (vn_x * vn_x + vn_y * vn_y) ** 0.5
            if vp_len == 0 or vn_len == 0:
                continue
            cos_a = (vp_x * vn_x + vp_y * vn_y) / (vp_len * vn_len)
            if cos_a <= cos_threshold:
                continue

            for other in (prev, nxt):
                dx = curr[0] - other[0]
                dy = curr[1] - other[1]
                nrm = (dx * dx + dy * dy) ** 0.5
                if nrm == 0:
                    continue
                dx /= nrm
                dy /= nrm

                probe = Point(curr[0] + dx * INSIDE_EPS, curr[1] + dy * INSIDE_EPS)
                if not res_area.contains(probe):
                    continue

                far = Point(curr[0] + dx * MAX_RAY, curr[1] + dy * MAX_RAY)
                start = Point(curr)
                ray = LineString([start, far])

                inter = ray.intersection(target_union)
                best_p, best_s = None, np.inf
                for p in _pts(inter):
                    s = ray.project(p)
                    if s <= EPS or s >= best_s:
                        continue
                    best_s, best_p = s, p

                if best_p is not None:
                    seg = LineString([start, best_p])
                    if seg.length > MIN_SEG:
                        red.append(seg)

    return gpd.GeoSeries(red, crs=edges.crs)


def szur_hatarral_parhuzamos(tier_lines, boundary_line, PARALLEL_TOL, cutter_lines=None):
    '''
    A határ PARALLEL_TOL bufferén belüli sub-szakaszokat törli a tier_lines-ból.

    Ha cutter_lines meg van adva (red/blue/orange helperek), a tier vonalakat
    ELŐSZÖR szétdarabolja a helper-keresztezési pontokon. A buffer-sávon belüli
    sub-szakaszt csak akkor dobja el, ha egyik végpontja sem horgonyoz helper-
    végpontot — különben megőrzi, hogy a helper ne lógjon a levegőben a törölt
    utca-részlet miatt.
    '''
    if not tier_lines:
        return []

    band = boundary_line.buffer(PARALLEL_TOL)

    cutters = [c for c in (cutter_lines or []) if c is not None and not c.is_empty]
    cutter_u = unary_union(cutters) if cutters else None

    out = []
    for ln in tier_lines:
        if ln is None or ln.is_empty:
            continue
        if cutter_u is not None and ln.intersects(cutter_u):
            try:
                pieces = extract_lines(shp_split(ln, cutter_u))
            except Exception:
                pieces = [ln]
        else:
            pieces = [ln]

        for p in pieces:
            if p is None or p.is_empty:
                continue
            if not p.within(band):
                out.append(p)
                continue
            # Teljes egészében a bufferen belül: csak akkor tartjuk, ha egy
            # végpontja horgonyoz egy helper-keresztezést (különben a helper
            # dangle lenne az elvágott utca-részlet végénél).
            if cutter_u is not None:
                e0 = Point(p.coords[0])
                e1 = Point(p.coords[-1])
                if e0.distance(cutter_u) < 1e-6 or e1.distance(cutter_u) < 1e-6:
                    out.append(p)
    return out


def endpoint_cluster_merge(tiers_in_priority, frozen_lines, ENDPOINT_TOL, anchor_vertex_lines=None):
    """
    Közel eső vonalvégpontokat egyetlen pontba olvasztja.
    Anchor-prioritás: (1) kisebb tier-index (frozen=0 → soha nem mozdul);
    (2) azonos tieren belül nagyobb kapcsolati fokszám (hány végpont osztja ugyanazt a koordinátát).
    A frozen_lines (határ + utcahálózat) együtt alkotja a tier 0-t — sem a határ,
    sem egyetlen utca pont nem mozdul el. A helper-vonalak (red/blue/orange) ezekhez
    a fix pontokhoz igazodnak.

    anchor_vertex_lines (opcionális): olyan vonalak, amelyek MINDEN csúcsát
    phantom tier-0 horgonyként adjuk hozzá. Tipikus használat: a határvonal
    minden csúcsa — így ha egy blue vége a határ szakasz-belsején landolna és
    egy red ugyanannak a csúcsnak a közeléből indul, mindkettő a csúcshoz
    rögzül (közös ponton találkoznak). Phantom endpointok nem tartoznak
    mozgatható vonalhoz, maguk sosem mozdulnak.
    """
    from collections import Counter, defaultdict

    tiers = [list(frozen_lines)] + [list(t) for t in tiers_in_priority]

    records = []  # [tier_idx, mutable_coords_list]
    for ti, tier in enumerate(tiers):
        for ln in tier:
            if ln is None or ln.is_empty:
                continue
            records.append([ti, list(ln.coords)])

    if not records:
        return []

    endpoints = []  # [x, y, line_idx, end_idx (0=start,1=end), tier_idx]
    for li, (ti, coords) in enumerate(records):
        endpoints.append([coords[0][0], coords[0][1], li, 0, ti])
        endpoints.append([coords[-1][0], coords[-1][1], li, 1, ti])

    # Phantom tier-0 horgonyok: anchor_vertex_lines MINDEN csúcsa. line_idx=-1
    # jelöli hogy nincs hozzá tartozó mozgatható vonal (csak vonz, nem mozog).
    if anchor_vertex_lines:
        for ln in anchor_vertex_lines:
            if ln is None or ln.is_empty:
                continue
            for c in ln.coords:
                endpoints.append([c[0], c[1], -1, -1, 0])

    key_res = 0.01
    degree = Counter()
    for x, y, *_ in endpoints:
        degree[(round(x / key_res), round(y / key_res))] += 1

    def ep_degree(ep):
        return degree[(round(ep[0] / key_res), round(ep[1] / key_res))]

    n = len(endpoints)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    tol2 = ENDPOINT_TOL * ENDPOINT_TOL
    for i in range(n):
        xi, yi = endpoints[i][0], endpoints[i][1]
        li_i = endpoints[i][2]
        for j in range(i + 1, n):
            # Egy vonal saját két végét SOHA nem uniózzuk: különben a rövid
            # (<ENDPOINT_TOL) vonalak 0 hosszra zsugorodnának és kiesnének.
            if endpoints[j][2] == li_i:
                continue
            dx = xi - endpoints[j][0]
            dy = yi - endpoints[j][1]
            if dx * dx + dy * dy <= tol2:
                union(i, j)

    clusters = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    multi_clusters = 0
    move_count = 0
    collapsed_skips = 0
    for members in clusters.values():
        if len(members) < 2:
            continue
        multi_clusters += 1
        anchor = min(members, key=lambda k: (endpoints[k][4], -ep_degree(endpoints[k]), k))
        ax, ay = endpoints[anchor][0], endpoints[anchor][1]
        # Tranzitív klaszterelés miatt még mindig előfordulhat, hogy egy vonal
        # MINDKÉT vége ugyanabban a klaszterben köt ki (A-C-B lánc). Detektáljuk
        # és csak az egyik végét mozdítjuk — így a vonal nem zsugorodik 0-ra.
        line_ends_in_cluster = defaultdict(list)
        for k in members:
            line_ends_in_cluster[endpoints[k][2]].append(k)

        moved_ends_per_line = defaultdict(int)
        for k in members:
            if k == anchor:
                continue
            ep = endpoints[k]
            if ep[4] == 0:
                continue
            li, end_idx = ep[2], ep[3]
            # Ha a vonal MINDKÉT vége ebben a klaszterben van (tranzitív A-C-B
            # lánc), CSAK az első véget mozdítjuk — különben a vonal 0 hosszra
            # zsugorodna. A másodikat skippeljük.
            if len(line_ends_in_cluster[li]) > 1 and li != endpoints[anchor][2]:
                if moved_ends_per_line[li] >= 1:
                    collapsed_skips += 1
                    continue
            coords = records[li][1]
            if end_idx == 0:
                coords[0] = (ax, ay)
            else:
                coords[-1] = (ax, ay)
            moved_ends_per_line[li] += 1
            move_count += 1

    print(f"[endpoint_cluster_merge] endpoints={n}, multi-clusters={multi_clusters}, "
          f"moves={move_count}, skipped-collapses={collapsed_skips}, tol={ENDPOINT_TOL}m")

    per_tier = [[] for _ in tiers]
    for ti, coords in records:
        cleaned = [coords[0]]
        for c in coords[1:]:
            if c != cleaned[-1]:
                cleaned.append(c)
        if len(cleaned) < 2:
            continue
        try:
            ln = LineString(cleaned)
            if ln.length > 0:
                per_tier[ti].append(ln)
        except Exception:
            pass
    return per_tier[0], per_tier[1:]


def hierarchikus_snap(tiers_in_priority, frozen_lines, MERGE_TOL):
    '''
    A mozgatható tiereket (tiers_in_priority) tier-enként snapeljük a fix
    horgonyokra: frozen_lines (határ + utcahálózat) + az eddig már snapolt
    mozgatható tierek. A frozen_lines geometriái soha nem módosulnak.
    Return: (frozen_lines, movable_snapped) — szétválasztva, hogy később csak
    a mozgathatókon dolgozzunk.
    '''
    movable_accum = []
    for tier in tiers_in_priority:
        if not tier:
            continue
        anchors_u = unary_union(list(frozen_lines) + movable_accum)
        snapped = snap(unary_union(list(tier)), anchors_u, MERGE_TOL)
        movable_accum += [g for g in extract_lines(snapped) if g is not None and not g.is_empty]
    return list(frozen_lines), movable_accum


def reanchor_touching_endpoints(movable, frozen, TOL):
    '''
    Csak a movable (helper: red/blue/orange) vonalak végpontjait vetíti vissza
    a legközelebbi másik vonalra, ha 0 < d ≤ TOL távolságra van tőle. A frozen
    (határ + utcák) geometriája NEM módosul.

    Miért kell: a shapely.snap csak vertex→vertex snap-el, így ha egy helper
    végpont egy másik helper szegmens-belsejében ült és annak vertexe elmozdult,
    a szegmens megdől és a touching pont lefloatol → dangle-ként törlődne.
    A frozen vonalak már úgyis a helyükön maradnak, rájuk ez a probléma nem
    vonatkozik.
    '''
    movable = [ln for ln in movable if ln is not None and not ln.is_empty]
    frozen = [ln for ln in frozen if ln is not None and not ln.is_empty]
    if not movable:
        return movable

    all_lines = list(movable) + list(frozen)
    tree = STRtree(all_lines)
    reanchored = 0
    for i in range(len(movable)):
        ln = movable[i]
        coords = list(ln.coords)
        new_coords = list(coords)
        changed = False
        for end_idx in (0, -1):
            p = Point(new_coords[end_idx])
            cand_idx = [k for k in tree.query(p.buffer(TOL)) if k != i]
            if not cand_idx:
                continue
            others_u = unary_union([all_lines[k] for k in cand_idx])
            d = p.distance(others_u)
            if 0 < d <= TOL:
                _, nearest = nearest_points(p, others_u)
                new_coords[end_idx] = (nearest.x, nearest.y)
                changed = True
                reanchored += 1
        if changed:
            cleaned = [new_coords[0]]
            for c in new_coords[1:]:
                if c != cleaned[-1]:
                    cleaned.append(c)
            if len(cleaned) >= 2:
                movable[i] = LineString(cleaned)

    if reanchored:
        print(f"[reanchor] {reanchored} lefloatolt helper-végpont visszavetítve (TOL={TOL}m)")
    return movable


def unify_close_endpoints(movable, frozen, TOL):
    '''
    Safety net: a korábbi lépések után is előfordulhat, hogy két mozgatható
    (red/blue/orange) végpont TOL-on belül maradt, de nem pontosan ugyanazon
    a koordinátán — ezt a polygonize dangle-ként látja, és a helper levegőben
    lóg. Union-find klaszterezéssel összevonjuk a TOL-on belüli mozgatható
    végpontokat egyetlen közös pontba.
    Anchor-választás klaszteren belül:
      1) ha valamelyik tag 1e-6-on belül van egy frozen vonalhoz → az lesz
         (így nem mozdulunk el a már-jól-illeszkedő pozícióról)
      2) különben a klasztertagok számtani középpontja (centroid)
    '''
    from collections import defaultdict

    movable = [ln for ln in movable if ln is not None and not ln.is_empty]
    frozen = [ln for ln in frozen if ln is not None and not ln.is_empty]
    if not movable:
        return movable

    frozen_u = unary_union(frozen) if frozen else None

    endpoints = []  # [x, y, line_idx, end_idx]
    for li, ln in enumerate(movable):
        c = list(ln.coords)
        endpoints.append([c[0][0], c[0][1], li, 0])
        endpoints.append([c[-1][0], c[-1][1], li, 1])

    n = len(endpoints)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    tol2 = TOL * TOL
    for i in range(n):
        xi, yi, li_i, _ = endpoints[i]
        for j in range(i + 1, n):
            if endpoints[j][2] == li_i:
                continue
            dx = xi - endpoints[j][0]
            dy = yi - endpoints[j][1]
            if dx * dx + dy * dy <= tol2:
                union(i, j)

    clusters = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    coords_per_line = [list(ln.coords) for ln in movable]
    unified = 0
    for members in clusters.values():
        if len(members) < 2:
            continue
        anchor_xy = None
        if frozen_u is not None:
            for k in members:
                if Point(endpoints[k][0], endpoints[k][1]).distance(frozen_u) < 1e-6:
                    anchor_xy = (endpoints[k][0], endpoints[k][1])
                    break
        if anchor_xy is None:
            ax = sum(endpoints[k][0] for k in members) / len(members)
            ay = sum(endpoints[k][1] for k in members) / len(members)
            anchor_xy = (ax, ay)

        moved_ends_per_line = defaultdict(int)
        for k in members:
            li, end_idx = endpoints[k][2], endpoints[k][3]
            c_list = coords_per_line[li]
            if (end_idx == 0 and tuple(c_list[0]) == anchor_xy) or \
               (end_idx == 1 and tuple(c_list[-1]) == anchor_xy):
                continue
            # Ne zsugorítsuk 0-ra: ha már egyik véget mozdítottuk, és a másik is
            # ebbe a klaszterbe esne, skip.
            if moved_ends_per_line[li] >= 1:
                continue
            if end_idx == 0:
                c_list[0] = anchor_xy
            else:
                c_list[-1] = anchor_xy
            moved_ends_per_line[li] += 1
            unified += 1

    out = []
    for coords in coords_per_line:
        cleaned = [coords[0]]
        for c in coords[1:]:
            if c != cleaned[-1]:
                cleaned.append(c)
        if len(cleaned) >= 2:
            try:
                ln = LineString(cleaned)
                if ln.length > 0:
                    out.append(ln)
            except Exception:
                pass

    if unified:
        print(f"[unify_close_endpoints] {unified} mozgatható végpont összevonva (TOL={TOL}m)")
    return out


def v_shape_fix(lines, V_TOL, V_ANGLE_MAX_DEG=30.0):
    '''
    Közös végpontból induló, közel-párhuzamos (≤ V_ANGLE_MAX_DEG szögű)
    vonalpárok közül a rövidebbet eltávolítjuk, ha:
      - a hosszabb V_TOL-on belül fut a rövidebb MÁSIK végétől, ÉS
      - a rövidebb másik vége tényleg dangling (degree=1).

    A dangling-feltétel védi a hálózatot hordozó vonalakat és a zárt gyűrűket
    (pl. lakott területi határ — ott minden coord degree≥2) a törléstől.
    '''
    from collections import defaultdict, Counter

    lines = [ln for ln in lines if ln is not None and not ln.is_empty]
    key = lambda c: (round(c[0] / 0.01), round(c[1] / 0.01))

    endpoint_deg = Counter()
    for ln in lines:
        endpoint_deg[key(ln.coords[0])] += 1
        endpoint_deg[key(ln.coords[-1])] += 1

    junctions = defaultdict(list)
    for i, ln in enumerate(lines):
        c0, c1 = ln.coords[0], ln.coords[-1]
        junctions[key(c0)].append((i, c0, c1))
        junctions[key(c1)].append((i, c1, c0))

    cos_thresh = np.cos(np.radians(V_ANGLE_MAX_DEG))
    to_remove = set()
    for members in junctions.values():
        if len(members) < 2:
            continue
        for a in range(len(members)):
            i, ja, oa = members[a]
            for b in range(a + 1, len(members)):
                j, jb, ob = members[b]
                if i in to_remove or j in to_remove or i == j:
                    continue
                vx1, vy1 = oa[0] - ja[0], oa[1] - ja[1]
                vx2, vy2 = ob[0] - jb[0], ob[1] - jb[1]
                l1 = (vx1 * vx1 + vy1 * vy1) ** 0.5
                l2 = (vx2 * vx2 + vy2 * vy2) ** 0.5
                if l1 == 0 or l2 == 0:
                    continue
                cos_a = (vx1 * vx2 + vy1 * vy2) / (l1 * l2)
                if cos_a < cos_thresh:
                    continue  # nem V: a két vonal nem közel-párhuzamos (pl. T-elágazás)
                short, long_, other = (i, j, oa) if lines[i].length <= lines[j].length else (j, i, ob)
                # VÉDELEM: csak akkor töröljük, ha a rövidebb másik vége dangling
                # (degree=1). Így a hálózatot hordozó vonalakat és a zárt
                # gyűrűs határvonalakat nem tudja érinteni.
                if endpoint_deg[key(other)] != 1:
                    continue
                if lines[long_].distance(Point(other)) < V_TOL:
                    to_remove.add(short)

    if to_remove:
        print(f"[v_shape_fix] removed {len(to_remove)} V-zászlót (V_TOL={V_TOL}m, V_ANGLE_MAX={V_ANGLE_MAX_DEG}°)")
    return [ln for k, ln in enumerate(lines) if k not in to_remove]


def kapcsolas(edges, orange, blue, red, res_area, MERGE_TOL=3.0, PARALLEL_TOL=15.0, ENDPOINT_TOL=10.0, V_TOL=3.0):

    boundary_line = res_area.boundary
    boundary_lines = extract_lines(boundary_line)

    if not boundary_lines:
        raise RuntimeError("Nem tudtam boundary vonalakat kinyerni (boundary_lines üres).")

    def _geoms(gs):
        return [g for g in gs.geometry if g is not None and not g.is_empty] if gs is not None and len(gs) else []

    # Csak a streets-re alkalmazzuk a határ-párhuzamos szűrést — az orange/blue/red
    # szándékos határ-híd vonalak, nem szabad filterezni még ha a teljes testük a
    # 15m bufferben van is (tipikus rövid blue esete).
    streets_raw = clip_lines(list(edges.geometry), res_area)
    red_f     = clip_lines(_geoms(red),    res_area)
    blue_f    = clip_lines(_geoms(blue),   res_area)
    orange_f  = clip_lines(_geoms(orange), res_area)

    # A helper vonalakat cutterként átadjuk: ha egy határ-parallel utca-szakaszt
    # egy red/blue/orange végpont metsz, az ottani sub-szakaszt NEM töröljük —
    # különben a helper a levegőben lógna.
    streets_f = szur_hatarral_parhuzamos(streets_raw, boundary_line, PARALLEL_TOL,
                                         cutter_lines=red_f + blue_f + orange_f)

    # FROZEN (soha nem mozog): határvonal + a határ-párhuzamos szűrés után
    # megmaradt utcahálózat. MOZGATHATÓ (helper-ek) prioritás szerint:
    # red > blue > orange — ezek snapelődnek a frozen-re és egymásra.
    frozen_lines = list(boundary_lines) + list(streets_f)

    frozen_merged, tiers_merged = endpoint_cluster_merge(
        tiers_in_priority=[red_f, blue_f, orange_f],
        frozen_lines=frozen_lines,
        ENDPOINT_TOL=ENDPOINT_TOL,
        # A határ MINDEN csúcsa horgonypont — így a szakasz-belsején landoló
        # blue és egy csúcsból induló red ugyanahhoz a csúcshoz rögzül.
        anchor_vertex_lines=boundary_lines,
    )

    frozen_out, movable_snapped = hierarchikus_snap(
        tiers_in_priority=tiers_merged,
        frozen_lines=frozen_merged,
        MERGE_TOL=MERGE_TOL,
    )

    # Csak a mozgatható helper-végpontokat vetítjük vissza a hálózatra
    # (segment-interior touching pont + snap-drift eset).
    movable_snapped = reanchor_touching_endpoints(movable_snapped, frozen_out, TOL=ENDPOINT_TOL)

    # Safety net: ha mindezek után is TOL-on belül maradtak helper-végpontok
    # külön koordinátán, egyetlen pontba olvasztjuk (polygonize-kompatibilitás).
    movable_snapped = unify_close_endpoints(movable_snapped, frozen_out, TOL=ENDPOINT_TOL)

    # V-alak javítás: rövid, közel-párhuzamos testvérvonalak eltávolítása.
    final_lines = v_shape_fix(frozen_out + movable_snapped, V_TOL)

    if not final_lines:
        raise RuntimeError("Nincs semmi a végső hálóhoz (final_lines üres).")

    # 1cm-es rácsra rögzítjük a koordinátákat, hogy a snap-drift ne hagyjon
    # vissza "közeli de nem pontos" csomópontokat — ezek dangle-ként esnének ki
    # a polygonize-ban, és a vonal nem vágná a poligont.
    linework = set_precision(unary_union(final_lines), 0.01)

    # Dead-end (dangle) eltávolítás polygonize ELŐTT — ez a korábbi 1.3.4 lépés,
    # most már a kapcsolas lezárásaként: az egyesites tiszta, zárt hálót kap.
    for _ in range(20):
        _, _, dangles, _ = polygonize_full(linework)
        if dangles.is_empty:
            break
        linework = unary_union(linework.difference(dangles))
    else:
        print("[kapcsolas] Figyelem: 20 iteráció után is maradtak dangle-ek.")

    u_lines = extract_lines(linework)
    merged_geom = linemerge(u_lines) if u_lines else linework
    final_lines = extract_lines(merged_geom) or u_lines

    return gpd.GeoSeries(final_lines, crs=edges.crs)


def egyesites(network_gs_proj, MIN_AREA=500, MAX_STEPS=20000, debug_path=None):
    '''
    MIN_AREA m2: ez alatt beolvasztjuk
    MAX_STEPS biztonsági limit (nagy hálónál se szálljon el)
    A bemenő háló már tiszta (kapcsolas már levágta a dangle-eket, 1cm rácson van).
    '''

    linework = unary_union([g for g in network_gs_proj.geometry if g is not None and not g.is_empty])

    polys = list(polygonize(linework))

    if not polys:
        raise RuntimeError("polygonize nem adott vissza poligonokat (nincs elég zárt hurok / noding probléma).")

    polygons_gdf = gpd.GeoDataFrame(geometry=polys, crs=network_gs_proj.crs).reset_index(drop=True)
    # buffer(0) lebegőpontos zajt adhat, ami a szomszédos cellák csúcs-illeszkedését
    # elrontja → make_valid a szelídebb alternatíva, megőrzi a koordinátákat
    polygons_gdf["geometry"] = polygons_gdf.geometry.apply(make_valid)
    polygons_gdf = polygons_gdf[polygons_gdf.geometry.type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)

    if debug_path is not None:
        polygons_gdf.to_file(debug_path, layer="3a_poligonok_polygonize_raw", driver="GPKG")
        print(f"[egyesites] polygonize raw: {len(polygons_gdf)} poligon; "
              f"{(polygons_gdf.geometry.area < MIN_AREA).sum()} < MIN_AREA ({MIN_AREA} m²) — ezeket összevonja a merge-loop")

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
