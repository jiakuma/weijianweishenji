"""制度文档解析：PDF / Word / 文本 / Excel 清单 -> 带页码的页结构。

与旧版的区别（重写重点）：
- PDF 改用 PyMuPDF `get_text("words")` 按 y 坐标重组行，修掉公文 PDF 每行硬换行
  造成的断句（例如「财务电子信息网络」+「系统安全运行」），并剔除 `- 1 -` 页码噪声；
- 输出从「一整串纯文本」改为「按页的 Page 列表」，页码一路带到冲突报告；
- 单页文本 < 30 字判定为扫描件，仅该页走 RapidOCR 兜底，不再整篇回退；
- `.xlsx` 单独作为「制度清单」输入，由 structure.py 读取，不进正文。

保留 `parse_file()` 旧签名，避免其它模块调用失效。
"""
from __future__ import annotations

import io
import re
import threading
from dataclasses import dataclass, field

# RapidOCR 非线程安全：每线程持有独立实例
_ocr_local = threading.local()

# 结构标记行：这些行前强制断行，保证「第X条 /（一）」边界不被引擎吃掉
_STRUCT_RE = re.compile(
    r"^(?:"
    r"第[一二三四五六七八九十百零〇0-9]+[章节条部分]"
    r"|[（(][一二三四五六七八九十百零〇0-9]+[）)]"
    r"|\d+\s*[.、．]"
    r")"
)
# 独立成行的页码 / 页眉页脚，如 "- 1 -"、"— 12 —"、"1"
_PAGE_NOISE_RE = re.compile(r"^[-—–_\s]*\d{1,4}[-—–_\s]*$")
_SENTENCE_END_RE = re.compile(r"[。；：！？;:]$")


@dataclass
class Page:
    """一页文本。number 为 1-based PDF 页码；非 PDF 格式为 None。"""
    number: int | None
    text: str
    ocr: bool = False

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class ParsedDocument:
    filename: str
    pages: list[Page] = field(default_factory=list)
    error: str | None = None
    note: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.pages)

    @property
    def chars(self) -> int:
        return sum(p.chars for p in self.pages)


# ------------------------------------------------------------------ OCR

def _get_ocr():
    engine = getattr(_ocr_local, "engine", None)
    if engine is None:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
        _ocr_local.engine = engine
    return engine


def _suppress_red_stamp(arr):
    """抑制扫描件上的红色印章：红章常压住正文（尤其签署页的单位名称），
    直接 OCR 会把「防治院」识成「防浴院」。只把高饱和红色像素置白，
    黑色/灰色正文不受影响。无红章时原样返回。
    """
    import numpy as np

    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    mask = (r - g > 30) & (r - b > 30) & (r > 120)
    if not bool(mask.any()):
        return arr
    out = arr.copy()
    out[mask] = 255
    return out


_OCR_TABLE_HINT_RE = re.compile(
    r"(序号|设备名称|生产厂家|制造商|品牌|规格|型号|单价|数量|合价|总价|小写|人民币)")
_OCR_CELL_RE = re.compile(r"^[\dA-Za-z.\-,/()（）%]+$")
_OCR_SHORT_CN_CELL_RE = re.compile(r"^[\u4e00-\u9fa5]{2,8}$")
_OCR_SENTENCE_END_RE = re.compile(r"[。；：！？;:]$")
_OCR_STRUCT_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百零〇0-9]+[章节条部分]"
    r"|[（(][一二三四五六七八九十0-9]+[）)]"
    r"|\d+[.、．]\s"
    r"|[一二三四五六七八九十]+[．.、])")


def _is_ocr_table_line(text: str) -> bool:
    """表格单元格/纯数字行不参与段落续行合并，避免表格被拼成一长串。"""
    s = text.strip()
    return bool(
        _OCR_TABLE_HINT_RE.search(s)
        or _OCR_CELL_RE.match(s)
        or _OCR_SHORT_CN_CELL_RE.match(s)
    )


def _reflow_ocr_lines(result) -> str:
    """把 OCR 的视觉行还原为语义行。

    RapidOCR 按版面条带输出，正文换行会被拆成两行。若直接按视觉行交给后续审核，
    「一致同意就甲方购买 / 的设备供货…」会被误读成标的留白。这里只合并满足以下
    条件的相邻行：同一段落续行、非表格单元格、非结构标题、纵向间距很小。
    """
    items = []
    for box, text, _score in result:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append({
            "x0": min(xs), "y0": min(ys),
            "x1": max(xs), "y1": max(ys),
            "text": text.strip(),
        })
    items.sort(key=lambda d: ((d["y0"] + d["y1"]) / 2, d["x0"]))

    out = []
    for cur in items:
        if not out:
            out.append(cur)
            continue
        prev = out[-1]
        prev_mid = (prev["y0"] + prev["y1"]) / 2
        cur_mid = (cur["y0"] + cur["y1"]) / 2
        same_row = abs(cur_mid - prev_mid) < 18
        gap = cur["y0"] - prev["y1"]
        joinable = (
            not same_row
            and gap <= 35
            and not _is_ocr_table_line(prev["text"])
            and not _is_ocr_table_line(cur["text"])
            and not _OCR_STRUCT_RE.match(cur["text"])
            and not _OCR_SENTENCE_END_RE.search(prev["text"])
            and not (
                ("：" in prev["text"] or ":" in prev["text"])
                and ("：" in cur["text"] or ":" in cur["text"])
            )
        )
        if joinable:
            prev["text"] += cur["text"]
            prev["x1"] = cur["x1"]
            prev["y1"] = cur["y1"]
        else:
            out.append(cur)
    return "\n".join(d["text"] for d in out if d["text"])


def _ocr_page(doc, index: int) -> str:
    """把指定页渲染成图片再识别；只处理被判定为扫描件的单页。"""
    import numpy as np
    import pymupdf

    # 3 倍渲染：2 倍对小字号/手写数字识别偏弱；红章抑制减少印章压字误识别
    pix = doc[index].get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[:, :, :3]
    arr = _suppress_red_stamp(arr)
    result, _ = _get_ocr()(arr[:, :, ::-1])
    return _reflow_ocr_lines(result) if result else ""


# ------------------------------------------------------------------ PDF 行重组

def _join_words(tokens: list[str]) -> str:
    """中文词直接相连，连续 ASCII 词之间补空格。"""
    out = ""
    prev_ascii = False
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        cur_ascii = bool(re.match(r"^[A-Za-z0-9]", token))
        if out and prev_ascii and cur_ascii:
            out += " "
        out += token
        prev_ascii = cur_ascii
    return out.strip()


def _page_lines(page) -> list[str]:
    """按 y 坐标把 words 聚成视觉行，解决标题与页码、跨行文本的错位。"""
    rows: dict[int, list[tuple[float, str]]] = {}
    for w in page.get_text("words"):
        x0, y0, word = w[0], w[1], w[4]
        key = round(y0 / 3) * 3
        rows.setdefault(key, []).append((x0, word))
    lines = []
    for y in sorted(rows):
        row = sorted(rows[y], key=lambda item: item[0])
        line = _join_words([word for _, word in row])
        if line:
            lines.append(line)
    return lines


def _clean_page(lines: list[str]) -> str:
    """合并硬换行：普通行续接上一行，结构标记行单独起行。"""
    blocks: list[str] = []
    for line in lines:
        line = line.strip()
        if not line or _PAGE_NOISE_RE.match(line):
            continue
        if _STRUCT_RE.match(line):
            blocks.append("\n" + line)
        elif blocks and not _SENTENCE_END_RE.search(blocks[-1]):
            prev = blocks[-1]
            sep = " " if re.search(r"[A-Za-z0-9]$", prev) and re.match(r"^[A-Za-z0-9]", line) else ""
            blocks[-1] = prev + sep + line
        else:
            blocks.append(line)
    return "\n".join(b.strip() for b in blocks if b.strip())


def _parse_pdf(raw: bytes, ocr: bool = True) -> list[Page]:
    import pymupdf

    pages: list[Page] = []
    with pymupdf.open(stream=raw, filetype="pdf") as doc:
        for i in range(len(doc)):
            text = _clean_page(_page_lines(doc[i]))
            used_ocr = False
            if len(text) < 30 and ocr:
                try:
                    text = _ocr_page(doc, i)
                    used_ocr = bool(text)
                except Exception:
                    text = ""
            if text:
                pages.append(Page(number=i + 1, text=text, ocr=used_ocr))
    return pages


# ------------------------------------------------------------------ 其它格式

def _parse_docx(raw: bytes) -> list[Page]:
    import docx

    doc = docx.Document(io.BytesIO(raw))
    parts = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    text = "\n".join(parts)
    return [Page(number=None, text=text)] if text else []


def _parse_text(raw: bytes) -> list[Page]:
    text = raw.decode("utf-8", errors="ignore").strip()
    return [Page(number=None, text=text)] if text else []


def read_manifest(raw: bytes) -> list[dict]:
    """读取 xlsx 制度清单：返回 [{name, group, dept}]。

    约定：第一列是序号（数字），第二列制度名称，第三列责任科室；
    「第X部分 …」这类行没有序号，作为 group 分组标题。
    """
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    ws = wb.worksheets[0]
    items: list[dict] = []
    group = ""
    for row in ws.iter_rows(values_only=True):
        cells = list(row) + [None] * (4 - len(row))
        seq, name, dept = cells[0], cells[1], cells[2]
        name = str(name).strip() if name is not None else ""
        if not name:
            continue
        if re.match(r"^\s*第[一二三四五六七八九十]+部分", name):
            group = re.sub(r"\s+", " ", name).strip()
            continue
        if seq is not None and str(seq).strip().isdigit():
            items.append({
                "name": re.sub(r"\s+", "", name),
                "group": group,
                "dept": str(dept).strip() if dept else "",
            })
    wb.close()
    return items


# ------------------------------------------------------------------ 对外入口

def parse_document(filename: str, raw: bytes, ocr: bool = True) -> ParsedDocument:
    """解析单个文件为按页结构。xlsx 不产生正文页（仅作为清单）。"""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    try:
        if ext == "pdf":
            pages = _parse_pdf(raw, ocr=ocr)
        elif ext == "xlsx":
            return ParsedDocument(filename=filename, pages=[], note="制度清单")
        elif ext in ("docx", "doc"):
            pages = _parse_docx(raw)
        else:
            pages = _parse_text(raw)
        if not pages:
            return ParsedDocument(filename=filename, pages=[],
                                  note="未提取到正文（可能是扫描件且 OCR 未识别）")
        return ParsedDocument(filename=filename, pages=pages)
    except Exception as exc:
        return ParsedDocument(filename=filename, pages=[], error=f"{type(exc).__name__}: {exc}")


def parse_file(filename: str, raw: bytes, ocr: bool = True) -> str:
    """兼容旧调用：返回拼接后的纯文本。"""
    doc = parse_document(filename, raw, ocr=ocr)
    if doc.error:
        raise RuntimeError(doc.error)
    return doc.text
