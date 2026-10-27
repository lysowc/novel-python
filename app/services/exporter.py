"""整本导出：TXT / Markdown / EPUB 3（纯标准库实现，无额外依赖）"""
import html
import io
import re
import uuid
import zipfile
from datetime import datetime

from app.models import Chapter, Novel


def chapter_heading(c: Chapter) -> str:
    title = (c.title or "").strip()
    # 大纲里的标题常常已经带了"第N章"，避免变成"第3章 第3章 xxx"
    if re.match(r"^第\s*[0-9零一二三四五六七八九十百千]+\s*章", title):
        return title
    return f"第{c.chapter_no}章 {title}".strip()


def paragraphs(text: str | None) -> list[str]:
    return [p.strip() for p in (text or "").splitlines() if p.strip()]


def to_txt(novel: Novel, chapters: list[Chapter]) -> str:
    out = [novel.title or "未命名", ""]
    if (novel.description or "").strip():
        out += ["简介：", novel.description.strip(), ""]
    for c in chapters:
        out += ["", chapter_heading(c), ""]
        out += ["　　" + p for p in paragraphs(c.content)]
    return "\r\n".join(out) + "\r\n"


def to_markdown(novel: Novel, chapters: list[Chapter]) -> str:
    out = [f"# {novel.title or '未命名'}", ""]
    if (novel.description or "").strip():
        out += [f"> {novel.description.strip()}", ""]
    for c in chapters:
        out += [f"## {chapter_heading(c)}", ""]
        for p in paragraphs(c.content):
            out += [p, ""]
    return "\n".join(out)


_CSS = """body { font-family: serif; line-height: 1.8; margin: 0 5%; }
h1, h2 { text-align: center; font-weight: bold; margin: 1.5em 0 1em; }
p { text-indent: 2em; margin: 0 0 0.6em; }
.intro p { text-indent: 0; }
"""


def _xhtml(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="zh-CN" xml:lang="zh-CN">\n'
        f'<head><meta charset="utf-8"/><title>{html.escape(title)}</title>'
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def to_epub(novel: Novel, chapters: list[Chapter]) -> bytes:
    esc = html.escape
    title = novel.title or "未命名"
    book_id = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'novel-python/{novel.id}')}"
    modified = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    pages = []  # (id, file, heading, xhtml)
    intro = f"<h1>{esc(title)}</h1>"
    if (novel.description or "").strip():
        intro += '<div class="intro">' + "".join(f"<p>{esc(p)}</p>" for p in paragraphs(novel.description)) + "</div>"
    pages.append(("intro", "intro.xhtml", title, _xhtml(title, intro)))
    for i, c in enumerate(chapters, 1):
        heading = chapter_heading(c)
        body = f"<h2>{esc(heading)}</h2>\n" + "\n".join(f"<p>{esc(p)}</p>" for p in paragraphs(c.content))
        pages.append((f"c{i}", f"c{i:04d}.xhtml", heading, _xhtml(heading, body)))

    manifest = "\n".join(
        f'    <item id="{pid}" href="{f}" media-type="application/xhtml+xml"/>' for pid, f, _, _ in pages)
    spine = "\n".join(f'    <itemref idref="{pid}"/>' for pid, _, _, _ in pages)
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="zh-CN">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{esc(title)}</dc:title>
    <dc:language>zh-CN</dc:language>
    <dc:description>{esc((novel.description or '').strip())}</dc:description>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
{manifest}
  </manifest>
  <spine toc="ncx">
{spine}
  </spine>
</package>
"""
    nav_items = "\n".join(f'      <li><a href="{f}">{esc(h)}</a></li>' for _, f, h, _ in pages[1:])
    nav = _xhtml("目录", f'<nav epub:type="toc" id="toc"><h1>目录</h1>\n    <ol>\n{nav_items}\n    </ol>\n</nav>')
    nav_points = "\n".join(
        f'    <navPoint id="np{i}" playOrder="{i}"><navLabel><text>{esc(h)}</text></navLabel>'
        f'<content src="{f}"/></navPoint>'
        for i, (_, f, h, _) in enumerate(pages, 1))
    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="{book_id}"/></head>
  <docTitle><text>{esc(title)}</text></docTitle>
  <navMap>
{nav_points}
  </navMap>
</ncx>
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        # mimetype 必须是第一个文件且不压缩
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
""", compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", _CSS, compress_type=zipfile.ZIP_DEFLATED)
        for _, f, _, content in pages:
            z.writestr(f"OEBPS/{f}", content, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()
