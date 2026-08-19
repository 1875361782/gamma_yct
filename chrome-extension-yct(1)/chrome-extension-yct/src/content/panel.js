// 页内侧边栏。整个 UI 关在 Shadow DOM 里，样式一条也不外泄——政务站点的
// 全局样式和这个面板互相污染会让两边都难看，而且会干扰可见性判定。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.panel) return;
  const { clean } = AFY.util;

  const CSS = `
:host { all: initial; }
.wrap {
  position: fixed; top: 0; right: 0; width: 420px; max-width: 46vw; height: 100vh;
  z-index: 2147483646; display: flex; flex-direction: column;
  font: 13px/1.55 -apple-system, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  color: #1c2333; background: #f7f8fb; box-shadow: -6px 0 24px rgba(20,28,50,.18);
  border-left: 1px solid #d9deea;
}
.wrap.collapsed { width: 34px; background: transparent; box-shadow: none; border-left: 0; }
.tab {
  writing-mode: vertical-rl; padding: 14px 8px; background: #26356b; color: #fff;
  border-radius: 8px 0 0 8px; cursor: pointer; font-size: 12px; letter-spacing: 2px;
  margin-top: 76px; user-select: none; box-shadow: -3px 0 12px rgba(20,28,50,.22);
}
.tab.alert { background: #9d2020; }
.head { padding: 10px 12px; background: #26356b; color: #fff; display: flex; gap: 8px; align-items: flex-start; }
.head h1 { margin: 0; font-size: 13px; font-weight: 600; flex: 1; }
.head .meta { margin-top: 4px; font-size: 11px; opacity: .82; word-break: break-all; }
.head button { background: rgba(255,255,255,.16); color: #fff; border: 0; border-radius: 5px; cursor: pointer; padding: 3px 7px; font-size: 12px; }
.ops { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 10px; border-bottom: 1px solid #e2e6f0; background: #fff; }
.ops button { border: 1px solid #c8cfe0; background: #fff; color: #26356b; border-radius: 6px; padding: 5px 10px; font-size: 12px; cursor: pointer; }
.ops button.primary { background: #26356b; color: #fff; border-color: #26356b; }
.ops button.danger { background: #9d2020; color: #fff; border-color: #9d2020; }
.ops button:disabled { opacity: .45; cursor: not-allowed; }
.body { flex: 1; overflow: auto; padding: 10px; }
.card { background: #fff; border: 1px solid #e2e6f0; border-radius: 8px; padding: 9px 10px; margin-bottom: 9px; }
.card h2 { margin: 0 0 6px; font-size: 12px; color: #4a5878; font-weight: 600; }
.note { background: #fff8e6; border-color: #f0d79a; color: #6b4e00; }
.bad { background: #fdeeee; border-color: #eec3c3; color: #7d1d1d; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { text-align: left; padding: 4px 5px; border-bottom: 1px solid #eef1f7; vertical-align: top; word-break: break-all; }
th { color: #7c88a5; font-weight: 500; }
.tag { display: inline-block; border-radius: 4px; padding: 0 5px; font-size: 11px; line-height: 17px; }
.tag.ok { background: #e6f4ea; color: #17632f; }
.tag.review { background: #fff3d6; color: #7a5600; }
.tag.failed { background: #fdeaea; color: #8d1d1d; }
.key { color: #6a7896; }
.avail { margin-top: 4px; padding: 5px 6px; border-radius: 5px; background: #f2f5fd; border: 1px solid #d8e0f4; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 5px; }
.chip { max-width: 100%; text-align: left; border: 1px solid #b9c4de; background: #fff; color: #26356b; border-radius: 999px; padding: 3px 9px; font-size: 11px; line-height: 1.5; cursor: pointer; }
.chip:hover { background: #26356b; color: #fff; border-color: #26356b; }
.probe { border: 1px solid #c8cfe0; background: #fff; color: #26356b; border-radius: 5px; padding: 2px 7px; font-size: 11px; cursor: pointer; margin-top: 4px; }
pre { margin: 6px 0 0; padding: 8px; background: #1c2333; color: #d8e0f4; border-radius: 6px; font: 11px/1.5 ui-monospace, Menlo, monospace; white-space: pre; overflow: auto; max-height: 320px; }
textarea.dump { width: 100%; height: 220px; margin-top: 6px; box-sizing: border-box; font: 11px/1.5 ui-monospace, Menlo, monospace; border: 1px solid #c8cfe0; border-radius: 6px; padding: 6px; }
.small { font-size: 11px; color: #78829c; }
ul { margin: 4px 0 0; padding-left: 16px; }
li { margin-bottom: 3px; }
`;

  let host = null;
  let shadow = null;
  let root = null;
  let onAction = () => {};

  const el = (tag, attrs = {}, children = []) => {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs)) {
      if (name === "class") node.className = value;
      else if (name === "text") node.textContent = value;
      else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
      else node.setAttribute(name, value);
    }
    for (const child of [].concat(children)) if (child) node.append(child);
    return node;
  };

  let collapsed = true;
  let onCollapseChange = () => {};

  const mount = (handler, { startCollapsed = true, onCollapse = () => {} } = {}) => {
    onAction = handler;
    onCollapseChange = onCollapse;
    collapsed = startCollapsed;
    if (host) return;
    host = document.createElement("div");
    host.id = "afy-panel-host";
    shadow = host.attachShadow({ mode: "open" });

    // 优先用构造式样式表：<style> 元素插进页面文档要受站点 CSP 的 style-src 约束，
    // 政务站点的 CSP 常常没有 'unsafe-inline'，那样面板会变成一堆没样式的裸文字。
    // adoptedStyleSheets 走的是另一条路，不受页面 CSP 管。
    let adopted = false;
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(CSS);
      shadow.adoptedStyleSheets = [sheet];
      adopted = true;
    } catch {
      adopted = false;
    }
    if (!adopted) {
      const style = document.createElement("style");
      style.textContent = CSS;
      shadow.append(style);
    }

    root = el("div", { class: "wrap" });
    shadow.append(root);
    document.documentElement.append(host);
  };

  const setCollapsed = (value, view) => {
    collapsed = Boolean(value);
    onCollapseChange(collapsed);
    render(view);
  };

  const button = (label, action, { primary = false, danger = false, disabled = false } = {}) =>
    el("button", {
      text: label,
      class: primary ? "primary" : danger ? "danger" : "",
      ...(disabled ? { disabled: "disabled" } : {}),
      onclick: () => onAction(action),
    });

  // 提示文案里用 ** 标重点是为了在日志/文档里也读得通；面板是纯文本渲染，去掉标记。
  const plain = (text) => String(text || "").replace(/\*\*/g, "");

  const statusTag = (status) =>
    el("span", { class: `tag ${status === "ok" ? "ok" : status === "review" ? "review" : "failed"}`, text: status });

  const REASON_TEXT = {
    empty: "选项面板开了，但里面一个选项都没有",
    needs_prior_step: "这是分步选择器，弹层里只剩「请先选…」的提示——上一步还没做",
    missing_step_value: "分步选择器，但档案里缺其中一步的值",
    no_panel: "试了四种展开方式都没能打开选项面板（控件可能被遮挡、被禁用，或本轮页面已变化）",
    no_match: "选项里没有档案里的这个值",
    needs_next_level: "这个向导比档案里的路径更深，还差一级没选",
    cleared_by_site: "站点把这个值判为非法并清空了输入框",
    blocked_by_stale_panel: "上一个字段的弹层还开着没关掉，把这个控件挡住了",
    click_not_applied: "点到了那一项，但它没有变成选中状态（点击没被站点接受）",
    no_suggestion: "打完字之后站点没给任何候选",
    validation: "站点校验没通过，先按它的提示改内容",
    ambiguous: "匹配到多个选项，需要你指定",
    unstable_readback: "写进去之后又被页面改掉或清空了",
    picked_in_panel: "已在弹层里逐级选中（这个输入框只是触发器，值不在它里面）",
    element_gone: "页面结构已变，原来的控件找不到了",
    disabled: "控件当前不可编辑",
    exception: "执行时抛错",
    no_value: "档案里这一项是空的",
    unmapped: "标签没有对应的档案字段",
    ambiguous_mapping: "一个标签对上了多个档案字段",
    no_fact: "档案里缺这条事实",
    alternate_needs_user: "备用/备选项，要的是另一个值，不能拿主字段的值来填——需要你本人决定",
    declaration_needs_user: "法律意思表示，必须你本人勾选",
    secret: "密码/验证码类，插件不碰",
    file: "文件上传，需你本人选择文件",
    readonly: "只读展示项",
  };

  const render = (view) => {
    if (!root) return;
    root.className = collapsed ? "wrap collapsed" : "wrap";
    root.replaceChildren();

    // 收起态默认。420px 的面板压在页面右侧会挡住站点自己的按钮，
    // 装上就霸屏是不礼貌的；展开与否由用户决定，并且记住选择。
    if (collapsed) {
      root.append(
        el("div", {
          class: view.dialog || view.probe?.blocked ? "tab alert" : "tab",
          text: view.probe?.blocked ? "填报助手 · 需登录" : "填报助手",
          title: "点击展开一窗通填报助手",
          onclick: () => setCollapsed(false, view),
        }),
      );
      return;
    }

    root.append(
      el("div", { class: "head" }, [
        el("div", {}, [
          el("h1", { text: "一窗通填报助手" }),
          el("div", {
            class: "meta",
            text: view.snapshot
              ? `${view.snapshot.stage.label} · ${view.snapshot.fields.length} 个可见控件 · 指纹 ${view.snapshot.fingerprint}`
              : "尚未观察当前页面",
          }),
        ]),
        el("button", {
          text: "收起 »",
          onclick: () => setCollapsed(true, view),
        }),
      ]),
    );

    root.append(
      el("div", { class: "ops" }, [
        button("只识别", "observe"),
        // 连续填报排在最前并设为主按钮：这才是常态用法。单页的「按档案填写」
        // 与「下一步」留着，用于出问题之后一步步核对。
        button("连续填报", "auto", { primary: true }),
        button("按档案填写", "fill"),
        button("处理弹窗", "dialog"),
        button("下一步", "advance"),
        button("复核并提交", "submit", { danger: true }),
        button("导出诊断", "diagnostics"),
        button("重置本次运行", "reset"),
      ]),
    );

    const body = el("div", { class: "body" });
    root.append(body);

    if (view.message) {
      body.append(el("div", { class: "card note" }, [el("div", { text: plain(view.message) })]));
    }

    for (const hint of view.hints || []) {
      body.append(el("div", { class: "card note" }, [el("div", { text: plain(hint) })]));
    }

    if (view.probe?.blocked) {
      body.append(
        el("div", { class: "card bad" }, [
          el("h2", { text: "登录状态" }),
          el("div", {
            text: `站点接口返回了「需要登录」（最近 ${view.probe.blocked} 次）。请先在页面上完成登录与实名授权，插件不代劳这一步。`,
          }),
        ]),
      );
    }

    if (view.dialog) {
      // 一律写「需要你处理」会把「站点校验没通过」和「一句无关紧要的提示」说成同一件事。
      const heading = {
        validation: "站点校验没通过",
        human: "这个弹窗需要你本人决定",
        acknowledge: "站点要你先知悉一件事",
        fill: "弹窗里有需要填写的字段",
        confirm: "页面上有一个提示弹窗",
      }[view.dialog.action] || "页面上有一个弹窗";
      const soft = view.dialog.action === "confirm" || view.dialog.action === "acknowledge";
      const card = el("div", { class: soft ? "card note" : "card bad" }, [
        el("h2", { text: view.dialog.title ? `${heading}：${view.dialog.title}` : heading }),
        el("div", { text: view.dialog.text || "" }),
        el("div", { class: "small", text: `按钮：${(view.dialog.buttons || []).join(" / ") || "（未识别）"}` }),
      ]);
      // 知情类：读完之后可以授权由插件代点，省掉每页一次的手动确认。
      // 授权只在本次运行内有效，且**永远不覆盖**承诺/签署/实名/支付那一类。
      if (view.dialog.action === "acknowledge") {
        card.append(
          el("div", { class: "small", text: "读完上面这段之后，你可以自己点它的「确定」；也可以授权插件代点——授权只在本次运行内有效，重置即失效。" }),
          el("div", {}, [
            el("button", {
              class: "probe",
              text: "我已读过，本次运行内代我确认同类提示",
              onclick: () => onAction("acknowledge"),
            }),
          ]),
        );
      }
      body.append(card);
    }

    if (view.snapshot?.errors?.length) {
      body.append(
        el("div", { class: "card bad" }, [
          el("h2", { text: "页面校验错误" }),
          el("ul", {}, view.snapshot.errors.map((text) => el("li", { text }))),
        ]),
      );
    }

    if (view.plan?.length) {
      const rows = el("tbody", {}, [
        el("tr", {}, [
          el("th", { text: "字段" }),
          el("th", { text: "档案来源" }),
          el("th", { text: "期望值" }),
          el("th", { text: "结果" }),
        ]),
      ]);
      const table = el("table", {}, [rows]);
      for (const item of view.plan) {
        const result = (view.results || []).find((row) => row.fieldId === item.fieldId);
        rows.append(
          el("tr", {}, [
            el("td", {}, [
              el("div", { text: item.label || "（无标签）" }),
              el("div", { class: "small", text: `${item.kind}${item.required ? " · 必填" : ""}` }),
            ]),
            el("td", {}, [
              el("div", { class: "small", text: item.canonicalKey }),
              el("div", { class: "small", text: item.matchedBy || "" }),
            ]),
            el("td", {}, [
              el("div", { text: clean(item.pathLabel || item.value).slice(0, 80) }),
              item.stepNote ? el("div", { class: "small", text: item.stepNote }) : null,
              item.optionStatus
                ? el("div", {
                    class: "small",
                    text:
                      item.optionStatus === "ok"
                        ? `站点选项 ${item.options.length} 项 · 预匹配 ${item.preMatch}`
                        : `选项枚举：${REASON_TEXT[item.optionStatus] || item.optionStatus}`,
                  })
                : null,
            ]),
            el("td", {}, [
              result ? statusTag(result.status) : el("span", { class: "small", text: "待执行" }),
              result?.reason ? el("div", { class: "small", text: REASON_TEXT[result.reason] || result.reason }) : null,
              result?.detail ? el("div", { class: "small", text: result.detail }) : null,
              // 分步选择器逐级的执行记录。它回答的是「到底断在哪一级」——
              // 这个弹层被误判过四轮，每一轮都是因为看不见这一层。
              result?.steps?.length
                ? el("div", { class: "small" }, [
                    el("div", { text: "逐级执行：" }),
                    ...result.steps.map((step) =>
                      el("div", {
                        text:
                          `　L${step.level} 要「${step.target}」→ 点了「${step.picked}」` +
                          `（候选 ${step.poolSize} 项，来自 ${step.from}，${step.via}）` +
                          (step.checkedAfter === null ? "" : step.checkedAfter ? " ✓已选中" : " ✗没选中"),
                      }),
                    ),
                  ])
                : null,
              result?.actual ? el("div", { class: "small", text: `实测：${result.actual}` }) : null,
              // 站点实际给的选项——而且**可以直接点**。
              //
              // 之前这里只是把候选排成一行字，人得切到设置页把
              // 「信息科技 (主营行业:通信、信息、数码科技(12678))」一字不差地抄进 JSON
              // 再回来重跑。候选就在手上却让人手抄，这个来回才是真正卡住的地方。
              // 现在点一下就写回档案并自动重填。
              result?.available?.length
                ? el("div", { class: "avail" }, [
                    el("div", {
                      class: "small",
                      text:
                        result.reason === "needs_next_level"
                          ? `还差一级。已选：${(result.picked || []).join(" / ") || "（无）"}。点下面任一项补上这一级：`
                          : "点一下站点给的值，直接写回档案并重填：",
                    }),
                    el(
                      "div",
                      { class: "chips" },
                      result.available.slice(0, 40).map((option) =>
                        el("button", {
                          class: "chip",
                          text: option,
                          title: option,
                          onclick: () =>
                            onAction({
                              type: "pick",
                              canonicalKey: item.canonicalKey,
                              value: option,
                              picked: result.picked,
                              // 分步选择器按「已点中的几级 + 这一级」重建路径；
                              // 单级控件直接覆盖。
                              // 只要这一轮已经点中/输入过前面几段，就按「前几段 + 这一段」
                              // 重建路径。自动补全也走这条：前一段是查询词。
                              mode:
                                item.cascade ||
                                result.picked?.length ||
                                result.reason === "needs_next_level" ||
                                (result.level || 0) > 0
                                  ? "append"
                                  : "set",
                            }),
                        }),
                      ),
                    ),
                  ])
                : null,
              // 被剔掉的静态提示行是「这是分步选择器、上一步没做」的直接证据，
              // 比任何解释都管用，所以原样端出来。
              result?.hints?.length
                ? el("div", { class: "avail" }, [
                    el("div", { class: "small", text: "弹层里只有这些提示，没有真选项：" }),
                    el("div", { text: result.hints.join(" / ") }),
                    // 缺的正是上一步的值时，直接把要补的档案键说出来。
                    item.stepGap
                      ? el("div", { class: "small", text: `档案里补上 ${item.stepGap}，插件就能替你把第一步也点掉。${item.stepNote || ""}` })
                      : el("div", { class: "small", text: "先在页面上把上一步选掉，再点「按档案填写」。" }),
                  ])
                : null,
              result?.groupHint ? el("div", { class: "small", text: result.groupHint }) : null,
              result?.scopedBy
                ? el("div", { class: "small", text: `按分组「${result.scopedBy}」收窄后选中` })
                : null,
              result?.extraLevels?.length
                ? el("div", { class: "small", text: `向导只有 ${(result.picked || "").split(" / ").length} 级，档案里多出的「${result.extraLevels.join(" / ")}」已忽略——建议用「清空这一项」重选一次，把路径修干净。` })
                : null,
              // 同名重复项取了第一个——这是一次代人做的选择，必须写在脸上。
              result?.matchLevel === "duplicate"
                ? el("div", { class: "small", text: "站点有多个同名选项，已按约定取第一个。若要的是后面那个，请在路径数组里补上它的分组名。" })
                : null,
              result?.panelText
                ? el("div", { class: "small", text: `弹层原文：${result.panelText}` })
                : null,
              Number.isInteger(result?.level) && result.level > 0
                ? el("div", { class: "small", text: `卡在第 ${result.level + 1} 级，已选：${(result.picked || []).join(" / ") || "（无）"}` })
                : null,
              result?.blockedBy?.length
                ? el("div", { class: "small", text: `挡路的弹层内容：${result.blockedBy.join(" ｜ ")}` })
                : null,
              result?.tried?.length
                ? el("div", { class: "small", text: `已尝试的展开方式：${result.tried.join(" → ")}` })
                : null,
              result?.verdict === "transformed"
                ? el("div", { class: "small", text: "站点改写了这个值，请人工确认" })
                : null,
              // 非精确命中要看得见，但不该报警：站点写「上海」而档案写「上海市」是
              // 正常的写法差异，不是错误。
              result?.verdict === "site_wording" && clean(result.actual) !== clean(item.value)
                ? el("div", { class: "small", text: `站点写法「${result.actual}」，档案写的是「${item.value}」（${result.matchLevel} 命中）` })
                : null,
              // 弹层结构猜不出来时，一键把它倒出来。
              ["custom-select", "popover-picker", "cascader", "autocomplete"].includes(item.kind)
                ? el("div", {}, [
                    el("button", {
                      class: "probe",
                      text: "探查弹层",
                      onclick: () =>
                        onAction({
                          type: "probe",
                          fieldId: item.fieldId,
                          label: item.label,
                          // 档案里想选的值当认领线索：两个弹层同时开着时，
                          // 「哪个里面有这个字」比「哪个是新开的」可靠。
                          wanted: Array.isArray(item.cascade) && item.cascade.length ? item.cascade[0] : item.value,
                        }),
                    }),
                    // 分步路径是一次次点出来的，点歪了就会攒出
                    // ["有限公司","有限公司","有限公司"] 这种走不通的东西。
                    // 给一个干净的重来入口，别让人回去手改 JSON。
                    item.canonicalKey
                      ? el("button", {
                          class: "probe",
                          text: "清空这一项",
                          title: `把档案里的 ${item.canonicalKey} 清掉重选`,
                          onclick: () => onAction({ type: "pick", canonicalKey: item.canonicalKey, mode: "clear" }),
                        })
                      : null,
                  ])
                : null,
            ]),
          ]),
        );
      }
      body.append(el("div", { class: "card" }, [el("h2", { text: `填写计划（${view.plan.length}）` }), table]));
    }

    if (view.skipped?.length) {
      const groups = new Map();
      for (const item of view.skipped) {
        const list = groups.get(item.reason) || [];
        list.push(item);
        groups.set(item.reason, list);
      }
      const card = el("div", { class: "card" }, [el("h2", { text: `未填写的控件（${view.skipped.length}）` })]);
      for (const [reason, items] of groups) {
        card.append(
          el("div", {}, [
            el("div", { class: "small", text: `${REASON_TEXT[reason] || reason}（${items.length}）` }),
            el(
              "ul",
              {},
              items.slice(0, 12).map((item) =>
                el("li", { class: "small" }, [
                  el("span", {
                    text:
                      `${item.label || "（无标签）"}${item.required ? "（必填）" : ""}` +
                      // 无标签的控件必须给出别的线索，否则这一行等于什么都没说。
                      (item.label ? "" : ` · ${item.placeholder || item.hint || item.kind || "无线索"}`),
                  }),
                  // 必填下拉在观察阶段被展开枚举过。把站点真正接受的合法值摊出来，
                  // 人才知道档案该怎么写；否则只能拿 no_match 反复试。
                  item.missingStep
                    ? el("div", { text: `缺的是这一步：${item.missingStep}${item.note ? ` —— ${item.note}` : ""}` })
                    : null,
                  // 站点合法值同样做成可点的：清空一项之后就是在这里重新起步的。
                  item.options?.length
                    ? el(
                        "div",
                        { class: "chips" },
                        item.options.slice(0, 30).map((option) =>
                          el("button", {
                            class: "chip",
                            text: option,
                            title: `写回 ${item.canonicalKey || item.label}`,
                            onclick: () =>
                              onAction({
                                type: "pick",
                                canonicalKey: item.canonicalKey,
                                value: option,
                                mode: "set",
                              }),
                          }),
                        ),
                      )
                    : item.optionStatus
                      ? el("div", {}, [
                          el("div", { text: `选项枚举：${REASON_TEXT[item.optionStatus] || item.optionStatus}` }),
                          item.panelText ? el("div", { text: `弹层原文：${item.panelText}` }) : null,
                        ])
                      : null,
                  // 未填写的下拉也要能探查——被清空或没映射上的那些，恰恰最需要看结构。
                  ["custom-select", "popover-picker", "cascader"].includes(item.kind)
                    ? el("button", {
                        class: "probe",
                        text: "探查弹层",
                        onclick: () =>
                        onAction({
                          type: "probe",
                          fieldId: item.fieldId,
                          label: item.label,
                          // 档案里想选的值当认领线索：两个弹层同时开着时，
                          // 「哪个里面有这个字」比「哪个是新开的」可靠。
                          wanted: Array.isArray(item.cascade) && item.cascade.length ? item.cascade[0] : item.value,
                        }),
                      })
                    : null,
                ]),
              ),
            ),
          ]),
        );
      }
      body.append(card);
    }

    if (view.diagnostics) {
      body.append(
        el("div", { class: "card" }, [
          el("h2", { text: "诊断（可全选复制）" }),
          el("div", { class: "small", text: "包含页面字段清单、填写计划、每个下拉的弹层结构与留痕。密码/验证码字段只记为 <secret>；其余业务值会包含在内。" }),
          el("textarea", { class: "dump", readonly: "readonly", text: view.diagnostics }),
        ]),
      );
    }

    // 注意名字：`view.probeResult` 是「探查弹层」这一次的结果，
    // `view.probe` 是常驻的接口嗅探状态（上面那张「需登录」提示卡用它）。
    // 两者曾经共用一个名字，探查结果每次都被嗅探状态盖掉，界面上永远显示失败。
    if (view.probeResult) {
      const p = view.probeResult;
      const card = el("div", { class: "card" }, [
        el("h2", { text: `弹层结构${p.label ? `：${p.label}` : ""}` }),
      ]);
      if (p.ok) {
        card.append(
          el("div", { class: "small", text: `容器类名：${p.panelClass || "（无）"} · 展开方式：${p.strategy || "?"} · 选项识别：${p.loose ? "兜底" : "标准"}` }),
          el("div", { class: "small", text: `识别为选项（${(p.candidates || []).length}）：${(p.candidates || []).join("、") || "（无）"}` }),
          el("div", { class: "small", text: `识别为提示（${(p.hints || []).length}）：${(p.hints || []).join(" / ") || "（无）"}` }),
          el("pre", { text: p.outline || "（结构为空）" }),
        );
      } else {
        // 注意：Element.append(null) 会把字面量 "null" 插进 DOM —— 上一版这里
        // 就在界面上打出了「nullnull」。统一过 el() 的过滤，别直接 append 可空值。
        card.append(
          ...[
            el("div", { text: `探查失败：${REASON_TEXT[p.reason] || p.message || p.reason || "未知原因"}` }),
            p.tried?.length ? el("div", { class: "small", text: `已尝试的展开方式：${p.tried.join(" → ")}` }) : null,
            p.raw ? el("pre", { text: p.raw }) : null,
          ].filter(Boolean),
        );
      }
      body.append(card);
    }

    if (view.missingRequired?.length) {
      body.append(
        el("div", { class: "card bad" }, [
          el("h2", { text: "必填但档案里没有的项" }),
          el("ul", {}, view.missingRequired.map((item) => el("li", { text: item.label }))),
        ]),
      );
    }
  };

  AFY.panel = {
    mount,
    render,
    isMounted: () => Boolean(host),
    expand: (view) => setCollapsed(false, view),
    isCollapsed: () => collapsed,
  };
})();
