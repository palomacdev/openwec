import requests

r = requests.get(
    "https://www.wikidata.org/w/api.php",
    params={
        "action": "wbsearchentities",
        "search": "Filipe Albuquerque", 
        "language": "en",
        "type": "item",
        "limit": "3",
        "format": "json",
    },
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
    }
)
print(r.status_code)
print(r.text[:500])