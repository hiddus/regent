"""M2 浏览器旅程自动化驱动（agent-browser batch 单会话全旅程）。

关键约束（实测）：
- agent-browser 的会话不跨进程保持：每个 CLI 进程结束页面即关。
  因此一个旅程的全部命令必须串在**一次 batch** 里（stdin JSON）。
- batch 以 `close` 收尾才能干净退出；Popen 流式写日志，超时被杀不丢中间结果。
- 本页禁用 `snapshot`（无障碍树挂起），取证一律 eval + screenshot。

页内自动驾驶：澄清是对话式追问（点选项 chip → 下一题），注入页面的 JS 循环
自动作答、点方向卡确认按钮、把全过程记入 window.__m2trace；刷新后需重注入。

用法：
    python deploy/novel/m2_journeys.py j1a      # 创建→澄清→方向卡→确认→开跑→刷新→断网恢复
    python deploy/novel/m2_journeys.py j1b <id> # 新会话打开 /w/{id}：跑到成章→阅读→纠错入口
    python deploy/novel/m2_journeys.py a11y     # 无障碍检查
产物：deploy/novel/artifacts/

为什么拆两段：agent-browser 的会话不跨进程。第二段用**全新浏览器**打开
`/w/{id}`，顺带把「刷新/重进恢复」这条路径做成真实用例，而不是同会话内的假刷新。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(BASE, "artifacts")
APP = "http://118.31.171.159:8000/"
os.makedirs(ART, exist_ok=True)

IDEA = "雨夜，老宅的钥匙在三个人手里辗转，谁在说谎"

# ---------------------------------------------------------------------------
# 页内自动驾驶（一次 eval 注入；reload 后需重注入）
# ---------------------------------------------------------------------------
JS_AUTOPILOT = r"""
(() => {
  if (window.__m2running) return 'already-running';
  window.__m2trace = ['inject'];
  window.__m2running = true;
  const seen = new Set();
  window.__m2t0 = Date.now();
  const log = (m) => {
    const line = 't' + Math.round((Date.now() - window.__m2t0)/1000) + ' ' + m;
    if (window.__m2trace[window.__m2trace.length-1] === line) return;
    window.__m2trace.push(line);
  };
  const NAV = /^(书架|创作|目录|我的)$/;
  const chip = () => {
    const els = [...document.querySelectorAll('button,[class*=chip],[class*=option],[role=button]')]
      .filter(e => e.childElementCount === 0 && !NAV.test(e.textContent.trim())
                   && e.textContent.trim().length >= 8 && !seen.has(e.textContent.trim())
                   && !e.disabled);
    if (!els.length) return null;
    const el = els[0];
    const label = el.textContent.trim().slice(0, 26);
    el.click(); seen.add(el.textContent.trim());
    return label;
  };
  const goalBtn = () => {
    const b = [...document.querySelectorAll('button')].find(x => !x.disabled &&
      /开始生成|生成第一章|开始第一章|开始创作|采用这个方向|确认并开始|开始写|进入创作|继续生成|生成正文|生成故事方向|就写这个方向/.test(x.textContent));
    return b || null;
  };
  const send = async (text) => {
    const el = [...document.querySelectorAll('textarea,input')]
      .find(e => e.placeholder && (e.placeholder.includes('描述') || e.placeholder.includes('想法')));
    if (!el || el.value) return false;
    const d = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value');
    d.set.call(el, text);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    await new Promise(r => setTimeout(r, 400));
    for (const t of ['keydown','keypress','keyup'])
      el.dispatchEvent(new KeyboardEvent(t, {key:'Enter', keyCode:13, which:13, bubbles:true}));
    return true;
  };
  let idle = 0;
  let sent = 0;
  const timer = setInterval(async () => {
    try {
      const body = document.body ? document.body.innerText.replace(/\s+/g,' ') : '';
      log('page: ' + body.slice(0, 60));
      const c = chip();
      if (c) { log('chip: ' + c); idle = 0; return; }
      const g = goalBtn();
      if (g) { const t = g.textContent.trim().slice(0,24); if (!seen.has('G'+t)) { g.click(); seen.add('G'+t); log('GOAL-CLICK: ' + t); return; } }
      idle++;
      // 只在长时间真的没动静时兜底发言，且最多两次：
      // 每次发言都会产生一条 fact.reported，灌太多会把状态搅浑。
      if (idle >= 6 && idle % 6 === 0 && sent < 2) {
        const ok = await send('采用默认方向继续，尽快确认方向并开始第一章');
        if (ok) sent++;
        log(ok ? 'sent-default#' + sent : 'no-input');
      }
      if (idle > 40) { log('give-up'); clearInterval(timer); window.__m2running = false; }
    } catch (e) { window.__m2trace.push('ERR ' + (e && e.message || e)); }
  }, 7000);
  return 'autopilot-started';
})()
"""
JS_TRACE = "(() => JSON.stringify({url: location.href, running: !!window.__m2running, online: navigator.onLine, trace: (window.__m2trace||[]).slice(-6), body: (document.body? document.body.innerText.replace(/\\s+/g,' ').slice(0,150) : 'nobody')}))()"

JS_FILL = (
    "(() => { const el = document.querySelector('textarea') || document.querySelector('input');"
    " if(!el) return 'no-input';"
    " const d = Object.getOwnPropertyDescriptor(el.constructor.prototype,'value');"
    " d.set.call(el, %s); el.dispatchEvent(new Event('input',{bubbles:true}));"
    " return 'filled:' + el.value.slice(0,24); })()"
)
JS_CLICK_TEXT = (
    "(() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes(%s));"
    " if(!b) return 'no-btn'; if(b.disabled) return 'btn-disabled'; b.click(); return 'clicked'; })()"
)
# 纠错入口探测：入口不只长在按钮上——创作页是「指导 Agent：调整方向、纠错、续写」
# 这样的输入框占位文案，只扫 button 会漏报（第一版就漏了）。
JS_FIND_CORRECTION = (
    "(() => { const btns=[...new Set([...document.querySelectorAll('button,a,[role=button],"
    "[class*=correct],[class*=error]')].map(e=>e.textContent.trim())"
    ".filter(t=>t && /纠错|报错|指正|问题反馈|反馈|重演|重写|改写/.test(t)))];"
    " const placeholders=[...new Set([...document.querySelectorAll('textarea,input')]"
    ".map(e=>e.placeholder||'').filter(t=>t && /纠错|指正|重演|改写|指导|调整方向/.test(t)))];"
    " return JSON.stringify({buttons: btns.slice(0,10), placeholders: placeholders.slice(0,6)}); })()"
)
# 阅读路径：先看入口，再点进去，最后量正文长度——只探测入口不算验证过阅读。
JS_READ_PROBE = (
    "(() => { const btns=[...new Set([...document.querySelectorAll('button,a,[role=button]')]"
    ".map(e=>e.textContent.trim()).filter(t=>t && /目录|章节|阅读|全文|下一章|上一章|回到正文/.test(t)))];"
    " const el=document.querySelector('[class*=reader],[class*=chapter-content],[class*=prose],article');"
    " const txt=(el && el.innerText) ? el.innerText.replace(/\\s+/g,' ') : '';"
    " return JSON.stringify({url: location.pathname, btns: btns.slice(0,12), textLen: txt.length,"
    " head: txt.slice(0,140)}); })()"
)
JS_OPEN_CATALOG = (
    "(() => { const b=[...document.querySelectorAll('button,a,[role=button]')]"
    ".find(x=>!x.disabled && /目录|章节列表|查看全部|卷目录/.test(x.textContent));"
    " if(!b) return 'no-catalog'; b.click(); return 'catalog-open:'+b.textContent.trim().slice(0,10); })()"
)
JS_OPEN_FIRST_CHAPTER = (
    "(() => { const rows=[...document.querySelectorAll('button,a,[role=button],li,[class*=chapter]')]"
    ".filter(x=>/第\\s*\\d+\\s*章/.test(x.textContent) && x.textContent.trim().length < 80);"
    " if(!rows.length) return 'no-chapter-row';"
    " rows[0].click(); return 'chapter-clicked:'+rows[0].textContent.trim().slice(0,24); })()"
)


def fill(text: str) -> list:
    return ["eval", JS_FILL % json.dumps(text, ensure_ascii=False)]


def click_text(t: str) -> list:
    return ["eval", JS_CLICK_TEXT % json.dumps(t, ensure_ascii=False)]


def shot(name: str) -> list:
    return ["screenshot", os.path.join(ART, name)]


def poll(tag: str, label: str = "") -> list:
    # 注意：必须是两条独立命令；合成一条会变成 eval 语法错误
    name = f"m2_{label}_{tag}.png" if label else f"m2_{tag}.png"
    return [["eval", JS_TRACE], ["screenshot", os.path.join(ART, name)]]


def check_cmds(cmds: list, label: str) -> None:
    """batch 的入参是「字符串数组的数组」。嵌套错一层会被当成单条命令，
    表现为 eval 语法错误或静默跑偏——所以发出去之前先断言结构。"""
    if not isinstance(cmds, list) or not cmds:
        raise SystemExit(f"[{label}] 命令列表为空")
    for i, c in enumerate(cmds):
        if not isinstance(c, list) or not c:
            raise SystemExit(f"[{label}] 第 {i} 条不是非空数组：{c!r}")
        for part in c:
            if not isinstance(part, str):
                raise SystemExit(f"[{label}] 第 {i} 条含非字符串参数：{c!r}")


def run_batch(cmds: list[list], label: str, timeout: int = 1200) -> str:
    """执行一次 batch；输出流式写文件，超时/结束后返回日志全文。"""
    check_cmds(cmds, label)
    log_path = os.path.join(ART, f"m2_{label}.log")
    cmd = "agent-browser batch --json"
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(f"### label={label} started={time.strftime('%H:%M:%S')}\n")
        log.flush()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", shell=True)
        try:
            proc.communicate(input=json.dumps(cmds), timeout=timeout)
            status = f"exited rc={proc.returncode}"
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            status = "TIMEOUT-KILLED"
        log.write(f"\n### {status} at {time.strftime('%H:%M:%S')}\n")
    with open(log_path, "r", encoding="utf-8") as fh:
        return fh.read()


def wait_secs(n: int) -> list:
    # CLI 对单条命令有 ~25s 读超时：wait 一律 ≤15s，长等用多段拼
    return ["wait", str(min(n, 15) * 1000)]


def cycle(n: int, start: int, secs: int = 15, label: str = "") -> list:
    """n 组「等 secs → 看一眼」；tag 从 start 编号。"""
    out: list = []
    for i in range(n):
        out.append(wait_secs(secs))
        out.extend(poll(f"s{start + i:02d}", label))
    return out


# ---------------------------------------------------------------------------
# J1a：创建 → 澄清（自动作答）→ 方向卡 → 确认（开跑）→ 刷新恢复 → 断网恢复
# ---------------------------------------------------------------------------
def extract_work_id(log: str) -> str | None:
    import re

    hits = re.findall(r"/w/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", log)
    return hits[-1] if hits else None


def j1a_cmds() -> list[list]:
    return [
        ["open", APP],
        wait_secs(3),
        fill(IDEA),
        wait_secs(1),
        click_text("开始构思"),
        wait_secs(15),
        ["eval", JS_AUTOPILOT],
        *cycle(8, 1, label="j1a"),          # ~2 分钟：澄清作答 + 方向卡出现
        *poll("cards", "j1a"),              # 取证：方向卡已出现
        # 确认方向这一步背后是**一次 1–2 分钟的 LLM 大纲生成**：
        # 窗口太短会在 POST /directions 在途时关掉浏览器，章运行就永远开不出来。
        *cycle(10, 10, label="j1a"),        # ~2.5 分钟：确认 → 大纲 → 运行创建
        *poll("run", "j1a"),
        ["reload"],                         # J2 刷新恢复：重载后状态须还原
        wait_secs(6),
        ["eval", JS_AUTOPILOT],
        *poll("reload", "j1a"),
        *cycle(3, 21, label="j1a"),
        ["set", "offline", "on"],           # J3 断网 15s
        wait_secs(15),
        *poll("offline", "j1a"),
        ["set", "offline", "off"],          # 恢复在线
        wait_secs(15),
        *poll("backonline", "j1a"),
        ["eval", JS_TRACE],
        ["eval", JS_FIND_CORRECTION],       # J4 纠错入口探测
        shot("m2_j1a_final.png"),
        ["close"],
    ]


def j1a() -> int:
    out = run_batch(j1a_cmds(), "j1a", timeout=1500)
    print(out[-2500:])
    wid = extract_work_id(out)
    print("\n=== WORK_ID:", wid, "===")
    if not wid:
        print("[WARN] 未从日志中取到 work_id：旅程可能没走到确认方向那一步")
        return 1
    return 0


# ---------------------------------------------------------------------------
# J1b：全新浏览器会话打开 /w/{id} → 跑到成章 → 阅读 → 纠错入口
# ---------------------------------------------------------------------------
def j1b_cmds(work_id: str) -> list[list]:
    return [
        ["open", f"{APP}w/{work_id}"],
        wait_secs(6),
        *poll("reopen", "j1b"),             # 恢复路径取证：不得是「实时生成中」假象
        ["eval", JS_AUTOPILOT],             # 若停在「等待开工」则自动点「开始第一章」
        # 真实一章要 14 次模型调用、4–17 分钟（见 M4 逐章数据），窗口必须给足
        *cycle(56, 1, secs=15, label="j1b"),  # ~14 分钟：ASSEMBLE→…→CANON
        ["eval", JS_TRACE],
        ["eval", JS_READ_PROBE],            # 阅读入口探测
        ["eval", JS_OPEN_CATALOG],          # 打开目录
        wait_secs(2),
        ["eval", JS_OPEN_FIRST_CHAPTER],    # 点开第一章
        wait_secs(3),
        ["eval", JS_READ_PROBE],            # 量正文长度：证明真的读到了
        ["eval", JS_FIND_CORRECTION],       # J4 纠错入口（含输入框占位文案）
        shot("m2_j1b_end.png"),
        ["close"],
    ]


def j1b(work_id: str) -> int:
    out = run_batch(j1b_cmds(work_id), "j1b", timeout=2100)
    print(out[-4000:])
    return 0


# ---------------------------------------------------------------------------
# 无障碍：axe-core 从 CDN 注入；取不到就退化为人工规则探针（不假装测过）
# ---------------------------------------------------------------------------
JS_AXE_LOAD = (
    "fetch('https://cdn.jsdelivr.net/npm/axe-core@4.10.2/axe.min.js')"
    ".then(r=>{ if(!r.ok) throw new Error('http '+r.status); return r.text(); })"
    ".then(t=>{ (new Function(t))(); window.__axeReady=true; return 'axe-loaded:'+t.length; })"
    ".catch(e=>'axe-load-failed:'+e.message)"
)
JS_AXE_RUN = (
    "(() => { if(!window.axe) return JSON.stringify({error:'axe-missing'});"
    " return window.axe.run(document, {resultTypes:['violations']})"
    " .then(r=>JSON.stringify({violations: r.violations.map(v=>({id:v.id,impact:v.impact,n:v.nodes.length,"
    " help:v.help, sample:(v.nodes[0]&&v.nodes[0].html||'').slice(0,120)}))}))"
    " .catch(e=>JSON.stringify({error:String(e)})); })()"
)
# 退化探针：无 axe 时至少验证四条硬规则（可键盘聚焦、图片有替代文本、按钮有可读名、表单有标签）
JS_A11Y_FALLBACK = (
    "(() => { const imgs=[...document.querySelectorAll('img')];"
    " const btns=[...document.querySelectorAll('button')];"
    " const inputs=[...document.querySelectorAll('input,textarea')];"
    " let focusable=0; for(const e of document.querySelectorAll('button,a,input,textarea,[tabindex]'))"
    "   if(e.tabIndex>=0 && !e.disabled) focusable++;"
    " return JSON.stringify({mode:'fallback',"
    "  imgsNoAlt: imgs.filter(i=>!i.alt).length,"
    "  btnsNoName: btns.filter(b=>!b.textContent.trim() && !b.getAttribute('aria-label')).length,"
    "  inputsNoLabel: inputs.filter(i=>!i.getAttribute('aria-label') && !i.placeholder && !i.id).length,"
    "  focusable, landmarks: [...document.querySelectorAll('main,nav,header,footer,[role=main],[role=navigation]')].length}); })()"
)


def a11y_cmds() -> list[list]:
    return [
        ["open", APP],
        wait_secs(5),
        ["eval", JS_A11Y_FALLBACK],
        ["eval", JS_AXE_LOAD],
        wait_secs(3),
        ["eval", JS_AXE_RUN],
        wait_secs(2),
        ["eval", JS_AXE_RUN],
        shot("m2_a11y_home.png"),
        ["close"],
    ]


def a11y() -> int:
    out = run_batch(a11y_cmds(), "a11y", timeout=300)
    print(out[-4000:])
    return 0


def dry() -> int:
    """只校验结构、不真的开浏览器：`python m2_journeys.py dry`。"""
    sample = "00000000-0000-0000-0000-000000000000"
    total = 0
    for label, cmds in (
        ("j1a", j1a_cmds()),
        ("j1b", j1b_cmds(sample)),
        ("a11y", a11y_cmds()),
    ):
        check_cmds(cmds, label)
        print(f"[dry] {label}: {len(cmds)} 条命令")
        total += len(cmds)
    print(f"[dry] 结构校验通过（共 {total} 条，未开浏览器）")
    return 0
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "j1a"
    table = {
        "j1a": j1a,
        "j1b": lambda: j1b(sys.argv[2]),
        "a11y": a11y,
        "dry": dry,
    }
    if mode not in table:
        raise SystemExit(f"unknown mode: {mode} (可选 {', '.join(table)})")
    raise SystemExit(table[mode]())
