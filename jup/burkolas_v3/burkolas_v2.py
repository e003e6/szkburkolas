import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt

from polygon_fuggvenyek import *
from poligon_szk_fuggvenyek import *


def poly_gen_pipeline(VAROS, MAX_EXT=200.0, EPS=0.25, DIST_LIM=100.0, MIN_SEG=0.1):

    # 1. poligonok létrehozása
    
    # letöltöm a szükséges adatokat osm-ről ()
    Gp, nodes, edges, res_p, city_boundary = letoltes(VAROS)
    res_cut = vag_residential_city(res_p, city_boundary)
    res_area, boundary = res_area_es_boundary(res_cut, edges)
    
    orange = orange_gen(Gp, nodes, edges, MAX_EXT=MAX_EXT, EPS=EPS, MIN_SEG=MIN_SEG)
    blue = blue_gen(nodes, boundary, DIST_LIM=DIST_LIM, MIN_SEG=MIN_SEG)

    network_gs_proj = kapcsolas(edges, orange, blue, res_area)

    return egyesites(network_gs_proj)



def generalas_pipeline(VAROS):

    # beolvasom az összekapcsolt pontok df-et
    gdf = gpd.read_file('./adatok/osszekapcsolt_pontok_es_cimek.gpkg')

    # szűröm városra és választásra
    gdf = gdf.query('telepules == @VAROS')                         

    # hozzárendelem a színeket a szavazókörökhöz (qgis vizualizációhoz)
    gdf = add_color_to_gdf(gdf)

    # legenerálom (később beolvasom) a beazonosítandó település parcellákat
    gdf_szigetek = poly_gen_pipeline(VAROS)

    # szavazókörhöz rendelem a poligonokat
    results = pontok_polygonban(gdf, gdf_szigetek, max_depth=45)

    # azokat a területeket amiben nincsen cím hozzárendelem a legnagyobb átfedésű szomszéd szavazókörhöz
    results_filled = ures_polyk_besorolasa(results)

    # felez() split sub-nm FP-drift eltüntetése: coverage újra-topológizálása
    # boundary-noding + polygonize segítségével
    results_clean = rebuild_coverage(results_filled)

    # a kis parcellákat egyesítem egyetelen multypolygonba
    merged = polygonok_egyesitese(results_clean)


    # export qgis-be
    merged.to_file(f'./adatok/{VAROS}_szigetek_besorolt_3.gpkg', layer='network_polygons', driver='GPKG')

    # hogy rárakjam qgis-be a címeket kiexprtálom ezt is
    gdf.to_file(f'./adatok/{VAROS}cimek_besorolt.gpkg', layer='network_polygons', driver='GPKG')

    return gdf, gdf_szigetek



if __name__ == "__main__":
    VAROS = 'Tököl'

    gdf, gdf_szigetek = generalas_pipeline(VAROS)

    
