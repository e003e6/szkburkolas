from ms_building_download import ms_building_download
from ms_building_feldolgozas import ms_building_feldolgozas
from overpass_download import overpass_download
from osm_epulet_pont_egyesites import overpass_feldolgozas
from gm_feldolgozas import gm_feldolgozas
from osm_gmaps_egyesites import osm_gmaps_egyesites
from gm_osm_lekerdezes import gm_osm_lekerdezes
from szkid_cim_kapcsolas import szkid_cim_kapcsolas, szkid_cim_kapcsolas_v2
from ms_ures_sample import ms_ures_sample
from osm_letoltes import osm_letoltes



def pipeline():

    # letölti az aktuális Microsft épület poligon dataset-et
    #  Output:
    #    1. microsoft_download/* -> microsoft épület poligonok magyaroszágon chunk-okban
    ms_building_download()

    # Microsoft épület poligon-ok betöltése és google maps lekérdezések létrehozása
    #  Input:
    #    1. microsoft_download/*
    #  Output:
    #    1. lekerdezendo_kordinatak -> microsoft poligonok alapján azok a kordináták amit le kell kérdezni google-el
    #    2. ms_minden_epulet -> minden microsoft épület elmentve
    ms_building_feldolgozas()
    
    # Letölti a legfrisebb adatokat az overpass-ból (pontok és épületek)
    #  Output:
    #    1. overpass_download/* -> épületek és pontok minden régióra külön geojson
    overpass_download()

    # Feldolozza a letöltött overpass adatokat
    #  Input: 
    #    1. overpass_download/*
    #  Output:
    #    1. osm_cim_kordinata -> minden osm-ben létező pontos címet tartalmazó kordináta
    #    2. osm_ures_epulet -> minden olyan osm épület amiben nincsen pontos cím kordináta
    #    3. osm_minden_epulet -> minden osm épület exportálva (cím és nem címes is)
    overpass_feldolgozas()

    # Letölti az összes településhez a burkoláshoz szükséges fájlokat (várshatárok és utcahálózatok)
    #  Input: 
    #    1. varosok.txt
    #  Output:
    #    1. varos_lakottterulet_hatar -> a városk latkott területi határai
    #    2. varos_kozigazgatasi_hatar -> a városok közigazgatási határai
    #    3. varos_nodes -> utcahálózat pontjai
    #    4. varos_edges -> utcahálózat élei
    osm_letoltes()

    # Microsoft éppletek van ahol nincsenek, ezeket a chunkokat azonosítjuk
    #  Input: 
    #    1. varos_lakottterulet_hatar
    #    2. varos_kozigazgatasi_hatar
    #    3. ms_minden_epulet
    #  Output:
    #    1. lekerdezendo_kordinatak_kigyaott_teruletek -> ahol nincsen ms poligon onnan vett véletlen pontok lakott területen belül
    ms_ures_sample()


    # A lekérdezett nyers google adatokat feldolgozza 
    #  Input:
    #    1. orszagos_teljes_ms_google.jsonl
    #    2. 
    #    3. 
    #  Output:
    #    1. teljes_ms_google_tisztott -> minden google lerkérdezett cím ahol van utca szám
    gm_feldolgozas()

    # Összekapcsolja a google és az osm kordinátákat egy df-be
    #  Input:
    #    1. teljes_ms_google_tisztott
    #    2. osm_cim_kordinata
    #  Output:
    #    1. osm_gmaps_merged -> osm és gmaps vagyis minden olyan pont összekapcsolva ahol van pontos cím
    osm_gmaps_egyesites()

    # Google maps második kör, ellenőrzöm hogy van e új pont amit még le tudnék kérdezni
    #  Output:
    #    1. lekerdezendo_kordinatak_osm_epulet -> azok az osm poligonok lekérdezési formában ahol nincsen pontos cím megadva
    gm_osm_lekerdezes()

    # Összekapcsolom a két fontos adathalmazomat hogy a kordinátákhoz szavazókör id-t tudjak rendelni
    #  Input:
    #    1. osm_gmaps_merged -> koordinátás (anchor) pontok pontos címmel
    #    2. nyers választási címjegyzék csv -> címek szavazókör id-vel
    #  Output:
    #    1. osszekapcsolt_pontok_es_cimek_<év> -> koordinátás pontok szavazokorid-dal feldúsítva
    #       (a burkolas_v3 által módosítás nélkül olvasható séma)
    #  Két bemenet: input címjegyzék + output út/név — más futáshoz csak ezt a kettőt kell cserélni.

    # 2022 cim generalas (2014–2022-es formátum)
    szkid_cim_kapcsolas('../data/nyers_data/valasztas_cimek_2022.csv',
                        '../data/work_data/osszekapcsolt_pontok_es_cimek_2022')

    # 2014 cim generalas (erre még nincsen csv)
    szkid_cim_kapcsolas('../data/nyers_data/valasztas_cimek_2014.csv',
                        '../data/work_data/osszekapcsolt_pontok_es_cimek_2014')

    # 2026 cim generalas (2026-os formátum, irsz-kulcsos)
    szkid_cim_kapcsolas_v2('../data/nyers_data/szavkor2026.csv',
                           '../data/work_data/osszekapcsolt_pontok_es_cimek_2026')


if __name__ == '__main__':
    pipeline()
