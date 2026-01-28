import pickle
import requests



def varosnev_hu(varos):
    '''
    A címek az adatbázisba duplikálva voltak minden nyelvű városnévvel.
    Az adatbázisba a duplikáltak törlése után sok helyen a településnév nem magyarul maradt benne.
    A függvény osm-ről lekérdezi a település (elsődleges) magyar nevét.
    '''

    r = requests.get("https://nominatim.openstreetmap.org/search", params={
        "format": "json",
        "q": varos,
        "limit": 1,
        "addressdetails": 1,
        "accept-language": "hu"
    }, headers={"User-Agent": "hu-cityname/1.0"})

    j = r.json()
    if not j:
        return varos

    a = j[0]["address"]
    return a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or a.get('river')




with open("../../adatok/working/varosnevek_lekerdezni.pkl", "rb") as f:
    u = pickle.load(f)

osszes_db = len(u)
print(osszes_db, 'város lekérdezése')

m = {}
lekert = 0
for x in u:
    mn = varosnev_hu(x)
    lekert += 1
    print(x, mn, round((lekert/osszes_db)*100, 2), '%')
    m[x] = mn


with open("../../adatok/fix/varosnevek_hu_map.pkl", "wb") as f:
    pickle.dump(m, f)

