#!/usr/bin/env python3
"""check_docs.py <repo-root> — harness 唯一的水位／死指標檢查（pre-commit 掛載）。

**這是 plugin 出貨的通用版**。裝進 repo 時複製成該 repo 的 `scripts/check_docs.py`
（原則 5：per-repo 套用，不 symlink），之後各 repo 自己調上限、互不影響。
單檔硬上限 285 行（P6 加第 7 類後實測 284）。**不拆檔**：安裝方式是
`cp tools/check_docs.py scripts/` 單檔複製（`commands/cc-harness.md` 與
`templates/hooks/pre-commit` 兩處都寫死單檔），拆了要同時改那兩處。只降不升。

七類判定（缺檔一律跳過，不當紅——不是每個 repo 都有每一件）：
  1. BACKLOG 流量：條數 ≤20、一行 ≤120 字（不變量是流量不是尺寸，滿載進一出一）。
  2. 字元水位：AGENTS.md／BACKLOG.md／SPEC.md 各有上限，單位是「字元數」不是 byte
     （中日文 byte 高估約 36%）。handovers/<slug>.md 不設上限：一份天然就小。
  3. 規則檔預算：`.claude/rules/**` 總計，以及「動任何 src 檔都會載入」那一桶
     （frontmatter paths 含恰好 `src/**`）；殘留 `TODO(paths)` 直接紅——paths 沒填對
     ＝規則永遠不會載入，靜默容忍等於規則不存在。
  4. hook 死指標：settings.json 的 command 指到的腳本必須存在（hook 是隱形的，
     指到不存在的檔不報錯，只會靜默不執行）。`.claude/settings.json` 與
     `.claude-plugin/../hooks/hooks.json` 兩處都掃。
  5. git hook 已裝且與真身一致：.git/hooks/<name> 對 scripts/hooks/（或 templates/hooks/）
     底下的同名檔。不一致＝紅
     （「以為裝了其實沒裝」，實測發生過）；沒裝＝warning（CI 與新 clone 本來就沒裝）。
  6. plugin manifest 死指標：`.claude-plugin/plugin.json` 宣告的 commands／hooks 路徑
     必須存在。plugin 載入失敗一樣是靜默的，跟 hook 同一種病。
  7. 常駐載入預算：帳號 `~/.claude/CLAUDE.md` ＋ repo `AGENTS.md`（沒有才看 `CLAUDE.md`）
     ＋ `~/.claude/projects/<dir>/memory/MEMORY.md` 的合計。這是每個 session 無條件先付的
     字元數，公式與 `hooks/session-start.sh` 印的那行**同一份**（改一邊要同時改另一邊）。
     帳號 CLAUDE.md 不存在（CI、新 clone）＝整類跳過。

超標的正解是**把規則搬到使用點**（path-scoped 規則檔／腳本檔頭／BACKLOG.md 檔頭／
docs/round.md），其次才下沉 rounds.md／ICEBERG.md。**改寫措辭不是解**（2026-08-31 實測：
15,000 字的 AGENTS.md 只擠得出約 100 字）。**調高數字不是選項**——只降不升。

死法：連續 6 輪沒抓到東西，且改 harness 檔時被迫先改本檔 → 砍到只剩第 4、6 類（死指標）。
第 7 類另有自己的死法：連續 6 輪沒紅，且改任何一個常駐檔都要先算它 → 改成只印不擋。
"""
import json
import os
import re
import sys

# 字元上限。AGENTS.md 4,000 是常駐層重構後的值。**只降不升。**
CHAR_BUDGETS = {"AGENTS.md": 4_000, "BACKLOG.md": 4_000, "SPEC.md": 20_000}
RULES_BUDGETS = {"srcScoped": 6_000, "rulesTotal": 21_000}
# 常駐載入上限。**不是用中位數訂的**（樣本 5 個有 4 個來自沒有 AGENTS.md 的 repo，取樣偏了）。
# 用目標形態構造：帳號 CLAUDE.md 1,500 ＋ AGENTS.md 4,000（＝CHAR_BUDGETS 那條）＋ MEMORY.md 1,000。
# 三個數字互相自洽，改任一個要同時檢查另外兩個。**只降不升**——4,879 → 6,500 是取樣
# 修正不是放寬，不構成先例。推導與翻案條件見 docs/decisions.md。
RESIDENT_BUDGET = 6_500
BACKLOG_MAX_ITEMS = 20
BACKLOG_MAX_LINE = 120
ADVICE = ("→ 先問「不知道這條的人會在什麼時候踩到」，搬到那個使用點；"
          "沒有使用點才下沉到 docs/archive/rounds.md／ICEBERG.md。改寫措辭省不到 1%")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def frontmatter_paths(text):
    """取 frontmatter 的 paths 清單。單值／flow 清單／block 清單三種寫法都要取到**全部**項目。
    block 清單的第二項以後最容易被漏掉（lazy regex 在 `m` flag 下只回第一項），所以逐行解析。"""
    fm = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
    if not fm:
        return []
    out, in_block = [], False
    for line in fm.group(1).split("\n"):
        line = line.rstrip("\r")
        if not in_block:
            head = re.match(r"paths:[ \t]*(.*)$", line)
            if not head:
                continue
            inline = head.group(1).strip()
            if inline.startswith("["):
                return [s.strip().strip("\"'") for s in inline.strip("[]").split(",") if s.strip()]
            if inline:
                return [inline.strip("\"'")]
            in_block = True
            continue
        item = re.match(r"[ \t]+-[ \t]*(.+?)[ \t]*$", line)
        if item:
            out.append(item.group(1).strip("\"'"))
            continue
        if line.strip() == "":
            continue
        if re.match(r"\S", line):
            break
    return out


def walk_markdown(root):
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(".md"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def check_char_budgets(root, fails):
    for name, cap in CHAR_BUDGETS.items():
        p = os.path.join(root, name)
        if not os.path.exists(p):
            continue
        n = len(read(p))
        if n > cap:
            fails.append(f"{name}: {n:,} 字 > {cap:,}（{ADVICE}）")


def check_backlog(root, fails):
    """條數上限以 BACKLOG.md 檔頭自己寫的「上限 N 條」為準，檔頭沒寫才用預設。
    否則檔頭與本檔會變成兩份事實，且檔頭那份才是使用者會讀到的。"""
    bl = os.path.join(root, "BACKLOG.md")
    if not os.path.exists(bl):
        return
    text = read(bl)
    lines = text.splitlines()
    declared = re.search(r"上限\s*(\d+)\s*條", text)
    cap = int(declared.group(1)) if declared else BACKLOG_MAX_ITEMS
    # 兩種寫法都要算到：清單（`- x`／`1. x`）與表格列（`| 3 | … |`）。
    # 只認第一格是純數字的表格列，這樣表頭與分隔列（`|---|`）不會被當成項目。
    items = [(i + 1, l) for i, l in enumerate(lines)
             if re.match(r"\s*(-|\*|\d+\.)\s", l) or re.match(r"\s*\|\s*\d+\s*\|", l)]
    if len(items) > cap:
        src = "檔頭宣告" if declared else "本檔預設"
        fails.append(f"BACKLOG.md: {len(items)} 條 > {cap}（{src}；滿載進一出一）")
        print("逐出候選（最早的優先）：")
        for ln, t in items[: len(items) - cap + 3]:
            print(f"  L{ln}: {t[:80]}")
    for ln, t in items:
        if len(t) > BACKLOG_MAX_LINE:
            fails.append(f"BACKLOG.md L{ln}: {len(t)} 字 > {BACKLOG_MAX_LINE}（一項一行，細節進 ICEBERG）")


def check_rules_budget(root, fails):
    rules_dir = os.path.join(root, ".claude", "rules")
    total = src_total = 0
    src_names = []
    for abs_path in walk_markdown(rules_dir):
        text = read(abs_path)
        rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
        total += len(text)
        if "src/**" in frontmatter_paths(text):
            src_total += len(text)
            src_names.append(f"{rel} {len(text):,}")
        if "TODO(paths)" in text:
            fails.append(f"{rel} 仍留著 TODO(paths)——paths 沒填成本 repo 實際存在的原始碼目錄，這份規則永不載入。")
    if src_total > RULES_BUDGETS["srcScoped"]:
        fails.append(f"src/** scoped 合計 {src_total:,} > {RULES_BUDGETS['srcScoped']:,}（{'、'.join(src_names)}）（{ADVICE}）")
    if total > RULES_BUDGETS["rulesTotal"]:
        fails.append(f".claude/rules/** 總計 {total:,} 字 > {RULES_BUDGETS['rulesTotal']:,}（{ADVICE}）")
    return total, src_total


def hook_script_path(command):
    """從 hook command 撈出它要跑的腳本路徑（repo 相對）。
    支援 `$CLAUDE_PROJECT_DIR/...`、`${CLAUDE_PLUGIN_ROOT}/...`、裸 `scripts/...`。"""
    command = str(command or "")
    m = re.search(r"\$\{?(?:CLAUDE_PROJECT_DIR|CLAUDE_PLUGIN_ROOT)\}?[/\\]([^\"'\s]+)", command)
    if m:
        return m.group(1)
    m = re.search(r"(?:^|\s)((?:scripts|tools|hooks|\.claude)/[^\"'\s]+)", command)
    return m.group(1) if m else None


def check_hook_pointers(root, fails):
    count = 0
    for rel in (".claude/settings.json", "hooks/hooks.json"):
        p = os.path.join(root, rel)
        if not os.path.exists(p):
            continue
        try:
            data = json.loads(read(p))
        except ValueError as err:
            fails.append(f"{rel} 不是合法 JSON：{err}")
            continue
        for event, groups in (data.get("hooks") or {}).items():
            for group in groups or []:
                for hook in (group or {}).get("hooks") or []:
                    count += 1
                    script = hook_script_path(hook.get("command"))
                    if script is None:
                        print(f"⚠️  {rel} 的 {event} 撈不出腳本路徑，無法驗證：{hook.get('command')}")
                    elif not os.path.exists(os.path.join(root, script)):
                        fails.append(f"{rel} 的 {event} 指向不存在的腳本 {script}（hook 會靜默不執行）")
    return count


def check_installed_hooks(root, fails):
    """scripts/hooks/ 是真身、.git/hooks/ 是實體且不進版控——兩者會無聲分岔。
    .git 通常是目錄，worktree／submodule 的是一行 `gitdir: <path>` 的檔。"""
    dot = os.path.join(root, ".git")
    src = next((d for d in (os.path.join(root, "scripts", "hooks"),
                            os.path.join(root, "templates", "hooks"))
                if os.path.isdir(d)), None)
    if src is None:
        return 0
    if os.path.isdir(dot):
        dst = os.path.join(dot, "hooks")
    else:
        m = re.match(r"gitdir:\s*(.+)", read(dot).strip()) if os.path.isfile(dot) else None
        if not m:
            return 0
        base = m.group(1).strip()
        dst = os.path.join(base if os.path.isabs(base) else os.path.join(root, base), "hooks")
    names = [n for n in sorted(os.listdir(src)) if not n.startswith(".")]
    for name in names:
        target = os.path.join(dst, name)
        if not os.path.exists(target):
            print(f"⚠️  git hook {name} 未安裝 → 跑 install-hooks.sh")
        elif read(os.path.join(src, name)) != read(target):
            rel = os.path.relpath(src, root).replace(os.sep, "/")
            fails.append(f".git/hooks/{name} 與 {rel}/{name} 不一致——「以為裝了其實沒裝」（跑 install-hooks.sh）")
    return len(names)


def check_plugin_manifest(root, fails):
    """plugin.json 宣告的元件路徑不存在時，runtime 只會少載入那一類，不報錯。"""
    p = os.path.join(root, ".claude-plugin", "plugin.json")
    if not os.path.exists(p):
        return 0
    try:
        data = json.loads(read(p))
    except ValueError as err:
        fails.append(f".claude-plugin/plugin.json 不是合法 JSON：{err}")
        return 0
    checked = 0
    for key in ("commands", "agents", "skills", "hooks"):
        val = data.get(key)
        for item in ([val] if isinstance(val, str) else (val or [])):
            if not isinstance(item, str):
                continue
            checked += 1
            if not os.path.exists(os.path.join(root, item.lstrip("./"))):
                fails.append(f"plugin.json 的 {key} 指向不存在的路徑 {item}（該類元件會靜默不載入）")
    return checked


def check_resident_budget(root, fails):
    """第 7 類。讀 repo 外的兩個檔（帳號規則與 memory 索引）是刻意的：常駐成本本來就跨 repo，
    只算 repo 內那份會漏掉大頭。帳號 CLAUDE.md 不在就整類跳過，回 None。"""
    home = os.path.expanduser("~")
    account = os.path.join(home, ".claude", "CLAUDE.md")
    if not os.path.exists(account):
        print("⚠️  常駐載入未檢查（讀不到 ~/.claude/CLAUDE.md）")
        return None
    repo_name = "AGENTS.md" if os.path.exists(os.path.join(root, "AGENTS.md")) else "CLAUDE.md"
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(root))
    parts = [("帳號 CLAUDE.md", account),
             (f"repo {repo_name}", os.path.join(root, repo_name)),
             ("MEMORY.md", os.path.join(home, ".claude", "projects", slug, "memory", "MEMORY.md"))]
    sizes = [(label, len(read(p)) if os.path.exists(p) else 0) for label, p in parts]
    total = sum(n for _, n in sizes)
    if total > RESIDENT_BUDGET:
        detail = " ＋ ".join(f"{label} {n:,}" for label, n in sizes)
        fails.append(f"常駐載入 {total:,} 字 > {RESIDENT_BUDGET:,}（{detail}）（{ADVICE}）")
    return total


def main(root):
    fails = []
    check_char_budgets(root, fails)
    check_backlog(root, fails)
    rules_total, src_total = check_rules_budget(root, fails)
    hooks = check_hook_pointers(root, fails)
    githooks = check_installed_hooks(root, fails)
    plugin_paths = check_plugin_manifest(root, fails)
    resident = check_resident_budget(root, fails)
    if fails:
        print("CHECK_DOCS FAIL:")
        for x in fails:
            print(" -", x)
        print("\n本檔的上限是體系不變量，不是可調參數（只降不升）。超標一律搬到使用點或下沉。")
        return 1
    print(f"CHECK_DOCS OK（單位字元：.claude/rules/** {rules_total:,}/{RULES_BUDGETS['rulesTotal']:,}、"
          f"src/** scoped {src_total:,}/{RULES_BUDGETS['srcScoped']:,}、hook 指標 {hooks} 支、"
          f"git hook {githooks} 支與版控真身一致、plugin 元件路徑 {plugin_paths} 條全部存在、"
          f"常駐載入 {'跳過' if resident is None else f'{resident:,}/{RESIDENT_BUDGET:,}'}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
