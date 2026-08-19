// 安全词表。这一层决定「插件绝不代劳的事」，与后端 browser_tasks 的口径保持一致
// （factory-server/backend/factory_api/modules/browser_tasks/service.py 的
// _DIALOG_HUMAN_ONLY 等），两边措辞不同步会让同一份档案在两条路径上行为不一致。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.policy) return;
  const { clean } = AFY.util;

  // 密码、验证码、动态口令：既不读也不写，连值都不进日志。
  const SECRET = new RegExp(
    "(密码|口令|短信码|短信验证码|动态码|验证码|校验码|图形码|otp|captcha|verification.?code)",
    "i",
  );
  const SECRET_AUTOCOMPLETE = new Set(["current-password", "new-password", "one-time-code"]);

  // 弹窗文案命中这些词就必须停下来交给人：一次法律意义上的意思表示、一次身份核验，
  // 或一次不可撤销的动作。插件替人点掉这类弹窗，等于替人做了它没有资格做的决定，
  // 而事后没有任何办法证明用户本人同意过。
  // 【永不自动】法律意思表示、身份核验、资金动作、销毁性操作。
  //
  // 这一类的共同点是：点下去等于**你本人在做出承诺、表明身份或处置财产**。插件替你
  // 点，等于替你做了一个它没有资格做的决定，而且事后没有任何办法证明你同意过。
  // 任何开关都不放行这一类——它不是「默认关闭的功能」，是边界。
  const HUMAN_ONLY = new RegExp(
    "(授权|委托|承诺|声明|须知|告知书|签署|签字|盖章|电子签|" +
      "实名|人脸|刷脸|扫脸|身份核验|身份验证|活体|" +
      "放弃|删除|撤销|撤回|注销|解散|清算|" +
      "支付|缴费|付款|转账|开票|" +
      "法律责任|失信|不可撤销|后果自负|承担.{0,4}责任)",
  );

  // 【知情确认】站点在**告知一个后果**，等你点头「我知道了」。
  //
  // 实测原话：「您选择申报的为有限责任公司名称，请仔细核对投资人信息，
  // 一经申报无法修改企业类型」。它和上面那一类的区别很实在：
  // 前者是「我知悉」，后者是「我承诺 / 我签署 / 我付款」。
  //
  // 默认仍然停下来把原话摆给人看——知情权是这类提示存在的全部意义。但用户看过之后
  // 可以授权「本次运行内同类提示自动确认」：否则每翻一页都要人点一次，连续填报就
  // 退化成了手动。授权只在**本次运行**内有效，重置即失效，且永远不覆盖上面那一类。
  //
  // 措辞穷举不完，所以认句式：「一经/一旦…无法/不能」「无法修改/不可更改」。
  const ACKNOWLEDGE = new RegExp(
    "(一经|一旦|一但).{0,10}(无法|無法|不能|不可|不得)|" +
      "(无法|無法|不能|不可|不得).{0,3}(修改|更改|变更|變更|恢复|還原|还原)|" +
      "请(仔细|认真|再次)?核对|請(仔細|認真|再次)?核對",
  );

  // 勾选类的法律意思表示。即使不在弹窗里，插件也不勾。
  const DECLARATION = new RegExp("(承诺|声明|保证|确认.{0,4}真实|已阅读|同意.{0,6}(协议|条款|须知))");

  // 最终提交：需要用户在插件里显式授权，且一次运行只允许一次。
  const FINAL_SUBMIT = new RegExp("^(提交|确认提交|正式提交|提交申报|申报提交|完成申报|提交申请|确认并提交)$");

  // 翻页。刻意排除「上一步」和「保存草稿」——它们不是推进。
  const NEXT = new RegExp("^(下一步|下一頁|下一页|继续|保存并下一步|下一步.{0,4})$");

  const PURE_INFO_CONFIRM = new RegExp("^(确定|确认|知道了|我知道了|好的|继续|是|关闭)$");

  // 弹窗里的**推进**按钮。与页面级的 NEXT 分开——弹窗里的措辞更花：
  // 名称申报的备用字号弹窗写的是「继续申请」，而 NEXT 只认「下一步/继续」这类。
  // 刻意不含「提交/申报完成」这类终局措辞：那些由 FINAL_SUBMIT 拦下，永远要人授权。
  const DIALOG_PROCEED = new RegExp("^(继续申请|继续办理|继续填写|继续|确认并继续|确定并继续|下一步)$");

  // 校验类弹窗（「字号包含生僻字，请修改字号」这种）。它长得和普通提示一模一样——
  // 都是一句话加一个「确定」——但含义完全相反：普通提示点掉就能继续，校验提示
  // 点掉之后页面照样不会前进。把它自动点掉，人看到的就只剩「翻页没反应」，
  // 而站点其实已经把原因说清楚了。所以它必须原样端到人面前。
  const VALIDATION = new RegExp(
    "(请修改|请重新|请先|请检查|不符合|不合法|不正确|填写有误|录入有误|不能为空|" +
      "已存在|重复|生僻字|禁用字|不可用|超出|超过.{0,4}限制|格式.{0,4}(不对|错误)|校验(未通过|失败))",
  );

  // 关闭图标不是一个决定，不该参与「哪个是确认按钮」的判断。
  const CLOSE_LABEL = new RegExp("^(close|×|✕|✖|x|关闭|取消)$", "i");

  const identityOf = (el) =>
    [
      el?.getAttribute?.("name"),
      el?.id,
      el?.getAttribute?.("placeholder"),
      el?.getAttribute?.("aria-label"),
      el?.getAttribute?.("title"),
    ]
      .filter(Boolean)
      .join(" ");

  /** 秘密字段判定要宽：宁可漏填一个普通字段，也不能碰一次口令。 */
  const isSecret = (el, label = "") => {
    if (!el) return false;
    const type = clean(el.getAttribute?.("type")).toLowerCase();
    if (type === "password") return true;
    const autocomplete = clean(el.getAttribute?.("autocomplete")).toLowerCase();
    if (SECRET_AUTOCOMPLETE.has(autocomplete)) return true;
    return SECRET.test(`${label} ${identityOf(el)}`);
  };

  AFY.policy = {
    SECRET,
    HUMAN_ONLY,
    ACKNOWLEDGE,
    DECLARATION,
    FINAL_SUBMIT,
    NEXT,
    PURE_INFO_CONFIRM,
    DIALOG_PROCEED,
    VALIDATION,
    CLOSE_LABEL,
    isSecret,
    isHumanOnly: (text) => HUMAN_ONLY.test(String(text || "")),
    // 知情类：**先排除永不自动那一类**。两者同时命中时（比如一句话里既有「承诺」
    // 又有「一经…无法修改」），一律按更严的那一类处理。
    isAcknowledge: (text) => {
      const value = String(text || "");
      return !HUMAN_ONLY.test(value) && ACKNOWLEDGE.test(value);
    },
    isValidation: (text) => VALIDATION.test(String(text || "")),
    isCloseLabel: (text) => CLOSE_LABEL.test(clean(text)),
    isDeclaration: (text) => DECLARATION.test(String(text || "")),
    isFinalSubmit: (text) => FINAL_SUBMIT.test(clean(text)),
    isNext: (text) => NEXT.test(clean(text)),
    // 弹窗里的推进按钮。**先排除最终提交**：措辞再像「继续」，只要它是终局动作，
    // 就必须走那条要人连点两次授权的路。
    isDialogProceed: (text) => {
      const value = clean(text);
      return !FINAL_SUBMIT.test(value) && DIALOG_PROCEED.test(value);
    },
    // 一页上最多自动点掉几个弹窗。政务站点翻页时通常只弹一个；连着冒出好几个
    // 更可能是判断错了，不该一路点下去。
    MAX_AUTO_DIALOGS: 3,
  };
})();
