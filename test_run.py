"""Manual provider check: calls Bright Data but never sends Telegram messages."""
import os
from monitor import state_dir
from brightdata_collector import fetch_posts, CollectionPending

if __name__ == '__main__':
    try:
        posts = fetch_posts(os.environ['FACEBOOK_GROUP_URL'], state_dir(),
                            int(os.getenv('LOOKBACK_MINUTES', '15')), int(os.getenv('MAX_POSTS', '10')))
        for post in posts:
            print(post['date'], post['authorName'], post['url'], 'media:', bool(post['attachments']))
        print('Recent posts:', len(posts))
        # Keep this snapshot for the next /trigger to deliver its results.
    except CollectionPending as error:
        print(error)
