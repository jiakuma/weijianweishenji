"""合同审计核心流水线：解析 -> 角色识别 -> 类型识别 -> 文本审核 -> 跨文件勾稽 -> 报告。

设计要点：
- 用户上传的是一批文件（主合同 + 若干附件），由 LLM 先判角色与类型，不要求用户预先分类；
- 主合同做十条维度文本审核，附件参与跨文件勾稽；
- 只陈述事实、位置与风险，不下违规定性结论（见 prompts 纪律）；
- 全程回写 task 进度，任何阶段失败都记录 error，绝不静默返回空报告。
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from ...llm import LLMConfig, OFFICIAL_MODEL, chat_url, normalize_base_url, post_chat
from ..conflict.parser import parse_document
from .prompts import (
    AUDIT_SYSTEM, AUDIT_USER, RECONCILE_SYSTEM, RECONCILE_USER,
    ROLE_SYSTEM, ROLE_USER, TYPE_SYSTEM, TYPE_USER,
)

PARSE_WORKERS = 4
DOC_EXCERPT = 1200       # 角色识别时每份文件取的开头字数
CONTRACT_TEXT_LIMIT = 18000  # 单份主合同送审核的最大字符数
ATTACH_TEXT_LIMIT = 4000     # 单个附件送勾稽的最大字数
ATTACH_TOTAL_LIMIT = 16000   # 所有附件合计上限


class LLMClient:
    """复用系统设置接口的 OpenAI 兼容最小客户端（与制度互斥同构）。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        base = normalize_base_url(base_url)
        self.url = chat_url(base)
        self.model = model or OFFICIAL_MODEL
        self.cfg = LLMConfig(base, api_key, self.model, 0.0, 4096, False)

    def chat(self, system: str, user: str):
        import httpx
        data = post_chat(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
            },
            timeout=120.0, config=self.cfg,
            client=httpx.Client(timeout=120.0),
        )
        choices = data.get("choices") or [{}]
        content = (choices[0].get("message", {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("模型返回空内容")
        return _extract_json(content)


def _extract_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                return obj
            except Exception:
                continue
    raise ValueError("模型未返回可解析的 JSON")


def _update(task, stage=None, done=None, total=None):
    if not task:
        return
    if stage is not None:
        task["stage"] = stage
    if done is not None:
        task["done"] = done
    if total is not None:
        task["total"] = total


def _render(text: str, limit: int) -> str:
    """截断到指定长度，保留开头与结尾，中间省略。"""
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n……（中间省略）……\n" + text[-half:]


def _page_text(doc) -> str:
    """把按页结构渲染成带页码标记的文本，供定位引用。"""
    parts = []
    for p in doc.pages:
        label = f"【第{p.number}页】" if p.number else ""
        parts.append(label + p.text)
    return "\n".join(parts)


def _parse_all(uploaded, task):
    """并行解析所有文件，返回 [{index,name,doc,text,excerpt}]。"""
    docs: dict[int, dict] = {}

    def one(idx, name, raw):
        d = parse_document(name, raw, ocr=True)
        full = _page_text(d)
        return idx, {"index": idx, "name": name, "doc": d, "text": full,
                     "chars": d.chars, "excerpt": _render(full, DOC_EXCERPT),
                     "error": d.error}

    with ThreadPoolExecutor(max_workers=PARSE_WORKERS) as ex:
        futs = [ex.submit(one, i, n, r) for i, (n, r) in enumerate(uploaded, 1)]
        for fut in as_completed(futs):
            idx, item = fut.result()
            docs[idx] = item
            _update(task, done=len(docs))
    return [docs[i] for i in sorted(docs)]


def _identify_roles(files, llm, task):
    _update(task, stage="识别文件角色")
    listing = "\n\n".join(
        f"[{f['index']}] 文件名：{f['name']}\n正文开头：{f['excerpt'] or '（未提取到正文）'}"
        for f in files
    )
    try:
        raw = llm.chat(ROLE_SYSTEM, ROLE_USER.format(count=len(files), files=listing))
        by_index = {}
        for r in (raw if isinstance(raw, list) else []):
            try:
                by_index[int(r.get("index"))] = r
            except (TypeError, ValueError):
                continue
        for f in files:
            r = by_index.get(f["index"], {})
            role = str(r.get("role") or "").strip()
            f["role"] = "主合同" if "主合同" in role else "附件"
            f["type"] = str(r.get("type") or "").strip() or ("采购合同" if f["role"] == "主合同" else "其他")
            f["role_reason"] = str(r.get("reason") or "").strip()
    except Exception:
        # 角色识别失败时按文件名兜底，保证流程继续
        for f in files:
            name = f["name"]
            f["role"] = "主合同" if re.search(r"合同|协议|合同书", name) and not re.search(r"补充协议", name) else "附件"
            f["type"] = "采购合同" if f["role"] == "主合同" else "其他"
            f["role_reason"] = "按文件名推断（角色识别不可用）"
    return files


def _identify_type(main, llm, task):
    _update(task, stage="识别合同类型")
    if not main:
        return ""
    try:
        raw = llm.chat(TYPE_SYSTEM, TYPE_USER.format(
            filename=main["name"], excerpt=_render(main["text"], 3000)))
        if isinstance(raw, dict):
            main["type"] = str(raw.get("type") or main.get("type") or "采购类合同")
            main["focus"] = str(raw.get("focus") or "").strip()
            main["type_reason"] = str(raw.get("reason") or "").strip()
    except Exception:
        main.setdefault("focus", "")
    return main.get("type", "")


_OCR_MODEL_NOISE_RE = re.compile(
    r"(OCR识别噪声|识别噪声|书写差异|空格差异|大小写|字母O与数字0|字母 O 与数字 0)")
_NO_RISK_RE = re.compile(r"(无明显风险|勾稽无误|无需修改)")
_SIGN_KEY_RE = re.compile(r"(法人代表|法定代表人|授权代表|签字|签章|盖章)")
_SIGN_MISSING_RE = re.compile(r"(未签|缺失|空白|未见|未盖章|未填写|没有签字|没有盖章)")


def _is_ocr_noise_issue(it: dict) -> bool:
    """过滤扫描噪声型误报：仅空格/大小写/O与0等字符形态差异不算合同问题。"""
    text = " ".join(str(it.get(k) or "") for k in
                    ("title", "fact", "analysis", "impact", "suggestion", "quote"))
    if "型号" in text and _OCR_MODEL_NOISE_RE.search(text):
        return True
    # 手写签名/印章不会被 OCR 识别成文字，不能据此判断签字盖章缺失
    if _SIGN_KEY_RE.search(text) and _SIGN_MISSING_RE.search(text):
        return True
    return bool(_NO_RISK_RE.search(text))


def _audit_one(main, llm):
    """审核单份主合同，问题带 source 文件名。"""
    raw = llm.chat(AUDIT_SYSTEM, AUDIT_USER.format(
        contract_type=main.get("type", ""),
        focus=main.get("focus", ""),
        text=_render(main["text"], CONTRACT_TEXT_LIMIT)))
    issues = raw if isinstance(raw, list) else []
    out = []
    for it in issues:
        if not isinstance(it, dict):
            continue
        if _is_ocr_noise_issue(it):
            continue
        out.append({
            "dimension": str(it.get("dimension") or "").strip(),
            "level": str(it.get("level") or "待核实异常").strip(),
            "title": str(it.get("title") or "待核实事项").strip(),
            "fact": str(it.get("fact") or "").strip(),
            "quote": str(it.get("quote") or "").strip(),
            "locate": (main["name"] + " · " + str(it.get("locate") or "正文")).strip(),
            "analysis": str(it.get("analysis") or "").strip(),
            "impact": str(it.get("impact") or "待核实").strip(),
            "suggestion": str(it.get("suggestion") or "").strip(),
            "source": main["name"],
        })
    return out


def _audit_mains(mains, llm, task):
    """逐份审核所有主合同；单份失败不影响其它。"""
    _update(task, stage="审核合同条款")
    all_issues, errors = [], []
    for idx, m in enumerate(mains):
        _update(task, done=idx)
        try:
            all_issues.extend(_audit_one(m, llm))
        except Exception as exc:
            errors.append(f"{m['name']} 条款审核失败：{type(exc).__name__}")
    for i, it in enumerate(all_issues, 1):
        it["index"] = i
    return all_issues, errors


def _reconcile(main, attachments, llm, task):
    _update(task, stage="跨文件勾稽")
    if not attachments:
        return [], "未提供相关附件，无法进行跨文件勾稽"
    if not main:
        return [], "未识别到主合同，无法勾稽"
    elements = (
        f"主合同文件名：{main['name']}\n合同类型：{main.get('type','')}\n"
        f"正文关键片段：\n{_render(main['text'], 5000)}"
    )
    parts = []
    budget = ATTACH_TOTAL_LIMIT
    for a in attachments:
        if budget <= 0:
            break
        chunk = _render(a["text"], min(ATTACH_TEXT_LIMIT, budget))
        budget -= len(chunk)
        parts.append(f"【{a['name']}｜类型：{a.get('type','其他')}】\n{chunk}")
    try:
        raw = llm.chat(RECONCILE_SYSTEM, RECONCILE_USER.format(
            elements=elements, attachments="\n\n".join(parts)))
        items = raw if isinstance(raw, list) else []
        out = []
        for it in items:
            if isinstance(it, dict):
                out.append({
                    "field": str(it.get("field") or "").strip(),
                    "files": str(it.get("files") or "").strip(),
                    "value_a": str(it.get("value_a") or "").strip(),
                    "value_b": str(it.get("value_b") or "").strip(),
                    "note": str(it.get("note") or "").strip(),
                })
        return out, None
    except Exception as exc:
        return [], f"跨文件勾稽失败：{type(exc).__name__}"


def analyze_contracts(uploaded, task=None, llm=None):
    """主入口。uploaded = [(filename, bytes), ...]；返回完整报告 dict。"""
    _update(task, stage="解析文件", done=0, total=len(uploaded))
    files = _parse_all(uploaded, task)

    parsed_ok = [f for f in files if not f.get("error") and f["chars"] > 0]
    parse_errors = [f for f in files if f.get("error") or f["chars"] == 0]

    files = _identify_roles(parsed_ok, llm, task)
    mains = [f for f in files if f["role"] == "主合同"]
    attachments = [f for f in files if f["role"] == "附件"]
    if not mains and files:
        mains = [files[0]]  # 兜底：至少审核一份
        attachments = [f for f in files[1:]]
    main = mains[0] if mains else None

    # 逐份识别主合同类型
    types = []
    for m in mains:
        ct = _identify_type(m, llm, task)
        if ct:
            types.append(ct)
    contract_type = types[0] if types else ""
    if len(set(types)) > 1:
        contract_type = "、".join(dict.fromkeys(types))

    issues, audit_errors = _audit_mains(mains, llm, task)
    recon, recon_err = _reconcile(main, attachments, llm, task)

    errors = [f"{f['name']}：{f.get('error') or '未提取到正文'}" for f in parse_errors]
    errors.extend(audit_errors)
    if recon_err and "未提供相关附件" not in recon_err:
        errors.append(recon_err)

    # 三级分层计数
    level_count = {"一般风险提示": 0, "待核实异常": 0, "证据支持的审计问题": 0}
    for it in issues:
        lv = it["level"] if it["level"] in level_count else "待核实异常"
        it["level"] = lv
        level_count[lv] += 1

    report = {
        "contract_type": contract_type,
        "file_roles": [{"name": f["name"], "role": f["role"], "type": f.get("type", ""),
                        "reason": f.get("role_reason", ""), "chars": f["chars"]} for f in files],
        "main_contract": main["name"] if main else "",
        "focus": main.get("focus", "") if main else "",
        "issue_count": len(issues),
        "issues": issues,
        "level_count": level_count,
        "reconciliation": recon,
        "recon_count": len(recon),
        "errors": errors,
        "disclaimer": "本报告为文本层面的风险提示与待核实事项，不构成违规定性结论。审计结论须结合适用制度与充分证据，由审计人员复核后作出。",
    }
    _update(task, stage="完成", done=task.get("total", len(uploaded)) if task else len(uploaded))
    return report
