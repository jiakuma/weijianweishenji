# -*- coding: utf-8 -*-
"""QA 独立验证（幂等性）：重复 import（replace=true）后仍 772 条、无膨胀。"""
import json
import time

import httpx

BASE = "http://127.0.0.1:5281"

FILES = [
    r"D:/desktop/智能体材料/审计知识助手优化/知识库梳理与雷处对接/新建文件夹 (2)/历年审计发现的问题/2024年度内审、外审发现问题明细表.xlsx",
    r"D:/desktop/智能体材料/审计知识助手优化/知识库梳理与雷处对接/新建文件夹 (2)/历年审计发现的问题/2025年度内审、外审发现问题明细表.xlsx",
]


def get_stats():
    return httpx.get(BASE + "/api/audit-issues/stats", timeout=30).json()


def main():
    print("=" * 70)
    print("【6】幂等性验证 - 重复 import(replace=true)")
    print("=" * 70)

    before = get_stats()
    print("导入前 stats:", json.dumps({k: before[k] for k in ("dbTotal", "vectorTotal", "byYear")}, ensure_ascii=False))

    print("\n调用 POST /api/audit-issues/import (replace=true) ...")
    started = time.time()
    r = httpx.post(BASE + "/api/audit-issues/import",
                   json={"files": FILES, "replace": True}, timeout=300)
    print("HTTP", r.status_code, "耗时 %.1fs" % (time.time() - started))
    result = r.json()
    print(json.dumps({k: result.get(k) for k in
                      ("ok", "total", "parsed", "dbOk", "vectorOk", "replaced",
                       "dbCleared", "vectorCleared", "failed", "perFile")},
                     ensure_ascii=False, indent=2))

    after = get_stats()
    print("\n导入后 stats:", json.dumps({k: after[k] for k in ("dbTotal", "vectorTotal", "byYear")}, ensure_ascii=False))

    c_idem = (
        before.get("dbTotal") == 772 and after.get("dbTotal") == 772
        and after.get("vectorTotal") == 772
        and after.get("byYear") == {"2024": 431, "2025": 341}
        and result.get("dbOk") == 772 and result.get("vectorOk") == 772
        and not any(f.get("phase") == "db" for f in result.get("failed", []))
    )
    print(f"\n>>> 判定 6 幂等性: {'PASS' if c_idem else 'FAIL'}"
          f"  (前={before.get('dbTotal')} 后={after.get('dbTotal')} "
          f"vector={after.get('vectorTotal')} byYear={after.get('byYear')})")


if __name__ == "__main__":
    main()
