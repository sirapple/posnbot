import hashlib
import json
import os
import time
from datetime import datetime
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= CONFIGURATION =================
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()
TARGET_URL = "https://www.sc.su.ac.th/"
BOT_NAME = "POSNBot"

MENTION_TEXT = "@here"  # Use "@everyone", "<@&ROLE_ID>", or "" for no ping
KEYWORDS = ["posn", "สอวน", "โอลิมปิกวิชาการ"]  # Set to [] to alert on ALL updates

CHECK_INTERVAL = 30       # Check every 30 seconds
MAX_RUNTIME = 13.5 * 60   # Run for 13.5 minutes per session

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "posn_state.json")
# =================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    )
}


def matches_keywords(text: str) -> bool:
    if not KEYWORDS:
        return True
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in KEYWORDS)


def fetch_page(url: str) -> str:
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
    except requests.exceptions.SSLError:
        res = requests.get(url, headers=HEADERS, timeout=15, verify=False)
    res.raise_for_status()
    res.encoding = res.apparent_encoding
    return res.text


def parse_page(html: str, base_url: str) -> tuple[dict[str, str], list[str], str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "meta"]):
        tag.decompose()

    links: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urljoin(base_url, href)
        title = " ".join(a.get_text().split())
        if len(title) >= 4:
            links[full_url] = title

    lines = [
        " ".join(line.split())
        for line in soup.get_text("\n").splitlines()
        if len(" ".join(line.split())) >= 4
    ]
    clean_text = "\n".join(lines)
    text_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
    return links, lines, text_hash


def load_state() -> dict | None:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(links: dict[str, str], lines: list[str], text_hash: str) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"links": links, "lines": lines, "text_hash": text_hash},
            f,
            ensure_ascii=False,
            indent=2,
        )


def send_test_notification() -> None:
    payload = {
        "username": BOT_NAME,
        "content": f"{MENTION_TEXT} *(Test message)*".strip(),
        "allowed_mentions": {"parse": ["everyone", "roles", "users"]},
        "embeds": [
            {
                "title": "✅ POSNBot Test Notification",
                "url": TARGET_URL,
                "description": (
                    "POSNBot is connected and working! You will receive an alert like this "
                    "whenever a new **POSN / สอวน.** update appears on **sc.su.ac.th**."
                ),
                "color": 0x00A86B,
                "footer": {"text": f"Tested at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"},
            }
        ],
    }
    res = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    res.raise_for_status()
    print(f"[{datetime.now():%H:%M:%S}] Test notification sent to Discord!")


def send_discord_notification(
    new_links: list[tuple[str, str]], matched_lines: list[str] | None = None
) -> None:
    if new_links:
        lines = [f"• **[{title}]({url})**" for url, title in new_links[:10]]
        if len(new_links) > 10:
            lines.append(f"*...and {len(new_links) - 10} more new links.*")
        description = "\n".join(lines)
        title = f"📢 POSN Update on sc.su.ac.th ({len(new_links)} new item{'s' if len(new_links) > 1 else ''})"
    elif matched_lines:
        snippets = [f"• {line[:180]}" for line in matched_lines[:5]]
        description = "New POSN-related text appeared on the page:\n" + "\n".join(snippets)
        title = "🔄 POSN Text Updated on sc.su.ac.th"
    else:
        return

    payload = {
        "username": BOT_NAME,
        "content": MENTION_TEXT,
        "allowed_mentions": {"parse": ["everyone", "roles", "users"]},
        "embeds": [
            {
                "title": title,
                "url": TARGET_URL,
                "description": description,
                "color": 0x00A86B,
                "footer": {"text": f"Checked at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"},
            }
        ],
    }

    res = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    res.raise_for_status()


def run_loop() -> None:
    if not WEBHOOK_URL.startswith("http"):
        raise ValueError("WEBHOOK_URL secret is missing or invalid.")

    if os.environ.get("SEND_TEST", "").lower() == "true":
        send_test_notification()

    state = load_state()
    start_time = time.time()

    while True:
        try:
            html = fetch_page(TARGET_URL)
            current_links, current_lines, current_hash = parse_page(html, TARGET_URL)

            if state is None:
                save_state(current_links, current_lines, current_hash)
                state = {"links": current_links, "lines": current_lines, "text_hash": current_hash}
                print(f"[{datetime.now():%H:%M:%S}] Baseline saved ({len(current_links)} links indexed).")
            else:
                old_links = state.get("links", {})
                old_lines = set(state.get("lines", []))

                new_items = [
                    (url, title)
                    for url, title in current_links.items()
                    if url not in old_links and matches_keywords(f"{title} {url}")
                ]

                new_matching_lines = (
                    [line for line in current_lines if line not in old_lines and matches_keywords(line)]
                    if old_lines
                    else []
                )

                if new_items:
                    print(f"[{datetime.now():%H:%M:%S}] Found {len(new_items)} POSN link(s)! Sending alert...")
                    send_discord_notification(new_items)
                elif new_matching_lines:
                    print(f"[{datetime.now():%H:%M:%S}] POSN text changed! Sending alert...")
                    send_discord_notification([], matched_lines=new_matching_lines)
                else:
                    print(f"[{datetime.now():%H:%M:%S}] No POSN updates.")

                if current_links != old_links or current_hash != state.get("text_hash") or not old_lines:
                    save_state(current_links, current_lines, current_hash)
                    state = {"links": current_links, "lines": current_lines, "text_hash": current_hash}

        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] Check failed (will retry in {CHECK_INTERVAL}s): {e}")

        if (time.time() - start_time) + CHECK_INTERVAL >= MAX_RUNTIME:
            print(f"[{datetime.now():%H:%M:%S}] Session complete. Saving state...")
            break

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_loop()
