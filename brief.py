#!/usr/bin/env python3
"""
Daily Brief generator — free stack.
Pulls recent news (with summaries + images) from topical RSS feeds, has Groq
write a detailed brief, renders a styled HTML email, and sends it via Resend.

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
import requests
import feedparser

# ---- config ---------------------------------------------------------------

# beat -> (short key for icon/image matching, [rss feeds])
BEATS = [
    ("AI & Tech", "AI", [
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        "https://feeds.arstechnica.com/arstechnica/index",
    ]),
    ("New Tech Products", "PRODUCT", [
        "https://www.engadget.com/rss.xml",
        "https://www.theverge.com/rss/index.xml",
    ]),
    ("World & Business", "WORLD", [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ]),
    ("Science & Health", "SCIENCE", [
        "https://www.sciencedaily.com/rss/top/science.xml",
        "https://feeds.bbci.co.uk/news/health/rss.xml",
    ]),
]

ITEMS_PER_BEAT = 6          # headlines+summaries fed to the model per beat
GROQ_MODEL = "llama-3.3-70b-versatile"   # free tier

# ---- fetch news -----------------------------------------------------------

def _clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()


def _entry_image(e):
    for attr in ("media_thumbnail", "media_content"):
        for it in e.get(attr, []) or []:
            if it.get("url"):
                return it["url"]
    for l in e.get("links", []):
        if l.get("type", "").startswith("image") and l.get("href"):
            return l["href"]
    for enc in e.get("enclosures", []):
        if "image" in enc.get("type", "") and enc.get("href"):
            return enc["href"]
    blob = e.get("summary", "")
    for c in e.get("content", []) or []:
        blob += c.get("value", "")
    m = re.search(r"<img[^>]+src=[\"']([^\"']+)", blob)
    return m.group(1) if m else None


def gather():
    """Return (context_text, images{key:url}, sources[(title,url)])."""
    blocks, images, sources = [], {}, []
    for title, key, feeds in BEATS:
        items = []
        for url in feeds:
            try:
                for e in feedparser.parse(url).entries:
                    items.append(e)
            except Exception:
                continue
        items = items[:ITEMS_PER_BEAT * 2][:ITEMS_PER_BEAT]
        lines = [f"## {title}"]
        for e in items:
            summ = _clean(e.get("summary", ""))[:280]
            lines.append(f"- {e.get('title', '').strip()} — {summ}")
            if key not in images:
                img = _entry_image(e)
                if img:
                    images[key] = img
        for e in items[:2]:
            if e.get("link"):
                sources.append((e.get("title", "").strip(), e["link"]))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks), images, sources

# ---- write brief with Groq ------------------------------------------------

def write_brief(context, today):
    prompt = f"""You are writing a substantive "Daily Brief" for {today}.

Below are real news items (headline — summary) from the last day, grouped into four beats.
Write an informative brief that conveys the ACTUAL facts, not vague one-liners.

Rules:
- For each of the four sections, write 3 to 4 bullets.
- Each bullet is EXACTLY ONE sentence — never a paragraph. Pack it with the specific
  keywords: proper names, companies, products, places, numbers/figures. Cover the key
  point of the story and no more. No filler, no "this is important", no second sentence.
- Stay faithful to the provided summaries; do not invent facts.
- Lead each section with its single most important story.

Formatting (follow EXACTLY):
- Put each section title ALONE on its own line, plain ALL CAPS text. No "#", no asterisks,
  no numbering — just the words, e.g. AI & TECH
- Start every bullet with "- " (a hyphen and a space). Never use "*" or "•" for bullets.
- To emphasise a short lead label, wrap it in **double asterisks**, e.g.
  "- **OpenAI ships agents:** the company launched ...". Use bold sparingly, nowhere else.
- Do NOT use any other markdown (#, *, >, tables). Do NOT write a SOURCES section.
- Do NOT invent facts beyond the material.

Sections, in this order:
1. AI & Tech
2. New Tech Products
3. World & Business
4. Science & Health

NEWS ITEMS:
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
    return resp.json()["choices"][0]["message"]["content"].strip()

# ---- render HTML ----------------------------------------------------------

URL_RE = re.compile(r"(https?://[^\s)]+)")
ICONS = {"AI": "\U0001F916", "PRODUCT": "\U0001F4F1",
         "WORLD": "\U0001F30D", "SCIENCE": "\U0001F52C"}


def _key_for(header):
    h = header.upper()
    for k in ICONS:
        if k in h or (k == "SCIENCE" and "HEALTH" in h):
            return k
    return None


def _fmt(text):
    out, last = [], 0
    for m in URL_RE.finditer(text):
        out.append(_html.escape(text[last:m.start()]))
        u = m.group(1)
        out.append(f'<a href="{_html.escape(u)}" style="color:#4f46e5;text-decoration:none;">link</a>')
        last = m.end()
    out.append(_html.escape(text[last:]))
    s = "".join(out)
    # markdown -> html: **bold** then strip any stray markdown symbols
    s = re.sub(r"\*\*(.+?)\*\*", r'<strong style="color:#111827;">\1</strong>', s)
    s = re.sub(r"__(.+?)__", r"<strong>\1</strong>", s)
    s = s.replace("**", "").replace("__", "")
    return s


def _header_text(s):
    """Return cleaned header text if this line is a section header, else None."""
    h = s.lstrip("#").strip()
    m = re.match(r"^\*\*(.+?)\*\*$", h)
    if m:
        h = m.group(1).strip()
    letters = [c for c in h if c.isalpha()]
    if not letters or h.startswith(("-", "•")):
        return None
    if sum(c.isupper() for c in letters) / len(letters) > 0.8 and len(h) < 45:
        return h
    return None


def _img_html(url):
    return (f'<img src="{_html.escape(url)}" alt="" style="width:100%;max-height:200px;'
            f'object-fit:cover;border-radius:10px;display:block;margin:0 0 14px;" />')


def to_html(brief, today, images, sources):
    parts = []
    for raw in brief.split("\n"):
        s = raw.strip()
        clean = s.lstrip("#*•-").strip()
        if not s or clean.upper().startswith(("DAILY BRIEF", "SOURCE")):
            continue
        header = _header_text(s)
        if header:
            key = _key_for(header)
            icon = ICONS.get(key, "•")
            parts.append(
                f'<h2 style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;'
                f'color:#4f46e5;margin:30px 0 12px;padding-bottom:7px;'
                f'border-bottom:2px solid #eef1f6;">{icon}&nbsp;&nbsp;{_html.escape(header)}</h2>'
            )
            if key and images.get(key):
                parts.append(_img_html(images[key]))
        elif s[0] in "-*•":
            content = s.lstrip("-*•").strip()
            parts.append(
                f'<p style="margin:0 0 13px 0;padding-left:20px;text-indent:-20px;'
                f'color:#374151;font-size:15px;line-height:1.6;">'
                f'<span style="color:#4f46e5;font-weight:700;">&bull;</span>&nbsp;&nbsp;{_fmt(content)}</p>'
            )
        else:
            parts.append(
                f'<p style="margin:0 0 13px 0;color:#374151;font-size:15px;line-height:1.6;">{_fmt(s)}</p>'
            )
    if sources:
        parts.append(
            '<h2 style="font-size:12px;letter-spacing:.09em;text-transform:uppercase;'
            'color:#4f46e5;margin:30px 0 12px;padding-bottom:7px;'
            'border-bottom:2px solid #eef1f6;">\U0001F517&nbsp;&nbsp;Sources</h2>'
        )
        for title, url in sources:
            parts.append(
                f'<p style="margin:0 0 6px 0;padding-left:16px;text-indent:-16px;'
                f'color:#6b7280;font-size:13px;line-height:1.5;">'
                f'<span style="color:#c7cbd1;">&bull;</span>&nbsp; '
                f'<a href="{_html.escape(url)}" style="color:#6b7280;text-decoration:none;">'
                f'{_html.escape(title[:110])}</a></p>'
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
      Auto-generated from BBC, The Verge, TechCrunch, Engadget &amp; ScienceDaily &middot; written by Groq &middot; delivered via Resend
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
    context, images, sources = gather()
    brief = write_brief(context, today)
    html = to_html(brief, today, images, sources)
    result = send_email(f"Daily Brief — {today}", brief, html)
    print("Sent:", result.get("id"), "| images:", len(images))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
