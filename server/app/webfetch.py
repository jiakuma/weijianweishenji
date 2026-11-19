"""网页链接导入。目前支持知乎回答与专栏文章。

知乎的回答内容需要登录态：调用方从设置里读取 zhihuCookie 传入。
取数优先走 api/v4 接口，失败时回退到页面内的 js-initialData。
"""
from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

ANSWER_URL = re.compile(r"(?:https?://)?(?:www\.)?zhihu\.com/question/(\d+)/answer/(\d+)", re.I)
ANSWER_SHORT = re.compile(r"(?:https?://)?(?:www\.)?zhihu\.com/answer/(\d+)", re.I)
ARTICLE_URL = re.compile(r"(?:https?://)?(?:www\.)?zhuanlan\.zhihu\.com/p/(\d+)", re.I)


def normalize_cookie(raw: str) -> str:
    """允许直接粘贴 cURL 命令、DevTools 请求头文本或纯 Cookie 字符串。"""
    s = (raw or "").strip()
    if not s:
        return ""
    # Copy as cURL: -H 'Cookie: xxx' / --header "cookie: xxx"
    m = re.search(r"(?i)(?:-H|--header)\s+['\"]?\s*cookie\s*:\s*([^'\"\n]+)", s)
    if m:
        return m.group(1).strip()
    # Copy as cURL: -b 'xxx' / --cookie "xxx"
    m = re.search(r"(?i)(?:-b|--cookie)\s+['\"]?([^'\"\n]+)", s)
    if m:
        return m.group(1).strip()
    # DevTools 请求头文本：cookie: xxx
    m = re.search(r"(?im)^\s*cookie\s*:\s*(.+)$", s)
    if m:
        return m.group(1).strip()
    return s


class LinkFetchError(Exception):
    """可展示给用户的抓取失败原因。"""


class NeedLogin(LinkFetchError):
    """需要登录态。"""


@dataclass
class FetchedLink:
    title: str
    markdown: str
    source_url: str


def is_zhihu(url: str) -> bool:
    return bool(ANSWER_URL.search(url) or ANSWER_SHORT.search(url) or ARTICLE_URL.search(url))


def _headers(cookie: str, referer: str) -> dict:
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": referer,
    }
    if cookie and cookie.strip():
        headers["Cookie"] = cookie.strip()
    return headers


def _check_error(resp: httpx.Response, cookie: str) -> None:
    if resp.status_code == 200:
        return
    body = resp.text[:400]
    if resp.status_code in (401, 403):
        if "need_login" in body or "unhuman" in body:
            if not cookie.strip():
                raise NeedLogin("该知乎内容需要登录：请在「系统设置 → 知乎 Cookie」填写后重试")
            raise NeedLogin("知乎 Cookie 已失效或权限不足，请重新填写")
        if "40362" in body or "请求存在异常" in body:
            raise LinkFetchError("知乎风控拦截了本次访问，请稍后重试或更换 Cookie")
    raise LinkFetchError(f"知乎返回 HTTP {resp.status_code}")


def _html_to_markdown(raw: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    s = re.sub(
        r'(?i)<img[^>]+(?:data-original|data-actualsrc|src)="([^"]+)"[^>]*>',
        lambda m: f"\n\n![]({m.group(1)})\n\n", s,
    )
    s = re.sub(
        r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        lambda m: f"[{re.sub(r'(?s)<[^>]+>', '', m.group(2)).strip()}]({m.group(1)})", s,
    )
    s = re.sub(r"(?i)<h([1-6])[^>]*>", lambda m: "\n\n" + "#" * int(m.group(1)) + " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "\n- ", s)
    s = re.sub(r"(?i)<blockquote[^>]*>", "\n> ", s)
    s = re.sub(r"(?i)</(p|div|section|blockquote|li|h[1-6])>", "\n\n", s)
    s = re.sub(r"(?is)<(b|strong)[^>]*>(.*?)</\1>", r"**\2**", s)
    s = re.sub(r"(?s)<[^>]+>", "", s)
    s = html_lib.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _build(title: str, author: str, content_html: str, voteup, url: str) -> str:
    meta = []
    if author:
        meta.append(f"作者：{author}")
    if isinstance(voteup, int) and voteup > 0:
        meta.append(f"赞同：{voteup}")
    meta.append(f"原文链接：{url}")
    return "\n".join([
        f"# {title}", "",
        " · ".join(meta), "",
        "---", "",
        _html_to_markdown(content_html),
    ]).strip() + "\n"


def _initial_state(html_text: str) -> dict | None:
    m = re.search(r'<script id="js-initialData"[^>]*>(.*?)</script>', html_text, re.S | re.I)
    if not m:
        return None
    try:
        return json.loads(html_lib.unescape(m.group(1)))
    except Exception:
        return None


def _deep_content(obj, best: str = "") -> str:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "content" and isinstance(value, str) and "<" in value and len(value) > len(best):
                best = value
            else:
                best = _deep_content(value, best)
    elif isinstance(obj, list):
        for value in obj:
            best = _deep_content(value, best)
    return best


def _from_page(client: httpx.Client, url: str, title: str, author: str, voteup, cookie: str) -> FetchedLink | None:
    resp = client.get(url, headers=_headers(cookie, "https://www.zhihu.com/"))
    if resp.status_code != 200:
        return None
    state = _initial_state(resp.text)
    content = _deep_content(state) if state else ""
    if not content:
        return None
    m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.S | re.I)
    if m:
        page_title = html_lib.unescape(re.sub(r"(?s)<[^>]+>", "", m.group(1))).strip()
        page_title = re.sub(r"\s*-\s*知乎\s*$", "", page_title)
        if page_title:
            title = page_title
    return FetchedLink(title, _build(title, author, content, voteup, url), url)


def _fetch_api(client: httpx.Client, url: str, api: str, cookie: str, default_title: str) -> FetchedLink:
    resp = client.get(api, headers=_headers(cookie, url))
    _check_error(resp, cookie)
    data = {}
    try:
        data = resp.json()
    except Exception:
        pass
    content = data.get("content") or ""
    author_obj = data.get("author") or {}
    author = author_obj.get("name", "") if isinstance(author_obj, dict) else str(author_obj)
    title = ""
    question = data.get("question")
    if isinstance(question, dict):
        title = question.get("title") or ""
    if not title:
        title = data.get("title") or default_title
    voteup = data.get("voteup_count") or data.get("voteupCount")
    if content.strip():
        return FetchedLink(
            title, _build(title, author, content, voteup, url), url,
        )
    fallback = _from_page(client, url, title, author, voteup, cookie)
    if fallback:
        return fallback
    if not cookie.strip():
        raise NeedLogin("未取到正文，该知乎内容可能需要登录：请在「系统设置 → 知乎 Cookie」填写后重试")
    raise LinkFetchError("未取到正文，可能是 Cookie 已失效或内容为付费/限制访问")


def fetch_zhihu(url: str, cookie: str = "") -> FetchedLink:
    m = ANSWER_URL.search(url)
    if m:
        aid = m.group(2)
        api = f"https://www.zhihu.com/api/v4/answers/{aid}?include=content,voteup_count,author,question,created_time"
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            return _fetch_api(client, url, api, cookie, f"知乎回答 {aid}")
    m = ANSWER_SHORT.search(url)
    if m:
        aid = m.group(1)
        api = f"https://www.zhihu.com/api/v4/answers/{aid}?include=content,voteup_count,author,question,created_time"
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            return _fetch_api(client, url, api, cookie, f"知乎回答 {aid}")
    m = ARTICLE_URL.search(url)
    if m:
        pid = m.group(1)
        api = f"https://www.zhihu.com/api/v4/articles/{pid}?include=content,title,author,voteup_count"
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            return _fetch_api(client, url, api, cookie, f"知乎专栏 {pid}")
    raise LinkFetchError("无法识别的知乎链接（支持回答与专栏文章）")
