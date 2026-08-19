// 站点档案：把「上海一窗通长什么样」集中在一个文件里，引擎本身保持站点无关。
// 换一个政务站点，理论上只需要新增一份 profile。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.site) return;

  AFY.site = {
    id: "yct.sh.gov.cn",
    label: "上海市企业登记在线（一窗通）",

    // 业务域。插件只在这里动作；zwdt.sh.gov.cn / zwdtuser.sh.gov.cn 是登录域，
    // 一旦跳过去就交还控制权，不去读它的 DOM，更不去填它的表单。
    //
    // 同步清单（改一处必须改全部四处，漏改的表现是「静默不工作」）：
    //   ① 这里 ② src/background/service-worker.js 的 CDP_ALLOWED_ORIGINS
    //   ③ manifest.json 的 content_scripts[].matches ④ manifest.json 的 host_permissions
    businessOrigins: ["https://online.yct.sh.gov.cn"],
    identityOriginHint: /zwdt(user)?\.sh\.gov\.cn/i,

    entryUrl:
      "https://online.yct.sh.gov.cn/web-yct-master/?web-name-apply=%2Fweb-name-apply%2F%23%2Fname-declaration#/wujieViews/A1",

    // 站点的字典接口。MAIN world 嗅探器按 path 归类，用来把「下拉是空的」
    // 准确地解释成「未登录」而不是「级联前置没满足」——这两者的处理完全不同，
    // 而此前正是把前者误读成了后者。
    dictionaryApi: /\/(dicname|getDistrictDesc|getOrgForm|getIndustryDesc|dict)/i,
    // 站点用 HTTP 200 + body.code=401 表达「已拦截，请登录」。只看状态码看不出来。
    unauthorizedBodyHint: /(请登录|已拦截|未登录|登录后再试|token.*(失效|过期))/,

    // 阶段识别：按页面可见标题匹配，命中第一条即是当前阶段。
    // 仅用于展示与留痕，不用来决定填什么——填什么只看当前页真实存在的字段。
    stages: [
      { id: "name_declaration", label: "名称申报", test: /名称申报|申报承诺/ },
      { id: "name_comparison", label: "名称比对", test: /名称比对|相同相似|禁限用/ },
      { id: "registration_authority", label: "登记机关", test: /登记机关|受理点/ },
      { id: "establishment_information", label: "设立信息", test: /设立登记|企业信息|投资人|主要人员|经营范围/ },
      { id: "processing_mode", label: "办理方式", test: /办理方式|全程电子化|材料指引/ },
      { id: "integrated_services", label: "开办服务", test: /开办服务|社保|公积金|印章|发票|银行/ },
      { id: "materials_and_preview", label: "材料与预览", test: /材料|预览|上传/ },
      { id: "signature_handoff", label: "签署", test: /签署|电子签|实名|人脸/ },
      { id: "receipt_or_progress", label: "回执与进度", test: /办件|受理号|已提交|进度/ },
    ],

    // 填写顺序依赖。级联字段必须按依赖先后写，写完上游要重新枚举下游选项——
    // 政务下拉的选项是接口返回的，上游没定下来时下游要么为空、要么是上一次的残留。
    // 路径用平台 enterprise_registration.v1 的写法（见 field-map.js 头部的取舍说明）。
    fieldOrder: [
      "enterprise.name.administrative_division",
      "enterprise.name.trade_name",
      "enterprise.name.trade_name_pinyin",
      "enterprise.name.industry_expression",
      "enterprise.name.organization_form",
    ],

    // 分步选择器：一个控件，弹层里要连点好几级。
    //
    // 「组织形式」是实测出来的：它的弹层里写着「1. 请选择企业类型 / 2. 请选择组织形式」，
    // 不先选企业类型，第二级永远是空的。这段提示曾经被两次误读——一次当成级联前置
    // 查错了方向，一次被兜底选择器当成了「唯一的选项」报给人。
    //
    // 有了这份声明，档案里只写 organization_form 也能走通：路径由 steps 逐个去档案里
    // 取值拼出来；缺哪一步就明说缺哪一步，而不是丢一个「选不中」。
    // 档案里直接给数组时，数组优先，这份声明不生效。
    // 登录态下实测到的真实层级（弹层原文）：
    //   1. 请选择企业类型
    //      内资企业 › 有限公司 / 股份公司 / 非公司企业法人 / 个人独资企业 / …
    //      外资企业 › 有限公司 / 股份公司 / …
    //   2. 请选择组织形式        ← 第一步选完才有内容
    //
    // **「有限公司」在两个分组下各有一个**，所以档案最好直接写全路径数组
    //   ["内资企业", "有限公司", "有限责任公司"]
    // 数组优先于下面这份声明。下面的 steps 只是「档案里分开写」时的兜底拼法，
    // 它拼不出分组那一层，第二级会靠 chooseSteps 的分组消歧兜住。
    stepPickers: {
      "enterprise.name.organization_form": {
        steps: ["enterprise.enterprise_type", "enterprise.name.organization_form"],
        label: "组织形式",
        note: "这个弹层是两步：先选企业类型（如「有限公司」），再选组织形式（如「有限责任公司」）。",
      },
    },

    receipt: {
      success: /提交成功|申报成功|申请成功|受理成功|已成功提交|已提交申请|办理成功/,
      reference: /(?:办件|申请|受理|业务|申报)(?:编号|流水号|号码)\s*[:：]?\s*([A-Za-z0-9-]{4,})/,
    },
  };
})();
