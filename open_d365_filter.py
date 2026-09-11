"""
Aabner D365 All Production Orders og saetter filter paa ProdId.

Krav: pip install playwright && playwright install chromium

Brug: python open_d365_filter.py 113165
"""

import asyncio
import sys
from playwright.async_api import async_playwright

PROD_ID = sys.argv[1] if len(sys.argv) > 1 else "113165"
D365_URL = "https://comrodgroup-prod.operations.eu.dynamics.com/?cmp=com&mi=ProdTableListPage"
CHROME_PROFILE = r"C:\Users\JohnnyAaskovBendtsen\AppData\Local\Google\Chrome\User Data"


async def main():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=CHROME_PROFILE,
            channel="chrome",
            headless=False,
            args=["--start-maximized"],
        )

        page = await context.new_page()
        print(f"Aabner All Production Orders...")
        await page.goto(D365_URL, wait_until="domcontentloaded", timeout=60000)

        print("Venter paa grid...")
        await page.wait_for_selector(
            ".grid-canvas, [data-dyn-role='ContentBody'] table, .formList, .dyn-groupHeader",
            timeout=30000
        )
        await asyncio.sleep(3)

        print(f"Saetter Quick Filter: {PROD_ID}")
        await page.keyboard.press("Control+f")
        await asyncio.sleep(1)

        quick_filter = page.locator(
            "input.quickFilterInput, input[aria-label*='Filter'], .quickFilter input"
        ).first
        await quick_filter.fill(PROD_ID, timeout=5000)
        await asyncio.sleep(0.5)
        await quick_filter.press("Enter")

        print(f"Filter anvendt - viser ordre {PROD_ID}")
        await asyncio.sleep(3)
        input("Tryk Enter for at lukke...")
        await context.close()


asyncio.run(main())
