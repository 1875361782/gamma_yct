// 一页的观察层：盘点字段、算指纹、认阶段、收集校验错误、找可用动作。
//
// 指纹的用途只有一个——**判断句柄是否还有效**。页面一变（翻页、弹窗开合、加了一行
// 投资人），之前盘点出来的元素引用与字段编号就全部作废，必须重新盘点。这条纪律
// 不成立时，最典型的后果是把第 2 个投资人的信息填进第 1 行。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.page) return;
  const { clean, visible, disabled } = AFY.util;
  const { dom, labels, controls, policy, site, dialogs } = AFY;

  let sequence = 0;

  const hash = (text) => {
    let value = 0;
    for (let index = 0; index < text.length; index += 1) {
      value = (value * 31 + text.charCodeAt(index)) | 0;
    }
    return (value >>> 0).toString(36);
  };

  const headings = (scope) =>
    dom
      .queryAll("h1, h2, h3, .el-dialog__title, .step-title, .page-title, .el-steps__title", scope)
      .filter(visible)
      .map((el) => clean(el.innerText))
      .filter(Boolean)
      .slice(0, 12);

  const stepLabels = () =>
    dom
      .queryAll(".el-step__title, .el-steps .is-process, .ant-steps-item-title", null)
      .filter(visible)
      .map((el) => clean(el.innerText))
      .filter(Boolean)
      .slice(0, 12);

  const validationErrors = (scope) =>
    dom
      .queryAll(
        ".el-form-item__error, .ant-form-item-explain-error, .el-message--error, .error-message, .error-tip",
        scope,
      )
      .filter(visible)
      .map((el) => clean(el.innerText))
      .filter(Boolean)
      .slice(0, 30);

  /**
   * 盘点当前作用域里的可填字段。
   *
   * scope 由调用方给：有活动弹窗时传弹窗元素，否则传 null（整页）。混着盘会让
   * 「弹窗要填的字段」和「背景页的字段」出现在同一份计划里。
   */
  const inventory = (scope = null) => {
    const found = dom.queryAll(controls.FIELD_SELECTOR, scope);
    const rows = [];
    for (const el of found) {
      const kind = controls.kindOf(el);
      if (!controls.visibleControl(el, kind)) continue;
      const label = labels.labelOf(el).slice(0, 300);
      let id = clean(el.getAttribute("data-afy-field"));
      if (!id) {
        sequence += 1;
        id = `afy-${sequence}`;
        el.setAttribute("data-afy-field", id);
      }
      rows.push({
        id,
        el,
        kind,
        label,
        section: labels.sectionOf(el).slice(0, 120),
        placeholder: clean(el.getAttribute("placeholder")).slice(0, 200),
        // 没标签的控件光报「（无标签）」等于什么都没说。把 id / name / class 带上，
        // 人才能对着页面认出它到底是哪一个。
        hint: [
          el.id && `id=${el.id}`,
          el.getAttribute("name") && `name=${el.getAttribute("name")}`,
          clean(el.className).split(" ").filter(Boolean).slice(0, 2).join("."),
        ]
          .filter(Boolean)
          .join(" ")
          .slice(0, 120),
        required: labels.isRequired(el),
        disabled: disabled(el),
        secret: kind === "secret",
        unsupported: kind === "file",
        value: kind === "secret" ? "" : String(controls.readValue(el, kind)).slice(0, 200),
        options:
          kind === "native-select"
            ? [...el.options].slice(0, 200).map((option) => clean(option.textContent))
            : [],
        framePath: dom.framePathOf(el),
        occurrence: 0,
      });
    }
    for (const row of rows) row.occurrence = labels.occurrenceOf(row.el, row.label, rows);
    return rows;
  };

  const ACTION_SELECTOR = "button, .el-button, .ant-btn, input[type='submit'], [role='button']";

  const roleOf = (label) =>
    policy.isFinalSubmit(label) ? "submit" : policy.isNext(label) ? "next" : "other";

  const actions = (scope = null) =>
    dom
      .queryAll(ACTION_SELECTOR, scope)
      .filter((el) => visible(el) && !disabled(el))
      .map((el) => ({ el, label: clean(el.innerText || el.getAttribute("aria-label")).slice(0, 60) }))
      .filter((item) => item.label)
      .map((item) => ({ ...item, role: roleOf(item.label) }));

  /**
   * **被过滤掉的**按钮：不可见或被禁用的那些。
   *
   * 为什么要单独记：actions 只收「可见且可点」的，于是「按钮消失了」和「按钮还在
   * 但被站点禁用了」在诊断里长得一模一样——都是清单里没有它。可这两件事的下一步
   * 完全不同：前者要找页面是不是已经变了，后者是站点在等某个校验回来，得看它在等什么。
   * 实测卡在这里过：四个字段全填好、名称也生成了，「下一步」却从清单里消失，
   * 无从判断是哪一种。
   */
  const inactiveActions = (scope = null) =>
    dom
      .queryAll(ACTION_SELECTOR, scope)
      .filter((el) => !visible(el) || disabled(el))
      .map((el) => ({
        label: clean(el.innerText || el.getAttribute("aria-label")).slice(0, 60),
        role: roleOf(clean(el.innerText || el.getAttribute("aria-label"))),
        why: !visible(el) ? "hidden" : "disabled",
      }))
      .filter((item) => item.label)
      .slice(0, 20);

  const stageOf = (text) => {
    for (const stage of site.stages) {
      if (stage.test.test(text)) return stage;
    }
    return { id: "unknown_stage", label: "未识别阶段" };
  };

  /**
   * 页面快照。scope 自动跟随活动弹窗——观察层和执行层看到的必须是同一个作用域，
   * 否则「界面上显示 3 个字段、实际去填 30 个」这种事迟早发生。
   */
  const snapshot = () => {
    const dialog = dialogs.active();
    const scope = dialog || null;
    const fields = inventory(scope);
    const head = headings(scope);
    const steps = stepLabels();
    const errors = validationErrors(scope);
    const acts = actions(scope);
    const stage = stageOf([...head, ...steps, document.title].join(" "));

    const fingerprint = hash(
      [
        location.pathname + location.hash,
        document.title,
        head.join("|"),
        steps.join("|"),
        fields.map((field) => `${field.label}:${field.kind}:${field.required ? 1 : 0}`).join("|"),
        acts.map((action) => `${action.role}:${action.label}`).join("|"),
        errors.join("|"),
        dialog ? `dialog:${dialogs.titleOf(dialog)}` : "",
      ].join("\n"),
    );

    return {
      url: `${location.origin}${location.pathname}${location.hash}`,
      title: document.title,
      stage,
      headings: head,
      steps,
      fields,
      actions: acts,
      // 被禁用/隐藏的按钮。「下一步」不在 actions 里时，靠它区分「页面已经变了」
      // 和「站点把按钮禁用了、在等某个校验」——两者的下一步完全不同。
      inactiveActions: inactiveActions(scope),
      errors,
      dialog: dialog ? dialogs.describe(dialog) : null,
      dialogEl: dialog,
      scope,
      fingerprint,
      inBusinessOrigin: site.businessOrigins.includes(location.origin),
    };
  };

  const readReceipt = () => {
    const text = clean(document.body?.innerText || "").slice(0, 200000);
    const success = site.receipt.success.test(text);
    const match = site.receipt.reference.exec(text);
    return {
      confirmed: Boolean(success || match),
      referenceNo: match ? match[1] : "",
      statusText: success ? clean(text.match(site.receipt.success)?.[0] || "") : "",
    };
  };

  AFY.page = {
    snapshot,
    inventory,
    actions,
    inactiveActions,
    validationErrors,
    headings,
    readReceipt,
    hash,
    stageOf,
  };
})();
