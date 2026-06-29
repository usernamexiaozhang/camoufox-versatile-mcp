"""
js_rewriter.py - JS source-level instrumentation via regex and AST transforms.
"""
from __future__ import annotations
import re
from typing import Tuple


INSTRUMENT_RUNTIME = r"""
(function(){
  if (window.__mcp_tap_installed) return;
  window.__mcp_tap_installed = true;
  window.__mcp_vmp_log = window.__mcp_vmp_log || [];
  var CAP = 20000;
  window.__mcp_tap_cfg = window.__mcp_tap_cfg || { sampling: 1, tagFilter: null };
  function _push(e){
    if (window.__mcp_vmp_log.length >= CAP) return;
    e.ts = Date.now();
    window.__mcp_vmp_log.push(e);
  }
  function _tag(t){
    var f = window.__mcp_tap_cfg.tagFilter;
    if (!f) return true;
    return (t||'').indexOf(f) !== -1;
  }
  function _preview(v){
    try {
      if (v === null) return 'null';
      if (v === undefined) return 'undefined';
      var t = typeof v;
      if (t === 'function') return '[fn ' + (v.name || '') + ']';
      if (t === 'object') {
        var s = JSON.stringify(v);
        return s && s.length > 120 ? s.substr(0, 120) + '...' : s;
      }
      var s2 = String(v);
      return s2.length > 120 ? s2.substr(0, 120) + '...' : s2;
    } catch (e) { return '[err]'; }
  }
  window.__mcp_tap_get = function(obj, key, tag){
    var val;
    try { val = obj[key]; } catch (e) { val = undefined; }
    if (_tag(tag) && Math.random() < window.__mcp_tap_cfg.sampling){
      _push({ type:'tap_get', tag: tag, key: String(key),
              objType: obj && obj.constructor ? obj.constructor.name : typeof obj,
              value: _preview(val) });
    }
    return val;
  };
  window.__mcp_tap_call = function(fn, thisArg, args, tag){
    var r;
    try { r = fn.apply(thisArg, args); } catch (e) {
      if (_tag(tag)) _push({ type:'tap_call_err', tag: tag,
                             name: fn && fn.name || 'anon', err: String(e) });
      throw e;
    }
    if (_tag(tag) && Math.random() < window.__mcp_tap_cfg.sampling){
      _push({ type:'tap_call', tag: tag, name: fn && fn.name || 'anon',
              argc: args ? args.length : 0, arg0: args && args.length ? _preview(args[0]) : null,
              ret: _preview(r) });
    }
    return r;
  };
  window.__mcp_tap_method = function(obj, key, args, tag){
    var fn = obj[key];
    if (typeof fn !== 'function') return undefined;
    var r = fn.apply(obj, args);
    if (_tag(tag) && Math.random() < window.__mcp_tap_cfg.sampling){
      _push({ type:'tap_method', tag: tag,
              objType: obj && obj.constructor ? obj.constructor.name : typeof obj,
              method: String(key),
              argc: args ? args.length : 0,
              arg0: args && args.length ? _preview(args[0]) : null,
              ret: _preview(r) });
    }
    return r;
  };
})();
"""


_MEMBER_BRACKET_RE = re.compile(
    r'([A-Za-z_$][A-Za-z0-9_$]*)\s*\[\s*([^\[\]\n]{1,200}?)\s*\]'
)


def _rewrite_member_access(src: str, tag: str, max_rewrites: int = 5000) -> Tuple[str, int]:
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        if count >= max_rewrites:
            return m.group(0)
        obj = m.group(1)
        key = m.group(2)
        if obj in ('require', 'module', 'exports', 'console', '__mcp_tap_get',
                   '__mcp_tap_call', '__mcp_tap_method', '_push', '_tag', '_preview'):
            return m.group(0)
        end = m.end()
        tail = src[end:end + 3]
        if tail.lstrip().startswith('=') and not tail.lstrip().startswith('=='):
            return m.group(0)
        count += 1
        return f"__mcp_tap_get({obj},{key},{repr(tag)})"

    new_src = _MEMBER_BRACKET_RE.sub(repl, src)
    return new_src, count


def regex_rewrite(src: str, tag: str = "vmp", rewrite_member_access: bool = True,
                  max_rewrites: int = 5000) -> Tuple[str, dict]:
    stats = {"member_access_rewrites": 0}
    new_src = src
    if rewrite_member_access:
        new_src, n = _rewrite_member_access(new_src, tag, max_rewrites)
        stats["member_access_rewrites"] = n
    return INSTRUMENT_RUNTIME + "\n" + new_src, stats


ACORN_REWRITE_JS_TEMPLATE = r"""
async (src, tag, opts) => {
  if (!window.acorn) {
    await new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = 'https://cdnjs.cloudflare.com/ajax/libs/acorn/8.11.3/acorn.min.js';
      s.onload = res; s.onerror = rej;
      document.head.appendChild(s);
    });
    await new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = 'https://cdnjs.cloudflare.com/ajax/libs/acorn-walk/8.3.2/walk.min.js';
      s.onload = res; s.onerror = rej;
      document.head.appendChild(s);
    });
  }
  let ast;
  try {
    ast = acorn.parse(src, { ecmaVersion: 'latest', sourceType: 'script', allowReturnOutsideFunction: true });
  } catch (e) {
    return { ok: false, error: 'parse_error: ' + e.message };
  }

  const edits = [];
  const tagLit = JSON.stringify(tag);

  function isAssignTarget(node, parent) {
    return parent && parent.type === 'AssignmentExpression' && parent.left === node;
  }
  function isUpdateTarget(node, parent) {
    return parent && parent.type === 'UpdateExpression' && parent.argument === node;
  }

  acorn.walk.ancestor(ast, {
    MemberExpression(node, state, ancestors) {
      const parent = ancestors[ancestors.length - 2];
      if (isAssignTarget(node, parent) || isUpdateTarget(node, parent)) return;
      if (parent && parent.type === 'CallExpression' && parent.callee === node) {
        return;
      }
      if (opts.rewriteMemberAccess) {
        const objSrc = src.slice(node.object.start, node.object.end);
        const keySrc = node.computed
          ? src.slice(node.property.start, node.property.end)
          : JSON.stringify(node.property.name);
        edits.push({
          start: node.start, end: node.end,
          replacement: `__mcp_tap_get(${objSrc}, ${keySrc}, ${tagLit})`
        });
      }
    },
    CallExpression(node, state, ancestors) {
      if (!opts.rewriteCalls) return;
      if (node.callee.type === 'MemberExpression') {
        const me = node.callee;
        const objSrc = src.slice(me.object.start, me.object.end);
        const keySrc = me.computed
          ? src.slice(me.property.start, me.property.end)
          : JSON.stringify(me.property.name);
        const argsSrc = node.arguments.length
          ? '[' + node.arguments.map(a => src.slice(a.start, a.end)).join(',') + ']'
          : '[]';
        edits.push({
          start: node.start, end: node.end,
          replacement: `__mcp_tap_method(${objSrc}, ${keySrc}, ${argsSrc}, ${tagLit})`
        });
      } else if (node.callee.type === 'Identifier') {
        const fnSrc = src.slice(node.callee.start, node.callee.end);
        const argsSrc = node.arguments.length
          ? '[' + node.arguments.map(a => src.slice(a.start, a.end)).join(',') + ']'
          : '[]';
        edits.push({
          start: node.start, end: node.end,
          replacement: `__mcp_tap_call(${fnSrc}, null, ${argsSrc}, ${tagLit})`
        });
      }
    }
  });

  edits.sort((a, b) => b.start - a.start);
  let out = src;
  for (const e of edits) {
    out = out.slice(0, e.start) + e.replacement + out.slice(e.end);
  }

  return { ok: true, src: out, edit_count: edits.length };
}
"""
