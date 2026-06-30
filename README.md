# camoufox-versatile-mcp

[中文](README.md) | [English](README_en.md)

> 基于反指纹浏览器（Camoufox）的 MCP Server，支持 **通用 captcha 挑战自动通过** + **JSVMP 签名还原管线**。

Fork 自 [camoufox-reverse-mcp](https://github.com/WhiteNightShadow/camoufox-reverse-mcp)，新增：
- 内置 **captcha 通用检测 + 自动通过**（基于 `camoufox_captcha`）
- 全新 **JSVMP 离线复现管线**：WASM 反汇编 / Worker 源码捕获 / WS 流量抓取 / 离线重放 + 自动 diff

---

## 快速开始

### 安装

#### 方式一：AI 对话框直接安装（推荐）

在你常用的 AI 编码工具（Cursor / Claude Code / Windsurf / Coze 等）的对话框中输入：

```
帮我安装下这个 MCP 工具：camoufox-versatile-mcp
项目地址：https://github.com/usernamexiaozhang/camoufox-versatile-mcp
```

AI 会自动完成以下全部步骤：

1. `git clone` 或 `pip install` 克隆安装项目
2. 读取 `pyproject.toml` 确认依赖
3. 配置 Cursor 的 `mcpServers`（自动写入 `settings.json`）
4. 启动 MCP Server 并验证连接

> **为什么推荐这种方式？** AI 了解你的操作系统、Python 环境、Cursor 版本，它可以处理 Windows/macOS/Linux 的路径差异，自动选择合适的安装命令，并在安装完成后告诉你是否成功。

#### 方式二：手动安装

```bash
git clone https://github.com/usernamexiaozhang/camoufox-versatile-mcp
cd camoufox-versatile-mcp
pip install -e .
```

### 客户端配置（Cursor）

```json
{
  "mcpServers": {
    "camoufox-versatile": {
      "command": "python",
      "args": ["-m", "camoufox_versatile_mcp"]
    }
  }
}
```

### 启动参数

| 参数 | 说明 |
|------|------|
| `--headless` | 无头模式运行 |
| `--proxy` | 代理服务器，如 `--proxy http://127.0.0.1:7890` |
| `--geoip` | 启用 GeoIP 推理（根据代理 IP 自动设置地理位置） |
| `--humanize` | 启用拟人化鼠标移动 |
| `--os` | OS 指纹：`auto` / `windows` / `macos` / `linux` |
| `--locale` | 浏览器语言，如 `zh-CN` |

---

## 工具一览（共 45 个）

### 浏览器控制（11 个）
| 工具 | 说明 |
|------|------|
| `launch_browser` | 启动 Camoufox 反指纹浏览器（含 i_know_what_im_doing + disable_coop + forceScopeAccess 默认三件套） |
| `close_browser` | 关闭浏览器 |
| `navigate` | 导航到指定 URL（**支持 `auto_solve_challenge` 参数**） |
| `reload` | 刷新页面 |
| `take_screenshot` | 截图 |
| `take_snapshot` | 获取页面无障碍树 |
| `click` / `type_text` | 点击元素 / 输入文本 |
| `wait_for` | 等待元素出现或 URL 匹配 |
| `get_page_info` | 获取当前页面信息 |
| `scroll` | 滚动页面 |
| `go_back` / `go_forward` | 前进 / 后退 |

### 通用 Captcha 挑战自动通过（2 个）⭐
| 工具 | 说明 |
|------|------|
| `detect_captcha` | 检测当前页面是否有 captcha 挑战（不点击） |
| `auto_solve_captcha` | 自动检测 + 通过 captcha 挑战（`captcha_type` / `challenge_type`） |

> 支持 `cloudflare` 的 Turnstile 和 Interstitial 两种模式，`captcha_type` 参数预留扩展位（hcaptcha、recaptcha 等）。
>
> **注意**：非标准 Cloudflare 挑战（如自定义 WAF checkbox）需要额外定制化处理。

### JS 执行与调试（1 个）
| 工具 | 说明 |
|------|------|
| `evaluate_js` | 在页面上下文执行任意 JS 表达式 |

### 脚本分析（2 个）
| 工具 | 说明 |
|------|------|
| `scripts(action)` | 脚本管理：`list` / `get` / `save` |
| `search_code` | 搜索关键词 |

### Hook 与追踪（4 个）
| 工具 | 说明 |
|------|------|
| `hook_function` | Hook 或追踪函数 |
| `inject_hook_preset` | 注入预置 Hook（xhr / fetch / crypto / websocket / debugger_bypass / cookie / runtime_probe） |
| `remove_hooks` | 移除所有 Hook |
| `inject_hook` | 注入自定义 JS Hook |

### 网络分析（5 个）
| 工具 | 说明 |
|------|------|
| `network_capture(action)` | 网络捕获控制 |
| `list_network_requests` | 列出已捕获的请求 |
| `get_network_request` | 获取请求完整详情 |
| `get_request_initiator` | 获取请求发起的 JS 调用栈 |
| `intercept_request` | 拦截请求 |

### JSVMP 逆向分析（5 个）
| 工具 | 说明 |
|------|------|
| `hook_jsvmp_interpreter` | JSVMP 运行时探针 |
| `instrumentation(action)` | 源码级插桩 |
| `compare_env` | 浏览器环境指纹收集 |
| `detect_vmp` | 检测页面使用了哪种 VMP 保护（obfuscated / string_eval / vm_dispatch / wasm） |
| `trace_vmp_for_sign` | 签名安全 Tap：追踪 VMP 读取了哪些 fingerprint 属性 |

### Cookie 与存储（4 个）
| 工具 | 说明 |
|------|------|
| `cookies(action)` | Cookie 管理 |
| `get_storage` | 获取 localStorage / sessionStorage |
| `export_state` / `import_state` | 导出 / 导入浏览器状态 |

### 验证（1 个）
| 工具 | 说明 |
|------|------|
| `verify_fingerprint` | 验证指纹是否满足目标网站要求 |

### 环境感知（1 个）
| 工具 | 说明 |
|------|------|
| `get_env_info` | 获取当前浏览器环境信息（OS、UA、屏幕、语言等） |

### JSVMP 离线重放管线（8 个）🆕
| 工具 | 说明 |
|------|------|
| `get_tap_log` | 读取 trace_vmp_for_sign 记录的 Tap 日志 |
| `disassemble_wasm` | 将 WASM 字节码反汇编为 WAT + 导入/导出函数列表 + 入口函数猜测 |
| `capture_worker_js` | 劫持 `new Worker()`，捕获 blob: URL 源码（JSVMP 字节码的直接来源） |
| `capture_ws_payloads` | 劫持 `new WebSocket()`，记录所有 send/recv 流量 |
| `auto_suggest_missing_props` | 根据失败日志 + trace_log 推荐缺失的 fingerprint prop |
| `replay_vmp_offline` | 在 Node 沙箱中离线重放 VMP 代码（**增强版**：支持 `supplied_env` / `expected_sign` / `auto_diff`） |
| `trace_vmp_for_sign` | 签名安全 Tap：追踪 VMP 读取了哪些 fingerprint 属性 |

---

### JS 引擎 Trace + HTTP 包 Trace（3 个）🆕
|| 工具 | 说明 |
||------|------|
|| `js_engine_trace` | JS 引擎 tracing（三层独立方案，零编译，零 patch） |
|| `http_packet_trace` | HTTP 包 trace（捕获完整请求/响应 + 发起者 JS 调用栈） |
|| `trace_js_and_http` | 组合模式：同时开启 JS trace + HTTP trace，持续 N 秒后自动停止 |

> 基于 RuyiTrace 的 HttpPacketTrace 思路，用 Playwright 路由拦截实现，无需 RuyiTrace 的 C++ 插桩。


## Captcha 挑战自动通过使用示例

### 方式一：导航时自动过挑战（推荐）

```python
navigate(
    url="https://target-site-with-captcha.com/",
    auto_solve_challenge=True,        # 开启自动过挑战
    challenge_type="auto",             # auto 检测 turnstile/interstitial
    challenge_ready_delay=5.0,         # 等待 iframe 加载
    expected_content_selector="#main"  # 成功后等待主内容出现
)
```

### 方式二：手动调用工具

```python
# 1. 先检测
result = detect_captcha()
# 返回: {"url": "...", "detected": {"turnstile": True, "interstitial": False}, "challenge_present": True}

# 2. 再解决
result = auto_solve_captcha(
    captcha_type="cloudflare",   # 当前支持 cloudflare
    challenge_type="auto",       # auto / interstitial / turnstile
    ready_delay=5.0,
    solve_attempts=3,
    verify=True
)
# 返回: {"solved": True, "verified": True, "challenge_type_used": "turnstile", ...}
```

---

## JSVMP 离线重放管线（典型流程）

```
┌─────────────────────────────────────────────────────────┐
│  ① 浏览器端：trace_vmp_for_sign()                       │
│     → 追踪 VMP 读取了哪些 fingerprint 属性               │
│     → 输出 prop → value 列表                            │
│                                                         │
│  ② WASM 反汇编：disassemble_wasm()                       │
│     → 传入 b64/hex/wasm-url                            │
│     → 输出 WAT + imports/exports + 入口函数猜测          │
│                                                         │
│  ③ Worker 源码捕获：capture_worker_js()                  │
│     → 劫持 new Worker(blob:...)                        │
│     → 获取 VMP 字节码源码                               │
│                                                         │
│  ④ WS 流量抓取：capture_ws_payloads()                   │
│     → 劫持 new WebSocket()                              │
│     → 记录 VMP 获取字节码的请求/响应                    │
│                                                         │
│  ⑤ 离线重放：replay_vmp_offline()                       │
│     → Node 沙箱执行 VMP 代码                             │
│     → supplied_env 注入缺失的 fingerprint                │
│     → auto_diff 对比签名是否匹配                         │
│                                                         │
│  ⑥ 缺失属性建议：auto_suggest_missing_props()           │
│     → 结合 trace_log + 失败日志推荐 props                │
│     → 输出可直接复制到 supplied_env                      │
└─────────────────────────────────────────────────────────┘
```

### 完整示例

```python
# Step 1: 在浏览器中追踪 VMP 读取的属性
props = trace_vmp_for_sign(
    trigger_js="sign({order_id: 12345})",
    wait_ms=500,
)

# Step 2: 反汇编 WASM（如果 VMP 使用了 WebAssembly）
wasm_info = disassemble_wasm(wasm_b64, source_kind="base64", generate_wat=True)
print(wasm_info["exports"])   # e.g. ["enc", "dec"]
print(wasm_info["entry_candidates"])

# Step 3: 离线重放（第一次可能 diff 不匹配）
result = replay_vmp_offline(
    vmp_code=vmp_code,
    entry="sign",
    input={"order_id": 12345},
    expected_sign="target_sign_value",
    auto_diff=True,
)
if not result["diff"]["match"]:
    # Step 4: 获取建议的缺失 prop
    suggestions = auto_suggest_missing_props(
        failed_replay_error=result["diff"]["replayed_preview"],
        supplied_env={},
    )
    print(suggestions["suggestions"])

    # Step 5: 重新重放（带上建议的 env）
    result = replay_vmp_offline(
        vmp_code=vmp_code,
        entry="sign",
        input={"order_id": 12345},
        expected_sign="target_sign_value",
        auto_diff=True,
        supplied_env={"navigator": {"userAgent": "..."}},
    )
    assert result["diff"]["match"]
```

---

## JS 引擎 Trace + HTTP 包 Trace 使用示例

### 方式一：组合模式（最简单）

```python
# 同时开启 JS trace + HTTP trace，持续 5 秒后自动停止
result = trace_js_and_http(
    duration=5,
    trace_js=True,
    trace_http=True,
    trace_values=True,           # 捕获函数参数值
    trace_dom_events=True,       # 捕获 DOM 事件
    capture_http_body=False,    # 不捕获响应体（省空间）
)
# result["js_trace_summary"]   → JS 函数调用热图
# result["http_trace"]         → HTTP 请求摘要
```

### 方式二：分步控制

```python
# Layer 1: 安装 JS 源码级 tracer（无需特殊浏览器）
js_engine_trace(action="install_js", max_depth=20, trace_values=True)

# 导航到目标页面，触发 JS
navigate("https://target-site.com/api/sign")

# 读取 trace 结果
result = js_engine_trace(action="read", session_id="abc12345")
# → hot_functions, depth_histogram, sample_events

# Layer 2: HTTP 包 trace
http_packet_trace(action="start", capture_body=True)

# 触发目标请求
await page.evaluate("fetch('/api/sign', {method:'POST', body: JSON.stringify({a:1})})")

# 读取 HTTP trace
http_result = http_packet_trace(action="read")
# → api_requests, javascript_requests, slow_requests
```

### 方式三：CDP DevTools 原生 tracer（需要 remote_debugging_port）

```python
# 启动带 remote debugging 的浏览器
launch_browser(remote_debugging_port=9222)

# 启动 CDP 原生 tracer（Firefox 内置的 JS ExecutionTracer）
js_engine_trace(
    action="start_cdp",
    trace_values=True,
    trace_dom_events=True,
    trace_dom_mutations=False,
    max_records=50000,
    cdp_port=9222,
)
```

---

## 技术架构

```
┌─────────────────────────────────────────────────┐
│           AI 编码助手 (Cursor / Claude)          │
│                    ↕ MCP (stdio)                  │
├─────────────────────────────────────────────────┤
│           camoufox-versatile-mcp                 │
│  ┌──────────┬──────────┬──────────┬──────────┐  │
│  │Navigation│ Script   │Debugging │ Hooking  │  │
│  ├──────────┼──────────┼──────────┼──────────┤  │
│  │ Network  │ JSVMP    │  Cookie  │  Verify  │  │
│  ├──────────┼──────────┼──────────┼──────────┤  │
│  │ ★ Captcha Auto-Solve (cloudflare)           │  │
│  │ ★ JSVMP Pipeline (WASM/Worker/WS/Replay)    │  │
│  └──────────┴──────────┴──────────┴──────────┘  │
│                    ↕ Playwright API              │
├─────────────────────────────────────────────────┤
│      Camoufox (反指纹 Firefox, Juggler 协议)     │
└─────────────────────────────────────────────────┘
```

---

## 与原版 camoufox-reverse-mcp 的区别

| 特性 | camoufox-reverse-mcp | camoufox-versatile-mcp |
|------|---------------------|------------------------|
| Captcha 挑战自动通过 | 需要单独安装 `camoufox-captcha` | **内置，无需额外配置** |
| navigate 自动过挑战 | 无 | `auto_solve_challenge=True` |
| detect_captcha 工具 | 无 | 有 |
| auto_solve_captcha 工具 | 依赖外部 | 有 |
| JSVMP 离线重放管线 | 无 | **全新（8 个工具）** |
| VMP 检测 + Tap 追踪 | 无 | 有 |
| WASM 反汇编 | 无 | 有 |
| Worker / WS 流量捕获 | 无 | 有 |
| 自动 diff + 缺失属性建议 | 无 | 有 |
| JS 引擎 Trace（三层方案） | 无 | **新建（3个工具）** |
| HTTP 包 Trace（Playwright 拦截） | 无 | 有 |
---

## 许可证

MIT
