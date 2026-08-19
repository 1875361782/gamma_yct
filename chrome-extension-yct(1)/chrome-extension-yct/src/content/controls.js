// 控件分类、读写与「稳定回读」。
//
// 分类判据用**容器类名**而不是 ARIA 角色，两条踩过的坑决定了这个选择：
//
// 1) Element 的 filterable select 在内层 input 聚焦时会把 readOnly 置回 false。
//    按 readOnly 判定，只要那一刻恰好聚焦过，下拉就会被当成普通文本框走 JS 直接
//    赋值——值进不了组件的数据模型，而回读读到的正是 JS 刚写进去的那个值，于是
//    **核验假阳性通过**，页面随后重渲染把它清空。回读核验的全部意义就是识破这种
//    情况，不能让它漏判。
// 2) el-autocomplete 的结构是 <div role="combobox"><input role="textbox">，它允许
//    自由输入（本站「行业表述」正是这种）。把祖先的 role=combobox 算进来，就会逼
//    它去匹配一个根本不存在的选项。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.controls) return;
  const { clean, delay, nextFrame, visible, disabled, isElement } = AFY.util;
  const { policy, labels } = AFY;

  const CHOICE_WRAPPER = ".el-checkbox, .el-radio, .el-checkbox-button, .el-radio-button, .ant-checkbox-wrapper, .ant-radio-wrapper, label";

  const FIELD_SELECTOR = [
    "input:not([type=hidden]):not([type=button]):not([type=submit]):not([type=reset]):not([type=image])",
    "textarea",
    "select",
    "[contenteditable='true']",
  ].join(",");

  const kindOf = (el) => {
    // 跨文档安全的判定，理由见 util.isElement——子应用在 blob: iframe 里，
    // 用 instanceof 会把它的字段全判成 unknown。
    if (!isElement(el)) return "unknown";
    const type = clean(el.getAttribute("type")).toLowerCase();
    if (policy.isSecret(el, labels.labelOf(el))) return "secret";
    if (type === "file") return "file";
    if (type === "checkbox") return "checkbox";
    if (type === "radio") return "radio";
    if (el.tagName === "SELECT") return "native-select";
    if (el.getAttribute("contenteditable") === "true") return "text";

    // 顺序不能动：autocomplete 必须先判，否则会被下面的 combobox/popover 分支吃掉。
    if (el.closest(".el-autocomplete, .ant-select-auto-complete")) return "autocomplete";
    if (el.closest(".el-cascader")) return "cascader";
    if (el.closest(".el-date-editor, .ant-picker") || type === "date") return "date";
    if (el.closest(".el-select, .ant-select")) return "custom-select";
    // 只认元素**自身**是触发器，祖先不算（见文件头第 2 条）。
    if (el.getAttribute("role") === "combobox" || el.getAttribute("aria-haspopup") === "listbox") {
      return "custom-select";
    }
    const readonly = Boolean(el.readOnly || el.getAttribute("aria-readonly") === "true");
    // 这里必须要求只读：同样挂着 el-popover__reference 的「名称字号」是真正的文本
    // 输入框，误判成选择器就永远填不进去。
    if (readonly && el.closest(".el-popover__reference")) return "popover-picker";
    if (readonly) return "readonly";
    return "text";
  };

  const OPENS_PANEL = new Set(["custom-select", "popover-picker", "cascader"]);
  const isPanelControl = (kind) => OPENS_PANEL.has(kind);

  /**
   * 可见性。Element Plus 把 checkbox/radio 的真实 <input> 藏起来（透明、零尺寸），
   * 直接对 input 判可见会让整页的勾选项全部消失，所以这两类看外层包装。
   */
  const visibleControl = (el, kind) => {
    if (kind === "checkbox" || kind === "radio") {
      const wrapper = el.closest(CHOICE_WRAPPER);
      return visible(wrapper || el);
    }
    return visible(el);
  };

  const readValue = (el, kind) => {
    if (!isElement(el)) return "";
    if (kind === "checkbox") return Boolean(el.checked);
    if (kind === "radio") return el.checked ? clean(labels.labelOf(el) || el.value || "true") : "";
    if (kind === "native-select") {
      return clean(el.selectedOptions?.[0]?.textContent || el.value);
    }
    if (el.getAttribute("contenteditable") === "true") return clean(el.textContent);
    return el.value ?? "";
  };

  const fire = (el, names = ["input", "change", "blur"]) => {
    for (const name of names) {
      const Ctor = name === "input" ? InputEvent : Event;
      el.dispatchEvent(new Ctor(name, { bubbles: true }));
    }
  };

  /**
   * 用原生 setter 写值，绕过框架对 value 属性的劫持，再补事件让 Vue 收到。
   *
   * `commit: false` 用于自动补全：收尾的 blur 正是组件关掉候选面板的信号，
   * 打完字就 blur，候选还没来得及渲染就没了，后面根本无从选起。
   */
  const writeText = (el, value, { commit = true } = {}) => {
    const text = value == null ? "" : String(value);
    if (el.getAttribute("contenteditable") === "true") {
      el.focus();
      el.textContent = text;
      if (commit) fire(el);
      else el.dispatchEvent(new InputEvent("input", { bubbles: true }));
      return;
    }
    // 原型要取**元素自己文档**的那一份。子应用在 blob: iframe 里，它的 input 用的是
    // 子文档的 HTMLInputElement；虽然原生 value setter 跨文档调用通常也认（brand
    // check 看内部槽），但没有任何规范承诺这一点，而这里写错的后果是「值写不进去」
    // 这类最难查的静默失败。取不到就退回顶层的那份。
    const view = el.ownerDocument?.defaultView || window;
    const Ctor = el.tagName === "TEXTAREA" ? view.HTMLTextAreaElement : view.HTMLInputElement;
    const prototype =
      Ctor?.prototype ||
      (el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype);
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    el.focus();
    // 先清空再写：站点有几处输入框做了「累加」式的 input 监听，直接覆盖会拼接。
    if (setter) setter.call(el, "");
    else el.value = "";
    el.dispatchEvent(new InputEvent("input", { bubbles: true }));
    if (setter) setter.call(el, text);
    else el.value = text;
    if (!commit) {
      // 只通知「值变了」，不收尾。自动补全要靠这一步把候选拉出来。
      el.dispatchEvent(new InputEvent("input", { bubbles: true }));
      return;
    }
    fire(el);
    // 合成的 blur 事件能触发组件的 @blur 校验，但不会真的移走焦点。焦点留在这里，
    // 下一个字段展开的弹层可能被这个还聚焦的输入框抢回去，所以显式失焦一次。
    el.blur?.();
  };

  const selectNativeOption = (el, wanted) => {
    const options = [...el.options];
    const match = AFY.util.chooseOne(
      wanted,
      options.map((option) => clean(option.textContent)),
    );
    if (match.status === "none" || match.status === "ambiguous") return match;
    const option = options.find((item) => clean(item.textContent) === match.value);
    el.value = option.value;
    fire(el);
    return match;
  };

  /**
   * 勾选 / 取消勾选。点外层包装而不是隐藏的 input——Element Plus 的事件挂在包装上，
   * 点 input 有时毫无反应，而「毫无反应」在回读里长得和「站点拒绝了这个值」一样。
   *
   * 可信通道（CDP）可用时优先走它：真实点击对勾选类组件天然有效。点成了就直接
   * 返回，**不再补点**——勾选框是开关，补一下等于把刚做的操作原样撤销。
   *
   * 合成兜底那一路的补点必须**按方向判**，不能按「还没勾上」判：档案要求取消勾选
   * 时，第一击成功取消之后 `!el.checked` 恰好成立，补点又把它勾了回去，净效果为零，
   * 而回读只能报「没填上」——这个字段于是永远填不成「否」。传入期望状态，
   * 只有状态确实没变到位才补这一下。
   */
  const clickChoice = async (el, desired) => {
    const wrapper = el.closest(CHOICE_WRAPPER);
    const target = wrapper?.querySelector(".el-checkbox__inner, .el-radio__inner") || wrapper || el;
    if (AFY.trusted?.enabled()) {
      const result = await AFY.trusted.click(target);
      if (result.ok) return;
      AFY.trusted.noteFallback();
      // 见 selects.clickNode 的同名分支：序列落地一半时补合成点击会造成双击，
      // 对勾选框就是「勾上又取消」。这一下到底成没成，交给回读核验。
      if (result.reason === "interrupted") return;
    }
    // view 用元素自己文档的窗口：组件读 event.view 时，顶层 window 对子应用里的
    // 元素是错的。
    const view = el.ownerDocument?.defaultView || window;
    const before = Boolean(el.checked);
    target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view }));
    if (target === el) return;
    // 没给期望态时（radio 只有「选上」一个方向），退回「状态没变就补一下」。
    const missed = desired === undefined ? Boolean(el.checked) === before : Boolean(el.checked) !== desired;
    if (missed) el.click();
  };

  /**
   * 稳定回读：写完等一帧再读一次，隔 500ms 再读一次，两次一致才算数。
   *
   * 单次回读挡不住这套站点最常见的失败：JS 直接赋值 → 立刻读到自己写的值 → 判定
   * 通过 → Vue 下一轮渲染按数据模型把它清空。两次采样之间必须跨过至少一个渲染周期。
   */
  const stableRead = async (el, kind, { settle = 500 } = {}) => {
    await nextFrame();
    const first = readValue(el, kind);
    await delay(settle);
    const second = readValue(el, kind);
    return {
      value: second,
      stable: String(first) === String(second),
      first: String(first),
    };
  };

  AFY.controls = {
    FIELD_SELECTOR,
    CHOICE_WRAPPER,
    kindOf,
    isPanelControl,
    visibleControl,
    readValue,
    writeText,
    selectNativeOption,
    clickChoice,
    stableRead,
    fire,
    disabled,
  };
})();
