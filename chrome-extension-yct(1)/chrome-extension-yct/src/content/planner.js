// 盘点结果 + 企业档案 → 一份填写计划。
//
// 这一层只做「哪个字段对应档案里的哪条事实」，不碰 DOM。它的产物必须是可以摊在
// 界面上让人逐条核对的：每一条都带来源键、期望值、匹配依据，以及**没能映射上的
// 原因**。此前把「没映射上」和「这一页本来就没这个字段」混在一起静默跳过，
// 结果就是「下拉总是选不中」查无实据。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.planner) return;
  const { clean, readPath } = AFY.util;
  const { fieldMap, policy, site } = AFY;

  const SKIP_KINDS = new Set(["secret", "file", "readonly", "unknown"]);

  // 「备用字号1」「备选名称」「其他联系人」这类：它们要的是**另一个**值，
  // 而不是主字段那一个。模糊匹配（标签里含「字号」）会把主字段的值灌进去，
  // 于是同一个名字被提交三遍——实测在名称申报的确认弹窗里发生过。
  const ALTERNATE_LABEL = /(备用|备选|其[他它]|第[二三四五六七八九十]|另一?个?)/;

  const orderIndex = (key) => {
    const index = site.fieldOrder.indexOf(key);
    return index < 0 ? site.fieldOrder.length : index;
  };

  const stringify = (value) => {
    if (value == null) return "";
    // 级联值用 " / " 拼只是给人看的；真正逐级选用的是 plan.cascade 里的原数组。
    if (Array.isArray(value)) return value.map(clean).filter(Boolean).join(" / ");
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
  };

  /**
   * @param {Array} fields  page.inventory() 的结果
   * @param {object} dossier 企业档案（enterprise_registration.v1 的子集）
   */
  const build = (fields, dossier) => {
    const plan = [];
    const skipped = [];

    for (const field of fields) {
      const base = {
        fieldId: field.id,
        label: field.label,
        section: field.section,
        kind: field.kind,
        required: field.required,
        current: field.value,
        placeholder: field.placeholder,
        hint: field.hint,
      };

      if (SKIP_KINDS.has(field.kind)) {
        skipped.push({ ...base, reason: field.kind === "secret" ? "secret" : field.kind });
        continue;
      }
      if (field.disabled) {
        skipped.push({ ...base, reason: "disabled" });
        continue;
      }
      // 法律意思表示永远不由插件勾选。把原文摊出来让人自己点。
      if ((field.kind === "checkbox" || field.kind === "radio") && policy.isDeclaration(field.label)) {
        skipped.push({ ...base, reason: "declaration_needs_user" });
        continue;
      }

      const hits = fieldMap.candidates(field);
      if (!hits.length) {
        skipped.push({ ...base, reason: "unmapped" });
        continue;
      }
      if (hits.length > 1) {
        // 两个都说得通就是一次歧义，要报告，不抛硬币。
        skipped.push({
          ...base,
          reason: "ambiguous_mapping",
          candidates: hits.map((hit) => hit.path),
        });
        continue;
      }

      // 「备用/备选」类字段只接受**指向备用键本身**的映射。
      //
      // 实测踩过：确认弹窗里的「备用字号1 / 备用字号2」（非必填，本意是让人另填两个
      // **不同的**备选名称）标签含「字号」，被 `contains:字号` 映射到主字段
      // enterprise.name.trade_name，主字号被原样灌了进去——同一个名字提交三遍。
      // 这不是帮忙，是往申报件里塞错误数据。
      //
      // 判据放在匹配**之后**：档案里有 alternative_1/2 时，「备用字号1」会精确命中
      // 它（精确优先于包含），那条路必须放行；只有落到主字段上才拦。
      if (ALTERNATE_LABEL.test(base.label) && !/alternati|alternate|backup|备用|备选/i.test(hits[0].path)) {
        skipped.push({ ...base, reason: "alternate_needs_user", canonicalKey: hits[0].path });
        continue;
      }

      // 主路径取平台词汇，读不到再退到技能包词汇——档案按哪一套写都认。
      const paths = [hits[0].path, ...(hits[0].alt || [])].map((path) =>
        fieldMap.resolve(path, field.occurrence),
      );
      const key = paths[0];
      let raw;
      let usedPath = key;
      for (const path of paths) {
        const found = readPath(dossier, path);
        if (found !== undefined && found !== null && found !== "") {
          raw = found;
          usedPath = path;
          break;
        }
      }
      const value = stringify(raw);
      if (!value) {
        skipped.push({ ...base, reason: "no_fact", canonicalKey: key });
        continue;
      }

      // 分步选择器：档案直接给数组时数组优先；否则按 site.stepPickers 声明的步骤
      // 逐个去档案里取值拼路径。缺哪一步要明说缺哪一步——只丢一个「选不中」
      // 会让人去改一个本来就没错的值。
      let steps = Array.isArray(raw) ? raw.map(clean).filter(Boolean) : null;
      let missingStep = "";
      const picker = site.stepPickers?.[hits[0].path];
      if (!steps && picker) {
        const resolved = picker.steps.map((path) => ({
          path,
          value: stringify(readPath(dossier, fieldMap.resolve(path, field.occurrence))),
        }));
        const gap = resolved.find((item) => !item.value);
        if (gap) missingStep = gap.path;
        else steps = resolved.map((item) => item.value);
      }
      // 缺某一步的值时**不跳过**：企业类型可能在上一页已经选过，弹层的第二级也许
      // 直接就可用。跳过等于替站点做了一个我们没资格做的判断。改为照常尝试单级选择，
      // 把缺口一路带到结果里——真撞上 needs_prior_step 时，一句话就能说清该补什么。
      plan.push({
        ...base,
        canonicalKey: usedPath,
        value,
        raw,
        // 分步/级联字段的完整路径。单级时为 null，走普通的一次择一。
        // `value` 仍然是**最后一级**的值：控件最终显示的是它，回读核验比的也是它。
        cascade: steps && steps.length > 1 ? steps : null,
        pathLabel: steps && steps.length > 1 ? steps.join(" / ") : "",
        stepNote: picker ? picker.note || "" : "",
        // 这一项是分步选择器，但档案里缺了这一步的值。只在真失败时才需要说出来。
        stepGap: missingStep,
        matchedBy: hits[0].exact ? "exact_label" : `contains:${hits[0].matchedAlias}`,
        order: orderIndex(hits[0].path),
      });
    }

    // 级联字段先写：政务下拉的下游选项由上游决定，顺序错了下游要么为空、
    // 要么读到上一次的残留。
    plan.sort((a, b) => a.order - b.order);

    const missingRequired = skipped.filter(
      (item) => item.required && (item.reason === "no_fact" || item.reason === "unmapped"),
    );

    return { plan, skipped, missingRequired };
  };

  AFY.planner = { build, stringify };
})();
