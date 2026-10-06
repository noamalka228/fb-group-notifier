import os
import sys
import time
import argparse
import json
import sqlite3
import secrets
from pathlib import Path
from datetime import datetime
import requests
from fastapi import FastAPI, Request, HTTPException
import uvicorn
from dotenv import load_dotenv
from brightdata_collector import CollectionPending, fetch_posts, clear_pending

# Fix Windows encoding for prints
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf8')

load_dotenv(Path(__file__).with_name('.env'))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Facebook Group Notifier API (Bright Data)")

def state_dir():
    path = Path(os.getenv('STATE_DIR', str(Path(SCRIPT_DIR) / 'state')))
    if not path.is_absolute():
        path = Path(SCRIPT_DIR) / path
    path.mkdir(parents=True, exist_ok=True)
    return path

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
            log(f"  [WARN] Telegram API returned {resp.status_code}")
            if resp.status_code != 400:
                return False
            payload.pop("parse_mode")
            resp = requests.post(url, json=payload, timeout=15)
        return resp.ok and resp.json().get('ok', False)
    except Exception as e:
        log(f"  [ERROR] Telegram send failed: {type(e).__name__}")
        return False

def _escape_html(text: str) -> str:
    if not text:
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def trigger_scrape():
    """Serialize cron/API runs and remember only successfully delivered posts."""
    db = sqlite3.connect(state_dir() / 'notifier.sqlite3', timeout=0)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY)')
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        result = _trigger_scrape(db)
        db.commit()
        if result.get('status') == 'success':
            clear_pending(state_dir())
        return result
    except sqlite3.OperationalError:
        return {'status': 'failed', 'error': 'State database unavailable or another run is active'}
    finally:
        db.close()

@app.get('/trigger')
def trigger_route(request: Request):
    expected = os.getenv('TRIGGER_SECRET', '')
    if not expected:
        raise HTTPException(status_code=503, detail='Trigger authentication is not configured')
    supplied = request.headers.get('X-Trigger-Secret', '')
    if not supplied or not secrets.compare_digest(supplied.encode('utf8'), expected.encode('utf8')):
        raise HTTPException(status_code=401, detail='Unauthorized')
    result = trigger_scrape()
    if result.get('status') == 'success':
        return sum(item['success'] for item in result['messages_sent'])
    return result

def _trigger_scrape(db):
    log("API Endpoint /trigger called")
    
    group_url = os.getenv("FACEBOOK_GROUP_URL")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not group_url or not bot_token or not chat_id:
        return {"status": "failed", "error": "Missing FACEBOOK_GROUP_URL, TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"}

    log("  Starting or resuming Bright Data collection...")
    try:
        minutes = int(os.getenv('LOOKBACK_MINUTES', '15'))
        limit = int(os.getenv('MAX_POSTS', '10'))
        if minutes < 1 or limit < 1:
            raise ValueError('LOOKBACK_MINUTES and MAX_POSTS must be positive')
        dataset_items = fetch_posts(group_url, state_dir(), minutes, limit)
        log(f"  Bright Data collection finished. Found {len(dataset_items)} recent posts.")
        
    except CollectionPending as e:
        return {"status": "pending", "snapshot_id": e.snapshot_id, "message": str(e)}
    except Exception as e:
        log(f"  [ERROR] Bright Data collection failed: {e}")
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
        if post_id == 'unknown' or db.execute('SELECT 1 FROM sent WHERE id=?', (post_id,)).fetchone():
            continue
        
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
            lines.append(f"\n🔗 <a href=\"{_escape_html(post_url).replace(chr(34), '&quot;')}\">Open post</a>")

        message = "\n".join(lines)
        log(f"  >> Sending post by {author} to Telegram")
        success = send_telegram(bot_token, chat_id, message)
        if success:
            db.execute('INSERT OR IGNORE INTO sent(id) VALUES (?)', (post_id,))
        
        sent_messages.append({"id": post_id, "success": success})
        time.sleep(1)

    return {
        "status": "success" if all(item['success'] for item in sent_messages) else "partial_failure",
        "posts_found": len(dataset_items),
        "messages_sent": sent_messages
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Collect once for cron')
    args = parser.parse_args()
    if args.once:
        result = trigger_scrape()
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result.get('status') in ('success', 'pending') else 1)
    print("="*60)
    print("  Facebook Group → Telegram Notifier (BRIGHT DATA)")
    print("  Listening on http://127.0.0.1:8000")
    print("  Call GET http://localhost:8000/trigger to fetch posts")
    print("="*60)
    uvicorn.run(app, host=os.getenv('HOST', '127.0.0.1'), port=8000)
