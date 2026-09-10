"""把缺陷 6~10 写进当日归档。单次读改写，锚点唯一性断言。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "docs/archive/novel-m2-m4-2026-09-10.md"

ANCHOR = "## 仍开放\n"

SECTION = """## 下午续：director_v2 首次真实成章攻坚（缺陷 6~10）

修复缺陷 4/5（事务自锁）后，章第一次跑过了 DIRECT，随即暴露出一串**内容层**卡点。
每一步都用「真机跑一章 → 读证据 → 最小修复 → 单测 + 反证 → 部署 → 再跑」推进，
产物在 `deploy/novel/artifacts/ch1_run{6..15}.txt`。进展可查：

| 运行 | 卡在 | 错误 |
|---|---|---|
| run6 | DIRECT | `DIRECTOR_PRODUCTION_STOPPED: 场景包含重复或未定义的角色` |
| run7 | PRODUCE | `导演判断缺少可核对的原文证据` |
| run9 | PRODUCE | 同上（加了点名后看清：导演在引用 rule_issues / 加说话人前缀） |
| run10 | PRODUCE | 同上（再看清：事件被重复累加 3 组） |
| run11 | PRODUCE | `命令被 Runtime 拒绝 [CONTINUE_SCENE]: 节拍数已达上限` |
| run13 | PRODUCE | `命令被 Runtime 拒绝 [ACCEPT_SCENE]: 存在硬失败` |
| run15 | PRODUCE | 同上，但此时引文类问题已全清，硬失败是**真的** |

### 缺陷 6：角色表被当成封闭集合（已修复）

- **证据**：最近两次 plan 调用，导演写出的角色名是 `陈渡（记忆观察者）`、
  `陈远舟（记忆中的父亲）`、`陌生人（老周）`——它把「名字 + 本场作用」写进了
  `persona`，而 `persona` 是 cast 的键。原字段是个**没有 description 的裸 `str`**。
- **用户纠偏（关键）**：「不应该用角色图谱把导演能力限制死，可以基于导演的诉求
  创建新的角色。」——角色表是**起点不是牢笼**。
- **修复（三处）**：
  1. `ActorDirection.persona` 加 description + 新增 `role` 字段，给「本场作用」一个
     正当去处（模型想表达的东西要有地方写，堵不如疏）；
  2. `_canonical_persona` 只做**确定性**归一（原样是键则原样保留；去掉末尾整段括号
     后正好是键则归一）。**不做模糊匹配**——角色名是身份键，猜错不可逆；
  3. 导演可在 `new_personas` 里声明新人物（声纹必填），由 `_register_new_personas`
     **落库**。下游拿人物名当身份用（信息隔离、声纹、正典），只躺在 JSON 里的名字
     会在更后面炸，而且是查不出原因的炸。声明了没出场的不入库（角色表会被带进
     后续每一章的上下文）。
- **注意**：提示词里**早就写了**「只使用给定角色」，模型照样加括号——所以真正的
  修正在字段层，不在提示词层。

### 缺陷 7：evidence 被要求字节级逐字相同（已修复）

- **证据**：8 条 evidence 里 7 条逐字命中，第 8 条把原文的「**他**将开表器握在手中」
  写成「**陈默**将开表器握在手中」——把代词还原成人名——整章判死。
- **修复**：量**覆盖度**而非同一性（最长连续重合 ≥ max(12 字, 引文 60%)，以引文
  长度为上限）。凭空捏造凑不出这么长的逐字重合，反捏造保证在实质上仍成立。
  这与角色名归一是**两回事**：引文只是审计用的出处，近义改写无害；风险不同，
  规则就该不同。
- **配套**：可引用文本补上 `rule_issues`（提示词要求「有 rule_issues 必须重演」，
  那规则提示本身就必须可引用，否则导演只能复述它、再被判捏造）。
- **配套**：给一次**带反馈的自修**（`_grounded_judgment`）。因为规划用
  temperature=0，入参不变的盲重试会以近乎确定的方式产出同一个坏名字——自修必须改
  入参。判据一次没放松，只是给模型看见错误的机会。

### 缺陷 8：事件被重复累加（已修复，根因级）

结算请求会把已有事件一并给模型看，模型原样回吐，`extend` 就重复累加：6 条事件跑完
3 个节拍变成 **22 条、三组完全相同**。重复事件喂出重复正文、触发规则冲突、迫使
导演重演——**整串级联故障的根因只是一句 `extend`**。`_extend_events` 按陈述去重。

### 缺陷 9：命令被 Runtime 拒绝即判死（部分修复 + 一处**故意不修**）

- 节拍用尽时导演仍选 CONTINUE → 收束为 RENDER（唯一还能执行的路径，留痕入库）。
  这是**可判定**的非法，不必再问模型一次。
- **故意不修**：存在硬失败时导演仍选 ACCEPT。**不**收束成 REWRITE。既有测试
  `test_director_cannot_override_failed_independent_validation` 编码了一条安全属性：
  独立核验没过的东西，导演不能绕过去。把它悄悄改成 REWRITE 等于替产品做了决定。
  已用 `test_accept_is_never_coerced_even_on_hard_failure` 钉住，防后来者"顺手修好"。

### 缺陷 10：核验阶段的字节级引文校验（已修复）

`fact.quote not in content` / `change.quote not in content` 两处同样是字节级。真机
5 条「事实证据不在正文中」全部来自 `A……B` 这种**省略号跳读**引用，模型自评
`passed=True`，是被比对改判成硬失败。`_is_grounded`：允许跳读，但**每一段留下的
片段都要按同一套覆盖度判据有出处**——省略号是把真实片段接起来，不是改写许可证。

### 当前状态与待决策（run15）

引文类问题**已全清**（7 条 fact 全部 OK）。剩下的硬失败是真的：

- 结算产生 6 项状态变化，核验模型只回报 3 项（`陈默的呼吸状态`、`陈默的眼神`、
  `表盘状态` 缺）→ **正文确实没把这 3 项写出来** → 硬失败 → 导演仍选 ACCEPT →
  Runtime 拒绝 → 整章 TERMINAL_FAILED。

三个方向，**须由人拍板**（不是实现细节）：

1. **允许有限次的命令自修**：被拒时把原因回传，再问一次；坚持 ACCEPT 才判死。
   需调整 WATCH_PROSE 的决策落库顺序与 command_id 生成，是一次结构性改动。
2. **维持现状**：正文漏写已结算状态时该章直接失败。质量更高，成章率更低。
3. **放宽状态比对**：不要求 key/value 全等，改为「已结算状态被正文覆盖的比例 ≥ X」。
   会削弱「正文与结算一致」这条保证。

### 纪律备注

- 本轮**反复出现同一类病**：把「模型输出的格式漂移」当成「内容违规」一击致命。
  已连续修 5 处，每处单独看都成立。判断标准应当是：**这个拒绝是"可判定的非法"
  （可确定性替换/归一）还是"要模型改主意"（需带反馈自修）**——后者不能靠放宽判据。
- 每处修复都配了单测 + 反证（`mutate_check_d6/d7/d8/d9.py`，共 26 个突变全部被抓）。
- `tests/unit/infrastructure/test_workspace_writer.py::test_symlink_in_base_is_rejected_when_supported`
  在本机（Windows）失败，与本次改动无关；`test_eval_chain_entry.py` 单独跑通过，
  合并跑时会被 safe-delete 守卫中止（临时目录超 50 个文件），同样是环境问题。

"""

EDITS = [(ANCHOR, ANCHOR + SECTION)]


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    for old, new in EDITS:
        count = text.count(old)
        if count != 1:
            print(f"[FAIL] 锚点命中 {count} 次（期望 1）")
            return 1
        text = text.replace(old, new, 1)
    TARGET.write_text(text, encoding="utf-8")
    print(f"[OK] 归档已追加（{len(SECTION.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
