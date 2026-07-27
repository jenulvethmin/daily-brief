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
    ("New Tech Products", "PRODUCT", [
        "https://www.engadget.com/rss.xml",
        "https://www.theverge.com/rss/index.xml",
    ]),
    ("AI & Tech", "AI", [
        "https://techcrunch.com/feed/",
        "https://www.theverge.com/rss/index.xml",
        "https://feeds.arstechnica.com/arstechnica/index",
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
1. New Tech Products
2. AI & Tech
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
# key -> (accent, soft background tint)
SECTION_STYLE = {
    "AI":      ("#4f46e5", "#eef1ff"),
    "PRODUCT": ("#0d9488", "#e9faf5"),
    "WORLD":   ("#d97706", "#fff5e8"),
    "SCIENCE": ("#e11d48", "#fff0f3"),
}
DEFAULT_STYLE = ("#4f46e5", "#eef1ff")
SOURCE_STYLE = ("#475569", "#f1f5f9")
LINK_ICON = "\U0001F517"


def _key_for(header):
    h = header.upper()
    for k in ICONS:
        if k in h or (k == "SCIENCE" and "HEALTH" in h):
            return k
    return None


def _fmt(text, strong_color="#0f172a"):
    out, last = [], 0
    for m in URL_RE.finditer(text):
        out.append(_html.escape(text[last:m.start()]))
        u = m.group(1)
        out.append(f'<a href="{_html.escape(u)}" style="color:#4f46e5;text-decoration:none;">link</a>')
        last = m.end()
    out.append(_html.escape(text[last:]))
    s = "".join(out)
    # markdown -> html: **bold** then strip any stray markdown symbols
    repl = r'<strong style="color:' + strong_color + r';">\1</strong>'
    s = re.sub(r"\*\*(.+?)\*\*", repl, s)
    s = re.sub(r"__(.+?)__", repl, s)
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


def _rgba(hex_color, alpha):
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _chip(icon, title, accent):
    """Tinted pill with accent text — quieter and more native than a solid fill."""
    return (f'<span style="display:inline-block;background:{_rgba(accent, ".10")};'
            f'color:{accent};font-size:11px;font-weight:700;letter-spacing:.07em;'
            f'text-transform:uppercase;padding:7px 14px;border-radius:999px;'
            f'border:1px solid {_rgba(accent, ".16")};">'
            f'{icon}&nbsp;&nbsp;{_html.escape(title)}</span>')


def _bullet(content, accent):
    return (f'<p class="b" style="margin:16px 0 0 0;padding-left:23px;text-indent:-23px;'
            f'color:#33415a;font-size:15.5px;line-height:1.65;">'
            f'<span style="color:{accent};font-weight:700;font-size:15px;">&#9679;</span>'
            f'&nbsp;&nbsp;{_fmt(content, accent)}</p>')


def _section_card(icon, title, accent, tint, image, bullets):
    """Translucent panel: tinted fill + hairline border + layered shadow + top highlight."""
    img = ""
    if image:
        img = (f'<img src="{_html.escape(image)}" alt="" class="im" '
               f'style="width:100%;max-height:210px;object-fit:cover;border-radius:13px;'
               f'display:block;margin:18px 0 4px;border:1px solid {_rgba(accent, ".14")};" />')
    return (f'<div class="card" style="background:{_rgba(accent, ".055")};'
            f'border:1px solid {_rgba(accent, ".15")};border-radius:18px;'
            f'padding:22px 24px 26px;margin:0 0 18px;'
            f'box-shadow:inset 0 1px 0 rgba(255,255,255,.85),'
            f'0 1px 2px rgba(15,23,42,.035),0 6px 16px {_rgba(accent, ".07")};">'
            f'{_chip(icon, title, accent)}{img}{"".join(bullets)}</div>')


def to_html(brief, today, images, sources):
    sections, cur = [], None
    for raw in brief.split("\n"):
        s = raw.strip()
        clean = s.lstrip("#*•-").strip()
        if not s or clean.upper().startswith(("DAILY BRIEF", "SOURCE")):
            continue
        header = _header_text(s)
        if header:
            if cur:
                sections.append(cur)
            key = _key_for(header)
            accent, tint = SECTION_STYLE.get(key, DEFAULT_STYLE)
            cur = {"icon": ICONS.get(key, "•"), "title": header, "accent": accent,
                   "tint": tint, "image": images.get(key), "bullets": []}
        elif cur is not None and s[0] in "-*•":
            cur["bullets"].append(_bullet(s.lstrip("-*•").strip(), cur["accent"]))
    if cur:
        sections.append(cur)

    parts = [_section_card(sec["icon"], sec["title"], sec["accent"], sec["tint"],
                           sec["image"], sec["bullets"]) for sec in sections]

    if sources:
        accent, tint = SOURCE_STYLE
        links = []
        for title, url in sources:
            links.append(
                f'<p style="margin:13px 0 0 0;padding-left:19px;text-indent:-19px;'
                f'color:#64748b;font-size:13.5px;line-height:1.55;">'
                f'<span style="color:#a9b4c4;">&#9679;</span>&nbsp;&nbsp;'
                f'<a href="{_html.escape(url)}" style="color:#4a5769;text-decoration:none;">'
                f'{_html.escape(title[:110])}</a></p>'
            )
        parts.append(
            f'<div class="card" style="background:{_rgba(accent, ".055")};'
            f'border:1px solid {_rgba(accent, ".15")};border-radius:18px;'
            f'padding:22px 24px 26px;margin:0 0 6px;'
            f'box-shadow:inset 0 1px 0 rgba(255,255,255,.85),'
            f'0 1px 2px rgba(15,23,42,.035),0 6px 16px {_rgba(accent, ".07")};">'
            f'{_chip(LINK_ICON, "Sources", accent)}{"".join(links)}</div>'
        )

    body = "\n".join(parts)
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>Daily Brief</title>
<style>
  /* Fluid by default: the shell fills the client's reading pane up to 880px,
     so large monitors get a wide column and laptops/tablets scale down on
     their own. Only phones need an explicit step-down. */
  @media only screen and (max-width: 620px) {{
    .page   {{ padding: 0 !important; }}
    .shell  {{ border-radius: 0 !important; border-left: 0 !important; border-right: 0 !important; }}
    .hero   {{ padding: 24px 18px !important; }}
    .hdate  {{ font-size: 21px !important; }}
    .body   {{ padding: 16px 16px 4px !important; }}
    .card   {{ padding: 18px 18px 20px !important; border-radius: 15px !important; }}
    .b      {{ font-size: 15px !important; }}
    .im     {{ max-height: 170px !important; }}
    .foot   {{ padding: 16px 18px 22px !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:#eceff4;">
<div class="page" style="background:#eceff4;padding:30px 18px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased;">
  <div class="shell" style="width:100%;max-width:880px;margin:0 auto;background:#ffffff;border:1px solid rgba(15,23,42,.07);border-radius:22px;overflow:hidden;box-shadow:0 1px 2px rgba(15,23,42,.04),0 10px 30px rgba(15,23,42,.075);">
    <div class="hero" style="background:#111a2e;padding:30px 30px 28px;box-shadow:inset 0 -1px 0 rgba(255,255,255,.08);">
      <div style="color:#93a0f5;font-size:11px;font-weight:700;letter-spacing:.17em;text-transform:uppercase;">Your Daily Brief</div>
      <div class="hdate" style="color:#ffffff;font-size:25px;font-weight:800;letter-spacing:-.02em;margin-top:7px;line-height:1.25;">{_html.escape(today)}</div>
    </div>
    <div class="body" style="padding:24px 24px 6px;">
      {body}
    </div>
    <div class="foot" style="padding:18px 30px 26px;color:#94a3b8;font-size:11.5px;text-align:center;line-height:1.6;border-top:1px solid rgba(15,23,42,.06);">
      BBC &middot; The Verge &middot; TechCrunch &middot; Engadget &middot; ScienceDaily<br>written by Groq &middot; delivered via Resend
    </div>
  </div>
</div>
</body>
</html>"""

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
