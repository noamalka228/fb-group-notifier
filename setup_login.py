"""
One-time Facebook login setup.
Opens a browser window for you to log into Facebook manually.
Saves the session so the monitor can run headlessly.
"""
import asyncio
import os
import json
from playwright.async_api import async_playwright

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "browser_state.json")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")


async def main():
    print("="*60)
    print("  Facebook Group Notifier - Login Setup")
    print("="*60)
    print()

    # Validate config
    if not os.path.exists(CONFIG_FILE):
        print("[ERROR] config.json not found. Please create it first.")
        return

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    if config.get("telegram_bot_token", "").startswith("PASTE"):
        print("[WARNING] You haven't set your Telegram bot token in config.json yet.")
        print("          You can still proceed with Facebook login setup.")
        print()

    print("A browser window will open. Please:")
    print("  1. Log in to your Facebook account")
    print("  2. Navigate to your target group to verify access")
    print("  3. Come back here and press Enter")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = await context.new_page()
        await page.goto("https://www.facebook.com/")

        input(">>> Press Enter here AFTER you have logged in to Facebook... ")

        # Save session state
        await context.storage_state(path=STATE_FILE)
        await browser.close()

    print()
    print(f"[OK] Session saved to {STATE_FILE}")
    print("     You can now run monitor.py")


if __name__ == "__main__":
    asyncio.run(main())
