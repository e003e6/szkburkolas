# Szavazóköri poligonok létrehozása

**Poligonok létrehozása lakott területet fedve az utcahálózat alapján a választási címjegyzék kordinátái alapján**

A projtekt célja, hogy létrehozzon szavazóköri poligonokot amik
1. teljesen és koherensen (nem egymásba lógva, a lakott területet teljesen fedve) lefedik az OSM-től lekérdezett lakotterületi határokat
2. a nyilvános választási címjegyzék alapján dologzunk, lerakjuk a címjegyzékben szereplő címeket a térképre és ez alapján próbáljuk meghatározni a szavazókör határait. 

*A poligonoknak tökéltesen illeszkedniük kell, tökéletesen fedniük kell a lakott területi részt és soha nem lehetnek fedésben egymással a szavazóköri poligonok!*

Logikai alapok: 
1. a lakott terület feloszjuk az utcahálózat alapján (plusz segédvonalk amik összekötik az utcahálózatot a lakotterületi határral) kisebb részegységekre
2. a kisebb részegységekbe vetítjük a szavzókörök szerint besorolt címeket kordinátájukkal
3. ha egy kisebb egységben egyetlen szavazóköri címek vannak akkor ezt az egységet oda soroljuk
4. ha egy kisebb egységben több szavazókörhüz tartaozó címek vannak akkor tovább daraboljuk az egységet
5. a sehová nem tartozó egységeket hozzáreneljük az egyik szomszédos szavazóköri egységhez
6. az azonos szavazókörökhüz taratozó egységeeket összevonjuk egyetlen nagy poligonná


## Bementei adatok
Lokális bemenetek: 
1. címjegyzék tábla: címjegyzékben szereplő házszámmal ellátott címek, a hozzájuk tartozó kordináta és a besololásuk hogy melyik szavazókörbe tartozik a cím (szavazókör ID)


Címjegyzék tábla:
| id | szavazokorid | utca | hazszam | telepules | geometry |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1739892 | 1600517 | Petőfi Sándor utca | 1 | Ibafa | POINT (17.91557 46.15492) |
| 1739893 | 1600517 | Petőfi Sándor utca | 10 | Ibafa | POINT (17.91403 46.15501) |
| 1739894 | 1600517 | Petőfi Sándor utca | 11 | Ibafa | POINT (17.91308 46.15513) |
| 1739895 | 1600517 | Petőfi Sándor utca | 12 | Ibafa | POINT (17.91349 46.15506) |
| 1739897 | 1600517 | Petőfi Sándor utca | 14 | Ibafa | POINT (17.91289 46.15508) |

Lekérdezett bemenetek: 
1. OSM-től (osmnx csomag) lekérdezett lakotterületi határ a városra (ha az adott várhoz jelzi a program)
2. OSM-től (osmnx csomag) lekérzdett utcahálózat a városra (ha az adott várhoz jelzi a program)


## Program logikai működése V2

### 1. Adatletöltés (`letoltes`)
- OSM-ről letöltjük metrikus vetületbe (EOV) a:
  - Úthálózatot (gráf + nodes/edges GeoDataFrame)
  - `landuse=residential` poligonokat (lakott területek)
  - Hivatalos városhatárt (`geocode_to_gdf`)

### 2. Terület-előkészítés
- `vag_residential_city`: minden lakóterületi poligont levágja a városhatárra (metszés)
- `res_area_es_boundary`: megtartja csak azokat a lakóterületeket, ahol az úthálózat ténylegesen fut (metszet hossza > 0) → eredmény: `res_area` (unió poligon), `boundary` (határ vonal)

### 3. Összekötő vonalak generálása
- `orange_gen` – NARANCS vonalak (zsákutcák lezárása):
  - Megkeressük az összes zsákutca-végpontot (irányítatlan gráfban csomópontfok = 1)
  - Minden végpontból MAX_EXT métert sugárzunk az utca folytatásában kifelé
  - A sugár első metszéspontján (más utcával) megállunk → connector keletkezett

- `blue_gen` – KÉK vonalak (határ-közelség):
  - Minden csomóponttól, ami DIST_LIM-en belül van a lakóterület határától
  - Merőleges vonalat húzunk a legközelebbi határpontig → connector keletkezett

### 4. Hálózat összerakása (`kapcsolas`)
a. Összegyűjtjük az összes réteget: utcák + narancs + kék vonalak → levágjuk `res_area`-ra
b. SNAP: a vonalvégpontokat ráhúzzuk a lakóterületi határra (SNAP_TOL tolerancián belül)
c. STRIP & JOIN (dupla-fal kezelés):
   - A határral párhuzamosan futó utcák egy "dupla falat" képeznének (vékony sliver sávban)
   - A STRIP_TOL sávban lévő vonalvégpontokból explicit összekötőket húzunk a határra
   - Majd a teljes STRIP_TOL sávot kivágjuk a vonalhálóból → dupla fal eltűnik
d. Végső háló = clipped utcák + összekötők + boundary vonal
e. `unary_union` + `linemerge` → topológiailag egységes, zárt vonalháló

### 5. Poligonizálás + kis poligonok beolvasztása (`egyesites`)
a. `polygonize()`: a zárt vonalhurkok kitöltése → cellapoligonok keletkeznek
b. Kis poligonok (< MIN_AREA m²) iteratív beolvasztása:
   - Mindig a legkisebb poligont keressük
   - Azt a szomszédját keressük, amellyel a leghosszabb a közös határ
   - Beolvasztjuk → ismételjük amíg minden poligon ≥ MIN_AREA

### 6. Szavazókörökhöz rendelés
a. Betöltjük a választási címjegyzéket (geocódolt pontok szavazókör ID-val)
b. Szín hozzárendelés szavazókörönként: golden ratio hue elosztással → vizuálisan jól elkülönülő paletta
c. `pontok_polygonban` – minden cellához pont-szavazókör megfeleltetés:
   - 0 pont a cellában → `szavazokorid = None`
   - Pontosan 1 szavazókör → hozzárendeljük
   - Több szavazókör → `polygon_szkid_voronoi_vagas` (Voronoi-cellás felosztás)
   - `debug_path` megadásakor a `voronoi_sejtek` (poligon) és `voronoi_elek` (vonal)
     debug rétegeket is kiírja.
d. `polygon_szkid_voronoi_vagas` – vegyes cellák Voronoi-cellás felosztása:
   - **Magok** = a cella ÖSSZES címpontja, mindegyik a saját szkid-jével (nem csak a
     top-2, és nem centroidok) — így kettőnél több szavazókört is pontosan kezel.
   - A magokra Voronoi-diagramot számolunk (`shapely.ops.voronoi_diagram`, a cella
     bbox-ára kiterjesztve), majd minden territóriumot a cellára vágunk → a sík minden
     pontja a hozzá legközelebbi maghoz tartozik, a territórium szkid-jét a magjáé adja.
   - Az azonos szkid-ű szomszédos territóriumokat összevonjuk (`unary_union`, dissolve)
     → al-cellák. Ami elválasztó élként megmarad, az kizárólag a különböző szkid-ek
     közötti Voronoi-felezővonal, pontosan a pontfelhők közötti résbe ülve.
   - **Osztály-megőrző egyszerűsítés**: a nyers felezővonal sok apró szakaszból áll
     (mikro-hullámzás, minden szemközti pontpárhoz egy csúcs). Mivel a felhők között
     hézag van, bármely vonal jó, ami helyesen szétválasztja a szkid-eket. Ezért a
     vágóélt Douglas–Peucker-rel (`shapely.simplify(preserve_topology=True)`)
     egyszerűsítjük, és **bináris kereséssel cellánként** megkeressük a LEGNAGYOBB
     toleranciát, amelynél a vonalra újravágva minden pont a saját szkid-darabjában
     marad. A vágóél végpontjait (utca-horgony / csomópont) rögzítve hagyjuk, a
     határt érintő végeket kifelé hosszabbítjuk, hogy a húr biztosan átvágja a cellát
     (`shapely.ops.polygonize`, majd a darabokat a cellára vágjuk). Eredmény: kevés,
     nagy, egyenes szakasz — mint egy utcahatár.
   - Visszatérés: `(rows, cells, edges)` — `rows` a szkid szerint összevont al-cellák
     (a tényleges kimenet), `cells` az összes levágott Voronoi-cella (`voronoi_sejtek`
     debug réteg, a NYERS Voronoi-bontás), `edges` az EGYSZERŰSÍTETT vágóélek
     (`voronoi_elek` debug réteg).
   - Degenerált esetben (pl. egybeeső magok) a `pontok_polygonban` a teljes cellát a
     többségi szkid-hez sorolja (fallback). Adattisztítás nincs — jó bemenetet feltételez.
   - A korábbi rekurzív `polygon_tobb_szavazokor` és `felez` függvények egyelőre
     megmaradnak a fájlban referenciaként, de már nincsenek hívva.
e. `ures_polyk_besorolasa` – üres cellák feltöltése:
   - Érintkező szomszédok szavazóköreit megszámoljuk → a legtöbbször előforduló = nyertes
f. `polygonok_egyesitese` – szavazókörönkénti összevonás:
   - unary_union szavazókörönként
   - Ha MultiPolygon maradt: morfológiai zárás (buffer(+tol) → buffer(-tol)), növekvő tol
   - Ismétlés amíg egyetlen Polygon nem lesz, vagy max_tol el nem ér

### 7. Export
- QGIS-ba exportálás `.gpkg` formátumban (szavazókör határok + geocódolt címpontok)


**A célunk hogy a V2 működésmódot fejlesszük és egy jobb v3-at hozzunk létre de úgy hogy kövessük a leírt elveket és célokat**

## Program logikai működése V3

### 1. Vonalhálózat kialakítása (`burkolas_v3.py :: poly_gen_pipeline`)

#### 1.1. Terület-letöltés
- `letoltes`: OSM-ről letölti metrikus vetületbe az úthálózatot (gráf+edges), a `landuse=residential` poligonokat és a hivatalos városhatárt.
- `vag_residential_city`: minden residential poligont a városhatárra vág.
- `res_area_es_boundary`: megtartja csak azokat a residential foltokat, ahol az úthálózat ténylegesen fut → `res_area` (unió), `boundary` (vonal).

#### 1.2. Segédvonalak (három réteg)
- `orange_gen` — **narancs**: minden zsákutca-végpontból (fokszám=1) MAX_EXT (200 m) hosszú sugarat lő az utca folytatásában, és az első MÁS utcával való metszésig megy.
- `blue_gen` — **kék**: a határtól DIST_LIM (100 m) távolságon belüli csomópontokból induló utcákat a határra hosszabbítja (DIST_LIM × RAY_MULT hosszan sugároz, első határ-metszéspontnál áll meg).
- `red_gen` — **vörös**: a lakott területi határ MINDEN csúcsában a szomszédos él folytatását a polygon BELSEJE felé sugárral vizsgálja (szög ≤ SHARP_MAX_ANGLE_DEG, probe contains); megáll az első utca/orange/blue/határ metszésnél. A kinyúló városrészek alapját vágja el, így nem képződnek elnyúló tüske-poligonok.

#### 1.3. Vonalháló tisztítása (`kapcsolas`)
- **Alapelv**: a helperek (red/blue/orange) a generálásban `ray.intersection(target)`-tel készülnek, tehát a végpontjuk PONTOSAN a célvonalon ül. `unary_union` önmagában elvégzi a rendes noding-ot (közös csúcs beszúrása a célvonalba), így **semmilyen generálás-utáni snap nem kell** — minden snap csak elmozdítaná a végpontot az exact találati pontról, és a helpert dangle-lé tenné.
- `szur_hatarral_parhuzamos`: a határ PARALLEL_TOL (15 m) bufferén belül teljesen fekvő utcákat törli (dupla-fal-sliver megelőzés). A helper vonalakat cutterként használja: ha egy parallel utcát red/blue/orange keresztez, az utcát a keresztezési pontnál szétvágja, és a helper-horgonyos sub-szakaszt akkor is tartja, ha a bufferen belül van — így a helper sosem lóg a levegőben törölt utca-részlet miatt.
- `unary_union` + `set_precision 0.01`: az összes vonalat (határ + szűrt utcák + red + blue + orange) egyetlen noding-lépésben rántja össze, 1 cm-es rácsra rögzítve.
- `v_shape_fix`: a noded hálón a közös végpontból induló, közel-párhuzamos vonalpárok közül a rövidebbet eltávolítja, ha a hosszabb V_TOL (3 m) távolságon belül fut a rövidebb MÁSIK végpontjától ÉS az a másik vég dangling (fokszám=1). Csak valós V-zászlókat vág, terhelő szakaszokat nem.
- **Dangle-loop**: iteratív dead-end-eltávolítás `polygonize_full` dangle-kimenete alapján, amíg nincs több (max 20 iteráció). Ezzel a `kapcsolas` tiszta, zárt hálót ad vissza — az `egyesites` csak poligont épít.

#### 1.5. Poligonizálás (`egyesites`)
- `polygonize`: a zárt vonalhurkok kitöltése.
- Kis poligonok iteratív beolvasztása (< MIN_AREA m², default 500): mindig a legkisebb cellát az azzal legnagyobb közös határú szomszédba olvasztja, amíg minden cella ≥ MIN_AREA.

### 2–6. Szavazókörökhöz rendelés (`generalas_pipeline`)
A V2 lépések változatlanok: `meret_alapu_felosztas` → `pontok_polygonban` → (`polygon_tobb_szavazokor`) → `ures_polyk_besorolasa` → `rebuild_coverage` → **`hibas_szigetek_torlese`** → `polygonok_egyesitese`. Részletek a V2 szekcióban.

**Végső adattisztítás (`hibas_szigetek_torlese`)**: a `rebuild_coverage` és a `polygonok_egyesitese` közé illesztve. Hibás forrásadat miatt (rossz koordináta → rossz `szavazokorid`) a coverage egy-egy cellája rossz szavazókörhöz kerülhet, ami az egyesítés után a szavazókör távoli, EGYETLEN címet tartalmazó szigeteként jelenik meg (egy másik szk területébe ágyazva). A függvény szavazókörönként szigetekre bont (`_extract_polys`), megszámolja a beleeső címeket, és a `min_cimek` (alapért. 1) küszöb alatti szigetek celláit `szavazokorid=None`-ra állítja, majd az `ures_polyk_besorolasa`-val a leghosszabb közös határú szomszédhoz (= körülvevő szk) sorolja át. Biztonsági korlát: egy szavazókör ÖSSZES szigetét sosem törli (a legnagyobb területűt megtartja). A `gdf` címpontokat szándékosan nem módosítja (a koordináta úgyis hibás).

**Egy-szavazóköri shortcut**: a `generalas_pipeline` a címjegyzék településre-szűrése után megnézi hány egyedi `szavazokorid` van. Ha pontosan 1, a teljes vonalháló-generálás (`poly_gen_pipeline`, `meret_alapu_felosztas`, `pontok_polygonban`, stb.) kimarad — csak a residential poligont kérjük le (`letoltes_csak_res`, útgráf nélkül), városhatárra vágjuk (`vag_residential_city`), és az unió lesz a szavazóköri poligon.

### 7. Export
Ugyanaz mint a V2-nél: `.gpkg` QGIS-be.

## Ismert OSM-hiba: üres drive-gráf kis településeknél

`ox.graph_from_place(PLACE, network_type="drive")` aprófalvakra (pl. Ibafa) üres gráfot adhat vissza → az `ox.project_graph` ilyenkor `ValueError: Graph contains no edges`-szel elhasal. Lehetséges okok:
- A Nominatim a falu nevére túl szűk vagy pont-alapú geocode-ot ad, nem olyan polygont amiben utcák vannak.
- A `drive` network_type csak motorizált-járható highway-eket tart meg (`motorway…residential, living_street`); ha a falu utcái `highway=track` vagy `highway=service`, kiesnek.
- A default `retain_all=False` eldobja a nem-összefüggő komponenseket.

**Következmény**: a `letoltes` nem hívható vakon minden településre. Az egy-szavazóköri shortcut pont ezt kerüli el (csak residential + városhatár kell, útgráf nincs). Több-szavazóköri településnél, ha `letoltes` elhasal drive-gráf miatt, először érdemes ellenőrizni a tagging-et OSM-en, ne fallback-eljünk csendben más network_type-ra.

**Kötelező invariáns**: ha egy településre nincs `landuse=residential` az OSM-ben (vagy a városhatáron belül nem marad), a függvény RuntimeError-rel **leáll**. SOHA nem esünk vissza a sima településhatárra helyettesítőként — ez akkor is igaz ha több szavazókör van a településen. A `letoltes` és `letoltes_csak_res` is explicit hibát dob ilyenkor.

## Megoldandó probléma: `res_area_es_boundary` tangenciálisan ejti a diszjunkt lakott foltokat

**Tünet**: Tab esetében a település 2 különálló `landuse=residential` poligonból áll. A futás után a második lakott egységhez *egyáltalán nem készült* poligon — sem a `2_utcak_hatarral` debug layerben (azaz nincs ott a határgyűrűje), sem a későbbi kimenetekben. Utcák viszont látszanak arrafelé az `1_utcak` layerben.

**Gyökérok**: a `res_area_es_boundary` (`polygon_fuggvenyek.py:195`) szűrője:
```python
keep_polys = [p for p in polys if roads_union.intersection(p).length > 0]
```
Tab második foltjánál OSM-ben a `landuse=residential` polygon szorosan a beépített telkek köré van rajzolva, és az utcák a telekvonal MELLETT futnak (az utca *kihagyva*, a határ az utca mellé megy). Így `roads_union.intersection(p)` csak pontszerű tangenciális érintéseket ad → `length == 0` → a poligon kiesik a `keep_polys`-ból, nem kerül be a `res_area`-ba, nincs határgyűrű, a `polygonize` számára nem létezik.

**Miért nem véletlen hiba**: ez a szűrő logikailag jogos céllal van ott (elhagyott, valóban úthálózat nélküli residential foltok kiszűrése), de a `length > 0` kritérium túl szigorú: egyetlen tangenciális érintés, vagy utca melletti (nem belsejében futó) rajzolás már kidobja a valódi lakott foltot is.

**A fix lehetséges irányai** (nem eldöntve):
- **Tolerált távolság**: `roads_union.distance(p) < TOL` (pl. 5–10 m) — érintkező és majdnem-érintkező utakat is elfogad.
- **Node-alapú teszt**: legalább egy `nodes.geometry` pont essen a `p.buffer(TOL)`-ba.
- **Hibrid**: `length > 0` **vagy** node-alapú feltétel.

A node-alapú (vagy hibrid) feltétel azért vonzóbb, mint a puszta távolság, mert jobban ellenáll annak hogy egy vékony sliver (`vag_residential_city` határ-vágásból maradt forgács) mellett véletlenül fusson egy utca — node kell a polygon közelébe, nem csak él.

**Mellékhatás amit figyelni kell**: ha a szűrőt lazítjuk, bekerülhetnek olyan kis OSM-ből ottfelejtett residential foltok, amikben tényleg nincs település. A `meret_alapu_felosztas` és a `pontok_polygonban` üres-cella kezelése (`ures_polyk_besorolasa`) ezeket elnyelné vagy üresként hagyná — érdemes ellenőrizni, mielőtt kiadjuk.

## Nyitott kérdés: végleges kimeneti vetület (web-megjelenítéshez)

**Kontextus**: a poligonokat később **webes térképen** fogjuk megjeleníteni, tehát a kimeneti CRS-t ehhez kell véglegesíteni. Jelenleg:
- `osmnx.project_graph()` városonként lokális UTM-zónába vetít (Magyarországon EPSG:32633 vagy EPSG:32634, attól függően hogy a város nyugatra vagy keletre esik) → több várost batch-elve (`burkolas_multiple.py`) nem konkatenálható CRS-egyeztetés nélkül.
- A `burkolas_multiple.py` jelenleg `EPSG:23700` (EOV) target CRS-re vetít. Ez Magyarország hivatalos metrikus vetülete, geometriailag helyes, DE **HD72 dátumon van**, így QGIS/webes megjelenítéskor a WGS84-alapú web-vetületekre (`EPSG:3857`, `EPSG:4326`) váltáskor dátum-transzformáció kell — QGIS ezért dob dialógust betöltéskor.

**Mit kell eldönteni, mielőtt éles használatra kiadjuk**:
1. **Tároljuk EOV-ban** (jelenleg ez van): geometriailag pontos, de minden web-export előtt kell egy `to_crs('EPSG:4326')` lépés, és figyelni kell a dátum-transzformáció helyes beállítására.
2. **Tároljuk UTM 34N-ben** (`EPSG:32634`): WGS84 alapú → nincs dátumváltás, a 3857/4326-ra váltás tiszta. A zóna-33-ba eső nyugat-magyarországi települések szélén <0,1% torzulás — érdemi hatás nincs.
3. **Tároljuk közvetlenül WGS84-ben** (`EPSG:4326`): web-ready, semmilyen konverzió nem kell exportkor. Hátrány: nem metrikus, ha utólag területet/puffert akarunk számolni a kimeneten, át kell vetíteni.
4. **Tároljuk 3857-ben**: közvetlen web-tile CRS, nulla konverzió. Hátrány: sarki/széli torzulás, nem metrikus.

**Ajánlott irány** (nem eldöntve): a pipeline belsejében maradjon a metrikus számítás (UTM vagy EOV), de a `burkolas_multiple.py` végső egyesített kimenetét célszerű `EPSG:4326`-ra írni — ez a de facto web-szabvány, minden leaflet/mapbox/maplibre közvetlenül eszi. Döntés előtt tisztázandó: a web-frontend milyen formátumot vár (GeoJSON? Vector tile? MVT?), és kell-e metrikus attribútum (pl. terület m²-ben) a kimenetben.

