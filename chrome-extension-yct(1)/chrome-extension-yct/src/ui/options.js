// 档案编辑与平台设置。档案只写 chrome.storage.local，不出本机。

// 示例用平台 enterprise_registration.v1 的路径写法；技能包词汇（investors[] /
// people[] / enterprise.name.industry）也能读到，见 field-map.js 的取舍说明。
const SAMPLE = {
  enterprise: {
    name: {
      administrative_division: "上海市",
      trade_name: "示例字号",
      trade_name_pinyin: "SHILIZIHAO",
      // 行业表述有**两个值**，必须写成 [查询词, 要选的那一条]：
      //   第一个 = 打进输入框的字（站点按它搜候选，也是最终显示的内容）
      //   第二个 = 下拉里要点的那一条（括号里的主营行业代码才是真正绑定的东西）
      // 只写整串会搜不到任何候选，站点判非法并清空输入框，弹「请输入正确的行业表述！」。
      industry_expression: ["信息科技", "信息科技 (主营行业:通信、信息、数码科技(12678))"],
      // 组织形式的弹层是两步向导（实测）：
      //   1. 请选择企业类型 → 内资企业 › 有限公司
      //   2. 请选择组织形式 → 有限公司
      // 两级恰好同名，这是站点的真实写法，不是笔误。
      // 分组名「内资企业」可写可不写：内资/外资下各有一个「有限公司」，
      // 不写时按约定取第一个（内资那一支）。
      organization_form: ["有限公司", "有限公司"],
      // 备用字号（非必填）。站点在名称确认弹窗里问「备用字号1/2」，本意是让你另填
      // 两个**不同的**备选名称——留空即可，但**绝不能**写成和主字号一样：
      // 那等于把同一个名字提交三遍。不填就把这两行删掉。
      alternative_1: "愿力智算",
      alternative_2: "愿力数科",
    },
    // 这一项同时被两处用到：页面上的「企业类型」字段，以及「组织形式」弹层的第一步。
    // 万一这两处的文案不一致（弹层第一级写「内资公司」而字段写「有限责任公司」），
    // 就把 organization_form 直接写成路径数组，数组优先于这里：
    //   "organization_form": ["内资公司", "有限责任公司"]
    // 不确定弹层第一级有哪些值时，在面板上点那一行的「探查弹层」。
    enterprise_type: "有限责任公司",
    registered_address: "上海市浦东新区示例路 1 号 101 室",
    registered_capital: "100",
    business_scope: "一般项目：技术服务、技术开发、技术咨询、技术交流、技术转让、技术推广。",
  },
  capital: { currency: "人民币", unit: "万元" },
  contact: { name: "张三", phone: "13800000000" },
  shareholders: [
    {
      name: "张三",
      type: "自然人股东",
      id_type: "中国居民身份证",
      id_number: "310000000000000000",
      contribution_amount: "60",
      ratio: "60",
      method: "货币",
      deadline: "2046-12-31",
    },
    {
      name: "李四",
      type: "自然人股东",
      id_type: "中国居民身份证",
      id_number: "310000000000000001",
      contribution_amount: "40",
      ratio: "40",
      method: "货币",
      deadline: "2046-12-31",
    },
  ],
  personnel: [
    { name: "张三", role: "法定代表人", id_type: "中国居民身份证", id_number: "310000000000000000", phone: "13800000000" },
    { name: "李四", role: "监事", id_type: "中国居民身份证", id_number: "310000000000000001", phone: "13800000001" },
  ],
};

/** 档案里有几条「事实」（叶子值）。数组算一条——它整体就是一个值（如分步路径）。 */
const countFacts = (node) => {
  if (node == null) return 0;
  if (Array.isArray(node)) return node.length ? 1 : 0;
  if (typeof node === "object") {
    return Object.values(node).reduce((sum, value) => sum + countFacts(value), 0);
  }
  return String(node).trim() ? 1 : 0;
};

const $ = (id) => document.getElementById(id);
const dossierBox = $("dossier");
const dossierMsg = $("dossier-msg");
const platformMsg = $("platform-msg");

const say = (node, text, level = "") => {
  node.textContent = text;
  node.className = `msg ${level}`;
};

const load = async () => {
  const stored = await chrome.storage.local.get(["dossier", "platform", "autoAcknowledge"]);
  ackBox.checked = Boolean(stored.autoAcknowledge);
  paintAck(ackBox.checked);
  dossierBox.value = JSON.stringify(stored.dossier || {}, null, 2);
  $("base-url").value = stored.platform?.baseUrl || "";
  say(
    platformMsg,
    stored.platform?.enabled
      ? `平台：${stored.platform.baseUrl}（${stored.platform.token ? "已配对" : "未配对"}）`
      : "未接入平台，当前是本地模式。",
  );
};

$("save").addEventListener("click", async () => {
  let parsed;
  try {
    parsed = JSON.parse(dossierBox.value || "{}");
  } catch (error) {
    say(dossierMsg, `JSON 解析失败：${error.message}`, "bad");
    return;
  }
  if (typeof parsed !== "object" || Array.isArray(parsed)) {
    say(dossierMsg, "档案最外层必须是一个对象。", "bad");
    return;
  }
  // 秘密值从来不该进档案。这里只警告不阻断——判断哪个字段是秘密的权力在用户手里，
  // 但插件必须把这件事说出来。
  const text = JSON.stringify(parsed);
  const risky = /(密码|口令|验证码|短信码|动态码|password|otp)/i.test(text);

  // 保存必须有确定的结果反馈。
  //
  // 这里曾经是一句裸的 await set()：扩展重载之后，**旧的设置页标签**上下文已经失效，
  // set 抛异常直接冒泡，下面那句「已保存」根本不执行——用户点了保存，界面上什么都
  // 没变，于是以为存进去了，回到业务页却发现档案还是老样子，只能反复怀疑填报逻辑。
  // 写完再回读一次数它有几条：set 没报错不等于真的存进去了，而「以为保存了其实没有」
  // 是最难查的那一类。
  try {
    await chrome.storage.local.set({ dossier: parsed });
    const back = await chrome.storage.local.get("dossier");
    const saved = countFacts(back?.dossier);
    const wanted = countFacts(parsed);
    if (saved !== wanted) {
      throw new Error(`回读只看到 ${saved} 条事实，应为 ${wanted} 条`);
    }
    say(
      dossierMsg,
      `已保存 ${saved} 条事实。` +
        (risky ? "但档案里出现了疑似密码/验证码的键名，插件不会填这类字段，建议删掉。" : "") +
        "回到业务页面点「只识别」即可核对映射结果。",
      risky ? "warn" : "",
    );
  } catch (error) {
    say(
      dossierMsg,
      `保存失败：${String(error?.message || error)}。` +
        "如果扩展刚刚重新加载过，请关掉这个设置页、从扩展图标重新打开再试一次。",
      "bad",
    );
  }
});

$("sample").addEventListener("click", () => {
  dossierBox.value = JSON.stringify(SAMPLE, null, 2);
  say(dossierMsg, "已载入示例，请改成真实信息后再保存。示例里的证件号码是占位符。", "warn");
});

$("clear-journal").addEventListener("click", async () => {
  const result = await call({ type: "AFY_CLEAR_JOURNAL" });
  say(dossierMsg, result?.ok ? "留痕已清空。" : `清空失败：${result?.error || "未知错误"}`, result?.ok ? "" : "bad");
});

// chrome.permissions.request 必须在用户手势里调用，所以这一步只能发生在选项页的
// 按钮点击中，不能放进 service worker——扩展只把 online.yct.sh.gov.cn 写死进
// host_permissions，平台地址是运行时才知道的，走 optional_host_permissions 申请。
const ensureHostPermission = async (baseUrl) => {
  const origins = [`${baseUrl.replace(/\/+$/, "")}/*`];
  if (await chrome.permissions.contains({ origins })) return true;
  return chrome.permissions.request({ origins });
};

const call = async (message) => {
  try {
    return await chrome.runtime.sendMessage(message);
  } catch (error) {
    return { ok: false, error: String(error?.message || error) };
  }
};

$("pair").addEventListener("click", async () => {
  const baseUrl = $("base-url").value.trim();
  const pairingCode = $("pairing-code").value.trim();
  if (!/^https:\/\//i.test(baseUrl)) {
    say(platformMsg, "平台地址必须以 https:// 开头。", "bad");
    return;
  }
  if (!pairingCode) {
    say(platformMsg, "请填写一次性配对码。", "bad");
    return;
  }
  if (!(await ensureHostPermission(baseUrl))) {
    say(platformMsg, `你拒绝了访问 ${baseUrl} 的权限，配对无法进行。`, "bad");
    return;
  }
  const result = await call({
    type: "AFY_PAIR",
    baseUrl,
    pairingCode,
    chromeVersion: navigator.userAgent.match(/Chrome\/([\d.]+)/)?.[1] || "",
  });
  say(platformMsg, result?.ok ? "配对成功。" : `配对失败：${result?.error || "未知错误"}`, result?.ok ? "" : "bad");
  $("pairing-code").value = "";
});

// ---------------------------------------------------------------- 知情提示
//
// 「告知后果」类提示（「一经申报无法修改企业类型」这种）由谁来点，是**用户的**决定，
// 不该由插件替他定，也不该每跑一次就问一遍——工具配置一次就该长期生效。
// 开关只覆盖这一类；承诺、签署、实名、支付、最终提交不在其内，那些没有开关。
const ackMsg = $("ack-msg");
const ackBox = $("auto-ack");

const paintAck = (on) => {
  say(
    ackMsg,
    on
      ? "已开启：这类提示会被自动确认，连续填报不会因此中断。每次代点都会留痕并记下站点原话。"
      : "未开启：每次遇到会停下来把原话摆给你看，你可以自己点，或当场授权（仅本次运行有效）。",
    on ? "" : "warn",
  );
};

ackBox.addEventListener("change", async () => {
  const on = ackBox.checked;
  try {
    await chrome.storage.local.set({ autoAcknowledge: on });
    paintAck(on);
  } catch (error) {
    ackBox.checked = !on;
    say(ackMsg, `保存失败：${String(error?.message || error)}。扩展若刚重载过，请重开本页再试。`, "bad");
  }
});

// ---------------------------------------------------------------- 可信输入
//
// `debugger` **不能**做可选权限。Chrome 把它列在「不允许出现在
// optional_permissions 里」的名单上，写进去的话 `permissions.request` 会直接报
// 「Only permissions specified in the manifest may be requested.」——本来想让它
// 不进安装时的权限清单，结果是这个开关根本点不动。它只能写在 manifest 的
// `permissions` 里，随扩展安装一次性授予。
//
// 于是这里的开关是**软开关**：只写 storage 标记，不申请也不收回权限。
// 强制力靠两道（都已实现，不依赖权限本身）：
//   ① SW 侧 withCdp 每次都查 trustedInput，off 就拒绝派发；
//   ② content 侧监听 storage 变更，off 时当场把通道置为停用。
// 「停用」还会先把所有已附着的标签松开，横幅立刻消失。
const trustedMsg = $("trusted-msg");

const paintTrusted = async () => {
  const stored = await chrome.storage.local.get("trustedInput");
  if (stored?.trustedInput === "off") {
    say(trustedMsg, "已停用。所有点击走普通合成事件（政务站的自绘下拉可能时灵时不灵）。", "warn");
  } else {
    say(
      trustedMsg,
      "已启用。业务页运行时点击优先走可信通道；页面顶部出现「正在调试此浏览器」提示条属正常现象，点掉它即当场收回。",
    );
  }
};

$("trusted-enable").addEventListener("click", async () => {
  await chrome.storage.local.set({ trustedInput: "auto" });
  await paintTrusted();
});

$("trusted-disable").addEventListener("click", async () => {
  // 先写停用标记再松开附着：反过来的话，松开与写标记之间的空隙里，
  // 业务页可能又发起一次点击把会话重新附上。
  await chrome.storage.local.set({ trustedInput: "off" });
  const released = await call({ type: "AFY_CDP_RELEASE_ALL" });
  await paintTrusted();
  if (released?.detached) {
    say(trustedMsg, `已停用，并松开了 ${released.detached} 个标签页的调试附着（横幅已消失）。`, "warn");
  }
});

void paintTrusted();

$("sync").addEventListener("click", async () => {
  const result = await call({ type: "AFY_SYNC_ALIASES" });
  say(
    platformMsg,
    result?.ok ? `已同步 ${result.count} 条字段别名，覆盖内置表。` : `同步失败：${result?.error || "未知错误"}`,
    result?.ok ? "" : "bad",
  );
});

void load();
