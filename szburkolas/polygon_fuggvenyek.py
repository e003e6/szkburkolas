import osmnx as ox
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

from shapely.ops import unary_union, linemerge, polygonize, polygonize_full, split as shp_split, snap as shp_snap
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


def orange_gen(Gp, nodes, edges, boundary, MAX_EXT=200.0, EPS=0.25, MIN_SEG=0.1):
    '''
    NARANCS (dead-end -> következő utca VAGY a lakott területi határ)

    A zsákutca-végből az utca folytatásában lőtt sugár a {legközelebbi MÁSIK utca,
    határvonal} közül a KÖZELEBBINÉL áll meg. Így a narancs vonal sosem lép ki a
    lakott területből (a határ ugyanúgy megállítja, mint a kéknél), tehát utólag
    nem kell res_area-ra klippelni.
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

        # A lakott területi határ is megállítja a sugarat: ha az közelebb van,
        # mint a legközelebbi utca, a narancs a határnál áll meg (nem lép ki).
        if boundary is not None:
            for p in _pts(ray.intersection(boundary)):
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


def szur_hatarral_parhuzamos(tier_lines, boundary_line, PARALLEL_TOL, cutter_lines=None):
    '''
    A határ PARALLEL_TOL bufferén belüli sub-szakaszokat törli a tier_lines-ból.

    Ha cutter_lines meg van adva (blue/orange helperek), a tier vonalakat
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


# === parallel_snap segédek ===

def _grid_key(x, y, grid=0.01):
    return (round(x / grid), round(y / grid))


def apply_coord_map(line, coord_map, grid=0.01):
    '''
    Egy LineString minden csúcsát, amelynek rács-kulcsa benne van a coord_map-ben,
    átírjuk az új koordinátára. Így ha egy snapelt utca-csúcs koordinátája azonos
    egy másik (nem-snapelt) vonal csúcsával, az utóbbi is "elmozdul vele".
    '''
    if line is None or line.is_empty or not coord_map:
        return line
    new_coords = []
    changed = False
    for c in line.coords:
        k = _grid_key(c[0], c[1], grid)
        if k in coord_map:
            new = coord_map[k]
            new_coords.append((new[0], new[1]))
            if abs(new[0] - c[0]) > 1e-9 or abs(new[1] - c[1]) > 1e-9:
                changed = True
        else:
            new_coords.append((c[0], c[1]))
    if not changed:
        return line
    deduped = [new_coords[0]]
    for c in new_coords[1:]:
        if c != deduped[-1]:
            deduped.append(c)
    if len(deduped) < 2:
        return None
    return LineString(deduped)


def _snap_line_to_target(ln, target, SNAP_TOL, grid=0.01):
    '''
    Egy vonalat snap-el a target felé (shapely.ops.snap). Visszatér a snapelt vonallal
    és egy coord_map dict-tel (old grid-key → new (x,y)), ami a drag-along lépéshez kell.
    '''
    snapped = shp_snap(ln, target, SNAP_TOL)
    if snapped is None or snapped.is_empty:
        return ln, {}
    if snapped.geom_type != "LineString":
        return ln, {}
    old_coords = list(ln.coords)
    new_coords = list(snapped.coords)
    coord_map = {}
    if len(old_coords) == len(new_coords):
        for old, new in zip(old_coords, new_coords):
            if abs(new[0] - old[0]) > 1e-9 or abs(new[1] - old[1]) > 1e-9:
                coord_map[_grid_key(old[0], old[1], grid)] = (new[0], new[1])
    else:
        for old, new in ((old_coords[0], new_coords[0]), (old_coords[-1], new_coords[-1])):
            if abs(new[0] - old[0]) > 1e-9 or abs(new[1] - old[1]) > 1e-9:
                coord_map[_grid_key(old[0], old[1], grid)] = (new[0], new[1])
    return snapped, coord_map


def _is_parallel(line, target, SNAP_TOL, MIN_PARALLEL_LEN):
    if line is None or line.is_empty or target is None or target.is_empty:
        return False
    try:
        inter = line.intersection(target.buffer(SNAP_TOL))
    except Exception:
        return False
    return inter.length >= MIN_PARALLEL_LEN


def _snap_pass(subjects, target, SNAP_TOL, MIN_PARALLEL_LEN, drag_groups, grid=0.01):
    '''
    Egy snap-fázis: subjects → target (parallel-jelölés + snap + drag-along).
    drag_groups: list of (list-ref), amelyek mindegyikére alkalmazni kell a coord_map-et.
    Visszatér: (új subjects lista, snap-elt darabszám).
    '''
    if target is None or target.is_empty or not subjects:
        return subjects, 0
    coord_map = {}
    new_subjects = list(subjects)
    snapped_idxs = set()
    count = 0
    for i, s in enumerate(subjects):
        if s is None or s.is_empty:
            continue
        if _is_parallel(s, target, SNAP_TOL, MIN_PARALLEL_LEN):
            snapped, cmap = _snap_line_to_target(s, target, SNAP_TOL, grid=grid)
            new_subjects[i] = snapped
            snapped_idxs.add(i)
            coord_map.update(cmap)
            count += 1
    if coord_map:
        # nem-snap-jelölt subjects-en alkalmazzuk a drag-et
        for i in range(len(new_subjects)):
            if i in snapped_idxs:
                continue
            new_subjects[i] = apply_coord_map(new_subjects[i], coord_map, grid=grid)
        # és a többi listán (streets, helpers stb.)
        for lst in drag_groups:
            for j in range(len(lst)):
                lst[j] = apply_coord_map(lst[j], coord_map, grid=grid)
    return new_subjects, count


def parallel_snap(streets, helpers, boundary_lines, SNAP_TOL=3.0, MIN_PARALLEL_LEN=10.0, verbose=True):
    '''
    Hierarchikus parallel-snap:
      1) streets → boundary
      2) helpers → boundary
      3) helpers → streets (a (1)+(2) utáni frissült streets-en)
      4) helpers → helpers (a rövidebb a hosszabbhoz)
    A snap "magával húzza" a kapcsolódó éleket: minden lépés után a coord_map alapján
    a nem-snap-jelölt vonalak coord-jait is áthelyezzük.

    Bemenet: streets, helpers — LineString listák. boundary_lines — LineString lista.
    Visszatér: (streets, helpers) — szűrt, snap-elt listák.
    '''
    streets = [s for s in streets if s is not None and not s.is_empty]
    helpers = [h for h in helpers if h is not None and not h.is_empty]
    boundary_union = unary_union(boundary_lines) if boundary_lines else None

    stats = {'s_to_b': 0, 'h_to_b': 0, 'h_to_s': 0, 'h_to_h': 0}

    # 1) streets → boundary
    streets, stats['s_to_b'] = _snap_pass(streets, boundary_union, SNAP_TOL, MIN_PARALLEL_LEN,
                                          drag_groups=[helpers])

    # 2) helpers → boundary
    helpers, stats['h_to_b'] = _snap_pass(helpers, boundary_union, SNAP_TOL, MIN_PARALLEL_LEN,
                                          drag_groups=[streets])

    # 3) helpers → streets
    streets_union = unary_union([s for s in streets if s is not None and not s.is_empty])
    helpers, stats['h_to_s'] = _snap_pass(helpers, streets_union, SNAP_TOL, MIN_PARALLEL_LEN,
                                          drag_groups=[])  # streets target, nem dragoljuk

    # 4) helpers → helpers (rövidebb a hosszabbhoz)
    # Sorrend: csökkenő hossz; mindig az aktuális helper-re tesszük a TÖBBI uniojáraval szembeni snap-et,
    # de csak a hosszabbak halmaza (még nem snap-elt) a target.
    if len(helpers) >= 2:
        coord_map_h = {}
        snapped_idxs = set()
        order = sorted(range(len(helpers)),
                       key=lambda i: -(helpers[i].length if helpers[i] is not None and not helpers[i].is_empty else 0))
        for pos in range(len(order) - 1, 0, -1):
            i = order[pos]
            if i in snapped_idxs:
                continue
            h = helpers[i]
            if h is None or h.is_empty:
                continue
            longer_idxs = [order[p] for p in range(pos) if order[p] not in snapped_idxs]
            longer_lines = [helpers[j] for j in longer_idxs
                            if helpers[j] is not None and not helpers[j].is_empty]
            if not longer_lines:
                continue
            cand_union = unary_union(longer_lines)
            if _is_parallel(h, cand_union, SNAP_TOL, MIN_PARALLEL_LEN):
                snapped, cmap = _snap_line_to_target(h, cand_union, SNAP_TOL)
                helpers[i] = snapped
                snapped_idxs.add(i)
                coord_map_h.update(cmap)
                stats['h_to_h'] += 1
        if coord_map_h:
            for j in range(len(helpers)):
                if j in snapped_idxs:
                    continue
                helpers[j] = apply_coord_map(helpers[j], coord_map_h)
            for j in range(len(streets)):
                streets[j] = apply_coord_map(streets[j], coord_map_h)

    streets = [s for s in streets if s is not None and not s.is_empty and s.length > 1e-6]
    helpers = [h for h in helpers if h is not None and not h.is_empty and h.length > 1e-6]

    if verbose:
        print(f"[parallel_snap] s→b: {stats['s_to_b']}, h→b: {stats['h_to_b']}, "
              f"h→s: {stats['h_to_s']}, h→h: {stats['h_to_h']}  "
              f"(SNAP_TOL={SNAP_TOL}m, MIN_PARALLEL_LEN={MIN_PARALLEL_LEN}m)")

    return streets, helpers


def kapcsolas(edges, orange, blue, res_area, SNAP_TOL=10.0, MIN_PARALLEL_LEN=15.0, V_TOL=8.0, debug_path=None):
    '''
    Vonalháló összerakása parallel-aktivált, drag-connected snap-pel.

    1) Boundary + streets + helpers (blue/orange) clip-elése res_area-ra.
    2) (Debug) pre-snap union kiírása `2a_egyesites_pre_snap` rétegként, ha `debug_path` adott.
    3) `parallel_snap`: hierarchikus snap (streets→boundary, helpers→boundary,
       helpers→streets, helpers→helpers). Csak ott sül el, ahol két vonal
       legalább MIN_PARALLEL_LEN méteren át a SNAP_TOL bufferében fut. A snap
       a coord_map alapján "magával húzza" a kapcsolódó nem-snapelt éleket.
    4) Noding + V-fix + dangle-loop a snapelt linework-ön.
    '''
    boundary_line = res_area.boundary
    boundary_lines = extract_lines(boundary_line)

    if not boundary_lines:
        raise RuntimeError("Nem tudtam boundary vonalakat kinyerni (boundary_lines üres).")

    def _geoms(gs):
        return [g for g in gs.geometry if g is not None and not g.is_empty] if gs is not None and len(gs) else []

    # Csak az utcahálózatot kell res_area-ra vágni: a teljes települési hálózat a
    # lakott területen kívüli utakat is tartalmazza. A helperek viszont konstrukció
    # szerint a határon belül végződnek (orange: utcáig/határig, blue: határig),
    # ezért NEM klippeljük őket — a vágás csak elmozdítaná a végpontjukat.
    streets_raw = clip_lines(list(edges.geometry), res_area)
    blue_f    = _geoms(blue)
    orange_f  = _geoms(orange)

    # [DEBUG 1] hivatalos utcahálózat lakott területre vágva, egyesítve.
    if debug_path is not None and streets_raw:
        streets_union = unary_union(streets_raw)
        gpd.GeoDataFrame(geometry=extract_lines(streets_union), crs=edges.crs).to_file(
            debug_path, layer="1_utcahalozat", driver="GPKG"
        )

    # [DEBUG 4] a snap ELŐTTI, egyesített nyers vonalháló (boundary + utcák + helperek).
    if debug_path is not None:
        pre_lines = list(boundary_lines) + list(streets_raw) + list(blue_f) + list(orange_f)
        if pre_lines:
            pre_union = set_precision(unary_union(pre_lines), 0.01)
            gpd.GeoDataFrame(geometry=extract_lines(pre_union), crs=edges.crs).to_file(
                debug_path, layer="4_egyesitett_uthalozat", driver="GPKG"
            )

    # Parallel-snap: streets és helperek külön listák, boundary a horgony.
    helpers_in = list(blue_f) + list(orange_f)
    streets_snapped, helpers_snapped = parallel_snap(
        streets_raw, helpers_in, boundary_lines,
        SNAP_TOL=SNAP_TOL, MIN_PARALLEL_LEN=MIN_PARALLEL_LEN,
    )

    all_lines = list(boundary_lines) + list(streets_snapped) + list(helpers_snapped)
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

    # [DEBUG 5] a vonaltisztító függvények (snap + noding + V-fix + dangle-loop)
    # utáni, kész zárt vonalháló — az egyesites bemenete.
    if debug_path is not None and final_lines:
        gpd.GeoDataFrame(geometry=final_lines, crs=edges.crs).to_file(
            debug_path, layer="5_egyesitett_uthalozat_tisztitott", driver="GPKG"
        )

    return gpd.GeoSeries(final_lines, crs=edges.crs)


def egyesites(network_gs_proj, debug_path=None):
    '''
    A zárt vonalhurkokat poligonokká tölti (polygonize) és MINDEN cellát megtart.

    Szándékosan NINCS méret-alapú beolvasztás: a finom felbontást a
    szkid-besorolásig meg kell őrizni (két szomszédos, eltérő szavazókörű kis
    cella nem olvadhat egybe a kategorizálás előtt). Az üres (cím nélküli)
    cellákat a besorolás UTÁN az `ures_polyk_besorolasa` szívja fel a leghosszabb
    közös határú szomszédjukba.

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
        # A 6_nyers_poligonok réteget a poly_gen_pipeline írja ki, MIUTÁN a bypass
        # (út nélküli lakott) foltokat is hozzáfűzte — így a teljes cella-halmaz látszik.
        print(f"[egyesites] polygonize raw: {len(polygons_gdf)} poligon (beolvasztás nélkül, minden cella megmarad)")

    return polygons_gdf
