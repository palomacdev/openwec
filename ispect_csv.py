import csv, io, pathlib, random

def inspect_csv(path):
    raw = pathlib.Path(path).read_bytes()
    for enc in ["utf-8", "latin-1", "cp1252"]:
        try:
            text = raw.decode(enc); break
        except: pass
    
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    rows = list(reader)
    if not rows: return
    
    print(f"\n{'='*60}")
    print(f"FILE: {path}")
    print(f"ROWS: {len(rows)}")
    print(f"COLS: {len(rows[0])}")
    print(f"\nFIELDS:")
    for k, v in rows[0].items():
        print(f"  {k:<40} = {v}")

# Amostra de cada série e tipo
samples = [
    "raw/wec/13_2024/04_LE MANS/Race/classification/03_Classification_Race.CSV",
    "raw/wec/13_2024/04_LE MANS/Race/analysis/23_Analysis_Race.CSV",
    "raw/elms/19_2024/06_AUTODROMO DO ALGARVE/Race/classification/03_Classification_Race.CSV",
    "raw/elms/19_2024/06_AUTODROMO DO ALGARVE/Race/analysis/23_Analysis_Race.CSV",
    "raw/imsa/24_2024/02_Daytona International Speedway/01_IMSA WeatherTech.../Race/classification/03_Results_Race_Official.CSV",
]

for s in samples:
    inspect_csv(s)