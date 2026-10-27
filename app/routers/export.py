"""整本导出（后台）"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.helpers import fail
from app.models import Chapter, Novel
from app.services import exporter

router = APIRouter(prefix="/api/admin", tags=["export"])

FORMATS = {
    "txt": ("text/plain; charset=utf-8", "txt"),
    "md": ("text/markdown; charset=utf-8", "md"),
    "epub": ("application/epub+zip", "epub"),
}


@router.get("/novels/{nid}/export")
def export_novel(nid: int, format: str = Query("txt"), include_drafts: int = Query(0),
                 db: Session = Depends(get_db)):
    novel = db.query(Novel).filter(Novel.id == nid).first()
    if not novel:
        return fail("小说不存在")
    if format not in FORMATS:
        return fail("不支持的导出格式")
    statuses = ("published", "draft") if include_drafts else ("published",)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.novel_id == nid, Chapter.status.in_(statuses))
        .order_by(Chapter.chapter_no)
        .all()
    )
    if not chapters:
        return fail("没有可导出的章节" + ("" if include_drafts else "（草稿默认不导出）"))

    media_type, ext = FORMATS[format]
    if format == "epub":
        body: bytes = exporter.to_epub(novel, chapters)
    elif format == "md":
        body = exporter.to_markdown(novel, chapters).encode("utf-8")
    else:
        body = ("﻿" + exporter.to_txt(novel, chapters)).encode("utf-8")  # BOM：Windows 记事本正确识别 UTF-8
    filename = f"{novel.title or 'novel'}.{ext}"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename=\"novel-{nid}.{ext}\"; filename*=UTF-8''{quote(filename)}"},
    )
