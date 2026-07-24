#!/usr/bin/env python3
"""
Daily Brief generator — free stack.
Pulls recent news from Google News RSS, has Gemini write the brief,
and emails it via Resend. No paid services.

Env vars required:
  GEMINI_API_KEY   - free key from https://aistudio.google.com/apikey
  RESEND_API_KEY   - from https://resend.com/api-keys
  MAIL_FROM        - e.g. "Daily Brief <brief@karunka.lk>"  (verified Resend domain)
  MAIL_TO          - e.g. "jenulvip@gmail.com"
"""

import os
import sys
import datetime
import urllib.parse
import requests
import feedparser

# ---- config ---------------------------------------------------------------

# Each beat = (section title, Google News search query)
BEATS = [
    ("AI & Tech", "artificial intelligence OR AI model OR OpenAI OR Anthropic OR Nvidia"),
    ("New Tech Products", "new gadget launch OR smartphone launch OR wearable OR laptop launch"),
    ("World & Business", "world news OR global economy OR markets"),
    ("Science & Health", "new study OR scientific research OR health research"),
]

ITEMS_PER_BEAT = 8          # how many headlines to feed the model per beat
GEMINI_MODEL = "gemini-2.0-flash"   # free tier

# ---- fetch news -----------------------------------------------------------

def fetch_beat(query):
    """Return a list of (title, source, link) from Google News RSS for a query."""
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query + " when:1d")   # last day
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    feed = feedparser.parse(url)
    items = []
    for entry in feed.entries[:ITEMS_PER_BEAT]:
        source = entry.get("source", {}).get("title", "")
        items.append((entry.title, source, entry.link))
    return items


def build_context():
    blocks = []
    for title, query in BEATS:
        items = fetch_beat(query)
        lines = [f"## {title}"]
        for t, src, link in items:
            lines.append(f"- {t} ({src}) {link}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

# ---- write brief with Gemini ---------------------------------------------

def write_brief(context, today):
    prompt = f"""You are writing a concise "Daily Brief" for {today}.

Below are raw news headlines grouped into four beats, pulled from the last 24 hours.
Synthesize them — do NOT just list them. For each of the four sections, lead with the
single most important item, then a few tight bullets. Where stories connect across
sections, draw the through-line in one line. Keep the whole thing scannable in ~2 minutes.

Sections, in this order:
1. AI & Tech
2. New Tech Products
3. World & Business
4. Science & Health

Format as plain text with a dated header, bold-ish section labels (just capitalized text),
and short bullets starting with "- ". End with a "SOURCES" section listing the most useful
URLs you drew from. Direct, concise tone. No preamble.

RAW HEADLINES:
{context}
"""
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        f"?key={os.environ['GEMINI_API_KEY']}"
    )
    resp = requests.post(
        url,
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

# ---- send via Resend ------------------------------------------------------

def send_email(subject, body):
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
        json={
            "from": os.environ["MAIL_FROM"],
            "to": [os.environ["MAIL_TO"]],
            "subject": subject,
            "text": body,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()

# ---- main -----------------------------------------------------------------

def main():
    today = datetime.date.today().strftime("%A, %B %-d, %Y")
    context = build_context()
    brief = write_brief(context, today)
    result = send_email(f"Daily Brief — {today}", brief)
    print("Sent:", result.get("id"))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
