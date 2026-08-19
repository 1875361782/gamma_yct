// MAIN world 里的只读接口嗅探。
//
// 为什么需要它：这个站点用 HTTP 200 + 响应体 `code: 401` 表达「当前请求已拦截，
// 请登录后再试」。只看状态码看不出来，而下拉一旦拿不到字典就是空的——空下拉在
// 界面上和「级联前置没选」长得一模一样。分不清这两者，人就会顺着错误的方向查很久
// （已经发生过一次：把组织形式的静态提示浮层误读成级联前置，方向整个错了）。
//
// 边界，写死在这里：
// - 只观察，不改写请求、不改写响应、不重放；原始的 fetch/XHR 行为一字不动。
// - 只取 URL 的 path（丢掉 query，query 里可能有令牌）、HTTP 状态，以及**字典接口**
//   响应体前 400 字里是否出现「请登录」措辞。不读 data，不读任何业务值。
// - 出错一律吞掉：嗅探是诊断增强，绝不能把站点自己的请求带崩。
//
// 这个文件改坏一行，整个站点就打不开了。改动前先读下面 patchedFetch 的注释。
(() => {
  const MARKER = "__afy_yct_probe__";
  if (window[MARKER]) return;
  window[MARKER] = true;

  const UNAUTHORIZED = /(请登录|已拦截|未登录|登录后再试|token.*(失效|过期))/;
  // 只有字典接口才值得读响应体——它们是下拉选项的来源，也是「空下拉」的根因所在。
  const DICTIONARY = /\/(dicname|getDistrictDesc|getOrgForm|getIndustryDesc|dict)/i;
  // 其余接口只看状态码，不碰 body：读 body 要 clone + 全量解码，每个请求都做纯属浪费。
  const ANY_API = /\/(api|rest|service)\//i;

  const post = (payload) => {
    try {
      const target = window.top && window.top !== window ? window.top : window;
      target.postMessage({ marker: MARKER, ...payload }, "*");
    } catch {
      /* 跨源顶层拿不到就算了，诊断信息缺一条不影响填写 */
    }
  };

  const pathOf = (input) => {
    try {
      const raw = input && typeof input === "object" && "url" in input ? input.url : input;
      return new URL(String(raw), location.href).pathname.slice(0, 200);
    } catch {
      return "";
    }
  };

  const reportStatus = (path, status) => {
    if (status !== 401 && status !== 403) return false;
    post({ blocked: true, path, status, message: "接口返回未授权" });
    return true;
  };

  const reportBody = (path, status, text) => {
    if (!UNAUTHORIZED.test(String(text || "").slice(0, 400))) return;
    post({ blocked: true, path, status, message: "站点提示需要登录" });
  };

  const originalFetch = window.fetch;
  if (typeof originalFetch === "function") {
    window.fetch = function patchedFetch(input) {
      // `this || window` 不是防御性冗余，是必需的：站点代码里 `fetch(url)` 这种裸调用
      // 在严格模式下 this 是 undefined，而 fetch 要求 this 是 Window。
      // 写成 `originalFetch.apply(this, ...)` 会让站点每一个 fetch 都抛
      // "Illegal invocation"——扩展一装上，整个站点就打不开了。
      const promise = originalFetch.apply(this || window, arguments);
      try {
        const path = pathOf(input);
        if (path && (DICTIONARY.test(path) || ANY_API.test(path))) {
          promise
            .then((response) => {
              if (reportStatus(path, response.status)) return;
              if (!DICTIONARY.test(path)) return;
              // clone() 保证站点自己的 body 一次都不被消费。
              response
                .clone()
                .text()
                .then((text) => reportBody(path, response.status, text))
                .catch(() => {});
            })
            .catch(() => {});
        }
      } catch {
        /* 嗅探本身出任何问题都不能影响这次请求 */
      }
      return promise;
    };
  }

  const XHR = window.XMLHttpRequest;
  if (XHR && XHR.prototype) {
    const open = XHR.prototype.open;
    XHR.prototype.open = function patchedOpen(method, url) {
      try {
        this[`${MARKER}path`] = pathOf(url);
      } catch {
        /* 站点可能冻结实例；拿不到就不嗅探这一条 */
      }
      return open.apply(this, arguments);
    };

    const send = XHR.prototype.send;
    XHR.prototype.send = function patchedSend() {
      try {
        const path = this[`${MARKER}path`] || "";
        if (path && (DICTIONARY.test(path) || ANY_API.test(path))) {
          this.addEventListener("load", () => {
            try {
              if (reportStatus(path, this.status)) return;
              if (!DICTIONARY.test(path)) return;
              // responseType 不是 text/空 时读 responseText 会抛，先判类型。
              const type = this.responseType;
              if (type !== "" && type !== "text") return;
              reportBody(path, this.status, this.responseText);
            } catch {
              /* 读不到就算了 */
            }
          });
        }
      } catch {
        /* 同上，绝不影响这次请求 */
      }
      return send.apply(this, arguments);
    };
  }
})();
