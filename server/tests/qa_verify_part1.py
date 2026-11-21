# -*- coding: utf-8 -*-
"""QA 独立验证脚本（第1部分）：统计、语义检索、数据隔离、幂等性。"""
import json
import sys

import httpx

BASE = "http://127.0.0.1:5281"
ok = lambda b: "PASS" if b else "FAIL"


def show(title, resp):
    print("\n" + "=" * 70)
    print(title)
    print("-" * 70)
    print("HTTP", resp.status_code)
    try:
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2)[:6000])
    except Exception:
        print(resp.text[:4000])


def main():
    results = {}

    # ---------------- 1. 统计核对 ----------------
    r = httpx.get(BASE + "/api/audit-issues/stats", timeout=30)
    show("【1】GET /api/audit-issues/stats", r)
    data = r.json() if r.status_code == 200 else {}
    year = data.get("byYear", {})
    c1 = (data.get("dbTotal") == 772 and data.get("vectorTotal") == 772
          and year.get("2024") == 431 and year.get("2025") == 341)
    results["1_stats"] = c1
    print(f">>> 判定 1: {ok(c1)}  dbTotal={data.get('dbTotal')} vectorTotal={data.get('vectorTotal')} byYear={year}")

    # ---------------- 2. 历史问题语义检索 ----------------
    queries = [
        "医保管理 医疗费用收取不规范",
        "差旅费报销 超标准",
        "捐赠审计 物资管理",
    ]
    search_ok = True
    detail = []
    for q in queries:
        r = httpx.post(BASE + "/api/audit-issues/search", json={"query": q, "limit": 5}, timeout=60)
        d = r.json() if r.status_code == 200 else {}
        items = d.get("items", [])
        structured = all(
            ("category" in it and "problemDesc" in it and
             "legalBasis" in it and "remediation" in it and "notice" in it)
            for it in items
        ) if items else False
        years = sorted({it.get("sourceYear") for it in items})
        notices = all("复核" in it.get("notice", "") for it in items) if items else False
        print("\n" + "-" * 70)
        print(f"QUERY: {q}")
        print(f"  total={d.get('total')} vectorUsed={d.get('vectorUsed')} 年份命中={years}")
        print(f"  结构化字段齐全={structured} 复核提示={notices}")
        for it in items[:5]:
            print(f"    - [{it.get('sourceYear')}] {it.get('category','')[:60]}")
            print(f"      问题: {it.get('problemDesc','')[:70]}")
            print(f"      法定依据: {it.get('legalBasis','')[:50]} | 整改: {it.get('remediation','')[:40]}")
        good = d.get("total", 0) > 0 and structured and notices
        detail.append((q, good))
        if not good:
            search_ok = False
    results["2_search"] = search_ok
    print(f">>> 判定 2: {ok(search_ok)} 各查询: {[(q, ok(g)) for q,g in detail]}")

    # 特别验证 2024 检查要点 / 2025 真实问题召回
    r = httpx.post(BASE + "/api/audit-issues/search", json={"query": "检查要点 医疗费用", "limit": 10}, timeout=60)
    d = r.json() if r.status_code == 200 else {}
    y2024 = [it for it in d.get("items", []) if it.get("sourceYear") == 2024]
    r2 = httpx.post(BASE + "/api/audit-issues/search", json={"query": "审计发现 问题表述 整改", "limit": 10}, timeout=60)
    d2 = r2.json() if r2.status_code == 200 else {}
    y2025 = [it for it in d2.get("items", []) if it.get("sourceYear") == 2025]
    print(f"\n  2024条目可召回: {ok(len(y2024)>0)} (命中 {len(y2024)} 条)")
    print(f"  2025条目可召回: {ok(len(y2025)>0)} (命中 {len(y2025)} 条)")

    # ---------------- 4. 数据隔离 ----------------
    # /api/search 搜历史问题独特词，应只有制度库文档（docId 以 d 开头），无 AuditIssue
    r = httpx.get(BASE + "/api/search", params={"q": "医保管理情况", "mode": "混合"}, timeout=60)
    show("【4a】GET /api/search?q=医保管理情况", r)
    hits = r.json() if r.status_code == 200 else []
    bad_ids = [h.get("docId") for h in hits if not str(h.get("docId", "")).startswith("d")]
    iso_a = isinstance(hits, list) and len(bad_ids) == 0
    print(f">>> 4a: {ok(iso_a)} 返回{len(hits)}条, 非d开头docId={bad_ids}")

    # /api/audit-issues/search 只返回问题条目（id 以 ai- 开头）
    r = httpx.post(BASE + "/api/audit-issues/search", json={"query": "医保管理", "limit": 5}, timeout=60)
    d = r.json() if r.status_code == 200 else {}
    items = d.get("items", [])
    bad_ids2 = [it.get("id") for it in items if not str(it.get("id", "")).startswith("ai-")]
    iso_b = len(items) > 0 and len(bad_ids2) == 0
    print(f">>> 4b: {ok(iso_b)} 返回{len(items)}条, 非ai-开头id={bad_ids2}")
    results["4_isolation"] = iso_a and iso_b

    # ---------------- 6. 幂等性 ----------------
    # 需要找到原导入的 xlsx 路径
    import os
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    print("\n" + "=" * 70)
    print("【6】幂等性 - 查找 xlsx 源文件")
    print("-" * 70)
    xlsx = []
    for root, dirs, files in os.walk(os.path.join(os.path.dirname(__file__), "..")):
        for f in files:
            if f.lower().endswith((".xlsx", ".xlsm")) and ("问题" in f or "2024" in f or "2025" in f or "审计" in f):
                xlsx.append(os.path.abspath(os.path.join(root, f)))
    print("候选 xlsx:", json.dumps(xlsx, ensure_ascii=False, indent=2))
    results["_xlsx"] = xlsx

    print("\n" + "=" * 70)
    print("汇总（第1部分）")
    print("-" * 70)
    for k, v in results.items():
        if not k.startswith("_"):
            print(f"  {k}: {ok(v)}")


if __name__ == "__main__":
    main()
