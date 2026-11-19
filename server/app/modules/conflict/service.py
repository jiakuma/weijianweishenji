"""制度互斥检测核心流程：解析 -> 归一化 -> 规则召回 -> LLM 精判 -> 汇总覆盖率。

与旧版的区别：
- 去掉 60000 字截断、1800 字滑窗、固定 8 类 topic、LLM 抽条款、LLM 预筛；
- 三种上传形态（大汇编 / 多小文件 / 混合）在 structure.py 归一化成 Unit / Clause 后统一比对；
- 候选对由规则倒排召回，只有「精判」交给 LLM，且每条结果带文件/制度/页码/条号定位；
- 全程记录覆盖率与失败数：精判全部失败会直接报错，绝不静默返回「0 冲突」。
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from ...llm import LLMConfig, OFFICIAL_MODEL, chat_url, normalize_base_url, post_chat
from .parser import parse_document, read_manifest
from .prompts import (
    BATCH_COMPARE_SYSTEM,
    BATCH_COMPARE_USER,
    COMPARE_SYSTEM,
    COMPARE_USER,
    UNIT_OVERLAP_SYSTEM,
    UNIT_OVERLAP_USER,
)
from .structure import Clause, Corpus, build_candidates, normalize

PARSE_WORKERS = 4
COMPARE_WORKERS = 4
DEFAULT_BUDGET = 240          # 单次任务送模型精判的候选对上限
BATCH_SIZE = 8                # 批量精判：一次请求判 8 对，同等调用量覆盖倍增
CLAUSE_TEXT_LIMIT = 420       # 单条条款送模型的原文长度
UNIT_OVERLAP_LIMIT = 120      # 进入粗判的制度对上限（按制度级向量相似度取）
UNIT_OVERLAP_SIM = 0.55       # 制度对进入粗判的相似度阈值
STAGE1_BATCH = 10             # 粗判一次请求判 10 个制度对
DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "conflict"

_done_lock = threading.Lock()


class LLMClient:
    """OpenAI 兼容端点的最小客户端，复用系统设置里的接口地址 / Key / 模型。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        base = normalize_base_url(base_url)
        self.url = chat_url(base)
        self.api_key = api_key
        self.model = model or OFFICIAL_MODEL
        # 批量精判输出更长（一次 8 对），放宽到 4096
        self.cfg = LLMConfig(base, api_key, self.model, 0.0, 4096, False)
        self._local = threading.local()

    def _http(self) -> httpx.Client:
        """每线程独立连接池，供并发精判复用连接。"""
        client = getattr(self._local, "client", None)
        if client is None:
            client = httpx.Client(timeout=60.0)
            self._local.client = client
        return client

    def chat(self, system: str, user: str):
        data = post_chat(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
            },
            timeout=60.0, config=self.cfg, client=self._http(),
        )
        choices = data.get("choices") or [{}]
        content = (choices[0].get("message", {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("模型返回空内容（可能是额度、模型名或代理配置问题）")
        return _extract_json(content)


def _extract_json(text: str):
    """从模型返回中稳定解析 JSON（兼容 ```json 代码块 / 前置解释文字）。"""
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
            except ValueError:
                continue
    raise ValueError("无法解析模型返回的 JSON")


def _clip(text: str, limit: int = CLAUSE_TEXT_LIMIT) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


# ------------------------------------------------------------------ 中间产物落盘

def _dump_artifacts(task_id: str | None, corpus: Corpus, pairs: list, report: dict) -> None:
    """把 Unit / Clause / 候选对 / 覆盖率落盘，便于复核「到底比了什么」。"""
    if not task_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task_id):
        return
    try:
        folder = DATA_DIR / task_id
        folder.mkdir(parents=True, exist_ok=True)
        payloads = {
            "units.json": [u.to_dict() for u in corpus.units],
            "clauses.json": [c.to_dict() for c in corpus.clauses],
            "candidates.json": [
                {"clause_a": corpus.clauses[i].clause_id,
                 "clause_b": corpus.clauses[j].clause_id,
                 "score": score, "shared": shared}
                for i, j, score, shared in pairs
            ],
            "coverage.json": report["coverage"],
        }
        for name, data in payloads.items():
            (folder / name).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    except Exception as exc:
        report["coverage"]["artifact_error"] = f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------ 解析 + 归一化

def _parse_one(item, task: dict | None, mock: bool):
    idx, fn, raw = item
    entry = task["files"][idx] if task else None
    if entry:
        entry["status"] = "parsing"
    ext = fn.lower().rsplit(".", 1)[-1] if "." in fn else ""
    result = {"filename": fn, "is_manifest": ext in ("xlsx", "xls"),
              "doc": None, "manifest": [], "chars": 0, "error": None, "note": None}
    try:
        if result["is_manifest"]:
            result["manifest"] = read_manifest(raw)
            result["note"] = f"制度清单（{len(result['manifest'])} 项）"
        else:
            doc = parse_document(fn, raw, ocr=not mock)
            result["doc"] = doc
            result["chars"] = doc.chars
            if doc.error:
                result["error"] = doc.error
            elif doc.note:
                result["note"] = ("演示模式未启用 OCR，扫描件未解析" if mock else doc.note)
            elif not doc.pages:
                result["note"] = "未提取到正文"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    if entry:
        entry["chars"] = result["chars"]
        entry["note"] = result["note"]
        entry["error"] = result["error"]
        entry["is_manifest"] = result["is_manifest"]
        entry["manifest_count"] = len(result["manifest"])
        entry["status"] = "error" if result["error"] else ("skipped" if result["note"] else "done")
    if task is not None:
        with _done_lock:
            task["done"] = sum(
                1 for f in task["files"] if f["status"] in ("done", "skipped", "error")
            )
    return result


def _doc_summary(r: dict, corpus: Corpus) -> dict:
    fn = r["filename"]
    units = [u for u in corpus.units if u.source_file == fn]
    return {
        "filename": fn,
        "chars": r["chars"],
        "truncated": False,          # 新流程不再截断正文
        "clause_count": sum(len(u.clauses) for u in units),
        "unit_count": len(units),
        "is_manifest": r["is_manifest"],
        "error": r["error"],
        "note": r["note"],
    }


def _prepare(uploaded, task: dict | None, mock: bool) -> tuple[list[dict], Corpus]:
    if task is not None:
        task["stage"] = "解析中"
    items = [(idx, fn, raw) for idx, (fn, raw) in enumerate(uploaded)]
    with ThreadPoolExecutor(max_workers=PARSE_WORKERS) as ex:
        results = list(ex.map(lambda x: _parse_one(x, task, mock), items))

    if task is not None:
        task["stage"] = "归一化中"
    docs = [r["doc"] for r in results if r["doc"] is not None]
    # 清单是权威制度名表；同一批上传的多个 xlsx 合并
    manifest = [m for r in results for m in r["manifest"]]
    corpus = normalize(docs, manifest)

    if task is not None:
        task["stage"] = "召回候选"
        for entry in task["files"]:
            units = [u for u in corpus.units if u.source_file == entry["filename"]]
            entry["units"] = len(units)
            entry["clauses"] = sum(len(u.clauses) for u in units)
    return results, corpus


# ------------------------------------------------------------------ 精判

def _compare_one(llm: LLMClient, ca: Clause, cb: Clause, shared: list[str]) -> dict:
    out = llm.chat(COMPARE_SYSTEM, COMPARE_USER.format(
        locate_a=ca.locate(), unit_a=ca.unit_name,
        chapter_a=ca.chapter or "—", article_a=ca.article or "—",
        text_a=_clip(ca.text),
        locate_b=cb.locate(), unit_b=cb.unit_name,
        chapter_b=cb.chapter or "—", article_b=cb.article or "—",
        text_b=_clip(cb.text),
        shared="、".join(shared[:8]) or "无",
    ))
    if not isinstance(out, dict):
        raise ValueError("模型未返回 JSON 对象")
    return out


def _pair_block(no: int, ca: Clause, cb: Clause, shared: list[str]) -> str:
    return (
        f"【条款对 {no}】\n"
        f"条款A｜来源：{ca.locate()}\n"
        f"所属制度：{ca.unit_name}｜章节：{ca.chapter or '—'}｜条号：{ca.article or '—'}\n"
        f"原文：{_clip(ca.text)}\n"
        f"条款B｜来源：{cb.locate()}\n"
        f"所属制度：{cb.unit_name}｜章节：{cb.chapter or '—'}｜条号：{cb.article or '—'}\n"
        f"原文：{_clip(cb.text)}\n"
        f"共享线索：{'、'.join(shared[:8]) or '无'}"
    )


def _compare_batch(llm: LLMClient, clauses: list[Clause], items: list) -> dict[int, dict]:
    """一次请求精判多对；items 为 [(i, j, score, shared), ...]，返回 {pair_no: result}。"""
    blocks = [_pair_block(n + 1, clauses[i], clauses[j], shared)
              for n, (i, j, _score, shared) in enumerate(items)]
    out = llm.chat(BATCH_COMPARE_SYSTEM, BATCH_COMPARE_USER.format(
        count=len(items), pairs="\n\n".join(blocks),
    ))
    results: dict[int, dict] = {}
    rows: list = []
    if isinstance(out, list):
        rows = out
    elif isinstance(out, dict):
        rows = out.get("results") or out.get("pairs") or []
    for item in rows:
        if isinstance(item, dict) and isinstance(item.get("pair"), int):
            results[item["pair"]] = item
    return results


def _format_conflict(ca: Clause, cb: Clause, r: dict, score: float, shared: list[str]) -> dict:
    subject = (r.get("subject") or "").strip()
    return {
        # ---- 旧字段：保证现有前端不破 ----
        "topic": subject if subject and subject != "无" else ca.topic,
        "conflict_type": r.get("conflict_type", "无"),
        "severity": r.get("severity", "无"),
        "explanation": r.get("explanation", ""),
        "file_a": ca.source_file,
        "chunk_a": ca.page,
        "subject_a": ca.unit_name,
        "quote_a": r.get("quote_a") or _clip(ca.text, 120),
        "file_b": cb.source_file,
        "chunk_b": cb.page,
        "subject_b": cb.unit_name,
        "quote_b": r.get("quote_b") or _clip(cb.text, 120),
        # ---- 新增定位字段 ----
        "unit_a": ca.unit_name, "unit_b": cb.unit_name,
        "page_a": ca.page, "page_b": cb.page,
        "article_a": ca.article, "article_b": cb.article,
        "chapter_a": ca.chapter, "chapter_b": cb.chapter,
        "locate_a": ca.locate(), "locate_b": cb.locate(),
        "clause_id_a": ca.clause_id, "clause_id_b": cb.clause_id,
        "score": score, "shared_keywords": shared,
    }


def _judge_unit_pairs(llm: LLMClient, units: list, batch: list) -> dict[int, dict]:
    blocks = []
    for n, (x, y, _s) in enumerate(batch):
        ua, ub = units[x], units[y]
        blocks.append(
            f"【制度对 {n + 1}】\n"
            f"制度A：《{ua.name}》（{ua.group or '未分组'}）简介：{_clip(ua.text, 200)}\n"
            f"制度B：《{ub.name}》（{ub.group or '未分组'}）简介：{_clip(ub.text, 200)}"
        )
    out = llm.chat(UNIT_OVERLAP_SYSTEM, UNIT_OVERLAP_USER.format(
        count=len(batch), pairs="\n\n".join(blocks),
    ))
    rows = out if isinstance(out, list) else (out.get("results") or out.get("pairs") if isinstance(out, dict) else [])
    res: dict[int, dict] = {}
    for item in rows or []:
        if isinstance(item, dict) and isinstance(item.get("pair"), int):
            res[item["pair"]] = item
    return res


def _unit_overlap_stage(llm: LLMClient, corpus: Corpus) -> tuple[set | None, dict]:
    """两阶段第一阶段：制度对粗判。

    制度级向量先筛出最相似的候选制度对（宁多勿漏的阈值），
    再由 LLM 判定是否「管辖重叠」，条款级精判只做在重叠的制度对内部。
    任何一步失败都降级为「全部保留」，保证召回不因粗判异常而缩水。
    返回 (重叠制度对集合 or None, stage1 统计)。
    """
    units = [u for u in corpus.units if u.clauses]
    stage1 = {"prescreened": 0, "overlap": 0, "degraded": False, "enabled": False}
    if len(units) < 4:
        # 单元太少时制度对本来就少，粗判没有收益
        return None, stage1
    try:
        from .embed import encode

        vecs = encode([f"{u.name} {u.group}：{u.text[:250]}" for u in units])
        if vecs is None:
            raise RuntimeError("embedding unavailable")
    except Exception:
        stage1["degraded"] = True
        return None, stage1

    import numpy as np

    m = np.asarray(vecs, dtype=np.float32)
    norm = np.linalg.norm(m, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    m = m / norm
    sim = m @ m.T
    iu = np.triu_indices(len(units), 1)
    sims = sim[iu]
    order = np.argsort(-sims)
    cand = [
        (int(iu[0][k]), int(iu[1][k]), float(sims[k]))
        for k in order if sims[k] >= UNIT_OVERLAP_SIM
    ][:UNIT_OVERLAP_LIMIT]
    stage1["enabled"] = True
    stage1["prescreened"] = len(cand)
    if not cand:
        return set(), stage1

    batches = [cand[k:k + STAGE1_BATCH] for k in range(0, len(cand), STAGE1_BATCH)]
    keep: set = set()
    failed_batches = 0

    def _work(batch):
        try:
            return batch, _judge_unit_pairs(llm, units, batch), None
        except Exception as exc:
            return batch, None, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=min(COMPARE_WORKERS, len(batches))) as ex:
        for batch, res, err in ex.map(_work, batches):
            if err:
                failed_batches += 1
                # 粗判失败的一批整体保留（宁多勿漏）
                for x, y, _s in batch:
                    keep.add(frozenset((units[x].unit_id, units[y].unit_id)))
                continue
            for n, (x, y, _s) in enumerate(batch):
                r = res.get(n + 1)
                # 粗判项缺失同样按保留处理
                if r is None or r.get("overlap") is True:
                    keep.add(frozenset((units[x].unit_id, units[y].unit_id)))

    if failed_batches == len(batches):
        stage1["degraded"] = True
        return None, stage1  # 粗判全失败：不筛选，全部候选进入精判
    stage1["overlap"] = len(keep)
    return keep, stage1


def _real_analyze(corpus: Corpus, task: dict | None, llm: LLMClient, budget: int) -> tuple[list, dict, list]:
    clauses = corpus.clauses
    if task is not None:
        task["stage"] = "制度对粗判"
    unit_filter, stage1 = _unit_overlap_stage(llm, corpus)
    pairs, total, semantic_used = build_candidates(clauses, max_pairs=budget, unit_pair_filter=unit_filter)
    if task is not None:
        task["compare_total"] = len(pairs)
        task["compare_done"] = 0
        task["stage"] = "比对中"

    def _work(p):
        i, j, score, shared = p
        try:
            return i, j, score, shared, _compare_one(llm, clauses[i], clauses[j], shared), None
        except Exception as exc:
            return i, j, score, shared, None, f"{type(exc).__name__}: {exc}"

    conflicts: list[dict] = []
    failures: list[str] = []
    first_error = ""
    if pairs:
        batches = [pairs[k:k + BATCH_SIZE] for k in range(0, len(pairs), BATCH_SIZE)]

        def _work_batch(batch):
            try:
                return batch, _compare_batch(llm, clauses, batch), None
            except Exception as exc:
                return batch, {}, f"{type(exc).__name__}: {exc}"

        with ThreadPoolExecutor(max_workers=min(COMPARE_WORKERS, len(batches))) as ex:
            futures = [ex.submit(_work_batch, b) for b in batches]
            for fut in as_completed(futures):
                batch, results, err = fut.result()
                if err and not first_error:
                    first_error = err
                # 批量结果里缺失的对（或整批失败），逐对降级重试，绝不静默丢对
                for n, (i, j, score, shared) in enumerate(batch):
                    r = results.get(n + 1)
                    if r is None:
                        try:
                            r = _compare_one(llm, clauses[i], clauses[j], shared)
                        except Exception as exc:
                            failures.append(f"{type(exc).__name__}: {exc}")
                            continue
                    if r.get("conflict") is True:
                        conflicts.append(_format_conflict(clauses[i], clauses[j], r, score, shared))
                if task is not None:
                    with _done_lock:
                        task["compare_done"] = task.get("compare_done", 0) + len(batch)

    # 精判全失败必须显式报错，不能再伪装成「0 冲突」
    if pairs and len(failures) == len(pairs):
        raise RuntimeError(f"大模型精判全部失败（{len(failures)}/{len(pairs)}）：{first_error}")

    conflicts.sort(key=lambda x: ({"高": 0, "中": 1, "低": 2}.get(x["severity"], 3), -x["score"]))
    coverage = dict(corpus.coverage)
    recall_desc = (
        "语义相似度阈值 + 制度对粗判过滤后按相关度排序" if (semantic_used and stage1.get("enabled"))
        else "语义相似度阈值过滤后按相关度排序" if semantic_used
        else "关键词召回按相关度排序（语义模型不可用，已降级）"
    )
    coverage.update({
        "theoretical_pairs": len(clauses) * (len(clauses) - 1) // 2,
        "candidate_pairs": total,
        "compared_pairs": len(pairs),
        "judged_pairs": len(pairs) - len(failures),
        "unit_pairs_compared": len({
            tuple(sorted((clauses[i].unit_id, clauses[j].unit_id)))
            for i, j, _, _ in pairs
        }),
        "failed_pairs": len(failures),
        "first_error": first_error,
        "budget": budget,
        "skipped_pairs": max(0, total - len(pairs)),
        "skip_reason": f"{recall_desc}，仅精判 Top-{len(pairs)}；其余未送模型" if total > len(pairs) else "候选池已全部精判",
        "semantic_recall": semantic_used,
        "stage1": stage1,
    })
    return conflicts, coverage, pairs


# ------------------------------------------------------------------ 演示模式

def _mock_analyze(corpus: Corpus) -> tuple[list, dict]:
    """演示数据：仍走真实解析与归一化，只用假冲突占位，定位信息取自真实条款。"""
    clauses = corpus.clauses
    if len(clauses) >= 2:
        ca, cb = clauses[0], clauses[-1]
    else:
        ca = cb = Clause("demo", "U0000", "演示制度", "演示文件.pdf", text="示例条款")
    conflicts = [
        {
            "topic": "差旅费报销审批",
            "conflict_type": "权限冲突",
            "severity": "高",
            "explanation": "同一事项「差旅费报销审批」，两份文件规定的审批主体矛盾：一份要求部门经理审批，另一份要求财务总监审批，执行时无法判断。（演示数据）",
            "file_a": ca.source_file, "chunk_a": ca.page,
            "subject_a": ca.unit_name, "quote_a": "单笔差旅费报销由部门经理审批。",
            "file_b": cb.source_file, "chunk_b": cb.page,
            "subject_b": cb.unit_name, "quote_b": "所有差旅费报销须经财务总监审批。",
            "unit_a": ca.unit_name, "unit_b": cb.unit_name,
            "page_a": ca.page, "page_b": cb.page,
            "article_a": ca.article, "article_b": cb.article,
            "locate_a": ca.locate(), "locate_b": cb.locate(),
            "score": 0, "shared_keywords": [],
        },
        {
            "topic": "市内交通补助标准",
            "conflict_type": "标准冲突",
            "severity": "中",
            "explanation": "同一「市内交通补助」标准不一致：一份规定每日 80 元，另一份规定每日 120 元，口径冲突。（演示数据）",
            "file_a": ca.source_file, "chunk_a": ca.page,
            "subject_a": ca.unit_name, "quote_a": "市内交通补助标准为 80 元/天。",
            "file_b": cb.source_file, "chunk_b": cb.page,
            "subject_b": cb.unit_name, "quote_b": "市内交通补助标准为 120 元/天。",
            "unit_a": ca.unit_name, "unit_b": cb.unit_name,
            "page_a": ca.page, "page_b": cb.page,
            "article_a": ca.article, "article_b": cb.article,
            "locate_a": ca.locate(), "locate_b": cb.locate(),
            "score": 0, "shared_keywords": [],
        },
    ]
    coverage = dict(corpus.coverage)
    coverage.update({
        "candidate_pairs": 0, "compared_pairs": 0, "judged_pairs": 0,
        "failed_pairs": 0, "skipped_pairs": 0,
        "skip_reason": "演示模式未调用大模型", "budget": 0,
    })
    return conflicts, coverage


# ------------------------------------------------------------------ 对外入口

def analyze_files(uploaded, task: dict = None, llm: LLMClient | None = None,
                  mock: bool = False, budget: int = DEFAULT_BUDGET):
    """uploaded: list of (filename, raw_bytes)；task: 进度字典，逐阶段回写。"""
    results, corpus = _prepare(uploaded, task, mock)

    if mock:
        conflicts, coverage = _mock_analyze(corpus)
        pairs: list = []
    else:
        if llm is None:
            raise RuntimeError("未配置大模型，请到系统设置填写接口地址与 API Key")
        conflicts, coverage, pairs = _real_analyze(corpus, task, llm, budget)

    report = {
        "mode": "mock" if mock else "real",
        "docs": [_doc_summary(r, corpus) for r in results],
        "units": corpus.units_summary(),
        "clause_count": len(corpus.clauses),
        "topics": list(dict.fromkeys(c["topic"] for c in conflicts)),
        "coverage": coverage,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
    }
    _dump_artifacts((task or {}).get("task_id"), corpus, pairs, report)

    if task is not None:
        task["conflicts"] = conflicts
        task["conflict_count"] = len(conflicts)
        task["report"] = report
        task["units"] = len(corpus.units)
        task["clauses"] = len(corpus.clauses)
        task["stage"] = "完成"
    return report
