import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt

from polygon_fuggvenyek import *
from poligon_szk_fuggvenyek import *


def poly_gen_pipeline(VAROS, MAX_EXT=200.0, EPS=0.25, DIST_LIM=100.0, MIN_SEG=0.1, debug_path=None):

    # 1. poligonok létrehozása

    # letöltöm a szükséges adatokat osm-ről ()
    Gp, nodes, edges, res_p, city_boundary = letoltes(VAROS)
    res_cut = vag_residential_city(res_p, city_boundary)
    res_area, boundary, bypass_polys = res_area_es_boundary(res_cut, edges)

    if res_area is not None:
        orange = orange_gen(Gp, nodes, edges, MAX_EXT=MAX_EXT, EPS=EPS, MIN_SEG=MIN_SEG)
        blue = blue_gen(Gp, nodes, boundary, DIST_LIM=DIST_LIM, MIN_SEG=MIN_SEG)
        red = red_gen(res_area, edges, orange, blue, MIN_SEG=MIN_SEG)

        network_gs_proj = kapcsolas(edges, orange, blue, red, res_area)

        polygons = egyesites(network_gs_proj, debug_path=debug_path)
    else:
        # Egyetlen residential foltban sincs út → nincs mit polygonize-olni;
        # minden folt bypass-ágon érkezik.
        orange = gpd.GeoSeries([], crs=edges.crs)
        blue = gpd.GeoSeries([], crs=edges.crs)
        red = gpd.GeoSeries([], crs=edges.crs)
        network_gs_proj = gpd.GeoSeries([], crs=edges.crs)
        polygons = gpd.GeoDataFrame(geometry=[], crs=edges.crs)

    if bypass_polys:
        bypass_gdf = gpd.GeoDataFrame(geometry=bypass_polys, crs=edges.crs)
        bypass_gdf["geometry"] = bypass_gdf.geometry.apply(make_valid)
        bypass_gdf = bypass_gdf[bypass_gdf.geometry.type.isin(["Polygon", "MultiPolygon"])]
        polygons = pd.concat([polygons, bypass_gdf], ignore_index=True)
        polygons = gpd.GeoDataFrame(polygons, geometry="geometry", crs=edges.crs)

    if debug_path is not None:
        gpd.GeoDataFrame(
            geometry=edges.geometry.reset_index(drop=True), crs=edges.crs
        ).to_file(debug_path, layer="1_utcak", driver="GPKG")

        if len(orange) > 0:
            gpd.GeoDataFrame(geometry=list(orange), crs=orange.crs).to_file(
                debug_path, layer="1a_orange_zsakutca", driver="GPKG"
            )

        if len(blue) > 0:
            gpd.GeoDataFrame(geometry=list(blue), crs=blue.crs).to_file(
                debug_path, layer="1b_blue_hatarkozeli", driver="GPKG"
            )

        if len(red) > 0:
            gpd.GeoDataFrame(geometry=list(red), crs=red.crs).to_file(
                debug_path, layer="1c_red_hatarelhosszabbitas", driver="GPKG"
            )

        gpd.GeoDataFrame(geometry=list(network_gs_proj), crs=network_gs_proj.crs).to_file(
            debug_path, layer="2_utcak_hatarral", driver="GPKG"
        )

        polygons.to_file(debug_path, layer="4_poligonok_nyersen", driver="GPKG")

    return polygons



def generalas_pipeline(VAROS, debug=False, export=True):

    debug_path = f"./adatok/{VAROS}_debug_halozat.gpkg" if debug else None

    # beolvasom az összekapcsolt pontok df-et
    gdf = gpd.read_file('./adatok/osszekapcsolt_pontok_es_cimek.gpkg')

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
            res_p, city_boundary = letoltes_csak_res(VAROS)
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
            merged.to_file(f'./adatok/{VAROS}_szigetek_besorolt_3.gpkg', layer='network_polygons', driver='GPKG')
            gdf.to_file(f'./adatok/{VAROS}_cimek_besorolt.gpkg', layer='network_polygons', driver='GPKG')

        return gdf, merged

    # legenerálom (később beolvasom) a beazonosítandó település parcellákat
    try:
        gdf_szigetek = poly_gen_pipeline(VAROS, debug_path=debug_path)
    except NincsResidentialError as e:
        print(f"[{VAROS}] {e} — kihagyom.")
        return None, None

    # utcakövető méret-alapú feldarabolás a szkid-besorolás ELŐTT
    gdf_szigetek = meret_alapu_felosztas(gdf_szigetek)

    if debug_path is not None:
        gdf_szigetek.to_file(debug_path, layer="5_poligonok_felosztva", driver="GPKG")

    # szavazókörhöz rendelem a poligonokat
    # ideiglenesen: skip_felezes=True → vegyes cellák nem bomlanak tovább (többségi szkid)
    results = pontok_polygonban(gdf, gdf_szigetek, max_depth=45, skip_felezes=True)

    # azokat a területeket amiben nincsen cím hozzárendelem a legnagyobb átfedésű szomszéd szavazókörhöz
    results_filled = ures_polyk_besorolasa(results)

    # felez() split sub-nm FP-drift eltüntetése: coverage újra-topológizálása
    # boundary-noding + polygonize segítségével
    results_clean = rebuild_coverage(results_filled)

    # a kis parcellákat egyesítem egyetelen multypolygonba
    merged = polygonok_egyesitese(results_clean)

    if export:
        merged.to_file(f'./adatok/{VAROS}_szigetek_besorolt_4.gpkg', layer='network_polygons', driver='GPKG')
        gdf.to_file(f'./adatok/{VAROS}_cimek_besorolt.gpkg', layer='network_polygons', driver='GPKG')

    return gdf, merged



if __name__ == "__main__":
    VAROS = 'Kétvölgy'

    gdf, gdf_szigetek = generalas_pipeline(VAROS, debug=True)
