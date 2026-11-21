# -*- coding: utf-8 -*-
"""查看制度文件导入进度。直接运行：python 查看导入进度.py"""
import httpx
from collections import Counter

BASE = "http://127.0.0.1:5281"

def main():
    try:
        tasks = httpx.get(BASE + "/api/imports", timeout=10).json()
        items = tasks.get("items", tasks) if isinstance(tasks, dict) else tasks
        docs = httpx.post(BASE + "/api/docs/query", json={}, timeout=15).json()
    except Exception as exc:
        print("连不上后端，请确认平台已启动（端口 5281）。", exc)
        return

    cnt = Counter(t.get("status") for t in items)
    total = len(items)
    done = cnt.get("已完成", 0)
    skip = cnt.get("重复跳过", 0)
    fail = cnt.get("失败", 0)
    waiting = cnt.get("等待中", 0)
    running = cnt.get("解析中", 0)

    print("=" * 48)
    print("制度文件导入进度")
    print("=" * 48)
    print(f"已入库资料：{len(docs)} 篇")
    print(f"任务总数：{total}")
    print(f"  ✅ 已完成   {done}")
    print(f"  ⏭  重复跳过 {skip}")
    print(f"  ❌ 失败     {fail}")
    print(f"  🔄 解析中   {running}")
    print(f"  ⏳ 等待中   {waiting}")
    processed = done + skip + fail
    if total:
        print(f"\n处理进度：{processed}/{total}（{processed*100//total}%）")
    if fail:
        print("\n失败项（前 20）：")
        for t in items:
            if t.get("status") == "失败":
                print("  -", (t.get("name") or "")[:50], "|", (t.get("message") or "")[:60])

if __name__ == "__main__":
    main()
