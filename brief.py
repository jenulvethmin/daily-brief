#!/usr/bin/env python3
"""
Daily Brief generator — free stack.
Pulls recent news from Google News RSS, has Groq write the brief,
formats it as a styled HTML email, and sends via Resend. No paid services.

Env vars required:
  GROQ_API_KEY     - free key from https://console.groq.com/keys
  RESEND_API_KEY   - from https://resend.com/api-keys
  MAIL_FROM        - e.g. "Daily Brief <brief@karunka.lk>"  (verified Resend domain)
  MAIL_TO          - e.g. "jenulvip@gmail.com"
"""

import os
import re
import sys
import html as _html
import datetime
import urllib.parse
import requests
import feedparser

# ---- config ---------------------------------------------------------------

BEATS = [
    ("AI & Tech", "artificial intelligence OR AI model OR OpenAI OR Anthropic OR Nvidia"),
    ("New Tech Products", "new gadget launch OR smartphone launch OR wearable OR laptop launch"),
    ("World & Business", "world news OR global economy OR markets"),
    ("Science & Health", "new study OR scientific research OR health research"),
]

ITEMS_PER_BEAT = 8
GROQ_MODEL = "llama-3.3-70b-versatile"   # free tier

# ---- fetch news -----------------------------------------------------------

def fetch_beat(query):
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query + " when:1d")
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

# ---- write brief with Groq ------------------------------------------------

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

Format as plain text. Put each section title on its own line in ALL CAPS. Start every
bullet with "- ". For the lead item in each section, begin the bullet with a short bold
label followed by a colon. End with a "SOURCES" section listing the most useful URLs you
drew from, one per line starting with "- ". Direct, concise tone. No preamble.

RAW HEADLINES:
{context}
"""
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
        },
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()

# ---- render HTML ----------------------------------------------------------

URL_RE = re.compile(r"(https?://[^\s)]+)")
SECTION_ICONS = [("AI", "\U0001F916"), ("PRODUCT", "\U0001F4F1"),
                 ("WORLD", "\U0001F30D"), ("SCIENCE", "\U0001F52C"),
                 ("HEALTH", "\U0001F52C"), ("SOURCE", "\U0001F517")]


def _icon(header):
    h = header.upper()
    for key, icon in SECTION_ICONS:
        if key in h:
            return icon
    return "•"


def _fmt(text):
    """Escape text and turn bare URLs into links."""
    out, last = [], 0
    for m in URL_RE.finditer(text):
        out.append(_html.escape(text[last:m.start()]))
        url = m.group(1)
        out.append(f'<a href="{_html.escape(url)}" style="color:#4f46e5;'
                    f'text-decoration:none;">link</a>')
        last = m.end()
    out.append(_html.escape(text[last:]))
    return "".join(out)


def _is_header(s):
    letters = [c for c in s if c.isalpha()]
    if not letters or s.startswith(("-", "*")):
        return False
    return sum(c.isupper() for c in letters) / len(letters) > 0.8 and len(s) < 45


def to_html(brief, today):
    parts, in_sources = [], False
    for raw in brief.split("\n"):
        s = raw.strip()
        if not s or s.upper().startswith("DAILY BRIEF"):
            continue
        if _is_header(s):
            in_sources = "SOURCE" in s.upper()
            parts.append(
                f'<h2 style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;'
                f'color:#4f46e5;margin:28px 0 12px;padding-bottom:7px;'
                f'border-bottom:2px solid #eef1f6;">{_icon(s)}&nbsp;&nbsp;'
                f'{_html.escape(s)}</h2>'
            )
        elif s.startswith(("-", "*")):
            content = s[1:].strip()
            if in_sources:
                parts.append(
                    f'<p style="margin:0 0 6px 0;padding-left:16px;text-indent:-16px;'
                    f'color:#6b7280;font-size:13px;line-height:1.5;">'
                    f'<span style="color:#c7cbd1;">&bull;</span>&nbsp; {_fmt(content)}</p>'
                )
                continue
            lead, rest = "", content
            cpos = content.find(":")
            if 0 < cpos < 75 and "http" not in content[:cpos].lower():
                lead, rest = content[:cpos + 1], content[cpos + 1:]
            inner = (f'<strong style="color:#111827;">{_fmt(lead)}</strong>' if lead else "") + _fmt(rest)
            parts.append(
                f'<p style="margin:0 0 11px 0;padding-left:18px;text-indent:-18px;'
                f'color:#374151;font-size:15px;line-height:1.55;">'
                f'<span style="color:#4f46e5;">&bull;</span>&nbsp; {inner}</p>'
            )
        else:
            parts.append(
                f'<p style="margin:0 0 11px 0;color:#374151;font-size:15px;'
                f'line-height:1.55;">{_fmt(s)}</p>'
            )
    body = "\n".join(parts)
    return f"""\
<div style="background:#f4f6f8;padding:24px 12px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 1px 4px rgba(16,24,40,.08);">
    <div style="background:#4f46e5;background:linear-gradient(135deg,#4f46e5,#7c3aed);padding:24px 28px;">
      <div style="color:#ffffff;font-size:21px;font-weight:700;letter-spacing:-.01em;">Daily Brief</div>
      <div style="color:#dfe3ff;font-size:13px;margin-top:3px;">{_html.escape(today)}</div>
    </div>
    <div style="padding:4px 28px 26px;">
      {body}
    </div>
    <div style="padding:15px 28px;border-top:1px solid #eef1f6;color:#9ca3af;font-size:12px;">
      Auto-generated from Google News &middot; written by Groq &middot; delivered via Resend
    </div>
  </div>
</div>"""

# ---- send via Resend ------------------------------------------------------

def send_email(subject, text, html):
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
        json={
            "from": os.environ["MAIL_FROM"],
            "to": [os.environ["MAIL_TO"]],
            "subject": subject,
            "text": text,
            "html": html,
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
    html = to_html(brief, today)
    result = send_email(f"Daily Brief — {today}", brief, html)
    print("Sent:", result.get("id"))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
