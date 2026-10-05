"""
One-time Facebook login setup.
Opens a browser window for you to log into Facebook manually.
Saves the session so the monitor can run headlessly.
"""
import asyncio
import os
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "browser_state.json")

async def main():
    print("="*60)
    print("  Facebook Group Notifier - Login Setup")
    print("="*60)
    print()

    print("A browser window will open. Please:")
    print("  1. Log in to your burner Facebook account")
    print("  2. Wait for the feed to load completely")
    print("  3. Come back to this terminal and press Enter")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = await context.new_page()
        
        group_url = os.getenv("FACEBOOK_GROUP_URL", "https://www.facebook.com/")
        await page.goto(group_url)

        input(">>> Press Enter here AFTER you have logged in to Facebook... ")

        # Save session state
        await context.storage_state(path=STATE_FILE)
        await browser.close()

    print()
    print(f"[OK] Session saved to {STATE_FILE}")
    print("     You can now copy the contents of this file into your Render FB_COOKIES variable.")

if __name__ == "__main__":
    asyncio.run(main())
