from mcp.server.fastmcp import FastMCP
from .browser import BrowserManager

mcp = FastMCP(
    "camoufox-versatile-mcp",
    instructions="Anti-detection browser MCP server for JavaScript reverse engineering with built-in "
    "Cloudflare challenge auto-solve. Uses Camoufox (C++ engine-level fingerprint spoofing) to "
    "bypass bot detection while performing JS analysis, debugging, hooking, network interception, "
    "and JSVMP bytecode analysis."
)

browser_manager = BrowserManager()

# Import all tool modules to register them with the MCP server
from .tools import navigation       # noqa: E402, F401
from .tools import script_analysis  # noqa: E402, F401
from .tools import debugging       # noqa: E402, F401
from .tools import hooking         # noqa: E402, F401
from .tools import network         # noqa: E402, F401
from .tools import storage         # noqa: E402, F401
from .tools import jsvmp           # noqa: E402, F401
from .tools import instrumentation # noqa: E402, F401
from .tools import environment     # noqa: E402, F401
from .tools import verification    # noqa: E402, F401
from .tools import trace           # noqa: E402, F401
from .tools import captcha        # noqa: E402, F401
from .tools import vmp_pipeline  # noqa: E402, F401
from .tools import js_engine_trace_mcp  # noqa: E402, F401
