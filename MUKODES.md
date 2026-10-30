# Szavazóköri poligonok – a teljes rendszer működése

> Ez a dokumentum **nem kódleltár**. Azt írja le, hogyan gondolkodik a rendszer: honnan jönnek az
> adatok, mi történik velük, és lépésről lépésre hogyan áll elő a végén minden szavazókör
> határa. A függvény- és fájlnevek csak zárójelben szerepelnek (`fájl.py`), hogy vissza lehessen
> keresni őket a kódban. A pszeudokód-blokkok embereknek szólnak, nem futtathatók.
>
> A leírás a **ténylegesen futó kódot** követi (2026. októberi állapot). Ahol a régebbi
> `szburkolas/claude.md` mást mond, a kód az irányadó – az eltérések listája az 5.3 fejezetben van.

---

## Tartalom

0. [Összefoglaló – mit csinál a rendszer](#0-összefoglaló--mit-csinál-a-rendszer)
1. [Adatok és források](#1-adatok-és-források)
2. [A. fázis – Adatstruktúra: címes pontok előállítása](#2-a-fázis--adatstruktúra-címes-pontok-előállítása)
3. [B. fázis – BURKOLÁS: a szavazóköri poligonok előállítása](#3-b-fázis--burkolás-a-szavazóköri-poligonok-előállítása)
4. [Ismert korlátok, nyitott kérdések](#4-ismert-korlátok-nyitott-kérdések)
5. [Függelékek](#5-függelékek)

---

## 0. Összefoglaló – mit csinál a rendszer

**A cél:** Magyarország minden településére megrajzolni a szavazókörök határait térképen.

A nyilvános választási címjegyzék csak annyit mond, hogy *melyik cím melyik szavazókörbe tartozik*
(pl. „Ibafa, Petőfi Sándor utca 12. → 1600517-es szavazókör”). Térképi határ nincs hozzá. A rendszer
ezt a határt **rekonstruálja**: a címeket koordinátákhoz köti, a lakott területet az utcahálózat
mentén apró cellákra bontja, a cellákat a bennük lévő címek alapján szavazókörökhöz rendeli, majd a
cellákat szavazókörönként összeolvasztja.

**A végeredménynek három kötelező tulajdonsága van:**

1. **Teljes lefedés** – a település lakott területén nincs olyan pont, ami egyik szavazókörhöz sem tartozik.
2. **Nincs átfedés** – két szavazókör poligonja sehol nem lóg egymásba.
3. **Csak lakott terület** – a poligonok az OSM `landuse=residential` területet fedik, nem a teljes közigazgatási határt.
   Ha egy településnek nincs lakott terület-poligonja, a rendszer **inkább kihagyja**, minthogy a
   településhatárral helyettesítse.

**Két nagy fázis van:**

- **A. fázis – Adatstruktúra** (`adatstruktua/`): minden létező forrásból (OSM, Google Maps,
  Microsoft épületek) összegyűjti az országos „koordináta + pontos cím” pontkészletet, és
  összepárosítja a választási címjegyzékkel. Eredmény: választási évenként egy pontfájl, amelyben
  minden pontnak van koordinátája és szavazókör-azonosítója (`szkid`).
- **B. fázis – Burkolás** (`szburkolas/`): településenként a pontokból és az OSM utcahálózatból
  előállítja a szavazóköri poligonokat. **Ez a rendszer lényege.**

```
 ┌──────────────────────── FORRÁSOK ────────────────────────┐
 │ Microsoft épületek   OSM Overpass   OSM (osmnx)   Google │      Választási címjegyzék
 │  (épület-poligon)   (címes pontok, (határ, lakott  Maps  │      (cím → szavazókör)
 │                      épületek)      terület, utca) (cím) │       2014 / 2022 / 2026
 └──────────┬──────────────┬────────────────┬─────────┬─────┘               │
            │              │                │         │                     │
            ▼              ▼                │         ▼                     │
   ┌──────────────────────────────────┐     │  ┌─────────────┐              │
   │ A. FÁZIS – ADATSTRUKTÚRA         │     │  │ Google-     │              │
   │  • hol kell címet kérdezni?      │────────▶ lekérdezés  │              │
   │  • OSM + Google címes pontok     │◀────────  (külső)    │              │
   │    egyesítése                    │     │  └─────────────┘              │
   │  • párosítás a címjegyzékkel ◀───────────────────────────────────────┘
   └──────────────┬───────────────────┘     │
                  │ osszekapcsolt_pontok_   │ varos_lakottterulet_hatar
                  │ es_cimek_<év>.gpkg      │ varos_kozigazgatasi_hatar
                  │ (pont + szkid)          │ utcak_nodes / utcak_edges
                  ▼                         ▼
   ┌──────────────────────────────────────────────────────┐
   │ B. FÁZIS – BURKOLÁS (településenként)                 │
   │  lakott terület ─▶ cellaháló az utcák mentén          │
   │  címek a cellákba ─▶ vegyes cellák Voronoi-vágása     │
   │  üres cellák a szomszédhoz ─▶ tisztítás ─▶ összevonás │
   │  ─▶ polír (kozmetika)                                 │
   └──────────────┬───────────────────────────────────────┘
                  ▼
        data/kesz_data/<év>_tobbmag.gpkg
          ├─ poligonok  (szkid-enként egy poligon, minden településre)
          └─ cimek      (a besorolt címpontok, ellenőrzéshez)
```

---

## 1. Adatok és források

### 1.1 A nyers források

| Forrás | Mit ad | Hogyan jön le | Hova kerül | Jellege |
|---|---|---|---|---|
| **Microsoft Building Footprints** | Országosan minden épület körvonala műholdkép-felismerésből (cím nélkül) | Letöltés a Microsoft dataset-listájából, ~50 „quadkey” csomag (`ms_building_download.py`) | `data/nyers_data/microsoft_download/*.csv` | Ritkán frissül |
| **OSM – Overpass, pontok** | Minden OSM node, aminek van házszáma (`addr:housenumber`) | 8 régióra külön lekérdezés (`overpass_download.py`) | `data/nyers_data/overpass_download/pontok/` | Folyamatosan frissül |
| **OSM – Overpass, épületek** | Minden `building` way/relation, címmel vagy anélkül | ugyanott | `.../overpass_download/epuletek/` | Folyamatosan frissül |
| **OSM – osmnx** | Településenként: közigazgatási határ, `landuse=residential` lakott terület, autóval járható (`drive`) utcagráf | Nominatim + Overpass az osmnx-en át (`osm_letoltes.py`) | `data/nyers_data/*.parquet` (4 fájl) | Folyamatosan frissül |
| **Google Maps** | Koordinátához tartozó pontos cím (fordított geokódolás) | Külső lekérdezés a rendszer által előállított koordinátalistákra | `data/nyers_data/google_lekredezesk/*.jsonl` | Származtatott |
| **Választási címjegyzék** | Cím → szavazókör azonosító, választási évenként | Kézzel letöltött fájlok | `valasztas_cimek_2014.csv`, `valasztas_cimek_2022.csv`, `szavkor2026.csv` | Évenként fix |
| **Településlista** | A feldolgozandó települések nevei (3178 sor) | Kézzel karbantartott | `data/nyers_data/varosok.txt` | Fix |

**A választási címjegyzék két formátumban létezik:**

- **2014 / 2022:** vesszővel tagolt CSV: `szavazokor_id, utca_nev, hazszam, telepules_nev`.
  Budapestnél a településnév „Budapest XII. kerület” alakú.
- **2026:** TAB-bal tagolt fájl: az utcanév két oszlopban (`kozteruletnev` + `kozteruletjelleg`),
  van **irányítószám** (`pir`), a szavazókör azonosítója a `wardid`, Budapest „Budapest XII” alakban.

**A `varosok.txt` különlegessége:** az első sora a sima „Budapest”, utána szerepel mind a 23 kerület
is („Budapest I. kerület” … „Budapest XXIII. kerület”). Ez szándékos: a burkolás a 23 kerületet
23 külön településként kezeli, de az MS-épület-ellenőrzéshez az egész-városi lakott terület is kell.

### 1.2 Mappakonvenció

| Mappa | Mi van benne |
|---|---|
| `data/nyers_data/` | Letöltött, érintetlen források + az OSM település-cache (parquet) |
| `data/work_data/` | Köztes, feldolgozott adatok (parquet/gpkg), amiket a következő lépés olvas |
| `data/out_data/` | Kifelé menő lekérdezési listák (Google-nek szánt koordináták, JSON) |
| `data/test_data/` | QGIS-ben nézegethető vizualizációs/diagnosztikai rétegek |
| `data/kesz_data/` | A burkolás végső gyűjtő-GPKG-i és logjai |
| `szburkolas/adatok/` | Egy település kézi futtatásának kimenetei és debug GPKG-je |

### 1.3 Vetületek (CRS)

- **Tárolás: WGS84 (EPSG:4326).** Minden parquet és pontfájl ebben van – egységes az egész országra.
- **Számítás a burkolásban: helyi UTM-zóna.** Futáskor az osmnx a település helyzete alapján
  választ (EPSG:32633 vagy 32634). Itt minden távolság méterben értendő.
- **Centroid- és területszámítás az adatstruktúrában: EOV (EPSG:23700)** – pontosabb, mint fokokban.
- **Végső kimenet: EOV (EPSG:23700)**, hogy a különböző UTM-zónájú települések egy rétegbe kerülhessenek.

---

## 2. A. fázis – Adatstruktúra: címes pontok előállítása

### 2.0 A fázis logikája egy bekezdésben

A burkoláshoz minden választási címnek kellene egy koordináta. Ilyen lista nincs, ezért a rendszer
fordítva dolgozik: **összegyűjt minél több olyan térképi pontot, amihez ismert a pontos cím**
(OSM-ből közvetlenül, a többit pedig Google-lel fordítva geokódolva), majd ezeket a pontokat
**név szerint** párosítja a választási címjegyzékkel. Hogy hol kell a Google-től címet kérdezni,
azt az épületek mutatják meg: ahol épület van, ott valószínűleg cím is van.

```
 MS épületek ──▶ épület-középpontok ─────────────┐
 (MS-rések: véletlen pontok a lakott területen) ─┼──▶ Google-lekérdezés ──▶ Google címes pontok ─┐
 OSM cím nélküli épületek ──▶ középpontok ───────┘                                               │
                                                                                                  ▼
 OSM címes node-ok + OSM címes épületek ──────────────────────────────────────▶ OSM címes pontok ─┤
                                                                                                  ▼
                                                                      egyesített címes pontkészlet
                                                                                   │
                                          választási címjegyzék (évenként) ──▶ PÁROSÍTÁS
                                                                                   ▼
                                                     osszekapcsolt_pontok_es_cimek_<év>.gpkg
```

A lépések sorrendje a `adatstruktua/0_pipeline.py` sorrendjét követi.

### 2.1 Microsoft épületek letöltése (`ms_building_download.py`)

A cél, hogy **egyetlen csomag se maradjon ki vagy legyen csonka** – egy hiányzó csomag csendben
egy egész országrésznyi címet vinne el.

```
LETÖLTÖM a Microsoft dataset-listáját, és kiszűröm a "Hungary" sorokat (~50 csomag)
BETÖLTÖM a helyi manifestet (melyik csomagot mikor, melyik URL-ről töltöttem le)

MINDEN magyar csomagRA:
    HA a manifest szerint ugyanaz az URL és feltöltési dátum, és a fájl megvan és nem üres:
        KIHAGYOM (nem változott)
    KÜLÖNBEN:
        streamelve letöltöm egy ideiglenes fájlba
        HA a letöltött bájtszám ≠ a szerver által ígért méret → HIBA, eldobom
        ellenőrzöm, hogy érvényes gzip, és az első sor érvényes JSON
        kicsomagolom, majd atomikusan a helyére teszem
        frissítem a manifestet

HA bármelyik csomag sikertelen → az egész lépés hibával áll le (nem megyünk tovább hiányos adattal)
```

### 2.2 Microsoft épületek feldolgozása (`ms_building_feldolgozas.py`)

```
BEOLVASOM az összes csomagot egyetlen országos épülettáblába
MINDEN épülethez kiszámolom a középpontját (lat/lon)
ARCHIVÁLOM: ms_minden_epulet.parquet (körvonal + középpont)

kiszámolom az alapterületet méterben
ELDOBOM a 30 m²-nél kisebb épületeket (fészer, garázs, zaj – nem lakóépület)

KIÍROM a megmaradt épületek középpontjait [lat, lon] listaként:
    out_data/lekerdezendo_kordinatak_ms.json   ← ezt kérdezzük le a Google-től
```

### 2.3 OSM Overpass letöltés (`overpass_download.py`)

Az országot 8 régióra bontja (Budapest, Pest vármegye és a 6 tervezési-statisztikai régió), mert
egyben túl nagy lenne. Régiónként két lekérdezés:

- **pontok:** minden node, aminek van `addr:housenumber` címkéje,
- **épületek:** minden `building` címkéjű way és relation, teljes geometriával.

Az eredmények GeoJSON-ként mennek a `pontok/` és `epuletek/` mappába; üres válasz hibának számít.

### 2.4 OSM Overpass feldolgozás (`osm_epulet_pont_egyesites.py`)

Két dolgot állít elő: **az OSM-ben már ismert címes pontokat**, és **azokat az OSM-épületeket,
amelyekhez nincs cím** (ezeket később a Google-től kérdezzük).

```
PONTOK:
    összefűzöm a régiókat, duplikált OSM-azonosítókat eldobom
    megtartom: város, irányítószám, utca, házszám, koordináta
    csak a házszámmal rendelkezők maradnak

ÉPÜLETEK (régiónként):
    félreteszem az ÖSSZES épület-poligont (ebből lesz a "minden épület" réteg)
    a címmel rendelkezők (házszám VAGY addr:place VAGY helyrajzi szám) poligonját is félreteszem
    a címes épületeket a középpontjukra cserélem (EOV-ban számolva)
    → csak a házszámmal rendelkező épület-középpontok maradnak

OSM CÍMES PONTOK = pontok + címes épület-középpontok
    duplikátum, ha irányítószám + utca + házszám egyezik → az elsőt tartom meg (a node-ot)
    KIÍROM: work_data/osm_cim_kordinata.parquet

CÍM NÉLKÜLI ÉPÜLETEK = minden épület − amelyiknek bármilyen címe van
    ELDOBOM azokat, amelyekbe már beleesik egy címes OSM pont (azok címe úgyis ismert)
    KIÍROM: work_data/osm_ures_epulet.parquet

KIÍROM még: work_data/osm_minden_epulet.parquet (vizualizációhoz)
```

### 2.5 OSM település-letöltés: határ, lakott terület, utcahálózat (`osm_letoltes.py`)

Ez a lépés tölti le **a burkoláshoz szükséges térképi alapot** minden településre. Négy országos
parquet fájlt épít, mindegyikben egy `telepules` oszloppal:

| Fájl | Tartalom |
|---|---|
| `varos_kozigazgatasi_hatar.parquet` | A település hivatalos határa |
| `varos_lakottterulet_hatar.parquet` | Az OSM `landuse=residential` poligonok (soronként egy) |
| `utcak_nodes.parquet` | Az autóval járható utcagráf csomópontjai |
| `utcak_edges.parquet` | Az utcagráf élei (utcaszakaszok geometriával) |

```
MINDEN településRE a varosok.txt-ből (kivéve Budapestet és a kerületeket):
    lekérdezés: {város: <név>, ország: Magyarország}
    határ        ← geokódolás (Nominatim)
    lakott terület ← minden landuse=residential poligon a településen
                     HA nincs ilyen → hibalogba: "nincs landuse=residential"
    utcagráf     ← a "drive" típusú úthálózat (csomópontok + élek)
    bármelyik réteg hibája → hibalogba írom és továbblépek (nem állok meg)

BUDAPEST KÜLÖN ÁGON (mert a Nominatim a kerületneveket nem ismeri):
    letöltöm a 23 kerület közigazgatási határát (admin_level=9) egyetlen lekérdezéssel
        ellenőrzés: pontosan 23 kerület jött, mindegyik 1–80 km² közötti
    határ:   "Budapest" egész-városi sor + 23 kerület-sor
    lakott:  az egész-városi residential foltok "Budapest" címkével (az MS-ellenőrzéshez)
             + ugyanezek kerülethatárra vágva, kerületnévvel (ezt olvassa a burkolás)
    utcák:   egész Budapest drive-gráfja EGYSZER, majd kerülethatár szerint gráf-szinten
             levágva (így minden él mindkét végpontja benne marad a kerület adataiban)

MENTÉS:
    a vegyes típusú oszlopokat (pl. ahol egy cella hol szám, hol lista) JSON-szöveggé alakítom,
    mert a parquet csak egytípusú oszlopot tud tárolni (a burkolás olvasáskor visszaalakítja)
    inkrementális futásnál az újratöltött települések régi sorait cserélem, a többit megtartom

A hibák a data/nyers_data/osm_letoltes_hibak.txt fájlba kerülnek (település, réteg, üzenet).
```

Közvetlen futtatáskor csak a még hiányzó településeket tölti le (`hianyzo_varosok`).

### 2.6 Microsoft-rések pótlása (`ms_ures_sample.py`)

A Microsoft adatkészlet **helyenként egyszerűen üres** (felhős műholdkép, feldolgozási rés). Ott
nincs épület, tehát a 2.2 lépés nem ad lekérdezendő pontot – pedig ott is laknak. Ezt a lépés a
lakott területekkel összevetve ismeri fel, és véletlen pontokkal tölti ki.

```
BEOLVASOM a lakott terület-poligonokat (EGYENKÉNT, nem településenként összevonva –
    különben egy MS-adatokkal körülvett üres folt "belső lyukként" rejtve maradna)
MINDEN lakott foltot a település közigazgatási határára vágok
BEOLVASOM az MS épület-középpontokat, és megnézem, melyik melyik foltba esik

MINDEN lakott foltRA:
    HA egyetlen MS-épület sincs benne:
        "A eset": az egész folt üres → mintavételezendő
    KÜLÖNBEN:
        az MS-pontok befoglaló téglalapja (bbox)
        megnézem a 4 irányt (fent, lent, bal, jobb): a folt mekkora része lóg ki a bbox-ból
        HA valamelyik irányban ≥ 10%:
            "B eset": a kilógó szelet(ek) üres → mintavételezendő
        KÜLÖNBEN: rendben van, kihagyom

MINDEN mintavételezendő területRE:
    N = terület (km²) × 500 pont (legalább 1)
    egyenletes véletlen pontokat dobok a területre (elutasításos mintavétel)

KIÍROM: out_data/lekerdezendo_kordinatak_kihagyott_teruletek.json   [lat, lon] listaként
```

### 2.7 Google-lekérdezés (külső lépés)

A három koordinátalistát – MS épület-középpontok, MS-rések véletlen pontjai, cím nélküli OSM
épületek középpontjai – fordított geokódolással lekérdezik a Google Maps-től. **A lekérdező program
nincs ebben a repóban** (prototípusa: `jup/osm_gmaps_addr.ipynb`). Az eredmény soronként egy JSON
lista a `data/nyers_data/google_lekredezesk/` mappában:

```
orszagos_teljes_ms_google.jsonl   ← MS épületek
kihagyott_teruletek.jsonl         ← MS-rések véletlen pontjai
osm_epulet.jsonl                  ← cím nélküli OSM épületek
```

Egy tipikus sor: `["4671540…", "Petőfi u. 24", "Páhi", " 6075 Hungary", 46.71543, 19.38775]`.

### 2.8 Google-válaszok feldolgozása (`gm_feldolgozas.py`)

A Google-válaszok szerkezete nem egységes (cégnév, üzlethelyiség, hiányzó házszám is előfordul),
ezért a feldolgozás **a lista hossza alapján** dönt:

```
MINDEN .jsonl fájl MINDEN soráRA:
    HA < 6 elem → eldobom (használhatatlan)
    HA = 6 elem → [azonosító, utca+házszám, település, irsz+ország, lat, lon]
                  HA nincs utca+házszám → eldobom
    HA = 7 elem → egy cégnév ékelődött be → kivágom
    HA > 8 elem → az utolsó 5 elemből rakom össze, ha az utca mezőben betű és szám is van
    ellenőrzés: az utca mezőben és az "irsz + ország" mezőben is legyen betű ÉS szám
    az "irsz + ország" mezőt kettébontom; csak a "Hungary" sorok maradnak

utca + házszám szétválasztása: az első számjegynél vágok ("Petőfi u. 24" → "Petőfi u." | "24")
UTCANÉV egységesítése: rövidítések kiírása (u. → utca, krt. → körút, stb.)
HÁZSZÁM egységesítése: "épület/lépcsőház/emelet…" levágása, "/" és "-" kezelése,
                        "10a" → "10 A", nagybetűsítés; helyrajzi szám → az utca mezőbe kerül

KIÍROM: work_data/teljes_ms_google_tisztott.parquet (forrásfájl-címkével)
```

### 2.9 OSM és Google pontok egyesítése (`osm_gmaps_egyesites.py`)

**Ha ugyanaz a cím mindkét forrásban szerepel, az OSM nyer** (pontosabb, különösen az irányítószám).

```
Google oldal: csak a "Hungary" sorok
közös oszlopnevek: city, postcode, street, housenumber, geometry, source (osm / gmaps)

1. SZÖVEGES DUPLIKÁTUM: kulcs = kisbetűs, szóköz/írásjel nélküli "város|irsz|utca|házszám"
   a Google-pontot eldobom, ha a kulcsa az OSM-ben már megvan
2. TÉRBELI DUPLIKÁTUM: a Google-pontot eldobom, ha 5 m-en belül van egy OSM-pont
   (a Google néha más irányítószámot ad ugyanarra a házra)

KIÍROM: work_data/osm_gmaps_merged.parquet   ← AZ ORSZÁGOS CÍMES PONTKÉSZLET
```

### 2.10 Második Google-kör előkészítése (`gm_osm_lekerdezes.py`)

```
a cím nélküli OSM épületekből (2.4) eldobom azokat, amelyekbe már beleesik
    egy egyesített címes pont (2.9)
a maradék középpontjait kiírom: out_data/lekerdezendo_kordinatak_osm_epulet.json
```

Ez egy **visszacsatolás**: a lista a következő Google-lekérdezés bemenete (→ `osm_epulet.jsonl`),
amit a 2.8–2.9 lépés újbóli futtatása beépít.

### 2.11 Párosítás a választási címjegyzékkel (`szkid_cim_kapcsolas.py`)

Ez az A. fázis kulcslépése. **Irány: a koordinátás pontok kapnak szavazókör-azonosítót** – nem a
választási címek kapnak koordinátát. Egy választási címhez több pont is tartozhat (pl. egy házhoz
OSM-node és Google-pont is), de **egy pont pontosan egy szavazókört kap**.

Két belépési pont van, egy-egy választási formátumhoz; mindkettőnek két kötelező paramétere
van: a nyers címjegyzék útja és a kimenet útja.

| | 2014 / 2022 (`szkid_cim_kapcsolas`) | 2026 (`szkid_cim_kapcsolas_v2`) |
|---|---|---|
| Bemenet | vesszős CSV | TAB-os fájl |
| Földrajzi kulcs | település + **budapesti kerület** | település + **irányítószám** |
| Budapesti kerület forrása | választási oldal: a településnévből; pont oldal: az irányítószámból (1**XX**…) | a településnévből, ha nincs: az irányítószámból |

#### a) Normalizálás – mindkét oldalon ugyanazokkal a szabályokkal

A két oldal csak akkor párosítható, ha ugyanazt a címet ugyanúgy írják le. Ezért mindkét oldal
**ujjlenyomatot** kap:

```
TELEPÜLÉS-UJJLENYOMAT:  ékezettelenítés + kisbetű + szóközök összehúzása
                        Budapest minden kerülete → "budapest" (a kerület külön kulcsban él)
KERÜLET:                Budapesten 1..23 (a "Budapest XII. kerület" / "Budapest XII" / "12. kerület"
                        alakokból, vagy az 1XXX irányítószám 2–3. jegyéből); máshol 0
IRÁNYÍTÓSZÁM:           pontosan 4 számjegy, különben üres
UTCA-UJJLENYOMAT:       rövidítések kiírása (u. → utca, krt. → körút …), majd ékezettelenítés,
                        kisbetű, és MINDEN nem-alfanumerikus karakter törlése
                        ("Petőfi Sándor u." → "petofisandorutca")
HÁZSZÁM-ELEMZÉS:        "12"      → 12–12, betű nélkül
                        "12/A"    → 12–12, "A"
                        "26-28"   → 26–28 (tartomány)
                        "000032/A"→ 32–32, "A"   (vezető nullák jelölve)
                        "5. sz."  → 5–5
                        "V"       → 5–5 (római szám)
                        "123 hrsz"→ helyrajzi szám → NEM párosítható
                        "0", "n.a.", üres → nincs házszám → NEM párosítható
```

A választási oldalon a teljesen azonos sorok (szavazókör + település + kerület/irsz + utca + házszám)
egyszer szerepelnek. A kimeneti településnév Budapesten a kanonikus kerületnév („Budapest XII.
kerület”) – pontosan a `varosok.txt` alakja, mert a burkolás erre szűr.

Párosításban csak az a sor vesz részt, amelynek van értelmes házszáma, nem helyrajzi szám, van
utca-ujjlenyomata és kitöltött földrajzi kulcsa.

#### b) Illesztési kaszkád

A szabályok a legszigorúbbtól a lazábbig követik egymást. **Egy pontot az első szabály visz el,
amely talál neki párt**; a későbbi szabályok csak a még gazdátlan pontokkal foglalkoznak.

```
közös feltétel MINDEN szabálynál: település + földrajzi kulcs + utca-ujjlenyomat egyezik

1. PONTOS:          házszám-tól, házszám-ig és betű is egyezik
2. TARTOMÁNYBA ESIK: a pont egy száma beleesik a másik oldal tartományába (betű egyezik)
                     — mindkét irányban (pont-oldali tartomány ↔ választási szám is)
3. TARTOMÁNY-ÁTFEDÉS: két tartomány átfed (betű egyezik)
4. BETŰ ELHAGYÁSA:   a szám egyezik, a betűt figyelmen kívül hagyom ("12/A" ↔ "12")

MINDEN szabály után:
    HA egy ponthoz több KÜLÖNBÖZŐ szavazókör is jelölt →
        a legkisebb azonosítót kapja (determinisztikus), és "ambiguous" jelzést kap
    a párosított pontok kikerülnek a további szabályokból
```

(Létezik egy ötödik, „házszám ±2 tolerancia” szabály is, de alapértelmezésben nincs bekapcsolva.)

#### c) Minőségellenőrzés (QA)

A futás kiírja a konzolra és a `data/work_data/qa/` mappába:

- szabályonkénti párosítási arányok (figyelmeztet, ha a pontos egyezés 30% alatt van),
- a nem párosított pontok településenkénti/utcánkénti toplistája,
- **a fő metrika: a választási címek hány százalékát érte el legalább egy valós koordináta**,
  településenkénti bontásban is,
- debug-jelzés minden pontra, amely több mint 500 m-re van a saját utcája pontjainak középpontjától.

#### d) Kimenet

```
osszekapcsolt_pontok_es_cimek_<év>.gpkg / .parquet   ← CSAK a párosított pontok
    szavazokorid  (egész szám)
    utca, hazszam (a pont saját, eredeti címe)
    telepules     (a választási oldal neve; Budapesten "Budapest N. kerület")
    irsz
    geometry      (pont, WGS84)

osszekapcsolt_pontok_es_cimek_<év>_debug.gpkg        ← MINDEN pont, a párosítás részleteivel
    (melyik szabály, ambiguous, vezető nullák, hrsz, üres házszám, térbeli kilógó)
```

**Ez a fájl a burkolás egyetlen bemenete.** A burkolás soha nem olvassa a nyers címjegyzéket.

---

## 3. B. fázis – BURKOLÁS: a szavazóköri poligonok előállítása

### 3.0 Az alapötlet és a fogalmak

**Az alapötlet:** a szavazókörök határai a valóságban szinte mindig utcák mentén futnak. Ezért:

1. a lakott területet **az utcák mentén** apró darabokra – **cellákra** – vágjuk (mint egy kirakós),
2. minden cellába „beledobjuk” a benne lévő címpontokat,
3. ha egy cellában csak egy szavazókör címei vannak → a cella azé a szavazóköré,
4. ha egy cellában több szavazókör címei keverednek → a cellát tovább vágjuk a pontok között,
5. a cím nélküli cellákat a szomszédjuk kapja meg,
6. az azonos szavazókörhöz tartozó cellákat összeolvasztjuk.

Mivel a kiindulás egy **hézag- és átfedésmentes kirakós**, és minden későbbi lépés csak
*átcímkéz* vagy *egy cellát belül oszt fel*, a végeredmény is hézag- és átfedésmentes marad.

**Fogalmak:**

| Fogalom | Jelentés |
|---|---|
| **szkid** | szavazókör-azonosító (`szavazokorid`) |
| **cella** | a lakott terület egy darabja, amit utcák és segédvonalak zárnak közre |
| **coverage** | cellák halmaza, amelyek hézag és átfedés nélkül fedik a területet |
| **lakott terület / határ** | az OSM `landuse=residential` poligonok (településhatárra vágva) és azok körvonala |
| **segédvonal (helper)** | a rendszer által húzott vonal, ami nyitott utcavégeket köt be (narancs, kék) |
| **noding** | vonalak összefésülése úgy, hogy minden metszéspontban közös csúcs legyen |
| **dangle (lógó vég)** | olyan vonaldarab, aminek az egyik vége nem kapcsolódik semmihez – nem zár be cellát |
| **sliver** | nagyon vékony, hosszú, értelmetlen poligon (két közel párhuzamos vonal között) |
| **söpört terület** | ha egy határvonalat elmozdítunk, a régi és az új vonal közé eső terület – az ebbe eső címek kerülnének át a másik szavazókörbe |

### 3.1 Belépési pontok és a tömeges futtatás

Három módon lehet futtatni (mind a `szburkolas/` mappából):

| Belépési pont | Mire való |
|---|---|
| `burkolas_v3.py` | **Egy** település kézi futtatása, debug réteggel. Kimenet a `szburkolas/adatok/` mappába. |
| `burkolas_multiple.py` | Minden település **sorban**, egy gyűjtő GPKG-be. |
| `burkolas_multiple_tobbmag.py` | Minden település **párhuzamosan**, több processzormagon, egy gyűjtő GPKG-be. Ez a fő futtató. |

A tömeges futtatás **többnapos futásra** van tervezve, ezért:

```
BEOLVASOM a bemeneti pontfájlt EGYSZER, és településenként előre szétválogatom
BEOLVASOM a gyűjtő GPKG "poligonok" rétegéből a már kész településneveket
    ← ez a folytatás alapja: ami már benne van, azt nem csinálom újra

LÉTREHOZOK (CPU-magok − 2) dolgozó processzt
    (minden 25. település után egy dolgozó újraindul, hogy a memória ne hízzon)

MINDEN még nem kész településRE (a varosok.txt EREDETI sorrendjében – így kicsik és nagyok
vegyesen jönnek, és folyamatosan látszik a haladás; méret szerinti sorrendnél órákig
semmi sem készülne el):
    DOLGOZÓ:  lefuttatja a teljes burkolást (3.2) erre a településre,
              az eredményt EOV-ra vetíti, és visszaadja. Fájlba NEM ír.
    FŐPROCESSZ (az egyetlen író – a GPKG nem bírja a párhuzamos írást):
        HA státusz = "hiba"  → logolom a hibát, továbblépek
        HA státusz = "nincs" → nincs cím vagy nincs lakott terület, továbblépek
        HA státusz = "üres"  → üres eredmény, továbblépek
        HA státusz = "ok":
            1. hozzáfűzöm a "cimek" réteghez a besorolt címpontokat
            2. hozzáfűzöm a "poligonok" réteghez a szavazóköri poligonokat
               ← ez a sorrend szándékos: a "poligonok" a folytatás-jelző, csak teljesen
                 kész település kerül bele; megszakadt írásnál a település újra sorra kerül

A végén összesítés: sikeres / már kész / nincs lakott / üres / hibás.
Minden a <gyűjtő>_log.txt fájlba is bekerül, időbélyeggel.
```

### 3.2 Egy település teljes menete – áttekintés

(`burkolas_v3.py :: generalas_pipeline`)

```
BEMENET: településnév, az összekapcsolt pontfájl

címek ← a pontfájl sorai, ahol telepules = településnév
HA nincs egyetlen cím sem → "nincs", kilépek
minden szavazókör kap egy jól megkülönböztethető színt (QGIS-hez)

HA a településen CSAK EGY szavazókör van:
    → RÖVIDÍTETT ÚT (3.3), kész

cellák, utcák ← CELLAHÁLÓ ÉPÍTÉSE (3.4)
    HA a településnek nincs lakott terület-poligonja → "nincs", kilépek

besorolt_cellák ← CÍMEK A CELLÁKBA (3.5), a vegyes cellák VORONOI-VÁGÁSÁVAL (3.6)
besorolt_cellák ← ÜRES CELLÁK FELTÖLTÉSE (3.7)
besorolt_cellák ← COVERAGE ÚJRAÉPÍTÉSE (3.8)
besorolt_cellák ← HIBÁS EGYCÍMES SZIGETEK TÖRLÉSE (3.9)
szavazókörök    ← ÖSSZEVONÁS SZAVAZÓKÖRÖNKÉNT (3.10)
szavazókörök    ← POLÍR (3.11)

KIMENET: (címek, szavazókörök)
```

### 3.3 Rövidített út: egy szavazókörös település

Ha egy településen csak egy szavazókör van, nincs mit felosztani.

```
beolvasom a település lakott terület-poligonjait és közigazgatási határát (utcagráf NEM kell)
    HA nincs lakott terület → "nincs", kilépek (SOHA nem helyettesítem a településhatárral)
minden lakott foltot a településhatárra vágok
a foltok uniója = AZ EGYETLEN SZAVAZÓKÖR POLIGONJA
```

Ez azért is fontos, mert az aprófalvaknál az OSM gyakran nem ad autóval járható utcagráfot – ezek
nagy része egy szavazókörös, így ezen az úton gond nélkül átmennek.

### 3.4 A cellaháló építése

(`burkolas_v3.py :: poly_gen_pipeline`, a munkát a `polygon_fuggvenyek.py` végzi)

Ez a lépés állítja elő a „kirakóst”. A cél egy olyan **zárt vonalháló**, amelynek minden hurka egy
cella, és ahol egyetlen vonalvég sem lóg a levegőben.

#### 3.4.1 Beolvasás és vetítés (`polygon_io.py :: olvas_varos`)

```
a négy országos parquet-ből kiszűröm az adott település sorait:
    lakott terület  → HA nincs: "NincsResidential" hiba (a település kimarad)
    közigazgatási határ → HA nincs: hiba
    utca-csomópontok és -élek → HA nincs: hiba (több szavazókörnél utca nélkül nem megy)
a csomópontok alapján kiválasztom a helyi UTM-zónát, és mindent oda vetítek
a csomópontok x/y oszlopait újraszámolom a vetített geometriából
    (különben a gráfban a csomópontok fokban, az élek méterben lennének)
a JSON-szöveggé alakított oszlopokat visszaalakítom listává
újraépítem az osmnx utcagráfot
```

#### 3.4.2 A lakott terület előkészítése

```
MINDEN lakott foltot a település közigazgatási határára vágok
    (az OSM a határon átlógó foltokat a szomszéd településnél is visszaadja)
    HA semmi nem marad → hiba

a foltokat egyenként megvizsgálom:
    HA fut benne utca (az utcahálózat és a folt közös része > 0 hosszú):
        → "hálózatos folt": ezt fogjuk utcák mentén cellákra vágni
    KÜLÖNBEN:
        → "bypass folt": egyben, egyetlen cellaként kerül a végeredménybe

lakott_terület = a hálózatos foltok uniója
határ          = a lakott_terület körvonala
```

#### 3.4.3 Segédvonalak: a nyitott utcavégek bekötése

Az utcahálózat önmagában nem zár be cellákat: a zsákutcák a semmiben végződnek, és a lakott terület
szélén az utcák nem érik el a határt. Ezeket a rendszer **segédvonalakkal** köti be. Mindkét
segédvonal egy **sugár**: az utca saját irányában meghosszabbítjuk, amíg bele nem ütközik valamibe.
A végpont *pontosan* a találati ponton ül, így később nem kell igazgatni.

```
            határ
   ─────────────────────────────
        │            ┆ kék
        │            ┆
   ─────┼──────┬─────┼──────────      ── utca
        │      │     │                ┄┄ segédvonal
        │      │                         (narancs: zsákutca folytatása
        │      ┆ narancs                   kék: utca kihúzása a határig)
        │      ┆
   ─────┴──────┴────────────────
```

**NARANCS – zsákutcák lezárása** (`orange_gen`)

```
zsákutca-vég = olyan csomópont, amelyhez csak EGY utca csatlakozik

MINDEN zsákutca-végRE:
    irány ← az utca utolsó szakaszának iránya, kifelé a zsákutca végén
    sugár ← 200 m hosszú szakasz ebben az irányban
    megkeresem a sugár LEGKÖZELEBBI találatát:
        • bármely MÁSIK utcával (a saját utcáját nem számítom), VAGY
        • a lakott terület határával
        (a 0,25 m-en belüli találatokat figyelmen kívül hagyom – az a kiindulópont maga)
    HA van találat és a szakasz > 0,1 m:
        narancs vonal = zsákutca-vég → találati pont
    (HA 200 m-en belül semmi → nincs narancs vonal, a végét a dangle-ciklus kezeli)
```

Mivel a határ is megállítja, a narancs vonal soha nem lép ki a lakott területből.

**KÉK – kihúzás a lakott terület határáig** (`blue_gen`)

```
MINDEN csomópontRA, amely 100 m-en belül van a határhoz:
    MINDEN hozzá csatlakozó utcáRA:
        sugár ← az utca irányában, a csomóponttól kifelé, 150 m hosszan (100 m × 1,5)
        megnézem, hol metszi a sugár a HATÁRT
    a csomópont összes sugara közül a LEGKÖZELEBBI határ-találatot választom
    HA van ilyen és a szakasz > 0,1 m:
        kék vonal = csomópont → határpont
```

Szándékosan **nem a legközelebbi határpontra** húzunk merőlegest, hanem az utca irányát követjük –
így a cellahatár úgy néz ki, mintha az utca folytatódna.

#### 3.4.4 A vonalháló összerakása és tisztítása (`kapcsolas`)

Ez a legkényesebb rész: az OSM-vonalak pontatlanok, a határ gyakran pár méterrel az utca mellett fut.
Ha két vonal közel párhuzamosan halad, közöttük **sliver** cellák keletkeznek (keskeny, hosszú
csíkok), ha pedig valami nem kapcsolódik, **lógó vonalak**. A tisztítás négy lépésből áll.

```
1. VÁGÁS
   az utcahálózatot a lakott területre vágom (a területen kívüli utcák nem kellenek)
   a segédvonalakat NEM vágom – konstrukció szerint már a határon belül végződnek,
   a vágás csak elmozdítaná a pontos végpontjukat

2. PÁRHUZAMOS SNAP – "ami majdnem egybeesik, legyen egy"
   egy vonal akkor "párhuzamos" egy céllal, ha legalább 15 m hosszan
   a cél 10 m-es sávjában fut
   négy menetben, hierarchikusan (a határ a horgony, az sosem mozdul):
      a) utcák     → határ
      b) segédvonalak → határ
      c) segédvonalak → utcák
      d) segédvonalak → hosszabb segédvonalak (mindig a rövidebb igazodik)
   ha egy vonal csúcsa elmozdul, a VELE AZONOS KOORDINÁTÁN ülő más vonalak
   csúcsai is vele mozdulnak ("magával húzza") – így nem szakad szét a kapcsolat

3. ÖSSZEFÉSÜLÉS (noding)
   határ + utcák + segédvonalak EGYETLEN lépésben egyesítve:
       minden metszéspontban közös csúcs keletkezik
   minden koordinátát 1 cm-es rácsra kerekítek (lebegőpontos zaj ellen)

4. V-ZÁSZLÓK TÖRLÉSE
   MINDEN csomópontban MINDEN vonalpárRA, amely onnan indul:
       HA a két vonal szöge ≤ 30° (közel párhuzamosak)
       ÉS a rövidebbik másik vége lóg (semmihez nem kapcsolódik)
       ÉS a hosszabbik 8 m-en belül halad el a rövidebbik másik vége mellett:
           a rövidebbiket törlöm (egy felesleges "V" alak, ami sliver cellát csinálna)
   (a lógó-vég feltétel védi a hálózatot hordozó vonalakat és a zárt határgyűrűt)

5. LÓGÓ VÉGEK LEVÁGÁSA (dangle-ciklus)
   AMÍG van olyan vonaldarab, ami nem része egyetlen zárt huroknak sem (legfeljebb 20-szor):
       törlöm ezeket a darabokat
   (egy törlés újabb lógó véget szabadíthat fel, ezért ismétlem)

6. a megmaradt vonalakat összefűzöm (linemerge) → TISZTA, ZÁRT VONALHÁLÓ
```

#### 3.4.5 Cellák képzése (`egyesites`)

```
cellák ← a zárt vonalháló minden hurkának kitöltése (polygonize)
minden cella geometriáját érvényesítem (a koordinátákat megőrző módon)
HA nem jött ki egyetlen cella sem → hiba

cellák += a bypass foltok (3.4.2), mindegyik egy-egy cellaként
```

**Szándékosan nincs méret szerinti beolvasztás.** Két szomszédos kis cella lehet két különböző
szavazóköré – ha a besorolás előtt összeolvasztanánk őket, ez az információ elveszne. A cím nélküli
cellákat a besorolás *után* nyelik el a szomszédaik (3.7).

### 3.5 Címek a cellákba (`pontok_polygonban`)

```
a címpontokat a cellák vetületére vetítem

MINDEN cellára:
    a cellán BELÜL lévő címpontok (a határon fekvők és a lakott területen kívüliek nem számítanak)
    HA 0 cím:
        → üres cella (szkid = nincs)
    HA minden címe UGYANAHHOZ a szavazókörhöz tartozik:
        → a cella azé a szavazóköré
    HA több szavazókör címei keverednek:
        → VORONOI-VÁGÁS (3.6): a cellát al-cellákra bontom
        HA a vágás valamiért elhasal (pl. egybeeső pontok):
            → az egész cella a TÖBBSÉGI szavazókörhöz kerül
```

### 3.6 Vegyes cellák felosztása Voronoi-vágással (`polygon_szkid_voronoi_vagas`)

**A probléma:** egy cellában (pl. egy hosszú tömbben) az egyik fele az egyik, a másik fele a
másik szavazókörbe tartozik. Valahol a két pontfelhő között kell egy határvonal.

**Az ötlet két részből áll:**
1. A **Voronoi-felosztás** adja a „természetes” határt: a cella minden pontja ahhoz a címhez tartozik,
   amelyikhez a legközelebb van. Ahol a szomszéd címek más szavazókörhöz tartoznak, ott a határ
   pontosan félúton fut közöttük.
2. Ez a határ viszont **cikcakkos** (minden szemközti címpár ad egy töréspontot). Mivel a két
   pontfelhő között van hézag, **bármely vonal jó, ami helyesen választja szét őket** – ezért a
   rendszer a lehető legjobban kiegyenesíti, de csak addig, amíg egyetlen cím sem kerül át a rossz oldalra.

```
  Voronoi-határ (nyers)              kiegyenesítve (osztály-megőrző)
  ┌───────────────────────┐          ┌───────────────────────┐
  │ A   A    ╲  B    B    │          │ A   A      │ B    B   │
  │   A    ╱╲ ╲   B       │          │   A        │  B       │
  │ A    ╱    ╱  B   B    │   ──▶    │ A          │ B   B    │
  │   A  ╲   ╱  B         │          │   A        │B         │
  │ A     ╲ ╱   B    B    │          │ A          │ B    B   │
  └───────────────────────┘          └───────────────────────┘
```

```
BEMENET: egy cella és a benne lévő címpontok (mindegyik a saját szkid-jével)

1. VORONOI
   MINDEN címpont egy "mag"
   kiszámolom a magok Voronoi-felosztását, és minden területet a cellára vágok
   minden darab annak a magnak a szkid-jét kapja, amelyik benne van
       (ha lebegőpontos hiba miatt egyik sincs benne: a legközelebbiét)

2. ÖSSZEVONÁS
   az azonos szkid-ű Voronoi-darabokat összeolvasztom → "pontos al-cellák"

3. VÁGÓVONALAK
   a különböző szkid-ű al-cellák közös határvonalai → ez a nyers, cikcakkos vágóháló
   HA nincs ilyen (gyakorlatilag egy szkid) → a pontos al-cellák a kimenet, kész

4. KIEGYENESÍTÉS – a legnagyobb biztonságos tolerancia keresése
   próba(t):
       minden vágóvonalat egyszerűsítek t tűréssel (Douglas–Peucker, a végpontok fixek –
           azok utcán vagy csomópontban horgonyoznak)
       a cellahatárt érintő végeket 1 m-rel kifelé meghosszabbítom
           (különben a vonal csak érintené a határt, és nem vágná át a cellát)
       a cellát ezekkel a vonalakkal darabokra vágom
       ÉRVÉNYES, HA minden darabban csak EGY szkid címei vannak

   bináris keresés t-re 0 és a cella befoglaló téglalapjának átlója között, 18 lépésben:
       HA próba(t) érvényes → t jó, próbálok nagyobbat
       KÜLÖNBEN → túl nagy, próbálok kisebbet
   (t = 0 mindig érvényes, tehát mindig van megoldás)

5. VÉGSŐ AL-CELLÁK
   a legnagyobb érvényes t-vel vágom fel a cellát
       (ha mégsem lenne érvényes: t = 0-val)
   minden darab a benne lévő címek szkid-jét kapja
       (cím nélküli darab: a legközelebbi címét)
   az azonos szkid-ű darabokat összeolvasztom

KIMENET: al-cellák (szkid-del) — ezek a cella helyére lépnek
         + debug: a nyers Voronoi-darabok és a kiegyenesített vágóvonalak
```

Az eredmény: kevés, hosszú, egyenes szakasz – mintha egy utca lenne ott.

### 3.7 Üres cellák feltöltése (`ures_polyk_besorolasa`)

```
AMÍG van üres (szkid nélküli) cella:
    MINDEN üres cellára:
        megkeresem a szomszédait, amelyekkel KÖZÖS ÉLE van (> 1 cm hosszú –
            a csak egy pontban érintkező cella nem szomszéd)
        a már besorolt szomszédok közül az nyer, amelyikkel a LEGHOSSZABB a közös határ
        az üres cella átveszi a nyertes szkid-jét
            (ugyanebben a körben a többi üres cella már ezt látja – így a láncok is feltöltődnek)
    HA egy körben egyetlen cella sem töltődött fel:
        a maradék elszigetelt (pl. egy cím nélküli, különálló bypass folt) → így marad, kilépek
```

A „leghosszabb közös határ” szabály azt jelenti, hogy egy üres zug ahhoz a szavazókörhöz kerül,
amelyik a legjobban „körülöleli”.

### 3.8 A coverage újraépítése (`rebuild_coverage`)

A Voronoi-vágás és a sok geometriai művelet után a szomszédos cellák közös élei
nanométeres eltéréseket mutathatnak. Ezek az összeolvasztás után 0 szélességű résekként,
„lógó vonalakként” jelennének meg QGIS-ben.

```
az összes cella körvonalát egyetlen lépésben összefésülöm (noding):
    a majdnem egybeeső élek bájtra pontosan egybeesnek
ebből a tiszta vonalhálóból újra kitöltöm a cellákat (polygonize)
minden új cella a régi cellától örökli a szkid-jét és színét
    (amelyikben a belső pontja van; ha egyikben sem, a legközelebbitől)
```

### 3.9 Hibás egycímes szigetek törlése (`hibas_szigetek_torlese`)

**A probléma:** néha egy koordinátához rossz cím van rendelve (rossz Google-válasz, rossz párosítás),
így egy cella egy teljesen máshol lévő szavazókörhöz kerül. Összeolvasztás után ez egy
**apró sziget** lesz, egy másik szavazókör közepén, egyetlen címmel.

```
MINDEN szavazókörRE:
    a celláit összeolvasztom, és szétbontom összefüggő darabokra ("szigetekre")
    HA csak egy darab van → rendben, továbblépek
    MINDEN szigetRE megszámolom a beleeső címpontokat
    gyanús = az a sziget, amelyben legfeljebb 1 cím van
    BIZTONSÁGI KORLÁT: HA minden sziget gyanús, a legnagyobb területűt megtartom
        (egy szavazókör sosem tűnhet el teljesen)
    a gyanús szigetek celláit üresre állítom (szkid = nincs)

az üresre állított cellákat az ÜRES CELLÁK FELTÖLTÉSE (3.7) szétosztja
    → tipikusan a körülvevő szavazókör nyeli el őket
```

A több címet tartalmazó szigetek (valódi, különálló tömbök) érintetlenek maradnak. A címpontokat
ez a lépés nem módosítja.

### 3.10 Összevonás szavazókörönként (`polygonok_egyesitese`)

```
MINDEN szkid-RE:
    az összes cellája uniója → a szavazókör geometriája (érvényesítve)
```

Egy szavazókör lehet **MultiPolygon** is, ha valóban több különálló részből áll (pl. két lakott folt).
Ha a 3.7-ben maradt elszigetelt üres cella, az egy szkid nélküli sorként jelenik meg.

### 3.11 Polír – kozmetikai csiszolás osztály-megőrzéssel (`polir.py`)

Ez az **utolsó, tisztán esztétikai** lépés. A Voronoi- és egyszerűsítési lépések után a
szavazókör-határok egy része még ferde vagy fűrészfogas lehet. A polír ezeket az utcahálózathoz
igazítja.

**Kötelező szabály – osztály-megőrzés:** a polír egyetlen címet sem tehet át rossz szavazókörbe.
Minden mozdítás előtt megnézi, milyen címek esnének a régi és az új vonal közé (**söpört terület**).
Egy cím csak akkor kerülhet át, ha legfeljebb 1 m-re van az új határvonaltól (gyakorlatilag rajta
ül). Ha a végső ellenőrzés bármi hibát talál, a polír **eldobja minden módosítását**, és az
érintetlen eredményt adja vissza – a kozmetika soha nem ronthat a helyességen.

```
HA legfeljebb egy szavazókör van → nincs mit csiszolni, kész

1. ÍVEK KINYERÉSE
   a szavazókör-poligonok határait összefésülöm (1 mm-es rácson), és összefűzöm úgy,
       hogy a háromnál több vonal találkozásánál vágjon
   → minden "A szavazókör ↔ B szavazókör" közös határ EGY ív, amit EGYSZER dolgozok fel
     (így a két szomszéd ugyanabból az ívből épül újra – nem nyílhat rés köztük)
   KÜLSŐ ív = amit csak egy szavazókör szegélyez (a lakott terület széle) → mindig ZÁROLT

2. ZÁROLT / SZABAD SZAKASZOK
   egy belső ív szakaszai "utcán fekszenek", ha a legközelebbi utcától ≤ 4 m-re vannak
       és az iránya ≤ 12°-kal tér el az utcáétól
   egy összefüggő utcán-fekvő futam ZÁROLT, ha legalább 25 m hosszú
       és összesen legfeljebb 25°-ot fordul (közel egyenes)
   minden más SZABAD
   az ívet a zárolt/szabad váltásoknál feldarabolom (a váltópontok fix horgonyok)

   A ZÁROLT darabok érintetlenek maradnak – azok már jók, utcán futnak.

MINDEN SZABAD darabRA (a végpontjai végig fixek):
   3. FŰRÉSZFOG-ELTÁVOLÍTÁS (mohó Visvalingam–Whyatt)
      a belső csúcsokat a "jelentéktelenségük" sorrendjében veszem sorra
          (az a csúcs a legjelentéktelenebb, amelyik a legkisebb háromszöget zárja be a szomszédaival)
      egy csúcsot CSAK AKKOR törlök, ha a háromszögben, amit az eltávolítása lesöpörne,
          NINCS EGYETLEN CÍM SEM
      → az üres tüskék és fűrészfogak méretüktől függetlenül eltűnnek,
        a címeket kerülgető hullámok megmaradnak

   4. IRÁNY-ILLESZTÉS AZ UTCARÁCSHOZ
      minden szakaszt a legközelebbi utca irányához VAGY arra merőlegeshez fordítom
          (amelyikhez közelebb áll)
      az egymás utáni, 5°-on belül azonos irányú szakaszokat egy egyenessé vonom össze
      az első egyenes a kezdőponton, az utolsó a végponton megy át,
          a köztes sarkok a szomszédos egyenesek metszéspontjai
      CSAK AKKOR fogadom el, ha a vonal nem metszi önmagát, és a söpört területbe
          eső minden cím ≤ 1 m-re van az új vonaltól

   5. FELTÉTELES RÁHÚZÁS AZ UTCA KÖZÉPVONALÁRA
      HA a darab közepe ≤ 8 m-re van egy utcától
      ÉS a darab (kezdőpont→végpont) iránya ≤ 15°-ra tér el az utcáétól:
          a belső csúcsokat, amelyek ≤ 8 m-re vannak, ráhúzom az utca középvonalára
      (különben – két utca között – a vonal marad, ahol van)
      CSAK AKKOR fogadom el, ha a söpört-terület próba átmegy (az eredeti vonalhoz képest)

6. ÚJRAÉPÍTÉS
   zárolt + feldolgozott szabad vonalak → 1 mm-es rácson összefésülve → cellák kitöltése
   minden új cella a polír ELŐTTI szavazókörtől örökli a szkid-jét (a belső pontja alapján)

7. VALIDÁCIÓ – mind a négynek teljesülnie kell:
   • nincs átfedés két cella között (> 0,001 m²)
   • a lefedett terület gyakorlatilag ugyanaz, mint polír előtt
       (eltérés ≤ max(5 m², a teljes terület 0,01%-a))
   • nem maradt lógó vonal
   • nincs "durva átkerülés": egyetlen cím sem került más szavazókörbe úgy,
       hogy 1 m-nél messzebb van az új határtól
   HA bármelyik bukik → az ÉRINTETLEN, polír előtti eredményt adom vissza
   KÜLÖNBEN → a cellákat szavazókörönként összevonom (3.10) → ez a végeredmény
```

### 3.12 Kimenetek

**A gyűjtő GPKG** (pl. `data/kesz_data/2026_tobbmag.gpkg`), EOV vetületben:

| Réteg | Oszlopok | Tartalom |
|---|---|---|
| `poligonok` | `szavazokorid, color, telepules, geometry` | Minden település minden szavazóköre – **ez a hiteles kimenet**, és a folytatás-jelző |
| `cimek` | `szavazokorid, color, utca, hazszam, telepules, irsz, geometry` | A besorolt címpontok, a poligonjukkal azonos színnel – ellenőrzéshez |

Mellette a `<név>_log.txt`: időbélyeges log minden településről, a hibák teljes leírásával.

**Egy település kézi futtatásakor** (`burkolas_v3.py`, `debug=True`) a `szburkolas/adatok/<település>_debug.gpkg`
rétegei a folyamat sorrendjében:

| Réteg | Mit mutat |
|---|---|
| `0_lakott_terulet_hatar` | a lakott terület határa a településhatárra vágás után |
| `1_utcahalozat` | az utcák a lakott területre vágva |
| `2_narancs_vonalak` | a zsákutca-lezárások |
| `3_kek_vonalak` | a határig kihúzott utcák |
| `4_egyesitett_uthalozat` | a teljes vonalháló a snap ELŐTT |
| `5_egyesitett_uthalozat_tisztitott` | a kész, zárt vonalháló (snap, noding, V-javítás, dangle-ciklus után) |
| `6_nyers_poligonok` | a cellák (a bypass foltokkal együtt) – a besorolás bemenete |
| `voronoi_sejtek` | a vegyes cellák nyers Voronoi-darabjai |
| `voronoi_elek` | a kiegyenesített vágóvonalak |
| `polir_zarolt_ivek` | a polír által érintetlenül hagyott határszakaszok |
| `polir_szabad_ivek` | a polír által csiszolt határszakaszok |

Kézi futtatáskor a végeredmény a `szburkolas/adatok/<település>_szigetek_besorolt_26.gpkg` (poligonok) és
`<település>_cimek_besorolt_26.gpkg` (címek) fájlokba kerül.

### 3.13 A garanciák – hol biztosítja a kód az invariánsokat

| Invariáns | Hol garantált |
|---|---|
| **Csak lakott terület** | A cellák a lakott terület határából és a rá vágott utcákból épülnek (3.4); a narancs/kék vonalak a határnál megállnak. |
| **Nincs fallback a településhatárra** | Ha nincs `landuse=residential`, az olvasás hibát dob, a település kimarad (3.3, 3.4.1). |
| **Teljes lefedés** | A cellák egy zárt vonalháló kitöltései; a bypass foltok egészben bekerülnek; minden későbbi lépés csak átcímkéz vagy egy cellát belül oszt fel; az üres cellák a szomszédhoz kerülnek (3.7). |
| **Nincs átfedés** | A `polygonize` egymást nem fedő lapokat ad; a Voronoi-al-cellák a cellára vannak vágva; a coverage-újraépítés (3.8) eltünteti a mikro-átfedéseket; a polír validál (3.11/7). |
| **Szomszédos szavazókörök közt nincs rés** | Minden közös határ egyetlen vonalként él, mindkét szomszéd abból épül (3.8, 3.11/1). |
| **Egy cím sem kerül rossz oldalra** | Voronoi: csak olyan egyszerűsítés, ahol minden darab egyszkid-ű (3.6); polír: söpört-terület próba + végső ellenőrzés (3.11). Kivétel szándékosan: a hibás egycímes szigetek (3.9). |

### 3.14 Paraméterek

| Paraméter | Érték | Hol | Hatás |
|---|---|---|---|
| Narancs sugár hossza | 200 m | `orange_gen` | Ennél messzebb lévő utcát a zsákutca nem köt be |
| Kék csomópont-távolság | 100 m | `blue_gen` | Csak a határhoz ennyire közeli csomópontokból húzunk kék vonalat |
| Kék sugár hossza | 150 m (100 × 1,5) | `blue_gen` | A határig tartó meghosszabbítás maximuma |
| Kiinduló-tűrés / min. szakasz | 0,25 m / 0,1 m | narancs, kék | A saját kiindulópont találatát ill. a túl rövid vonalakat kiszűri |
| Snap tűrés | 10 m | `kapcsolas` | Ezen a sávon belül futó vonalakat összerántja |
| Snap min. párhuzamos hossz | 15 m | `kapcsolas` | Csak ennyi hosszon párhuzamos vonalak snapelnek (pontszerű közelség nem) |
| V-zászló távolság / szög | 8 m / 30° | `v_shape_fix` | Ennél közelebbi, szűkebb V-ket töröl |
| Noding rács | 1 cm | `kapcsolas` | Lebegőpontos zaj ellen |
| Dangle-ciklus | max. 20 kör | `kapcsolas` | |
| Szomszédság min. közös él | 1 cm | `ures_polyk_besorolasa` | Pontszerű érintkezés nem szomszédság |
| Voronoi bináris keresés | 18 lépés | Voronoi-vágás | Kiegyenesítés pontossága |
| Vágóvonal-hosszabbítás | 1 m | Voronoi-vágás | Biztosítja, hogy a vonal átvágja a cellát |
| Gyanús sziget küszöb | ≤ 1 cím | `hibas_szigetek_torlese` | |
| Utcán fekvés | ≤ 4 m, ≤ 12° | polír | Zárolás feltétele |
| Zárolt futam | ≥ 25 m, ≤ 25° fordulás | polír | |
| Irány-csoportosítás | 5° | polír | Egy egyenesbe vont szakaszok |
| Középvonal-snap | ≤ 8 m, ≤ 15° | polír | |
| Átkerülési tűrés (FLIP_TOL) | 1 m | polír | Ennél messzebb lévő cím nem kerülhet át |
| Polír rács | 1 mm | polír | Durvább rács lapokat tüntethetne el |
| Validációs tűrések | 0,001 m² átfedés; max(5 m², 0,01%) területeltérés | polír | |
| Célvetület | EPSG:23700 (EOV) | batch | Közös kimeneti vetület |
| Dolgozók száma | CPU-magok − 2 | `burkolas_multiple_tobbmag` | |
| Dolgozó újraindítása | 25 településenként | `burkolas_multiple_tobbmag` | Memória lapos marad |
| HTTP időkorlát | 60 s (burkolás), 300 s (letöltés) | osmnx | Beragadt kérés ne akassza meg a futást |

---

## 4. Ismert korlátok, nyitott kérdések

- **Utca mellett rajzolt lakott foltok kiesnek a hálóból.** Egy folt csak akkor „hálózatos”, ha
  egy utca *belefut* (pozitív hosszon metszi). Ha az OSM-ben a lakott terület szorosan a telkek köré
  van rajzolva, és az utca csak érinti (pl. Tab második foltja), a folt bypass-ként egyben kerül be,
  utcák szerinti felosztás nélkül. Javítási irányok: távolság- vagy csomópont-alapú feltétel.
- **Rossz település geokódolása.** Az OSM-letöltés név alapján geokódol, és az osmnx a „fontosabb”
  találatot választja. 13 rövid nevű település más település határát/utcáit kapta
  (Som → Szombathely, Kömlő → Komló, Jenő → Budajenő, …). A 2014/2022-es párosításban az
  ékezettelenítés miatt Kömlő és Komló, Kömörő és Komoró össze is keveredik (a 2026-os,
  irányítószám-kulcsos út védett).
- **Üres utcagráf aprófalvaknál.** Az OSM `drive` hálózat egyes falvakra üres. Egy szavazókörnél ez
  nem gond (3.3), többnél a település hibával kimarad.
- **Elszigetelt cím nélküli foltok.** Ha egy különálló lakott foltban nincs cím és nincs
  szomszédja, szkid nélkül kerül a kimenetbe.
- **A végleges kimeneti vetület nyitott kérdés.** Most EOV; webes megjelenítéshez WGS84 vagy UTM 34N
  kényelmesebb lenne (az EOV HD72 dátuma miatt dátum-transzformáció kell).
- **A Google-lekérdező nincs a repóban** – a `.jsonl` fájlok külső folyamatból jönnek.
- **Nem hívott maradványkód:** `szur_hatarral_parhuzamos` (`polygon_fuggvenyek.py`), `felez` és
  `polygon_tobb_szavazokor` (`poligon_szk_fuggvenyek.py`) – referenciaként maradtak bent, a futásban
  nem vesznek részt.

---

## 5. Függelékek

### 5.1 Adatfájl-katalógus – mi honnan jön, ki használja

| Fájl | Ki írja | Ki olvassa |
|---|---|---|
| `nyers_data/microsoft_download/*.csv` | MS letöltés (2.1) | MS feldolgozás (2.2) |
| `work_data/ms_minden_epulet.parquet` | MS feldolgozás (2.2) | MS-rések (2.6) |
| `out_data/lekerdezendo_kordinatak_ms.json` | MS feldolgozás (2.2) | Google-lekérdezés (2.7) |
| `nyers_data/overpass_download/{pontok,epuletek}/*.geojson` | Overpass letöltés (2.3) | Overpass feldolgozás (2.4) |
| `work_data/osm_cim_kordinata.parquet` | Overpass feldolgozás (2.4) | Egyesítés (2.9) |
| `work_data/osm_ures_epulet.parquet` | Overpass feldolgozás (2.4) | Második Google-kör (2.10) |
| `work_data/osm_minden_epulet.parquet` | Overpass feldolgozás (2.4) | (vizualizáció) |
| `nyers_data/varos_kozigazgatasi_hatar.parquet` | OSM település-letöltés (2.5) | MS-rések (2.6), burkolás (3.3, 3.4) |
| `nyers_data/varos_lakottterulet_hatar.parquet` | OSM település-letöltés (2.5) | MS-rések (2.6), burkolás (3.3, 3.4) |
| `nyers_data/utcak_nodes.parquet`, `utcak_edges.parquet` | OSM település-letöltés (2.5) | burkolás (3.4) |
| `nyers_data/osm_letoltes_hibak.txt` | OSM település-letöltés (2.5) | ember |
| `out_data/lekerdezendo_kordinatak_kihagyott_teruletek.json` | MS-rések (2.6) | Google-lekérdezés (2.7) |
| `nyers_data/google_lekredezesk/*.jsonl` | Google-lekérdezés (2.7, külső) | Google feldolgozás (2.8) |
| `work_data/teljes_ms_google_tisztott.parquet` | Google feldolgozás (2.8) | Egyesítés (2.9) |
| `work_data/osm_gmaps_merged.parquet` | Egyesítés (2.9) | Második Google-kör (2.10), párosítás (2.11) |
| `out_data/lekerdezendo_kordinatak_osm_epulet.json` | Második Google-kör (2.10) | Google-lekérdezés (2.7) |
| `nyers_data/valasztas_cimek_2014.csv`, `_2022.csv`, `szavkor2026.csv` | kézi letöltés | párosítás (2.11) |
| `work_data/osszekapcsolt_pontok_es_cimek_<év>.gpkg` | párosítás (2.11) | **burkolás (3.x)** |
| `work_data/qa/*.csv` | párosítás (2.11) | ember |
| `nyers_data/varosok.txt` | kézi | OSM-letöltés (2.5), burkolás batch (3.1) |
| `kesz_data/<név>.gpkg` + `_log.txt` | burkolás batch (3.1) | QGIS / web |

### 5.2 Fájltérkép

**`adatstruktua/` – A. fázis**

| Fájl | Szerepe |
|---|---|
| `0_pipeline.py` | Az A. fázis lépéseinek sorrendje egy helyen |
| `ms_building_download.py` | Microsoft épületcsomagok letöltése, integritás-ellenőrzéssel |
| `ms_building_feldolgozas.py` | MS épületek → középpontok → Google-lekérdezési lista |
| `overpass_download.py` | OSM címes pontok és épületek letöltése régiónként |
| `osm_epulet_pont_egyesites.py` | OSM címes pontkészlet + cím nélküli épületek |
| `osm_letoltes.py` | Településenkénti határ, lakott terület, utcagráf; Budapest-ág |
| `ms_ures_sample.py` | MS-rések felismerése és véletlen pontokkal kitöltése |
| `gm_feldolgozas.py` | Google-válaszok értelmezése és tisztítása |
| `utca_nev_norm.py` | Utcanév-rövidítések és házszámformák egységesítése |
| `osm_gmaps_egyesites.py` | OSM + Google pontok összefésülése, duplikátumszűrés |
| `gm_osm_lekerdezes.py` | Második Google-kör: még cím nélküli OSM épületek |
| `szkid_cim_kapcsolas.py` | Címjegyzék ↔ koordináta párosítás, QA, kimeneti séma |

**`szburkolas/` – B. fázis**

| Fájl | Szerepe |
|---|---|
| `burkolas_v3.py` | Egy település teljes menete; a cellaháló-építés vezérlése |
| `burkolas_multiple.py` | Minden település sorban, gyűjtő GPKG, folytatás, log |
| `burkolas_multiple_tobbmag.py` | Ugyanez párhuzamosan, több processzormagon |
| `polygon_io.py` | A település OSM-adatainak beolvasása a parquet-ekből, UTM-vetítés, gráf-újraépítés |
| `polygon_fuggvenyek.py` | A cellaháló: lakott terület, narancs/kék vonalak, snap, noding, tisztítás, poligonizálás |
| `poligon_szk_fuggvenyek.py` | A besorolás: címek a cellákba, Voronoi-vágás, üres cellák, coverage, szigetek, összevonás |
| `polir.py` | A kozmetikai csiszolás osztály-megőrzéssel |

### 5.3 Eltérések a `szburkolas/claude.md` V3-leírásától

A `claude.md` egy korábbi tervezési állapotot ír le; a kód jelenleg így tér el tőle:

- **Nincs vörös (`red_gen`) segédvonal** – csak narancs és kék.
- A `kapcsolas` **párhuzamos snap-et** használ (3.4.4/2), nem a „határral párhuzamos utcák törlését”
  (`szur_hatarral_parhuzamos` – ez a függvény létezik, de nincs hívva).
- A `claude.md` szerint generálás után „semmilyen snap nem kell” – a kód viszont pont a snap-re épít.
- A narancs vonalat **a határ is megállítja**, nem csak egy másik utca.
- A poligonizálás **nem olvasztja be a kis cellákat** (nincs `MIN_AREA`), és nincs
  `meret_alapu_felosztas` előfelosztás.
- Az üres cellák a **leghosszabb közös határú** szomszédhoz kerülnek, nem többségi szavazással.
- A szavazókörönkénti összevonás **nem csinál morfológiai zárást** (buffer ki/be), a MultiPolygon megmaradhat.
- A **polír** (3.11) új, utolsó lépés, a `claude.md` nem említi.
- Az OSM-adatokat a burkolás már **nem tölti le futás közben**, hanem az előre letöltött parquet-ekből
  olvassa (`polygon_io.py`).
