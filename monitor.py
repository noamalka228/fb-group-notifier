import os
import sys
import time
from datetime import datetime
import requests
from fastapi import FastAPI
import uvicorn
from dotenv import load_dotenv
from apify_client import ApifyClient

# Fix Windows encoding for prints
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf8', buffering=1)

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Facebook Group Notifier API (Apify)")

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

def _escape_html(text: str) -> str:
    if not text:
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

@app.get("/trigger")
def trigger_scrape():
    """API Endpoint to fetch posts via Apify and send via Telegram."""
    log("API Endpoint /trigger called")
    
    group_url = os.getenv("FACEBOOK_GROUP_URL")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    apify_token = os.getenv("APIFY_API_TOKEN")
    
    if not group_url or not bot_token or not chat_id or not apify_token:
        return {"error": "Missing env variables. Need FACEBOOK_GROUP_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, APIFY_API_TOKEN"}

    # Initialize the ApifyClient
    client = ApifyClient(apify_token)

    # Prepare the Apify Actor input
    run_input = {
        "startUrls": [{"url": group_url}],
        "viewOption": "CHRONOLOGICAL",
        "onlyPostsNewerThan": "15 minutes", # Strict filtering on Apify's side
        "maxPosts": 10, # Hard limit
    }

    log("  Starting Apify Actor (apify/facebook-groups-scraper)...")
    try:
        # Run the Actor and wait for it to finish
        run = client.actor("apify/facebook-groups-scraper").call(run_input=run_input)
        
        # Fetch Actor results from the run's dataset
        dataset_items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        log(f"  Apify run finished. Found {len(dataset_items)} new posts.")
        
    except Exception as e:
        log(f"  [ERROR] Apify execution failed: {e}")
        return {"error": str(e), "status": "failed"}

    sent_messages = []
    
    for item in reversed(dataset_items):
        # Media / Image Filtering
        media = item.get("media") or item.get("images") or item.get("attachments") or item.get("photos") or []
        if not media:
            log("  >> Skipping post: No images found.")
            continue

        # Robust Author Extraction
        author = "Unknown Author"
        if isinstance(item.get("user"), dict) and item["user"].get("name"):
            author = item["user"]["name"]
        elif isinstance(item.get("author"), dict) and item["author"].get("name"):
            author = item["author"]["name"]
        else:
            author = item.get("authorName") or item.get("userName") or item.get("user_name") or "Unknown Author"

        # Robust Text Extraction
        text = item.get("text") or item.get("message") or item.get("postText") or item.get("content") or item.get("caption") or ""
        
        # Robust URL & Date Extraction
        post_url = item.get("url") or item.get("postUrl") or item.get("link") or ""
        timestamp = item.get("date") or item.get("time") or item.get("createdAt") or item.get("timestamp") or ""
        post_id = item.get("postId") or item.get("id") or "unknown"
        
        if author == "Unknown Author" and not text:
            log(f"  [DEBUG] Schema mismatch detected. Available keys: {list(item.keys())}")

        lines = [f"📢 <b>New post in group</b>"]
        lines.append(f"👤 <b>{_escape_html(author)}</b>")
        if timestamp:
            lines.append(f"🕐 {_escape_html(str(timestamp))}")
        lines.append("")
        if text:
            lines.append(_escape_html(text[:1000]))
        if post_url:
            lines.append(f"\n🔗 <a href=\"{post_url}\">Open post</a>")

        message = "\n".join(lines)
        log(f"  >> Sending post by {author} to Telegram")
        success = send_telegram(bot_token, chat_id, message)
        
        sent_messages.append({"id": post_id, "success": success})
        time.sleep(1)

    return {
        "status": "success",
        "posts_found": len(dataset_items),
        "messages_sent": sent_messages
    }

if __name__ == "__main__":
    print("="*60)
    print("  Facebook Group → Telegram Notifier (APIFY VERSION)")
    print("  Listening on http://0.0.0.0:8000")
    print("  Call GET http://localhost:8000/trigger to fetch posts")
    print("="*60)
    uvicorn.run("monitor:app", host="0.0.0.0", port=8000, reload=False)
