// 后台：留痕落盘 + 可选的平台桥。
//
// 本地优先：不配平台也能跑完整流程，档案从选项页导入。平台桥只做三件今天后端
// 确实支持的事——配对拿设备令牌、拉取字段别名（别名的唯一来源是平台的 form_schemas，
// 扩展只做匹配不做定义）、上报运行事件。
//
// 明确的缺口，不装作已完成：平台现有的 /agent/jobs/next 下发的是**录制步骤**，
// 不是企业档案。所以「从平台拉档案」这条路要后端在 job payload 里带上 case_data
// 才能真正跑通；在那之前，接平台模式下档案仍然来自本地导入。

const JOURNAL_LIMIT = 500;

const defaults = {
  dossier: {},
  journal: [],
  platform: { enabled: false, baseUrl: "", token: "", runId: "" },
  aliases: [],
};

const read = async (keys) => {
  const stored = await chrome.storage.local.get(keys);
  return { ...defaults, ...stored };
};

const appendJournal = async (entry) => {
  const { journal } = await read("journal");
  const next = [...journal, entry].slice(-JOURNAL_LIMIT);
  await chrome.storage.local.set({ journal: next });
  return next.length;
};

const platformFetch = async (path, { method = "GET", body = null, baseUrlOverride = "" } = {}) => {
  const { platform } = await read("platform");
  const base = String(baseUrlOverride || platform.baseUrl || "").replace(/\/+$/, "");
  if (!baseUrlOverride && !platform.enabled) throw new Error("平台桥未启用");
  if (!/^https:\/\//i.test(base)) throw new Error("平台地址必须是 https");
  // MV3 里 service worker 的 fetch 同样受主机权限约束。平台地址是用户运行时填的，
  // 不可能写死在 host_permissions 里，所以走 optional_host_permissions——
  // 权限必须在选项页的用户点击里申请（这里只做校验，拿不到就明说，不要发出去被静默拦掉）。
  const granted = await chrome.permissions.contains({ origins: [`${base}/*`] });
  if (!granted) throw new Error(`还没有访问 ${base} 的权限，请在设置页重新点一次「配对」并允许授权`);
  const response = await fetch(`${base}/api/v1/chrome-extension${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(platform.token ? { "X-AF-Extension-Token": platform.token } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text.slice(0, 400) };
  }
  if (!response.ok) throw new Error(`平台返回 ${response.status}：${JSON.stringify(data).slice(0, 200)}`);
  return data;
};

// ---------------------------------------------------------------- 可信输入（CDP）
//
// content script 合成的事件 isTrusted=false，Element Plus 组件时灵时不灵的病根。
// 这里经 chrome.debugger 的 CDP Input 域派发**真实输入事件**（isTrusted=true、
// 完整 mousedown/mouseup 序列、走真实 hit-testing）。
//
// 纪律，与 policy.js 的「不执行任何远程下发的 JavaScript」同源：
// - 只发 Input 域命令（鼠标/键盘/文本插入）。不碰 Runtime/Page/DOM 域——
//   这个通道拿到的能力必须和「一只手」一样多，一点不多。
// - 文本内容不落留痕（journal 里从不出现 AFY_CDP_* 的业务值）。
// - 只接受业务域页面发来的请求。清单要与 manifest 的 content_scripts.matches 同步。
//
// 已知代价（设计文档 §3.7）：附着期间页面顶部有「正在调试此浏览器」横幅；
// 每个标签同时只能有一个调试方（与 DevTools 互斥）；用户点掉横幅即断开——
// 断开按「本标签内不再打扰」处理，直到用户在面板上点「重置」。
//
// 业务域白名单同步清单（改一处必须改全部四处）：
//   ① 这里的 CDP_ALLOWED_ORIGINS
//   ② src/common/site-profile.js 的 businessOrigins
//   ③ manifest.json 的 content_scripts[].matches
//   ④ manifest.json 的 host_permissions
// 不能抽公共模块的原因：SW 是 module，content script 不是，两边共享不了 import。
const CDP_ALLOWED_ORIGINS = ["https://online.yct.sh.gov.cn"];
const CDP_PROTOCOL_VERSION = "1.3";

const cdpState = {
  // SW 随时可能被回收，内存里的两份记录都会清零。attached 靠「命令失败补一次
  // attach」自愈即可；**否决记号不能只留内存**——用户点掉横幅之后如果恰好没有
  // 后续点击（一轮跑完时最常见），SW 闲置回收就把否决忘了，下一轮横幅自己弹回来，
  // 那正是设计约束里说的「不得自动重连」。所以 user_detached 落 storage.session
  // （随浏览器重启清零，与「本次会话内不再打扰」的语义一致）。
  attached: new Set(),
  vetoed: new Map(), // tabId → reason（user_detached / debugger_busy）
};

const vetoKey = (tabId) => `cdpVeto:${tabId}`;

const readVeto = async (tabId) => {
  const memo = cdpState.vetoed.get(tabId);
  if (memo) return memo;
  try {
    const stored = await chrome.storage.session.get(vetoKey(tabId));
    const reason = stored?.[vetoKey(tabId)];
    if (reason) cdpState.vetoed.set(tabId, reason); // 回填内存，省下后续读盘
    return reason || "";
  } catch {
    return "";
  }
};

const writeVeto = async (tabId, reason) => {
  cdpState.vetoed.set(tabId, reason);
  try {
    await chrome.storage.session.set({ [vetoKey(tabId)]: reason });
  } catch {
    /* storage.session 不可用时至少内存里还有 */
  }
};

const clearVeto = async (tabId) => {
  cdpState.vetoed.delete(tabId);
  try {
    await chrome.storage.session.remove(vetoKey(tabId));
  } catch {
    /* 同上 */
  }
};

// `debugger` 写在 manifest 的 permissions 里（Chrome 不允许它做可选权限），
// 所以 permissions.contains 正常情况下恒为真。这里仍然查一次是为了兜住两种边缘：
// 用户在 chrome://extensions 里停用了扩展的部分能力，或运行在不支持该 API 的构建上。
// 判据以命名空间是否真的可用为准——拿不到就如实报不可用，content 侧走合成事件兜底。
const debuggerGranted = async () => {
  try {
    if (!(await chrome.permissions.contains({ permissions: ["debugger"] }))) return false;
  } catch {
    return false;
  }
  return Boolean(chrome.debugger?.attach);
};

/** 设置页的「停用」开关。SW 侧也要认它——否则收回权限失败时，已经就绪的页面会继续用。 */
const trustedInputOff = async () => {
  try {
    const stored = await chrome.storage.local.get("trustedInput");
    return stored?.trustedInput === "off";
  } catch {
    return false;
  }
};

const cdpGuard = (message, sender) => {
  const tabId = sender?.tab?.id;
  if (!tabId) return { ok: false, reason: "no_tab" };
  const origin = String(sender?.origin || "");
  if (!CDP_ALLOWED_ORIGINS.includes(origin)) return { ok: false, reason: "origin_not_allowed" };
  return { ok: true, tabId };
};

const onDetached = (source, reason) => {
  if (!source?.tabId) return;
  cdpState.attached.delete(source.tabId);
  // 用户点掉横幅是明确的意思表示：本标签内不再自动重连，直到面板「重置」。
  if (reason === "canceled_by_user") void writeVeto(source.tabId, "user_detached");
};

/**
 * 幂等注册 onDetach。**不能**只在 SW 顶层按「命名空间是否存在」注册一次：
 * debugger 是可选权限，SW 在未授权时启动的话命名空间还不存在，监听就永远缺席；
 * 而用户随后在设置页授权、命名空间热挂上来（CDP 会话又会让这个 SW 一直活着），
 * 此时点掉横幅没有任何人记否决 → 下一条命令报 not attached → 自动重连，
 * 横幅点掉就回来，形成点不掉的循环。所以每次 attach 前都补调一次。
 */
const ensureDebuggerListeners = () => {
  const api = globalThis.chrome?.debugger?.onDetach;
  if (!api || api.hasListener?.(onDetached)) return;
  api.addListener(onDetached);
};

/**
 * 附着并执行一段 CDP 命令。
 *
 * `run` 收到一个 send 包装：**每发出一条命令就记一次**。这是重放安全的关键——
 * 会话半路断掉时（用户点横幅、Chrome 收走），在途命令报的是「Detached while
 * handling command.」，光看文案分不清「一条都没发出去」和「发到一半」。整段重放
 * 一次 mousePressed 已经落地的点击序列，页面会收到两次 mousedown：Element 的
 * 下拉是开关，等于点开又点关，正是「不允许双击」那条纪律要挡的东西。
 * 所以只有一条都没发出去时才允许重放，否则如实报 interrupted 交给回读核验。
 */
const withCdp = async (tabId, run) => {
  if (!(await debuggerGranted())) return { ok: false, reason: "no_permission" };
  if (await trustedInputOff()) return { ok: false, reason: "setting_off" };
  const veto = await readVeto(tabId);
  if (veto) return { ok: false, reason: veto };

  // **计数的是「已经落地的命令」，不是「已经发出的命令」**——自增必须在 await
  // 之后。写在前面的话，run 的第一条语句就是 send，任何失败都会让 landed>0，
  // 于是「一条都没发出去，可以安全重放」这个分支永远进不去，SW 回收后的自愈也
  // 就没了；反过来还会把「什么都没发生」的中断报成 interrupted。
  // 只有**有副作用的**命令才计数。`mouseMoved` 单纯是移动指针，重放它没有任何
  // 后果；把它算进去的话，「指针移到位了、按下那条断了」也会被判成不可重放，
  // 白白丢掉一次本来可以安全重来的点击。
  let landed = 0;
  const send = async (method, params, { harmless = false } = {}) => {
    const result = await chrome.debugger.sendCommand({ tabId }, method, params);
    if (!harmless) landed += 1;
    return result;
  };

  /**
   * 命令序列失败时怎么归类。**三个落点共用这一个**（正常路径、already-attached
   * 探测、重放），各写各的必然漂移——终检就是在另外两处逮到了漏判。
   *
   * 判据只有一条：`landed > 0`（有副作用的命令已经落地）→ 报 `interrupted`，
   * content 侧据此不补合成点击。否决要**另开一个字段**带出去，不能覆盖 reason：
   * 覆盖了的话「已落地」这个事实就随着改名丢了，调用方照样补点。
   */
  const classifyRunError = async (error, fallbackReason) => {
    const why = String(error?.message || error);
    const dropped = /not attached|detached/i.test(why);
    if (landed > 0) {
      cdpState.attached.delete(tabId);
      return { ok: false, reason: "interrupted", veto: await readVeto(tabId), detail: why.slice(0, 120) };
    }
    return { ok: false, reason: fallbackReason, dropped, detail: why.slice(0, 120) };
  };

  const attach = async () => {
    ensureDebuggerListeners();
    await chrome.debugger.attach({ tabId }, CDP_PROTOCOL_VERSION);
    cdpState.attached.add(tabId);
    // attach 与 onDetach 存在竞态：用户点横幅的瞬间我们可能刚好连上。连上之后
    // 复查一次否决，命中就主动松开——否则等于替用户撤销了他刚做的决定。
    const late = await readVeto(tabId);
    if (late) {
      cdpState.attached.delete(tabId);
      try {
        await chrome.debugger.detach({ tabId });
      } catch {
        /* 已经掉了就算了 */
      }
      return late;
    }
    return "";
  };

  if (!cdpState.attached.has(tabId)) {
    try {
      const late = await attach();
      if (late) return { ok: false, reason: late };
    } catch (error) {
      const text = String(error?.message || error);
      if (/already attached/i.test(text)) {
        // 可能是上一个 SW 实例留下的会话（那就还是我们的），也可能是 DevTools。
        // 用一条命令试出来，但**只有明确的「不是我们的会话」才永久否决**：
        // 一次瞬时失败（目标 frame 正在导航之类）不该让整页永久丢掉可信通道。
        ensureDebuggerListeners();
        try {
          await run(send);
          cdpState.attached.add(tabId);
          return { ok: true };
        } catch (probe) {
          const verdict = await classifyRunError(probe, "command_failed");
          // landed > 0 说明这个会话本来就是我们的（命令真的执行了），
          // 这时报 debugger_busy 是错的——那会把一次半途断线变成整页永久降级。
          if (verdict.reason === "interrupted") return verdict;
          // 一条都没落地 + 明确的「不是我们的会话」→ 才是真的被别人占着。
          if (verdict.dropped) {
            await writeVeto(tabId, "debugger_busy");
            return { ok: false, reason: "debugger_busy" };
          }
          return verdict;
        }
      }
      if (/cannot attach|cannot access|restricted/i.test(text)) {
        return { ok: false, reason: "restricted_target" };
      }
      return { ok: false, reason: "attach_failed", detail: text.slice(0, 120) };
    }
  }

  try {
    await run(send);
    return { ok: true };
  } catch (error) {
    const text = String(error?.message || error);
    if (/not attached|detached/i.test(text)) {
      cdpState.attached.delete(tabId);
      // 序列已经落地一部分：不重放。重放会造成双 mousedown。
      if (landed > 0) return classifyRunError(error, "interrupted");
      try {
        const late = await attach();
        if (late) return { ok: false, reason: late };
        await run(send);
        return { ok: true };
      } catch (again) {
        // 重放自己也可能半路落地——landed 是跨这两次尝试累加的，
        // 所以这里同样要先问一次「落地过没有」，不能一律报 attach_failed。
        return classifyRunError(again, "attach_failed");
      }
    }
    return { ok: false, reason: "command_failed", detail: text.slice(0, 120) };
  }
};

// 常用键的最小映射表。只支持展开/确认这几种引擎真会用到的键，不做通用键盘。
const CDP_KEYS = {
  ArrowDown: { keyCode: 40, code: "ArrowDown" },
  ArrowUp: { keyCode: 38, code: "ArrowUp" },
  Enter: { keyCode: 13, code: "Enter", text: "\r" },
  Escape: { keyCode: 27, code: "Escape" },
  Tab: { keyCode: 9, code: "Tab" },
};

ensureDebuggerListeners();

// 权限是运行时授予的：授予的那一刻命名空间才挂上来，这时要补注册监听。
if (globalThis.chrome?.permissions?.onAdded) {
  chrome.permissions.onAdded.addListener((granted) => {
    if (granted?.permissions?.includes("debugger")) ensureDebuggerListeners();
  });
}

if (globalThis.chrome?.tabs?.onRemoved) {
  chrome.tabs.onRemoved.addListener((tabId) => {
    cdpState.attached.delete(tabId);
    void clearVeto(tabId);
  });
}

const handlers = {
  async AFY_JOURNAL({ entry }) {
    return { ok: true, size: await appendJournal(entry) };
  },

  async AFY_CDP_STATUS(message, sender) {
    const guard = cdpGuard(message, sender);
    if (!guard.ok) return guard;
    return {
      ok: true,
      permission: (await debuggerGranted()) && !(await trustedInputOff()),
      attached: cdpState.attached.has(guard.tabId),
      veto: await readVeto(guard.tabId),
    };
  },

  /**
   * 预附着。通道就绪时先把调试横幅弹出来，让它引起的那一次视口回流**先发生**。
   * 不预附着的话，第一次点击是「先按旧布局算好坐标 → attach → 横幅挤掉几十像素
   * → 用旧坐标点下去」，垂直居中的弹窗按钮正好落在风险区。
   */
  async AFY_CDP_ATTACH(message, sender) {
    const guard = cdpGuard(message, sender);
    if (!guard.ok) return guard;
    // 发一条 mouseMoved 到视口原点：既无副作用（只是移动指针，不按下），又能在
    // 「already attached」那条路径上真正探出会话归属。空 run 是不行的——它一条
    // 命令都不发、必定成功，于是被 DevTools 占着的标签也会被记成「我们已附着」，
    // 后面每一次点击都要白试一遍才发现。
    return withCdp(guard.tabId, async (send) => {
      await send(
        "Input.dispatchMouseEvent",
        { type: "mouseMoved", x: 0, y: 0, pointerType: "mouse" },
        { harmless: true },
      );
    });
  },

  async AFY_CDP_CLICK(message, sender) {
    const guard = cdpGuard(message, sender);
    if (!guard.ok) return guard;
    const rawX = Number(message.x);
    const rawY = Number(message.y);
    if (!Number.isFinite(rawX) || !Number.isFinite(rawY) || rawX < 0 || rawY < 0) {
      return { ok: false, reason: "bad_point" };
    }
    // 页面缩放（Ctrl +/−）下，content 侧的 CSS 像素坐标与 CDP 的落点差一个 zoom
    // 因子；不补偿就会点到隔壁选项上——选错一个区划就是一次错误申报。
    let zoom = 1;
    try {
      zoom = (await chrome.tabs.getZoom(guard.tabId)) || 1;
    } catch {
      zoom = 1;
    }
    const x = rawX * zoom;
    const y = rawY * zoom;
    const clickCount = Math.min(2, Math.max(1, Number(message.clickCount) || 1));
    const base = { x, y, button: "left", clickCount, pointerType: "mouse" };
    return withCdp(guard.tabId, async (send) => {
      await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y, pointerType: "mouse" }, { harmless: true });
      await send("Input.dispatchMouseEvent", { type: "mousePressed", ...base });
      await send("Input.dispatchMouseEvent", { type: "mouseReleased", ...base });
    });
  },

  async AFY_CDP_KEY(message, sender) {
    const guard = cdpGuard(message, sender);
    if (!guard.ok) return guard;
    const key = CDP_KEYS[String(message.key || "")];
    if (!key) return { ok: false, reason: "unsupported_key" };
    const shared = {
      key: String(message.key),
      code: key.code,
      windowsVirtualKeyCode: key.keyCode,
      nativeVirtualKeyCode: key.keyCode,
    };
    return withCdp(guard.tabId, async (send) => {
      await send("Input.dispatchKeyEvent", {
        type: key.text ? "keyDown" : "rawKeyDown",
        ...(key.text ? { text: key.text } : {}),
        ...shared,
      });
      await send("Input.dispatchKeyEvent", { type: "keyUp", ...shared });
    });
  },

  async AFY_CDP_INSERT_TEXT(message, sender) {
    const guard = cdpGuard(message, sender);
    if (!guard.ok) return guard;
    const text = String(message.text ?? "").slice(0, 2000);
    // 刻意不留任何包含 text 的日志：可信输入通道绝不能变成第二份档案副本。
    return withCdp(guard.tabId, async (send) => {
      await send("Input.insertText", { text });
    });
  },

  async AFY_CDP_RELEASE(message, sender) {
    const guard = cdpGuard(message, sender);
    if (!guard.ok) return guard;
    await clearVeto(guard.tabId);
    if (cdpState.attached.has(guard.tabId)) {
      cdpState.attached.delete(guard.tabId);
      try {
        await chrome.debugger.detach({ tabId: guard.tabId });
      } catch {
        /* 已经掉了就算了 */
      }
    }
    return { ok: true };
  },

  /**
   * 全局松开。设置页点「停用」时调，**必须在移除权限之前**：权限一旦收回，
   * chrome.debugger 命名空间就没了，detach 再也发不出去，横幅会一直挂到关标签。
   * 这个 handler 不走 cdpGuard——它的调用方是扩展自己的设置页，没有 sender.tab。
   */
  async AFY_CDP_RELEASE_ALL() {
    let detached = 0;
    // getTargets 不可用时**也要往下走**：本地那几份记录（含 storage.session 里的
    // 否决记号）必须清掉，否则「停用再启用」会带着上一轮的否决，通道莫名不工作。
    if (globalThis.chrome?.debugger?.getTargets) {
      try {
        const targets = await chrome.debugger.getTargets();
        for (const target of targets) {
          if (!target.attached || !target.tabId) continue;
          try {
            await chrome.debugger.detach({ tabId: target.tabId });
            detached += 1;
          } catch {
            /* 不是我们附着的会话，detach 会报错，忽略即可 */
          }
        }
      } catch {
        /* 枚举失败就只清本地记录 */
      }
    }
    cdpState.attached.clear();
    cdpState.vetoed.clear();
    try {
      const all = await chrome.storage.session.get(null);
      const keys = Object.keys(all || {}).filter((key) => key.startsWith("cdpVeto:"));
      if (keys.length) await chrome.storage.session.remove(keys);
    } catch {
      /* 同上 */
    }
    return { ok: true, detached };
  },

  async AFY_STATE() {
    const state = await read(["dossier", "journal", "platform", "aliases"]);
    return {
      ok: true,
      hasDossier: Object.keys(state.dossier || {}).length > 0,
      journalSize: (state.journal || []).length,
      platform: { ...state.platform, token: state.platform.token ? "已配对" : "" },
      aliasCount: (state.aliases || []).length,
    };
  },

  async AFY_CLEAR_JOURNAL() {
    await chrome.storage.local.set({ journal: [] });
    return { ok: true };
  },

  // 配对失败时**不能**动已存的配置：旧实现先把 platform 覆盖成空壳再发请求，
  // 一次输错配对码就把之前配好的设备令牌抹掉了。
  async AFY_PAIR({ baseUrl, pairingCode, chromeVersion }) {
    const result = await platformFetch("/agent/pair", {
      method: "POST",
      baseUrlOverride: baseUrl,
      body: {
        pairing_code: pairingCode,
        name: "一窗通填报助手",
        extension_version: chrome.runtime.getManifest().version,
        chrome_version: chromeVersion || "",
      },
    });
    const token = result?.token || result?.extension_token || "";
    if (!token) throw new Error("平台没有返回设备令牌");
    await chrome.storage.local.set({ platform: { enabled: true, baseUrl, token, runId: "" } });
    return { ok: true };
  },

  // 别名的唯一来源是平台的 form_schemas。拉到就覆盖本地那份，改一处不会漏两处。
  async AFY_SYNC_ALIASES() {
    const result = await platformFetch("/agent/binding-options");
    const items = Array.isArray(result?.items) ? result.items : [];
    await chrome.storage.local.set({ aliases: items });
    return { ok: true, count: items.length };
  },

  async AFY_REPORT({ runId, event }) {
    const { platform } = await read("platform");
    if (!platform.enabled || !runId) return { ok: false, reason: "未接入平台或没有 run_id" };
    await platformFetch(`/agent/jobs/${encodeURIComponent(runId)}/events`, {
      method: "POST",
      body: {
        event_type: String(event?.action || "note"),
        step_index: Number(event?.step || 0),
        url: String(event?.url || ""),
        label: String(event?.field || event?.title || ""),
        verified: event?.status === "ok",
        error: String(event?.reason || ""),
      },
    });
    return { ok: true };
  },
};

// 注册前先验明 chrome.runtime 存在。两种已知场景里它是 undefined：
// ① 在 chrome://extensions 反复「重新加载」时，被作废的旧 service worker 实例
//    可能带着已拆掉的上下文再跑一次（extension context invalidated），顶层直接
//    访问 chrome.runtime 就抛 Uncaught TypeError，并且一直挂在扩展的错误列表里；
// ② 自检页的解析检查会把本文件加载进普通网页跑一遍。
// 这两种场景都不该注册监听，也都不值得抛一条吓人的未捕获异常。
if (globalThis.chrome?.runtime?.onMessage) {
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    const handler = handlers[message?.type];
    if (!handler) return undefined;
    handler(message, sender)
      .then(sendResponse)
      .catch((error) => sendResponse({ ok: false, error: String(error?.message || error).slice(0, 300) }));
    return true;
  });
} else {
  console.warn(
    "[一窗通填报助手] 扩展上下文不可用（多半是重载后的旧实例，或本文件被加载进了普通页面），本次不注册消息监听。",
  );
}
