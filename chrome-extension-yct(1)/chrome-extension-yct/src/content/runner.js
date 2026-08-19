// 一页的执行循环：观察 → 计划 → 填写 → 逐条回读核验 → 处理弹窗 → 推进。
//
// 三条不可动摇的纪律：
// 1) 每个字段执行前重新用标记取回元素。指纹变了就作废重来——政务表单加一行投资人
//    会重排整片 DOM，拿着上一轮的引用去写，值会落到别人的行里。
// 2) 有一个字段没写进去，就不点「下一步」。写不进去的原因要如实分类留痕。
// 3) 最终提交是一次性闸门：需要用户在本插件里单独授权，且一次运行只允许一次。
//    结果不明确时**不重试**——重试一次申报比不重试危险得多。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.runner) return;
  const { clean, delay, until, chooseOne } = AFY.util;
  const { dom, page, planner, controls, selects, dialogs, policy, site } = AFY;

  // 展开枚举一个下拉要 3~4 次 DOM 往返 + 最长 2.4s 等待。一页十几个会明显拖慢
  // 识别，所以设一个成本闸；被跳过的那些必须在界面上说出来，不能让人以为都查过了。
  const ENUMERATE_LIMIT = 8;
  const TRANSITION_TIMEOUT = 18000;

  const state = {
    journal: [],
    autoDialogCount: 0,
    submitAttempted: false,
    lastFingerprint: "",
    // 用户是否授权「知情类提示由插件代点」。永远不覆盖 human 那一类
    // （承诺、签署、实名、支付、最终提交）——那一类没有开关。
    acknowledgeAuthorized: false,
    // 授权来自设置页的持久开关（true）还是面板上的一次性授权（false）。
    // 前者是用户对工具的配置，重置不该把它清掉；后者只活到本次运行结束。
    acknowledgePersistent: false,
    // 本次运行已经填过哪些页。**必须跨调用保留**：连续填报会因为「等你确认一个
    // 提示」而中途返回，你确认完再点一次继续——如果这时记录清零，同一页就会被
    // 重填一遍，把已经填好的值冲掉。实测正是这样。
    seenPages: new Set(),
    // 正在跑吗。防止连点两次、或「授权后自动继续」与手动点击撞在一起——
    // 两条链交错着填同一页，结果比不填还糟。
    autoRunning: false,
  };

  const record = (entry) => {
    const item = { at: new Date().toISOString(), ...entry };
    state.journal.push(item);
    if (state.journal.length > 400) state.journal.splice(0, state.journal.length - 400);
    try {
      // service worker 休眠或没有接收方时 sendMessage 会异步 reject，
      // 不接住就是一条 unhandled rejection。留痕投递失败绝不该影响页面执行。
      chrome.runtime.sendMessage({ type: "AFY_JOURNAL", entry: item })?.catch?.(() => {});
    } catch {
      /* 扩展上下文失效（重新加载扩展）时同步抛错，同样忽略 */
    }
    return item;
  };

  /**
   * 从「选中项」里推导出「查询词」：取第一个括号之前的部分。
   *
   * 本站行业表述的候选是「信息科技 (主营行业:互联网信息服务(07532))」，
   * 括号那段只是展示后缀。输入框要的是「信息科技」——拿整串去搜，一个候选都搜不到。
   * 这条推导让档案里只写选中项也能填进去，不必要求人拆成两段。
   */
  const queryOf = (label) => {
    const text = clean(label);
    const cut = text.search(/[（(]/);
    return cut > 0 ? clean(text.slice(0, cut)) : text;
  };

  /**
   * 点按钮（下一步 / 弹窗确定 / 最终提交）：可信通道优先，失败回退原生 click。
   * 先滚进视野再点——CDP 点击走真实 hit-testing，按钮在视口外就点不到；
   * 原生 click 不挑位置，回退路径不受影响。返回实际走的通道，供留痕。
   */
  const pressButton = async (el) => {
    if (AFY.trusted?.enabled()) {
      try {
        el.scrollIntoView?.({ block: "center" });
        await delay(80);
      } catch {
        /* scrollIntoView 失败不拦路 */
      }
      const result = await AFY.trusted.click(el);
      if (result.ok) return "cdp";
      AFY.trusted.noteFallback();
      // 序列落地一半（连接在 mouseup 前断掉）时不补 click：翻页/提交类按钮
      // 被点两次的后果比漏点严重得多。advance 后面本来就等页面指纹变化，
      // 没生效会如实报 page_did_not_transition。
      if (result.reason === "interrupted") return "interrupted";
    }
    el.click();
    return "synthetic";
  };

  /**
   * 一页的「身份」：步骤 + 字段标签集合。
   *
   * 用它回答「翻过去了吗」，**不要用页面指纹**——指纹回答的是「DOM 动了吗」，
   * 站点弹个提示框、异步刷一块区域都会让它变。实测踩过：点完「下一步」站点弹了
   * 个「提示」，指纹一变就被当成翻页成功，于是在同一页上又填了一遍，把刚填好的
   * 值冲掉。同一步骤里翻到下一批字段时身份也会变，所以它不会把真推进误判成原地。
   */
  const identityOf = (snapshot) =>
    `${snapshot.stage.id}|${(snapshot.fields || [])
      // **弹层里的字段不算数。** 组织形式那个 popover 一开，弹层里十几个 radio 就被
      // 盘进字段清单（7 → 19）；等它关掉又变回 7。身份跟着抖，就会把「原地未动」
      // 判成「进了新页」——实测正是这样：翻页失败后引擎以为到了下一页，
      // 在同一页上重填一遍，把刚填好的值冲掉。身份必须只认页面本身的字段。
      .filter((field) => !field.el?.closest?.(AFY.selects.PANEL_SELECTOR))
      // 认**控件标记**（引擎自己打的 data-afy-field），不认标签文本。
      //
      // 标签会随填写内容变：名称申报页那三个拼音选项的标签就是动态生成的名称预览
      // ——字号填了叫「上海愿力成像信息科技」，字号被清空就变成「上海」。
      // 拿它当身份，页面明明没动，身份却变了，于是又一次「以为翻页成功」，
      // 在同一页上重填一遍。标记跟着元素走，页面没换它就不变。
      .map((field) => field.id || field.label)
      .sort()
      .join(",")}`;

  const guardOrigin = () => {
    if (site.businessOrigins.includes(location.origin)) return null;
    return {
      ok: false,
      pause: "origin_not_allowed",
      // `reason` 必须一起给：探查那条路径只读 reason，只有 pause 时界面会渲染出
      // 「探查失败：undefined」——一个什么也没说的错误提示。
      reason: "origin_not_allowed",
      message: `当前页面 ${location.origin} 不在业务域白名单内，插件不在这里做任何动作。`,
    };
  };

  // ---------------------------------------------------------------- 观察

  /**
   * 观察当前页。enumerate 为 true 时顺带展开各个下拉读回真实选项——
   * 这是让「上海」能对上「上海市」的前提：不展开，选项永远是空的。
   */
  const observe = async ({ dossier = {}, enumerate = true } = {}) => {
    const blocked = guardOrigin();
    if (blocked) return blocked;

    const snapshot = page.snapshot();
    const { plan, skipped, missingRequired } = planner.build(snapshot.fields, dossier);
    state.lastFingerprint = snapshot.fingerprint;

    // 枚举顺序：先枚举有档案值的（要拿来选），再枚举「必填但档案里没有」的下拉。
    // 后者看着像浪费，其实是这个面板最有用的一件事：它把站点真正接受的合法值集摊给人看，
    // 人才知道档案该怎么写——否则只能拿「no_match」反复试。
    let enumerated = 0;
    let enumerateSkipped = 0;
    if (enumerate) {
      const targets = [
        ...plan.filter((item) => controls.isPanelControl(item.kind)),
        // 不再只枚举「必填」的：清空了一项之后，它会落到 skipped 里，
        // 而人正需要在这里看到站点的合法值重新选。只按必填过滤，那条路就断了。
        ...skipped.filter((item) => controls.isPanelControl(item.kind) && item.reason !== "disabled"),
      ];
      for (const item of targets) {
        if (enumerated >= ENUMERATE_LIMIT) {
          enumerateSkipped += 1;
          continue;
        }
        const el = dom.byFieldId(item.fieldId);
        if (!el) continue;
        enumerated += 1;
        const result = await selects.enumerate(el);
        item.options = result.options;
        item.optionSource = result.via;
        item.optionStatus = result.status;
        if (result.status === "ok" && item.value) {
          // 级联字段拿第一级去预匹配：整条路径拼起来的字符串不会出现在任何一层选项里。
          const probe = item.cascade?.length ? item.cascade[0] : item.value;
          const match = chooseOne(probe, result.options);
          item.preMatch = match.status;
          item.preMatchValue = match.value || "";
        } else {
          item.preMatch = result.status;
        }
      }
      selects.closePanels();
    }

    const emptyDropdowns = [...plan, ...skipped].filter(
      (item) => item.optionStatus === "empty" || item.optionStatus === "no_panel",
    );
    const hints = [];
    if (emptyDropdowns.length) {
      // 顺序是实测排出来的：这一页卡住时，先怀疑未登录，再怀疑级联前置。
      // 反过来排会把人带到错误的方向上——已经发生过一次。
      hints.push(
        `有 ${emptyDropdowns.length} 个下拉展开后没有任何选项。` +
          `最常见的原因是**尚未登录**（站点的字典接口会被拦截并返回「请登录后再试」），` +
          `其次才是级联前置字段还没选。请先确认右上角已是登录状态。`,
      );
    }
    if (enumerateSkipped) {
      hints.push(`另有 ${enumerateSkipped} 个下拉本轮没有展开枚举（单轮上限 ${ENUMERATE_LIMIT} 个），它们的选项未知。`);
    }

    record({
      action: "observe",
      stage: snapshot.stage.id,
      url: snapshot.url,
      fingerprint: snapshot.fingerprint,
      fieldCount: snapshot.fields.length,
      planCount: plan.length,
      dialog: snapshot.dialog?.title || "",
    });

    return { ok: true, snapshot, plan, skipped, missingRequired, hints };
  };

  // ---------------------------------------------------------------- 填写

  const classifyReadback = (expected, actual, matchLevel) => {
    const want = clean(expected);
    const got = clean(actual);
    if (!got) return "empty_after_write";
    if (want === got) return "match";
    if (["normalized", "prefix", "cascade", "steps"].includes(matchLevel)) {
      return "site_wording";
    }
    if (AFY.util.normalize(want) === AFY.util.normalize(got)) return "site_wording";
    // 站点接受了但改写了（金额补零、日期换格式、证件号加空格）。不能默认通过。
    return "transformed";
  };

  const fillOne = async (item) => {
    const el = dom.byFieldId(item.fieldId);
    if (!el) return { ...item, status: "failed", reason: "element_gone" };
    if (controls.disabled(el)) return { ...item, status: "failed", reason: "disabled" };

    el.scrollIntoView({ block: "center", inline: "center" });
    await delay(120);

    let matchLevel = "exact";
    let scopedBy = "";
    // 路径比向导长时多出来的那几段。填成功了，但要在界面上说出来，
    // 免得档案里一直留着一段没用的值没人知道。
    let extraLevels = [];
    // 分步选择器的逐级执行记录（哪一级、候选池从哪来、点了谁、走哪条通道、点完选中没）。
    let stepTrace = [];
    // 回读要比对的值。多数控件就是 item.value；自动补全是个例外——
    // 输入框最终显示的是查询词，不是下拉里那条带编码后缀的长文案。
    let expected = item.value;
    try {
      switch (item.kind) {
        // 三类弹层控件走同一条路：级联、分步向导、单级下拉本来就是同一件事的三种长相。
        // 有 cascade 就逐级点，没有就一次择一。
        //
        // 失败时把 available / hints / panelText / tried / level 一并带出去。这几样是人
        // 下一步唯一能依据的信息：站点给了哪些合法值、弹层里写的是什么、卡在第几级、
        // 试过哪几种展开姿势。只报一个 reason 等于让人对着「选不中」干瞪眼。
        case "cascader":
        case "custom-select":
        case "popover-picker": {
          const result = await selects.chooseSteps(el, item.cascade || item.value);
          if (!result.ok) {
            return {
              ...item,
              status: "failed",
              reason: result.reason,
              available: result.available,
              hints: result.hints,
              panelText: result.panelText,
              blockedBy: result.blockedBy,
              tried: result.tried,
              level: result.level,
              picked: result.picked,
              groupHint: result.groupHint,
              steps: result.steps,
            };
          }
          matchLevel = result.matchLevel;
          scopedBy = result.scopedBy || "";
          extraLevels = result.extraLevels || [];
          // 成功路径也要留：分步选择器最难查的一种失败是「每一级都报成功，
          // 最后回读却是空的」——那时唯一能说明问题的就是这份逐级记录。
          stepTrace = result.steps || [];
          break;
        }
        case "native-select": {
          const result = controls.selectNativeOption(el, item.value);
          if (result.status === "none" || result.status === "ambiguous") {
            return { ...item, status: "failed", reason: result.status === "none" ? "no_match" : "ambiguous", available: result.candidates.slice(0, 20) };
          }
          matchLevel = result.status;
          break;
        }
        case "checkbox": {
          const desired = ["true", "1", "yes", "是", "选中", "同意"].includes(clean(item.value).toLowerCase());
          // 期望态要传下去：合成兜底那一路靠它决定「点完了要不要再补一下」，
          // 不传的话「取消勾选」会被补点翻回来。
          if (Boolean(el.checked) !== desired) await controls.clickChoice(el, desired);
          break;
        }
        case "radio": {
          const own = clean(el.value);
          const label = clean(AFY.labels.labelOf(el));
          const match = chooseOne(item.value, [own, label].filter(Boolean));
          if (match.status === "none" || match.status === "ambiguous") {
            return { ...item, status: "failed", reason: "no_match", available: [own, label].filter(Boolean) };
          }
          if (!el.checked) await controls.clickChoice(el, true);
          matchLevel = match.status;
          break;
        }
        // 自动补全：打字**只是第一步**，还得从候选里选中。
        //
        // 只写不选是这套站点最隐蔽的假阳性：input 里看着有值，回读读到的正是刚写进去
        // 的那串字，判定通过；而组件的数据模型没更新，站点在下一步把它打回来。
        // 「行业表述」实测就是这种。
        // 自动补全有**两个不同的值**：打什么字（查询词），和选哪一条（绑定编码）。
        //
        // 本站「行业表述」实测：输入框要的是「信息科技」，下拉里那条
        // 「信息科技 (主营行业:互联网信息服务(07532))」的括号部分只是展示后缀。
        // 把整串打进去，站点搜不到任何候选、判定非法、**清空输入框**，
        // 然后弹「请输入正确的行业表述！」。把这两个值当成同一个，这一关就永远过不去。
        //
        // 所以档案支持写成数组：[查询词, 要选的那一条]。只写一个值时两者相同。
        case "autocomplete": {
          const path = (item.cascade || [item.value]).map(clean).filter(Boolean);
          const wantPick = path[path.length - 1];

          // 查询词**不需要档案分两段写**——它就藏在选中项里。
          // 站点候选长成「信息科技 (主营行业:互联网信息服务(07532))」，括号那段是
          // 展示后缀；拿整串去搜一个候选都搜不到，站点还会判非法并清空输入框。
          // 所以依次试：档案显式给的第一段 → 括号前那段 → 整串。第一个搜出候选的赢。
          const queries = [...new Set([path.length > 1 ? path[0] : "", queryOf(wantPick), wantPick].filter(Boolean))];

          let result = { ok: false, reason: "no_suggestion" };
          let usedQuery = queries[0];
          for (const query of queries) {
            usedQuery = query;
            // commit: false —— 不要收尾 blur，那正是组件关掉候选面板的信号，
            // 也是站点「不是合法行业词就清空」那条校验的触发点。
            controls.writeText(el, query, { commit: false });
            result = await selects.pickSuggestion(el, wantPick);
            if (result.ok || result.reason !== "no_suggestion") break;
          }

          // 回读比的是**输入框最终显示的东西**，也就是查询词那一侧。
          expected = usedQuery;

          if (result.ok) {
            matchLevel = result.matchLevel;
            // 选完必须失焦。焦点留在自动补全里，它的候选面板会一直挂着，
            // 下一个字段展开时就会因为「本来就有弹层可见」而被判成打不开——
            // 组织形式那次 no_panel 正是被行业表述的候选面板挡住的。
            el.blur?.();
            break;
          }
          // 所有查询词都搜不出候选 = 这个控件本来就允许自由输入。
          // 把最像合法值的那一段（括号前）留在框里，再补收尾事件。
          if (result.reason === "no_suggestion") {
            const best = queryOf(wantPick);
            expected = best;
            controls.writeText(el, best, { commit: false });
            controls.fire(el);
            el.blur?.();
            break;
          }
          // 有候选却选不中：不能当成功。文本留在框里只会把问题推到下一页。
          // 措辞按 reason 分开——「一个都没匹配上」和「匹配上好几个」要改的东西不一样。
          return {
            ...item,
            status: "failed",
            reason: result.reason,
            available: result.available,
            // 面板上点候选时，把**真正搜出候选的那个查询词**一起带上，
            // 写回档案就是 [查询词, 选中项] 两段，下次一次就中。
            picked: [usedQuery],
            detail:
              result.reason === "ambiguous"
                ? "档案值同时命中多个候选。站点这里的候选常带「(主营行业:…)」后缀，档案要写成与其中一条**完全一致**的整串。"
                : "站点给的候选里没有档案这个值；自动补全必须从候选里选中才作数。",
          };
        }

        default:
          // text / date 走文本写入。
          controls.writeText(el, item.value);
          break;
      }
    } catch (error) {
      return { ...item, status: "failed", reason: "exception", detail: String(error?.message || error).slice(0, 200) };
    }

    const readback = await controls.stableRead(el, item.kind);
    if (!readback.stable) {
      // 两次采样之间值变了 = 页面正在把它改掉或清空。这正是回读核验要抓的假阳性。
      return {
        ...item,
        status: "failed",
        reason: "unstable_readback",
        detail: `写入后先读到「${readback.first}」，${clean(readback.value) ? `再读变成「${readback.value}」` : "再读被清空"}`,
      };
    }

    let verdict = classifyReadback(expected, readback.value, matchLevel);

    // 「输入框只是个触发器」这一类：值在组件的数据模型里，读 input.value 永远是空。
    //
    // 实测（组织形式）：两级都真的点中了（弹层里 is-checked 立在那儿），站点也据此
    // 生成了拟设名称「…有限责任公司」，可这个 popover 的输入框自始至终是空的——
    // 回读于是报 empty_after_write，把一次成功说成了失败。人照着这个结论去查，
    // 只会一次次重填一个本来就填好了的字段。
    //
    // 所以：分步选择器**每一级都验证过确实选中**（steps 里没有一个 false）时，
    // 就以那份逐级证据为准。这不是放宽回读——回读挡的是「写进去又被清空」，
    // 而这里有比 input.value 更直接的证据：站点自己把每一级都标成了选中。
    if (
      verdict === "empty_after_write" &&
      controls.isPanelControl(item.kind) &&
      stepTrace.length &&
      stepTrace.every((step) => step.checkedAfter === true)
    ) {
      verdict = "picked_in_panel";
    }

    // 自动补全写完之后框是空的 = 站点把它判非法清掉了。
    // 这一条不能只报 review 就完事：review 没有候选按钮，人在面板上无路可走，
    // 只能回去手改 JSON——正是那个卡了好几轮的死循环。
    // 所以顺手把候选枚举出来，让人点一下就把「查询词 + 选中项」写回档案。
    if (item.kind === "autocomplete" && verdict === "empty_after_write") {
      const rescueQuery = queryOf(item.cascade?.[0] || item.value);
      controls.writeText(el, rescueQuery, { commit: false });
      const available = await selects.listSuggestions(el); // 只列候选，不选中任何一条
      return {
        ...item,
        status: "failed",
        reason: "cleared_by_site",
        available,
        picked: [rescueQuery],
        detail: `站点把这个值判为非法并清空了输入框。用「${rescueQuery}」搜到的候选列在下面，点一个即可。`,
      };
    }
    const passed = verdict === "match" || verdict === "site_wording" || verdict === "picked_in_panel";
    return {
      ...item,
      status: passed ? "ok" : "review",
      verdict,
      matchLevel,
      scopedBy,
      extraLevels,
      // 没通过时它是唯一的现场；`picked_in_panel` 也要带上——那条结论正是**靠**
      // 这份逐级记录得出的，藏起来就没法复核了。
      steps: passed && verdict !== "picked_in_panel" ? undefined : stepTrace,
      actual: String(readback.value).slice(0, 200),
    };
  };

  /** 按计划填写。dryRun 只报告不动 DOM。 */
  const fill = async ({ dossier = {}, dryRun = false } = {}) => {
    const blocked = guardOrigin();
    if (blocked) return blocked;

    const observed = await observe({ dossier, enumerate: dryRun });
    if (!observed.ok) return observed;
    if (dryRun) return { ...observed, results: [], dryRun: true };

    const results = [];
    let fingerprint = observed.snapshot.fingerprint;
    for (const item of observed.plan) {
      const result = await fillOne(item);
      results.push(result);
      record({
        action: "fill",
        field: item.label,
        canonicalKey: item.canonicalKey,
        status: result.status,
        reason: result.reason || result.verdict || "",
        // 刻意只记状态与来源键，不记业务值：留痕不该变成一份档案副本。
      });
      // item.order 小于清单长度即「它在级联依赖链上」——用 order 而不是比对 key，
      // 因为 canonicalKey 可能是回退词汇里的那一个。
      if (result.status === "ok" && item.order < site.fieldOrder.length) {
        // 上游级联字段刚变，下游的选项集已经不是刚才那份了。让页面把新选项拉回来。
        await delay(500);
        const now = page.snapshot();
        if (now.fingerprint !== fingerprint) fingerprint = now.fingerprint;
      }
    }

    // 填完之后，把**没填上的下拉**的合法值枚举出来。
    //
    // 不做这一步，被清空过的字段在面板上只剩一行「档案里缺这条事实」，
    // 没有任何可点的东西——人只能回去手改 JSON，那正是要消灭的死循环。
    // 填写路径走的是 enumerate:false（为了不拖慢），所以必须在这里补。
    let rescued = 0;
    for (const item of observed.skipped) {
      if (!controls.isPanelControl(item.kind)) continue;
      if (rescued >= ENUMERATE_LIMIT) break;
      const el = dom.byFieldId(item.fieldId);
      if (!el) continue;
      rescued += 1;
      const found = await selects.enumerate(el);
      item.options = found.options;
      item.optionStatus = found.status;
      item.hints = found.hints;
      item.panelText = found.panelText;
    }

    const after = page.snapshot();
    return {
      ok: true,
      results,
      snapshot: after,
      skipped: observed.skipped,
      missingRequired: observed.missingRequired,
      hints: observed.hints,
      failures: results.filter((item) => item.status !== "ok"),
      readyToAdvance:
        results.every((item) => item.status === "ok") &&
        after.errors.length === 0 &&
        observed.missingRequired.length === 0,
    };
  };

  // ---------------------------------------------------------------- 弹窗

  /**
   * 处理当前弹窗。返回 `handled`（已自动点掉）/`needs_user`/`needs_fill`/`none`。
   * 自动点掉的只有纯提示类，且一页最多 MAX_AUTO_DIALOGS 个。
   */
  const handleDialog = async ({ dossier = {} } = {}) => {
    const el = dialogs.active();
    if (!el) return { kind: "none" };

    const info = dialogs.classify(el);
    if (info.action === "human") {
      record({ action: "dialog_pause", title: info.title, reason: info.reason });
      return { kind: "needs_user", info: dialogs.describe(el) };
    }

    // 校验提示不自动点掉。点掉之后页面照样不会前进，而人会只看到「翻页没反应」——
    // 站点其实已经把原因写在这个框里了，那句话才是要交到人手上的东西。
    // 知情确认：站点告知一个后果，等人点头。
    //
    // 默认停下来把**原话**摆给人看——知情权就是这类提示存在的全部意义。人看过之后
    // 可以授权「本次运行内同类提示自动确认」，否则每翻一页都要点一次，连续填报就
    // 退化成手动。授权只在本次运行内有效（重置即失效），并且永远不覆盖 human 那一类。
    if (info.action === "acknowledge") {
      if (!state.acknowledgeAuthorized) {
        record({ action: "dialog_pause", title: info.title, reason: "needs_acknowledge" });
        return {
          kind: "needs_acknowledge",
          info: dialogs.describe(el),
          text: clean(info.text).slice(0, 300),
          reason:
            `站点要你先知悉一件事：「${clean(info.text).slice(0, 160)}」。` +
            "读完之后，你可以自己点它的「确定」，也可以在面板上授权由插件代点（只在本次运行内有效）。",
        };
      }
      const button = dialogs.confirmButton(el);
      if (!button) {
        record({ action: "dialog_pause", title: info.title, reason: "no_safe_confirm_button" });
        return { kind: "needs_user", info: dialogs.describe(el), reason: "没能确定这个弹窗里哪个按钮是安全的确认按钮。" };
      }
      const spokenAck = clean(info.text).slice(0, 200);
      const viaAck = await pressButton(button.node);
      // 授权点掉的每一次都要留痕，并且**记下原话**——事后要能说清插件替人确认了什么。
      record({
        action: "dialog_acknowledge",
        title: info.title,
        button: button.label,
        via: viaAck,
        text: spokenAck,
        authorized: true,
      });
      await delay(600);
      return { kind: "handled", title: info.title, button: button.label, text: spokenAck };
    }

    if (info.action === "validation") {
      record({ action: "dialog_validation", title: info.title, reason: info.reason });
      return {
        kind: "needs_user",
        info: dialogs.describe(el),
        reason: `站点校验没通过：${info.text.slice(0, 200)}。按它说的改完再继续，插件不会替你把这个框点掉。`,
      };
    }

    if (info.action === "fill") {
      const fields = page.inventory(el);
      const { plan, skipped, missingRequired } = planner.build(fields, dossier);
      const results = [];
      for (const item of plan) results.push(await fillOne(item));
      const failures = results.filter((item) => item.status !== "ok");
      record({ action: "dialog_fill", title: info.title, filled: results.length, failures: failures.length });

      // 填完之后要不要替人往下走。
      //
      // 三个条件缺一不可：**每一条都回读通过**（有一条没写进去就不推进——那等于
      // 把问题带到下一页）、**找得到唯一的推进按钮**（「继续申请」这类，且不是最终
      // 提交）、**用户授权过**（设置页那个开关；没授权就仍旧停下来交给人核对）。
      const proceed = dialogs.proceedButton(el);
      if (state.acknowledgeAuthorized && !failures.length && !missingRequired.length && proceed) {
        const via = await pressButton(proceed.node);
        record({
          action: "dialog_proceed",
          title: info.title,
          button: proceed.label,
          via,
          filled: results.length,
          authorized: true,
        });
        await delay(800);
        return { kind: "handled", title: info.title, button: proceed.label, results };
      }

      // 没授权、或有字段没填上、或找不到唯一的推进按钮 → 交给人。
      return {
        kind: "needs_fill",
        info: dialogs.describe(el),
        results,
        skipped,
        missingRequired,
        reason: failures.length
          ? `弹窗里有 ${failures.length} 项没能确认写入，先处理再继续——没填全就往下走会把问题带到下一页。`
          : proceed
            ? "弹窗里的字段已按档案填好，请核对后自己点继续；也可以在设置页开启自动确认，之后这一步就不再打断你。"
            : "弹窗里的字段已按档案填好，但没能确定哪个按钮是安全的「继续」，请你本人点。",
      };
    }

    if (state.autoDialogCount >= policy.MAX_AUTO_DIALOGS) {
      record({ action: "dialog_pause", title: info.title, reason: "auto_budget_exhausted" });
      return { kind: "needs_user", info: dialogs.describe(el), reason: "连续弹出的确认框过多，已停下来交给你。" };
    }

    const button = dialogs.confirmButton(el);
    if (!button) {
      record({ action: "dialog_pause", title: info.title, reason: "no_safe_confirm_button" });
      return { kind: "needs_user", info: dialogs.describe(el), reason: "没能确定这个弹窗里哪个按钮是安全的确认按钮。" };
    }

    state.autoDialogCount += 1;
    // **点掉之前先把它说了什么记下来。**
    //
    // 实测吃过亏：翻页之后站点弹了个标题只有「提示」的框，被归类成纯确认自动点掉，
    // 留痕里只有 title 和按钮名——而页面根本没翻过去。那句正文十有八九就是原因
    // （「请选择组织形式」之类），却被我们自己抹掉了，人只能对着「翻页没反应」干瞪眼。
    // 这与「校验提示不自动点掉」是同一条纪律的两面：分类可能判错，但**证据不能丢**。
    const spoken = clean(info.text).slice(0, 200);
    const via = await pressButton(button.node);
    record({ action: "dialog_confirm", title: info.title, button: button.label, via, text: spoken });
    await delay(600);
    return { kind: "handled", title: info.title, button: button.label, text: spoken };
  };

  // ---------------------------------------------------------------- 推进

  const waitForChange = async (previous) =>
    until(
      async () => {
        const now = page.snapshot();
        return now.fingerprint !== previous ? now : null;
      },
      { timeout: TRANSITION_TIMEOUT, interval: 500 },
    );

  /** 点「下一步」。只允许存在唯一一个 next 动作时才点。 */
  const advance = async ({ dossier = {} } = {}) => {
    const blocked = guardOrigin();
    if (blocked) return blocked;

    const before = page.snapshot();
    if (before.dialog) {
      const handled = await handleDialog({ dossier });
      if (handled.kind !== "handled") return { ok: false, pause: handled.kind, dialog: handled };
    }
    if (before.errors.length) {
      return { ok: false, pause: "validation_errors", errors: before.errors };
    }

    const candidates = before.actions.filter((action) => action.role === "next");
    if (candidates.length === 0) {
      // 「下一步」不在可点清单里，有两种完全不同的原因，必须分开说：
      // 站点把它**禁用**了（多半在等某个异步校验回来，等一会儿再点就行），
      // 还是它**真的不在这一页**（页面已经变了，或这一步本来就没有下一步）。
      const blocked = (before.inactiveActions || []).filter((item) => item.role === "next");
      if (blocked.length) {
        return {
          ok: false,
          pause: "next_action_disabled",
          blockedActions: blocked,
          snapshot: before,
          message:
            `页面上有「${blocked[0].label}」，但它现在是${blocked[0].why === "disabled" ? "禁用" : "隐藏"}状态，点不了。` +
            "站点多半正在校验刚填的内容（名称查重之类），等它变回可点再继续；" +
            "若一直不恢复，看看页面上有没有红字提示。",
        };
      }
      return { ok: false, pause: "no_next_action", snapshot: before };
    }
    if (candidates.length > 1) {
      return { ok: false, pause: "ambiguous_next_action", labels: candidates.map((item) => item.label) };
    }

    state.autoDialogCount = 0;
    const via = await pressButton(candidates[0].el);
    record({ action: "advance", button: candidates[0].label, from: before.stage.id, via });

    let changed = await waitForChange(before.fingerprint);
    // 站点说的最后一句话。翻不过去时它就是原因本身，必须一路带到界面上。
    let spoken = "";

    // **指纹变了不等于翻过去了。**
    //
    // 实测那一幕：点完「下一步」，站点弹出一个标题只有「提示」的框——DOM 变了、
    // 指纹跟着变，于是被判成「已进入新步骤」，而页面其实原地未动。连续填报据此
    // 往下走，在同一页上又填了一遍，把刚填好的值冲掉。
    // 所以：还在同一步、而且有弹窗挡着时，先把弹窗处理掉，再看是否真的推进。
    let guard = 0;
    while (changed && changed.dialog && identityOf(changed) === identityOf(before) && guard < policy.MAX_AUTO_DIALOGS) {
      guard += 1;
      const dialog = await handleDialog({ dossier });
      if (dialog.kind !== "handled") {
        return { ok: false, pause: dialog.kind, dialog, dialogText: dialog.text || "" };
      }
      spoken = dialog.text || spoken;
      changed = await waitForChange(changed.fingerprint);
    }

    if (!changed) {
      // 页面没动，最常见的原因是「下一步」弹出了一个确认框把跳转拦住了。
      const dialog = await handleDialog({ dossier });
      if (dialog.kind === "handled") {
        spoken = dialog.text || spoken;
        changed = await waitForChange(before.fingerprint);
      } else if (dialog.kind !== "none") {
        return { ok: false, pause: dialog.kind, dialog, dialogText: dialog.text || "" };
      }
    }
    // 走到这里 changed 可能有值但**页面身份没变**——那也不算推进。
    if (!changed || identityOf(changed) === identityOf(before)) {
      const now = page.snapshot();
      return {
        ok: false,
        pause: "page_did_not_transition",
        errors: now.errors,
        snapshot: now,
        // 站点自己说的那句话，往往直接就是下一步该做什么。
        dialogText: spoken,
      };
    }
    return { ok: true, snapshot: changed };
  };

  /**
   * 连续填报：填一页 → 核验 → 翻页 → 再填下一页，直到**撞上任何需要人的闸门**。
   *
   * 这是这套东西真正该有的形态（RPA 里叫 attended 模式）：机器把不需要人的步骤
   * 连起来跑，人只在该他做决定的地方出现。它**不放宽任何一道闸门**，只是免去
   * 「填一页点一次、翻一页再点一次」的机械往返。
   *
   * 会停下来的全部情形，每一种都对应一件只有人能做的事：
   * - 有字段没写进去（`fields_incomplete`）——没填全就翻页等于把问题推到下一页；
   * - 弹窗需要人（承诺书、签名、实名、最终提交措辞）或需要人确认（`needs_fill`）；
   * - 页面有校验错误、找不到唯一的「下一步」、点了没进新步骤；
   * - **走到了有提交按钮的页面**（`reached_submit`）——最终提交永远要人连点两次授权，
   *   自动流程绝不碰它；
   * - 到达步数上限（防呆，不是安全边界）。
   */
  const autoRun = async ({ dossier = {}, maxPages = 12, onProgress = () => {} } = {}) => {
    const blocked = guardOrigin();
    if (blocked) return blocked;

    // 两条链同时填同一页，比不填还糟。连点两次「连续填报」、或者「授权后自动继续」
    // 与手动点击撞在一起时，都会出现这种交错——实测 journal 里能看到
    // 「fill 行业表述 failed」紧接着「fill 名称区划 ok」，那就是两轮在抢。
    if (state.autoRunning) {
      return { ok: false, pause: "already_running", message: "连续填报正在进行中，先让它跑完或点「重置本次运行」。" };
    }
    state.autoRunning = true;

    const pages = [];
    // 已经填过的页面的「身份」。**记录必须跨调用保留**（放在 state 里）：连续填报
    // 会因为「等你确认一个提示」而中途返回，你确认完再点一次继续——如果这时记录
    // 清零，同一页就会被重填一遍。实测正是这样：确认之后又从头填了一轮，
    // 把已经填好的名称字号冲成空。
    //
    // 判据也不能只看页面指纹：指纹会因为弹窗开关、DOM 微调而变化，它回答的是
    // 「页面动了吗」，不是「进到下一步了吗」。
    const seenPages = state.seenPages;

    try {
    for (let index = 0; index < maxPages; index += 1) {
      // 每一页开始前先看有没有弹窗挡着：它可能是上一次翻页带出来的。
      const early = page.snapshot();
      if (early.dialog) {
        const dialog = await handleDialog({ dossier });
        if (dialog.kind !== "handled" && dialog.kind !== "none") {
          return {
            ok: false,
            pause: dialog.kind,
            dialog,
            // 知情类停下来时，把原话一路带到界面上——那是人做决定的全部依据。
            dialogText: dialog.text || "",
            reason: dialog.reason || "",
            pages,
            stoppedAt: early.stage,
          };
        }
      }

      const snapshot = page.snapshot();
      // 提交页到此为止。**这一条是硬边界**：最终提交要用户在面板上连点两次授权，
      // 自动流程只负责把人送到这里，绝不替他按下去。
      if (snapshot.actions.some((action) => action.role === "submit")) {
        return {
          ok: true,
          pause: "reached_submit",
          pages,
          snapshot,
          message: "已经走到最终提交环节。请你本人逐项复核，确认无误后用「复核并提交」授权——自动流程不碰提交。",
        };
      }

      // 这一页填过了吗。**同一页绝不填第二遍**：站点在同一步里做局部更新（点掉一个
      // 提示框、异步刷新一块区域）都会让指纹变化，而那不是「进到下一步」。
      const identity = identityOf(snapshot);
      if (seenPages.has(identity)) {
        return {
          ok: false,
          pause: "no_progress",
          pages,
          snapshot,
          message:
            `点完「下一步」之后，页面还是同一步（${snapshot.stage.label}），字段也和刚才那一页一样。` +
            "已经停下来，**没有**再填一遍——重复填写会把刚填好的内容冲掉。" +
            "多半是站点弹了确认框把跳转拦住了，或者这一页还有它自己的校验没过：请看一眼页面上的提示，处理完再点「连续填报」。",
        };
      }
      seenPages.add(identity);

      onProgress({ index, stage: snapshot.stage });
      const filled = await fill({ dossier });
      if (!filled.ok) return { ...filled, pages };

      pages.push({
        stage: filled.snapshot?.stage?.label || snapshot.stage.label,
        filled: filled.results.length,
        failures: filled.failures.length,
      });

      if (!filled.readyToAdvance) {
        return {
          ok: false,
          pause: "fields_incomplete",
          pages,
          results: filled.results,
          failures: filled.failures,
          skipped: filled.skipped,
          missingRequired: filled.missingRequired,
          hints: filled.hints,
          snapshot: filled.snapshot,
        };
      }

      let moved = await advance({ dossier });
      // 「下一步」被站点暂时禁用（在做名称查重之类的异步校验）时，等它恢复再点——
      // 这不是失败，是还没轮到我们。等不回来才如实报出去。
      if (!moved.ok && moved.pause === "next_action_disabled") {
        onProgress({ index, stage: snapshot.stage, waiting: "next_action_disabled" });
        const back = await until(
          () => {
            const now = page.snapshot();
            return now.actions.some((action) => action.role === "next") ? now : null;
          },
          { timeout: 15000, interval: 700 },
        );
        if (back) moved = await advance({ dossier });
      }
      if (!moved.ok) return { ...moved, pages };

      // 翻页之后页面还在动：弹窗淡入、下一页异步渲染。这时立刻取快照，拿到的是
      // 半旧的状态——弹窗还没被识别到、字段还是上一页的，于是被**自己的防重填
      // 规则**判成「原地打转」。实测正是这样：明明已经翻到备用字号弹窗了，
      // 面板却说「页面还是同一步」。等它稳下来再进入下一轮。
      await until(
        () => {
          const now = page.snapshot();
          return now.dialog || identityOf(now) !== identityOf(snapshot) ? now : null;
        },
        { timeout: 5000, interval: 300 },
      );
    }
    return {
      ok: false,
      pause: "max_pages",
      pages,
      message: `已经连续处理了 ${maxPages} 页还没走到提交环节，先停下来让你看一眼——继续的话再点一次。`,
    };
    } finally {
      // 无论从哪条路返回（包括抛错），都要把「正在跑」放掉，
      // 否则一次异常就会让「连续填报」永远点不动。
      state.autoRunning = false;
    }
  };

  /**
   * 最终提交。三道闸：本次运行未提交过、用户在插件里显式授权、点击前指纹与
   * 复核时一致。提交后只认页面上的成功文字或办件编号，读不到就交给人目视核对，
   * **不重试**。
   */
  const submit = async ({ authorizedFingerprint = "" } = {}) => {
    const blocked = guardOrigin();
    if (blocked) return blocked;
    if (state.submitAttempted) {
      return { ok: false, pause: "already_attempted", message: "本次运行已经点过一次提交，不会再点第二次。请直接查看页面上的当前状态。" };
    }

    const before = page.snapshot();
    if (!authorizedFingerprint || authorizedFingerprint !== before.fingerprint) {
      return { ok: false, pause: "fingerprint_changed", message: "页面在你确认之后又变化了，需要重新复核再提交。" };
    }
    const targets = before.actions.filter((action) => action.role === "submit");
    if (targets.length !== 1) {
      return { ok: false, pause: "ambiguous_submit_action", labels: targets.map((item) => item.label) };
    }

    state.submitAttempted = true;
    record({ action: "submit_attempted", button: targets[0].label, fingerprint: before.fingerprint });
    await pressButton(targets[0].el);
    await delay(1500);

    // 站点常在提交后再弹一次「确认要提交吗」。这一次点击的授权已经拿到了，
    // 所以纯确认类可以点掉；承诺、授权、签署的弹窗仍然会被分级挡住。
    let guard = 0;
    while (guard < policy.MAX_AUTO_DIALOGS) {
      const dialog = await handleDialog({});
      if (dialog.kind !== "handled") {
        if (dialog.kind === "needs_user" || dialog.kind === "needs_fill") {
          return { ok: false, pause: dialog.kind, dialog, submitted: true };
        }
        break;
      }
      guard += 1;
      await delay(1200);
    }

    await delay(1500);
    const receipt = page.readReceipt();
    record({ action: "submit_result", confirmed: receipt.confirmed, referenceNo: receipt.referenceNo });
    return { ok: true, submitted: true, receipt, snapshot: page.snapshot() };
  };

  /**
   * 探查某个下拉的弹层结构。政务站点的自绘弹层长什么样是猜不出来的——组织形式
   * 这一个已经先后被误读过两次。与其继续猜，不如让人一键把真实结构贴出来。
   */
  const probe = async (fieldId, targetHint = "") => {
    const blocked = guardOrigin();
    if (blocked) return blocked;
    const el = dom.byFieldId(fieldId);
    if (!el) return { ok: false, reason: "element_gone" };
    try {
      // 带上档案里想选的值当认领线索：页面上同时开着两个关不掉的弹层时，
      // 「哪个里面有我要选的字」比「哪个是新开的」可靠得多。
      const result = await selects.describePanel(el, { targetHint: clean(targetHint) });
      record({ action: "probe_panel", field: fieldId, ok: Boolean(result.ok) });
      return result;
    } catch (error) {
      // 探查是诊断工具，它自己抛错必须说清楚抛在哪，不能吐一个 undefined 出去。
      return { ok: false, reason: "probe_exception", message: String(error?.message || error).slice(0, 300) };
    }
  };

  /**
   * 一键导出诊断。人工誊抄面板内容既费事又漏细节——尤其是弹层结构，
   * 那恰恰是最关键、最难描述的一块。这里把它们一次性打包成可粘贴的 JSON。
   *
   * 边界：**不导出密码/验证码字段的任何信息**，其余业务值会包含在内
   * （档案本来就是用户自己的），导出前界面上会明说这一点。
   */
  const diagnostics = async ({ dossier = {} } = {}) => {
    const blocked = guardOrigin();
    if (blocked) return blocked;

    const snapshot = page.snapshot();
    const { plan, skipped, missingRequired } = planner.build(snapshot.fields, dossier);

    // 逐个探查弹层控件——这是导出里最值钱的部分。
    // **skipped 里的也要探**：被清空或没映射上的下拉恰恰是最需要看结构的那些，
    // 上一版只探 plan，于是组织形式一被清空就从诊断里消失了。
    const panels = [];
    const panelTargets = [...plan, ...skipped].filter((row) => controls.isPanelControl(row.kind)).slice(0, 10);
    for (const item of panelTargets) {
      const el = dom.byFieldId(item.fieldId);
      if (!el) continue;
      // 同 probe：用档案值（分步选择器取第一级）当认领线索。
      const wanted = Array.isArray(item.cascade) && item.cascade.length ? item.cascade[0] : item.value;
      const detail = await selects.describePanel(el, { targetHint: clean(wanted || "") }).catch((error) => ({
        ok: false,
        reason: "probe_exception",
        message: String(error?.message || error).slice(0, 200),
      }));
      panels.push({ label: item.label, canonicalKey: item.canonicalKey, wanted: item.cascade || item.value, ...detail });
    }

    return {
      ok: true,
      version: AFY.VERSION,
      at: new Date().toISOString(),
      url: `${location.origin}${location.pathname}${location.hash}`.slice(0, 300),
      stage: snapshot.stage,
      fingerprint: snapshot.fingerprint,
      dialog: snapshot.dialog,
      validationErrors: snapshot.errors,
      actions: snapshot.actions.map((action) => ({ role: action.role, label: action.label })),
      // 被禁用/隐藏的按钮。没有这一项时，「下一步不见了」在诊断里无从判断是
      // 「页面变了」还是「站点把它禁用了」——而两者的下一步完全不同。
      inactiveActions: snapshot.inactiveActions || [],
      fields: snapshot.fields.map((field) => ({
        label: field.label,
        section: field.section,
        kind: field.kind,
        required: field.required,
        readonly: field.disabled,
        hint: field.hint,
        placeholder: field.placeholder,
        // 秘密字段连当前值的长度都不记。
        current: field.secret ? "<secret>" : field.value,
      })),
      plan: plan.map((item) => ({
        label: item.label,
        kind: item.kind,
        canonicalKey: item.canonicalKey,
        value: item.value,
        cascade: item.cascade,
        stepGap: item.stepGap,
        matchedBy: item.matchedBy,
      })),
      skipped: skipped.map((item) => ({ label: item.label, kind: item.kind, reason: item.reason, hint: item.hint })),
      missingRequired: missingRequired.map((item) => item.label),
      panels,
      // 档案里到底有哪些键（**只记路径，不记值**——留痕不该变成一份档案副本）。
      // 排查时最常见的岔路就是「以为档案存进去了，其实没有」：没有这一项，
      // 满屏的 no_fact 只能靠猜是映射不对还是档案本来就空。
      dossierFacts: (() => {
        const out = [];
        const walk = (node, prefix) => {
          if (out.length >= 60 || node == null) return;
          if (Array.isArray(node)) {
            if (node.length) out.push(`${prefix}[${node.length}]`);
            return;
          }
          if (typeof node === "object") {
            for (const [key, value] of Object.entries(node)) {
              walk(value, prefix ? `${prefix}.${key}` : key);
            }
            return;
          }
          if (String(node).trim()) out.push(prefix);
        };
        walk(dossier, "");
        return out;
      })(),
      // 上下文一失效，这份诊断里的「档案缺这条事实」「通道不可用」全都是**假象**，
      // 真实原因只有一个：扩展重载后页面没刷新。不写进导出的话，贴出来的 JSON
      // 会把看的人引向档案和权限，而那两处都是好的。
      extensionContext: globalThis.chrome?.runtime?.id
        ? "alive"
        : "stale（扩展已重载但本页未刷新，本文件里的 no_fact / no_runtime 均为假象，请先刷新页面重跑）",
      probe: { blockedRequests: AFY.probeState?.blocked || 0 },
      // 通道状态是排查「点了没反应」的第一现场：mode 不是 ready 时全部点击都在
      // 走合成事件，fallbacks 多说明可信通道半路断了。
      trustedInput: AFY.trusted
        ? {
            mode: AFY.trusted.state.mode,
            reason: AFY.trusted.state.reason,
            clicks: AFY.trusted.state.clicks,
            fallbacks: AFY.trusted.state.fallbacks,
          }
        : null,
      journal: state.journal.slice(-80),
    };
  };

  AFY.runner = {
    state,
    // 导出给自检：「翻过去了吗」这个判据是本模块最容易悄悄写错的一处，
    // 而它错了的后果是重复填写、冲掉已填好的值。必须能被单独验证。
    identityOf,
    observe,
    fill,
    fillOne,
    probe,
    diagnostics,
    handleDialog,
    advance,
    autoRun,
    submit,
    /**
     * 授权「知情类提示由插件代点」。两个来源：
     * - 设置页的持久开关（`persistent: true`）——配置一次长期有效，重置不清；
     * - 面板上看过原话后的一次性授权——只在本次运行内有效，重置即失效。
     */
    authorizeAcknowledge: ({ persistent = false } = {}) => {
      if (state.acknowledgeAuthorized && state.acknowledgePersistent === persistent) return;
      state.acknowledgeAuthorized = true;
      state.acknowledgePersistent = persistent;
      record({ action: "acknowledge_authorized", persistent });
    },
    /** 设置页关掉开关时调。一次性授权不受影响（它本来就只活到本次运行结束）。 */
    revokeAcknowledge: () => {
      if (!state.acknowledgePersistent) return;
      state.acknowledgeAuthorized = false;
      state.acknowledgePersistent = false;
      record({ action: "acknowledge_revoked" });
    },
    resetRun: () => {
      state.journal = [];
      state.autoDialogCount = 0;
      state.submitAttempted = false;
      // 一次性授权跟着运行走；设置页那个持久开关不受重置影响——
      // 它是用户对工具的配置，不是对某一次运行的临时决定。
      if (!state.acknowledgePersistent) state.acknowledgeAuthorized = false;
      state.seenPages.clear();
      state.autoRunning = false;
    },
  };
})();
