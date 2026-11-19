"""本地文档解析与切块。不依赖外部服务，PDF/DOCX/PPTX/文本/网页均可处理。"""
from __future__ import annotations

import glob
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

MAX_CHUNK = 420
MIN_CHUNK = 60

STOP = {
    "我们", "可以", "这个", "那个", "以及", "通过", "进行", "一个", "对于", "其中",
    "同时", "并目", "因此", "由于", "如果", "需要", "使用", "没有", "这些", "那些",
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
}


def clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 去除控制字符 / 零宽字符等不可见脏字符（PDF/扫描件提取常见副产物）
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b\u200c\u200d\ufeff]", "", text)
    # 合并常规空白（半角/全角空格、tab）为单个半角空格
    text = re.sub(r"[ \t\u3000]+", " ", text)
    # 去除「非英文/数字字符 + 空格 + 非英文/数字字符」之间的空格：
    # PDF/扫描件提取常产出「经 营 风 险 管 控」「《 市属」「2015 年」形态，
    # 若不清理会破坏子串匹配，导致检索完全失效；英文单词间空格不受影响
    text = re.sub(r"(?<=[^0-9A-Za-z])[ \t\u3000]+(?=[^0-9A-Za-z])", "", text)
    # 清理 OCR/PDF 噪点符号（正式公文与正文中不应出现的字符）
    text = re.sub(r"[@#$%^&*{}|\\~<>]+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def guess_format(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    return {
        "pdf": "PDF", "doc": "DOC", "docx": "DOCX",
        "ppt": "PPTX", "pptx": "PPTX", "md": "MD", "markdown": "MD",
        "txt": "TXT", "html": "网页", "htm": "网页",
        "png": "图片", "jpg": "图片", "jpeg": "图片", "webp": "图片",
    }.get(ext, "TXT")


def _pdf_pages(path: Path) -> list[tuple[int, str, str]]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    out = []
    for i, page in enumerate(reader.pages, start=1):
        text = clean(page.extract_text() or "")
        if text:
            out.append((i, "正文", text))
    return out


def _docx_pages(path: Path) -> list[tuple[int, str, str]]:
    from docx import Document as DocxDocument
    doc = DocxDocument(str(path))
    section = "正文"
    buf: list[str] = []
    out: list[tuple[int, str, str]] = []
    page = 1
    for para in doc.paragraphs:
        text = clean(para.text)
        if not text:
            continue
        style = (para.style.name or "").lower()
        if "heading" in style or "标题" in style:
            if buf:
                out.append((page, section, "\n".join(buf)))
                buf = []
                page += 1
            section = text[:40]
        buf.append(text)
    for table in doc.tables:
        rows = [" | ".join(clean(c.text) for c in row.cells) for row in table.rows]
        joined = "\n".join(r for r in rows if r.strip(" |"))
        if joined:
            buf.append(joined)
    if buf:
        out.append((page, section, "\n".join(buf)))
    return out


# 旧版 .doc 通过本机 Word（pywin32 COM）转 .docx 后复用 docx 解析。
# Word 是单实例 STA COM 服务器：并发拉起多个实例会触发 RPC 冲突（“RPC 服务器不可用 /
# 对象已断开连接”）；跨线程调用同一实例又需封送。故用一个常驻 STA 工作线程 +
# 单一 Word 实例串行处理所有转换请求，彻底规避并发与单元问题。
class _WordConverter:
    def __init__(self) -> None:
        import importlib.util

        self._q: "queue.Queue" = queue.Queue()
        # 未装 pywin32 时不启动 COM 线程：.doc 转换如实报错，其余解析照常工作
        self._available = importlib.util.find_spec("pythoncom") is not None
        if self._available:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        import pythoncom
        import win32com.client
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        word = None
        try:
            while True:
                item = self._q.get()
                if item is None:
                    break
                src, out, ev, err = item
                try:
                    if word is None:
                        word = win32com.client.Dispatch("Word.Application")
                        try:
                            word.Visible = False
                        except Exception:
                            pass
                        try:
                            word.DisplayAlerts = 0  # wdAlertsNone：不弹窗
                        except Exception:
                            pass
                    doc = word.Documents.Open(str(src))
                    try:
                        doc.SaveAs2(str(out), FileFormat=16)  # 16 = wdFormatXMLDocument
                    finally:
                        doc.Close(False)  # 不保存改动
                except Exception as e:  # noqa: BLE001
                    err.append(e)
                    # 出错后回收实例，下次重新拉起，避免坏状态累积
                    if word is not None:
                        try:
                            word.Quit()
                        except Exception:
                            pass
                    word = None
                finally:
                    ev.set()
        finally:
            if word is not None:
                try:
                    word.Quit()
                except Exception:
                    pass
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def convert(self, src: Path, out: Path, timeout: int = 180) -> None:
        if not self._available:
            raise RuntimeError("未安装 pywin32，无法转换 .doc；请另存为 .docx 后导入")
        ev = threading.Event()
        err: list = []
        self._q.put((src, out, ev, err))
        if not ev.wait(timeout):
            raise RuntimeError("Word 转换超时（可能文件过大或 Word 无响应）")
        if err:
            raise err[0]


_word_converter = _WordConverter()


def _doc_legacy_pages(path: Path) -> list[tuple[int, str, str]]:
    """旧版 .doc（Word 97-2003，OLE2 二进制格式）。

    python-docx 只能读 .docx（OOXML zip），对旧版 .doc 直接报
    “Package not found”。这里借本机 Word（pywin32 COM）把 .doc 另存为 .docx，
    再复用 _docx_pages 抽取文本；Word 不可用时返回明确错误而非崩溃。
    """
    try:
        import win32com  # noqa: F401  仅用于探测是否可用
    except ImportError:
        return [(1, "不支持的格式",
                 "环境未安装 pywin32，无法解析旧版 .doc 文件（请改用 .docx，或在 Windows 上安装 Word 后重试）。")]

    # COM 跨边界传中文长路径易失败（Word 报“找不到文件”），先落到短 ASCII 临时路径再打开。
    # 每次解析用独立临时目录：导入线程池并发（max_workers=3）时，固定文件名会导致
    # 多个 Word 进程争抢同一临时文件，出现 Permission denied / Open.Close 错误。
    work = Path(tempfile.mkdtemp(prefix="kb_docconv_"))
    short_src = work / "src.doc"
    out = work / "out.docx"
    try:
        shutil.copyfile(str(path), str(short_src))
        try:
            _word_converter.convert(short_src, out)
        except Exception as exc:
            return [(1, "解析失败", f"Word 解析 .doc 失败：{exc}")]
        if not out.exists():
            return [(1, "解析失败", "Word 未能生成 .docx（文件可能受密码保护或格式不被支持）")]
        return _docx_pages(out)
    except Exception as exc:
        return [(1, "解析失败", f"Word 解析 .doc 失败：{exc}")]
    finally:
        shutil.rmtree(str(work), ignore_errors=True)


def _pptx_pages(path: Path) -> list[tuple[int, str, str]]:
    from pptx import Presentation
    prs = Presentation(str(path))
    out = []
    for i, slide in enumerate(prs.slides, start=1):
        texts = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                texts.append(clean(shape.text))
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    texts.append(" | ".join(c.text.strip() for c in row.cells))
        if texts:
            out.append((i, texts[0][:40] or f"第 {i} 页", "\n".join(texts)))
    return out


def _text_pages(path: Path) -> list[tuple[int, str, str]]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() in (".html", ".htm"):
        raw = html_to_text(raw)
    raw = clean(raw)
    # 知乎等导出的 Markdown 常带 YAML frontmatter，正文提取时跳过
    if raw.startswith("---"):
        front = re.match(r"^---[\s\S]*?\n---\s*", raw)
        if front:
            raw = raw[front.end():].strip()
    section = "正文"
    buf: list[str] = []
    out: list[tuple[int, str, str]] = []
    page = 1
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        heading = re.match(r"^#{1,4}\s+(.+)$", line)
        if heading:
            if buf:
                out.append((page, section, "\n".join(buf)))
                buf = []
                page += 1
            section = heading.group(1)[:40]
            continue
        buf.append(line)
        if sum(len(x) for x in buf) > 900:
            out.append((page, section, "\n".join(buf)))
            buf = []
            page += 1
    if buf:
        out.append((page, section, "\n".join(buf)))
    return out


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    from html import unescape
    return unescape(html)


# ---------------------------------------------------------------------------
# OCR：扫描件 / 图片型 PDF 先渲染成图片，再用 RapidOCR（PP-OCR 的 ONNX 版，
# CPU 友好，不依赖有 oneDNN 缺陷的 PaddlePaddle 3.x）识别文字
# ---------------------------------------------------------------------------
_OCR_ENGINE = None


def _find_pdftoppm() -> str | None:
    candidate = r"D:/Users/wwt29/AppData/Local/Programs/MiKTeX/miktex/bin/x64/pdftoppm.exe"
    if os.path.exists(candidate):
        return candidate
    return shutil.which("pdftoppm")


def _get_ocr():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _render_pdf_to_images(path: Path, out_dir: str, dpi: int = 200) -> list[str]:
    poppler = _find_pdftoppm()
    if not poppler:
        return []
    prefix = os.path.join(out_dir, "p")
    try:
        subprocess.run([poppler, "-png", "-r", str(dpi), str(path), prefix],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=False)
    except Exception:
        return []
    return sorted(glob.glob(os.path.join(out_dir, "*.png")))


def _ocr_images(image_paths: list[str]) -> list[tuple[int, str, str]]:
    if not image_paths:
        return []
    try:
        ocr = _get_ocr()
    except Exception:
        return []
    pages: list[tuple[int, str, str]] = []
    for idx, img in enumerate(image_paths, start=1):
        try:
            result, _ = ocr(str(img))
        except Exception:
            result = None
        if not result:
            continue
        # RapidOCR 每条结果结构为 [bbox, 文本字符串, 置信度]
        lines = [ln[1] for ln in result if ln and len(ln) > 1 and ln[1]]
        text = "\n".join(lines).strip()
        if text:
            pages.append((idx, "正文", text))
    return pages


def _ocr_pdf(path: Path) -> list[tuple[int, str, str]]:
    tmp = tempfile.mkdtemp(prefix="ocr_")
    try:
        imgs = _render_pdf_to_images(path, tmp)
        return _ocr_images(imgs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def parse_file(path: Path, filename: str) -> tuple[int, list[tuple[int, str, str]]]:
    """返回 (总页数, [(页码, 章节, 文本)])。

    文本型文档直接提取；扫描件/图片型文档会尝试 OCR（RapidOCR）识别，
    识别成功即按识别文字切片，失败才保留占位说明、不伪造正文。
    原件始终完整保留，可在「原始文件」中在线预览。
    """
    fmt = guess_format(filename)
    if fmt == "图片":
        pages = _ocr_images([str(path)])
        if pages:
            return len(pages), pages
        return 1, [(1, "图片", f"图片资料：{filename}。OCR 未识别到文字，原始文件已完整保留，可下载查看。")]
    handlers = {
        "PDF": _pdf_pages, "DOCX": _docx_pages, "DOC": _doc_legacy_pages, "PPTX": _pptx_pages,
    }
    pages = handlers.get(fmt, _text_pages)(path)
    if not pages or not any(t.strip() for _, _, t in pages):
        # 扫描版/图片版文档：先尝试 OCR，失败再返回占位说明
        ocr_pages = _ocr_pdf(path) if fmt == "PDF" else []
        if ocr_pages:
            return len(ocr_pages), ocr_pages
        note = (f"【扫描版文件】{filename} 没有文字层（每页均为图片），"
                "本地 OCR 未能识别文字，未提取正文；原始文件已完整保留，可在「原始文件」中在线预览。")
        return len(pages), [(1, "扫描件", note)]
    return len(pages), pages


def split_into_chunks(pages: list[tuple[int, str, str]]) -> list[tuple[int, str, str]]:
    """把长文本按语义边界切成可检索片段，保留页码与章节。"""
    chunks: list[tuple[int, str, str]] = []
    for page, section, text in pages:
        parts = re.split(r"(?<=[。！？；!?;\n])", text)
        buf = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if len(buf) + len(part) > MAX_CHUNK and len(buf) >= MIN_CHUNK:
                chunks.append((page, section, buf.strip()))
                buf = part
            else:
                buf += part
        if buf.strip():
            chunks.append((page, section, buf.strip()))
    return chunks or [(1, "正文", "未提取到正文")]


def first_sentence(text: str, limit: int = 110) -> str:
    text = clean(text.replace("\n", " "))
    m = re.search(r"[。！？；]", text)
    return (text[: m.end()] if m and m.end() <= limit else text[:limit]).strip()


def extract_summary(chunks: list[tuple[int, str, str]], enabled: bool = True) -> str:
    if not enabled:
        return "自动摘要已关闭，此文件仅完成文本提取与切块。"
    texts = [t for _, _, t in chunks]
    head = first_sentence(texts[0], 100) if texts else ""
    if len(texts) > 1:
        tail = first_sentence(texts[-1], 80)
        return f"{head} 全文共 {len(chunks)} 个检索切片，末尾部分涉及：{tail}"
    return head or "已完成文本提取与切块。"


def extract_keywords(chunks: list[tuple[int, str, str]], limit: int = 5) -> list[str]:
    text = "".join(t for _, _, t in chunks)
    chinese = re.findall(r"[\u4e00-\u9fa5]{2,6}", text)
    freq: dict[str, int] = {}
    for word in chinese:
        if word in STOP or len(word) < 2:
            continue
        for n in (2, 3, 4):
            for i in range(len(word) - n + 1):
                gram = word[i:i + n]
                if gram in STOP:
                    continue
                freq[gram] = freq.get(gram, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (kv[1] * (len(kv[0]) ** 0.6), kv[1]), reverse=True)
    picked: list[str] = []
    for word, count in ranked:
        if count < 2:
            break
        if any(word in p or p in word for p in picked):
            continue
        picked.append(word)
        if len(picked) >= limit:
            break
    return picked


def full_text_from_pages(pages: list[tuple[int, str, str]]) -> str:
    """完整解析文本，独立保存，不由切片反向拼接。"""
    parts = []
    for page, section, text in pages:
        parts.append(f"【第 {page} 页 · {section}】\n{text}")
    return "\n\n".join(parts).strip()


IMAGE_MD = re.compile(r"!\[[^\]]*\]\(\s*<?([^)>\s]+)", re.IGNORECASE)
IMAGE_HTML = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)


def extract_markdown_images(text: str) -> list[str]:
    refs = IMAGE_MD.findall(text) + IMAGE_HTML.findall(text)
    return [r for r in refs if not r.startswith(("http://", "https://", "data:"))]


def normalize_asset_ref(ref: str) -> str:
    from urllib.parse import unquote, urlsplit
    path = unquote(urlsplit(ref).path).lstrip("/")
    return path.replace("\\", "/")


def rewrite_markdown_images(text: str, mapping: dict[str, str]) -> str:
    """把本地图片引用改写成后端可访问的地址，原文文件本身不改动。"""
    def repl_md(m: re.Match) -> str:
        ref = m.group(1)
        key = normalize_asset_ref(ref)
        return m.group(0).replace(ref, mapping[key]) if key in mapping else m.group(0)

    def repl_html(m: re.Match) -> str:
        ref = m.group(1)
        key = normalize_asset_ref(ref)
        return m.group(0).replace(ref, mapping[key]) if key in mapping else m.group(0)

    text = IMAGE_MD.sub(repl_md, text)
    return IMAGE_HTML.sub(repl_html, text)
