// 入口：只有顶层 frame 装面板、跑引擎。
//
// 子 frame 里同样会被注入这套脚本（manifest 用 all_frames，是为了让 MAIN world 的
// 接口嗅探覆盖 wujie 子应用），但引擎在子 frame 里保持沉默——顶层通过同源
// contentDocument 已经能看到子应用的全部 DOM，两个实例各跑一遍只会把字段标记
// 互相覆盖掉。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.booted) return;
  AFY.booted = true;

  const isTop = (() => {
    try {
      return window.top === window;
    } catch {
      return false;
    }
  })();

  // ---- MAIN world 嗅探回传：只收 URL 路径、HTTP 状态与响应体里的 code/msg ----
  // 挂到命名空间上，导出诊断时 runner 要读它。
  const probe = (AFY.probeState = { blocked: 0, lastPath: "", lastMessage: "" });
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.marker !== AFY.MARKER) return;
    // blob: 文档的 origin 是 "null"，同源子文档是本站 origin。别的都不收。
    if (event.origin !== location.origin && event.origin !== "null") return;
    if (data.blocked) {
      probe.blocked += 1;
      probe.lastPath = String(data.path || "").slice(0, 200);
      probe.lastMessage = String(data.message || "").slice(0, 120);
    }
  });

  if (!isTop) return;

  // 每个模块自己带 `if (!AFY || AFY.x) return;` 的守卫，所以任何一个文件挂了，
  // 后面的全都静默不装载——用户看到的是「什么都没发生」，最难查的那种。
  // 这里显式点名缺了谁，直接打进控制台。
  const REQUIRED = [
    "util", "policy", "fieldMap", "site",
    "dom", "trusted", "labels", "controls", "selects", "dialogs", "page", "planner", "runner", "panel",
  ];
  const missing = REQUIRED.filter((name) => !AFY[name]);
  if (missing.length) {
    console.error(
      `[一窗通填报助手] 有模块没有装载成功：${missing.join(", ")}。` +
        "请到 chrome://extensions 看这个扩展是否报错，或重新加载扩展。",
    );
    return;
  }

  const view = { snapshot: null, plan: [], skipped: [], results: [], hints: [], probe };
  let authorizedFingerprint = "";

  /**
   * 扩展上下文还活着吗。
   *
   * 在 chrome://extensions 里重新加载扩展之后，**已经打开的页面上那份 content
   * script 仍然在跑**，但它和新的扩展进程已经断了联系：`chrome.runtime.id` 变成
   * undefined，storage/消息全部抛异常。
   *
   * 不显式检查的话，后果是一串指向错误方向的症状：读档案抛异常被 catch 成「空档案」
   * → 每个字段报 `no_fact`「档案里缺这条事实」→ 人跑去查档案，而档案好好的；
   * 可信通道连不上后台 → 悄悄退回合成事件。真正该做的只有一件事：刷新页面。
   * 开发期每次重载扩展都会踩，所以必须由面板直接说出来。
   */
  const contextAlive = () => Boolean(globalThis.chrome?.runtime?.id);

  const STALE_CONTEXT_MESSAGE =
    "扩展刚刚被重新加载过，这个页面上的脚本还是旧的（已经和扩展断开联系）。" +
    "请按 Cmd/Ctrl+R 刷新本页后再操作——在那之前读不到档案，点击也用不上可信输入。";

  /**
   * 读档案。**「读失败」和「档案本来是空的」必须分开**——混为一谈会丢用户数据。
   *
   * 实测事故：读 storage 抛异常时这里返回 `{}`，面板「点候选写回档案」那条路径
   * 拿着这个空对象 writePath 之后直接 set 回去，把用户一条条点出来的整份档案
   * 覆盖成只剩一个键。读不到就该拒绝写，而不是拿空档案当基线。
   */
  const readDossier = async () => {
    try {
      const stored = await chrome.storage.local.get("dossier");
      const dossier = stored?.dossier && typeof stored.dossier === "object" ? stored.dossier : {};
      return { ok: true, dossier };
    } catch (error) {
      return { ok: false, dossier: {}, error: String(error?.message || error) };
    }
  };

  const loadDossier = async () => (await readDossier()).dossier;

  /**
   * 「告知后果」类提示由谁来点，是用户在设置页做的**持久**决定。
   *
   * 起初做成了「每次运行在面板上授权一次」，实际用起来是每跑一次就被问一遍——
   * 而用户是这个申报件的所有人，他配置一次就该长期生效。开关只覆盖这一类；
   * 承诺、签署、实名、支付、最终提交不在其内，那些没有开关。
   */
  const syncAcknowledgeSetting = async () => {
    try {
      const stored = await chrome.storage.local.get("autoAcknowledge");
      if (stored?.autoAcknowledge) AFY.runner.authorizeAcknowledge({ persistent: true });
      else AFY.runner.revokeAcknowledge();
    } catch {
      /* 读不到就维持当前状态：宁可多问一次，也不擅自替人点 */
    }
  };

  if (globalThis.chrome?.storage?.onChanged) {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && changes.autoAcknowledge) void syncAcknowledgeSetting();
    });
  }

  // 接了平台就以平台的表单结构为准。别名的定义权在 form_schemas，扩展只做匹配。
  const loadAliases = async () => {
    try {
      const stored = await chrome.storage.local.get("aliases");
      const count = AFY.fieldMap.applyPlatformAliases(stored?.aliases || []);
      return count;
    } catch {
      return 0;
    }
  };

  // `probe`（接口嗅探状态）由这里统一注入，**不接受 patch 覆盖**——它是常驻状态，
  // 不属于某一次动作。探查弹层的结果走另一个名字 `probeResult`：
  // 两者曾经共用 `view.probe`，于是每次渲染都被嗅探状态盖掉，探查成功时 ok 变成
  // undefined、掉进失败分支，界面上永远打「探查失败：未知原因」——而诊断 JSON 里
  // 明明是 ok:true。诊断工具自己变成黑盒，是这套东西最不能接受的一种坏法。
  const paint = (patch = {}) => {
    Object.assign(view, patch, { probe });
    try {
      AFY.panel.render(view);
    } catch (error) {
      console.error("[一窗通填报助手] 面板渲染失败", error);
    }
  };

  // 直接点「按档案填写」时不会走观察路径，那条最重要的「多半是没登录」提示就丢了。
  // 从执行结果里把它补回来——这条提示的价值就在于人第一次撞上空下拉的那一刻。
  const hintsFromResults = (results) => {
    const empty = (results || []).filter((item) => item.reason === "empty" || item.reason === "no_panel");
    if (!empty.length) return [];
    return [
      `有 ${empty.length} 个下拉展开后没有任何选项。最常见的原因是尚未登录` +
        `（站点的字典接口会被拦截并返回「请登录后再试」），其次才是级联前置字段还没选。`,
    ];
  };

  const pauseMessage = (result) => {
    const map = {
      origin_not_allowed: result.message,
      validation_errors: `页面有校验错误未解决：${(result.errors || []).join("；")}`,
      no_next_action: "当前页面上没有找到「下一步」。",
      ambiguous_next_action: `页面上有多个可能的下一步（${(result.labels || []).join(" / ")}），不替你选。`,
      // 站点自己说的那句话优先——它往往直接就是下一步该做什么，
      // 比我们转述的「没有进入新步骤」有用得多。
      page_did_not_transition: result.dialogText
        ? `点了下一步但页面没有前进。站点弹出的提示原文：「${result.dialogText}」——按它说的处理完再继续。`
        : "点了下一步，但页面在限定时间内没有进入新的稳定步骤。请看页面上的校验提示。",
      needs_user: result.dialog?.reason || "弹窗需要你本人处理。",
      needs_acknowledge:
        result.dialog?.reason ||
        result.reason ||
        "站点要你先知悉一件事，读完后可自己点确定，或在上面授权由插件代点。",
      needs_fill: result.dialog?.reason || result.reason || "弹窗里的字段已按档案填好，请你核对后自己点确认。",
      already_attempted: result.message,
      fingerprint_changed: result.message,
      ambiguous_submit_action: `页面上有多个可能的提交按钮（${(result.labels || []).join(" / ")}），不替你选。`,
      // 连续填报停下来的几种情形。每一种都对应一件只有你能做的事。
      fields_incomplete: `有 ${(result.failures || []).length} 个字段没能确认写入，先处理它们再继续——没填全就翻页只会把问题推到下一页。`,
      reached_submit: result.message,
      max_pages: result.message,
      no_progress: result.message,
      already_running: result.message,
      next_action_disabled: result.message,
    };
    return map[result.pause] || `已停下：${result.pause}`;
  };

  const handle = async (rawAction) => {
    // 动作可以是字符串，也可以是 {type, fieldId} —— 「探查弹层」要带上是哪一个控件。
    const action = typeof rawAction === "string" ? rawAction : rawAction?.type;
    // 上下文一失效，后面每一步都会得出**误导性**的结论（档案读成空、通道悄悄降级），
    // 所以在动手之前就拦下来，并且把唯一该做的事说出来。
    if (!contextAlive()) {
      return paint({ plan: [], skipped: [], results: [], message: STALE_CONTEXT_MESSAGE });
    }
    const dossier = await loadDossier();
    // 探查结果和诊断只属于点它的那一次，别让它们赖在后续每一轮的界面上。
    //
    // 两者要**各自独立**地清。上一版把 diagnostics 一起豁免了，于是点「导出诊断」
    // 不会清掉旧的探查卡片——一张很早以前留下的失败卡片就这么一直挂在界面底部，
    // 反复显示「探查失败：未知原因」，看起来像每次探查都在失败。
    if (action !== "probe") view.probeResult = null;
    if (action !== "diagnostics") view.diagnostics = "";
    try {
      if (action === "probe") {
        paint({ message: "正在展开这个下拉并倒出它的真实结构…" });
        const result = await AFY.runner.probe(rawAction.fieldId, rawAction.wanted || "");
        // 失败时一定要留下能追下去的东西。上一版这里吐过一句「探查失败：未知原因」，
        // 什么线索都没有——诊断工具自己变成黑盒是最不能接受的。
        const normalized = result.ok
          ? result
          : {
              ...result,
              reason: result.reason || result.pause || "unknown",
              raw: JSON.stringify(result).slice(0, 400),
            };
        return paint({
          probeResult: { ...normalized, label: rawAction.label || "" },
          message: result.ok
            ? "已倒出弹层结构，见下方。把这段贴出来就能定位它到底是什么结构。"
            : `探查失败：${normalized.message || normalized.reason}`,
        });
      }

      if (action === "diagnostics") {
        paint({ message: "正在采集诊断信息（会逐个展开下拉读结构，需要十几秒）…" });
        const result = await AFY.runner.diagnostics({ dossier });
        if (!result.ok) return paint({ message: pauseMessage(result) });
        const text = JSON.stringify(result, null, 2);
        let copied = false;
        try {
          await navigator.clipboard.writeText(text);
          copied = true;
        } catch {
          // 页面没焦点或站点 CSP 限制时会失败，退回让人从文本框里手动复制。
          copied = false;
        }
        return paint({
          diagnostics: text,
          message: copied
            ? "诊断已复制到剪贴板，直接粘贴即可。下面也留了一份可手动复制。"
            : "诊断已生成（复制到剪贴板失败，请从下面的文本框里手动全选复制）。",
        });
      }

      // 在面板上直接点站点给的候选：写回档案，然后立刻重填。
      //
      // 这条路径是这套东西最缺的一环。之前候选就摆在界面上，却要人切到设置页
      // 把那串字（「信息科技 (主营行业:通信、信息、数码科技(12678))」）一字不差地
      // 手抄进 JSON 再回来重跑——卡了好几轮，卡的都是这个来回，不是匹配算法。
      if (action === "pick") {
        const { canonicalKey, value, mode, picked } = rawAction;
        if (!canonicalKey) return paint({ message: "这一项没有可写回的档案键。" });

        // 写回之前**重新读一次，并且要求这一次读成功**。
        //
        // 上面那个 dossier 是本轮动作开头读的，读失败时它是空对象；拿它当基线
        // writePath 再 set 回去，就把整份档案覆盖成只剩这一个键——用户一条条点出来
        // 的东西一次抹光。实测发生过：档案只剩 administrative_division 一个键。
        const fresh = await readDossier();
        if (!fresh.ok) {
          return paint({
            message:
              `读不到档案（${fresh.error}），为避免把已有档案覆盖掉，这次没有写入。` +
              "多半是扩展刚重载过，请按 Cmd/Ctrl+R 刷新本页后重试。",
          });
        }
        const book = fresh.dossier;

        if (mode === "clear") {
          AFY.util.writePath(book, canonicalKey, "");
          try {
            await chrome.storage.local.set({ dossier: book });
          } catch (error) {
            return paint({ message: `清空失败：${String(error?.message || error)}` });
          }
          paint({ message: `已清空档案里的 ${canonicalKey}。点「只识别」看站点给的合法值，再从头选。` });
          return handle("observe");
        }

        if (!value) return paint({ message: "这一项没有可写回的值。" });
        // 分步选择器：用**这一轮真正点中的那几级** + 新选的这一级重建路径，
        // 而不是往档案里已有的数组上追加。档案里原来那条路径可能本身就是错的
        // （组织形式那条就是按 enterprise_type 拼出来的、站点根本没有的写法），
        // 追加只会把错的留在前面。
        const next = mode === "append" ? [...(Array.isArray(picked) ? picked : []), value] : value;
        AFY.util.writePath(book, canonicalKey, next);
        try {
          await chrome.storage.local.set({ dossier: book });
        } catch (error) {
          return paint({ message: `写回档案失败：${String(error?.message || error)}` });
        }
        paint({ message: `已把「${Array.isArray(next) ? next.join(" / ") : next}」写进档案（${canonicalKey}），正在重填…` });
        return handle("fill");
      }

      if (action === "reset") {
        AFY.runner.resetRun();
        authorizedFingerprint = "";
        // 松开调试附着（顶部横幅随之消失），并清掉「用户点掉过横幅」的降级记号——
        // 重置就是「重来一遍」的意思表示。下一轮动作前重新探测通道。
        await AFY.trusted.release();
        const trusted = await AFY.trusted.init();
        paint({
          plan: [], skipped: [], results: [], hints: [],
          message:
            "已重置本次运行的留痕与提交闸门。" +
            (trusted.mode === "ready" ? "可信输入通道就绪。" : ""),
        });
        return;
      }

      if (action === "observe") {
        paint({ message: "正在识别当前页面并展开下拉读取真实选项…" });
        const result = await AFY.runner.observe({ dossier, enumerate: true });
        if (!result.ok) return paint({ message: pauseMessage(result) });
        authorizedFingerprint = "";
        return paint({
          snapshot: result.snapshot,
          plan: result.plan,
          skipped: result.skipped,
          missingRequired: result.missingRequired,
          hints: result.hints,
          results: [],
          dialog: result.snapshot.dialog,
          message: result.snapshot.dialog ? "当前有弹窗，下面的清单只针对弹窗内部。" : "",
        });
      }

      // 用户读过原话之后，授权「本次运行内知情类提示由插件代点」，并立刻继续跑。
      //
      // 这是一次**用户在产品里做出的**授权：他看见了站点的原话，才有这个按钮可点。
      // 授权跟着本次运行走，重置即失效；承诺、签署、实名、支付那一类不在其内——
      // 那一类没有开关。
      if (action === "acknowledge") {
        AFY.runner.authorizeAcknowledge();
        paint({ message: "已记下你的授权：本次运行内，这类「知悉后果」的提示由插件代你点确定。正在继续…" });
        return handle("auto");
      }

      // 连续填报：填一页 → 核验 → 翻页 → 再填，直到撞上任何需要人的闸门。
      // 它不放宽任何闸门，只是免去「填一页点一次、翻一页再点一次」的机械往返。
      if (action === "auto") {
        paint({ message: "连续填报中：填一页、逐条回读核验、再翻下一页…遇到需要你决定的地方会停下来。" });
        const result = await AFY.runner.autoRun({
          dossier,
          onProgress: ({ index, stage }) =>
            paint({ message: `连续填报中：第 ${index + 1} 页「${stage.label}」，正在填写并核验…` }),
        });
        authorizedFingerprint = "";
        const walked = (result.pages || [])
          .map((page, index) => `${index + 1}.${page.stage}(${page.filled}项${page.failures ? `/${page.failures}项待处理` : ""})`)
          .join("　");
        const trail = walked ? `已走过：${walked}。` : "";
        return paint({
          snapshot: result.snapshot || view.snapshot,
          plan: result.results || [],
          results: result.results || [],
          skipped: result.skipped || [],
          missingRequired: result.missingRequired,
          hints: result.hints || [],
          dialog: result.dialog?.info || result.snapshot?.dialog || null,
          message: trail + (result.message || pauseMessage(result)),
        });
      }

      if (action === "fill") {
        paint({ message: "正在按档案填写并逐条回读核验…" });
        const result = await AFY.runner.fill({ dossier });
        if (!result.ok) return paint({ message: pauseMessage(result) });
        authorizedFingerprint = "";
        return paint({
          snapshot: result.snapshot,
          plan: result.results,
          results: result.results,
          skipped: result.skipped,
          missingRequired: result.missingRequired,
          hints: [...(result.hints || []), ...hintsFromResults(result.results)],
          dialog: result.snapshot.dialog,
          message: result.readyToAdvance
            ? "全部字段回读通过，可以点「下一步」。"
            : `有 ${result.failures.length} 个字段没有确认写入，先处理它们再翻页。`,
        });
      }

      if (action === "dialog") {
        const result = await AFY.runner.handleDialog({ dossier });
        if (result.kind === "none") return paint({ dialog: null, message: "当前没有打开的弹窗。" });
        if (result.kind === "handled") {
          return paint({ dialog: null, message: `已点掉提示弹窗「${result.title}」的「${result.button}」。` });
        }
        return paint({
          dialog: result.info,
          plan: result.results || view.plan,
          results: result.results || [],
          message: result.kind === "needs_fill" ? pauseMessage({ pause: "needs_fill" }) : result.reason || "这个弹窗需要你本人处理。",
        });
      }

      if (action === "advance") {
        paint({ message: "正在翻页…" });
        const result = await AFY.runner.advance({ dossier });
        if (!result.ok) {
          return paint({ dialog: result.dialog?.info || null, message: pauseMessage(result) });
        }
        authorizedFingerprint = "";
        return paint({ snapshot: result.snapshot, plan: [], results: [], skipped: [], message: "已进入新的步骤，请重新点「只识别」。" });
      }

      if (action === "submit") {
        const snapshot = AFY.page.snapshot();
        if (authorizedFingerprint !== snapshot.fingerprint) {
          authorizedFingerprint = snapshot.fingerprint;
          const targets = snapshot.actions.filter((item) => item.role === "submit").map((item) => item.label);
          return paint({
            snapshot,
            message:
              `这是最终提交。页面上识别到的提交按钮：${targets.join(" / ") || "（没有识别到）"}。` +
              "请先自行核对整页内容；确认无误后**再点一次**「复核并提交」，插件才会点击，且本次运行只会点这一次。",
          });
        }
        paint({ message: "已授权，正在提交…" });
        const result = await AFY.runner.submit({ authorizedFingerprint });
        authorizedFingerprint = "";
        if (!result.ok) return paint({ dialog: result.dialog?.info || null, message: pauseMessage(result) });
        return paint({
          snapshot: result.snapshot,
          message: result.receipt.confirmed
            ? `页面出现成功回执${result.receipt.referenceNo ? `，办件编号 ${result.receipt.referenceNo}` : ""}。注意「已提交」不等于「已核准」，也不等于「已领照」。`
            : "已点击提交，但页面上没有读到明确的成功文字或办件编号。请你本人查看页面确认，插件不会重试。",
        });
      }
    } catch (error) {
      paint({ message: `执行时出错：${String(error?.message || error).slice(0, 300)}` });
    }
  };

  const boot = async () => {
    let startCollapsed = true;
    try {
      const stored = await chrome.storage.local.get("panelCollapsed");
      if (typeof stored?.panelCollapsed === "boolean") startCollapsed = stored.panelCollapsed;
    } catch {
      /* 读不到就用默认的收起态 */
    }

    AFY.panel.mount(handle, {
      startCollapsed,
      onCollapse: (value) => {
        try {
          void chrome.storage.local.set({ panelCollapsed: value })?.catch?.(() => {});
        } catch {
          /* 扩展上下文失效时忽略 */
        }
      },
    });

    // 页面在扩展重载之后没刷新的话，这一句必须最先说——它决定了后面所有症状
    // （档案读成空、通道降级）该怎么解读。
    if (!contextAlive()) {
      paint({ message: STALE_CONTEXT_MESSAGE });
      return;
    }

    const count = await loadAliases();
    // 用户在设置页对「告知后果类提示」做的决定，启动时就要生效——
    // 否则连续填报还是会在第一个提示上停下来问一遍。
    await syncAcknowledgeSetting();
    // 通道状态要在第一句话里就说清：它决定了「点了没反应」时先查哪里。
    const trusted = await AFY.trusted.init().catch(() => AFY.trusted.state);
    const trustedNote =
      trusted.mode === "ready"
        ? "可信输入已启用：运行时页面顶部会出现「正在调试此浏览器」提示条，这是该通道的固定标识。"
        : trusted.reason === "setting_off"
          ? "可信输入已在设置页停用（点击将用合成事件，自绘下拉可能时灵时不灵）。"
          : trusted.reason === "no_permission"
            ? "可信输入不可用（缺少调试权限，点击将用合成事件）。"
            : trusted.reason === "user_detached"
              ? "你刚才关掉了调试提示条，可信输入已收回。点面板的「重置本次运行」可重新启用。"
              : trusted.reason === "no_runtime"
                ? "扩展上下文已失效（多半是刚重载过扩展），请刷新本页。"
                : "";
    paint({
      message:
        "已就绪。登录、实名、人脸、扫码与电子签名请你本人完成，插件不碰这些环节。" +
        (count ? `（字段别名已按平台表单结构覆盖 ${count} 条）` : "") +
        (trustedNote ? ` ${trustedNote}` : ""),
    });
  };

  void boot().catch((error) => console.error("[一窗通填报助手] 启动失败", error));

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "AFY_PING") {
      sendResponse({ ok: true, url: location.href, version: AFY.VERSION });
      return undefined;
    }
    if (message?.type === "AFY_ACTION") {
      // 从扩展弹窗触发的动作，结果要看得见——先把面板展开。
      AFY.panel.expand(view);
      handle(message.action)
        .then(() => sendResponse({ ok: true }))
        .catch((error) => sendResponse({ ok: false, error: String(error?.message || error) }));
      return true;
    }
    return undefined;
  });
})();
