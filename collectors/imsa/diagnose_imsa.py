"""
OpenWEC - IMSA DOM Diagnostic
Checks both old and new IMSA domains and dumps selects + sample links.
"""

import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

TARGETS = [
    ("old", "http://imsa.alkamelsystems.com/"),
    ("new", "https://imsa.results.alkamelcloud.com/"),
]

async def diagnose(url: str, label: str):
    print(f"\n{'='*60}")
    print(f"[{label.upper()}] {url}")
    print('='*60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=20000)
        except Exception as e:
            print(f"  [WARN] {e}")

        await asyncio.sleep(2)

        data = await page.evaluate("""
        () => {
            const selects = Array.from(document.querySelectorAll('select')).map(s => ({
                name: s.name, id: s.id,
                options: Array.from(s.options).slice(0, 5).map(o => ({
                    value: o.value, text: o.text.trim()
                }))
            }));

            const links = Array.from(document.querySelectorAll('a[href]'))
                .map(a => ({ href: a.getAttribute('href'), text: a.textContent.trim().slice(0, 60) }))
                .filter(l => l.href && (l.href.includes('Result') || l.href.includes('.CSV') || l.href.includes('.csv')))
                .slice(0, 5);

            const xhr = [];

            return { title: document.title, selects, sample_links: links };
        }
        """)

        print(f"  Title:   {data['title']}")
        print(f"\n  Selects ({len(data['selects'])}):")
        for s in data['selects']:
            print(f"    name='{s['name']}' id='{s['id']}'")
            for o in s['options']:
                print(f"      value='{o['value']}' → '{o['text']}'")

        print(f"\n  Sample CSV/Result links:")
        for l in data['sample_links']:
            print(f"    {l['href'][:90]}")

        await browser.close()

async def main():
    for label, url in TARGETS:
        await diagnose(url, label)

if __name__ == "__main__":
    asyncio.run(main())