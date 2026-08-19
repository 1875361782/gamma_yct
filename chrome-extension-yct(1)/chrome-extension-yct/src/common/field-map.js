// 标签同义词 → 档案路径。
//
// 仓库里存在两套企业档案词汇，这里必须选一套当主、另一套当回退，**绝不能造第三套**：
//
// - 平台运行面 `enterprise_registration.v1`（factory-server/domain/af_caseengine/dossier.py）：
//   `enterprise.name.industry_expression`、`shareholders[]`、`personnel[]`、`contact.*`。
//   它是 form_schemas 和 /agent/binding-options 实际下发的那一套。
// - 技能包 field-mapping.md 的模型侧词汇：`enterprise.name.industry`、`investors[]`、
//   `people[]`、`domicile.*`、`capital.*`。它更细，是给大模型读的。
//
// 主键取平台那套（接平台时同步下来的别名能直接插进来用），技能词汇进 `alt`：
// 档案里按哪套写都读得到。接平台后平台别名**前置覆盖**内置表——别名的定义权在
// 平台的表单结构，扩展只做匹配，不做定义。
//
// 排序即优先级：一个别名是另一个别名的子串时，长的必须在前，否则「字号拼音」
// 会先被「字号」吃掉。
(() => {
  const AFY = globalThis.__AFY;
  if (!AFY || AFY.fieldMap) return;
  const { clean, normalize } = AFY.util;

  /**
   * @typedef {{path:string, alt?:string[], aliases:string[], section?:string[]}} Entry
   * section 只在字段跨分区重名时填：「联系电话」在股东分区和联络员分区下是两个人。
   */
  const BUILTIN = [
    // ---- 名称申报 ----
    { path: "enterprise.name.trade_name_pinyin", aliases: ["字号拼音", "名称拼音", "拼音"] },
    { path: "enterprise.name.administrative_division", aliases: ["名称区划", "行政区划", "企业名称行政区划"] },
    { path: "enterprise.name.trade_name", aliases: ["名称字号", "企业字号", "字号", "商号"] },
    { path: "enterprise.name.industry_expression", alt: ["enterprise.name.industry"], aliases: ["行业表述", "行业用语", "行业特点", "所属行业"] },
    { path: "enterprise.name.organization_form", aliases: ["组织形式", "企业组织形式"] },
    { path: "enterprise.full_name", aliases: ["企业名称", "公司名称"] },
    // 站点实测用的是「备**用**字号1」，不是「备选」。别名必须收全：差一个字就会
    // 掉进模糊匹配，被 trade_name 的「字号」吃掉，主字号被原样灌进备用位——
    // 同一个名字提交三遍。精确命中优先于包含命中，所以列上就赢得过「字号」。
    { path: "enterprise.name.alternative_1", aliases: ["备用字号1", "备用字号一", "备用名称1", "备选名称一", "备选字号1", "备选字号一"] },
    { path: "enterprise.name.alternative_2", aliases: ["备用字号2", "备用字号二", "备用名称2", "备选名称二", "备选字号2", "备选字号二"] },
    { path: "application.name_reservation_number", aliases: ["名称保留号", "名称登记号", "核准通知书文号"] },

    // ---- 主体、登记机关、住所 ----
    { path: "enterprise.enterprise_type", alt: ["enterprise.type"], aliases: ["企业类型", "市场主体类型", "公司类型"] },
    { path: "enterprise.domestic_or_foreign", aliases: ["内资外资", "内资/外资", "投资来源"] },
    { path: "enterprise.duration", aliases: ["营业期限", "经营期限"] },
    { path: "registration.authority", aliases: ["登记机关", "登记机构"] },
    { path: "registration.acceptance_point", aliases: ["受理点", "受理窗口", "办理点"] },
    { path: "enterprise.registered_address", alt: ["domicile.address_full"], aliases: ["注册地址", "详细地址", "住所", "经营场所"] },
    { path: "domicile.district", aliases: ["所在区", "区县"], section: ["住所", "经营场所", "地址"] },
    { path: "domicile.street_town", aliases: ["街道（乡镇）", "街道", "乡镇"], section: ["住所", "经营场所", "地址"] },
    { path: "domicile.postcode", aliases: ["邮政编码", "邮编"] },
    { path: "domicile.property_type", aliases: ["房屋用途", "场所类型", "产权性质"] },
    { path: "domicile.is_centralized_registration_address", aliases: ["是否集中登记地", "是否托管地址"] },
    { path: "domicile.property_owner_name", aliases: ["产权人", "房屋所有权人", "出租方"] },
    { path: "domicile.usage_proof_type", aliases: ["使用证明类型", "场所使用证明"] },

    // ---- 资本 ----
    { path: "enterprise.registered_capital", alt: ["capital.amount"], aliases: ["注册资本", "认缴资本", "出资总额", "认缴出资总额"] },
    { path: "capital.currency", aliases: ["币种", "货币种类"] },
    { path: "capital.unit", aliases: ["金额单位", "计量单位"] },

    // ---- 股东 / 投资人（平台叫 shareholders，技能词表叫 investors）----
    { path: "shareholders[{index}].deadline", alt: ["investors[{index}].contribution.date", "capital.subscription_deadline"], aliases: ["出资期限", "认缴期限", "缴付期限", "认缴出资日期", "出资日期"], section: ["股东", "投资人", "发起人"] },
    { path: "shareholders[{index}].contribution_amount", alt: ["investors[{index}].contribution.amount"], aliases: ["认缴出资额", "出资金额", "出资额"], section: ["股东", "投资人", "发起人"] },
    { path: "shareholders[{index}].ratio", alt: ["investors[{index}].contribution.ratio"], aliases: ["出资比例", "股权比例", "持股比例"], section: ["股东", "投资人", "发起人"] },
    { path: "shareholders[{index}].method", alt: ["investors[{index}].contribution.method"], aliases: ["出资方式"], section: ["股东", "投资人", "发起人"] },
    { path: "shareholders[{index}].type", alt: ["investors[{index}].type"], aliases: ["股东类型", "投资人类型"], section: ["股东", "投资人", "发起人"] },
    { path: "shareholders[{index}].name", alt: ["investors[{index}].name"], aliases: ["股东姓名", "投资人姓名", "发起人姓名", "股东名称", "投资人名称"], section: ["股东", "投资人", "发起人"] },
    { path: "shareholders[{index}].country_or_region", alt: ["investors[{index}].country_or_region"], aliases: ["国别地区", "国别/地区"], section: ["股东", "投资人", "发起人"] },
    { path: "shareholders[{index}].id_type", alt: ["investors[{index}].id_type"], aliases: ["股东证件类型", "投资人证件类型", "证件类型"], section: ["股东", "投资人", "发起人"] },
    { path: "shareholders[{index}].id_number", alt: ["investors[{index}].id_number"], aliases: ["股东证件号码", "投资人证件号码", "统一社会信用代码", "证件号码"], section: ["股东", "投资人", "发起人"] },

    // ---- 主要人员（平台叫 personnel，技能词表叫 people）----
    { path: "personnel[{index}].role", alt: ["people[{index}].roles"], aliases: ["人员职务", "职务"], section: ["人员", "法定代表", "董事", "监事", "经理", "财务"] },
    { path: "personnel[{index}].name", alt: ["people[{index}].name"], aliases: ["人员姓名", "任职人员姓名", "姓名"], section: ["人员", "法定代表", "董事", "监事", "经理", "财务", "受益所有人", "代理"] },
    { path: "personnel[{index}].id_type", alt: ["people[{index}].id_type"], aliases: ["人员证件类型", "证件类型"], section: ["人员", "法定代表", "董事", "监事", "经理", "财务", "受益所有人", "代理"] },
    { path: "personnel[{index}].id_number", alt: ["people[{index}].id_number"], aliases: ["人员证件号码", "证件号码"], section: ["人员", "法定代表", "董事", "监事", "经理", "财务", "受益所有人", "代理"] },
    { path: "personnel[{index}].phone", alt: ["people[{index}].mobile"], aliases: ["人员手机号", "手机号码", "移动电话"], section: ["人员", "法定代表", "董事", "监事", "经理", "财务", "受益所有人", "代理"] },
    { path: "personnel[{index}].nationality", alt: ["people[{index}].nationality"], aliases: ["国籍"] },
    { path: "personnel[{index}].birth_date", alt: ["people[{index}].birth_date"], aliases: ["出生日期"] },
    { path: "personnel[{index}].email", alt: ["people[{index}].email"], aliases: ["电子邮箱", "邮箱", "email"] },

    // ---- 联络人 ----
    { path: "contact.name", aliases: ["联络人姓名", "联系人姓名", "联络员姓名"] },
    { path: "contact.phone", aliases: ["联络人手机号", "联系人手机", "联络员手机", "联系电话", "联系方式"] },

    // ---- 经营范围与开办服务 ----
    { path: "enterprise.business_scope", aliases: ["经营范围", "经营项目"] },
    { path: "services.employment_and_social_insurance.apply", aliases: ["就业社保登记", "社保开户"] },
    { path: "services.housing_fund.apply", aliases: ["公积金登记", "住房公积金开户"] },
    { path: "services.physical_seals.apply", aliases: ["印章刻制", "实体印章"] },
    { path: "services.invoice.apply", aliases: ["发票申领", "票种核定"] },
    { path: "services.license_delivery.method", aliases: ["执照领取方式", "领取方式"] },
  ];

  let active = [];

  const prepare = (entries) =>
    entries
      .map((entry) => ({
        path: entry.path,
        alt: entry.alt || [],
        aliases: entry.aliases || [],
        normAliases: (entry.aliases || []).map(normalize).filter(Boolean),
        normSections: (entry.section || []).map(normalize),
      }))
      .filter((entry) => entry.path && entry.normAliases.length);

  /**
   * 用平台下发的别名覆盖内置表。平台条目的形状是
   * `{match: string[], path: string}`（见 dossier.py 的 aliases），前置放置即优先。
   */
  const applyPlatformAliases = (items) => {
    const platform = (Array.isArray(items) ? items : [])
      .map((item) => ({ path: clean(item?.path), aliases: (item?.match || []).map(clean).filter(Boolean) }))
      .filter((item) => item.path && item.aliases.length);
    active = prepare([...platform, ...BUILTIN]);
    return platform.length;
  };

  applyPlatformAliases([]);

  /**
   * 匹配一个字段。返回**全部**命中项——两个候选是一次歧义，要报告，不是抛硬币。
   */
  const candidates = (field) => {
    const label = normalize(field.label);
    const section = normalize(field.section);
    if (!label) return [];
    const hits = [];
    for (const entry of active) {
      if (entry.normSections.length && section) {
        if (!entry.normSections.some((item) => section.includes(item))) continue;
      }
      for (const alias of entry.normAliases) {
        if (label === alias) {
          hits.push({ path: entry.path, alt: entry.alt, matchedAlias: alias, exact: true });
          break;
        }
        if (label.includes(alias)) {
          hits.push({ path: entry.path, alt: entry.alt, matchedAlias: alias, exact: false });
          break;
        }
      }
    }
    const exact = hits.filter((hit) => hit.exact);
    const pool = exact.length ? exact : hits;
    if (!pool.length) return [];
    // 同一个 path 被多条别名命中不算歧义，去重。
    const seen = new Set();
    const unique = pool.filter((hit) => (seen.has(hit.path) ? false : seen.add(hit.path)));
    if (exact.length) return unique;
    // 包含命中里别名越长越具体；同长度并列才是真歧义。
    const longest = Math.max(...unique.map((hit) => hit.matchedAlias.length));
    return unique.filter((hit) => hit.matchedAlias.length === longest);
  };

  AFY.fieldMap = {
    builtin: BUILTIN,
    candidates,
    applyPlatformAliases,
    /** 把 `shareholders[{index}].name` 里的占位换成实际行号。 */
    resolve: (path, index = 0) => clean(path).replace("{index}", String(index)),
  };
})();
