// 弹窗识别与分级。
//
// 这个站点在「下一步」和「提交」之后都会弹框，而且弹框有两类完全不同的东西：
// 纯确认（点掉就走）和要求填写/选择的子表单。把它们混在一起处理，就会出现
// 「弹窗开着，引擎却在盘点背景页的字段」这种最难查的错。
//
// 所以这里定下两条纪律：
// 1) 有活动弹窗时，**字段盘点、按钮查找一律限定在活动弹窗子树内**。
// 2) 弹窗文案命中人工词表就停下来交给人——哪怕它长得只是个「确定」。
//    替人点掉一次承诺书，事后没有任何办法证明用户本人同意过。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.dialogs) return;
  const { clean, visible, disabled } = AFY.util;
  const { dom, policy, controls } = AFY;

  // 同一个弹窗会命中好几层（.el-overlay > .el-overlay-dialog > .el-dialog）。
  // 全都列上，再由下面的「只留最内层」把作用域收到最紧的那一层——盘点范围越紧，
  // 越不可能把背景页的字段算进来。
  const DIALOG_SELECTOR = [
    ".el-dialog__wrapper",
    ".el-overlay",
    ".el-overlay-dialog",
    ".el-dialog",
    ".el-message-box__wrapper",
    ".el-message-box",
    ".el-drawer__wrapper",
    ".el-drawer",
    ".ant-modal-wrap",
    ".ant-modal",
    "[role='dialog'][aria-modal='true']",
    "[role='alertdialog']",
  ].join(",");

  // 下拉弹层不是弹窗。它们的 z-index 常常比对话框还高，混进来会让引擎把
  // 「一个展开的下拉」当成「一个要处理的弹窗」，然后去点它里面的选项。
  const NOT_A_DIALOG = ".el-select-dropdown, .el-cascader__dropdown, .ant-select-dropdown, .el-autocomplete-suggestion";

  const BUTTON_SELECTOR = "button, .el-button, .ant-btn, [role='button']";

  const zIndexOf = (el) => {
    let node = el;
    for (let depth = 0; node && depth < 6; depth += 1) {
      const value = Number(getComputedStyle(node).zIndex);
      if (Number.isFinite(value)) return value;
      node = node.parentElement;
    }
    return 0;
  };

  const dialogText = (el) => clean(el.innerText).slice(0, 2000);

  const titleOf = (el) => {
    const node = el.querySelector(
      ".el-dialog__title, .el-message-box__title, .ant-modal-title, .el-drawer__title, [class*='title']",
    );
    return clean(node?.innerText).slice(0, 200);
  };

  /** 当前所有可见弹窗，按 z-index 升序；最后一个是活动弹窗。 */
  const openDialogs = () => {
    const found = dom
      .queryAll(DIALOG_SELECTOR)
      .filter((el) => visible(el))
      .filter((el) => !el.matches(NOT_A_DIALOG) && !el.closest(NOT_A_DIALOG))
      // 遮罩层和它内部的对话框会同时命中，去掉「包含另一个候选」的外层，只留最内层。
      .filter((el, _index, list) => !list.some((other) => other !== el && el.contains(other)));
    return found
      .map((el) => ({ el, z: zIndexOf(el) }))
      .sort((a, b) => a.z - b.z)
      .map((item) => item.el);
  };

  const buttonsIn = (el) =>
    [...el.querySelectorAll(BUTTON_SELECTOR)]
      .filter((node) => visible(node) && !disabled(node))
      // 关闭图标不是一个决定，不参与分类。站点常把它渲染成
      // `<button aria-label="Close" class="el-message-box__headerbtn">`，
      // 只按 el-dialog__headerbtn 过滤会让它以「Close」的名字混进按钮清单。
      .filter((node) => !node.className.toString().includes("headerbtn"))
      .map((node) => ({ node, label: clean(node.innerText || node.getAttribute("aria-label")) }))
      .filter((item) => item.label)
      .filter((item) => !item.node.className.toString().includes("close") || !AFY.policy.isCloseLabel(item.label));

  /** 弹窗里可填写的业务字段（已排除密码/验证码/文件）。 */
  const fieldsIn = (el) =>
    [...el.querySelectorAll(controls.FIELD_SELECTOR)].filter((node) => {
      const kind = controls.kindOf(node);
      if (kind === "secret" || kind === "file" || kind === "readonly") return false;
      return controls.visibleControl(node, kind) && !disabled(node);
    });

  /**
   * 分级。返回的 action 有四种取值，对应四条完全不同的处理路径：
   * - `human`      停下来交给人。法律意思表示、身份核验、不可撤销动作。
   * - `validation` 站点校验没通过。**不自动点掉**——点掉之后页面照样不前进，
   *                而站点已经把原因写在弹窗里了，那句话才是人要的东西。
   * - `fill`       弹窗里有要填的东西，当独立子表单盘点填写，填完仍需人确认。
   * - `confirm`    纯提示/步骤确认，可以在预算内自动点掉。
   */
  const classify = (el) => {
    const text = dialogText(el);
    const title = titleOf(el);
    const buttons = buttonsIn(el);
    const fields = fieldsIn(el);
    const haystack = `${title} ${text}`;

    if (policy.isHumanOnly(haystack)) {
      return { action: "human", reason: "human_only", title, text, buttons, fields };
    }
    // 弹窗里出现最终提交措辞时，即使没命中人工词表也不自动点。
    if (buttons.some((item) => policy.isFinalSubmit(item.label))) {
      return { action: "human", reason: "final_submit_inside", title, text, buttons, fields };
    }
    // 校验提示必须排在 fill 之前：有些校验弹窗里带一个「修改成这个」的输入框，
    // 那更不能替人填。
    if (policy.isValidation(haystack)) {
      return { action: "validation", reason: "site_validation", title, text, buttons, fields };
    }
    // 知情确认（「一经申报无法修改企业类型」这类）：站点在告知一个后果，等人点头。
    // 默认仍然停下来把原话摆出来；用户看过并授权之后，本次运行内可以自动点掉。
    // 它排在 human_only 之后——两者同时命中时按更严的那一类处理。
    if (policy.isAcknowledge(haystack)) {
      return { action: "acknowledge", reason: "needs_acknowledge", title, text, buttons, fields };
    }
    if (fields.length) {
      return { action: "fill", reason: "has_fields", title, text, buttons, fields };
    }
    return { action: "confirm", reason: "informational", title, text, buttons, fields };
  };

  /** 纯确认弹窗里那个「确定」。找不到就返回 null——找不到时不乱点。 */
  const confirmButton = (el) => {
    const buttons = buttonsIn(el);
    const safe = buttons.filter(
      (item) => !policy.isHumanOnly(item.label) && !policy.isFinalSubmit(item.label),
    );
    const exact = safe.filter((item) => policy.PURE_INFO_CONFIRM.test(item.label));
    if (exact.length === 1) return exact[0];
    if (exact.length > 1) {
      const primary = exact.find((item) => item.node.classList.contains("el-button--primary"));
      if (primary) return primary;
      return null;
    }
    return null;
  };

  /**
   * 填写型弹窗里的**推进**按钮（「继续申请」这类）。找不到就返回 null，绝不乱点。
   *
   * 与 confirmButton 分开：那个找的是「确定/知道了」这类纯确认；这里找的是
   * 「填完了，往下走」。同样排除人工词表与最终提交——终局动作永远要人授权。
   */
  const proceedButton = (el) => {
    const buttons = buttonsIn(el);
    const safe = buttons.filter(
      (item) =>
        !policy.isHumanOnly(item.label) &&
        !policy.isFinalSubmit(item.label) &&
        !policy.isCloseLabel(item.label),
    );
    const hits = safe.filter((item) => policy.isDialogProceed(item.label));
    if (hits.length === 1) return hits[0];
    if (hits.length > 1) {
      // 多个都像推进时，只认主按钮；仍然分不清就交给人——不抛硬币。
      const primary = hits.find((item) => item.node.classList.contains("el-button--primary"));
      return primary || null;
    }
    return null;
  };

  const describe = (el) => {
    const info = classify(el);
    return {
      title: info.title,
      text: info.text.slice(0, 400),
      action: info.action,
      reason: info.reason,
      buttons: info.buttons.map((item) => item.label).slice(0, 8),
      fieldCount: info.fields.length,
    };
  };

  AFY.dialogs = {
    DIALOG_SELECTOR,
    openDialogs,
    active: () => openDialogs().slice(-1)[0] || null,
    classify,
    confirmButton,
    proceedButton,
    buttonsIn,
    fieldsIn,
    describe,
    titleOf,
    dialogText,
  };
})();
