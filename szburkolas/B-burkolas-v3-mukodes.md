# B. Burkoló egység – V3 működési leírás

Ez a dokumentum a burkoló egység (B) folyamatát írja le. Két célt szolgál egyszerre: ebből készül a dolgozat teljes B-fejezete, és ebből készül a kód. Ezért minden lépésnél szerepel a cél, a bemenet, az eljárás (pszeudokódban), ábra, indoklás, kimenet. A régi (V2) folyamattal való eltéréseket egyetlen függelék (F.) foglalja össze a kód átírásához; a törzsszöveg önmagában teljes.

---

## 0. Alapelvek, fogalmak, paraméterek

### 0.1 A két művelet

A folyamat a cellákon végig csak két műveletet végez:

1. **átcímkézés** – egy cella kategóriát kap vagy kategóriát vált;
2. **belső felosztás** – egy cellát a saját határain belül egy vonallal darabokra vágunk, vagy darabjait egyesítjük.

Egyik sem hoz létre területet a lakott területen kívül, és nem vesz el belőle. Ezért a hézag- és átfedésmentesség nem utólagos javítás eredménye, hanem a konstrukcióból következik.

### 0.2 Amit a folyamat soha nem csinál

- **Vonalat nem mozgat.** Minden határvonal vagy egy bemeneti vonal (utca, vasút, folyó, lakott határ), vagy egy abból szerkesztett vonal (segédvonal, vázvonal, vágóvonal). Ez garantálja, hogy az eredmény geometriailag visszavezethető a forrásra.
- **Vonalat nem töröl**, kivéve a zárt hurkot nem alkotó, lógó vonaldarabokat a vonalháló építésekor (1.4, 1.7).
- **A bemeneti hálózatot nem alakítja át** (nincs csomópont-összevonás, nincs párhuzamos élek középvonallá olvasztása): az osztott pályák és körforgalmak közötti cím nélküli területet a cellaszintű szabályok kezelik (3., 6., 9.2).
- **Épületadatot nem használ.** A bemenet kizárólag: címpontok, lakott terület, közigazgatási határ, vonalhálózat.
- **Kész cellát nem kerekít és nem javít** (`make_valid`): a kerekítés egyszer, a vonalakon történik, az összefésülés előtt.

### 0.3 Fogalmak

| Fogalom | Jelentés |
|---|---|
| **kategória** | a címlista szerinti besorolás (szavazókör, kézbesítési körzet stb.); a kódban `szkid` |
| **cella** | a lakott terület egy darabja, amit vonalak zárnak közre; a folyamat atomja |
| **tömb** | a vonalháló poligonizálásával kapott cella, a sávosítás előtt |
| **utcafront-sáv (sáv)** | a tömbnek az a része, amelyhez egy adott utca van a legközelebb: az arra az utcára néző telkek |
| **sarokcella** | két különböző utca találkozásánál kivágott, 30 m-es cella: a saroktelek |
| **vázvonal** | a sávok és sarokcellák közötti, a rendszer által szerkesztett belső vonal |
| **vágóvonal** | a rendszer által egy cella belső felosztásához szerkesztett vonal |
| **segédvonal (narancs)** | zsákutca végéből az utca irányában az első találatig húzott vonal |
| **sliver** | két közel párhuzamos vonal közé szorult, telekszélesség alatti, cím nélküli cella (szilánkcella; a dolgozatban: keskeny maradékcella) |
| **összefésülés (noding)** | a vonalak minden kereszteződésébe közös csúcspont kerül, és a vonalak ott elvágódnak; enélkül a poligonizálás nem talál zárt hurkot |
| **lógó vég** | vonaldarab végpontja, amelyhez semmilyen más vonal nem csatlakozik |
| **T-csomópont** | a cella határán ülő csúcspont, ahol egy, a cellához nem tartozó vonal végződik vagy keresztez |
| **utca nélküli folt** | lakott folt, amelybe egyetlen vonal sem fut be; egyben, egy cellaként halad végig |
| **éltábla** | minden vonaldarab attribútumai és a két cella, amit elválaszt |
| **reprezentatív pont** | egy poligon belsejében garantáltan benne lévő pont (`representative_point`) |

### 0.4 Él-típusok és rangok

Minden vonaldarabnak van típusa és rangja. A rang azt fejezi ki, mennyire valószínű, hogy valódi körzethatár; **beolvasztásnál mindig a legalacsonyabb rangú élen át olvad be egy cella**, egyenlő rangnál a leghosszabb élen át.

| Rang | Típus | OSM-címkék |
|---|---|---|
| 1 | vázvonal, vágóvonal | a rendszer szerkeszti (4., 5., 8. lépés) |
| 2 | segédvonal (narancs) | a rendszer szerkeszti (1.5) |
| 3 | gyalogút, ösvény, szervizút, lépcső, kerékpárút | `footway`, `path`, `pedestrian`, `steps`, `service`, `cycleway`, `track` |
| 4 | lakóutca | `residential`, `living_street`, `unclassified`, `tertiary`, `tertiary_link` |
| 5 | főút | `primary`, `secondary`, `trunk`, `motorway` és `_link` változataik |
| 6 | vasút, folyó | `railway=rail`, `waterway=river/stream/canal` |
| 7 | lakott terület határa | OSM lakott folt körvonala, a hivatalos közigazgatási határra vágva; **ezen soha nem olvad át semmi** |

A rang 3 vonalak csak akkor maradnak a hálóban, ha mindkét végükön kapcsolódnak (1.4): egy tömbön átvezető ösvény vagy két utcát összekötő köz vágóvonal, egy telekre vagy parkba vezető és ott véget érő bejáró nem.

### 0.5 Nevesített paraméterek

A módszernek négy nevesített paramétere van. Egyik sem tapasztalati: mindegyik egy ismert mennyiségből vezethető le.

| Név | Érték | Indoklás | Hol |
|---|---|---|---|
| **TELEKSZÉLESSÉG** | 6 m | Magyarországon nincs országos minimum telekszélesség; a helyi építési szabályzatok (HÉSZ) övezetenként írják elő: zártsorú beépítésnél 8–10 m, oldalhatáron állónál 14 m, szabadon állónál 14–20 m, falusias övezetben 12–25 m. A 6 m konzervatív alsó érték, ami alatt sorház sem állhat. | 3. |
| **SAROKMÉRET** | 30 m | a saroktelek a HÉSZ-ek szerint egy telekszélességnyi (14–20 m); a sarokház tehát a sarok 20–25 méterén belül áll, a 30 m ezt egy ráhagyással fedi | 5. |
| **VÁZFELBONTÁS** | 2 m | a vázvonalat pontokból számoljuk; a pontok távolságának fele a vázvonal legnagyobb fogazata; 2 m-nél ez 1 m, ami a TELEKSZÉLESSÉG alatt marad, így a vázvonal pontatlansága sosem tud slivert csinálni | 4. |
| **RÁCS** | 1 cm | lebegőpontos zaj ellen; a geodéziai pontosság alatt | 1.2, R. |

### 0.6 Garantált tulajdonságok

1. **Teljes lefedés** a lakott területen (kivétel: cím nélküli, szomszéd nélküli, elszigetelt lakott folt).
2. **Nincs átfedés**, és szomszédos körzetek között nincs rés: minden közös határ egyetlen vonalként létezik.
3. **Csak lakott terület**, a hivatalos közigazgatási határon belül.
4. **A besorolás megőrzése**: egyetlen címpont sem kerül a saját kategóriájától eltérő körzetbe (kivétel szándékosan: a hibás, egycímes szigetek, 10.2).
5. **Determinisztikus**: ugyanarra a bemenetre mindig ugyanaz az eredmény; a vázvonalak helyét a tömb alakja adja, nem a címpontok.

---

## Bemenetek

Településenként (Budapesten kerületenként), helyi méteres vetületben (UTM-zóna):

| Réteg | Tartalom | Kötelező attribútum |
|---|---|---|
| címpontok | az A egység kimenete | `szkid`, `geometry` (pont) |
| lakott terület | OSM `landuse=residential` foltok | `geometry` (poligon) |
| közigazgatási határ | hivatalos határ (Magyarországon állami, EU-szinten Eurostat GISCO LAU; Budapesten kerülethatár) | `geometry` |
| utcák | OSM úthálózat, minden gépjárművel és gyalogosan járható típus (`osmnx`, `network_type="all"`) | `osmid`, `name`, `highway`, `geometry` (vonal) |
| vasút, folyó | OSM `railway=rail`, `waterway=river/stream/canal` | `geometry` |

---

## R. Topológia-frissítés (minden fázis végén futó rutin)

Mielőtt a lépéseket leírom, itt van az a rutin, amelyet a 3., 4., 5., 6., 8. és 9. lépés végén mindig lefuttatunk. Két dolgot biztosít: a cellák közös élei bájtra pontosan egybeesnek, és az éltábla mindig a teljes aktuális állapotot tükrözi. Az éltáblát sosem frissítjük darabonként (az a tipikus hibaforrás), hanem minden fázis végén nulláról újraszámoljuk; településenként néhány ezer él, térbeli indexszel elhanyagolható költség.

```
BEMENET: cellák (attribútumokkal: szkid, cellatípus, tömb_id, utca_id), előző éltábla

1. vonalak ← az összes cella határa egyetlen lépésben összefésülve, RÁCS rácson
             (set_precision, majd unary_union)
2. új_cellák ← polygonize(vonalak)
3. MINDEN új cellára: attribútumai ← annak a régi cellának az attribútumai,
                      amelyben a reprezentatív pontja van
4. ÚJ ÉLTÁBLA:
   MINDEN vonaldarabRA:
       felezőpont, normálvektor
       cella_bal  ← az a cella, amelyben (felezőpont + 1 cm · normál) van; nincs: külvilág
       cella_jobb ← az a cella, amelyben (felezőpont − 1 cm · normál) van
       típus, rang, utca_id ← ATTRIBÚTUM-VISSZAILLESZTÉS (1.3) az előző éltáblából;
                              HA nincs találat → a darab ebben a fázisban szerkesztett vonal:
                                                 típus vágóvonal (4. lépésben: vázvonal), rang 1
       sor: él_id, geometria, típus, rang, utca_id, cella_bal, cella_jobb, hossz
5. MINDEN cellára: címek ← a cella belsejében lévő címpontok (újraszámolva)

KIMENET: cellák, éltábla
```

---

## 0. lépés – Előkészítés

### 0.1 Egykategóriás település (rövidített út)

```
HA a településen a címpontok mind egy kategóriához tartoznak:
    lakott foltok ← közigazgatási határra vágva (0.2)
    HA nincs lakott folt → "nincs", kilépek (a határral SOHA nem helyettesítem)
    az összes folt uniója = az egyetlen körzet poligonja; KÉSZ
```

Az aprófalvak nagy része ezen az úton megy át, vonalháló nélkül.

### 0.2 Lakott terület vágása a közigazgatási határra

```
MINDEN lakott foltRA:
    folt ← folt ∩ közigazgatási határ
    HA üres → eldobom
```

Két ok:
- a kimenet soha nem lóghat ki a hivatalos határból, különben a területből számolt mutatók értelmetlenek;
- az OSM név szerinti lekérdezése gyakran a szomszéd település lakott foltjait is visszaadja.

**Szabály: ha kérdéses, mindig a hivatalos közigazgatási határ a szél, bármi is van az OSM-ben.**

### 0.3 Vonalhálózat vágása a lakott területre

```
MINDEN vonalRA (utca, vasút, folyó):
    vonal ← vonal ∩ (lakott foltok uniója)      ← attribútumok megmaradnak
    MultiLineString → darabokra bontom
```

A vágás a lakott területre történik, nem a közigazgatási határra. Ok: a hálózat összefüggően érkezik, benne minden kivezető út és gyakran a szomszéd település hálózata is. A vágás mellékterméke hasznos: a levágott utcavégek pontosan a lakott határon ülnek, tehát be vannak kötve.

A hálózaton más előkészítés nincs. Az osztott pályás utak két vonala és a körforgalmak gyűrűje változatlanul marad: a közöttük lévő cím nélküli területet a sliver-tisztítás (3.), az üres cellák feldarabolása (6.) és a rang-szabály (9.2) sorolja a megfelelő oldalhoz. Egy csomópont-összevonó előfeldolgozás ugyanezt adná, de vonalat mozgatna és egy távolságparamétert hozna be.

### 0.4 Foltok szétválasztása

```
MINDEN lakott foltRA:
    HA fut benne vonal (vonalhálózat ∩ folt hossza > 0):
        → HÁLÓZATOS folt: ezt vágjuk cellákra
    KÜLÖNBEN:
        → UTCA NÉLKÜLI folt: egyben, egy cellaként megy tovább (2.3)

lakott_terület = hálózatos foltok uniója
határ          = lakott_terület körvonala (típus: lakott határ, rang 7)
```

**QA-jelzés a lakott terület minőségére.** Néhány településen az OSM szerkesztői nem a teljes belterületet vették fel egy foltként, hanem az utak nélküli részeket külön, apró darabokban; vagy a mellékutcákat belevették, de a főutak mentén feldarabolták a foltokat. Ezt két mutató jelzi, és az ilyen településeket kézzel javítjuk. Egyik sem befolyásolja az eredményt, csak a QA-jelentésbe kerül.

```
(a) címek kívül   ← a lakott területen KÍVÜL eső címpontok aránya az összes címponthoz
(b) főutak kívül  ← a 0.3 vágásnál LEVÁGOTT rang 4–5 úthossz aránya a lakott foltok együttes
                    burkán (convex_hull) belüli teljes rang 4–5 úthosszhoz
JELZÉS "lakott terület gyanús", HA (a) > 10 % VAGY (b) > 30 %
```

A két küszöb QA-küszöb, nem az eljárás paramétere: a kimenetet nem változtatja, csak arra mutat rá, melyik település lakott területét kell kézzel ellenőrizni.

```
      közigazgatási határ
   ┌─────────────────────────────────────────────┐
   │        ╭──────────────╮                     │
   │        │ lakott folt  │   ╭────╮            │
   │   ═════╪══════════════╪═══╡    │ ← utca,    │
   │        │   (hálózatos)│   │folt│   a lakott │
   │        ╰──────────────╯   ╰────╯   területre│
   │                           (utca   vágva     │
   │                            nélküli)         │
   └─────────────────────────────────────────────┘
```

---

## 1. lépés – A vonalháló

**Cél:** zárt vonalháló, amelynek minden hurka egy cella, egyetlen vonalvég sem lóg, és minden vonaldarab tudja, honnan jött.

### 1.1 A vonalak összegyűjtése

```
vonalak = lakott határ (rang 7)
        + utcák (rang 3/4/5 a highway címke szerint, 0.4 táblázat)
        + vasút, folyó (rang 6)
minden vonal attribútuma: vonal_id (sorszám), típus, rang, utca_id
```

**Az utca_id.** A B egység saját azonosítója, az OSM utcavonal `name` címkéjéből: ugyanazzal az ujjlenyomat-függvénnyel normalizálva (rövidítések feloldása, ékezettelenítés, kisbetű, írásjelek törlése), amelyet az A egység a címek utcanevére használ. Ha a vonalnak nincs neve, az `osmid` az azonosító. Az utca_id-nek egyetlen feladata van: ugyanannak az utcának az OSM-ben sok darabra bontott szakaszait egy csoportba fogni (4.3, 9.1). Nem függ az A egységtől, de mivel ugyanúgy van normalizálva, a QA-ban összevethető a címpontok utcanevével: egy címpont, amelynek utcája nem egyezik a sávja utcájával, gyanús (rossz koordináta vagy rossz sáv).

### 1.2 Kerekítés rácsra

```
MINDEN vonalRA: koordináták RÁCS (1 cm) rácsra kerekítve (shapely.set_precision)
```

Az egyetlen hely, ahol kerekítés történik. Az összefésülés előtt kell, hogy a majdnem egybeeső csúcsok tényleg egybeessenek.

### 1.3 Első összefésülés és attribútumok

```
darabok ← az összes vonal egyetlen lépésben egyesítve (shapely.unary_union)
          → minden kereszteződésben közös csúcs, minden vonal ott elvágva
ATTRIBÚTUM-VISSZAILLESZTÉS (lent)
```

**Miért kell visszaillesztés.** Az összefésülés előtt van egy táblánk: minden sorban egy vonal geometriája és az attribútumai (vonal_id, típus, rang, utca_id). Az `unary_union` ezekből egyetlen geometriát ad vissza, sok vonaldarabból állót, és a darabokhoz semmilyen attribútum nem tartozik; azt sem tudjuk, melyik darab melyik eredeti vonalból jött. Az attribútumokat tehát vissza kell keresni.

**Miért működik a visszakeresés.** Az összefésülés csak két dolgot csinál a vonalakkal: a kereszteződéseknél elvágja őket, és a pontosan egybeeső szakaszokat egyszer tartja meg. Nem mozgat és nem rajzol új vonalat. Ezért minden kapott darab geometriailag egy eredeti vonal egy szakasza, és a darab bármelyik belső pontja rajta ül az eredeti vonalon. A felezőpontot használjuk, mert a végpontok több vonal közös csúcsai lehetnek (egy kereszteződésben négy vonal is találkozik), a felezőpont viszont csak egy vonalon ül.

```
ATTRIBÚTUM-VISSZAILLESZTÉS(darabok, eredeti_tábla):
    index ← STRtree(eredeti_tábla geometriái)
    MINDEN darabRA:
        f ← a darab felezőpontja (interpolate(0.5, normalized=True))
        találatok ← index.query(f.buffer(RÁCS))        ← az eredeti vonalak, amelyek 1 cm-en belül
                                                           vannak a felezőponthoz
        HA 1 találat → a darab örökli annak típusát, rangját, utca_id-jét
        HA több találat (két eredeti vonal pontosan egybeesett ezen a szakaszon,
                         pl. egy utca a lakott határra vágás után a határon fut):
            rang ← a legmagasabb a találatok közül      ← a határ nyer az utca felett
            típus ← a legmagasabb rangú találat típusa
            utca_id ← bármelyik utca-találat utca_id-je  ← megmarad, hogy a sáv csoportosítható legyen
        HA 0 találat → HIBA (nem fordulhat elő; ha igen, a RÁCS kerekítés maradt ki)
```

Példa: a Petőfi utca (vonal_id 7, rang 4) keresztezi a Kossuth utcát (vonal_id 12, rang 4). Az összefésülés négy darabot ad. Az első darab felezőpontja a Petőfi utca nyugati felén ül → 1 cm-en belül csak a 7-es vonal van → a darab: típus utca, rang 4, utca_id "petofiutca". A kereszteződés csúcsa mindkét vonalon ül, de azt nem kérdezzük meg.

```
   eredeti tábla:                  darabok az összefésülés után:
   7  Petőfi  rang 4  ═══════      ═══●═══   ← 2 darab, mindkettő felezőpontja a 7-esen ül
   12 Kossuth rang 4     ║            ║      ← 2 darab, mindkettő felezőpontja a 12-esen ül
                         ║            ║
```

Ugyanez a rutin fut az 1.6-ban (a narancs vonalak beillesztése után) és az R. rutin 4. pontjában (ott az "eredeti tábla" az előző éltábla, és a sehová nem illeszkedő darab frissen szerkesztett vonal).

### 1.4 Rang 3 lógó végek törlése

Gyalogút, ösvény, szervizút csak akkor határjelölt, ha ténylegesen átvezet valahova. Ami egy épülethez, parkolóba vagy parkba vezet és ott véget ér, az egy telek közepén vágna.

```
ISMÉTELD, AMÍG változik:
    MINDEN rang 3 darabRA:
        HA valamelyik végpontjához egyetlen más darab sem csatlakozik (fokszám 1):
            törlöm
```

Megmarad minden gyalogút és szervizút, amely mindkét végén kapcsolódik (tömbön átvezető ösvény, lakótelepi belső út, két utcát összekötő köz, a lakott határig kifutó ösvény). Eltűnik minden bejáró, garázsút, parkba vezető ösvény, amely véget ér.

```
   ═══════════════════ utca ═══════════════════════
          │ bejáró (rang 3)         ┆ ösvény (rang 3)
          │ → törölve               ┆ → marad, mert
          ·                         ┆   átvezet
                                    ┆
   ═══════════════════ utca ═══════════════════════
```

### 1.5 Zsákutcák lezárása – narancs segédvonal

A zsákutca nem zár be cellát. A végét az utca saját irányában meghosszabbítjuk az első találatig. Csak gépjárműutcára (rang 4–5), mert ott a telekszerkezet a meghosszabbítás irányában folytatódik.

```
zsákutca-vég = csomópont, amelyhez az 1.4 utáni hálóban csak EGY darab csatlakozik,
               ÉS az a darab rang 4 vagy 5

MINDEN zsákutca-végRE:
    irány ← az utca utolsó szakaszának iránya, kifelé
    sugár ← félegyenes a csomópontból ebben az irányban
             (a gyakorlatban: a befoglaló téglalap átlójánál hosszabb szakasz)
    találat ← a sugár LEGKÖZELEBBI metszése bármely MÁS darabbal
              (a saját utcáját nem számítom; a 0,25 m-en belüli találatot nem számítom,
               az a kiindulópont maga)
    narancs vonal = csomópont → találat   (típus: segédvonal, rang 2)
```

Felső korlát nincs: a lakott határ mindig megállítja, ezért a narancs vonal sosem lép ki a lakott területből.

```
   ─────────────────────── lakott határ ─────────────────
         │                                   ┆
         │ utca                              ┆ narancs: a zsákutca
   ──────┼──────────┬────────────────────────┼────────── főút
         │          │                        ┆
         │          │ zsákutca               ┆
         │          │                        ┆
         │          ┆ narancs                ┆
         │          ┆                        ┆
   ──────┴──────────┴────────────────────────┴──────────
```

### 1.6 Második összefésülés

```
darabok ← unary_union(darabok + narancs vonalak), attribútumok visszaillesztése mint 1.3
```

### 1.7 Lógó végek törlése

```
ISMÉTELD:
    poligonok, lógó, cut, érvénytelen ← polygonize_full(darabok)   ← NÉV szerint kicsomagolva
    HA lógó üres ÉS cut üres → KÉSZ
    darabok ← darabok − lógó − cut
```

`cut` = olyan vonal, amelynek mindkét oldalán ugyanaz a poligon van, nem választ el semmit. A ciklus konvergál, mert minden körben fogy a vonal. (Figyelem: a `shapely.ops.polygonize_full` és a felső szintű `shapely.polygonize_full` eltérő sorrendben adja vissza a lógó és a cut éleket; mindig név szerint kell kicsomagolni, nem pozíció szerint.)

**Kimenet:** zárt vonalháló, minden darab attribútummal.

---

## 2. lépés – Cellák képzése

### 2.1 Poligonizálás

```
poligonok, lógó, cut, érvénytelen ← polygonize_full(vonalháló)
HA lógó ≠ ∅ VAGY érvénytelen ≠ ∅ → HIBA, a település leáll   ← csendben nem mehet tovább
cellák ← poligonok; cellatípus ← "tömb"
```

Innentől **vonalhálót nem építünk**: minden további művelet poligon-művelet (`split`, `union`, `intersection`), amelyek nem hoznak létre önálló vonalat. Ezért lógó él a kimenetben nem keletkezhet.

### 2.2 Utca nélküli foltok

```
cellák += utca nélküli foltok, egy-egy cellaként; cellatípus ← "folt"
```

### 2.3 Éltábla és címek

Az R. rutin 4–5. pontja: éltábla a vonalháló attribútumaiból, címek a cellákba (a cella belsejében lévő címpontok; a határon ülők és a lakott területen kívüliek nem számítanak).

```
   ═══════════ Petőfi utca (rang 4) ═══════════
   │                                           │
   │  cella #12                                │ Kossuth utca
   │                                           │ (rang 4)
   ┆ narancs (rang 2)                          │
   ┆                                           │
   ═══════════ vasút (rang 6) ═════════════════

   éltábla a #12 cellára:
     Petőfi utca  rang 4  120 m  szomszéd: #7
     Kossuth utca rang 4   60 m  szomszéd: #13
     narancs      rang 2   60 m  szomszéd: #11
     vasút        rang 6  120 m  szomszéd: #20
```

---

## 3. lépés – Sliver-tisztítás

**Cél:** a telekszélesség alatti, cím nélküli cellák eltüntetése, mielőtt a sávosítás eltorzulna tőlük.

### 3.1 Sliver felismerése

```
MINDEN cellára:
    r ← a legnagyobb beírt kör sugara (shapely.maximum_inscribed_circle; az eredmény hossza)
    SLIVER, HA  2·r < TELEKSZÉLESSÉG  ÉS  a cellában nincs címpont
```

Mindkét feltétel kell. A szélesség garantálja, hogy a cella nem lehet telek; a cím hiánya garantálja, hogy a beolvasztás nem veszít besorolási információt. Cím nélküli, keskeny cella beolvasztása csak egy határjelölt vonalat cserél egy néhány méterrel arrébb futóra. **Címes cella soha nem olvad be ebben a lépésben, bármilyen alakú.**

```
   ─────────────────────────── lakott határ ────────────────────
   │ ░░░░░░░░░░░░░░░░░ sliver (3 m széles, nincs cím) ░░░░░░░░░ │
   ═══════════════════════════ utca ═══════════════════════════════
   │  ház   ház   ház   ház   ház   ház   ház   ház   ház   ház  │
```

### 3.2 T-feldarabolás

Egy sliver több tömb hosszan is elnyúlhat. Egyben beolvasztva egy körzet átnyúlna a szomszédokba. Ezért a slivert előbb elvágjuk minden T-csomópontjánál.

```
MINDEN sliverRE:
    T-csomópontok ← a cella határának csúcsai, amelyekben a cella határához nem tartozó
                    vonaldarab is végződik vagy áthalad (éltáblából: ≥ 3 él a csúcsban)
    MINDEN T-csomópontRA:
        vágóvonal ← a csomópontból a határra merőlegesen a cella belseje felé,
                    az első kilépésig a cellából (a szemközti élig)
        cella ← split(cella, vágóvonal)
```

```
   ─────────────────────────── lakott határ ────────────────────
   │ ░░░░░░░░░ ┆ ░░░░░░░░░░░░░ ┆ ░░░░░░░░░░░ ┆ ░░░░░░░░░░░░░░ │  ← sliver, T-knél elvágva
   ═══════════╪════════════════╪═════════════╪═════════════════   főút
   │          │                │             │                 │
   │  tömb A  │ keresztutca    │   tömb B    │     tömb C      │
```

Minden darab egy tömb hosszú, és csak a saját tömbjébe tud beolvadni.

### 3.3 Beolvasztás

```
ISMÉTELD, AMÍG van sliver-darab:
    MINDEN sliver-darabRA:
        jelöltek ← nem-sliver szomszédok az éltáblából: (szomszéd, közös élek legkisebb rangja, közös hossz)
        HA nincs jelölt → kihagyom ebben a körben
        nyertes ← legkisebb rangú közös él; egyenlő rangnál leghosszabb közös él
        darab ← union(nyertes, darab)
    HA egy körben semmi nem olvadt be → a maradék cím nélküli cellaként marad (6. lépés kezeli)
R. rutin
```

A határ melletti sliver példájában az utca (rang 4) alacsonyabb, mint a lakott határ (rang 7), ezért a darab az utcán át, befelé olvad: a körzet széle a lakott határ marad, az utca eltűnik a határvonalból. Vonal nem mozdult.

### 3.4 Ismert korlát: a V-alak

Két szűk szögben induló vonal (egy rövid csonk a főút mellett) a narancs lezárással keskeny háromszöget zár be. Ez cím nélkül sliver, és beolvad. **Ritka esetben a háromszög végében lehet egy címpont**: ekkor a cella nem sliver, és egyetlen címhez egy hosszú, keskeny, háromszög alakú körzetdarab tartozik. Ezt a kimenetben jelezni kell, és ha a gyakorlatban előfordul, külön korrigálni.

```
   ════════════════════════ főút ════════════════════════
     ╲ csonk (rang 4)
       ╲
         ╲ ┆ narancs
           ╲┆
            ·   ← ha itt van egy cím, a háromszög megmarad
```

---

## 4. lépés – Tömbök felbontása utcafront-sávokra

**Cél:** minden tömböt annyi sávra bontani, ahány utca szegélyezi, hogy a tömb belsejében a telekszerkezetet követő, egyenes határjelölt vonal legyen.

### 4.1 Miért

A címlistás körzetet utca × oldal × házszám-tartomány szerint határozzák meg. Az ennek megfelelő térképi egység nem a tömb, hanem a tömbnek az a sávja, amelynek telkei egy adott utcára néznek. Ha egy tömb mindkét oldala ugyanabba a körzetbe tartozik, a sávok a végén összeolvadnak, és a vázvonal eltűnik. Ha a körzethatár a tömb belsejében fut, a vázvonal lesz a határ: egyenes, a tömb alakjából levezetett vonal, nem a címpontok szórásából születő cikcakk. **A címpontok ebben a lépésben nem vesznek részt**; a vázvonal helye kizárólag a tömb alakjától függ.

### 4.2 Mi a vázvonal

A tömb középvonala (mediális tengely): azoknak a belső pontoknak a halmaza, amelyek a tömb határának két különböző részétől ugyanolyan messze vannak.

```
 ┌──────────────────────────────────────────────────┐
 │ ╲                                              ╱ │
 │   ╲                                          ╱   │
 │     ╲──────────────────────────────────────╱     │  ← egyenlő távolság a felső és alsó oldaltól
 │     ╱                                      ╲     │
 │   ╱                                          ╲   │
 │ ╱                                              ╲ │  ← egyenlő távolság a bal és a felső oldaltól
 └──────────────────────────────────────────────────┘
```

Nekünk nem a vonal kell, hanem amit kettévág: a tömb azon részei, amelyekhez egy-egy utca van a legközelebb.

```
 ═══════════════════════ felső utca ═══════════════════════
 │ ╲                                                    ╱ │
 b│   ╲                  F sáv                        ╱   │j
 a│ B   ╲──────────────────────────────────────────╱   J │o
 l│ sáv ╱                  A sáv                    ╲  sáv│b
 │   ╱                                                ╲   │b
 │ ╱                                                    ╲ │
 ═══════════════════════ alsó utca ════════════════════════
```

### 4.3 Kiszámítás Voronoival a tömb határpontjaiból

Shapely-ben nincs mediális tengely függvény, de három beépített hívással kijön. A Voronoit itt nem a terület címpontok szerinti felosztására használjuk, hanem az utcahálózat kiegészítésére: a tömb belsejébe a telekszerkezetet követő vonalat tesz.

```
MINDEN tömbRE (cella, amelynek határán ≥ 2 különböző utca_id van):

1. HATÁRPONTOK
   gyűrűk ← a tömb külső határa + lyukainak határai
   pontok ← shapely.segmentize(gyűrűk, VÁZFELBONTÁS) csúcsai
   MINDEN pontRA: csoport ← annak az éltábla-sornak az utca_id-je, amelyen a pont ül
                           (nem-utca élnél: az él_id, mint saját csoport)

2. VORONOI
   V ← shapely.voronoi_polygons(pontok)
   MINDEN Voronoi-cellára: a tömbre vágom (intersection), és ahhoz a ponthoz rendelem,
                           amelyik benne van

3. EGYESÍTÉS CSOPORTONKÉNT
   MINDEN csoportRA: sáv_nyers ← union(a csoport Voronoi-cellái)
   → a csoporton belüli belső vonalak eltűnnek; ami marad, az a sávok közötti határ

4. VÁZVONALAK KIEGYENESÍTÉSE (a közös vonalat egyszerűsítem, nem a poligonokat külön)
   vázvonalak ← (a sáv_nyers poligonok határainak uniója) − (a tömb határa)
   vázvonalak ← shapely.simplify(vázvonalak, VÁZFELBONTÁS, preserve_topology=True)
   sávok ← polygonize(tömb határa + vázvonalak)
   MINDEN sávRA: csoport ← annak a sáv_nyers-nek a csoportja, amelyben a sáv reprezentatív pontja van

5. NEM-UTCA SÁVOK BEOLVASZTÁSA
   MINDEN sávRA, amelynek csoportja nem utca (lakott határ, vasút, folyó, segédvonal):
       nyertes ← az a szomszédos utca-sáv, amellyel a leghosszabb a közös vázvonala
       sáv ← union(nyertes, sáv)
   (ok: ezekre a vonalakra nem néz telek; a falusi, egyoldalas tömbnél ezzel a tömb
    egyetlen sáv marad)

MINDEN sáv attribútuma: cellatípus "sáv", tömb_id, utca_id,
                         utca_vonal (a csoport éleinek linemerge-e)
R. rutin (a vázvonalak itt kapják a vázvonal típust és az 1-es rangot)
```

Ha a tömb határán csak egy utca van, a tömb maga egy sáv; ha egy sincs, nem sávosítunk.

```
 •   •   •   •   •   •   •   •   •   •   •   •   •   ← határpontok a felső utcán, 2 m-enként (csoport: "Petőfi")
 ┊
 ┊   minden belső pont ahhoz a • -hoz tartozik,
 ┊   amelyikhez a legközelebb van;
 ┊   a két pontsor között félúton: a vázvonal
 ┊
 •   •   •   •   •   •   •   •   •   •   •   •   •   ← határpontok az alsó utcán (csoport: "Kossuth")
```

A vázvonal a pontok miatt legfeljebb 1 m-es fogazatot kap (a VÁZFELBONTÁS fele); a 4. pont `simplify` hívása ezt leveszi. A határpontokat a lépés végén eldobjuk; szerepük csak a szerkesztés.

Falusi, egyoldalas eset:

```
 ═══════════════════════ utca ═══════════════════════
 │  ház   ház   ház   ház   ház   ház   ház   ház   │  ← a címpontok itt vannak
 │                   utca-sáv                       │
 │- - - - - - - - - - - - - - - - - - - - - - - - - │  ← vázvonal, félúton
 │                   határ-sáv  → beolvad az utca-sávba (4.3/5)
 ┈┈┈┈┈┈┈┈┈┈┈┈┈ lakott terület határa ┈┈┈┈┈┈┈┈┈┈┈┈┈┈
```

### 4.4 Ismert korlát

A vázvonal egyenlő távolságra fut a két utcától; a valódi telekhatár el lehet tolva. Ez besorolási hibát nem okoz, mert a címpontok az utcafrontokon vannak, a vázvonaltól messze. Parcellaréteg nélkül a középvonal a legvédhetőbb becslés.

---

## 5. lépés – Sarokcellák

**Cél:** a saroktelek egy négyszögletes ingatlan, amelynek jogi utcája (hova nyílik a kapu) nem ismert; nem futhat rajta át a sávok közötti 45°-os vonal.

```
MINDEN tömbRE:
    sarok-csomópontok ← a tömb határának csúcsai, ahol két KÜLÖNBÖZŐ utca_id-jű utca találkozik
                        (ugyanannak az utcának a kanyarja nem sarok)
    MINDEN sarok-csomópontRA (S), a két utca: U1, U2:
        d1 ← min(SAROKMÉRET, az U1 e tömb menti hosszának fele)     ← két sarok nem érhet össze
        d2 ← min(SAROKMÉRET, az U2 e tömb menti hosszának fele)
        P1 ← S-től d1 méterre U1 mentén;  m1 ← U1-re merőleges egyenes P1-ben, a tömb belsejébe
        P2 ← S-től d2 méterre U2 mentén;  m2 ← U2-re merőleges egyenes P2-ben, a tömb belsejébe
        X  ← m1 ∩ m2
        HA X nincs a tömbben (nagyon tompa szög) → nincs sarokcella ennél a csomópontnál
        sarok_négyszög ← S, P1, X, P2
        az U1 és U2 sávjait elvágom m1 és m2 mentén (split)
        sarokcella ← union(azok a darabok, amelyek reprezentatív pontja a sarok_négyszögben van)
        cellatípus "sarok", utca_id üres
R. rutin
```

```
 ══════════════════════ felső utca ══════════════════════
 │ sarok │                                      │ sarok │
 │ 30×30 │                F sáv                 │ 30×30 │
 ├───────┤                                      ├───────┤  ← m: merőleges a felső utcára, 30 m-re a saroktól
 │       │ - - - - - - - vázvonal - - - - - - - │       │
 │ B sáv │                                      │ J sáv │
 ├───────┤                                      ├───────┤
 │ sarok │                A sáv                 │ sarok │
 ══════════════════════ alsó utca ═══════════════════════
```

A sarokcella besorolását a címei adják; vegyes esetben a Voronoi (8.4), üres esetben a rang-szabály (9.2).

**Ismert korlát:** 60 méternél mélyebb tömbnél a két sáv közötti átlós vonal a sarok mögött, 30 m-en túl még egy darabon folytatódik a vázvonalig. Ez a saroktelek hátsó kertjének egy darabja; ház és cím ott nincs, besorolási hibát nem okoz, a hátsó telekhatár lesz pontatlan.

---

## 6. lépés – Üres cellák T-feldarabolása

**Cél:** egy cím nélküli cella szélességtől függetlenül átnyúlhat több tömb mellett (például egy 6,2 m széles, 800 m hosszú sáv út és vasút között, vagy egy osztott pálya két vonala közötti sáv). Egyben beolvasztva egy körzet átnyúlna a szomszédokba. Ezért minden cím nélküli cellát elvágunk minden T-csomópontjánál, ugyanazzal a szabállyal, mint a slivereket.

```
MINDEN cellára (sáv, sarok, tömb, folt), amelyben nincs címpont:
    T-feldarabolás a 3.2 szerint
R. rutin
```

Üres cellánál a feldarabolás sosem árt: több darab csak finomabban illeszkedik a szomszédokhoz. Címes cellát itt nem darabolunk.

```
   ═══════════════════════ vasút ═══════════════════════════
   │ üres sáv  ┆  üres sáv   ┆  üres sáv  ┆   üres sáv      │  ← a túloldali keresztutcák T-jeinél elvágva
   ═══════════╪═════════════╪════════════╪═════════════════   főút
   │  tömb A  │   tömb B    │   tömb C   │     tömb D      │
```

Ha egy üres cellához sehol nem ér hozzá vonal (nincs T-csomópontja), nincs információ, amivel darabolni lehetne, és egyben kerül besorolásra (9.). Ez ritka és elfogadott. Ugyanez a szabály kezeli a körforgalom belsejét (minden ágnál T-csomópont → cikkek → a szomszédos tömbökhöz) és az osztott pálya két vonala közötti sávot (keresztutcánként egy darab).

---

## 7. lépés – Besorolás

```
MINDEN cellára:
    HA 0 cím → ÜRES
    HA minden cím egy kategóriájú → a cella azé a kategóriáé
    HA több kategória → VEGYES → 8. lépés
```

---

## 8. lépés – Vegyes cellák felosztása

**Cél:** a vegyes cellát a besorolás megőrzésével, egyenes vonalakkal darabolni. A Voronoi csak végső eset.

### 8.1 Sáv: a címek sorba rendezése az utca mentén, és merőleges vágás

**Mi történik, emberi nyelven.** Egy sáv egy utcához tartozik. Képzeljük el, hogy az utca vonalára egy mérőszalagot fektetünk az egyik végétől. Minden háznak a sávban megkeressük az utca hozzá legközelebbi pontját, és leolvassuk, hány méternél van ez a pont a szalagon. Így minden ház egy számot kap: hol van az utca mentén. Ezt hívjuk vetítésnek. A házakat ezután e szám szerint sorba rakjuk, és a kategóriáikat egymás mellé írjuk. Ha a sor például `A A A A B B B`, akkor egyetlen hely van, ahol a kategória vált: az utolsó A (mondjuk 212 m-nél) és az első B (230 m-nél) között. A körzethatár tehát valahol 212 és 230 m között keresztezi az utcát, ezt a 18 méteres közt a címek adják. Hogy a közön belül pontosan hol, azt az utcahálózat dönti el: ha ebbe a közbe torkollik egy keresztutca (mondjuk 221 m-nél), ott vágunk, mert a körzeteket tömbökben jelölik ki, és a határ a keresztutca folytatásában fut; ha nincs keresztutca, a köz közepén. A vágás egy egyenes szakasz, amely az utcára merőlegesen indul ennél a méterjelnél, és átmegy a sávon a túlsó széléig (a vázvonalig vagy a lakott határig).

Így a kétdimenziós pontfelhőből egy egydimenziós sor lett, a határból pedig egyetlen szám. Ez a dimenziócsökkentés: ettől lesz a határ egyenes és nem a pontok szórását követő cikcakk.

```
            0 m                 212 m   230 m                        utca mérőszalag
 ════════════╪═══════════════════╪═══════╪═════════════════════════  főút (U)
   A    A    A    A    A    A    A  │    B    B    B    B    B       a sáv házai, vetítve
                                    │
                                    │ ← vágóvonal: U-ra merőleges, a 221 m-es
                                    │   keresztutca-betorkollásnál (ha az a közbe esik),
                                    │   különben a köz közepén (221 m)
 ───────────────────────────────────┴────────────────────────────── vázvonal / lakott határ
 ═══════════════════════════════════╤══════════════════════════════ főút túloldala
                                    │ keresztutca (T-csomópont U-n, 221 m-nél)
```

```
MINDEN címRE: t ← U.project(cím)      ← a cím vetülete: hány méter az utca kezdetétől
sor ← címek t szerint rendezve, kategóriával

TISZTA, HA minden kategória egyetlen összefüggő futamot alkot a sorban
          (A A A A B B B: tiszta;  A A B A B B: nem tiszta)

HA TISZTA:
    MINDEN szomszédos futam-pár (K1, K2) közöttRE:
        köz ← (K1 utolsó címének t-je, K2 első címének t-je), nyílt intervallum
        T-jelöltek ← az U vonalán ülő T-csomópontok (ahol másik vonal végződik vagy keresztez),
                     amelyek t-je a közbe esik
        HA van T-jelölt → t_vágás ← a köz felezőpontjához legközelebbi T-jelölt t-je
        KÜLÖNBEN      → t_vágás ← a köz közepe
        P ← U.interpolate(t_vágás)                     ← a vágás pontja az utcán
        n ← az U érintőjére merőleges irány P-ben     ← görbe utcánál is egyértelmű
        vágóvonal ← egyenes P-ből n irányban, a sávból való kilépésig
        darabok ← split(sáv, vágóvonal)
        ELLENŐRZÉS: minden darabban egy kategória?
            HA nem (görbe utca miatt egy ház a rossz oldalra esett):
                t_vágás-t a közön belül tolom (a köz negyedelő pontjai), újra
            HA így sem → 8.4
```

**Görbe utcánál.** A merőlegest az utca adott pontjához húzott érintőre vesszük, ez görbénél is egyértelmű. A vágóvonal egyenes; a sáv mélységén (30–80 m) ez a telekhatárakhoz képest nem torzít, mert a telekhatárak maguk is egyenesek. Éles kanyar belső oldalán két közeli merőleges összetarthat; ha a sávon belül metszenék egymást, a köztük lévő apró darab cím nélküli, és a 9. lépés sorolja be. Ez ismert korlát (13.).

### 8.2 Egyeztetés a szemközti sávval: egy vonal, ha lehet, lépcső, ha kell

A tömb két oldalán a két sáv vágásai általában nem esnek egy vonalba (a 45/47 és a 12/14 közötti telekhatárok nem egymás folytatásai). A valóságban is lépcsős a hátsó telekhatár, de a geokódolás elcsúszásából jövő kis eltolásokat nem akarjuk lépcsőként látni. Ezért ha a két sáv megengedett köze a vázvonalra vetítve átfed, a két vágást egy közös vonalba hozzuk.

A közös vonal **két szakaszból** áll: az egyik az innenső utcára merőleges, a másik a túlsó utcára merőleges, és a vázvonalon találkoznak. Ha a két utca párhuzamos, ez egy egyenes. Ha nem párhuzamosak (trapéz alakú tömb, görbülő utcák), a vonal a vázvonalon törik, pontosan ott, ahol a valódi telekhatárak is törnek, a telkek hátsó vonalán. Egyetlen egyenes itt rossz lenne: nem lehet egyszerre mindkét utcára merőleges.

```
HA a szemközti sáv (ugyanaz a tömb, közös vázvonal) is vegyes, és ugyanaz a két kategória
   között van benne futam-határ:
    I1 ← az innenső sáv közének két szélső merőlegese a vázvonalat hol metszi
         → intervallum a vázvonal mentén (vázvonal.project)
    I2 ← ugyanez a túlsó sávra
    HA I1 ∩ I2 ≠ ∅:
        T-jelöltek ← T-csomópontok bármelyik utcán, amelyeknek az utcára merőleges vonala
                     I1 ∩ I2-be metszi a vázvonalat
        v ← a legközelebbi T-jelölt metszéspontja, ha van; különben (I1 ∩ I2) közepe
        V ← vázvonal.interpolate(v)
        vágás_1 ← V-ből az innenső utcára merőlegesen az utcáig
        vágás_2 ← V-ből a túlsó utcára merőlegesen az utcáig
        split(innenső sáv, vágás_1); split(túlsó sáv, vágás_2)
        ELLENŐRZÉS mindkét sávon mint 8.1
    KÜLÖNBEN:
        mindkét sáv a saját t_vágás-ával (8.1) → lépcső a vázvonalon
```

```
 párhuzamos utcák:                           nem párhuzamos utcák:
 ═════════ felső utca ═════════              ═════════ felső utca ═══════════
   A   A   A  │ B   B   B                      A   A   A   │ B   B   B
 ─────────────┼──────────── vázvonal         ──────────────╲──────────── vázvonal (V-ben törik)
   A   A   A  │ B   B                          A   A   A     ╲ B   B
 ═════════ alsó utca ══════════              ═══════ alsó utca ═════════════
   egy egyenes                                  két merőleges, V-ben találkozva
```

Ez az osztálymegőrzési elv döntési szabályként: a vonal addig egyszerűsödik, amíg egyetlen cím sem kerül rossz oldalra; csak most nem Voronoi-vonalat egyszerűsítünk, hanem két merőlegest egyeztetünk. Számításigénye elhanyagolható: két intervallum összehasonlítása.

### 8.3 Sáv: a címek sorba rendezése az utcától mért távolság szerint (két házsor)

Ha az utca menti sor nem tiszta, előfordulhat, hogy a sávban két házsor van egymás mögött, mert a köztük futó utcát nem rajzolták be (új parcellázás). Ekkor nem az utca mentén, hanem az utcától mért távolság szerint rendezünk: minden ház egy számot kap, hány méterre van az utcától. Ha ez a sor tiszta (az első sor házai mind A, a hátsóé mind B), a vágás az utcával párhuzamos vonal a két sor között.

```
MINDEN címRE: d ← U.distance(cím)           ← hány méterre van a ház az utcától
sor ← címek d szerint rendezve
HA TISZTA:
    MINDEN szomszédos futam-pár közöttRE:
        d_vágás ← a két futam d-közének közepe
        vágóvonal ← U.offset_curve(d_vágás), a sávra vágva   ← az utcával párhuzamos, követi a görbületét
        darabok ← split(sáv, vágóvonal); ELLENŐRZÉS mint 8.1
```

```
 ═══════════════ felső utca ═══════════════
   A    A    A    A    A    A    A    A        ← erre az utcára néznek
 - - - - - - - - - - - - - - - - - - - - - -   ← vágóvonal: párhuzamos az utcával, a két sor között
   B    B    B    B    B    B    B    B        ← a be nem rajzolt belső utcára néznek
 ─ ─ ─ ─ ─ ─ ─ ─ vázvonal ─ ─ ─ ─ ─ ─ ─ ─ ─
```

**Görbe utcánál.** Az `offset_curve` az utca görbületét követi, tehát a vágóvonal is görbül. Ez helyes: a hiányzó belső utca is az előtte lévővel párhuzamosan futna. Torzítás itt nincs.

### 8.4 Végső eset: Voronoi a keveredő szakaszon

Ha egyik vetítés sem ad tiszta futamot, a címek valóban keverednek (sarok, szigetszerű besorolás, adathiba).

```
SÁVNÁL:
    előfutam ← a sor elejének leghosszabb tiszta futama; utófutam ← a végének leghosszabb tiszta futama
    ezeket merőleges vágóvonallal levágom (8.1 szerint)
    a középső, keveredő darabra: VORONOI-VÁGÁS
SAROKCELLÁNÁL, TÖMBNÉL ÉS UTCA NÉLKÜLI FOLTNÁL: Voronoi-vágás a teljes cellára

VORONOI-VÁGÁS (cella, címek):
    1. minden címpont egy mag; Voronoi-felosztás, a cellára vágva; minden darab a magja kategóriáját kapja
    2. azonos kategóriájú darabok egyesítve → nyers al-cellák
    3. a különböző kategóriájú al-cellák közös határvonalai → nyers vágóháló
    4. KIEGYENESÍTÉS: a legnagyobb t tűrés keresése bináris kereséssel (18 lépés, 0 és a cella
       átlója között), amelynél a Douglas–Peucker-rel egyszerűsített vágóvonalakkal (végpontok fixek,
       a cellahatárt érintő végek 1 m-rel meghosszabbítva) vágott cella minden darabja egykategóriás
       (t = 0 mindig érvényes, tehát mindig van megoldás)
    5. a legnagyobb érvényes t-vel vágom; minden darab a benne lévő címek kategóriáját kapja,
       cím nélküli darab a legközelebbi címét; azonos kategóriájú darabok egyesítve
```

A Voronoi tehát mindig lokális: egy sáv keveredő szakaszára, egy 30 m-es sarokra vagy egy utca nélküli foltra korlátozódik.

### 8.5 A felosztás után

```
minden darab a benne lévő címek kategóriáját kapja; cím nélküli darab → ÜRES (9. lépés)
az azonos kategóriájú, szomszédos darabok a cellán belül egyesülnek
R. rutin (a vágóvonalak itt kapják a vágóvonal típust és az 1-es rangot)
```

---

## 9. lépés – Üres cellák besorolása

Három szabály, mindig a legbiztosabbtól a legbizonytalanabb felé.

### 9.1 Sáv: utca menti folytonosság

A körzetet utcaoldalanként határozzák meg, ezért egy üres sáv a saját utcájának ugyanazon oldalán lévő sávoktól örököl.

```
MINDEN üres sávRA (utca U):
    jelöltek ← a U-hoz tartozó, már besorolt sávok, amelyek UGYANAZON AZ OLDALON vannak:
               két sáv ugyanazon az oldalon van, ha a reprezentatív pontjaikat összekötő
               szakasz NEM keresztezi U vonalát   (shapely crosses)
    HA van jelölt:
        a két irányban (kisebb t, nagyobb t) a legközelebbi jelölt
        HA egyezik a kategóriájuk, vagy csak egy van → az
        HA eltér → a közelebbi, és a sáv JELZÉST kap (ellenőrizendő)
```

```
 ════════════════ Petőfi utca ════════════════════════════
   12. körzet  │  ???  üres sáv  │  12. körzet    → 12. körzet
 ─────────────────────────────────────────────────────────
```

### 9.2 Rang-szabály (minden más cella, és a sáv, ha 9.1 nem adott eredményt)

```
ISMÉTELD, AMÍG változik valami:
    MINDEN üres cellára:
        szomszédok ← éltábla: (szomszéd, a közös élek legkisebb rangja, közös hossz), csak besoroltak
        nyertes ← legkisebb rang; egyenlő rangnál leghosszabb közös él
        a cella a nyertes kategóriáját kapja (ugyanebben a körben a többi üres már látja)
```

Egy vázvonalon (rang 1) át könnyen olvad egy cella, vasúton (rang 6) át csak akkor, ha más nincs; lakott határon (rang 7) át soha.

### 9.3 Legközelebbi cella

```
a még üres cellák (nincs közös élük semmivel, pl. utca nélküli folt):
    a legközelebbi besorolt cella kategóriáját kapják
    HA nincs ilyen → üres marad, jelzéssel
R. rutin
```

---

## 10. lépés – Lezárás

### 10.1 Hibás, egycímes szigetek

Rossz koordináta (hibás geokódolás vagy párosítás) egy cellát egy távoli kategóriához sorolhat. Egyesítés után ez egy apró sziget egy másik körzet közepén.

```
MINDEN kategóriáRA:
    cellái egyesítve, összefüggő darabokra bontva
    HA egy darab → rendben
    gyanús ← legfeljebb 1 címet tartalmazó darab
    BIZTONSÁG: ha minden darab gyanús, a legnagyobbat megtartom (egy kategória nem tűnhet el)
    a gyanús darabok cellái → ÜRES → 9. lépés újra rájuk
```

A több címet tartalmazó szigetek maradnak: a valóságban egy utca távolabbi szakasza gyakran egy másik körzet közepén van. (A házszám-paritás ellenőrzése szándékosan nem része az eljárásnak; a QA-jelentésben jelzésként szerepelhet.)

### 10.2 Egyesítés kategóriánként

```
R. rutin
MINDEN kategóriáRA: az összes cellája uniója → a körzet poligonja (MultiPolygon megengedett)
```

### 10.3 Validáció és napló

```
1. VALIDÁCIÓ – mindnek teljesülnie kell:
   • nincs átfedés (> 0,001 m²)
   • a lefedett terület megegyezik a 2. lépés celláinak uniójával (eltérés ≤ max(5 m², 0,01 %))
   • nincs lógó vonal
   • egyetlen cím sincs a saját kategóriájától eltérő körzetben
   HA bármelyik bukik → HIBA, a település jelzést kap
2. NAPLÓ – szerkezeti mutatók (12. fejezet) településenként
```

Kozmetikai utómunka (fűrészfog-eltávolítás, utcarácshoz igazítás, középvonalra húzás) nincs: a határok a szerkesztés módjából adódóan egyenesek és utcán vagy vázvonalon futnak. Ha egy diagnosztikai futásban mégis mérni akarjuk, hogy egy kozmetikai lépés mennyit mozdítana, az csak mér, nem módosít.

---

## 11. Kimenetek

**Gyűjtő GPKG** (EOV vetületben):

| Réteg | Oszlopok | Tartalom |
|---|---|---|
| `poligonok` | `szkid, telepules, jelzes, geometry` | körzetenként egy poligon; `jelzes`: üres / eltérő-folytonosság / egycímes-háromszög / validáció-hiba |
| `cimek` | `szkid, utca, hazszam, telepules, irsz, geometry` | a besorolt címpontok, ellenőrzéshez |

### 11.1 Debug GPKG – minden lépés, minden vonal, minden pont

Egy település kézi futtatásakor (`debug=True`) a `<település>_debug.gpkg` **minden valaha megrajzolt vonalat, pontot és cellát** külön rétegben tartalmaz, a lépés sorszámával a réteg nevében. Így QGIS-ben bármelyik lépés eredménye rátehető az előző lépésére, és látszik, az adott lépés pontosan mit csinált. Ezekből a rétegekből készülnek a dolgozat ábrái is.

**Közös oszlopok minden rétegen:** `lepes` (pl. "1.5"), `tipus` (a réteg tartalma), `szin` (hex kód), plusz a réteg saját attribútumai. A `szin` oszlop miatt az egész GPKG egyetlen adatvezérelt QGIS-stílussal (szín ← `szin`) egyből a lenti színekben jelenik meg.

**Színrendszer.** A vonalak színe a vonal eredetét mutatja, a cellák kitöltése az állapotukat; a két rendszer egy ábrán is olvasható marad.

| Elem | Szín | Hex |
|---|---|---|
| lakott terület határa | fekete | `#000000` |
| közigazgatási határ | sötétszürke, szaggatott | `#555555` |
| rang 3 (gyalogút, szervizút) | világosszürke | `#AAAAAA` |
| rang 4 (lakóutca) | kék | `#1F77B4` |
| rang 5 (főút) | sötétkék | `#08306B` |
| rang 6 vasút / folyó | barna / cián | `#8C564B` / `#17BECF` |
| törölt vonal (1.4, 1.7) | piros, szaggatott | `#D62728` |
| narancs segédvonal | narancs | `#FF7F0E` |
| vázvonal | lila | `#9467BD` |
| sarok-merőleges | magenta | `#E377C2` |
| vágóvonal, T-csomópontnál | zöld | `#2CA02C` |
| vágóvonal, köz közepén | világoszöld | `#98DF8A` |
| vágóvonal, párhuzamos (8.3) | türkiz | `#17BECF` |
| vágóvonal, Voronoi (8.4) | piros | `#D62728` |
| Voronoi nyers sejtek | halványpiros kitöltés | `#F7B6B6` |
| T-vágás (3.2, 6.) | olajzöld | `#BCBD22` |
| cella: egykategóriás | kategória színe (a `szkid` szerinti paletta) | – |
| cella: vegyes | narancs kitöltés | `#FFBB78` |
| cella: üres | szürke kitöltés | `#C7C7C7` |
| cella: sliver | rózsaszín kitöltés | `#F7B6D2` |
| cella: sarok | halványmagenta kitöltés | `#F2C6E6` |
| határpont (4.3) | a csoport utcájának színe, pont | – |
| címpont | kategória színe, pont | – |
| gyanús sziget / jelzés | piros körvonal, vastag | `#D62728` |

**Rétegek, a folyamat sorrendjében.** (P = pont, V = vonal, C = cella/poligon.)

| Réteg | Típus | Tartalom | Saját attribútumok |
|---|---|---|---|
| `0_2_lakott_nyers` | C | OSM lakott foltok vágás előtt | |
| `0_2_kozig_hatar` | V | közigazgatási határ | |
| `0_2_lakott_vagott` | C | lakott foltok a határra vágva | |
| `0_3_vonalak_nyers` | V | OSM vonalak vágás előtt | highway, name, rang |
| `0_3_vonalak_levagott` | V | a lakott területen KÍVÜL maradt darabok (QA (b) alapja) | rang |
| `0_3_vonalak_vagott` | V | vonalak a lakott területre vágva | rang, utca_id |
| `0_4_foltok` | C | foltok, típussal | folt_tipus (hálózatos / utca nélküli) |
| `0_4_cimek_kivul` | P | lakott területen kívüli címpontok (QA (a)) | szkid |
| `1_1_hatar` | V | lakott határ | |
| `1_3_darabok` | V | első összefésülés utáni darabok, attribútummal | rang, utca_id, vonal_id |
| `1_4_rang3_torolt` | V | törölt lógó rang 3 darabok | kör (hányadik iterációban) |
| `1_5_zsakutca_vegek` | P | narancs kiindulópontok | rang |
| `1_5_narancs` | V | narancs vonalak | hossz, talalat_tipus (utca / határ / vasút…) |
| `1_5_talalatok` | P | a narancs vonalak végpontjai | |
| `1_6_darabok` | V | második összefésülés utáni darabok | rang, utca_id |
| `1_7_logo_torolt` | V | törölt lógó és cut darabok | ok (lógó / cut), kör |
| `1_7_vonalhalo` | V | a kész vonalháló | rang, utca_id, tipus |
| `2_1_tombok` | C | cellák poligonizálás után | cella_id |
| `2_2_foltcellak` | C | utca nélküli foltok mint cellák | |
| `2_3_eltabla` | V | éltábla élei | rang, utca_id, cella_bal, cella_jobb |
| `2_3_cimek` | P | címpontok, cella_id-vel | szkid, cella_id |
| `3_1_sliverek` | C | felismert sliverek | beirt_atmero |
| `3_2_T_csomopontok` | P | T-csomópontok a slivereken | |
| `3_2_T_vagasok` | V | T-vágóvonalak | |
| `3_3_beolvasztas` | V | nyíl a sliver-darab középpontjától a nyertes cella középpontjáig | nyertes_rang, kozos_hossz |
| `3_R_cellak` | C | cellák a sliver-tisztítás után | |
| `4_3_hatarpontok` | P | 2 m-es határpontok | csoport (utca_id / él_id) |
| `4_3_voronoi_nyers` | C | Voronoi-cellák a határpontokból, tömbre vágva | csoport |
| `4_3_savok_nyers` | C | csoportonként egyesített nyers sávok | csoport |
| `4_3_vazvonalak_nyers` | V | vázvonalak egyszerűsítés előtt | |
| `4_3_vazvonalak` | V | vázvonalak egyszerűsítés után | |
| `4_3_nemutca_beolvasztas` | C | a beolvasztott nem-utca sávok | csoport, nyertes_utca |
| `4_R_savok` | C | sávok | tomb_id, utca_id |
| `5_sarok_csomopontok` | P | sarok-csomópontok | utca_1, utca_2 |
| `5_merolegesek` | V | m1, m2 merőlegesek | d |
| `5_sarok_negyszogek` | C | S–P1–X–P2 négyszögek | |
| `5_R_cellak` | C | cellák sarokkivágás után | cellatipus (sáv / sarok / tömb / folt) |
| `6_T_csomopontok` | P | T-csomópontok az üres cellákon | |
| `6_T_vagasok` | V | T-vágóvonalak | |
| `6_R_cellak` | C | cellák | |
| `7_besorolas` | C | cellák állapottal | allapot (egykategóriás / vegyes / üres), szkid |
| `8_1_vetitesek` | V | a cím és az utcai talppontja közötti szakasz | t, szkid |
| `8_1_sorok` | P | címek t értékkel és futam-sorszámmal | t, futam, tiszta |
| `8_1_vagas_T` | V | merőleges vágások T-csomópontnál | t, T_utca |
| `8_1_vagas_kozep` | V | merőleges vágások a köz közepén | t |
| `8_2_kozos_vonal` | V | a két sávon egyeztetett vágások (két szakasz) | v, tores_szog |
| `8_3_parhuzamos` | V | párhuzamos vágások | d |
| `8_4_voronoi_sejtek` | C | nyers Voronoi-sejtek a címpontokból | szkid |
| `8_4_voronoi_nyers_vagohalo` | V | a cikcakkos vágóháló | |
| `8_4_voronoi_vagas` | V | kiegyenesített Voronoi-vágóvonalak | t_tures |
| `8_R_cellak` | C | cellák a felosztás után | szkid, vagas_tipus |
| `9_1_folytonossag` | V | nyíl az üres sávtól az örökítő sávig | irany, jelzes |
| `9_2_rang` | V | nyíl az üres cellától a nyertes szomszédig | nyertes_rang |
| `9_3_legkozelebbi` | V | nyíl a legközelebbi cellához | tav |
| `9_R_cellak` | C | minden cella besorolva | szkid, szabaly (címes / folytonosság / rang / legközelebbi) |
| `10_1_szigetek` | C | gyanús szigetek | cimek_szama |
| `10_2_korzetek` | C | a kész körzetek | szkid, jelzes |
| `10_3_validacio` | C | a validáció hibái (átfedés, átkerülés), ha van | ok |

Az „R” rétegek az R. rutin utáni állapotok: ezek a fázisok tiszta kimenetei, egymásra téve mutatják a cellák fejlődését tömbből sávon és sarkon át körzetig.

---

## 12. Pontosság és szerkezeti mutatók

**Pontosság.** Az eljárás egyetlen ellenőrizhető pontossági állítása a besorolás megőrzése: minden címpont a saját kategóriájának körzetében van. Ezt a validáció (10.3) méri, és 100%-nak kell lennie. A valódi körzethatárhoz képesti eltérés országosan nem mérhető, mert nincs referencia; ahol van kézzel rajzolt, megbízható körzetpoligon (például a valasztas.hu lipótvárosi poligonjai), ott esettanulmányként mérhető a V3-poligon és a referencia szimmetrikus differenciájának területaránya.

**Szerkezeti mutatók.** A folyamat belső minőségét mérik, településenként és országosan:

1. **vegyes cellák aránya** a besorolás előtt (7. lépés) – a vonalháló és a sávosítás jósága;
2. **üres cellák területaránya** – a geokódolás lefedettsége és a lakott terület minősége;
3. **vágástípusok megoszlása** a vegyes celláknál: merőleges-T / merőleges-közép / párhuzamos / Voronoi – a Voronoi aránya a "végső eset" mértéke;
4. **jelzések száma**: eltérő folytonosság, egycímes háromszög, validációs hiba;
5. **rang 3 törlések és narancs vonalak száma** – a bemeneti hálózat lezáratlansága.

---

## 13. Ismert korlátok (a dolgozatba)

- **Rang 3 zsákutcák** (1.4): egy hosszú, zsákutcás szervizút egy lakóparkba nem kap lezárást; a házai a nagy tömbben maradnak, és a 8. lépés vágja őket. Ha gyakori, az OSM `service=driveway` / `parking_aisle` alcímkék kizárásával és a sima `service` rang 4-be emelésével finomítható (címke-szintű döntés, nem paraméter).
- **A V-alakú csonk címmel** (3.4): egy címhez hosszú, keskeny háromszög tartozhat; jelzés, kézi korrekció.
- **Mély tömb saroktelkének hátsó kertje** (5.): a 45°-os vázvonal-maradék a sarok mögött.
- **Üres cella T-csomópont nélkül** (6.): egyben kerül besorolásra.
- **Éles kanyar belső oldala** (8.1): két közeli merőleges vágás összetarthat és a sávon belül metszheti egymást; a köztük lévő apró, cím nélküli darabot a 9. lépés sorolja be.
- **Nem párhuzamos utcák közös vágása** (8.2): a közös vonal a vázvonalon törik; ez a telkek hátsó vonalán lévő valódi törést közelíti, de a törés helye a vázvonaltól függ, nem a telekhatártól.
- **Hiányzó belső utca** (8.3): a vágás az utcával párhuzamos, de a hiányzó utca pontos helye nem ismert.
- **Hibásan szerkesztett lakott terület** (0.4): a QA-jelzés mutatja, az eljárás nem javítja; kézi javítás a saját adatbázisban.
- **Vázvonal és telekhatár eltérése** (4.4): parcellaréteg nélkül a középvonal a becslés.
- **Elszigetelt, cím nélküli lakott folt** (9.3): kategória nélkül marad.

---

## 14. Shapely-függvények a megvalósításhoz

| Feladat | Függvény |
|---|---|
| kerekítés rácsra | `shapely.set_precision(geom, 0.01)` |
| összefésülés | `shapely.unary_union(lines)` |
| poligonizálás diagnosztikával | `shapely.polygonize_full(lines)` – **név szerint kicsomagolni**: a felső szintű függvény (polygons, cuts, dangles, invalid), az `ops` modulbeli (polygons, dangles, cuts, invalid) sorrendet ad |
| végpont fokszáma | a darabok végpont-koordinátáinak számlálása (RÁCS után egzakt) |
| beírt kör | `shapely.maximum_inscribed_circle(poly, tolerance)` → sugár = az eredmény hossza |
| határpontok | `shapely.segmentize(ring, 2.0)` |
| Voronoi | `shapely.voronoi_polygons(MultiPoint)` |
| vonal egyszerűsítése | `shapely.simplify(line, 2.0, preserve_topology=True)` |
| cella vágása vonallal | `shapely.ops.split(poly, line)` |
| vetítés az utcára | `line.project(point)` / `shapely.line_locate_point` |
| párhuzamos vonal | `line.offset_curve(d)` |
| azonos oldal teszt | `LineString([p1, p2]).crosses(street)` |
| reprezentatív pont | `poly.representative_point()` |
| térbeli index | `shapely.STRtree` |
| vonalak összefűzése | `shapely.ops.linemerge` |

Saját geometriai számítás sehol nem kell.

---

## F. Függelék – eltérések a V2-től (a kód átírásához)

| V2 (`MUKODES.md` 3.) | V3 |
|---|---|
| `drive` hálózat | `all` hálózat; rang 3 lógó végek törölve (1.4) |
| kék segédvonal (határközeli csomópontok kihúzása, 100/150 m) | nincs; a vágás ott keletkezik, ahol a címek kategóriát váltanak és keresztutca torkollik be (8.1), valamint az üres cellák T-feldarabolásánál (3.2, 6.) |
| narancs segédvonal 200 m korláttal, minden zsákutcából | korlát nélkül, csak rang 4–5 zsákutcából (1.5) |
| párhuzamos snap (10/15 m) | nincs; a határ melletti keskeny cella sliverként olvad be (3.) |
| V-zászló törlése (8 m / 30°) | nincs; a háromszög sliverként olvad be (3.4) |
| cellák = tömbök | tömbök → utcafront-sávok + sarokcellák (4., 5.) |
| vegyes cella → Voronoi + kiegyenesítés | sáv → 1D vetítés, merőleges/párhuzamos vágás; Voronoi csak keveredő szakaszon, sarokban, foltban (8.) |
| üres cella → leghosszabb közös él | utca menti folytonosság → rang-szabály → legközelebbi (9.) |
| éltábla nincs; coverage-újraépítés egyszer | éltábla; R. rutin minden fázis végén |
| polír három kozmetikai lépéssel | csak validáció (10.3) |
| ~20 tapasztalati paraméter | 4 nevesített, levezetett paraméter (0.5) |
