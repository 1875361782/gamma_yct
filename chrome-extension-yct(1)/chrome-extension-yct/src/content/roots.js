// 页面遍历的地基：一次拿到「顶层文档 + 所有开放 shadow root + 所有同源 iframe 文档」。
//
// 为什么不靠 manifest 的 all_frames 各自为政：站点是 wujie 微前端，子应用挂在
// blob: iframe 里。Chrome 对 blob: 文档是否注入声明式 content script 并不可靠，
// 而 blob: 继承创建者的 origin，所以顶层脚本用 `iframe.contentDocument` 一定能进去。
// 于是引擎只有一个实例（顶层），子 frame 里的实例保持沉默——跨 frame 的消息编排
// 复杂度直接归零，字段标识也不会因为两个实例各标一遍而互相覆盖。
//
// 代价写在这里：**跨源 iframe 看不见**。本站业务只在 online.yct.sh.gov.cn 一个源上，
// 登录跳到 zwdt.sh.gov.cn 时本来就该交还控制权，所以这个代价是可接受的。真出现
// 跨源业务 frame，要改的是这里，不是引擎其他部分。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.dom) return;
  const { clean } = AFY.util;

  const MAX_ROOTS = 400;

  // 面板自己的宿主。**必须从遍历里排除**：它挂在 documentElement 上、内容在
  // 开放 shadow root 里，而 roots() 会穿透 shadow root——于是插件把自己的界面
  // 当成了页面的一部分。实测后果不只是脏数据：面板上那个「下一步」按钮会和站点的
  // 「下一步」一起被认成两个 next 动作，advance() 判「有多个可能的下一步」，
  // 从此永远拒绝翻页。诊断导出里这两个按钮并排出现，才看出来是这么回事。
  const PANEL_HOST_ID = "afy-panel-host";

  const sameOriginDocument = (frame) => {
    try {
      // 跨源访问会抛 SecurityError；blob:/about:blank 同源则正常返回。
      const doc = frame.contentDocument;
      return doc && doc.documentElement ? doc : null;
    } catch {
      return null;
    }
  };

  /**
   * 收集可遍历的根。顺序稳定：广度优先，先 shadow 后 iframe，
   * 这样字段的自动编号在同一页面上是可复现的。
   */
  const collect = (start) => {
    const list = [start || document];
    for (let index = 0; index < list.length && list.length < MAX_ROOTS; index += 1) {
      const root = list[index];
      let nodes;
      try {
        nodes = root.querySelectorAll("*");
      } catch {
        continue;
      }
      for (const node of nodes) {
        if (node.id === PANEL_HOST_ID) continue;
        if (node.shadowRoot && !list.includes(node.shadowRoot)) list.push(node.shadowRoot);
        if (node.tagName === "IFRAME" || node.tagName === "FRAME") {
          const doc = sameOriginDocument(node);
          if (doc && !list.includes(doc)) list.push(doc);
        }
        if (list.length >= MAX_ROOTS) break;
      }
    }
    return list;
  };

  /** 在所有根里查全部匹配。scope 传入时只在该子树（含其内部 shadow/iframe）里找。 */
  /**
   * 整页遍历加一层 50ms 缓存。
   *
   * 不加会二次方塌陷：collect() 要走遍全文档的每个元素找 shadow root 和同源 iframe，
   * 而 queryAll 每调一次就走一遍——展开一个下拉的过程里会调几十次。页面越大越慢，
   * 自检跑到后半段（结果表长到五十多行）时直接把用例拖过 20 秒超时。
   * 真实政务页比自检页大得多，这条更要命。
   *
   * 50ms 内 DOM 结构（shadow/iframe 的增减）几乎不会变，而选项的出现与否读的是
   * 元素本身，不受这层缓存影响。指定 scope 时不缓存——那种调用本来就很局部。
   */
  let cachedRoots = null;
  let cachedAt = 0;
  const roots = (start) => {
    if (start) return collect(start);
    const now = Date.now();
    if (cachedRoots && now - cachedAt < 50) return cachedRoots;
    cachedRoots = collect(null);
    cachedAt = now;
    return cachedRoots;
  };

  const inPanel = (el) => Boolean(el?.closest?.(`#${PANEL_HOST_ID}`)) || el?.getRootNode?.()?.host?.id === PANEL_HOST_ID;

  const queryAll = (selector, scope) => {
    const found = [];
    for (const root of roots(scope)) {
      try {
        found.push(...root.querySelectorAll(selector));
      } catch {
        /* 选择器非法时不让一个坏选择器带崩整轮遍历 */
      }
    }
    // 双保险：调用方可能直接把面板内部的节点当 scope 传进来。
    return found.filter((el) => !inPanel(el));
  };

  const query = (selector, scope) => queryAll(selector, scope)[0] || null;

  /** 按插件自己打的标记找回元素。标记是跨轮次唯一可靠的句柄。 */
  const byFieldId = (id) => {
    if (!id) return null;
    return query(`[data-afy-field="${CSS.escape(String(id))}"]`);
  };

  /**
   * 元素相对顶层文档的位置描述，用于留痕与用户核对。
   * 只记结构信息，不记值。
   */
  const framePathOf = (el) => {
    const path = [];
    let doc = el?.ownerDocument;
    let guard = 0;
    while (doc && doc !== document && guard < 10) {
      const frame = doc.defaultView?.frameElement;
      if (!frame) break;
      path.unshift(clean(frame.getAttribute("name") || frame.id || frame.tagName.toLowerCase()));
      doc = frame.ownerDocument;
      guard += 1;
    }
    return path;
  };

  /**
   * 该元素是否在某个 shadow root 内。
   *
   * 同样不能用 `instanceof ShadowRoot`：iframe 子文档里的 shadow root 属于另一套
   * 内置构造器，跨文档判定恒为 false。改看内部槽——DocumentFragment 的 nodeType
   * 是 11，带 host 的那个就是 shadow root。
   */
  const inShadow = (el) => {
    const root = el?.getRootNode?.();
    return Boolean(root && root.nodeType === 11 && root.host);
  };

  AFY.dom = { roots, queryAll, query, byFieldId, framePathOf, inShadow, inPanel, MAX_ROOTS, PANEL_HOST_ID };
})();
