import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt

from polygon_fuggvenyek import *
from poligon_szk_fuggvenyek import *
from polygon_io import olvas_varos
from polir import polir


def poly_gen_pipeline(VAROS, MAX_EXT=200.0, EPS=0.25, DIST_LIM=100.0, MIN_SEG=0.1, debug_path=None):

    # 1. poligonok létrehozása

    # OSM cache-ből (data/nyers_data/) — a tényleges letöltés az adatstruktua/osm_letoltes.py
    Gp, nodes, edges, res_p, city_boundary = olvas_varos(VAROS, csak_lakott=False)
    res_cut = vag_residential_city(res_p, city_boundary)
    res_area, boundary, bypass_polys = res_area_es_boundary(res_cut, edges)

    if res_area is not None:
        orange = orange_gen(Gp, nodes, edges, boundary, MAX_EXT=MAX_EXT, EPS=EPS, MIN_SEG=MIN_SEG)
        blue = blue_gen(Gp, nodes, boundary, DIST_LIM=DIST_LIM, MIN_SEG=MIN_SEG)

        network_gs_proj = kapcsolas(edges, orange, blue, res_area, debug_path=debug_path)

        polygons = egyesites(network_gs_proj, debug_path=debug_path)
    else:
        # Egyetlen residential foltban sincs út → nincs mit polygonize-olni;
        # minden folt bypass-ágon érkezik.
        orange = gpd.GeoSeries([], crs=edges.crs)
        blue = gpd.GeoSeries([], crs=edges.crs)
        network_gs_proj = gpd.GeoSeries([], crs=edges.crs)
        polygons = gpd.GeoDataFrame(geometry=[], crs=edges.crs)

    if bypass_polys:
        bypass_gdf = gpd.GeoDataFrame(geometry=bypass_polys, crs=edges.crs)
        bypass_gdf["geometry"] = bypass_gdf.geometry.apply(make_valid)
        bypass_gdf = bypass_gdf[bypass_gdf.geometry.type.isin(["Polygon", "MultiPolygon"])]
        polygons = pd.concat([polygons, bypass_gdf], ignore_index=True)
        polygons = gpd.GeoDataFrame(polygons, geometry="geometry", crs=edges.crs)

    # Debug rétegek:
    #   [0] 0_lakott_terulet_hatar             — itt (residential, hivatalos városhatárra vágva)
    #   [1] 1_utcahalozat                      — kapcsolas (lakott területre vágva)
    #   [2] 2_narancs_vonalak                  — itt
    #   [3] 3_kek_vonalak                      — itt
    #   [4] 4_egyesitett_uthalozat             — kapcsolas (snap előtti nyers háló)
    #   [5] 5_egyesitett_uthalozat_tisztitott  — kapcsolas (tisztítás után)
    #   [6] 6_nyers_poligonok                  — itt (polygonize + bypass-foltok, a besorolás bemenete)
    if debug_path is not None:
        if len(res_cut) > 0:
            res_cut_hatar = unary_union(list(res_cut.geometry)).boundary
            gpd.GeoDataFrame(geometry=extract_lines(res_cut_hatar), crs=edges.crs).to_file(
                debug_path, layer="0_lakott_terulet_hatar", driver="GPKG"
            )

        if len(orange) > 0:
            gpd.GeoDataFrame(geometry=list(orange), crs=orange.crs).to_file(
                debug_path, layer="2_narancs_vonalak", driver="GPKG"
            )

        if len(blue) > 0:
            gpd.GeoDataFrame(geometry=list(blue), crs=blue.crs).to_file(
                debug_path, layer="3_kek_vonalak", driver="GPKG"
            )

        if len(polygons) > 0:
            polygons.to_file(debug_path, layer="6_nyers_poligonok", driver="GPKG")

    return polygons, edges



def generalas_pipeline(VAROS, debug=False, export=True):

    debug_path = f"./adatok/{VAROS}_debug.gpkg" if debug else None

    # beolvasom az összekapcsolt pontok df-et
    gdf = gpd.read_file('../data/work_data/osszekapcsolt_pontok_es_cimek_v2.gpkg')

    # szűröm városra és választásra
    gdf = gdf.query('telepules == @VAROS')

    # ha nincs egyetlen szavazóköri cím sem ezzel a településnévvel, kilépünk
    # (nem dobunk hibát, hogy batch-futtatásnál ne álljon le az egész)
    if gdf.empty:
        print(f"[{VAROS}] Nincsen egyetlen szavazóköri cím sem az adatbázisban ilyen településnévvel — kihagyom.")
        return None, None

    # hozzárendelem a színeket a szavazókörökhöz (qgis vizualizációhoz)
    gdf = add_color_to_gdf(gdf)

    # ha a teljes településen egyetlen szavazókör van, a lakott
    # területi poligont adjuk vissza egyetlen szavazóköri poligonként
    if gdf["szavazokorid"].nunique() == 1:
        # csak residential + városhatár — útgráf-lekérdezés kimarad
        try:
            res_p, city_boundary = olvas_varos(VAROS, csak_lakott=True)
        except NincsResidentialError as e:
            print(f"[{VAROS}] {e} — kihagyom.")
            return None, None
        res_cut = vag_residential_city(res_p, city_boundary)
        res_area = unary_union(list(res_cut.geometry))

        szkid = gdf["szavazokorid"].iloc[0]
        color = gdf["color"].iloc[0]

        merged = gpd.GeoDataFrame(
            [{"szavazokorid": szkid, "color": color, "geometry": res_area}],
            geometry="geometry", crs=res_cut.crs,
        )

        if export:
            merged.to_file(f'./adatok/{VAROS}_szigetek_besorolt_4.gpkg', layer='network_polygons', driver='GPKG')
            gdf.to_file(f'./adatok/{VAROS}_cimek_besorolt.gpkg', layer='network_polygons', driver='GPKG')

        return gdf, merged

    # legenerálom (később beolvasom) a beazonosítandó település parcellákat
    try:
        gdf_szigetek, edges = poly_gen_pipeline(VAROS, debug_path=debug_path)
    except NincsResidentialError as e:
        print(f"[{VAROS}] {e} — kihagyom.")
        return None, None

    # Megjegyzés: a méret-alapú előfelosztás (meret_alapu_felosztas) szándékosan
    # KIMARAD — önkényes bbox-felezéseket vinne a cellákba, miközben a tényleges
    # szavazókör-határt az utcahálózati cellák + a pont-alapú módszerek adják.

    # szavazókörhöz rendelem a poligonokat
    # vegyes (több szkid-ű) cellák Voronoi-cellás felosztása (polygon_szkid_voronoi_vagas);
    # debug=True esetén a voronoi_sejtek / voronoi_elek rétegeket is kiírja a debug GPKG-be
    results = pontok_polygonban(gdf, gdf_szigetek, skip_felezes=False, debug_path=debug_path)

    # azokat a területeket amiben nincsen cím hozzárendelem a legnagyobb átfedésű szomszéd szavazókörhöz
    results_filled = ures_polyk_besorolasa(results)

    # felez() split sub-nm FP-drift eltüntetése: coverage újra-topológizálása
    # boundary-noding + polygonize segítségével
    results_clean = rebuild_coverage(results_filled)

    # végső adattisztítás: a hibás forrásadat miatt keletkező egy-címes
    # szavazókör-szigeteket törlöm, területüket a körülvevő (leghosszabb közös
    # határú) szomszéd szavazókör nyeli el
    results_clean = hibas_szigetek_torlese(results_clean, gdf)

    # a kis parcellákat egyesítem egyetelen multypolygonba
    merged = polygonok_egyesitese(results_clean)

    # POLÍR: a kész szkid-poligonok határhálózatának kozmetikai csiszolása az
    # utcahálózat alapján, osztály-megőrzéssel (utolsó lépés, az export előtt)
    merged = polir(merged, gdf, edges, debug_path=debug_path)

    if export:
        merged.to_file(f'./adatok/{VAROS}_szigetek_besorolt_5.gpkg', layer='network_polygons', driver='GPKG')
        gdf.to_file(f'./adatok/{VAROS}_cimek_besorolt.gpkg', layer='network_polygons', driver='GPKG')

    return gdf, merged



if __name__ == "__main__":
    VAROS = 'Halásztelek'       # Kétvölgy

    gdf, gdf_szigetek = generalas_pipeline(VAROS, debug=True)
