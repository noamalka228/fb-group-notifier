import asyncio
from monitor import fetch_and_parse_group, is_recent_timestamp
import os
from dotenv import load_dotenv
from playwright.async_api import async_playwright
import random

load_dotenv()

async def main():
    group_url = os.getenv("FACEBOOK_GROUP_URL", "https://www.facebook.com/groups/1253641912158395")
    print(f"Fetching {group_url} to debug timestamps...")
    
    ua_profiles = [
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "ch": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "plat": '"Windows"'
        }
    ]
    profile = random.choice(ua_profiles)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            user_agent=profile["ua"],
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "sec-ch-ua": profile["ch"],
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": profile["plat"],
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1"
            }
        )
        page = await context.new_page()
        
        await page.goto(group_url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(5000)
        
        for _ in range(4):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(3000)
            
        articles = await page.query_selector_all('[role="article"]')
        print(f"Total articles found on page: {len(articles)}")
        
        for i, article in enumerate(articles):
            text_content = await article.inner_text()
            if not text_content: continue
            lines = [line.strip() for line in text_content.split("\n") if line.strip()]
            if len(lines) < 2: continue
            
            author = lines[0]
            timestamp_line = lines[1]
            print(f"Post {i}: Author='{author}', Timestamp='{timestamp_line}', Included={is_recent_timestamp(timestamp_line, 60)}")

        await page.screenshot(path="debug_screenshot.png")
        print("Saved debug_screenshot.png")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
