"""自动分类：让 AI 自由决定分类名与层级（最多 3 层），只在无法判定时回退到「未分类」。

约束仅保留层级深度（MAX_CATEGORY_DEPTH，见 db.py）。为减少分类碎片化与同名交叉嵌套，
提示词会给出当前完整分类树，要求 AI 优先复用已有叶子路径、不要新建与已有路径语义
重复（含父子颠倒）或同名的分类、且禁用泛称分类名，层级越浅越好。
"""
from __future__ import annotations

import os
import re

import httpx

from .db import Category, Document, ensure_category_chain, get_raw_settings, now_str
from .llm import llm_config, post_chat

RESERVED = "未分类"

# 泛称/空泛分类名：出现则视为无效层级，直接丢弃
GENERIC_SEGMENTS = {
    "本级文件", "本层文件", "其他", "其它", "文档", "资料", "未分类",
    "内容", "正文", "文件", "默认", "一般", "新建", "新建分类", "分类",
    "misc", "other", "others", "unknown", "暂无",
}


def _build_tree_outline(session) -> str:
    """把当前分类树渲染成带缩进的嵌套大纲，供模型参考（不含「未分类」常驻项）。"""
    cats = session.query(Category).all()
    children_of: dict[str, list[Category]] = {}
    for c in cats:
        children_of.setdefault(c.parent_id or "", []).append(c)
    lines: list[str] = []

    def walk(parent_id: str, depth: int) -> None:
        for c in children_of.get(parent_id, []):
            if c.name == RESERVED:
                continue
            lines.append("  " * depth + c.name)
            walk(c.id, depth + 1)

    walk("", 0)
    return "\n".join(lines)


def _llm_choose(session, title: str, text: str) -> str | None:
    """让模型返回分类路径（用 / 分隔，最多 3 层）。返回原始字符串，由调用方解析。"""
    cfg = llm_config(session)
    if not cfg.ready:
        return None
    tree = _build_tree_outline(session)
    tree_hint = tree if tree else "（暂无已有分类，请自由新建）"
    prompt = (
        "你是知识库自动分类器。请为这份资料确定分类路径（用 / 分隔，最多 3 层，层级越浅越好）。\n\n"
        "现有分类树（若某个已有分类与本文档主题相符，优先复用其叶子节点）：\n"
        f"{tree_hint}\n\n"
        "判断步骤：\n"
        "1. 先判断本文档的【主要主题 / 领域】（医疗、通用 AI 技术、简历、金融、法律……）。\n"
        "2. 若文档主题确实属于某个已有分类（或它的父领域），归入该分类。\n"
        "3. 若文档属于已有分类都未覆盖的【全新领域】，请新建一个顶层分类，不要把它勉强塞进无关的已有分类下。\n"
        "   例：已有分类全是医疗相关，而本文讲的是通用大模型 / RAG / 工程实践等，应新建顶层「人工智能」之类，而不是归入医疗。\n\n"
        "禁止：\n"
        "1. 新建与已有分类语义重复、父子颠倒、或同名的分类。\n"
        "2. 使用「本级文件、其他、文档、资料、未分类、内容、正文」等泛称。\n"
        "3. 层级超过 3 层；能用 1-2 层说清就不下钻。\n"
        "4. 输出任何解释、标点或前后缀——只输出分类路径本身。\n\n"
        f"标题：{title}\n正文开头：{text[:600]}"
    )
    try:
        data = post_chat(
            {"model": cfg.model, "messages": [{"role": "user", "content": prompt}],
             "temperature": 0, "max_tokens": 40},
            timeout=15, config=cfg,
        )
        name = data["choices"][0]["message"]["content"].strip()
        return name or None
    except Exception:
        return None


def _split_path(raw: str) -> list[str]:
    """把模型返回的分类路径拆成层级段，兼容 / 、> 、、 等分隔符；丢弃泛称段，最多 3 层。"""
    if not raw:
        return []
    raw = raw.replace("、", "/").replace("›", "/").replace("→", "/")
    parts = [p.strip().strip("。\"'·- ") for p in re.split(r"[/>]", raw)]
    cleaned = []
    for p in parts:
        if not p or p in GENERIC_SEGMENTS:
            continue
        cleaned.append(p)
    return cleaned[:3]


def _new_category_name(title: str, keywords: list[str], names: list[str]) -> str:
    """LLM 完全不可用时的兜底：从关键词/标题里取一个短名，否则进「未分类」。"""
    for kw in keywords[:6]:
        cleaned = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", kw)
        if 2 <= len(cleaned) <= 8 and cleaned not in names and cleaned not in GENERIC_SEGMENTS:
            return cleaned
    cleaned = re.sub(r"\.[A-Za-z0-9]+$", "", title)
    cleaned = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", cleaned)[:8]
    return cleaned or RESERVED


def classify_document(session, title: str, text: str, keywords: list[str], enabled: bool = True) -> str:
    """返回该资料应归属的（最末级）分类名。

    - enabled=False：直接进「未分类」（手动关闭自动分类时）。
    - 正常：让 AI 自由决定分类路径，最多 3 层；无可用模型时回退到关键词兜底。
    """
    all_cats = session.query(Category).all()
    all_names = [c.name for c in all_cats]
    if not all_names:
        session.add(Category(id="cat-1", name=RESERVED, parent_id="", order_index=0, created_at=now_str()))
        session.commit()
        all_names = [RESERVED]

    if not enabled:
        return RESERVED

    raw = _llm_choose(session, title, text)
    segments = _split_path(raw)
    if segments:
        leaf = ensure_category_chain(session, segments)
        if leaf:
            return leaf.name

    # 模型不可用或未返回有效内容：用关键词兜底
    new_name = _new_category_name(title, keywords, all_names)
    if new_name not in all_names:
        session.add(Category(id=f"cat-{now_str().replace('-', '').replace(' ', '').replace(':', '')}-{len(all_names) + 1}",
                             name=new_name, parent_id="", order_index=len(all_names), created_at=now_str()))
        session.commit()
    return new_name
