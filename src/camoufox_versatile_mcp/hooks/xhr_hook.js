(function() {
    if (window.__mcp_xhr_hooked) return;
    window.__mcp_xhr_hooked = true;
    window.__mcp_xhr_log = window.__mcp_xhr_log || [];

    const _origProto = XMLHttpRequest.prototype;
    const _open = _origProto.open;
    const _send = _origProto.send;
    const _setReqHeader = _origProto.setRequestHeader;

    const hookedOpen = function(method, url) {
        this.__mcp_info = { method, url: String(url), headers: {}, timestamp: Date.now() };
        return _open.apply(this, arguments);
    };
    const hookedSetHeader = function(name, value) {
        if (this.__mcp_info) this.__mcp_info.headers[name] = value;
        return _setReqHeader.apply(this, arguments);
    };
    const hookedSend = function(body) {
        if (this.__mcp_info) {
            this.__mcp_info.body = typeof body === 'string' ? body : (body ? String(body).substring(0, 5000) : null);
            this.__mcp_info.stack = new Error().stack;
            const info = this.__mcp_info;
            this.addEventListener('load', function() {
                info.status = this.status;
                info.response_length = this.responseText?.length;
                window.__mcp_xhr_log.push(info);
                if (window.__mcp_xhr_log.length > 500) window.__mcp_xhr_log.shift();
            });
        }
        return _send.apply(this, arguments);
    };

    const nativeToString = function(name) {
        return 'function ' + name + '() { [native code] }';
    };

    try {
        Object.defineProperty(_origProto, 'open', {
            value: hookedOpen, writable: false, configurable: false
        });
        Object.defineProperty(_origProto, 'setRequestHeader', {
            value: hookedSetHeader, writable: false, configurable: false
        });
        Object.defineProperty(_origProto, 'send', {
            value: hookedSend, writable: false, configurable: false
        });
    } catch(e) {
        _origProto.open = hookedOpen;
        _origProto.setRequestHeader = hookedSetHeader;
        _origProto.send = hookedSend;
    }

    hookedOpen.toString = function() { return nativeToString('open'); };
    hookedSetHeader.toString = function() { return nativeToString('setRequestHeader'); };
    hookedSend.toString = function() { return nativeToString('send'); };
})();
