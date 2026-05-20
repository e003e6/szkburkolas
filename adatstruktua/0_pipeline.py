from ms_building_download import ms_building_download
from ms_building_feldolgozas import ms_building_feldolgozas
from overpass_download import overpass_download
from osm_epulet_pont_egyesites import overpass_feldolgozas
from gm_feldolgozas import gm_feldolgozas
from osm_gmaps_egyesites import osm_gmaps_egyesites
from gm_osm_lekerdezes import gm_osm_lekerdezes
from szkid_cim_kapcsolas import szkid_cim_kapcsolas
from ms_ures_sample import ms_ures_sample



def pipeline():

    # letölti az aktuális Microsft épület poligon dataset-et
    #    1. microsoft/* -> microsoft épület poligonok magyaroszágon chunk-okban
    ms_building_download()

    # Microsoft épület poligon-ok betöltése és google maps lekérdezések létrehozása
    #    1. lekerdezendo_kordinatak -> microsoft poligonok alapján azok a kordináták amit le kell kérdezni google-el
    #    2. ms_minden_epulet -> minden microsoft épület elmentve
    ms_building_feldolgozas()
    
    # Letölti a legfrisebb adatokat az overpass-ból (pontok és épületek)
    #    1. overpass_download/* -> épületek és pontok minden régióra külön geojson
    overpass_download()

    # Feldolozza a letöltött overpass adatokat
    #    1. osm_cim_kordinata -> minden osm-ben létező pontos címet tartalmazó kordináta
    #    2. osm_ures_epulet -> minden olyan osm épület amiben nincsen pontos cím kordináta
    #    3. osm_minden_epulet -> minden osm épület exportálva (cím és nem címes is)
    overpass_feldolgozas()

    
    # Microsoft éppletek van ahol nincsenek, ezeket a chunkokat azonosítjuk
    #    1. 
    ms_ures_sample()


    # A lekérdezett nyers google adatokat feldolgozza 
    #    1. teljes_ms_google_tisztott -> minden google lerkérdezett cím ahol van utca szám
    gm_feldolgozas()

    # Összekapcsolja a google és az osm kordinátákat egy df-be
    #    osm_gmaps_merged -> osm és gmaps vagyis minden olyan pont összekapcsolva ahol van pontos cím
    osm_gmaps_egyesites()

    # Google maps második kör, ellenőrzöm hogy van e új pont amit még le tudnék kérdezni
    #    1. lekerdezendo_kordinatak_osm -> azok az osm poligonok lekérdezési formában ahol nincsen pontos cím megadva
    gm_osm_lekerdezes()

    # Összekapcsolom a két fontos adathalmazomat hogy a kordinátákhoz szavazókör id-t tudjak rendelni
    # osm_gmaps_merged: kordináta-címeket --- címek-szavazókörid: választási címjegyzék
    szkid_cim_kapcsolas()




if __name__ == '__main__':
    pipeline()
