# -*- coding: utf-8 -*-
"""QA 独立验证（维度2核心）：通过 /api/qa/agent/stream 真实 SSE 提问，
验证 Agent 调用 recall_regulations 工具、返回文档级制度清单、带内联引用角标。"""
import json
import re
import sys
import time

import httpx

BASE = "http://127.0.0.1:5281"
URL = BASE + "/api/qa/agent/stream"


def ask(question: str, timeout: int = 180):
    """读 SSE，返回 (events列表, 完整文本)。按 data: 行解析。"""
    events = []
    raw_lines = []
    started = time.time()
    with httpx.stream("POST", URL,
                      json={"question": question, "mode": "混合", "module": "assistant", "sessionId": ""},
                      timeout=timeout) as resp:
        print(f"  SSE HTTP {resp.status_code}  (question={question})")
        for line in resp.iter_lines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                raw_lines.append(payload)
                if payload:
                    try:
                        events.append(json.loads(payload))
                    except Exception as e:
                        print("  [WARN] 无法解析 data:", payload[:100], e)
    print(f"  SSE 结束，耗时 {time.time()-started:.1f}s，收到 {len(events)} 个事件")
    return events, "\n".join(raw_lines)


def analyze(question: str, expect_cat: str = ""):
    print("\n" + "=" * 74)
    print(f"【3】Agent 提问: {question}")
    print("=" * 74)
    events, raw = ask(question)

    types = {}
    step_tools = []
    step_actions = []
    done_result = None
    answer_delta_text = []
    error_msgs = []
    for e in events:
        t = e.get("type")
        types[t] = types.get(t, 0) + 1
        if t == "step":
            action = e.get("action") or {}
            tool = action.get("tool")
            step_tools.append(tool)
            result = ((action.get("detail") or {}).get("result") or {})
            step_actions.append({
                "tool": tool,
                "ok": action.get("ok"),
                "total": result.get("total"),
                "matched": result.get("matched"),
                "topTitles": [it.get("docTitle") or it.get("title") for it in (result.get("items") or [])[:6]],
                "topCats": [it.get("category") for it in (result.get("items") or [])[:6]],
                "notice": result.get("notice"),
            })
        elif t == "answer_delta":
            answer_delta_text.append(e.get("text", ""))
        elif t == "done":
            done_result = e.get("result") or {}
        elif t == "error":
            error_msgs.append(e.get("message"))

    print("  事件类型统计:", types)
    print("  工具调用序列:", step_tools)
    for i, a in enumerate(step_actions):
        print(f"    step[{i}] tool={a['tool']} ok={a['ok']} total={a['total']} matched={a['matched']}")
        print(f"           标题: {a['topTitles']}")
        print(f"           分类: {a['topCats']}")
        print(f"           notice: {a['notice']}")

    # 判定
    used_recall = "recall_regulations" in step_tools
    used_searchdocs = "search_docs" in step_tools
    recall_actions = [a for a in step_actions if a["tool"] == "recall_regulations"]
    doc_level = any(a["total"] and a["total"] >= 2 and a["topTitles"] for a in recall_actions)

    # done 结果里的 answer / citations
    conclusion = ""
    citations = []
    actions = []
    native = None
    if done_result:
        ans = done_result.get("answer") or {}
        if isinstance(ans, dict):
            conclusion = ans.get("conclusion") or ans.get("markdown") or ""
        citations = done_result.get("citations") or []
        actions = done_result.get("actions") or []
        native = done_result.get("nativeTools")
    if not conclusion and answer_delta_text:
        conclusion = "".join(answer_delta_text)

    inline_refs = re.findall(r"\[\d+\]", conclusion)
    # 分类树命中：done 或召回条目里出现 expect_cat
    cat_hit = False
    if expect_cat:
        blob = json.dumps({"conclusion": conclusion, "steps": step_actions,
                           "citations": citations}, ensure_ascii=False)
        cat_hit = expect_cat in blob

    print(f"\n  done事件存在: {done_result is not None}")
    print(f"  nativeTools: {native}")
    print(f"  使用recall_regulations: {used_recall} / 使用search_docs: {used_searchdocs}")
    print(f"  文档级清单(>=2篇带标题): {doc_level}")
    print(f"  回答内联引用角标: {inline_refs[:10]} (共{len(inline_refs)}处)")
    print(f"  citations数: {len(citations)}")
    print(f"  结论预览: {conclusion[:300]}")
    if expect_cat:
        print(f"  分类树命中「{expect_cat}」: {cat_hit}")
    if error_msgs:
        print(f"  ERROR事件: {error_msgs}")

    verdict = used_recall and not used_searchdocs and doc_level and len(inline_refs) > 0 and bool(conclusion)
    if expect_cat:
        verdict = verdict and cat_hit
    print(f"\n  >>> 判定: {'PASS' if verdict else 'FAIL'}")
    return verdict, {
        "types": types, "step_tools": step_tools, "used_recall": used_recall,
        "used_searchdocs": used_searchdocs, "doc_level": doc_level,
        "inline_refs": inline_refs, "citations": len(citations),
        "conclusion": conclusion[:500], "cat_hit": cat_hit, "errors": error_msgs,
    }


def main():
    results = {}
    results["3a_差旅费"] = analyze("差旅费相关的制度有哪些？请列举")
    results["3b_预算决算"] = analyze("预算及决算相关的制度有哪些？", expect_cat="预算")
    print("\n" + "=" * 74)
    print("汇总（制度全量召回）")
    print("-" * 70)
    for k, v in results.items():
        print(f"  {k}: {'PASS' if v[0] else 'FAIL'}")


if __name__ == "__main__":
    main()
