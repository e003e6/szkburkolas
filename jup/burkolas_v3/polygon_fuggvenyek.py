import osmnx as ox
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

from osmnx._errors import InsufficientResponseError

from shapely.ops import unary_union, linemerge, polygonize, polygonize_full, split as shp_split
from shapely import make_valid, set_precision
from shapely.geometry import LineString, Polygon, MultiPolygon, Point, GeometryCollection


class NincsResidentialError(RuntimeError):
    '''Az OSM-ben nincs landuse=residential poligon ehhez a településhez.'''
    pass


# HTTP timeout az Overpass/Nominatim hívásokra — alapból nincs, ezért ha a szerver
# rate-limitel vagy lassul, a kérés végtelenül blokkol. Batch-futtatásnál egy rossz
# település megakasztaná az egész menetet. 60 mp után requests.Timeout kivétel lesz,
# amit a hívó ág kezelhet és továbbléphet a következő településre.
ox.settings.requests_timeout = 60


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

    # strukturált Nominatim-query: city= illeszkedik minden településszintű OSM objektumra
    # (város/falu/hamlet), és country= kizárja a névütközést külföldi egységekkel.
    # Ezzel elkerüljük, hogy pl. "Tab" a Tabi járásra (admin_level=7) mutasson.
    query = {"city": PLACE, "country": "Hungary"} if isinstance(PLACE, str) else PLACE

    G = ox.graph_from_place(query, network_type="drive")
    Gp = ox.project_graph(G)
    nodes, edges = ox.graph_to_gdfs(Gp, nodes=True, edges=True)

    try:
        res = ox.features_from_place(query, tags={"landuse": "residential"})
    except InsufficientResponseError:
        raise NincsResidentialError(f"Nincs landuse=residential poligon ehhez a PLACE-hez az OSM-ben: {PLACE}")
    res = res[res.geometry.type.isin(["Polygon", "MultiPolygon"])].copy()

    if res.empty:
        raise NincsResidentialError(f"Nincs landuse=residential poligon ehhez a PLACE-hez az OSM-ben: {PLACE}")

    res_p = res.to_crs(nodes.crs)

    place_gdf = ox.geocode_to_gdf(query)

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


def letoltes_csak_res(PLACE):
    '''
    Könnyített letöltés: CSAK a residential poligonokat és a hivatalos városhatárt.
    Útgráf NEM tölt le — egy-szavazóköri településeknél használjuk, ahol a szavazóköri
    poligon a teljes lakott terület. Ha nincs residential az OSM-ben, RuntimeError-rel
    leáll (soha nem esünk vissza a sima településhatárra).
    '''

    query = {"city": PLACE, "country": "Hungary"} if isinstance(PLACE, str) else PLACE

    try:
        res = ox.features_from_place(query, tags={"landuse": "residential"})
    except InsufficientResponseError:
        raise NincsResidentialError(f"Nincs landuse=residential poligon ehhez a PLACE-hez az OSM-ben: {PLACE}")
    res = res[res.geometry.type.isin(["Polygon", "MultiPolygon"])].copy()

    if res.empty:
        raise NincsResidentialError(f"Nincs landuse=residential poligon ehhez a PLACE-hez az OSM-ben: {PLACE}")

    res_p = ox.projection.project_gdf(res)

    place_gdf = ox.geocode_to_gdf(query)

    if place_gdf.empty:
        raise RuntimeError(f"Nem lehet lekérni a hivatalos határt (geocode_to_gdf üres): {PLACE}")

    city_geom = _safe_make_valid(place_gdf.geometry.iloc[0])

    if city_geom is None or city_geom.is_empty:
        raise RuntimeError(f"A lekért városhatár geometria üres/hibás: {PLACE}")

    city_boundary = gpd.GeoSeries([city_geom], crs=place_gdf.crs).to_crs(res_p.crs).iloc[0]
    city_boundary = _safe_make_valid(city_boundary)

    if city_boundary is None or city_boundary.is_empty:
        raise RuntimeError(f"A városhatár projekció után üres/hibás lett: {PLACE}")

    return res_p, city_boundary


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

    keep_mask = [roads_union.intersection(p).length > 0 for p in polys]
    keep_polys = [p for p, k in zip(polys, keep_mask) if k]
    bypass_polys = [p for p, k in zip(polys, keep_mask) if not k]

    if keep_polys:
        res_area = unary_union(keep_polys)
        boundary = res_area.boundary
    else:
        res_area = None
        boundary = None

    return res_area, boundary, bypass_polys


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


def kapcsolas(edges, orange, blue, red, res_area, PARALLEL_TOL=15.0, V_TOL=3.0):
    '''
    Vonalháló összerakása generálás-utáni snap NÉLKÜL.

    Alapelv: a helperek (red/blue/orange) `ray.intersection(target)`-tel készülnek,
    vagyis a végpontjuk PONTOSAN a célvonalon van. `unary_union` a rendes noding-ot
    önmagában elvégzi — minden helper-végpont automatikusan közös csúcs lesz a
    célvonalban is. Nincs szükség cluster_merge/snap/reanchor lépésekre; azok
    csak elmozdítanák a végpontokat a pontos találkozási pontról, és így
    dangle-lé tennék a helpert.
    '''
    boundary_line = res_area.boundary
    boundary_lines = extract_lines(boundary_line)

    if not boundary_lines:
        raise RuntimeError("Nem tudtam boundary vonalakat kinyerni (boundary_lines üres).")

    def _geoms(gs):
        return [g for g in gs.geometry if g is not None and not g.is_empty] if gs is not None and len(gs) else []

    streets_raw = clip_lines(list(edges.geometry), res_area)
    red_f     = clip_lines(_geoms(red),    res_area)
    blue_f    = clip_lines(_geoms(blue),   res_area)
    orange_f  = clip_lines(_geoms(orange), res_area)

    # Határ-párhuzamos streets szűrése; helper-keresztezéseknél az utcát a
    # kereszt-pontnál vágja és a helper-horgonyos sub-szakaszt akkor is tartja,
    # ha a bufferben van (különben a helper lelógna).
    streets_f = szur_hatarral_parhuzamos(streets_raw, boundary_line, PARALLEL_TOL,
                                         cutter_lines=red_f + blue_f + orange_f)

    all_lines = list(boundary_lines) + list(streets_f) + list(red_f) + list(blue_f) + list(orange_f)
    if not all_lines:
        raise RuntimeError("Nincs semmi a végső hálóhoz (all_lines üres).")

    # Egyetlen noding-lépés: unary_union pontos metszésekből közös csúcsokat épít,
    # set_precision 1cm-es rácsra rögzít (lebegőpontos zaj ellen).
    linework = set_precision(unary_union(all_lines), 0.01)

    # V-alak javítás a noded hálón: rövid, közel-párhuzamos testvérek törlése.
    linework = unary_union(v_shape_fix(extract_lines(linework), V_TOL))

    # Dangle-loop: csak TÉNYLEG lógó szakaszokat töröl (nem záró hurokhoz tartozó).
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
