# camoufox-versatile-mcp

[中文](README.md) | [English](README_en.md)

> 基于反指纹浏览器（Camoufox）的 MCP Server，支持 **通用 captcha 挑战自动通过** + **JSVMP 签名还原管线**。

Fork 自 [camoufox-reverse-mcp](https://github.com/WhiteNightShadow/camoufox-reverse-mcp)，新增：
- 内置 **captcha 通用检测 + 自动通过**（基于 `camoufox_captcha`）
- 全新 **JSVMP 离线复现管线**：WASM 反汇编 / Worker 源码捕获 / WS 流量抓取 / 离线重放 + 自动 diff

---

## 快速开始

### 安装

```bash
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

---

## 许可证

MIT
