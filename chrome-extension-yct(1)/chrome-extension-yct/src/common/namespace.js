// MV3 的声明式 content script 不支持 ES module，manifest 里列出的脚本按顺序
// 在同一个 isolated world 里跑。所以整个前台引擎共用这一个命名空间对象，
// 后面每个文件往上挂自己的一层，不再有 import。
(() => {
  const AFY = (globalThis.__AFY ||= {});
  if (AFY.util) return;

  AFY.VERSION = "0.1.0";
  // MAIN world 的接口嗅探器往 isolated world 投递消息时带这个标记。
  // 页面自己也能 postMessage，所以标记 + 来源校验缺一不可。
  AFY.MARKER = "__afy_yct_probe__";

  const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();

  // 归一化只用于「选项择一」的第二级，刻意做得很浅：全角转半角、去掉所有空白、
  // 去掉成对的中英文括号内容。**不做**繁简转换、不去「市/省/区」这类后缀——
  // 那会把「浦东新区」和「浦东」判成同一个，而政务下拉里这两者可能都存在。
  // 后缀差异交给第三级「唯一前缀」去解决，并且要求唯一。
  const normalize = (value) =>
    clean(value)
      .replace(/[！-～]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
      .replace(/　/g, "")
      .replace(/[（(][^）)]*[）)]/g, "")
      .replace(/\s+/g, "")
      .toLowerCase();

  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // 等一个渲染周期，但**必须带兜底**：标签页切到后台时 requestAnimationFrame 完全
  // 不触发，纯 rAF 的实现会让整轮填写永远挂在这里——用户只是切出去看了一眼，
  // 回来发现进度条停住了，而且没有任何报错。自检页第一次跑就是这么卡住的。
  const nextFrame = () =>
    new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        resolve();
      };
      const timer = setTimeout(finish, 250);
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          clearTimeout(timer);
          finish();
        }),
      );
    });

  /** 轮询直到 fn() 返回真值或超时。返回值即 fn 的最后一次结果。 */
  const until = async (fn, { timeout = 6000, interval = 150 } = {}) => {
    const deadline = Date.now() + timeout;
    let last = null;
    for (;;) {
      last = await fn();
      if (last) return last;
      if (Date.now() >= deadline) return last;
      await delay(interval);
    }
  };

  /**
   * 「这是个元素吗」——**不能**写成 `el instanceof Element`。
   *
   * 引擎靠同源穿透进 blob: iframe 取元素（roots.js），而 iframe 有自己的一套内置
   * 构造器：子文档里的元素 `instanceof`（顶层的）`Element` 恒为 false。踩下去的后果
   * 是静默的——visible 全判不可见、kindOf 全判 unknown，于是 page.inventory 把
   * **子应用里的每一个字段都跳过**，界面上显示「当前业务页面没有可填写字段」，
   * 而字段明明就在那里。本站业务字段几乎都在 wujie 子应用里，这条一旦写错，
   * 整个引擎在真实站点上等于没装。
   *
   * nodeType 是内部槽上的数值，跨文档一律为 1，没有这个坑。
   */
  const isElement = (node) => Boolean(node && node.nodeType === 1);

  const visible = (el) => {
    if (!isElement(el)) return false;
    const view = el.ownerDocument?.defaultView;
    if (!view) return false;
    const style = view.getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    if (Number(style.opacity || 1) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const disabled = (el) =>
    Boolean(
      el?.disabled ||
        el?.getAttribute?.("aria-disabled") === "true" ||
        el?.classList?.contains("is-disabled"),
    );

  /**
   * 三级择一。分级本身就是安全设计：能精确匹配就绝不模糊，模糊到多个就停下来
   * 让人决定，绝不「取第一个」。政务表单里选错一个区划就是一次错误申报。
   *
   * @returns {{status:'exact'|'normalized'|'prefix'|'none'|'ambiguous', value?:string,
   *            candidates:string[]}}
   */
  const chooseOne = (wanted, candidates) => {
    const list = (candidates || []).map(clean).filter(Boolean);
    const target = clean(wanted);
    if (!target || list.length === 0) return { status: "none", candidates: list };

    const exact = list.filter((item) => item === target);
    if (exact.length === 1) return { status: "exact", value: exact[0], candidates: list };
    // 字面**完全相同**的重复项：站点在不同分组下各放了一个同名选项
    // （内资企业和外资企业下各有一个「有限公司」）。按用户约定取第一个，
    // 并如实标成 duplicate，让界面能说出「有几个同名的、取了第一个」。
    //
    // 只对**字面相同**放宽。文案不同的多重命中（「信息科技 (主营行业:A)」与
    // 「…(主营行业:B)」）仍然判歧义——那是一次业务选择，不能替人做。
    if (exact.length > 1) {
      return { status: "duplicate", value: exact[0], duplicates: exact.length, candidates: list };
    }

    const normTarget = normalize(target);
    const normed = list.filter((item) => normalize(item) === normTarget);
    if (normed.length === 1) return { status: "normalized", value: normed[0], candidates: list };
    if (normed.length > 1) return { status: "ambiguous", candidates: normed };

    // 「上海」对「上海市」走这一级：一方是另一方的前缀，且全表只有一个这样的候选。
    const prefixed = list.filter((item) => {
      const norm = normalize(item);
      return norm.startsWith(normTarget) || normTarget.startsWith(norm);
    });
    if (prefixed.length === 1) return { status: "prefix", value: prefixed[0], candidates: list };
    if (prefixed.length > 1) return { status: "ambiguous", candidates: prefixed };

    return { status: "none", candidates: list };
  };

  /** 按点号 / 方括号路径读档案，`investors[0].name` 这种写法直接可用。 */
  const readPath = (root, path) => {
    if (!root || !path) return undefined;
    const parts = String(path)
      .replace(/\[(\d+)\]/g, ".$1")
      .split(".")
      .filter(Boolean);
    let node = root;
    for (const part of parts) {
      if (node == null) return undefined;
      node = node[part];
    }
    return node;
  };

  /**
   * 按同样的路径写值，缺的层自动补出来。
   *
   * 它存在的理由是把「照着站点选项改档案」这件事从「切到设置页手抄字符串再回来」
   * 变成「在面板上点一下」。前者是这套东西真正卡住的地方——候选明明就在手上，
   * 却要人去另一个页面誊写。
   */
  const writePath = (root, path, value) => {
    const parts = String(path || "")
      .replace(/\[(\d+)\]/g, ".$1")
      .split(".")
      .filter(Boolean);
    if (!parts.length) return root;
    const target = root && typeof root === "object" ? root : {};
    let node = target;
    for (let index = 0; index < parts.length - 1; index += 1) {
      const key = parts[index];
      const nextIsIndex = /^\d+$/.test(parts[index + 1]);
      if (node[key] == null || typeof node[key] !== "object") node[key] = nextIsIndex ? [] : {};
      node = node[key];
    }
    node[parts[parts.length - 1]] = value;
    return target;
  };

  AFY.util = {
    clean,
    isElement,
    normalize,
    writePath,
    delay,
    nextFrame,
    until,
    visible,
    disabled,
    chooseOne,
    readPath,
  };
})();
