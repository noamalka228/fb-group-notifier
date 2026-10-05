"""
Facebook Group → Telegram Notifier (API Mode)
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from urllib.parse import urljoin

import requests
from playwright.async_api import async_playwright
from fastapi import FastAPI
import uvicorn
from dotenv import load_dotenv

# Fix Windows encoding for prints
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf8', buffering=1)

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "monitor.log")

app = FastAPI(title="Facebook Group Notifier API")

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")

def send_telegram(bot_token: str, chat_id: str, message: str):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    if len(message) > 4000:
        message = message[:4000] + "\n\n... (truncated)"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            log(f"  [WARN] Telegram API returned {resp.status_code}: {resp.text}")
            payload["parse_mode"] = ""
            resp = requests.post(url, json=payload, timeout=15)
        return resp.ok
    except Exception as e:
        log(f"  [ERROR] Telegram send failed: {e}")
        return False

def is_recent_timestamp(time_str: str, max_minutes: int = 60) -> bool:
    """Parse Facebook time string and return True if it's <= max_minutes."""
    t = time_str.lower().strip()
    if "just now" in t:
        return True
    
    # Check for minutes
    if "minute" in t or re.match(r'^\d+\s*m$', t):
        nums = re.findall(r'\d+', t)
        if nums and int(nums[0]) <= max_minutes:
            return True
            
    # Check for hours
    if "hour" in t or "hr" in t or re.match(r'^\d+\s*h$', t):
        nums = re.findall(r'\d+', t)
        hours = int(nums[0]) if nums else 1 # e.g., "about an hour ago" -> 1
        if hours * 60 <= max_minutes:
            return True
            
    return False

async def fetch_and_parse_group(group_url: str) -> list[dict]:
    log(f"  Fetching: {group_url}")
    posts = []
    group_url = group_url.replace("mbasic.facebook.com", "www.facebook.com")

    import random
    ua_profiles = [
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "ch": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "plat": '"Windows"'
        },
        {
            "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "ch": '"Google Chrome";v="130", "Chromium";v="130", "Not?A_Brand";v="99"',
            "plat": '"macOS"'
        },
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
            "ch": '"Microsoft Edge";v="129", "Chromium";v="129", "Not=A?Brand";v="8"',
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

        try:
            await page.goto(group_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(5000)

            for _ in range(4):
                try:
                    close_btn = await page.query_selector('[aria-label="Close"]')
                    if close_btn:
                        await close_btn.click()
                except:
                    pass
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(3000)

            articles = await page.query_selector_all('[role="article"]')
            
            for article in articles:
                text_content = await article.inner_text()
                if not text_content or len(text_content.strip()) < 10:
                    continue
                    
                lines = [line.strip() for line in text_content.split("\n") if line.strip()]
                if len(lines) < 2:
                    continue

                author = lines[0]
                timestamp_line = lines[1]
                
                # Filter: Only keep posts made in the last ~10 minutes (12 min buffer)
                if not is_recent_timestamp(timestamp_line, max_minutes=12):
                    continue

                links = await article.query_selector_all('a')
                post_link = ""
                is_comment = False
                
                for link in links:
                    href = await link.get_attribute('href')
                    if href:
                        if 'comment_id=' in href:
                            is_comment = True
                        if ('/groups/' in href or '/posts/' in href or '/permalink/' in href) and 'user' not in href:
                            if not post_link or len(href) < len(post_link):
                                post_link = href
                                
                if is_comment:
                    continue
                    
                if post_link:
                    post_link = post_link.split('?')[0]
                    post_link = urljoin("https://www.facebook.com", post_link)
                
                clean_text_lines = []
                for line in lines[2:]: 
                    if line in ["See more", "Like", "Comment", "Share", "View more comments"]:
                        continue
                    if re.match(r'^\d+[hm]$', line):
                        continue
                    if line.startswith("about ") and " ago" in line:
                        continue
                    if line == "·":
                        continue
                    clean_text_lines.append(line)
                    
                clean_text = "\n".join(clean_text_lines)
                
                posts.append({
                    "author": author,
                    "timestamp": timestamp_line,
                    "text": clean_text[:1000],
                    "link": post_link,
                })

        except Exception as e:
            log(f"  [ERROR] Failed to fetch or parse page: {e}")
        finally:
            await browser.close()

    return posts

def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

@app.get("/trigger")
async def trigger_check():
    """API Endpoint to fetch posts <= 12 mins old and send via Telegram."""
    log("API Endpoint /trigger called")
    
    group_url = os.getenv("FACEBOOK_GROUP_URL")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not group_url or not bot_token or not chat_id:
        return {"error": "Missing environment variables (FACEBOOK_GROUP_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)"}

    posts = await fetch_and_parse_group(group_url)
    log(f"  Found {len(posts)} recent posts (<= 10 mins old)")

    sent_messages = []
    for post in reversed(posts): 
        lines = [f"📢 <b>New post in group</b>"]
        lines.append(f"👤 <b>{_escape_html(post['author'])}</b>")
        lines.append(f"🕐 {_escape_html(post['timestamp'])}")
        lines.append("")
        if post["text"]:
            lines.append(_escape_html(post["text"]))
        if post["link"]:
            lines.append(f"\n🔗 <a href=\"{post['link']}\">Open post</a>")

        message = "\n".join(lines)
        log(f"  >> Sending post by {post['author']} ({post['timestamp']})")
        send_telegram(bot_token, chat_id, message)
        sent_messages.append(post)
        await asyncio.sleep(1)

    return {
        "status": "success",
        "posts_found": len(posts),
        "messages_sent": sent_messages
    }

if __name__ == "__main__":
    print("="*60)
    print("  Facebook Group → Telegram Notifier API")
    print("  Listening on http://0.0.0.0:8000")
    print("  Call GET http://localhost:8000/trigger to fetch posts")
    print("="*60)
    uvicorn.run("monitor:app", host="0.0.0.0", port=8000, reload=False)
