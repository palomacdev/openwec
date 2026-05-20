import json
from collections import Counter

log = json.load(open(r"C:\dev\openwec\catalog\imsa\ingest_log.json"))
ok = [e["url"].split("/")[-1][:25] for e in log if e["status"] == "ok"]
for prefix, n in Counter(ok).most_common(30):
    print(f"{n:4d}  {prefix}")