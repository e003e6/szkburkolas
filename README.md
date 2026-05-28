# Szavazókör poligon burkolás



## Adatstruktúra

Frissíthető adatok lekérdezése és összevonása

### Nyers datasetek:

1. Fix adatok: 

   - [Microsoft Building Footprints](https://planetarycomputer.microsoft.com/dataset/ms-buildings) (országosan x épület poligon képfelismeréssel)

   - Választási címjegyzék (minden válsaztási évre kölön tábla, pontso címek és szavazókör azonsoító)

2. Frissülő adatok: 

   - [Open Street Map Ralation](https://www.openstreetmap.org) - épület poligonok (országosan x épület poligon pontos címmel)

   - [Open Street Map Node](https://www.openstreetmap.org) - kordináták (országosan x kordináta pontos címmel)

3. Származtatott adatok: 
   - [Google Maps](https://www.google.com/maps) - kordinátkhozt tartozó pontos címek. A lekérdezéshez használt adatok:
     - Microsoft Buildings teljes dataset (poligonok középpontja, mint kordináta)
     - Open Street Map pontos cím nélküli épület poligonok (poligonok középpontja, mint kordináta)

### Létrehozott datasetek:

1. Merged cím kordináták: 

   1. Open Steet Map cím kordináták: 
      1. Minden OSM Node kordinátája, ahol van pontos házszám
      2. Minden OSM Relations középpontja, ahol van pontos házszám

   1. Google Maps cím kordinták: 
      1. Minden lerkérdezett kordináta, ahol van pontos házszám

2. Címek és kordináták: 

   1. Merged cím kordináták (minden kordináta amihez tudjauk a pontos címet házszámmal)
   2. Választási címjegyzék (címek házszámal és a szvazóköri besorolásuk)

### Algiritmus futásakor lekérdezett adatok:

1. [Open Street Map](https://www.openstreetmap.org) - lakott területi poligonok
2. [Open Street Map](https://www.openstreetmap.org) - utcahálózat









