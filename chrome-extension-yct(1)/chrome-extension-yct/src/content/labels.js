// 标签、分区、必填状态的识别。
//
// 政务表单很少给 <label for>，Element Plus 把标签放在 .el-form-item__label 里，
// 而 aria-* 基本缺失。所以识别链是「无障碍属性 → 组件约定 → 兜底占位符」，
// 一级都不能少：只认 aria 会几乎全空，只认组件类名会在站点换组件时全废。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.labels) return;
  const { clean } = AFY.util;

  const FORM_ITEM = ".el-form-item, .ant-form-item, .form-item, [role='group']";
  const LABEL_IN_ITEM = "label, .el-form-item__label, .ant-form-item-label, legend, .item-label";
  const SECTION = [
    ".el-card__header",
    ".el-collapse-item__header",
    ".el-tabs__item.is-active",
    "fieldset > legend",
    ".section-title",
    ".title",
    "h1, h2, h3, h4",
  ].join(",");

  const labelOf = (el) => {
    if (!AFY.util.isElement(el)) return "";

    if (el.labels?.length) {
      const text = [...el.labels].map((item) => clean(item.innerText)).filter(Boolean).join(" / ");
      if (text) return text;
    }

    const labelledBy = clean(el.getAttribute("aria-labelledby"));
    if (labelledBy) {
      const root = el.getRootNode();
      const text = labelledBy
        .split(/\s+/)
        .map((id) => clean(root.getElementById?.(id)?.innerText))
        .filter(Boolean)
        .join(" / ");
      if (text) return text;
    }

    const aria = clean(el.getAttribute("aria-label"));
    if (aria) return aria;

    const wrapping = el.closest("label");
    if (wrapping) {
      // 包裹式 label 会把控件自己的文本也算进来，先减掉控件占的那部分。
      const text = clean(wrapping.innerText);
      if (text) return text;
    }

    const item = el.closest(FORM_ITEM);
    const itemLabel = item?.querySelector(LABEL_IN_ITEM);
    if (itemLabel) {
      const text = clean(itemLabel.innerText).replace(/[:：*＊]\s*$/, "");
      if (text) return text;
    }

    return clean(el.getAttribute("placeholder") || el.getAttribute("title"));
  };

  /**
   * 分区标题：用于消歧重名字段。往上找最近的卡片/折叠面板/标题，
   * 找不到就用同一容器里最近的一个前置标题元素。
   */
  const sectionOf = (el) => {
    let node = el?.closest?.(".el-card, .el-collapse-item, .el-tab-pane, fieldset, section, .panel");
    let guard = 0;
    while (node && guard < 6) {
      const heading = node.querySelector(SECTION);
      const text = clean(heading?.innerText).slice(0, 120);
      if (text) return text;
      node = node.parentElement?.closest(".el-card, .el-collapse-item, .el-tab-pane, fieldset, section, .panel");
      guard += 1;
    }
    // 兜底：向前找最近的标题文本节点。
    let prev = el?.closest(FORM_ITEM) || el;
    guard = 0;
    while (prev && guard < 40) {
      prev = prev.previousElementSibling || prev.parentElement;
      guard += 1;
      if (!prev) break;
      if (prev.matches?.(SECTION)) return clean(prev.innerText).slice(0, 120);
      const heading = prev.querySelector?.(SECTION);
      if (heading) return clean(heading.innerText).slice(0, 120);
    }
    return "";
  };

  const isRequired = (el) => {
    if (el?.required || el?.getAttribute?.("aria-required") === "true") return true;
    const item = el?.closest?.(FORM_ITEM);
    if (!item) return false;
    return Boolean(
      item.classList.contains("is-required") ||
        item.querySelector("[aria-required='true'], .required, .ant-form-item-required, .is-required"),
    );
  };

  /** 同一页里同名字段的序号，用于把「第 2 个投资人的姓名」对到 investors[1]。 */
  const occurrenceOf = (el, label, all) => {
    const target = clean(label);
    if (!target) return 0;
    const peers = all.filter((item) => clean(item.label) === target);
    const index = peers.findIndex((item) => item.el === el);
    return index < 0 ? 0 : index;
  };

  AFY.labels = { labelOf, sectionOf, isRequired, occurrenceOf, FORM_ITEM };
})();
