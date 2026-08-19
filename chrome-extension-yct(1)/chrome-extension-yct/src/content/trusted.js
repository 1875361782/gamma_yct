// 可信输入：把「点下去」交给 chrome.debugger 的 CDP Input 域。
//
// 为什么需要它：content script 合成的事件 isTrusted=false，Element Plus 这类组件
// 「只听 mousedown」「时灵时不灵」的病根就在这里——事件没走浏览器的真实输入管线。
// CDP Input 派发的事件 isTrusted=true、带完整 mousedown/mouseup 序列、经过真实
// hit-testing，和真人点击在页面眼里没有区别。这条通道是 2025-2026 各主流浏览器
// 自动化框架（browser-use / Stagehand / Nanobrowser）收敛出的同一个答案。
//
// 本模块只做三件事，不越界：
// - 「可信地点下去」：算出元素在**顶层视口**的坐标，请后台经 CDP 派发；
// - 坐标与遮挡预检：CDP 点击走真实 hit-testing，被弹层/遮罩盖住时点到的就是
//   别的东西——预检不过要如实报 occluded，绝不把点击发出去赌运气；
// - 通道可用性管理：权限没给、用户点掉了调试横幅、被 DevTools 占用，都按
//   「本页内降级」处理，调用方（selects/controls/runner）回退合成事件。
//
// 它**不知道**该点哪里——那是 selects/runner 的事。它也不发任何脚本执行类
// CDP 命令：后台只开放 Input 域（见 service-worker.js 的 policy 注释）。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.trusted) return;
  const { visible, isElement } = AFY.util;

  const state = {
    // unknown → init() 前；ready → 通道可用；off → 用户在设置页停用；
    // unavailable → 权限没给 / 横幅被点掉 / 被别的调试器占用 / 没有扩展上下文。
    mode: "unknown",
    reason: "",
    clicks: 0, // 走 CDP 成功的点击数
    fallbacks: 0, // 回退合成事件的次数（由调用方经 noteFallback 累加）
  };

  const hasRuntime = () => Boolean(globalThis.chrome?.runtime?.id);

  const send = async (message) => {
    if (!hasRuntime()) return { ok: false, reason: "no_runtime" };
    try {
      const reply = await chrome.runtime.sendMessage(message);
      return reply || { ok: false, reason: "no_reply" };
    } catch (error) {
      return { ok: false, reason: "messaging_failed", detail: String(error?.message || error).slice(0, 120) };
    }
  };

  /**
   * 元素中心在**顶层视口**的坐标（CSS 像素）。CDP 的 Input 坐标就是这个坐标系。
   *
   * 引擎是单实例 + 同源穿透（roots.js），元素可能在 blob: iframe 里好几层深。
   * 从元素自己的文档开始，逐层加上宿主 iframe 的位置与边框（clientLeft/Top），
   * 一路加到顶层。blob: 继承创建者 origin，这条 frameElement 链一定走得通；
   * 真走不通（跨源）就返回 null——那种元素引擎本来就看不见。
   */
  const pointOf = (el) => {
    // 跨文档安全判定（util.isElement）：这个函数**专门**服务于 iframe 里的元素，
    // 用 instanceof 等于对所有子应用字段返回 null，可信通道直接形同虚设。
    if (!isElement(el)) return null;
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    let x = rect.left + rect.width / 2;
    let y = rect.top + rect.height / 2;
    // 元素在自己文档里的坐标，留给 elementFromPoint 做命中预检。
    const local = { x, y };
    // 每一层「宿主 iframe + 该 iframe 所在文档里的坐标」，供 hitTest 逐层验遮挡。
    const chain = [];
    let doc = el.ownerDocument;
    let guard = 0;
    while (doc && doc !== document && guard < 10) {
      const frame = doc.defaultView?.frameElement;
      if (!frame) return null;
      const frameRect = frame.getBoundingClientRect();
      x += frameRect.left + frame.clientLeft;
      y += frameRect.top + frame.clientTop;
      doc = frame.ownerDocument;
      chain.push({ frame, doc, x, y });
      guard += 1;
    }
    // 走满 guard 上限却还没到顶层：坐标只累加了一部分，用它去点等于点在别处。
    // 所有「到不了顶层」的出口都必须返回 null。
    if (doc !== document) return null;
    return { x, y, local, chain };
  };

  /** elementFromPoint 的结果算不算「命中了 target」。它不进 shadow 树（返回宿主）。 */
  const hits = (found, target) =>
    Boolean(
      found &&
        (found === target ||
          target.contains(found) ||
          found.contains(target) ||
          (found.shadowRoot && found.shadowRoot.contains(target))),
    );

  /**
   * 命中预检：这个点上真正会被点到的是不是目标元素。
   *
   * 合成事件直达目标不需要这一步；CDP 点击走真实 hit-testing，预检不过等于
   * 把点击交给了随便什么盖在上面的东西——弹层遮罩、粘性表头、面板自己。
   *
   * **必须逐层查到顶层。** 只在元素自己的文档里查是个盲区：目标在 blob: iframe
   * 里、而顶层文档弹了一层 el-overlay 盖住整个 iframe 时，子文档里查得好好的，
   * CDP 却把这一下真实点击交给了顶层遮罩——而 selects.js 自己就写着「弹窗遮罩上
   * 的点击在某些站点等同于取消」。更糟的是这时 click 返回 ok，调用方不会回退，
   * 这一次点击就此无声无息地没了着落。
   */
  const hitTest = (el) => {
    const point = pointOf(el);
    if (!point) return { ok: false, reason: "no_geometry" };
    if (
      point.x < 0 ||
      point.y < 0 ||
      point.x >= window.innerWidth ||
      point.y >= window.innerHeight
    ) {
      return { ok: false, reason: "out_of_viewport", point };
    }
    const own = el.ownerDocument.elementFromPoint(point.local.x, point.local.y);
    if (!hits(own, el)) {
      return { ok: false, reason: "occluded", point, hitBy: own?.tagName || "none" };
    }
    // 逐层上行：每一层文档里，这个点都必须落在承载下一层的那个 iframe 上。
    for (const level of point.chain) {
      const found = level.doc.elementFromPoint(level.x, level.y);
      if (!hits(found, level.frame)) {
        return { ok: false, reason: "occluded", point, hitBy: found?.tagName || "none" };
      }
    }
    return { ok: true, point };
  };

  // 这些失败在本页内不会自己好转，撞上一次就整页降级，不再每次点击都试一遍。
  // 其余（attach 竞态、命令瞬时失败）只报这一次，下次照常再试。
  const PERMANENT = new Set([
    "no_permission",
    "setting_off",
    "user_detached",
    "debugger_busy",
    "restricted_target",
    "no_runtime",
    "origin_not_allowed",
  ]);

  const degrade = (reason) => {
    state.mode = "unavailable";
    state.reason = reason;
  };

  const settingOf = async () => {
    try {
      const stored = await chrome.storage.local.get("trustedInput");
      return stored?.trustedInput === "off" ? "off" : "auto";
    } catch {
      return "auto";
    }
  };

  // 用户在设置页点「停用」时，已经就绪的业务页要**立刻**收手，不能等下一次
  // init。轮询设置太贵（每次点击读一遍 storage），监听变更是零成本的正解。
  // SW 侧同样会拦（withCdp 查 trustedInput），两道一起才算闭环。
  if (globalThis.chrome?.storage?.onChanged) {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== "local" || !changes.trustedInput) return;
      if (changes.trustedInput.newValue === "off") {
        state.mode = "off";
        state.reason = "setting_off";
      } else if (state.mode !== "ready") {
        bumpGeneration();
        // 唤醒条件不能只认 off。设置页「启用可信输入」最典型的受益者恰恰是那些
        // 因为**没有权限**而停在 unavailable 的页面——只认 off 的话，用户点完
        // 「启用」回到业务页，什么都不会变，只能靠刷新，而刷新在政务表单页上
        // 意味着丢掉已填内容。
        state.mode = "unknown";
        state.reason = "";
        // init 里有 await：快速开关设置会让两轮 init 交错，后一轮把 mode 写回
        // ready 而用户其实已经关掉了。initOnce 保证同一时刻只有一轮在跑。
        void initOnce();
      }
    });
  }

  /**
   * 启动探测。结果决定 enabled() 的快速通路——展开策略列表是按它构建的，
   * 通道不可用时不往列表里塞 cdp 策略，免得每个下拉都白等一轮。
   */
  const init = async () => {
    if (!hasRuntime()) {
      degrade("no_runtime");
      return state;
    }
    if ((await settingOf()) === "off") {
      state.mode = "off";
      state.reason = "setting_off";
      return state;
    }
    const reply = await send({ type: "AFY_CDP_STATUS" });
    if (!reply.ok || !reply.permission || reply.veto) {
      degrade(reply.veto || reply.reason || (reply.permission ? "unknown" : "no_permission"));
      return state;
    }
    // 先把附着做掉，让调试横幅引起的视口回流**发生在任何坐标换算之前**，
    // 附着确认成功之后才宣告 ready。惰性到第一次点击才附着的话，那一次是
    // 「按旧布局算好坐标 → 横幅挤掉几十像素 → 用旧坐标点下去」，垂直居中的
    // 弹窗按钮正好落在最危险的位置。
    //
    // 附着没成时**不宣告 ready**：宣告了就等于说「横幅已在场、坐标可用」，
    // 而实际上第一次点击才会去附着，回流照样发生在坐标换算之后。
    if (!reply.attached) {
      const attached = await send({ type: "AFY_CDP_ATTACH" });
      if (!attached.ok) {
        degrade(attached.reason || "attach_failed");
        return state;
      }
      // 等横幅这一次布局稳下来再放行。
      await new Promise((resolve) => setTimeout(resolve, 250));
      await AFY.util.nextFrame();
    }
    state.mode = "ready";
    state.reason = "";
    return state;
  };

  // init 的串行化包装。设置开关连点、reset 与 onChanged 撞在一起时，两轮 init
  // 交错会让后完成的那一轮把状态覆盖成过期结果。
  //
  // 光「有在途就复用」还不够：调用方要的是「按**现在**的状态重新探一次」，而在途
  // 的那一轮是在 release()／设置变更**之前**启动的，它探的是旧世界。所以世代号一变，
  // 就必须等旧的那轮跑完再真的重探一次，不能把旧结果当答案交回去。
  let pending = null;
  let pendingGeneration = -1; // 在途那一轮**启动时**的世代
  let generation = 0;
  const bumpGeneration = () => {
    generation += 1;
  };
  const initOnce = async () => {
    const wanted = generation;
    if (pending) {
      // 比的必须是「在途那轮是哪个世代启动的」，不是「现在是第几代」——
      // 后者在 release() 刚 bump 完时恒等于 wanted，于是又把旧世界的探测结果
      // 当答案交回去，世代号等于白加。
      if (pendingGeneration === wanted) return pending;
      await pending.catch(() => {});
    }
    pendingGeneration = wanted;
    pending = init().finally(() => {
      pending = null;
    });
    return pending;
  };

  const enabled = () => state.mode === "ready";

  /**
   * 按**顶层视口坐标**可信点击。给「关掉赖着不走的弹层」用：那时要点的是一片
   * 空白，压根没有对应的元素句柄，click(el) 那条路走不通。
   *
   * 调用方负责挑一个安全落点（selects.findSafeSpot），这里只管点。
   */
  const clickPoint = async (x, y, { clickCount = 1 } = {}) => {
    if (!enabled()) return { ok: false, reason: state.reason || "not_ready" };
    if (!Number.isFinite(x) || !Number.isFinite(y)) return { ok: false, reason: "bad_point" };
    if (Math.abs((window.visualViewport?.scale || 1) - 1) > 0.01) {
      return { ok: false, reason: "viewport_scaled" };
    }
    const reply = await send({ type: "AFY_CDP_CLICK", x, y, clickCount });
    if (reply.ok) {
      state.clicks += 1;
      return { ok: true, via: "cdp" };
    }
    if (reply.veto && PERMANENT.has(reply.veto)) degrade(reply.veto);
    else if (PERMANENT.has(reply.reason)) degrade(reply.reason);
    return { ok: false, reason: reply.reason || "cdp_failed", detail: reply.detail };
  };

  /**
   * 可信点击。失败一律返回 { ok:false, reason }，由调用方决定是否回退合成事件——
   * 回退必须发生在同一次调用里，让每一次点击都有着落。
   */
  const click = async (el, { clickCount = 1 } = {}) => {
    if (!enabled()) return { ok: false, reason: state.reason || "not_ready" };
    if (!isElement(el) || !visible(el)) return { ok: false, reason: "not_visible" };
    // 双指捏合缩放（visualViewport.scale ≠ 1）下，CSS 像素坐标与 CDP 落点对不上，
    // 而这个偏移拿不到可靠的换算因子（页面级 Ctrl +/− 缩放另有 tabs.getZoom 补偿，
    // 在后台做）。对不准就不点——回退合成事件，它不挑坐标。
    if (Math.abs((window.visualViewport?.scale || 1) - 1) > 0.01) {
      return { ok: false, reason: "viewport_scaled" };
    }
    const checked = hitTest(el);
    if (!checked.ok) return checked;
    const reply = await send({
      type: "AFY_CDP_CLICK",
      x: checked.point.x,
      y: checked.point.y,
      clickCount,
    });
    if (reply.ok) {
      state.clicks += 1;
      return { ok: true, via: "cdp" };
    }
    // 「已落地一半」和「通道被收回」是两件独立的事，会同时发生（用户点横幅的那
    // 一刻正好有命令在途）。所以后台分两个字段带回来：reason 说这一次怎么了，
    // veto 说通道往后还能不能用。合成一个字段的话，改名会把「已落地」这个事实
    // 冲掉，调用方于是照样补点——双 mousedown 就是这么漏出去的。
    if (reply.veto && PERMANENT.has(reply.veto)) degrade(reply.veto);
    else if (PERMANENT.has(reply.reason)) degrade(reply.reason);
    return { ok: false, reason: reply.reason || "cdp_failed", detail: reply.detail };
  };

  /**
   * 可信按键。用在 selects 的键盘展开策略上——组件被 pointer-events / @click.stop
   * 挡住时，键盘常常是唯一还通的路，而合成 keydown 同样会被 isTrusted 检查挡掉。
   * 目标先聚焦，键才会落到它身上。
   */
  const pressKey = async (el, key) => {
    if (!enabled()) return { ok: false, reason: state.reason || "not_ready" };
    el?.focus?.();
    const reply = await send({ type: "AFY_CDP_KEY", key });
    if (!reply.ok) {
      if (reply.veto && PERMANENT.has(reply.veto)) degrade(reply.veto);
      else if (PERMANENT.has(reply.reason)) degrade(reply.reason);
    }
    return reply;
  };

  /**
   * 可信文本插入（Input.insertText，等价于一次 IME 提交）。当前引擎的文本写入
   * 仍走 controls.writeText 的原生 setter + input 事件（实测可进 Vue 模型），
   * 这条留给登录态 spike 验证「哪些字段非可信输入不可」。
   */
  const insertText = async (el, text) => {
    if (!enabled()) return { ok: false, reason: state.reason || "not_ready" };
    el?.focus?.();
    const reply = await send({ type: "AFY_CDP_INSERT_TEXT", text: String(text ?? "").slice(0, 2000) });
    if (!reply.ok) {
      if (reply.veto && PERMANENT.has(reply.veto)) degrade(reply.veto);
      else if (PERMANENT.has(reply.reason)) degrade(reply.reason);
    }
    return reply;
  };

  /** 松开调试附着（横幅随之消失）。面板「重置」时调，顺带清掉后台的降级记号。 */
  const release = async () => {
    if (!hasRuntime()) return;
    // 世代 +1：在途的那一轮探测（如果有）探的是松开之前的世界，结果一律作废。
    bumpGeneration();
    await send({ type: "AFY_CDP_RELEASE" });
    // 重置意味着用户想重来一遍：横幅被点掉的降级也一并清零，下一轮重新探测。
    // 但**设置里明确停用**（off）不属于「降级」，重置不该把它撤销——那是用户的
    // 另一个决定，只能由设置页改回来。
    if (state.mode === "unavailable" && state.reason !== "no_runtime") {
      state.mode = "unknown";
      state.reason = "";
    }
  };

  const noteFallback = () => {
    state.fallbacks += 1;
  };

  // 对外只暴露串行化的那一个：调用方（boot、reset、设置变更）不该各自操心重入。
  AFY.trusted = {
    state,
    init: initOnce,
    enabled,
    click,
    clickPoint,
    pressKey,
    insertText,
    release,
    noteFallback,
    pointOf,
    hitTest,
  };
})();
