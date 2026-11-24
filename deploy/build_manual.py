#!/usr/bin/env python3
"""生成审计工作平台产品手册 HTML（样式参考用户提供的产品手册，截图取自真实系统）。"""
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "screenshots"
OUT = ROOT / "docs" / "审计工作平台-产品手册.html"

def img(name: str) -> str:
    p = SHOTS / f"{name}.png"
    if not p.exists():
        return ""
    data = base64.b64encode(p.read_bytes()).decode()
    return f'data:image/png;base64,{data}'

def figure(name: str, caption: str) -> str:
    src = img(name)
    if not src:
        return ""
    return f'<figure><img src="{src}" alt="{caption}"><figcaption><b>图</b> {caption}</figcaption></figure>'

CSS = """
  :root {
    --yd-brand: #4F46E5; --yd-brand-soft: #EEF0FF; --yd-brand-pressed: #3730A3;
    --fg: #2C324E; --fg-2: #6B7089; --fg-3: #A1A4B2; --fg-strong: #1E3A6E;
    --bg: #FFFFFF; --bg-2: #F7F9FC; --accent: #EDF0F7; --border: #DDDFEB; --muted: #C5C8D6;
    --ok: #17DF97; --ok-fg: #1D8D68; --ok-soft: #E5FFF7;
    --warn: #F37232; --warn-fg: #B74D17; --warn-soft: #FFF2E5;
    --err: #EB5E75; --err-fg: #C82241; --err-soft: #FFF2F4;
    --purple: #B075EB; --purple-fg: #7D2BD6; --purple-soft: #F9F2FF;
    --shadow-sm: 0 1px 3px 0 rgba(90,95,124,.10), 0 1px 2px -1px rgba(90,95,124,.10);
    --shadow-md: 0 4px 16px -4px rgba(44,50,78,.12), 0 2px 6px -2px rgba(44,50,78,.08);
    --r: 4px; --r-lg: 8px;
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; scroll-padding-top: 24px; }
  body { margin: 0; background: var(--bg-2); color: var(--fg);
    font-family: 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', system-ui, sans-serif;
    font-size: 14px; line-height: 22px; -webkit-font-smoothing: antialiased; }
  a { color: var(--yd-brand); text-decoration: none; }
  a:hover { text-decoration: underline; }
  code, kbd, .num { font-family: 'SF Mono', 'JetBrains Mono', Menlo, monospace; font-variant-numeric: tabular-nums; }
  code { background: var(--accent); border-radius: var(--r); padding: 1px 5px; font-size: 12.5px; color: var(--fg-strong); }
  kbd { display: inline-block; min-width: 20px; padding: 0 5px; border: 1px solid var(--muted);
    border-bottom-width: 2px; border-radius: var(--r); background: #fff; font-size: 12px;
    line-height: 18px; text-align: center; color: var(--fg); }
  .layout { display: grid; grid-template-columns: 268px minmax(0, 1fr); }
  nav.toc { position: sticky; top: 0; height: 100vh; overflow-y: auto; background: #fff;
    border-right: 1px solid var(--border); padding: 20px 12px 40px; }
  nav.toc::-webkit-scrollbar { width: 8px; }
  nav.toc::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
  nav.toc .brand { display: flex; align-items: center; gap: 9px; padding: 0 8px 4px; }
  nav.toc .brand i { width: 26px; height: 26px; border-radius: 7px; flex: none;
    background: linear-gradient(135deg, var(--yd-brand), #7D7DF0); }
  nav.toc .brand b { font-size: 14px; font-weight: 600; color: var(--fg-strong); line-height: 18px; }
  nav.toc .brand span { display: block; font-size: 11px; color: var(--fg-3); font-weight: 400; }
  nav.toc .ver { font-size: 11px; color: var(--fg-3); padding: 6px 8px 12px; border-bottom: 1px solid var(--border); margin-bottom: 8px; }
  nav.toc .group { font-size: 11px; color: var(--fg-3); letter-spacing: .06em; font-weight: 600; padding: 14px 8px 4px; }
  nav.toc a { display: flex; align-items: baseline; gap: 7px; padding: 6px 8px; border-radius: var(--r);
    color: var(--fg); font-weight: 450; font-size: 13.5px; }
  nav.toc a:hover { background: var(--accent); text-decoration: none; }
  nav.toc a .n { font-family: 'SF Mono', Menlo, monospace; font-size: 11px; color: var(--fg-3); flex: none; width: 16px; }
  nav.toc a.on { background: var(--yd-brand-soft); color: var(--yd-brand-pressed); font-weight: 600; }
  main { padding: 40px 48px 120px; max-width: 1080px; min-width: 0; }
  h1 { font-size: 30px; line-height: 40px; font-weight: 700; letter-spacing: -.01em; margin: 0 0 6px; }
  h2 { font-size: 23px; line-height: 32px; font-weight: 650; letter-spacing: -.01em;
    margin: 64px 0 6px; padding-top: 20px; border-top: 1px solid var(--border); scroll-margin-top: 16px; }
  h2 .n { color: var(--fg-3); font-weight: 500; margin-right: 8px; font-variant-numeric: tabular-nums; }
  h3 { font-size: 17px; line-height: 26px; font-weight: 650; margin: 32px 0 8px; scroll-margin-top: 16px; }
  h4 { font-size: 14.5px; line-height: 22px; font-weight: 650; margin: 22px 0 6px; color: var(--fg-strong); }
  p { margin: 8px 0; }
  .lead { color: var(--fg-2); font-size: 15.5px; line-height: 25px; margin-bottom: 10px; }
  .sect-sub { color: var(--fg-2); margin: 0 0 20px; font-size: 14px; }
  ul, ol { margin: 8px 0; padding-left: 22px; }
  li { margin: 4px 0; }
  strong { font-weight: 600; color: var(--fg-strong); }
  .card { background: #fff; border: 1px solid var(--border); border-radius: var(--r-lg);
    padding: 16px 20px; margin: 14px 0; box-shadow: var(--shadow-sm); }
  .callout { background: var(--yd-brand-soft); border-radius: var(--r-lg); padding: 11px 16px; margin: 14px 0; }
  .callout.warn { background: var(--warn-soft); }
  .callout.err { background: var(--err-soft); }
  .callout.ok { background: var(--ok-soft); }
  .callout.purple { background: var(--purple-soft); }
  .callout .t { font-weight: 650; margin-bottom: 3px; font-size: 13.5px; }
  .callout p { margin: 4px 0; font-size: 13.5px; line-height: 21px; }
  .callout p:last-child { margin-bottom: 0; }
  table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--border);
    border-radius: var(--r-lg); overflow: hidden; margin: 14px 0; font-size: 13px; }
  th { background: var(--accent); text-align: left; font-weight: 600; color: var(--fg-2); padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 12.5px; }
  td { padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: top; line-height: 20px; }
  tr:last-child td { border-bottom: 0; }
  ol.steps { counter-reset: s; list-style: none; padding: 0; margin: 14px 0; }
  ol.steps > li { counter-increment: s; position: relative; padding: 10px 0 10px 42px; border-bottom: 1px dashed var(--border); }
  ol.steps > li:last-child { border-bottom: 0; }
  ol.steps > li::before { content: counter(s); position: absolute; left: 0; top: 11px; width: 26px; height: 26px;
    border-radius: 50%; background: var(--yd-brand); color: #fff; font-weight: 650;
    text-align: center; line-height: 26px; font-size: 12.5px; font-variant-numeric: tabular-nums; }
  ol.steps b { font-weight: 650; color: var(--fg-strong); }
  ol.steps .see { display: block; color: var(--fg-2); font-size: 13px; margin-top: 2px; }
  ol.steps .see::before { content: '说明：'; color: var(--fg-3); }
  figure { margin: 16px 0 22px; }
  figure img { width: 100%; display: block; border: 1px solid var(--border); border-radius: var(--r-lg);
    box-shadow: var(--shadow-md); background: #fff; }
  figcaption { font-size: 12.5px; color: var(--fg-2); margin-top: 8px; line-height: 19px; }
  figcaption b { color: var(--fg-strong); font-weight: 600; }
  .badge { display: inline-block; font-size: 11.5px; line-height: 18px; padding: 0 8px;
    border-radius: 999px; background: var(--accent); color: var(--fg-2); margin-right: 4px; vertical-align: 1px; }
  .badge.b { background: var(--yd-brand-soft); color: var(--yd-brand); }
  .badge.o { background: var(--warn-soft); color: var(--warn-fg); }
  .badge.g { background: var(--ok-soft); color: var(--ok-fg); }
  .badge.p { background: var(--purple-soft); color: var(--purple-fg); }
  .badge.r { background: var(--err-soft); color: var(--err-fg); }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
  .hero { background: #fff; border: 1px solid var(--border); border-radius: 12px;
    padding: 32px 36px; margin: 0 0 28px; box-shadow: var(--shadow-sm);
    background-image: linear-gradient(180deg, #F7F9FF 0%, #FFFFFF 60%); }
  .hero .kicker { font-size: 12px; letter-spacing: .08em; color: var(--yd-brand); font-weight: 600; margin-bottom: 8px; }
  .hero h1 { margin-bottom: 10px; }
  .hero .meta { color: var(--fg-3); font-size: 12.5px; margin-top: 16px; padding-top: 14px; border-top: 1px dashed var(--border); }
  .flow { display: flex; align-items: stretch; gap: 0; margin: 18px 0; overflow-x: auto; padding-bottom: 4px; }
  .flow .step { flex: 1; min-width: 116px; background: #fff; border: 1px solid var(--border); padding: 10px 12px; position: relative; }
  .flow .step + .step { border-left: 0; }
  .flow .step:first-child { border-radius: var(--r-lg) 0 0 var(--r-lg); }
  .flow .step:last-child { border-radius: 0 var(--r-lg) var(--r-lg) 0; }
  .flow .step .k { font-size: 10.5px; color: var(--fg-3); font-family: 'SF Mono', Menlo, monospace; }
  .flow .step .h { font-weight: 650; font-size: 13px; color: var(--fg-strong); margin: 2px 0; }
  .flow .step .d { font-size: 12px; color: var(--fg-2); line-height: 17px; }
  .kv { display: grid; grid-template-columns: 96px 1fr; gap: 6px 14px; font-size: 13.5px; margin: 10px 0; }
  .kv dt { color: var(--fg-3); }
  .kv dd { margin: 0; color: var(--fg); }
  @media (max-width: 1180px) {
    .layout { grid-template-columns: 1fr; }
    nav.toc { position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--border); }
    main { padding: 28px 20px 80px; }
    .two, .grid3 { grid-template-columns: 1fr; }
  }
  @media print {
    nav.toc { display: none; }
    .layout { grid-template-columns: 1fr; }
    main { max-width: none; padding: 0; }
    h2 { page-break-before: always; }
    figure img { box-shadow: none; }
  }
"""

TOC = """
<nav class="toc" id="toc">
  <div class="brand"><i></i><div><b>审计工作平台</b><span>用户操作手册</span></div></div>
  <div class="ver">v0.2.0 · 2026-11-23</div>
  <div class="group">开始使用</div>
  <a href="#s1"><span class="n">1</span>系统概览</a>
  <a href="#s2"><span class="n">2</span>登录与改密</a>
  <a href="#s3"><span class="n">3</span>界面总览</a>
  <div class="group">审计模块</div>
  <a href="#s4"><span class="n">4</span>审计助手</a>
  <a href="#s5"><span class="n">5</span>制度互斥检测</a>
  <a href="#s6"><span class="n">6</span>合同审计</a>
  <a href="#s7"><span class="n">7</span>底稿审核</a>
  <div class="group">基础模块</div>
  <a href="#s8"><span class="n">8</span>资料库</a>
  <a href="#s9"><span class="n">9</span>导入资料</a>
  <a href="#s10"><span class="n">10</span>历史问题库</a>
  <div class="group">管理模块</div>
  <a href="#s11"><span class="n">11</span>用户管理</a>
  <a href="#s12"><span class="n">12</span>反馈监控</a>
  <div class="group">附录</div>
  <a href="#a1"><span class="n">A</span>常见问题</a>
  <a href="#a2"><span class="n">B</span>术语表</a>
</nav>
"""

BODY = f"""
<main>
<div class="hero">
  <div class="kicker">用户操作手册</div>
  <h1>审计工作平台</h1>
  <p class="lead">本手册按实际使用顺序，逐模块讲解系统操作：登录、资料导入、智能问答、制度互斥检测、合同审计、底稿审核、反馈提交与管理。全部截图取自系统真实界面。</p>
  <div class="meta">适用版本 v0.2.0 · 编制日期 2026-11-23 · 本地部署、数据不出内网</div>
</div>

<h2 id="s1"><span class="n">1</span>系统概览</h2>
<p class="sect-sub">先了解系统在审计工作中承担什么、由哪些页面组成。</p>
<p>本平台把资料管理、智能问答、条款核查与底稿复核整合到一个工作台：先把资料导入建库，再用审计助手检索问答，或针对特定任务做制度互斥、合同审计、底稿审核；使用中的意见可提交反馈，管理员在监控台统一处理。</p>
<div class="grid3">
  <div class="card" style="margin:0"><div class="badge b">原则一</div><h4 style="margin-top:8px">只给提示，不定性</h4><p style="margin:4px 0 0;color:var(--fg-2);font-size:13px">系统只陈述可核实的事实与需要核实的地方，是否违规由审计人员判断，不输出违规定性结论。</p></div>
  <div class="card" style="margin:0"><div class="badge p">原则二</div><h4 style="margin-top:8px">答案带依据</h4><p style="margin:4px 0 0;color:var(--fg-2);font-size:13px">问答答案附引用溯源，审核结论写明对应的上位文件与条款，便于逐条查证。</p></div>
  <div class="card" style="margin:0"><div class="badge g">原则三</div><h4 style="margin-top:8px">数据不出内网</h4><p style="margin:4px 0 0;color:var(--fg-2);font-size:13px">本地部署，资料与向量索引都在本机；大模型仅用于调用，配置存数据库不写入代码。</p></div>
</div>
<h3>使用流程</h3>
<div class="flow">
  <div class="step"><div class="k">01</div><div class="h">导入</div><div class="d">把制度、合同、底稿等材料入库</div></div>
  <div class="step"><div class="k">02</div><div class="h">问答</div><div class="d">用审计助手检索与问答</div></div>
  <div class="step"><div class="k">03</div><div class="h">核查</div><div class="d">制度互斥、合同审计、底稿审核</div></div>
  <div class="step"><div class="k">04</div><div class="h">反馈</div><div class="d">提交建议，管理员处理</div></div>
</div>
<h3>页面一览</h3>
<table>
  <tr><th style="width:150px">页面</th><th style="width:120px">谁使用</th><th>作用</th><th style="width:56px">章节</th></tr>
  <tr><td>审计助手</td><td>全部用户</td><td>基于知识库问答，带引用溯源。</td><td><a href="#s4">4</a></td></tr>
  <tr><td>制度互斥</td><td>审计人员</td><td>比对制度条款冲突，给风险等级与定位。</td><td><a href="#s5">5</a></td></tr>
  <tr><td>合同审计</td><td>审计人员</td><td>合同文本问题与跨文件勾稽检查。</td><td><a href="#s6">6</a></td></tr>
  <tr><td>底稿审核</td><td>审计人员</td><td>以上位文件逐项核对底稿是否支撑结论。</td><td><a href="#s7">7</a></td></tr>
  <tr><td>资料库</td><td>全部用户</td><td>资料目录、原文查看、分类管理。</td><td><a href="#s8">8</a></td></tr>
  <tr><td>导入资料</td><td>管理员</td><td>上传文件、解析、分类、切片入库。</td><td><a href="#s9">9</a></td></tr>
  <tr><td>历史问题库</td><td>审计人员</td><td>历史审计问题定性，供问答参考。</td><td><a href="#s10">10</a></td></tr>
  <tr><td>用户管理</td><td>管理员</td><td>账号新增、编辑、停用、改密。</td><td><a href="#s11">11</a></td></tr>
  <tr><td>反馈监控</td><td>管理员</td><td>查看用户建议，筛选与处理。</td><td><a href="#s12">12</a></td></tr>
</table>

<h2 id="s2"><span class="n">2</span>登录与改密</h2>
<p class="sect-sub">首次使用先登录，并按提示修改初始密码。</p>
<ol class="steps">
  <li><b>打开系统</b> 浏览器访问系统地址，进入登录页。<span class="see">本地开发为 http://127.0.0.1:5280，部署后为服务器地址。</span></li>
  <li><b>输入账号密码</b> 用户名 <code>admin</code>，初始密码 <code>Admin@12345</code>。<span class="see">管理员账号由系统初始化生成，普通用户由管理员创建。</span></li>
  <li><b>修改初始密码</b> 首次登录会强制跳转到修改密码页，设置新密码后才能进入系统。</li>
</ol>
{figure('01-login', '登录页：输入用户名与密码')}
<div class="callout warn"><div class="t">安全提醒</div><p>初始密码仅用于首次登录，务必立即修改。部署环境建议使用 HTTPS；当前 IP+HTTP 场景请只在可信网络内访问。</p></div>

<h2 id="s3"><span class="n">3</span>界面总览</h2>
<p class="sect-sub">认识左侧导航、会话区与工作区三部分。</p>
<p>登录后进入统一工作区。界面分三栏：<strong>左侧导航</strong>列出全部模块，<strong>中间会话栏</strong>显示历史会话与新会话入口，<strong>右侧工作区</strong>是当前会话的对话内容与输入区。</p>
<figure><img src="{img('02-assistant')}" alt="界面总览"><figcaption><b>图</b> 界面总览：左侧导航、会话列表、对话工作区</figcaption></figure>
<p>四个审计模块（审计助手、制度互斥、合同审计、底稿审核）共用这套对话式工作区，通过左侧导航切换，会话按模块隔离管理。</p>

<h2 id="s4"><span class="n">4</span>审计助手</h2>
<p class="sect-sub">基于知识库提问，答案带引用，可追问。</p>
<ol class="steps">
  <li><b>新建会话</b> 在中间会话栏点「新会话」，开始一轮新问答。</li>
  <li><b>输入问题</b> 在底部输入框写明问题，按 <kbd>Enter</kbd> 发送，<kbd>Shift</kbd>+<kbd>Enter</kbd> 换行。<span class="see">可点「附件」上传文件，文件进入隔离临时区，不入资料库。</span></li>
  <li><b>查看答案</b> 系统检索知识库后作答，结论与依据分段展示，引用可直接点开核对。</li>
  <li><b>评价与反馈</b> 回答下方可点赞、点踩、重试；不满意可点「提建议」填写文字说明。</li>
</ol>
<figure><img src="{img('02-assistant')}" alt="审计助手"><figcaption><b>图</b> 审计助手：问答对话、引用与操作按钮</figcaption></figure>
<div class="callout"><div class="t">好问题的写法</div><p>写清主题与范围，例如「差旅费报销有哪些制度」「某现象是否违规、依据是什么」。问题越具体，检索越准。</p></div>

<h2 id="s5"><span class="n">5</span>制度互斥检测</h2>
<p class="sect-sub">上传多份制度，自动比对相互冲突的条款。</p>
<ol class="steps">
  <li><b>进入模块</b> 左侧导航点「制度互斥」。</li>
  <li><b>上传制度文件</b> 点「附件」选择多份制度文件，支持 PDF、Word 等格式。</li>
  <li><b>发起检测</b> 输入任务说明后发送，系统比对条款间的冲突。</li>
  <li><b>查看报告</b> 结果列出冲突条款的双方原文、所在文件、章节条号与风险等级，可导出报告。</li>
</ol>
<figure><img src="{img('06-conflict')}" alt="制度互斥"><figcaption><b>图</b> 制度互斥检测工作区</figcaption></figure>
<div class="callout warn"><div class="t">口径说明</div><p>系统给出的是「需要核实的条款冲突」，是否构成实质互斥、如何适用，须由审计人员结合制度时效与适用范围确认。</p></div>

<h2 id="s6"><span class="n">6</span>合同审计</h2>
<p class="sect-sub">检查合同文本问题与跨文件勾稽差异。</p>
<ol class="steps">
  <li><b>进入模块</b> 左侧导航点「合同审计」。</li>
  <li><b>上传合同与附件</b> 上传主合同及相关附件，支持多文件。</li>
  <li><b>发起审核</b> 说明关注点后发送，系统检查文本问题与跨文件数据一致性。</li>
  <li><b>查看报告</b> 报告分文本问题与勾稽差异两部分，给出定位与说明，可导出。</li>
</ol>
<figure><img src="{img('07-contract')}" alt="合同审计"><figcaption><b>图</b> 合同审计工作区</figcaption></figure>

<h2 id="s7"><span class="n">7</span>底稿审核</h2>
<p class="sect-sub">以审计实施方案为上位文件，逐项核对底稿是否支撑结论。</p>
<ol class="steps">
  <li><b>先传上位文件</b> 上传审计实施方案（文件名建议含「审计实施方案」字样）。</li>
  <li><b>再传底稿</b> 上传审计工作底稿，文件名建议含「审计工作底稿」字样。<span class="see">系统按文件名与正文自动识别角色，无需规定上传顺序。</span></li>
  <li><b>可对话指定</b> 若识别不准，在消息里写明「XX 是上位文件」即可覆盖自动识别，支持简称。</li>
  <li><b>查看报告</b> 报告按审计事项逐项给出通过 / 不通过 / 无法判断及原因，并汇总总体结论。</li>
</ol>
<figure><img src="{img('08-workpaper')}" alt="底稿审核"><figcaption><b>图</b> 底稿审核工作区</figcaption></figure>
<div class="callout ok"><div class="t">角色识别规则</div><p>文件名含「审计实施方案/审计方案/审计通知书」识别为上位文件，含「审计工作底稿」识别为底稿；其余交模型判别。上传区有轻提示引导。</p></div>

<h2 id="s8"><span class="n">8</span>资料库</h2>
<p class="sect-sub">集中管理已入库的资料，查看目录与原文。</p>
<p>资料库展示已导入的文件与其分类目录。可以按目录树浏览、点开资料查看解析后的原文与切片；分类可调整。资料只能从「导入资料」入口入库，助手上传的文件不会进入资料库。</p>
<figure><img src="{img('03-library')}" alt="资料库"><figcaption><b>图</b> 资料库：目录树与资料列表</figcaption></figure>

<h2 id="s9"><span class="n">9</span>导入资料</h2>
<p class="sect-sub">把文件解析、分类、切片后入库，建立检索能力。</p>
<ol class="steps">
  <li><b>进入导入</b> 左侧导航点「导入资料」（管理员可见）。</li>
  <li><b>选择文件</b> 支持 PDF、Word、PPT、Excel、Markdown、文本、图片，可多选、批量导入。</li>
  <li><b>等待解析</b> 系统自动解析文本、OCR 识别扫描件、分类并切片，建立向量索引。</li>
  <li><b>查看进度</b> 导入任务显示处理进度与结果，完成后即可在问答中检索到。</li>
</ol>
<figure><img src="{img('05-import')}" alt="导入资料"><figcaption><b>图</b> 导入资料页面</figcaption></figure>

<h2 id="s10"><span class="n">10</span>历史问题库</h2>
<p class="sect-sub">沉淀历史审计问题定性，供问答与判定参考。</p>
<p>历史问题库记录过往审计发现的问题，含年度、类别、表现形式、问题描述、法规依据、整改措施等字段。审计助手回答相关问题时，会参考这里的内容。</p>
<figure><img src="{img('04-issues')}" alt="历史问题库"><figcaption><b>图</b> 历史问题库列表</figcaption></figure>

<h2 id="s11"><span class="n">11</span>用户管理</h2>
<p class="sect-sub">管理员维护账号与角色。</p>
<ol class="steps">
  <li><b>进入管理</b> 管理员登录后在左侧导航点「用户管理」。</li>
  <li><b>新增或编辑</b> 可新增用户、编辑姓名与角色（管理员 / 普通用户）、停用账号。</li>
  <li><b>重置密码</b> 可为忘记密码的用户重置密码。</li>
</ol>
<figure><img src="{img('09-users')}" alt="用户管理"><figcaption><b>图</b> 用户管理页面</figcaption></figure>

<h2 id="s12"><span class="n">12</span>反馈监控</h2>
<p class="sect-sub">管理员查看用户提交的建议并跟进处理。</p>
<p>用户在回答下方点「提建议」提交文字说明，记录会带上提交账号、会话 ID、对话 ID、问题与回答。管理员在监控台可按时间范围、模块、状态筛选，点「查看」看完整详情，并标记为已处理或已忽略。</p>
<figure><img src="{img('10-feedback')}" alt="反馈监控"><figcaption><b>图</b> 反馈监控台：筛选与建议列表</figcaption></figure>

<h2 id="a1"><span class="n">A</span>常见问题</h2>
<h3>问答答案没有引用怎么办？</h3>
<p>说明知识库中相关资料不足。可先到「导入资料」把对应制度入库，再重新提问。</p>
<h3>底稿审核把方案认成了底稿？</h3>
<p>在消息里直接写明「XX 是上位文件」即可覆盖自动识别；长期建议把上位文件名带上「审计实施方案」字样。</p>
<h3>上传的文件会进资料库吗？</h3>
<p>不会。助手与各审计模块上传的文件进入隔离临时区，只有「导入资料」入口的文件才入库。</p>
<h3>报告里的结论能直接作为审计结论吗？</h3>
<p>不能。所有输出都是文本层面的提示，须由审计人员结合证据复核后确定。</p>

<h2 id="a2"><span class="n">B</span>术语表</h2>
<table>
  <tr><th style="width:140px">术语</th><th>说明</th></tr>
  <tr><td>上位文件</td><td>本次审核的依据文件，如审计实施方案、审计通知书。</td></tr>
  <tr><td>底稿</td><td>审计工作底稿，记录审计程序、事实与结论的工作文件。</td></tr>
  <tr><td>切片</td><td>文档解析后按语义切分的小段文本，用于检索。</td></tr>
  <tr><td>向量检索</td><td>按语义相似度检索切片，比关键词匹配更能找到相关表述。</td></tr>
  <tr><td>勾稽</td><td>核对同一数据在不同文件、表格之间是否一致。</td></tr>
  <tr><td>互斥条款</td><td>两份制度中对同一事项规定不一致、需要确认适用口径的条款。</td></tr>
</table>
</main>
""" + """
<script>
const links = [...document.querySelectorAll('nav.toc a')]
const obs = new IntersectionObserver(es => { es.forEach(e => { if(e.isIntersecting){ links.forEach(a=>a.classList.remove('on')); const a=links.find(x=>x.getAttribute('href')==='#'+e.target.id); if(a)a.classList.add('on') } }) }, {rootMargin:'-10% 0px -80% 0px'})
document.querySelectorAll('h2[id]').forEach(h=>obs.observe(h))
</script>
"""

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>审计工作平台 · 用户操作手册</title>
<style>{CSS}</style>
</head>
<body>
<div class="layout">
{TOC}
{BODY}
</div>
</body>
</html>
"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(html, encoding='utf-8')
print(f"生成: {OUT}")
print(f"大小: {OUT.stat().st_size / 1024 / 1024:.2f} MB")
