"""Story Manager (intake, chapters, rights) and Script Builder (narration script per episode)."""
from __future__ import annotations

import json
import re
import shutil
import unicodedata
from pathlib import Path

import yaml

from .config import get_settings
from .db import connect, jsonb, log_event

# Rough Vietnamese narration speed used only for planning episode length before TTS.
EST_CHARS_PER_SEC = 14.0
MAX_EPISODE_SEC = 60 * 60

SECTION_BREAK = re.compile(r"^\s*(\*\s*){3,}\s*$|^\s*-{3,}\s*$")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])[\"”»']?\s+")
MAX_CHUNK_CHARS = 220


class StoryError(ValueError):
    pass


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "story"


def parse_story_file(path: Path) -> dict:
    raw = unicodedata.normalize("NFC", path.read_text(encoding="utf-8"))
    meta: dict = {}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("\n---", 1)
        if len(parts) != 2:
            raise StoryError("front matter not closed with ---")
        meta = yaml.safe_load(parts[0][3:]) or {}
        body = parts[1].lstrip("-\n")
    if not meta.get("title"):
        m = re.search(r"^#\s+(.+)$", body, re.M)
        meta["title"] = m.group(1).strip() if m else path.stem
    body = re.sub(r"^#\s+.+$", "", body, count=1, flags=re.M)

    chapters = []
    pieces = re.split(r"^##\s+(.+)$", body, flags=re.M)
    if len(pieces) == 1:
        chapters.append({"title": meta["title"], "content": pieces[0].strip()})
    else:
        if pieces[0].strip():
            chapters.append({"title": "Mở đầu", "content": pieces[0].strip()})
        for i in range(1, len(pieces), 2):
            chapters.append({"title": pieces[i].strip(), "content": pieces[i + 1].strip()})
    chapters = [c for c in chapters if c["content"]]
    if not chapters:
        raise StoryError("story has no content")
    # A series bible contains spoilers and production notes, not all narration.
    script_heading = re.compile(r"^(?:[IVX]+\.\s*)?KỊCH BẢN TẬP\s+(\d+)\b", re.I)
    script_indices = [(i, script_heading.match(c["title"])) for i, c in enumerate(chapters)
                      if script_heading.match(c["title"])]
    if script_indices:
        if len(script_indices) != 1:
            raise StoryError("Tài liệu có nhiều kịch bản tập; hãy tách mỗi tập thành một tài liệu.")
        start, heading = script_indices[0]
        selected = []
        for ch in chapters[start + 1:]:
            if not re.match(r"^Cảnh\s+\d+\b", ch["title"], re.I):
                break
            selected.append(ch)
        if not selected:
            raise StoryError("Đã thấy phần KỊCH BẢN TẬP nhưng chưa có tiêu đề 'Cảnh 01…'; cần kiểm tra tài liệu.")
        episode_no = int(heading.group(1))
        preamble = pieces[0].strip().splitlines()
        series = meta.get("series") or (preamble[0].strip() if preamble else meta["title"])
        meta["title"] = f"{series} — Tập {episode_no:02d}"
        meta["series"] = series
        # Use a new identity for the selected episode if the full bible was imported before.
        if meta.get("slug"):
            meta["slug"] += f"-tap-{episode_no:02d}"
        meta["intake"] = {"kind": "story_bible", "episode_no": episode_no,
                          "selected_sections": [c["title"] for c in selected],
                          "excluded_sections": [c["title"] for c in chapters if c not in selected]}
        chapters = selected
    elif re.search(r"SERIES BIBLE|STORY BIBLE", body, re.I):
        raise StoryError("Đây là tài liệu Story Bible nhưng chưa nhận diện được kịch bản tập. Hãy tách phần lời kể trước.")
    return {"meta": meta, "chapters": chapters}


def import_story(path: Path) -> dict:
    """Import a story file. Rights start as 'pending' and need an explicit approval."""
    parsed = parse_story_file(path)
    meta = parsed["meta"]
    s = get_settings()
    slug = slugify(meta.get("slug") or meta["title"])
    dest_dir = s.storage / "stories" / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    if path.resolve() != dest.resolve():
        shutil.copyfile(path, dest)
    rights_type = meta.get("rights", "unknown")
    if rights_type not in ("original", "licensed", "public_domain", "unknown"):
        rights_type = "unknown"
    with connect() as c:
        existing = c.execute("SELECT * FROM stories WHERE slug=%s", (slug,)).fetchone()
        if existing:
            return {"story": existing, "created": False}
        story = c.execute("""
            INSERT INTO stories (slug, title, author, source_path, rights_type, rights_note, style_profile, metadata)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (slug, meta["title"], meta.get("author"), str(dest), rights_type, meta.get("rights_note"),
             meta.get("style_profile", "dark_fantasy_cinematic"),
             jsonb({k: meta.get(k) for k in ("characters", "locations", "series", "language", "source_url", "intake",
                                            "continuity", "continuity_updates", "unresolved_questions", "sound_cues", "voice_cast") if k in meta}))
        ).fetchone()
        for no, ch in enumerate(parsed["chapters"], start=1):
            c.execute("INSERT INTO chapters (story_id, chapter_no, title, content, char_count) VALUES (%s,%s,%s,%s,%s)",
                      (story["id"], no, ch["title"], ch["content"], len(ch["content"])))
        c.execute("INSERT INTO approvals (kind, story_id, payload) VALUES ('rights', %s, %s) ON CONFLICT DO NOTHING",
                  (story["id"], jsonb({"rights_type": rights_type, "title": story["title"]})))
        log_event(f"story imported: {story['title']} ({len(parsed['chapters'])} chapters, rights={rights_type})", conn=c)
    return {"story": story, "created": True}


def create_episodes(story_id: int) -> list[dict]:
    """Group chapters into episodes of at most ~60 minutes. Idempotent."""
    with connect() as c:
        story = c.execute("SELECT * FROM stories WHERE id=%s", (story_id,)).fetchone()
        if story is None:
            raise StoryError(f"story {story_id} not found")
        if story["rights_status"] != "approved":
            raise StoryError(f"story {story_id} rights not approved (status={story['rights_status']})")
        existing = c.execute("SELECT * FROM episodes WHERE story_id=%s ORDER BY episode_no", (story_id,)).fetchall()
        if existing:
            return existing
        chapters = c.execute("SELECT chapter_no, title, char_count FROM chapters WHERE story_id=%s ORDER BY chapter_no",
                             (story_id,)).fetchall()
        groups: list[list[dict]] = [[]]
        acc = 0.0
        for ch in chapters:
            est = ch["char_count"] / EST_CHARS_PER_SEC
            if groups[-1] and acc + est > MAX_EPISODE_SEC:
                groups.append([])
                acc = 0.0
            groups[-1].append(ch)
            acc += est
        out = []
        for no, g in enumerate(groups, start=1):
            title = story["title"] if len(groups) == 1 else f"{story['title']} — Tập {no}"
            out.append(c.execute("""INSERT INTO episodes (story_id, episode_no, title, chapter_from, chapter_to, status)
                                    VALUES (%s,%s,%s,%s,%s,'created') RETURNING *""",
                                 (story_id, no, title, g[0]["chapter_no"], g[-1]["chapter_no"])).fetchone())
        return out


# ---------------------------------------------------------------- Script Builder

def normalize_narration(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[*_`#>]+", "", text)
    text = text.replace("“", "\"").replace("”", "\"").replace("...", "…")
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_sentences(paragraph: str) -> list[str]:
    out: list[str] = []
    for sent in SENTENCE_SPLIT.split(paragraph):
        sent = sent.strip()
        if not sent:
            continue
        while len(sent) > MAX_CHUNK_CHARS:
            cut = max(sent.rfind(", ", 0, MAX_CHUNK_CHARS), sent.rfind("; ", 0, MAX_CHUNK_CHARS))
            if cut < 40:
                cut = sent.rfind(" ", 0, MAX_CHUNK_CHARS)
            if cut <= 0:
                cut = MAX_CHUNK_CHARS - 1
            out.append(sent[:cut + 1].strip())
            sent = sent[cut + 1:].strip()
        if sent:
            if out and len(sent) < 12 and not out[-1].endswith((".", "!", "?", "…", "\"")):
                out[-1] += " " + sent
            else:
                out.append(sent)
    return out


def build_lexicon(story: dict) -> dict:
    meta = story.get("metadata") or {}
    def entries(items):
        out = []
        for it in items or []:
            names = [it["name"], *it.get("aliases", [])]
            out.append({"id": it["id"], "names": [n.lower() for n in names if n]})
        return out
    return {"characters": entries(meta.get("characters")), "locations": entries(meta.get("locations"))}


def find_mentions(text: str, entries: list[dict]) -> dict[str, int]:
    low = text.lower()
    found: dict[str, int] = {}
    for e in entries:
        n = sum(len(re.findall(rf"(?<!\w){re.escape(name)}(?!\w)", low)) for name in e["names"])
        if n:
            found[e["id"]] = n
    return found


def build_episode_script(episode_id: int) -> dict:
    with connect() as c:
        ep = c.execute("SELECT * FROM episodes WHERE id=%s", (episode_id,)).fetchone()
        story = c.execute("SELECT * FROM stories WHERE id=%s", (ep["story_id"],)).fetchone()
        chapters = c.execute("""SELECT * FROM chapters WHERE story_id=%s AND chapter_no BETWEEN %s AND %s
                                ORDER BY chapter_no""", (story["id"], ep["chapter_from"], ep["chapter_to"])).fetchall()
    lex = build_lexicon(story)
    sections, para_no, sec_no = [], 0, 0
    for ch in chapters:
        blocks: list[list[str]] = [[]]
        for raw_para in re.split(r"\n\s*\n", ch["content"]):
            if SECTION_BREAK.match(raw_para):
                blocks.append([])
                continue
            para = normalize_narration(raw_para)
            if para:
                blocks[-1].append(para)
        for bi, block in enumerate(b for b in blocks if b):
            sec_no += 1
            paragraphs = []
            for text in block:
                para_no += 1
                paragraphs.append({
                    "paragraph_no": para_no,
                    "text": text,
                    "sentences": split_sentences(text),
                    "characters": find_mentions(text, lex["characters"]),
                    "locations": find_mentions(text, lex["locations"]),
                })
            sections.append({"section_no": sec_no, "chapter_no": ch["chapter_no"],
                             "chapter_title": ch["title"] if bi == 0 else None, "paragraphs": paragraphs})
    script = {
        "episode_id": episode_id,
        "story_id": story["id"],
        "story_slug": story["slug"],
        "story_title": story["title"],
        "title": ep["title"],
        "style_profile": story["style_profile"],
        "characters": (story["metadata"] or {}).get("characters", []),
        "locations": (story["metadata"] or {}).get("locations", []),
        "sections": sections,
    }
    for key in ('series', 'continuity', 'continuity_updates', 'unresolved_questions', 'sound_cues', 'voice_cast'):
        if key in (story['metadata'] or {}):
            script[key] = story['metadata'][key]
    out_dir = get_settings().storage / "episodes" / str(episode_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "episode_script.json"
    path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    with connect() as c:
        ver = c.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM scripts WHERE episode_id=%s", (episode_id,)).fetchone()["v"]
        c.execute("INSERT INTO scripts (episode_id, version, path, content) VALUES (%s,%s,%s,%s)",
                  (episode_id, ver, str(path), jsonb(script)))
    return {"path": str(path), "sections": len(sections), "paragraphs": para_no}
