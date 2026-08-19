// 自定义下拉：展开、把选项归属到正确的弹层、枚举、择一、收起。
//
// 三件必须做对的事：
//
// 一、**展开**。这里踩过两次，两次的教训相反，所以现在是「多策略逐个试 + 每次试完
//     验证」，而不是认准一种姿势：
//     - 单个 `el.click()` 打不开 Element 的 select：它只派发 click，没有 mousedown，
//       而组件常常听的是 mousedown。
//     - 反过来只往 wrapper 派发也不行：**事件不会从祖先向后代传播**。Element 各版本
//       的监听点不一样（有的在根 `.el-select`，有的在内层 `.el-input` / `.select-trigger`），
//       往最外层派发时，监听在内层的那一版收不到任何东西，表现就是「点了没反应」。
//     从**内层往上冒泡**能同时覆盖两种绑定，所以它是第一策略；wrapper 直派和键盘
//     打开作为后备。每试一次就查一次弹层，成了就停。
//
// 二、**归属**。政务下拉的选项来自接口（本站是 dicname / getDistrictDesc /
//     getOrgForm / getIndustryDesc 三支），点开才请求、才渲染，而且弹层被传送到
//     <body> 底下，DOM 上和触发它的输入框毫无父子关系。直接「扫全文档取可见选项」
//     会把上一个下拉的残留、以及背景页里其它已展开的弹层一起读进来。所以这里做
//     三级归属回退，其中差集法是最可靠的一级。
//
// 三、**收起**。枚举完必须关掉：留着展开的弹层会遮住后续字段的可见性判定，也会让
//     下一个下拉读到上一个的残留。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.selects) return;
  const { clean, delay, visible, disabled, chooseOne } = AFY.util;
  const { dom } = AFY;

  const OPTION_SELECTOR = [
    "[role='option']",
    ".el-select-dropdown__item",
    ".ant-select-item-option",
    ".el-cascader-node",
    // 一窗通「组织形式」实测：弹层里的候选是 **Element Plus 的 radio**，不是 li。
    //   <label class="el-radio"><span class="el-radio__input">
    //     <span class="el-radio__inner"></span><input type="radio" class="el-radio__original">
    //   </span><span class="el-radio__label">有限公司</span></label>
    // 认包装（label.el-radio）而不是里面那个 input：Element Plus 把 input 藏起来了
    // （零尺寸/透明），点它常常毫无反应，而「毫无反应」在回读里长得和「站点拒绝了
    // 这个值」一模一样。
    ".el-radio",
    ".el-checkbox",
    ".ant-radio-wrapper",
    ".ant-checkbox-wrapper",
  ].join(",");

  const PANEL_SELECTOR = [
    ".el-select-dropdown",
    ".el-cascader__dropdown",
    ".el-autocomplete-suggestion",
    ".ant-select-dropdown",
    ".el-popover",
    ".el-popper",
  ].join(",");

  // 弹层里没有标准选项元素时的兜底（组织形式那类自绘 popover）。
  const LOOSE_OPTION_SELECTOR = "li, td, .cell, .item, .option, [class*='item']";

  // 弹层里的**静态步骤提示**，长得和选项一模一样，但点它没有任何意义。
  //
  // 这一条是踩出来的：组织形式的弹层里写着「1. 请选择企业类型 / 2. 请选择组织形式」，
  // 兜底选择器把「2. 请选择组织形式」当成了唯一的选项报给人，于是界面上出现了
  // 「站点实际给的选项：2. 请选择组织形式」这种毫无意义的建议。更早一次是把同一段
  // 提示误读成了级联前置，整个排查方向都跑偏了。
  //
  // 认出它还有第二个用处：弹层里只剩提示、没有真选项，本身就说明**这是一个分步
  // 选择器，而上一步还没做**——这正是要告诉人的话。
  const HINT_TEXT = /^\s*(\d+\s*[.、,，)）]\s*)?(请|需)(先|你|您)?(选择|选取|输入|填写|指定)/;
  const isHintOption = (el) => HINT_TEXT.test(optionLabelOf(el));

  // 空态/加载态的那一行字。文本叶子兜底会把「无数据」也当成一个候选——
  // 于是「开了但没数据」被报成「有一个选项叫无数据」，比不报还糟。
  // 它和提示行的区别：提示行说明「上一步没做」，空态说明「站点没给数据」，
  // 所以只剔除、不计入 hints，免得把 empty 误判成 needs_prior_step。
  const EMPTY_STATE_CLASS = [
    ".el-select-dropdown__empty",
    ".el-select-dropdown__loading",
    ".el-cascader-menu__empty-text",
    ".el-empty",
    ".ant-empty",
  ].join(",");
  const EMPTY_STATE_TEXT =
    /^(无数据|暂无数据|没有数据|无匹配数据|无匹配项|暂无匹配|加载中|正在加载|查询中|no\s*data|no\s*match|loading)[.。…\s]*$/i;
  const isEmptyState = (el) =>
    Boolean(el.matches?.(EMPTY_STATE_CLASS) || el.closest?.(EMPTY_STATE_CLASS)) ||
    EMPTY_STATE_TEXT.test(optionLabelOf(el));

  // 展开后等选项渲染的节奏。与填写阶段的重试间隔保持一致，不让两处对「多久算慢」
  // 各有一套判断。
  const OPTION_WAIT_STEPS = [400, 800, 1200];

  const optionLabelOf = (el) =>
    clean(el?.innerText || el?.textContent || el?.getAttribute?.("title") || "");

  const optionDisabled = (el) =>
    disabled(el) ||
    el.classList.contains("is-disabled") ||
    el.classList.contains("ant-select-item-option-disabled");

  const wrapperOf = (el) =>
    el.closest(".el-select, .ant-select, .el-cascader, .el-date-editor, .ant-picker, .el-popover__reference") ||
    el.closest("[role='combobox']") ||
    el;

  /** 一次完整的指针序列。只发 click 的话，监听 mousedown 的组件收不到。 */
  const fireMouse = (target) => {
    if (!target) return;
    const init = { bubbles: true, cancelable: true, composed: true, view: window, button: 0 };
    try {
      target.dispatchEvent(new PointerEvent("pointerdown", { ...init, pointerType: "mouse" }));
    } catch {
      /* 老内核没有 PointerEvent，跳过即可 */
    }
    target.dispatchEvent(new MouseEvent("mousedown", init));
    try {
      target.dispatchEvent(new PointerEvent("pointerup", { ...init, pointerType: "mouse" }));
    } catch {
      /* 同上 */
    }
    target.dispatchEvent(new MouseEvent("mouseup", init));
    target.dispatchEvent(new MouseEvent("click", init));
  };

  const fireKey = (target, key, keyCode) => {
    for (const type of ["keydown", "keyup"]) {
      target.dispatchEvent(new KeyboardEvent(type, { key, keyCode, bubbles: true, cancelable: true }));
    }
  };

  /**
   * 点一个节点：可信通道（CDP，isTrusted=true）优先，不可用或没点成就回退合成
   * 指针序列。回退发生在**同一次调用里**——每一次点击都要有着落，不能让整轮
   * 识别去赌通道状态。返回实际走的通道，供留痕与诊断。
   */
  /** 候选如果是 radio/checkbox（组织形式那类），把里面那个隐藏的 input 取出来。 */
  const boxInside = (node) =>
    node?.matches?.("input[type=radio],input[type=checkbox]")
      ? node
      : node?.querySelector?.("input[type=radio],input[type=checkbox]") || null;

  /**
   * 这个候选选中了吗。
   *
   * 一窗通实测的 HTML 把「选中」同时写在三个地方：
   *   <label class="el-radio is-checked" aria-checked="true">
   *     <span class="el-radio__input is-checked"><input type="radio" …>
   * 三者理论上同步，但组件库各版本并不保证。只认 `input.checked` 一处，就可能
   * 「明明选中了却报点击没生效」，反过来也可能漏判。**任一处成立就算选中**——
   * 这里宁可宽松：真正的判据是回读，这一层只用来尽早发现「压根没点上」。
   */
  const isChosen = (node) => {
    if (!node) return null;
    const box = boxInside(node);
    const marked =
      node.classList?.contains("is-checked") ||
      node.getAttribute?.("aria-checked") === "true" ||
      Boolean(node.querySelector?.(".is-checked, [aria-checked='true']"));
    if (box) return Boolean(box.checked) || marked;
    // 连 input 都没有（li 型候选）→ 只能看标记；标记也没有就返回 null（无从判断）。
    return marked ? true : null;
  };

  const clickNode = async (node) => {
    if (AFY.trusted?.enabled()) {
      let result = await AFY.trusted.click(node);
      // 出了视口就先滚进来再试一次。分步弹层的第二级常常在 popover 的可滚动区
      // 底部，可信点击的命中预检会直接拒绝——而回退的合成事件对 radio 又不生效，
      // 于是「点了、也报成功、就是没选中」。滚一下能把绝大多数这种情况救回来。
      if (!result.ok && (result.reason === "out_of_viewport" || result.reason === "occluded")) {
        try {
          node.scrollIntoView({ block: "center", inline: "center" });
        } catch {
          /* 滚不动就算了，下面还有合成兜底 */
        }
        await AFY.util.nextFrame();
        result = await AFY.trusted.click(node);
      }
      if (result.ok) return { via: "cdp" };
      // `interrupted` = 指针序列已经落地一部分（多半是 mousedown 已经进去了、
      // 连接在 mouseup 之前断掉）。这是「回退合成事件」唯一的例外：再补一整套
      // 合成序列，页面就收到了两次 mousedown——对开关型触发器等于点开又点关，
      // 比不补更糟。这一下有没有生效，交给调用方本来就有的验证去判：展开路径
      // 每一步都查弹层，选项路径有候选集变化判定和回读核验。
      if (result.reason === "interrupted") {
        AFY.trusted.noteFallback();
        return { via: "interrupted" };
      }
      AFY.trusted.noteFallback();
    }
    fireMouse(node);
    // radio / checkbox 型候选必须再补一下。
    //
    // 合成的 `dispatchEvent(new MouseEvent('click'))` **不触发默认行为**：点在
    // `label.el-radio` 上，里面那个隐藏的 input 根本不会被选中，于是「点了、
    // chooseSteps 也报成功、就是没选中」——回读为空报 empty_after_write，
    // 而弹层里那一项看着就是没变。元素自己的 `.click()` 方法走的是 UA 的激活
    // 路径，label 的关联激活会发生，Element Plus 也就收到了 change。
    const box = boxInside(node);
    if (box && !box.checked) {
      node.click?.();
      if (!box.checked) box.click();
    }
    return { via: "synthetic" };
  };

  // 展开策略，按可信度排序。每试一个就验证一次，成了就停。
  //
  // `self` 排第一是因为**冒泡向上**：从内层 input 发出的事件会依次经过 .el-input、
  // .select-trigger、.el-select，两种绑定位置都能收到。反过来往 wrapper 直派只覆盖
  // 一种，而这恰恰是「名称区划点了没反应」的原因。
  const OPEN_STRATEGIES = [
    { name: "self", run: (el) => { el.focus?.(); fireMouse(el); } },
    { name: "trigger", run: (el) => fireMouse(el.closest(".select-trigger, .el-tooltip__trigger, .el-input") || el) },
    { name: "wrapper", run: (el) => fireMouse(wrapperOf(el)) },
    // Element 的 select 支持键盘展开。前三种被 pointer-events / @click.stop 挡住时，
    // 这条常常还通。
    { name: "keyboard", run: (el) => { el.focus?.(); fireKey(el, "ArrowDown", 40); fireKey(el, "Enter", 13); } },
  ];

  // 可信通道可用时排在最前：CDP 点击落在内层控件上，真实事件照样**冒泡向上**，
  // 同时覆盖两种绑定位置，还不挑 isTrusted。点不成（遮挡、出视口、通道断了）
  // 就抛错，由策略循环顺位落到合成事件那几级——梯子结构与 RPA 厂商的
  // 「点击方式降级」同构，这是十年行业验证过的形状。
  // 策略抛出它，表示「这一下可能已经生效了一半，别再往同一个触发器上补事件」——
  // openAndResolve 收到它就停止继续试后面的姿势，只去检查弹层到底开没开。
  const INTERRUPTED = "afy_interrupted";

  const CDP_STRATEGY = {
    name: "cdp",
    run: async (el) => {
      el.focus?.();
      const result = await AFY.trusted.click(el);
      if (!result.ok) {
        // 记一次回退。漏记的话诊断里 fallbacks 恒为 0，看起来像「可信通道一路顺畅」，
        // 而实际上每个下拉都在退回合成事件——排障时最容易被这种假象带偏。
        AFY.trusted.noteFallback();
        throw new Error(result.reason === "interrupted" ? INTERRUPTED : result.reason || "cdp_unavailable");
      }
    },
  };

  // 可信键盘：合成 keydown 同样带 isTrusted=false，会被检查它的组件忽略。
  //
  // **只发 ArrowDown，绝不发 Enter。** Element 的 select 对 ArrowDown 就会展开，
  // 而 Enter 会把当前高亮项**选中**——这一步的任务只是「打开」，选哪个是后面
  // chooseOne 三级匹配的事。合成 keyboard 策略里带 Enter 是历史遗留（合成事件常被
  // 忽略，所以没出过事），换成可信事件后它会真的生效：展开的同时替用户选中列表
  // 第一项，而回读读到的正是这个错值，属于最坏的一类失败。
  const CDP_KEYBOARD_STRATEGY = {
    name: "cdp-keyboard",
    run: async (el) => {
      el.focus?.();
      const down = await AFY.trusted.pressKey(el, "ArrowDown");
      if (!down.ok) {
        AFY.trusted.noteFallback();
        throw new Error(down.reason === "interrupted" ? INTERRUPTED : down.reason || "cdp_unavailable");
      }
    },
  };

  // 顺序即降级梯子：可信点击 → 合成指针姿势 → 可信键盘 → 合成键盘。
  // 键盘类排在指针类之后，因为它更容易有副作用（Enter 可能提交表单）。
  //
  // 按**名字**分组而不是按下标切片：以后往 OPEN_STRATEGIES 里加一条指针策略时，
  // 写死的 slice(0,3) 会把它悄悄丢掉，而这种「少试了一种姿势」的退化在诊断里
  // 长得和「组件打不开」一模一样。
  const KEYBOARD_STRATEGIES = new Set(["keyboard"]);
  const openStrategies = () => {
    if (!AFY.trusted?.enabled()) return OPEN_STRATEGIES;
    const pointer = OPEN_STRATEGIES.filter((item) => !KEYBOARD_STRATEGIES.has(item.name));
    const keyboard = OPEN_STRATEGIES.filter((item) => KEYBOARD_STRATEGIES.has(item.name));
    return [CDP_STRATEGY, ...pointer, CDP_KEYBOARD_STRATEGY, ...keyboard];
  };

  // 单发版本：只用最可信的那一种。**不能**把四种一口气全发出去——
  // 前一种已经把弹层打开了，后一种就会把它切回关闭。
  const openWidget = (el) => {
    try {
      OPEN_STRATEGIES[0].run(el);
    } catch {
      /* 忽略 */
    }
  };

  /**
   * 收起弹层。传入触发控件时会做最后一招：再点它一次把弹层切回关闭。
   *
   * 为什么要这一招：本站「组织形式」的弹层既不吃 Escape 也不吃外部点击。
   * 留一个开着的弹层，下一次操作会因为「它在展开之前就可见」而判成 no_panel——
   * 探查组织形式那次就是这么失败的，而真正的原因是上一次失败没把它关掉。
   */
  const closePanels = (trigger) => {
    const doc = document;
    for (const type of ["keydown", "keyup"]) {
      doc.body?.dispatchEvent(new KeyboardEvent(type, { key: "Escape", keyCode: 27, bubbles: true }));
    }
    // 点一下页面上没有语义的角落让组件失焦；刻意不用 body.click()——弹窗遮罩上的
    // 点击在某些站点等同于「取消」。
    for (const type of ["mousedown", "mouseup", "click"]) {
      doc.documentElement?.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    }
    // 这里曾经有一招「关不掉就再点一次触发器把它切回去」。**已撤销**：
    // 触发器是个开关，弹层本来就关着时这一点反而会把它打开，
    // 于是下一个字段一上来就看到一个不属于它的弹层。自检里下拉A 就是被这样点开的。
    // 关不掉的情况改由 resolvePanel 的 stale 兜底来处理，那条路只读不点。
    void trigger;
  };

  // 点上去会有副作用的东西。挑「安全空白点」时一律避开。
  const INTERACTIVE_AT_POINT =
    "a,button,input,select,textarea,label,li,summary,[role],[onclick],[tabindex]," +
    ".el-radio,.el-checkbox,.el-button,.el-select,.el-input,.el-tabs__item,.el-menu-item";

  /**
   * 在视口里找一个「点下去什么也不会发生」的落点。
   *
   * 给可信关闭用：本站的 orgDescPopper 既不吃 Escape 也不吃合成的外部点击，
   * 唯一还通的是**一次真实的页面外部点击**——而那需要一个坐标，不是元素句柄。
   *
   * 三条排除：弹层内部（点进去等于在选选项）、可交互元素（点了会有副作用）、
   * 插件自己的面板。找不到就返回 null，宁可不点。
   */
  const findSafeSpot = () => {
    const width = window.innerWidth;
    const height = window.innerHeight;
    if (!width || !height) return null;
    for (let gy = 0.12; gy <= 0.92; gy += 0.16) {
      for (let gx = 0.04; gx <= 0.96; gx += 0.14) {
        const x = Math.round(width * gx);
        const y = Math.round(height * gy);
        const hit = document.elementFromPoint(x, y);
        if (!hit) continue;
        if (hit.closest(PANEL_SELECTOR)) continue;
        if (hit.closest(INTERACTIVE_AT_POINT)) continue;
        if (AFY.dom.inPanel(hit)) continue;
        return { x, y, tag: hit.tagName };
      }
    }
    return null;
  };

  /**
   * 关不掉的弹层，最后一招：用**一次真实的空白处点击**。
   *
   * 为什么非可信不可：合成的外部点击这个 popover 根本不理（实测），而它是
   * Element Plus 的 clickoutside 那一套——真实点击就吃。这是可信通道除了
   * 「点得中」之外的第二个价值：它能收拾合成事件收拾不了的残局。
   */
  /**
   * 收起弹层，合成关不掉就用一次可信的空白点击。
   * 枚举/探查这类「开了就该收干净」的收尾必须走这条，否则弹层会赖到下一个字段。
   */
  const closePanelsHard = async (trigger) => {
    closePanels(trigger);
    if (!visiblePanels().length) return true;
    return closeByTrustedClick();
  };

  const closeByTrustedClick = async () => {
    if (!AFY.trusted?.enabled()) return false;
    const spot = findSafeSpot();
    if (!spot) return false;
    const result = await AFY.trusted.clickPoint(spot.x, spot.y);
    if (!result.ok) return false;
    const gone = await AFY.util.until(() => (visiblePanels().length ? null : true), {
      timeout: 600,
      interval: 150,
    });
    return Boolean(gone);
  };

  /**
   * 展开之前先确保没有残留弹层。openAndResolve 靠「新变可见」来归属弹层，
   * 上一轮留下的弹层会让本轮直接判 no_panel——而真正的原因是上一次没关掉。
   */
  const clearStalePanels = async (el) => {
    if (!visiblePanels().length) return { cleared: true, stale: [] };
    closePanels(el);
    // 等待窗口刻意压到 600ms：这是每次展开都要付的固定成本，给多了会把整轮识别拖垮。
    const gone = await AFY.util.until(() => (visiblePanels().length ? null : true), {
      timeout: 600,
      interval: 200,
    });
    if (gone) return { cleared: true, stale: [] };
    // 合成事件收不掉，换可信的真实点击再试一次。这一步专治 orgDescPopper：
    // 它赖在页面上会让**下一次**操作直接报 blocked_by_stale_panel，
    // 表现就是「第一次探查成功、第二次开始一直失败」。
    if (await closeByTrustedClick()) return { cleared: true, stale: [], via: "trusted_click" };
    // 还是关不掉。**这不是「打不开」**，是上一个字段的弹层赖着不走——
    // 两者的下一步完全不同，报成同一个 no_panel 会让人去查一个根本不存在的问题。
    return { cleared: false, stale: visiblePanels().map((panel) => panelTextOf(panel).slice(0, 80)) };
  };

  // 专属的下拉弹层容器。**空的下拉弹层也是弹层**——这一条是分清
  // 「压根没打开」和「打开了但站点没给选项（无数据 / 未登录）」的关键。
  // 之前要求弹层里必须有选项元素才算数，于是「无数据」的下拉被误报成 no_panel，
  // 人就会一直去查「为什么点不开」，而真正该查的是「为什么没数据」。
  const DROPDOWN_PANEL = [
    ".el-select-dropdown",
    ".el-cascader__dropdown",
    ".el-cascader-panel",
    ".ant-select-dropdown",
    ".el-autocomplete-suggestion",
  ].join(",");

  // 「有文字」就算弹层。自绘的 popover 里可能一个 li/.item 都没有——上一版要求
  // 必须命中选项选择器，于是这种弹层连「开没开」都判不出来；那一次能认出来，纯粹是
  // 因为里面恰好有一条提示行碰巧命中了兜底选择器。
  // 放宽的风险很小：真正会被点中的，只有文案命中档案值的那一个元素。
  const visiblePanels = () =>
    dom
      .queryAll(PANEL_SELECTOR)
      .filter(visible)
      .filter(
        (panel) =>
          panel.matches(DROPDOWN_PANEL) ||
          panel.querySelector(OPTION_SELECTOR) ||
          panel.querySelector(LOOSE_OPTION_SELECTOR) ||
          // 刻意用 textContent 而不是 innerText：innerText 会强制回流，而这个判断
          // 在每轮轮询里对每个候选弹层都要跑一次，用 innerText 能把识别拖成几十秒。
          Boolean(clean(panel.textContent)),
      );

  /** 弹层里的可见文字，用于把「无数据 / 加载中 / 请先选择企业类型」如实报给人。 */
  const panelTextOf = (panel) => clean(panel?.innerText).slice(0, 300);

  /**
   * 弹层里的候选项。返回值里 `hints` 是被剔掉的静态提示行——它不是噪音，
   * 而是「这是分步选择器、上一步还没做」的证据，要一路带到界面上。
   */
  // 「这是表单行，不是选项」的判据。
  //
  // **radio / checkbox 刻意不算**：一窗通「组织形式」的弹层实测下来，候选本身就是
  // 用 Element Plus 的 radio 实现的。把「含有 input」一律当成表单行，等于用防误判的
  // 规则把真正的候选全挡在外面——填写时弹层明明开着、一个候选都识别不出来，
  // 回读为空报 empty_after_write，人完全看不出是哪一步断的。
  // 只有**文本输入类**控件才是「这是一行要填的字段」的标志。
  const HAS_CONTROL =
    "input:not([type=radio]):not([type=checkbox]), textarea, select, [contenteditable='true']";

  /**
   * 最后一层兜底：按**文本叶子节点**找候选，不再赌标签名。
   *
   * 实测原因：一窗通「组织形式」弹层里的「内资企业 / 有限公司 / 股份公司 …」既不是
   * `li` 也不是 `.item`，选择器兜底一个都捞不到，界面上只剩一条「2. 请选择组织形式」，
   * 看起来就像「站点没给选项」。政务站点的自绘弹层用什么标签是没法穷举的，
   * 但「一个可见的、自己带文字的、没有更深文字后代的元素」这个特征是稳的。
   *
   * 安全性靠别处保证：真正会被点中的，只有文案精确/归一化/唯一前缀命中档案值的那一个。
   */
  const looseCandidates = (panel) => {
    const out = [];
    const walk = (node, depth) => {
      if (out.length >= 400 || depth > 12) return;
      for (const child of node.children) {
        if (!visible(child)) continue;
        // 带**文本输入**控件的是表单行，不是选项（radio/checkbox 不算，见 HAS_CONTROL）。
        if (child.querySelector(HAS_CONTROL)) {
          walk(child, depth + 1);
          continue;
        }
        const text = optionLabelOf(child);
        if (!text) {
          walk(child, depth + 1);
          continue;
        }
        // 还有带文字的后代 → 它是容器，继续往下钻，别把整块当成一个选项。
        const deeper = [...child.children].some((item) => visible(item) && optionLabelOf(item));
        if (deeper) {
          walk(child, depth + 1);
          continue;
        }
        out.push(child);
        if (out.length >= 400) return;
      }
    };
    walk(panel, 0);
    return out;
  };

  const optionsIn = (panel) => {
    const notHint = (list) => {
      const hints = list.filter(isHintOption).map(optionLabelOf);
      return {
        items: list.filter((item) => !isHintOption(item) && !isEmptyState(item)),
        hints,
      };
    };

    const strict = [...panel.querySelectorAll(OPTION_SELECTOR)].filter(visible);
    if (strict.length) {
      const split = notHint(strict);
      if (split.items.length) return { items: split.items, hints: split.hints, loose: false };
    }

    const loose = [...panel.querySelectorAll(LOOSE_OPTION_SELECTOR)]
      .filter(visible)
      .filter((item) => optionLabelOf(item) && !item.querySelector(LOOSE_OPTION_SELECTOR))
      // 兜底选择器里的 [class*='item'] 会捞到 .el-form-item 这类**表单行**。
      // 把它当选项点下去等于在弹窗里乱点，所以带输入控件的一律不算选项。
      .filter((item) => !item.querySelector(HAS_CONTROL));
    const split = notHint(loose);
    if (split.items.length) return { items: split.items, hints: split.hints, loose: true };

    // 选择器全都够不着时才走文本叶子。它最宽，所以放在最后。
    const leaves = notHint(looseCandidates(panel));
    return {
      items: leaves.items,
      // 提示行合并两处来源：选择器那一轮也可能只捞到提示。
      hints: [...new Set([...split.hints, ...leaves.hints])],
      loose: true,
    };
  };

  /** 弹层里当前真正可点的候选（可见、未禁用、有文案、不是提示行）。文档序。 */
  const selectableIn = (panel) =>
    optionsIn(panel).items.filter((item) => !optionDisabled(item) && optionLabelOf(item));

  /** 这个块是不是「一个步骤」——它直接带着「1. 请选择…」这样的提示行。 */
  const stepHeadingIn = (node) =>
    [...(node.children || [])].find(
      (child) => !child.children.length && HINT_TEXT.test(clean(child.textContent)),
    ) || null;

  /** 候选属于哪个步骤块：从它自己往上找第一个带步骤提示行的祖先。 */
  const stepContainerOf = (item, panel) => {
    let node = item.parentElement;
    let guard = 0;
    while (node && node !== panel && guard < 12) {
      if (stepHeadingIn(node)) return node;
      node = node.parentElement;
      guard += 1;
    }
    return null;
  };

  /**
   * 按**站点自己的结构**把候选分级，而不是靠「哪些元素是新出现的」。
   *
   * 为什么必须这样：一窗通「组织形式」点完第一级之后，Element Plus 会重渲染整个
   * radio-group——12 个 label 全部换成新的 DOM 节点。于是「新出现的候选」这个差集
   * 判据把**所有 14 项**都算成新的，`chooseOne("有限公司", …)` 在三个同名项里按约定
   * 取了第一个，又点回第一级那个已经选中的「有限公司」。验证 checked 当然通过
   * （它本来就选中），循环走完、报成功，而第二级从头到尾没被碰过——最后只有回读
   * 说一句「值是空的」。
   *
   * 站点的 DOM 里其实明写着层级：<ul><li><p>1. 请选择企业类型</p>…</li>
   *                                 <li><p>2. 请选择组织形式</p>…</li></ul>
   * 按步骤提示行分组，第 N 级就只在第 N 组里找。这是站点自己的表达，比元素身份稳。
   *
   * 结构对不上（比如区划那种多列级联，没有步骤提示行）就返回 null，退回差集法。
   */
  const stepGroups = (panel) => {
    const items = selectableIn(panel);
    if (!items.length) return null;
    const groups = new Map();
    for (const item of items) {
      const container = stepContainerOf(item, panel);
      // 有候选不属于任何步骤块 → 这不是「带步骤提示的分步结构」，别硬套。
      if (!container) return null;
      if (!groups.has(container)) groups.set(container, []);
      groups.get(container).push(item);
    }
    if (!groups.size) return null;
    // **只有一组也照样返回**：那说明下一级还没渲染出来（它的候选要等接口回来），
    // 而不是「这不是分步结构」。这个区别很要命——判成后者就会拿上一级的候选去
    // 匹配下一级，同名项一撞就点回上一级。
    const containers = [...groups.keys()].sort((a, b) =>
      a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1,
    );
    return containers.map((container) => groups.get(container));
  };

  /**
   * 按分组把候选收窄到某个标题名下。
   *
   * 为什么必须有这一步：一窗通「组织形式」第一步的候选是
   *   内资企业 › 有限公司 / 股份公司 / 非公司企业法人 / …
   *   外资企业 › 有限公司 / 股份公司 / …
   * 「有限公司」在两个组下各有一个。不按组收窄，chooseOne 只能判歧义然后停下来——
   * 而用户要的正是「内资企业里的那个有限公司」。
   *
   * 三级回退，都基于文档序的候选表：
   * 1) 标题的父容器里的候选（分组各自成块时最准）；
   * 2) 文档序上从标题往后，到**下一个同类元素**（同 tag + 同 class）为止——
   *    扁平列表里两个组标题通常长得一样，这条正好卡在下一组开头；
   * 3) 标题之后的全部。
   */
  const scopeUnderHeader = (header, all) => {
    if (!header || !all.length) return all;
    const index = all.indexOf(header);

    const inParent = all.filter((item) => item !== header && header.parentElement?.contains(item));
    if (inParent.length && inParent.length < all.length) return inParent;

    if (index < 0) return all;
    const sameKind = (el) => el.tagName === header.tagName && el.className === header.className;
    const bounded = [];
    for (let cursor = index + 1; cursor < all.length; cursor += 1) {
      if (sameKind(all[cursor])) break;
      bounded.push(all[cursor]);
    }
    if (bounded.length && bounded.length < all.length) return bounded;

    const after = all.slice(index + 1);
    return after.length ? after : all;
  };

  const allVisibleOptionElements = () => new Set(dom.queryAll(OPTION_SELECTOR).filter(visible));

  /**
   * 元素在**顶层视口**里的矩形。弹层被传送到 body（顶层文档），而触发器常常在
   * wujie 子应用的 blob: iframe 里——不换算到同一个坐标系，两者的位置根本没法比。
   */
  const rectInTop = (element) => {
    if (!element?.getBoundingClientRect) return null;
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    let dx = 0;
    let dy = 0;
    let doc = element.ownerDocument;
    let guard = 0;
    while (doc && doc !== document && guard < 10) {
      const frame = doc.defaultView?.frameElement;
      if (!frame) return null;
      const frameRect = frame.getBoundingClientRect();
      dx += frameRect.left + frame.clientLeft;
      dy += frameRect.top + frame.clientTop;
      doc = frame.ownerDocument;
      guard += 1;
    }
    if (doc !== document) return null;
    return {
      left: rect.left + dx,
      top: rect.top + dy,
      right: rect.right + dx,
      bottom: rect.bottom + dy,
      width: rect.width,
      height: rect.height,
    };
  };

  /**
   * 这个弹层是不是挂在这个触发器上的。
   *
   * 判据用**几何锚定**而不是「新出现的」：Element Plus 的 popper 定位在触发器
   * 正上/正下方，水平投影必然有重叠。这比按文案认领可靠——相邻两个区划下拉的
   * 候选高度重合，按文案会认错人；也比按新旧可靠——弹层本来就开着（上一步枚举
   * 没关掉）是本站的常态，按新旧会把自己的弹层判成别人的。
   */
  const isAnchoredTo = (panel, el) => {
    const p = rectInTop(panel);
    const t = rectInTop(el);
    if (!p || !t) return false;
    const overlapX = Math.min(p.right, t.right) - Math.max(p.left, t.left);
    if (overlapX <= 0) return false;
    // 弹层在触发器上方或下方，间距不超过触发器高度的两倍（popper 的箭头留白）。
    const gap = p.top >= t.bottom ? p.top - t.bottom : t.top - p.bottom;
    return gap <= Math.max(48, t.height * 2);
  };

  /**
   * 把弹层归属到这个控件。三级回退，越靠前越可信。
   *
   * @param {Element} el 触发控件
   * @param {Set<Element>} before 展开之前就已经可见的选项元素
   */
  const resolvePanel = (el, before, beforePanels, { allowStale = false, targetHint = "" } = {}) => {
    // 一级：无障碍关联。站点给了 aria-controls/aria-owns 时这是确定性的。
    const owned = clean(el.getAttribute("aria-controls") || el.getAttribute("aria-owns"));
    if (owned) {
      for (const id of owned.split(/\s+/)) {
        const panel = dom.query(`#${CSS.escape(id)}`);
        if (panel && visible(panel)) return { panel, via: "aria" };
      }
    }

    // 二级：差集。展开前后新出现的选项元素，必定属于刚展开的这一个。
    const after = allVisibleOptionElements();
    const fresh = [...after].filter((item) => !before.has(item));
    if (fresh.length) {
      const panel = fresh[0].closest(PANEL_SELECTOR);
      if (panel) return { panel, via: "diff" };
    }

    // 三级：**刚变可见**的弹层。Element Plus 复用同一个 popper 实例、只替换内容时，
    // 选项元素可能是同一批 DOM（差集为空），但弹层本身是从隐藏变可见的。
    //
    // 刻意不退回「取最后一个可见弹层」：上一个下拉没收干净时，那样会拿着别人的弹层
    // 去点选项——选错一个区划就是一次错误申报。宁可如实报 no_panel。
    const panels = visiblePanels().filter((panel) => !beforePanels.has(panel));
    if (panels.length) return { panel: panels[panels.length - 1], via: "appeared" };

    // 四级：**按目标文案认领**。诊断（blocked_by_stale_panel）实锤过这样一幕：
    // 组织形式的弹层早就开着、目标选项就在里面，旁边还挂着一个关不掉的名称区划
    // 下拉——本站两种弹层都不吃 Escape 和外部点击。按「恰好一个已开弹层」的
    // 老规则只能拒绝；而「哪个弹层里有我要选的字」是更直接的判据。
    // 仍然要求**唯一命中**：目标出现在多个弹层里就不认，绝不拿着别人的弹层点选项。
    //
    // 但认领分两种，严格程度不同：
    // - 循环内（allowStale=false）：**只认新出现的弹层**。上一个字段留下的弹层里
    //   如果碰巧也有「上海市」这三个字（相邻的两个区划下拉太容易撞），在本控件的
    //   弹层还没渲染出来的那 120ms 里就会被认领，于是在别人的弹层上点选项——
    //   本字段回读会发现没填上，而被误点的那个字段已经被悄悄改掉，回读体系根本
    //   覆盖不到这次跨字段误伤。
    // - 收尾（allowStale=true）：四种展开姿势都试过了，弹层不会再新开，这时才
    //   允许认领本来就开着的那个。
    if (targetHint) {
      const open = visiblePanels();
      // 4a：新出现的弹层里按文案认领——最保险的一档。
      const fresh = open.filter((panel) => !beforePanels.has(panel));
      const freshClaimed = fresh.filter((panel) => clean(panel.textContent).includes(targetHint));
      if (freshClaimed.length === 1) return { panel: freshClaimed[0], via: "content" };

      // 4b：**本来就开着**的弹层，要同时满足「含目标文案」和「几何上锚定在这个
      // 触发器上」才认。曾经只按「新出现」筛，结果把 observe 阶段枚举留下的、
      // 属于本控件自己的弹层判成了别人的——六种展开姿势轮流去点那个开关，
      // 弹层在开关之间摆动，收尾时正好是关的，于是报「六种全试过还是打不开」。
      // 加上几何判据之后，自己的弹层认得回来，相邻字段的弹层也不会被误认。
      const anchored = open.filter(
        (panel) => clean(panel.textContent).includes(targetHint) && isAnchoredTo(panel, el),
      );
      if (anchored.length === 1) return { panel: anchored[0], via: "content_anchored" };
    }

    // 五级（只在所有展开策略都试过之后才允许）：弹层在我们动手之前就开着，
    // 而且全页只有这一个。没有目标文案可依据（探查、枚举）时的最后一条路。
    if (allowStale) {
      const stale = visiblePanels();
      if (stale.length === 1) return { panel: stale[0], via: "stale" };
    }
    return { panel: null, via: "none" };
  };

  /**
   * 展开并定位弹层：逐个策略试，**每试一次就验证一次**，开了就停。
   *
   * 两段等待是刻意分开的，因为它们等的是两件不同的事：
   * - 弹层容器是同步渲染的 → 判断「开没开」只需要很短的等待（120/300ms），
   *   这样四种策略全试完最坏也就 1.7s，不会把识别拖垮；
   * - 选项来自接口 → 确认开了之后，再按 400/800/1200 的节奏等数据回来。
   */
  const openAndResolve = async (el, { targetHint = "" } = {}) => {
    const cleanup = await clearStalePanels(el);
    const before = allVisibleOptionElements();
    const beforePanels = new Set(visiblePanels());
    const tried = [];
    let resolved = { panel: null, via: "none" };
    let strategyUsed = "";

    // 关不掉的弹层里，如果有一个几何上就锚定在**本触发器**上，那它本来就是我们要
    // 的那个——直接用，绝不再去点触发器。
    //
    // 这一条是实测逼出来的：observe 阶段枚举组织形式时把它的弹层打开了、收尾没关掉
    // （本站的 orgDescPopper 不吃 Escape 也不吃外部点击），轮到 fill 时它把自己挡在
    // 门外。而触发器是个开关，六种展开姿势轮流去点，弹层在开/关之间摆动，收尾那一刻
    // 正好是关的，于是报「六种全试过还是打不开」——人只会去查「为什么点不开」，
    // 真正的原因却是「它早就开着，是我们自己把它点没的」。
    if (!cleanup.cleared) {
      const mine = visiblePanels().filter((panel) => isAnchoredTo(panel, el));
      if (mine.length === 1) {
        const panel = mine[0];
        let found = optionsIn(panel);
        for (const step of OPTION_WAIT_STEPS) {
          if (found.items.length) break;
          await delay(step);
          found = optionsIn(panel);
        }
        return { panel, via: "already_open", found, tried: ["already_open"], strategy: "already_open" };
      }
    }

    let interrupted = false;
    for (const strategy of openStrategies()) {
      try {
        // cdp 策略是异步的，必须等它真的点完再开始验证弹层；
        // 不 await 的话，120ms 的第一档等待会跑在点击之前，白等一轮。
        await strategy.run(el);
      } catch (error) {
        // 可信序列已经落地一半：**不再往同一个触发器上补别的姿势**。
        // 触发器是开关，补一次合成 mousedown 就可能把刚打开的又切回关闭。
        // 后面只做「弹层到底开没开」的检查，让事实说话。
        if (String(error?.message) === INTERRUPTED) interrupted = true;
        /* 其余姿势抛错不影响继续试下一种 */
      }
      tried.push(strategy.name);
      for (const step of [120, 300]) {
        await delay(step);
        // targetHint 要进策略循环内层，不能只留在收尾兜底：触发器多是**开关**，
        // 弹层本来就开着时，第一种姿势会把它切关、第二种再切开——只有循环里每一步
        // 都能按文案认领，才不会被这种奇偶切换耍了。
        resolved = resolvePanel(el, before, beforePanels, { targetHint });
        if (resolved.panel) break;
      }
      if (resolved.panel) {
        strategyUsed = strategy.name;
        break;
      }
      if (interrupted) break;
    }

    // 四种姿势都没开出新弹层：最后看一眼是不是有个「本来就开着」的唯一弹层。
    if (!resolved.panel) {
      resolved = resolvePanel(el, before, beforePanels, { allowStale: true, targetHint });
      if (resolved.panel) strategyUsed = "stale";
    }

    if (!resolved.panel) {
      return {
        panel: null,
        via: "none",
        found: { items: [], hints: [], loose: false },
        tried,
        strategy: "",
        // 上一个弹层没关掉时，这一条是真正的原因。
        blockedBy: cleanup.cleared ? [] : cleanup.stale,
      };
    }

    let found = optionsIn(resolved.panel);
    for (const step of OPTION_WAIT_STEPS) {
      if (found.items.length) break;
      await delay(step);
      // 组件可能把弹层换成新实例（尤其是级联长出新列时），每轮重新解析一次。
      const again = resolvePanel(el, before, beforePanels, { targetHint });
      if (again.panel) resolved = again;
      found = optionsIn(resolved.panel);
    }

    return { ...resolved, found, tried, strategy: strategyUsed };
  };

  /**
   * 展开并读回真实选项。
   *
   * 这一步是「让人/规则看见合法值集」的前提：不展开就枚举，选项永远是空的，
   * 只能拿档案里的原始写法去点——档案写「上海」、站点选项是「上海市」，就会稳定失败。
   * 语义匹配归上层（chooseOne 的三级），确定性点击归这里。
   */
  const enumerate = async (el, { keepOpen = false } = {}) => {
    const resolved = await openAndResolve(el);
    const options = [...new Set(resolved.found.items.map(optionLabelOf).filter(Boolean))].slice(0, 300);
    const panelText = resolved.panel ? panelTextOf(resolved.panel) : "";
    // 枚举是 stale 弹层最大的来源：一页十几个下拉逐个展开读选项，
    // 只要有一个收不干净，后面的字段就会被它挡住。这里必须硬关。
    if (!keepOpen) await closePanelsHard(el);

    if (!resolved.panel) {
      return { status: "no_panel", options: [], via: "none", loose: false, tried: resolved.tried, hints: [] };
    }
    if (!options.length) {
      // 弹层开着但没有真选项。两种情况要分开说：
      // - 弹层里只剩「1. 请选择企业类型」这类提示 → 这是**分步选择器**，上一步没做；
      // - 什么都没有 → 站点没给数据（多为未登录）。
      return {
        status: resolved.found.hints.length ? "needs_prior_step" : "empty",
        options: [],
        hints: resolved.found.hints,
        via: resolved.via,
        loose: resolved.found.loose,
        panelText,
        strategy: resolved.strategy,
      };
    }
    return {
      status: "ok",
      options,
      hints: resolved.found.hints,
      via: resolved.via,
      loose: resolved.found.loose,
      strategy: resolved.strategy,
      panel: resolved.panel,
    };
  };

  /**
   * 择一并点击。
   *
   * 失败一律返回带 reason 的结构，**绝不返回裸 false**：
   * - `empty`      弹层里一个选项都没有。最常见的原因是未登录（字典接口被拦），
   *                其次才是级联前置没满足。
   * - `no_match`   有选项但没有想要的那个。
   * - `ambiguous`  匹配到多个——这是一次歧义，交给人，不抛硬币。
   * - `no_panel`   压根没展开。
   * 这三种在诊断时完全不同，混成一个「失败」会让人查无实据。
   */
  const choose = async (el, wanted, { keepOpen = false } = {}) => {
    const target = clean(wanted);
    if (!target) return { ok: false, reason: "no_value" };

    const resolved = await openAndResolve(el, { targetHint: target });
    const found = resolved.found;

    if (!resolved.panel) {
      closePanels(el);
      return { ok: false, reason: "no_panel", via: "none", tried: resolved.tried };
    }

    const enabled = found.items.filter((item) => !optionDisabled(item));
    const labelsList = enabled.map(optionLabelOf);
    if (!labelsList.filter(Boolean).length) {
      const panelText = panelTextOf(resolved.panel);
      closePanels(el);
      // 弹层里只剩静态提示 = 这是分步选择器，上一步还没做。这和「站点没给数据」
      // 是两件事，混成一个 empty 会让人往未登录的方向白查一轮。
      return {
        ok: false,
        reason: found.hints.length ? "needs_prior_step" : "empty",
        via: resolved.via,
        available: [],
        hints: found.hints,
        panelText,
      };
    }

    const match = chooseOne(target, labelsList);
    if (match.status === "ambiguous") {
      closePanels(el);
      return { ok: false, reason: "ambiguous", wanted: target, available: match.candidates.slice(0, 12) };
    }
    if (match.status === "none") {
      closePanels(el);
      return { ok: false, reason: "no_match", wanted: target, available: labelsList.slice(0, 20) };
    }

    const index = labelsList.findIndex((label) => label === match.value);
    const option = enabled[index];
    // 居中而不是 nearest：可信点击要先过命中预检，元素贴着视口边缘就会被判出界。
    // 滚完等一帧让布局落定——坐标是在这之后算的。
    option.scrollIntoView({ block: "center", inline: "nearest" });
    await AFY.util.nextFrame();
    const clicked = await clickNode(option);
    await delay(200);

    // 级联控件点完一级还开着，且会长出新的一列。
    const stillOpen = visible(resolved.panel);
    if (stillOpen && !keepOpen) closePanels();

    return {
      ok: true,
      picked: match.value,
      // exact 之外的都要在界面上标出来：站点接受了一个与档案不完全相同的写法，
      // 这件事必须让人看见，而不是悄悄通过。
      matchLevel: match.status,
      wanted: target,
      stillOpen,
      panel: resolved.panel,
      inputVia: clicked.via,
    };
  };

  /**
   * 分步选择：在**同一个弹层里**逐级点下去。级联（区划）和分步向导（组织形式）
   * 是同一件事的两种长相，所以走同一段代码。
   *
   * 两条纪律：
   *
   * 1) 第二级起**不再调用 openWidget**。弹层这时已经开着，再往控件派发一次
   *    mousedown 会把它收起来，于是永远推进不到第二级。
   *
   * 2) 「下一级就绪」的判据是**候选集变了**，不是「有候选」。政务弹层里下一级的
   *    数据要等接口回来；不等就点，会把上一级的残留当成下一级。这里用元素身份
   *    取差集，差集为空时再退回比较文案集合——有些实现复用同一批 DOM 只换文字。
   */
  const chooseSteps = async (el, wanted) => {
    const path = (Array.isArray(wanted) ? wanted : [wanted]).map(clean).filter(Boolean);
    if (!path.length) return { ok: false, reason: "no_value" };

    // 用第一段当认领线索：弹层关不掉是这个站点的常态，「哪个弹层里有我要选的字」
    // 比「哪个弹层是新开的」可靠得多。
    const opened = await openAndResolve(el, { targetHint: path[0] });
    if (!opened.panel) {
      closePanels(el);
      return {
        ok: false,
        reason: opened.blockedBy?.length ? "blocked_by_stale_panel" : "no_panel",
        blockedBy: opened.blockedBy,
        tried: opened.tried,
        picked: [],
        level: 0,
      };
    }
    const panel = opened.panel;
    const picked = [];
    const skippedLevels = [];
    const seen = new Set();
    // 同时按**文案**记一份。判断「是不是长出了新的一级」只能看文案：
    // 有的向导每点一次就把这一列整个重渲染，元素全是新的，按身份判会把
    // 同级里没点的兄弟项当成新一级——末级点完后就会误报「还差一级」。
    const seenLabels = new Set();
    let previousLabels = "";
    // 只有上一击成功之后才要求「候选集必须变过」；起手和跳过分组之后都不要求。
    let needFresh = false;
    // 上一级点中的元素，歧义时拿它当分组标题收窄作用域。
    let lastNode = null;
    let scopedBy = "";
    // 真实的匹配级别。**必须一路带出去**：上一版这里恒返回 "exact"，于是
    // 「档案写上海市、站点写上海」这种唯一前缀命中被上层判成「站点改写了值」，
    // 一个本来成功的字段被报成需要人工复核。
    let lastMatchStatus = "exact";
    // 每一级的执行细节。分步选择器一旦出问题，「哪一级、候选池从哪来、多大、
    // 点了谁、走的哪条通道、点完选中没有」这六件事缺一件就只能靠猜——
    // 而这个弹层已经被误判过四轮。一路带到诊断里。
    const steps = [];

    for (let level = 0; level < path.length; level += 1) {
      const target = path[level];
      const ready = await AFY.util.until(
        () => {
          const all = selectableIn(panel);
          if (!all.length) return null;
          // 站点自己写明了层级（「1. 请选择…」「2. 请选择…」各占一块）时，
          // **一律以它为准**：第 N 级只在第 N 块里找。差集法在这里靠不住——
          // 点完第一级，Element Plus 会把整组 radio 重渲染成新 DOM，
          // 于是「新出现的」把上一级也算了进来，同名项一撞就又点回上一级。
          const groups = stepGroups(panel);
          if (groups) {
            // 索引要用**真正提交过的级数**（picked.length），不是路径下标。
            // 跳过分组标题那条路（下面的 skippedLevels）只加 level 不加 picked，
            // 两者从此错位：在两组的弹层里去要 groups[2]，于是白等 4500ms 超时，
            // 而目标当时就在第二组里。档案写 ["内资企业","有限公司","有限责任公司"]
            // ——正是 site-profile 注释推荐的写法——必踩。
            const stepIndex = picked.length;
            if (stepIndex < groups.length) {
              return {
                all,
                pool: groups[stepIndex],
                grouped: true,
                from: `group${stepIndex}/${groups.length}`,
              };
            }
            // 站点是「提示行 + 候选同块」的分步结构，但还没长出第 level 块 →
            // **下一级只是还没渲染出来**。绝不能拿现有候选去匹配：重渲染后的上一级
            // 里有同名项，一撞就又点回上一级。接着等，等不到就超时，都比点错强。
            if (needFresh) return null;
            return { all, pool: all, from: "grouped_overflow" };
          }
          if (!needFresh) return { all, pool: all, from: "first_level" };
          const fresh = all.filter((item) => !seen.has(item));
          if (fresh.length) return { all, pool: fresh, from: "fresh_diff" };
          // 复用同一批 DOM、只替换文案的实现：元素差集为空，但文案集合变了。
          const labels = all.map(optionLabelOf).join("");
          if (labels !== previousLabels) return { all, pool: all, from: "labels_changed" };
          // 候选一点没变，但要找的就在里面——上一击可能只是展开了一个分组，
          // 或者上下两级共用同一个列表容器。这种情况不该干等到超时。
          if (chooseOne(target, all.map(optionLabelOf)).status !== "none") {
            return { all, pool: all, from: "target_present" };
          }
          return null;
        },
        { timeout: 4500, interval: 300 },
      );

      if (!ready) {
        // 弹层已经关了，而且前面确实点中过 —— 向导比路径**短**，走完了。
        // 剩下的路径段是多余的（多半是面板上重复点候选攒出来的，
        // 比如 ["有限公司","有限公司","有限公司"]），不该因此报失败。
        // 值到底填没填上，交给回读去判——那才是唯一可信的判据。
        if (picked.length && !visible(panel)) {
          return {
            ok: true,
            picked: picked.join(" / "),
            steps,
            skippedLevels,
            scopedBy,
            extraLevels: path.slice(level),
            matchLevel: picked.length > 1 ? "steps" : lastMatchStatus,
            wanted: path.join(" / "),
          };
        }
        const found = optionsIn(panel);
        closePanels(el);
        return {
          ok: false,
          reason: found.hints.length ? "needs_prior_step" : "empty",
          level,
          picked,
          steps,
          skippedLevels,
          wanted: target,
          hints: found.hints,
          panelText: panelTextOf(panel),
        };
      }

      const { all, pool, grouped } = ready;
      const allLabels = all.map(optionLabelOf);
      let items = pool;
      let labelsList = pool.map(optionLabelOf);
      let match = chooseOne(target, labelsList);
      let poolFrom = ready.from || "";

      // 本级候选里没有，就在整个弹层里再找一次：分组列表、或者上下两级共用同一个
      // 容器时，目标本来就不在新出现的那一批里。
      //
      // **但站点自己分好了级（grouped）时绝不放宽。** 放宽的后果实测过：
      // 路径 ["有限公司","股份公司"] 的第二级里没有「股份公司」，放宽到整个弹层就
      // 命中了**第一级**的「股份公司」并点下去——把已经选对的企业类型改掉，
      // 组织形式仍然是空，而且返回成功。三条红线一次踩全：点错了、没停下来问人、
      // 报了假成功。分组本来就是为了防这件事，不能自己把它拆了。
      if (match.status === "none" && !grouped && pool !== all) {
        items = all;
        labelsList = allLabels;
        match = chooseOne(target, labelsList);
        poolFrom = `${poolFrom}→all`;
      }

      // 歧义或同名重复时，用上一级点中的那一项当分组标题再收窄一次。
      //
      // **duplicate 也要走这里，而且要排在「取第一个」之前**：路径里既然写了
      // 「内资企业」，那就是用户指定了哪一支，不该被「取第一个」这条兜底盖过。
      // 只有在没有上一级可依据时（路径省了分组名），才退回取第一个。
      if ((match.status === "ambiguous" || match.status === "duplicate") && lastNode) {
        // 收窄的底也必须是**本级的候选池**，不能是整个弹层：lastNode 是上一级点中的
        // 那一项，以 all 为底收窄会把选择拖回上一级去。
        const scopeBase = grouped ? items : all;
        const scoped = scopeUnderHeader(lastNode, scopeBase);
        if (scoped.length && scoped.length < scopeBase.length) {
          const scopedLabels = scoped.map(optionLabelOf);
          const scopedMatch = chooseOne(target, scopedLabels);
          if (scopedMatch.status !== "none" && scopedMatch.status !== "ambiguous") {
            items = scoped;
            labelsList = scopedLabels;
            match = scopedMatch;
            scopedBy = optionLabelOf(lastNode);
          }
        }
      }

      // 仍然没有：这一级很可能是**分组标题**（用户描述的「内资企业」那一层），
      // 或者站点已经替我们满足了它。下一级的值能对上就跳过它，而不是让整条路径失败。
      // 分组标题在不同实现里有时可点、有时只是一行字，这条容错让两种都能走通。
      // 下一级**哪怕是歧义**也要跳过这一级。歧义说明下一级的值确实在弹层里、
      // 只是需要按分组定死；而这一级的值压根不存在，多半是路径里多了一层
      // （比如按档案 enterprise_type 拼出来的前缀与弹层文案不是一套）。
      // 跳过之后报「歧义 + 请补分组名」，比报「找不到 有限责任公司（自然人投资或控股）」
      // 更接近人真正要做的事。
      if (match.status === "none" && level + 1 < path.length) {
        // 分好级时，「下一级能不能对上」也只能在**本级候选**里看。拿整个弹层去看，
        // 会把「下一段的值恰好在别的步骤里」误当成「这一级是分组标题」而跳过。
        const nextMatch = chooseOne(path[level + 1], grouped ? labelsList : allLabels);
        if (nextMatch.status !== "none") {
          skippedLevels.push(target);
          needFresh = false;
          continue;
        }
      }

      if (match.status === "ambiguous" || match.status === "none") {
        closePanels(el);
        return {
          ok: false,
          reason: match.status === "ambiguous" ? "ambiguous" : "no_match",
          level,
          picked,
          steps,
          skippedLevels,
          wanted: target,
          // 摆给人看的必须是**这一级真正的合法值**。分好级时给整个弹层的候选，
          // 人会以为「股份公司明明在里面，为什么说没有」——而它属于上一级。
          available: (match.status === "ambiguous"
            ? match.candidates
            : grouped
              ? labelsList
              : allLabels
          ).slice(0, 30),
          // 第一级就撞歧义时，多半是漏了分组这一层：同一个「有限公司」在内资/外资
          // 各有一个。把分组名加到路径最前面就能定死。
          groupHint:
            match.status === "ambiguous" && !lastNode
              ? "这个值同时命中多个候选。若它们分属不同分组（如内资企业 / 外资企业），在路径数组最前面补上分组名即可。"
              : "",
          panelText: panelTextOf(panel),
        };
      }

      all.forEach((item) => seen.add(item));
      allLabels.forEach((label) => seenLabels.add(label));
      lastMatchStatus = match.status;
      previousLabels = allLabels.join("");

      const node = items[labelsList.findIndex((label) => label === match.value)];
      // 居中 + 等一帧：分步弹层的第二级常在可滚动区底部，贴边会让命中预检判出界。
      node.scrollIntoView({ block: "center", inline: "nearest" });
      await AFY.util.nextFrame();
      // 可信点击优先；回退时用完整指针序列而不是裸 click——
      // 分步弹层里有实现是听 mousedown 推进下一级的。
      const clicked = await clickNode(node);
      picked.push(match.value);
      lastNode = node;
      needFresh = true;
      await delay(350);

      const chosen = isChosen(node);
      steps.push({
        level,
        target,
        // 记**真实**来源：放宽过就写成 group1/2→all。诊断里出现
        // 「from: group1/2 而 poolSize: 9」这种自相矛盾的记录，比没有记录更误导。
        from: poolFrom,
        poolSize: labelsList.length,
        pool: labelsList.slice(0, 8),
        picked: match.value,
        matchLevel: match.status,
        via: clicked.via,
        // 点完到底选中没有。判据见 isChosen（input.checked / is-checked / aria-checked
        // 任一成立）；判不出来（li 型候选，没有任何选中标记）记 null。
        checkedAfter: chosen,
      });

      // radio / checkbox 型候选：点完**立刻验证它真的被选中了**。
      //
      // 不验的话会得到最坏的一种结果：这一级其实没点上，chooseSteps 却照常走完
      // 并返回成功，最后只有回读说一句「值是空的」（empty_after_write）——人根本
      // 看不出是哪一级、为什么没生效。实测就是这样：第一级 is-checked 了，
      // 第二级没有，而引擎报的是「填了但空」。
      if (chosen === false) {
        closePanels(el);
        return {
          ok: false,
          reason: "click_not_applied",
          level,
          picked,
          steps,
          skippedLevels,
          wanted: target,
          panelText: panelTextOf(panel),
          detail:
            `点了「${match.value}」但这一项没有变成选中状态。` +
            (AFY.trusted?.enabled()
              ? "可信点击与合成点击都试过了，多半是它被遮挡或站点另有拦截。"
              : "当前走的是合成事件，这类候选常常不吃；到设置页开启「可信输入」多半能解决。"),
        };
      }
    }

    // 路径用完了，弹层却还开着、而且长出了没见过的一批候选：这个向导比路径更深。
    // 这不是失败，是「还差一级」——把下一级的候选交出来，让人在面板上点一下就把
    // 路径补全，而不是丢一个「填了但值是空的」让人去猜。
    if (visible(panel)) {
      // 判据必须是**元素身份**，不能是文案。
      //
      // 实测反例：组织形式第二级是「有限责任公司 / 有限公司」，而第一级里也有一个
      // 「有限公司」。按文案过滤会把第二级那个同名项判成「见过了」，面板上就只剩
      // 一个候选——人想选它却没得选。
      //
      // 之前改成按文案，是为了迁就自检 fixture「每点一次就重渲染整列」的行为；
      // 那是 fixture 不真实，不是产品该迁就的。真实弹层选完末级就收起，
      // 上面的 visible(panel) 守卫已经挡住了那种误报。
      const remaining = [
        ...new Set(
          selectableIn(panel)
            .filter((item) => !seen.has(item))
            .map(optionLabelOf)
            .filter(Boolean),
        ),
      ];
      if (remaining.length) {
        closePanels(el);
        return {
          ok: false,
          reason: "needs_next_level",
          level: picked.length,
          picked,
          steps,
          skippedLevels,
          available: remaining.slice(0, 40),
          panelText: panelTextOf(panel),
        };
      }
    }

    closePanels(el);
    return {
      ok: true,
      picked: picked.join(" / "),
      steps,
      skippedLevels,
      scopedBy,
      // 真点了不止一级才叫 steps；单级时要如实回传 chooseOne 的级别，
      // 否则 prefix/normalized 命中会被上层当成「站点改写了值」。
      matchLevel: picked.length > 1 ? "steps" : lastMatchStatus,
      wanted: path.join(" / "),
    };
  };

  /** 保留旧名字，级联就是分步的一种。 */
  const chooseCascade = (el, wanted) => chooseSteps(el, wanted);

  /**
   * 自动补全：打完字之后**从候选里选中**。
   *
   * 为什么必须选：el-autocomplete 光把文字写进 input 是不作数的——组件的数据模型
   * 要等 select 事件才更新。而回读读到的正是 JS 刚写进去的那串字，于是**核验假阳性
   * 通过**，页面看着填好了，下一步却被站点打回来说没选。本站「行业表述」就是这种。
   *
   * 和下拉的区别：候选是**打字触发**的，不能去点控件——点一下反而常把候选关掉。
   * 所以这里只补一次 input 事件，然后等候选渲染。
   */
  const pickSuggestion = async (el, wanted) => {
    const target = clean(wanted);
    if (!target) return { ok: false, reason: "no_value" };

    const before = allVisibleOptionElements();
    const beforePanels = new Set(visiblePanels());
    el.dispatchEvent(new InputEvent("input", { bubbles: true }));

    let resolved = { panel: null, via: "none" };
    let found = { items: [], hints: [], loose: false };
    for (const step of OPTION_WAIT_STEPS) {
      await delay(step);
      resolved = resolvePanel(el, before, beforePanels);
      if (!resolved.panel) continue;
      found = optionsIn(resolved.panel);
      if (found.items.length) break;
    }

    // 一个候选都没出来：这个控件多半本来就允许自由输入，保留已填的文本即可。
    // 这与「有候选但没选中」是两件事，不能混。
    if (!resolved.panel || !found.items.length) {
      return { ok: false, reason: "no_suggestion" };
    }

    const enabled = found.items.filter((item) => !optionDisabled(item));
    const labelsList = enabled.map(optionLabelOf);
    const match = chooseOne(target, labelsList);
    if (match.status === "ambiguous") {
      closePanels(el);
      return { ok: false, reason: "ambiguous", wanted: target, available: match.candidates.slice(0, 12) };
    }
    if (match.status === "none") {
      closePanels(el);
      return { ok: false, reason: "no_match", wanted: target, available: labelsList.slice(0, 30) };
    }

    const node = enabled[labelsList.findIndex((label) => label === match.value)];
    node.scrollIntoView({ block: "center", inline: "nearest" });
    await AFY.util.nextFrame();
    await clickNode(node);
    await delay(250);
    if (visible(resolved.panel)) closePanels();
    return { ok: true, picked: match.value, matchLevel: match.status };
  };

  /**
   * 只读回自动补全当前的候选，不选中任何一条。
   * 用于「站点把值清空了」之后把可选项摆到面板上，让人点一下就写回档案。
   */
  const listSuggestions = async (el) => {
    const before = allVisibleOptionElements();
    const beforePanels = new Set(visiblePanels());
    el.dispatchEvent(new InputEvent("input", { bubbles: true }));

    let resolved = { panel: null, via: "none" };
    let found = { items: [], hints: [], loose: false };
    for (const step of OPTION_WAIT_STEPS) {
      await delay(step);
      resolved = resolvePanel(el, before, beforePanels);
      if (!resolved.panel) continue;
      found = optionsIn(resolved.panel);
      if (found.items.length) break;
    }
    const options = found.items
      .filter((item) => !optionDisabled(item))
      .map(optionLabelOf)
      .filter(Boolean);
    closePanels(el);
    return [...new Set(options)].slice(0, 40);
  };

  /**
   * 探查弹层：把它的真实结构原样倒出来。
   *
   * 存在的理由很实际——政务站点的自绘弹层长什么样，猜是猜不出来的。组织形式这一个
   * 就先后被误读成「级联前置」和「唯一的选项是『2. 请选择组织形式』」两次。
   * 与其继续猜，不如给一个按钮，让人一次性把结构贴出来。
   */
  const describePanel = async (el, { targetHint = "" } = {}) => {
    // 目标文案要传进来。实测（组织形式）：名称区划的下拉赖着不走，页面上同时开着
    // 两个弹层，「哪个是新开的」就失效了；填写路径因为有档案值当线索还能认领到
    // 正确的那个，探查路径没线索就只能报 blocked_by_stale_panel——于是最需要看
    // 结构的那个弹层，恰恰是永远探不到的那个。
    const opened = await openAndResolve(el, { targetHint });
    if (!opened.panel) {
      closePanels(el);
      // blockedBy 要一起带出去：探查失败最常见的原因就是别的弹层没关掉，
      // 只报 no_panel 会把人引去查一个不存在的「打不开」问题。
      return {
        ok: false,
        reason: opened.blockedBy?.length ? "blocked_by_stale_panel" : "no_panel",
        blockedBy: opened.blockedBy,
        tried: opened.tried,
      };
    }
    const panel = opened.panel;
    const lines = [];
    const walk = (node, depth) => {
      if (lines.length >= 120 || depth > 8) return;
      // 用快照而不是 live HTMLCollection：遍历途中一旦有别的东西动了这棵树，
      // 对 node.children 的 for...of 可能永远走不完。诊断工具卡死是最糟的一种坏法。
      for (const child of [...node.children]) {
        if (lines.length >= 120) return;
        const own = clean(
          [...child.childNodes]
            .filter((n) => n.nodeType === 3)
            .map((n) => n.textContent)
            .join(" "),
        ).slice(0, 60);
        const cls = clean(child.className.toString()).split(/\s+/).filter(Boolean).slice(0, 3).join(".");
        lines.push(
          `${"  ".repeat(depth)}${child.tagName.toLowerCase()}${cls ? "." + cls : ""}` +
            `${visible(child) ? "" : " [hidden]"}${own ? `  «${own}»` : ""}`,
        );
        walk(child, depth + 1);
      }
    };
    walk(panel, 0);
    const found = optionsIn(panel);
    const result = {
      ok: true,
      panelClass: clean(panel.className.toString()).slice(0, 200),
      panelText: panelTextOf(panel),
      candidates: found.items.map(optionLabelOf).filter(Boolean).slice(0, 60),
      hints: found.hints.slice(0, 10),
      loose: found.loose,
      strategy: opened.strategy,
      outline: lines.join("\n"),
    };
    // 探查完必须收干净：连点两次探查时，第一次留下的弹层会把第二次挡住——
    // 表现就是「第一次成功、之后一直失败」。
    await closePanelsHard(el);
    return result;
  };

  AFY.selects = {
    OPTION_SELECTOR,
    PANEL_SELECTOR,
    isAnchoredTo,
    findSafeSpot,
    closePanelsHard,
    HINT_TEXT,
    optionLabelOf,
    clickNode,
    openWidget,
    closePanels,
    visiblePanels,
    selectableIn,
    looseCandidates,
    scopeUnderHeader,
    describePanel,
    chooseSteps,
    pickSuggestion,
    listSuggestions,
    enumerate,
    choose,
    chooseCascade,
  };
})();
