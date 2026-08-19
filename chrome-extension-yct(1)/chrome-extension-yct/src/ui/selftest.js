// 自检。在真实浏览器里按 manifest 的顺序加载引擎，对合成 DOM 跑断言。
//
// 它回答的是「装上之后引擎这一层能不能跑」：模块有没有全部注册、控件分类会不会
// 认错 Element Plus 的那几种结构、字段映射会不会选错、弹窗分级挡不挡得住承诺书。
// 它**不**回答「在真实业务页上填得对不对」——那要在业务页上用「只识别」核对。

(() => {
  const AFY = globalThis.__AFY;
  const rows = document.getElementById("rows");
  const summary = document.getElementById("summary");
  const fixture = document.getElementById("fixture");

  const cases = [];
  const test = (name, fn) => cases.push({ name, fn });

  const eq = (actual, expected, note = "") => {
    const a = JSON.stringify(actual);
    const b = JSON.stringify(expected);
    if (a !== b) throw new Error(`${note ? note + "：" : ""}期望 ${b}，实际 ${a}`);
    return `= ${b}`;
  };

  const ok = (value, note) => {
    if (!value) throw new Error(note || "期望为真");
    return note || "通过";
  };

  // ---------------------------------------------------------------- 合成 DOM

  const FORM_HTML = `
<div id="t-form">
  <div class="el-form-item is-required">
    <label class="el-form-item__label">名称区划：</label>
    <div class="el-select"><div class="el-input"><input id="t-division" class="el-input__inner"></div></div>
  </div>
  <div class="el-form-item is-required">
    <label class="el-form-item__label">名称字号：</label>
    <div class="el-input el-popover__reference"><input id="t-tradename" class="el-input__inner"></div>
  </div>
  <div class="el-form-item">
    <label class="el-form-item__label">字号拼音</label>
    <div class="el-input"><input id="t-pinyin" class="el-input__inner"></div>
  </div>
  <div class="el-form-item is-required">
    <label class="el-form-item__label">行业表述：</label>
    <div class="el-autocomplete" role="combobox">
      <div class="el-input"><input id="t-industry" role="textbox" class="el-input__inner"></div>
    </div>
  </div>
  <div class="el-form-item is-required">
    <label class="el-form-item__label">组织形式：</label>
    <div class="el-input el-popover__reference"><input id="t-orgform" readonly class="el-input__inner"></div>
  </div>
  <div class="el-form-item">
    <label class="el-form-item__label">登录密码：</label>
    <div class="el-input"><input id="t-pwd" type="password" class="el-input__inner"></div>
  </div>
  <div class="el-form-item">
    <label class="el-checkbox">
      <span class="el-checkbox__input">
        <input type="checkbox" id="t-promise" class="el-checkbox__original"
               style="opacity:0;position:absolute;width:0;height:0;margin:0">
      </span>
      <span class="el-checkbox__label">本人承诺所填内容真实有效</span>
    </label>
  </div>
  <div class="el-form-item">
    <label class="el-form-item__label">随便什么字段</label>
    <div class="el-input"><input id="t-unknown" class="el-input__inner"></div>
  </div>
</div>

<div class="el-overlay" style="z-index:2001">
  <div class="el-overlay-dialog">
    <div class="el-dialog" id="t-dialog-human">
      <span class="el-dialog__title">温馨提示</span>
      <div class="el-dialog__body">请确认已阅读《告知承诺书》并对所填内容的真实性作出承诺。</div>
      <div class="el-dialog__footer"><button class="el-button el-button--primary">确定</button></div>
    </div>
  </div>
</div>

<div class="el-dialog" id="t-dialog-info" style="z-index:2002">
  <span class="el-dialog__title">提示</span>
  <div class="el-dialog__body">本步骤信息已暂存。</div>
  <div class="el-dialog__footer">
    <button class="el-button">取消</button>
    <button class="el-button el-button--primary">确定</button>
  </div>
</div>

<!-- 校验类弹窗：长得和普通提示一模一样，含义完全相反。关闭图标带 aria-label="Close"，
     不过滤掉它，按钮清单里就会冒出一个「Close」。 -->
<div class="el-message-box" id="t-dialog-validation" style="z-index:2003">
  <div class="el-message-box__header">
    <button class="el-message-box__headerbtn" aria-label="Close">×</button>
  </div>
  <div class="el-message-box__content">字号包含生僻字，请修改字号</div>
  <div class="el-message-box__btns"><button class="el-button el-button--primary">确定</button></div>
</div>

<!-- 三个合成下拉，分别复现三种事件绑定位置，外加一个「开了但没数据」的。 -->
<div class="el-form-item">
  <label class="el-form-item__label">下拉A</label>
  <div class="el-select" id="t-selA"><div class="el-input"><input id="t-selA-input" class="el-input__inner" readonly></div></div>
</div>
<div class="el-select-dropdown" id="t-panelA" style="display:none">
  <ul><li class="el-select-dropdown__item">上海市</li><li class="el-select-dropdown__item">北京市</li></ul>
</div>

<div class="el-form-item">
  <label class="el-form-item__label">下拉B</label>
  <div class="el-select" id="t-selB"><div class="el-input"><input id="t-selB-input" class="el-input__inner" readonly></div></div>
</div>
<div class="el-select-dropdown" id="t-panelB" style="display:none">
  <ul><li class="el-select-dropdown__item">有限责任公司</li><li class="el-select-dropdown__item">股份有限公司</li></ul>
</div>

<div class="el-form-item">
  <label class="el-form-item__label">下拉C</label>
  <div class="el-select" id="t-selC"><div class="el-input"><input id="t-selC-input" class="el-input__inner" readonly></div></div>
</div>
<div class="el-select-dropdown" id="t-panelC" style="display:none">
  <ul><li class="el-select-dropdown__item">甲</li><li class="el-select-dropdown__item">乙</li></ul>
</div>

<div class="el-form-item">
  <label class="el-form-item__label">下拉D</label>
  <div class="el-select" id="t-selD"><div class="el-input"><input id="t-selD-input" class="el-input__inner" readonly></div></div>
</div>
<div class="el-select-dropdown" id="t-panelD" style="display:none">
  <div class="el-select-dropdown__empty">无数据</div>
</div>

<!-- E：分步向导。就是「组织形式」那种 —— 先选企业类型，第二级才出来。
     用的是自绘 li，走兜底选项识别。 -->
<div class="el-form-item">
  <label class="el-form-item__label">下拉E</label>
  <div class="el-input el-popover__reference" id="t-selE"><input id="t-selE-input" class="el-input__inner" readonly></div>
</div>
<div class="el-popover" id="t-panelE" style="display:none">
  <ul id="t-panelE-l1">
    <li class="item">1. 请选择企业类型</li>
    <li class="item">内资公司</li>
    <li class="item">个人独资企业</li>
  </ul>
  <ul id="t-panelE-l2"></ul>
</div>

<!-- F：分步向导，但第一步还没做——弹层里只剩静态提示。
     这正是线上「站点实际给的选项：2. 请选择组织形式」那一幕。 -->
<div class="el-form-item">
  <label class="el-form-item__label">下拉F</label>
  <div class="el-input el-popover__reference" id="t-selF"><input id="t-selF-input" class="el-input__inner" readonly></div>
</div>
<div class="el-popover" id="t-panelF" style="display:none">
  <ul><li class="item">1. 请选择企业类型</li><li class="item">2. 请选择组织形式</li></ul>
</div>

<!-- P：弹层慢半拍（点开 200ms 后才出现，模拟等接口）。它的候选里**没有**「上海市」，
     用来验证这段空窗期里不会把别的字段留下的弹层按文案认领走。 -->
<div class="el-form-item">
  <label class="el-form-item__label">下拉P</label>
  <div class="el-select" id="t-selP"><div class="el-input"><input id="t-selP-input" class="el-input__inner" readonly></div></div>
</div>
<div class="el-select-dropdown" id="t-panelP" style="display:none">
  <ul><li class="el-select-dropdown__item">杭州市</li><li class="el-select-dropdown__item">南京市</li></ul>
</div>

<!-- G：第一级还带分组，而且分组标题只是一行字、点不动。
     用户描述的真实结构就是这样：「内资企业」里的「有限公司」，然后才是组织形式。 -->
<div class="el-form-item">
  <label class="el-form-item__label">下拉G</label>
  <div class="el-input el-popover__reference" id="t-selG"><input id="t-selG-input" class="el-input__inner" readonly></div>
</div>
<div class="el-popover" id="t-panelG" style="display:none">
  <div id="t-panelG-l1">
    <div class="group-title">内资企业</div>
    <ul><li class="item">有限公司</li><li class="item">股份公司</li></ul>
  </div>
  <ul id="t-panelG-l2"></ul>
</div>

<!-- R：**照抄 2026-08-06 登录态实测**的组织形式弹层。候选不是 li，是 Element Plus
     的 radio（诊断里那 17 条 kind=radio / hint=el-radio__original 就是它们）。
     两级同名、内资外资各有一个「有限公司」，全都照原样复现。 -->
<div class="el-form-item">
  <label class="el-form-item__label">下拉R</label>
  <div class="el-input el-popover__reference" id="t-selR"><input id="t-selR-input" class="el-input__inner" readonly></div>
</div>
<div class="el-popover" id="t-panelR" style="display:none">
  <ul>
    <li>
      <p>1. 请选择企业类型</p>
      <div class="group"><h2>内资企业</h2>
        <div role="radiogroup" class="el-radio-group">
          <label class="el-radio" role="radio" aria-checked="false"><span class="el-radio__input"><span class="el-radio__inner"></span>
            <input type="radio" name="t-r1" aria-hidden="true" tabindex="-1" class="el-radio__original" style="opacity:0;position:absolute;width:0;height:0;margin:0"></span>
            <span class="el-radio__label">有限公司</span></label>
          <label class="el-radio" role="radio" aria-checked="false"><span class="el-radio__input"><span class="el-radio__inner"></span>
            <input type="radio" name="t-r1" aria-hidden="true" tabindex="-1" class="el-radio__original" style="opacity:0;position:absolute;width:0;height:0;margin:0"></span>
            <span class="el-radio__label">股份公司</span></label>
          <label class="el-radio" role="radio" aria-checked="false"><span class="el-radio__input"><span class="el-radio__inner"></span>
            <input type="radio" name="t-r1" aria-hidden="true" tabindex="-1" class="el-radio__original" style="opacity:0;position:absolute;width:0;height:0;margin:0"></span>
            <span class="el-radio__label">个人独资企业</span></label>
        </div>
      </div>
      <div class="group"><h2>外资企业</h2>
        <div role="radiogroup" class="el-radio-group">
          <label class="el-radio" role="radio" aria-checked="false"><span class="el-radio__input"><span class="el-radio__inner"></span>
            <input type="radio" name="t-r1" aria-hidden="true" tabindex="-1" class="el-radio__original" style="opacity:0;position:absolute;width:0;height:0;margin:0"></span>
            <span class="el-radio__label">有限公司</span></label>
          <label class="el-radio" role="radio" aria-checked="false"><span class="el-radio__input"><span class="el-radio__inner"></span>
            <input type="radio" name="t-r1" aria-hidden="true" tabindex="-1" class="el-radio__original" style="opacity:0;position:absolute;width:0;height:0;margin:0"></span>
            <span class="el-radio__label">股份公司</span></label>
        </div>
      </div>
    </li>
    <li>
      <p>2. 请选择组织形式</p>
      <div id="t-panelR-l2"></div>
    </li>
  </ul>
</div>

<!-- 自动补全三种：候选里有 / 候选里没有 / 压根没候选。
     「行业表述」是第一种，光写不选会造成假阳性。 -->
<div id="t-auto">
  <div class="el-form-item">
    <label class="el-form-item__label">行业A</label>
    <div class="el-autocomplete" role="combobox">
      <div class="el-input"><input id="t-autoA" role="textbox" class="el-input__inner"></div>
    </div>
  </div>
  <div class="el-form-item">
    <label class="el-form-item__label">行业B</label>
    <div class="el-autocomplete" role="combobox">
      <div class="el-input"><input id="t-autoB" role="textbox" class="el-input__inner"></div>
    </div>
  </div>
  <div class="el-form-item">
    <label class="el-form-item__label">行业C</label>
    <div class="el-autocomplete" role="combobox">
      <div class="el-input"><input id="t-autoC" role="textbox" class="el-input__inner"></div>
    </div>
  </div>
</div>
<div class="el-autocomplete-suggestion" id="t-panelAutoA" style="display:none">
  <ul><li class="item">信息科技</li><li class="item">信息科技服务</li></ul>
</div>
<div class="el-autocomplete-suggestion" id="t-panelAutoB" style="display:none">
  <ul><li class="item">批发零售</li><li class="item">餐饮服务</li></ul>
</div>

<!-- K：照抄一窗通「组织形式」弹层的真实形态。
     关键三点：① 选项是 div，不是 li/.item，选择器兜底一个都捞不到；
     ② 「有限公司」在内资企业和外资企业下各有一个；
     ③ 第二段在第一步选完之前只有一行提示。 -->
<div class="el-form-item">
  <label class="el-form-item__label">下拉K</label>
  <div class="el-input el-popover__reference" id="t-selK"><input id="t-selK-input" class="el-input__inner" readonly></div>
</div>
<div class="el-popover" id="t-panelK" style="display:none">
  <div class="tip">1. 请选择企业类型</div>
  <div class="col" id="t-panelK-inland">
    <div class="cat">内资企业</div>
    <div class="opt">有限公司</div>
    <div class="opt">股份公司</div>
    <div class="opt">个人独资企业</div>
  </div>
  <div class="col">
    <div class="cat">外资企业</div>
    <div class="opt">有限公司</div>
    <div class="opt">股份公司</div>
  </div>
  <div class="tip">2. 请选择组织形式</div>
  <div class="col" id="t-panelK-forms"></div>
</div>

<!-- L：候选带「(主营行业:…)」后缀，和线上一模一样。
     档案写「信息科技」会同时命中前两条 —— 必须判歧义，不能抛硬币。 -->
<div class="el-form-item">
  <label class="el-form-item__label">行业L</label>
  <div class="el-autocomplete" role="combobox">
    <div class="el-input"><input id="t-autoL" role="textbox" class="el-input__inner"></div>
  </div>
</div>
<div class="el-autocomplete-suggestion" id="t-panelAutoL" style="display:none">
  <ul>
    <li class="item">信息科技 (主营行业:互联网信息服务(07532))</li>
    <li class="item">信息科技 (主营行业:通信、信息、数码科技(12678))</li>
    <li class="item">信息科技开发 (主营行业:通信、信息、数码科技(12678))</li>
  </ul>
</div>

<!-- M：名称区划。站点写「上海」，档案写「上海市」——唯一前缀命中，
     这是正常的写法差异，不该被判成「站点改写了值」。 -->
<div id="t-misc">
  <div class="el-form-item">
    <label class="el-form-item__label">名称区划M</label>
    <div class="el-select" id="t-selM"><div class="el-input"><input id="t-selM-input" class="el-input__inner" readonly></div></div>
  </div>
</div>
<div class="el-select-dropdown" id="t-panelM" style="display:none">
  <ul><li class="el-select-dropdown__item">上海</li><li class="el-select-dropdown__item">北京</li></ul>
</div>

<!-- N：复现诊断实锤（blocked_by_stale_panel）的那一幕——两个**关不掉**的弹层
     同时开着（本站的弹层不吃 Escape 也不吃外部点击），目标选项只在其中一个里。
     刻意不给任何开合监听：closePanels 对它们无效，正如线上。 -->
<div class="el-form-item">
  <label class="el-form-item__label">下拉N</label>
  <div class="el-input el-popover__reference" id="t-selN"><input id="t-selN-input" class="el-input__inner" readonly></div>
</div>
<div class="el-popover" id="t-panelN" style="display:none">
  <ul><li class="item">丙选项</li><li class="item">丁选项</li></ul>
</div>
<div class="el-select-dropdown" id="t-panelN2" style="display:none">
  <ul><li class="el-select-dropdown__item">松江</li><li class="el-select-dropdown__item">嘉定</li></ul>
</div>`;

  /** 给合成下拉挂上三种不同位置的监听，复现 Element 各版本的绑定差异。 */
  const wireSyntheticSelects = () => {
    const show = (id) => () => {
      document.getElementById(id).style.display = "block";
    };
    // A：监听在**内层 input** 的 mousedown（只发 click 的实现打不开它）。
    document.getElementById("t-selA-input").addEventListener("mousedown", show("t-panelA"));
    // B：监听在**根 .el-select** 的 click——从内层发出的事件靠冒泡也能到达。
    document.getElementById("t-selB").addEventListener("click", show("t-panelB"));
    // C：只认**直接落在 wrapper 上**的点击。冒泡上来的不算，必须靠 wrapper 直派策略。
    const wrapperC = document.getElementById("t-selC");
    wrapperC.addEventListener("click", (event) => {
      if (event.target === wrapperC) show("t-panelC")();
    });
    // D：能打开，但站点没给数据——就是「名称区划」那一页的样子。
    document.getElementById("t-selD-input").addEventListener("mousedown", show("t-panelD"));
    // E：分步向导。选中第一级之后，第二级才被渲染出来（模拟接口回来）。
    const l2 = document.getElementById("t-panelE-l2");
    document.getElementById("t-selE-input").addEventListener("mousedown", () => {
      l2.innerHTML = "";
      show("t-panelE")();
    });
    document.getElementById("t-panelE-l1").addEventListener("mousedown", (event) => {
      const label = event.target.textContent.trim();
      if (label !== "内资公司") return;
      setTimeout(() => {
        l2.innerHTML = '<li class="item">有限责任公司</li><li class="item">股份有限公司</li>';
      }, 150);
    });
    // F：分步向导，第一步还没做，弹层里只剩静态提示。
    document.getElementById("t-selF-input").addEventListener("mousedown", show("t-panelF"));
    // R：组织形式的真实形态（radio 候选 + 两级同名）。第一级点中后，
    // 第二级的 radio 才被渲染出来（模拟接口回来）。
    const r2 = document.getElementById("t-panelR-l2");
    document.getElementById("t-selR-input").addEventListener("mousedown", () => {
      r2.innerHTML = "";
      show("t-panelR")();
    });
    document.getElementById("t-panelR").addEventListener("mousedown", (event) => {
      const label = event.target.closest(".el-radio");
      if (!label) return;
      // 模拟 Element Plus 组件自己的行为：它在包装上监听点击、更新数据模型、勾上
      // 隐藏的 input。**不能指望合成事件的默认行为**——dispatchEvent 派发的 click
      // 不会触发 label 关联 input 的激活（isTrusted=false），只有真实点击或
      // 元素的 .click() 方法才会。组件靠 addEventListener 工作，所以这条路是通的。
      const input = label.querySelector("input");
      if (input) {
        // 照抄线上：选中同时写在三处——input.checked、label 与 span 的 is-checked
        // class、label 的 aria-checked。引擎的 isChosen 认这三者中的任意一个，
        // fixture 就必须把三者都维护起来，否则测不到真实行为。
        const scope = label.closest(".el-radio-group") || label.parentElement;
        scope?.querySelectorAll(".el-radio").forEach((item) => {
          item.classList.remove("is-checked");
          item.setAttribute("aria-checked", "false");
          item.querySelector(".el-radio__input")?.classList.remove("is-checked");
          const box = item.querySelector("input");
          if (box) box.checked = false;
        });
        input.checked = true;
        label.classList.add("is-checked");
        label.setAttribute("aria-checked", "true");
        label.querySelector(".el-radio__input")?.classList.add("is-checked");
      }
      if (r2.contains(label)) return;
      if (label.querySelector(".el-radio__label").textContent.trim() !== "有限公司") return;
      const picked = label.querySelector(".el-radio__label").textContent.trim();
      setTimeout(() => {
        // **照抄线上那一幕**：Element Plus 点完第一级会重渲染整组 radio，
        // 12 个 label 全换成新的 DOM 节点。差集法（「哪些候选是新出现的」）会因此
        // 把上一级也算成新的，同名项一撞就又点回上一级——第二级从头到尾没被碰过，
        // 而引擎报成功。不复现这一步，用例就测不到真正的病因。
        const l1 = document.getElementById("t-panelR").querySelector("li");
        // eslint-disable-next-line no-self-assign — 就是要把全部节点换掉
        l1.innerHTML = l1.innerHTML;
        // 重渲染会丢掉 checked，按组件的行为把第一级的选中态补回去。
        [...l1.querySelectorAll(".el-radio")].forEach((item) => {
          const text = item.querySelector(".el-radio__label").textContent.trim();
          const box = item.querySelector("input");
          if (text === picked && !item.closest(".group")?.textContent.includes("外资")) {
            box.checked = true;
            item.classList.add("is-checked");
          }
        });
        r2.innerHTML = `
          <div role="radiogroup" class="el-radio-group">
            <label class="el-radio" role="radio" aria-checked="false"><span class="el-radio__input"><span class="el-radio__inner"></span>
              <input type="radio" name="t-r2" aria-hidden="true" tabindex="-1" class="el-radio__original" style="opacity:0;position:absolute;width:0;height:0;margin:0"></span>
              <span class="el-radio__label">有限责任公司</span></label>
            <label class="el-radio" role="radio" aria-checked="false"><span class="el-radio__input"><span class="el-radio__inner"></span>
              <input type="radio" name="t-r2" aria-hidden="true" tabindex="-1" class="el-radio__original" style="opacity:0;position:absolute;width:0;height:0;margin:0"></span>
              <span class="el-radio__label">有限公司</span></label>
          </div>`;
      }, 150);
    });
    // P：**弹层慢半拍**（200ms 后才出现）。政务下拉的常态——选项要等接口回来。
    // 它专门用来撞「按文案认领」那道守卫：在这 200ms 的空窗里，如果认领不区分
    // 新旧弹层，就会把别的字段留下的那个认领走。
    document.getElementById("t-selP-input").addEventListener("mousedown", () => {
      setTimeout(show("t-panelP"), 200);
    });
    wireAutocomplete("t-autoA", "t-panelAutoA");
    wireAutocomplete("t-autoB", "t-panelAutoB");
    wireAutocomplete("t-autoC", null);
    // L：照抄线上「行业表述」的真实行为。三条都要对：
    //  ① 只按**查询词**搜；把下拉里那条带编码后缀的长文案打进去，一个候选都搜不到；
    //  ② 选中之后输入框里留下的是查询词那一侧（「信息科技」），不是长文案；
    //  ③ 失焦时框里不是合法行业词就**清空** —— 线上「请输入正确的行业表述！」那一幕。
    (() => {
      const input = document.getElementById("t-autoL");
      const panel = document.getElementById("t-panelAutoL");
      const options = [...panel.querySelectorAll("li")];
      const queryOf = (label) => label.split(" (")[0];
      const hitsFor = (query) => (query ? options.filter((li) => queryOf(li.textContent.trim()) === query) : []);
      input.addEventListener("input", () => {
        setTimeout(() => {
          const hits = hitsFor(input.value.trim());
          for (const li of options) li.style.display = hits.includes(li) ? "" : "none";
          panel.style.display = hits.length ? "block" : "none";
        }, 150);
      });
      input.addEventListener("blur", () => {
        panel.style.display = "none";
        if (!hitsFor(input.value.trim()).length) input.value = "";
      });
      panel.addEventListener("mousedown", (event) => {
        const label = event.target.textContent.trim();
        if (!label) return;
        input.value = queryOf(label);
        panel.style.display = "none";
      });
    })();
    // M：选中后把值写回输入框，回读才有意义。
    const panelM = document.getElementById("t-panelM");
    document.getElementById("t-selM-input").addEventListener("mousedown", () => {
      panelM.style.display = "block";
    });
    panelM.addEventListener("mousedown", (event) => {
      const label = event.target.textContent.trim();
      if (!label) return;
      document.getElementById("t-selM-input").value = label;
      panelM.style.display = "none";
    });
    // K：分组标题点不动；选中企业类型之后第二段才渲染出来。
    const forms = document.getElementById("t-panelK-forms");
    document.getElementById("t-selK-input").addEventListener("mousedown", () => {
      forms.innerHTML = "";
      document.getElementById("t-panelK").style.display = "block";
    });
    document.getElementById("t-panelK").addEventListener("mousedown", (event) => {
      const opt = event.target.closest?.(".opt");
      if (!opt) return;
      if (forms.contains(opt)) {
        // 选中组织形式（末级）：真实 popover 选完就收起。
        // 上一版这里对任意 .opt 都重渲染整列，末级点完后同级兄弟会变成全新元素，
        // 于是被误判成「又长出一级」——那是 fixture 不真实，不是产品该迁就的。
        document.getElementById("t-panelK").style.display = "none";
        return;
      }
      setTimeout(() => {
        // 第二级里**故意**放一个和第一级同名的「有限公司」——线上就是这样，
        // 按文案去重会把它漏掉，面板上就只剩一个候选。
        forms.innerHTML = '<div class="opt">有限责任公司</div><div class="opt">有限公司</div>';
      }, 150);
    });
    // G：第一级带分组，分组标题点不动。
    const g2 = document.getElementById("t-panelG-l2");
    document.getElementById("t-selG-input").addEventListener("mousedown", () => {
      g2.innerHTML = "";
      show("t-panelG")();
    });
    document.getElementById("t-panelG-l1").addEventListener("mousedown", (event) => {
      if (event.target.textContent.trim() !== "有限公司") return;
      setTimeout(() => {
        g2.innerHTML = '<li class="item">有限责任公司</li><li class="item">一人有限公司</li>';
      }, 150);
    });
  };

  /**
   * 自动补全的候选是**打字触发**的，而且真实组件会在 blur 时把候选收掉。
   * 这里如实模拟这两点：input 事件后 150ms 出候选，blur 立刻收掉。
   * 收掉这一条很重要——writeText 默认会收尾 blur，不模拟就测不出那个坑。
   */
  const wireAutocomplete = (inputId, panelId) => {
    const input = document.getElementById(inputId);
    const panel = panelId ? document.getElementById(panelId) : null;
    input.addEventListener("input", () => {
      if (!panel) return;
      setTimeout(() => {
        panel.style.display = "block";
      }, 150);
    });
    input.addEventListener("blur", () => {
      if (panel) panel.style.display = "none";
    });
    // 真实组件是在**选中候选**时才把值写进 input 的。照做，端到端用例才算数。
    panel?.addEventListener("mousedown", (event) => {
      const label = event.target.textContent.trim();
      if (!label) return;
      input.value = label;
      input.dispatchEvent(new InputEvent("input", { bubbles: true }));
      panel.style.display = "none";
    });
  };

  const hideSyntheticPanels = () => {
    for (const id of [
      "t-panelA", "t-panelB", "t-panelC", "t-panelD", "t-panelE", "t-panelF", "t-panelG", "t-panelP", "t-panelR",
      "t-panelAutoA", "t-panelAutoB", "t-panelAutoL", "t-panelK", "t-panelM", "t-panelN", "t-panelN2",
    ]) {
      document.getElementById(id).style.display = "none";
    }
    document.getElementById("t-panelE-l2").innerHTML = "";
    document.getElementById("t-panelG-l2").innerHTML = "";
    document.getElementById("t-panelK-forms").innerHTML = "";
  };

  const DOSSIER = {
    enterprise: {
      name: {
        administrative_division: "上海市",
        trade_name: "示例字号",
        trade_name_pinyin: "SHILIZIHAO",
        industry_expression: "信息科技",
        organization_form: "有限公司",
      },
    },
    shareholders: [{ name: "张三" }],
  };

  // ---------------------------------------------------------------- 用例

  test("模块全部注册", () => {
    const required = [
      "util", "policy", "fieldMap", "site",
      "dom", "labels", "controls", "selects", "dialogs", "page", "planner", "runner", "panel",
    ];
    const missing = required.filter((name) => !AFY?.[name]);
    return eq(missing, [], "缺失模块");
  });

  test("chooseOne · 精确优先", () => eq(AFY.util.chooseOne("有限公司", ["有限公司", "股份有限公司"]).status, "exact"));

  test("chooseOne · 上海 → 上海市（唯一前缀）", () => {
    const result = AFY.util.chooseOne("上海", ["北京市", "上海市", "天津市"]);
    eq(result.status, "prefix");
    return eq(result.value, "上海市");
  });

  test("chooseOne · 多个候选判歧义而非抛硬币", () =>
    eq(AFY.util.chooseOne("上海", ["上海市", "上海市郊县"]).status, "ambiguous"));

  test("chooseOne · 字面完全相同的重复项取第一个", () => {
    // 内资企业和外资企业下各有一个「有限公司」。
    const result = AFY.util.chooseOne("有限公司", ["有限公司", "股份公司", "有限公司"]);
    eq(result.status, "duplicate");
    eq(result.duplicates, 2);
    return eq(result.value, "有限公司");
  });

  test("chooseOne · 文案不同的多重命中仍要判歧义", () =>
    // 「信息科技 (主营行业:A)」和「…(主营行业:B)」不是重复项，是两个不同的行业代码。
    eq(
      AFY.util.chooseOne("信息科技", [
        "信息科技 (主营行业:互联网信息服务(07532))",
        "信息科技 (主营行业:通信、信息、数码科技(12678))",
      ]).status,
      "ambiguous",
    ));

  test("面板自身不能被当成页面的一部分", () => {
    // 面板挂在 documentElement 的 shadow root 上，而 roots() 会穿透 shadow root。
    // 不排除的话，面板上那个「下一步」会和站点的「下一步」一起被认成两个 next 动作，
    // advance() 从此永远拒绝翻页。
    const host = document.createElement("div");
    host.id = AFY.dom.PANEL_HOST_ID;
    const shadow = host.attachShadow({ mode: "open" });
    const button = document.createElement("button");
    button.textContent = "下一步";
    shadow.append(button);
    document.documentElement.append(host);
    try {
      const found = AFY.dom.queryAll("button").filter((node) => node.textContent === "下一步");
      return eq(found.length, 0, "面板里的按钮不该被扫到");
    } finally {
      host.remove();
    }
  });

  test("chooseOne · 匹配不上就是匹配不上", () =>
    eq(AFY.util.chooseOne("广州", ["北京市", "上海市"]).status, "none"));

  test("readPath · 带下标的路径", () => eq(AFY.util.readPath(DOSSIER, "shareholders[0].name"), "张三"));

  test("字段映射 · 名称字号不会被字号拼音吃掉", () => {
    const hits = AFY.fieldMap.candidates({ label: "名称字号", section: "" });
    eq(hits.length, 1, "候选数");
    return eq(hits[0].path, "enterprise.name.trade_name");
  });

  test("字段映射 · 字号拼音不会被字号吃掉", () => {
    const hits = AFY.fieldMap.candidates({ label: "字号拼音", section: "" });
    eq(hits.length, 1, "候选数");
    return eq(hits[0].path, "enterprise.name.trade_name_pinyin");
  });

  test("字段映射 · 行业表述用平台词汇", () =>
    eq(AFY.fieldMap.candidates({ label: "行业表述", section: "" })[0].path, "enterprise.name.industry_expression"));

  test("安全词表 · 承诺/签署类必须交给人", () =>
    eq(
      ["请确认已阅读《告知承诺书》", "请完成电子签名", "本步骤信息已暂存"].map((t) => AFY.policy.isHumanOnly(t)),
      [true, true, false],
    ));

  test("安全词表 · 最终提交与下一步分得开", () =>
    eq(
      [AFY.policy.isFinalSubmit("提交"), AFY.policy.isFinalSubmit("下一步"), AFY.policy.isNext("下一步"), AFY.policy.isNext("上一步")],
      [true, false, true, false],
    ));

  test("控件分类 · el-select 即使不只读也是下拉", () =>
    eq(AFY.controls.kindOf(document.getElementById("t-division")), "custom-select"));

  test("控件分类 · el-autocomplete 是自由文本不是下拉", () =>
    eq(AFY.controls.kindOf(document.getElementById("t-industry")), "autocomplete"));

  test("控件分类 · 只读的 popover 触发器是选择器", () =>
    eq(AFY.controls.kindOf(document.getElementById("t-orgform")), "popover-picker"));

  test("控件分类 · 同样挂 popover 但非只读的是文本框", () =>
    eq(AFY.controls.kindOf(document.getElementById("t-tradename")), "text"));

  test("控件分类 · 密码框识别为 secret", () =>
    eq(AFY.controls.kindOf(document.getElementById("t-pwd")), "secret"));

  test("可见性 · Element Plus 隐藏的 checkbox 原生 input 不能被判成不可见", () => {
    const input = document.getElementById("t-promise");
    eq(AFY.util.visible(input), false, "原生 input 本身确实不可见");
    return eq(AFY.controls.visibleControl(input, "checkbox"), true, "但按包装判定应可见");
  });

  test("标签识别 · 从 el-form-item__label 取并去掉冒号", () =>
    eq(AFY.labels.labelOf(document.getElementById("t-tradename")), "名称字号"));

  test("必填识别 · is-required 容器", () =>
    eq(
      [
        AFY.labels.isRequired(document.getElementById("t-division")),
        AFY.labels.isRequired(document.getElementById("t-pinyin")),
      ],
      [true, false],
    ));

  test("弹窗识别 · 只留最内层，不把遮罩当弹窗", () => {
    const dialogs = AFY.dialogs.openDialogs();
    const ids = dialogs.map((el) => el.id);
    return eq(ids, ["t-dialog-human", "t-dialog-info", "t-dialog-validation"], "按 z-index 升序的最内层弹窗");
  });

  test("弹窗分级 · 校验提示不能被当成普通确认点掉", () => {
    const el = document.getElementById("t-dialog-validation");
    return eq(AFY.dialogs.classify(el).action, "validation");
  });

  test("弹窗按钮 · 关闭图标不进按钮清单", () => {
    const labels = AFY.dialogs.buttonsIn(document.getElementById("t-dialog-validation")).map((b) => b.label);
    return eq(labels, ["确定"], "aria-label=Close 的关闭图标应被排除");
  });

  test("弹窗分级 · 承诺书停下来交给人", () =>
    eq(AFY.dialogs.classify(document.getElementById("t-dialog-human")).action, "human"));

  test("弹窗分级 · 纯提示可自动确认，且确认按钮找得到", () => {
    const el = document.getElementById("t-dialog-info");
    eq(AFY.dialogs.classify(el).action, "confirm");
    const button = AFY.dialogs.confirmButton(el);
    return eq(button?.label, "确定");
  });

  test("盘点 · 作用域内的可填控件", () => {
    const fields = AFY.page.inventory(document.getElementById("t-form"));
    const labels = fields.map((f) => f.label);
    ok(labels.includes("名称区划"), "应盘点到名称区划");
    ok(labels.includes("登录密码"), "密码框也要盘点到（但会在计划里被排除）");
    return `共 ${fields.length} 个控件`;
  });

  test("计划 · 顺序按级联依赖，秘密与法律声明被排除", () => {
    const fields = AFY.page.inventory(document.getElementById("t-form"));
    const { plan, skipped } = AFY.planner.build(fields, DOSSIER);
    eq(
      plan.map((item) => item.canonicalKey),
      [
        "enterprise.name.administrative_division",
        "enterprise.name.trade_name",
        "enterprise.name.trade_name_pinyin",
        "enterprise.name.industry_expression",
        "enterprise.name.organization_form",
      ],
      "填写顺序",
    );
    const reasons = skipped.map((item) => item.reason);
    ok(reasons.includes("secret"), "密码框必须被排除");
    ok(reasons.includes("declaration_needs_user"), "承诺勾选必须交给人");
    ok(reasons.includes("unmapped"), "未知字段必须如实标为未映射");
    return `计划 ${plan.length} 项，跳过 ${skipped.length} 项`;
  });

  test("计划 · 分步选择器缺上一步的值时不跳过，但要把缺口带上", () => {
    const fields = AFY.page.inventory(document.getElementById("t-form"));
    // 示例档案里刻意没有 enterprise.enterprise_type。
    const { plan } = AFY.planner.build(fields, DOSSIER);
    const item = plan.find((row) => row.canonicalKey === "enterprise.name.organization_form");
    ok(item, "组织形式仍要留在计划里——企业类型可能在上一页已经选过");
    eq(item.cascade, null, "拼不出完整路径时退回单级选择");
    return eq(item.stepGap, "enterprise.enterprise_type", "缺口要如实带上");
  });

  test("计划 · 档案给全两步时拼出完整路径", () => {
    const fields = AFY.page.inventory(document.getElementById("t-form"));
    const full = { ...DOSSIER, enterprise: { ...DOSSIER.enterprise, enterprise_type: "内资公司" } };
    const { plan } = AFY.planner.build(fields, full);
    const item = plan.find((row) => row.canonicalKey === "enterprise.name.organization_form");
    eq(item.cascade, ["内资公司", "有限公司"]);
    return eq(item.pathLabel, "内资公司 / 有限公司");
  });

  test("写入与回读 · 稳定回读能读到刚写的值", async () => {
    const input = document.getElementById("t-pinyin");
    AFY.controls.writeText(input, "SHILIZIHAO");
    const readback = await AFY.controls.stableRead(input, "text", { settle: 120 });
    eq(readback.value, "SHILIZIHAO");
    return eq(readback.stable, true);
  });

  test("站点档案 · 业务域白名单只有一个源", () =>
    eq(AFY.site.businessOrigins, ["https://online.yct.sh.gov.cn"]));

  // -------------------------------------------------- 展开策略（这次线上就卡在这）

  test("展开 · 监听在内层 input 的 mousedown（只发 click 打不开）", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.enumerate(document.getElementById("t-selA-input"));
    eq(result.status, "ok");
    eq(result.options, ["上海市", "北京市"]);
    return eq(result.strategy, "self");
  });

  test("展开 · 监听在根 .el-select 上，靠冒泡也能到达", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.enumerate(document.getElementById("t-selB-input"));
    eq(result.status, "ok");
    eq(result.options, ["有限责任公司", "股份有限公司"]);
    return eq(result.strategy, "self", "从内层发出的事件冒泡上去即可，不必换策略");
  });

  test("展开 · 只认直接落在 wrapper 上的点击时，会自动升级到 wrapper 策略", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.enumerate(document.getElementById("t-selC-input"));
    eq(result.status, "ok");
    eq(result.options, ["甲", "乙"]);
    return eq(result.strategy, "wrapper", "前两种够不着时必须升级");
  });

  test("展开 · 开了但没数据要报 empty 并带出弹层原话，不能报成 no_panel", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.enumerate(document.getElementById("t-selD-input"));
    eq(result.status, "empty", "这正是「名称区划」那一页的样子");
    return eq(result.panelText, "无数据");
  });

  test("提示行 · 「1. 请选择企业类型」不是选项", () =>
    eq(
      ["1. 请选择企业类型", "2. 请选择组织形式", "请先选择上级", "内资公司", "有限责任公司"].map((t) =>
        AFY.selects.HINT_TEXT.test(t),
      ),
      [true, true, true, false, false],
    ));

  test("分步 · 只剩提示时报 needs_prior_step，不能把提示当成选项", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.enumerate(document.getElementById("t-selF-input"));
    eq(result.status, "needs_prior_step", "线上就是这里把「2. 请选择组织形式」当成了唯一选项");
    eq(result.options, [], "提示行不许出现在选项里");
    return eq(result.hints, ["1. 请选择企业类型", "2. 请选择组织形式"]);
  });

  test("分步 · 两级向导能逐级点下去", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selE-input"), [
      "内资公司",
      "有限责任公司",
    ]);
    ok(result.ok, `应当成功，实际：${result.reason || ""} ${JSON.stringify(result.available || [])}`);
    eq(result.matchLevel, "steps");
    return eq(result.picked, "内资公司 / 有限责任公司");
  });

  test("分步 · 第一级选错时如实报第几级卡住", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selE-input"), [
      "外商投资公司",
      "有限责任公司",
    ]);
    eq(result.ok, false);
    eq(result.level, 0);
    return eq(result.available, ["内资公司", "个人独资企业"], "第一级的合法值要交出来");
  });

  test("分步 · 分组标题点不动时自动跳过这一级（内资企业 → 有限公司 → 有限责任公司）", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selG-input"), [
      "内资企业",
      "有限公司",
      "有限责任公司",
    ]);
    ok(result.ok, `应当成功，实际：${result.reason || ""} ${JSON.stringify(result.available || [])}`);
    eq(result.skippedLevels, ["内资企业"], "分组标题应被识别为不可点并跳过");
    return eq(result.picked, "有限公司 / 有限责任公司");
  });

  test("分步 · 路径省掉分组这一层同样走得通", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selG-input"), [
      "有限公司",
      "有限责任公司",
    ]);
    ok(result.ok, `应当成功，实际：${result.reason || ""}`);
    eq(result.skippedLevels, []);
    return eq(result.picked, "有限公司 / 有限责任公司");
  });

  // ------------------------------------------- 自动补全：打字之后必须选中

  const autoField = (inputId) => {
    const fields = AFY.page.inventory(document.getElementById("t-auto"));
    const el = document.getElementById(inputId);
    return fields.find((row) => row.el === el);
  };

  test("自动补全 · 打完字要从候选里选中，精确优先于前缀", async () => {
    hideSyntheticPanels();
    const el = document.getElementById("t-autoA");
    AFY.controls.writeText(el, "信息科技", { commit: false });
    const result = await AFY.selects.pickSuggestion(el, "信息科技");
    ok(result.ok, `应当选中，实际：${result.reason || ""}`);
    return eq(result.picked, "信息科技", "「信息科技服务」也在候选里，精确匹配必须赢");
  });

  test("自动补全 · 有候选但没有档案里的值 → 判失败，不许假阳性通过", async () => {
    hideSyntheticPanels();
    const field = autoField("t-autoB");
    const result = await AFY.runner.fillOne({
      fieldId: field.id, kind: field.kind, label: field.label, value: "信息科技",
    });
    eq(result.status, "failed", "光把字写进去看着像成功，其实组件没收到值");
    eq(result.reason, "no_match");
    return eq(result.available, ["批发零售", "餐饮服务"], "站点候选要交出来");
  });

  test("自动补全 · 站点压根没候选时保留自由输入", async () => {
    hideSyntheticPanels();
    const field = autoField("t-autoC");
    const result = await AFY.runner.fillOne({
      fieldId: field.id, kind: field.kind, label: field.label, value: "自定义行业表述",
    });
    eq(result.status, "ok");
    return eq(result.actual, "自定义行业表述");
  });

  test("自动补全 · 端到端：选中之后回读通过", async () => {
    hideSyntheticPanels();
    const field = autoField("t-autoA");
    const result = await AFY.runner.fillOne({
      fieldId: field.id, kind: field.kind, label: field.label, value: "信息科技",
    });
    eq(result.status, "ok", `实际：${result.reason || result.verdict || ""}`);
    return eq(result.actual, "信息科技");
  });

  // ------------------------- 照抄线上的组织形式弹层（div 选项 + 两个分组同名）

  test("候选识别 · 选项是 div 不是 li 时，文本叶子兜底也要认出来", async () => {
    hideSyntheticPanels();
    document.getElementById("t-panelK").style.display = "block";
    const labels = AFY.selects.selectableIn(document.getElementById("t-panelK")).map(AFY.selects.optionLabelOf);
    hideSyntheticPanels();
    // 两条「请选择…」提示要被剔掉，两个分组标题和各自的选项都要认出来。
    return eq(labels, [
      "内资企业", "有限公司", "股份公司", "个人独资企业",
      "外资企业", "有限公司", "股份公司",
    ]);
  });

  test("分组消歧 · 内资企业 › 有限公司 › 有限责任公司 一次点到底", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selK-input"), [
      "内资企业",
      "有限公司",
      "有限责任公司",
    ]);
    ok(result.ok, `应当成功，实际：${result.reason || ""} ${JSON.stringify(result.available || [])}`);
    eq(result.scopedBy, "内资企业", "第二级的歧义必须靠分组收窄解决");
    return eq(result.picked, "内资企业 / 有限公司 / 有限责任公司");
  });

  test("writePath · 缺的层要自动补出来", () => {
    const target = {};
    AFY.util.writePath(target, "enterprise.name.organization_form", ["有限公司", "有限责任公司"]);
    AFY.util.writePath(target, "shareholders[1].name", "李四");
    eq(AFY.util.readPath(target, "enterprise.name.organization_form"), ["有限公司", "有限责任公司"]);
    return eq(AFY.util.readPath(target, "shareholders[1].name"), "李四");
  });

  test("分步 · 路径比向导浅时报 needs_next_level，且同名的下一级选项不能漏", async () => {
    hideSyntheticPanels();
    // 只给第一级：站点还有第二级没选，这不是失败，是「还差一级」。
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selK-input"), ["有限公司"]);
    eq(result.ok, false);
    eq(result.reason, "needs_next_level");
    eq(result.picked, ["有限公司"], "已点中的那一级要带出来，供面板重建路径");
    // 第二级里那个也叫「有限公司」的**必须**出现——线上就是它被漏掉了，
    // 面板上只剩一个候选，人想选它却没得选。
    return eq(result.available, ["有限责任公司", "有限公司"]);
  });

  test("分步 · 两级都叫「有限公司」也要能一路点到底（线上真实写法）", async () => {
    hideSyntheticPanels();
    // 站点实测：1. 企业类型 = 内资企业›有限公司，2. 组织形式 = 有限公司。
    // 两级恰好同名，不是笔误。
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selK-input"), [
      "有限公司",
      "有限公司",
    ]);
    ok(result.ok, `应当成功，实际：${result.reason || ""} ${JSON.stringify(result.available || [])}`);
    return eq(result.picked, "有限公司 / 有限公司");
  });

  test("分步 · 路径比向导长时忽略多出来的段，不报失败", async () => {
    hideSyntheticPanels();
    // 面板上重复点候选会攒出 ["有限公司","有限公司","有限公司"] 这种路径。
    // 向导只有两级，第三段是多余的——值其实已经选中了，不该因此判失败。
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selK-input"), [
      "有限公司",
      "有限公司",
      "有限公司",
    ]);
    ok(result.ok, `应当成功，实际：${result.reason || ""}`);
    eq(result.picked, "有限公司 / 有限公司");
    return eq(result.extraLevels, ["有限公司"]);
  });

  test("认领 · 两个关不掉的弹层同时开着时，按目标文案认领正确的那个", async () => {
    hideSyntheticPanels();
    // 复现线上 blocked_by_stale_panel：组织形式的弹层和名称区划的下拉都开着、
    // 都关不掉。目标「丙选项」只在其中一个里 → 按文案认领它，而不是拒绝。
    document.getElementById("t-panelN").style.display = "block";
    document.getElementById("t-panelN2").style.display = "block";
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selN-input"), ["丙选项"]);
    ok(result.ok, `应当成功，实际：${result.reason || ""} ${JSON.stringify(result.blockedBy || [])}`);
    return eq(result.picked, "丙选项");
  });

  test("匹配级别 · 唯一前缀命中要如实回传，不能一律报 exact", async () => {
    hideSyntheticPanels();
    // 档案写「上海市」、站点写「上海」——这是正常的写法差异。
    // 上一版 chooseSteps 恒返回 exact，于是这种成功被上层判成「站点改写了值」。
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selM-input"), "上海市");
    ok(result.ok, `应当选中，实际：${result.reason || ""}`);
    eq(result.picked, "上海");
    return eq(result.matchLevel, "prefix");
  });

  test("匹配级别 · 唯一前缀命中在填写结果里判 site_wording 而不是 transformed", async () => {
    hideSyntheticPanels();
    const fields = AFY.page.inventory(document.getElementById("t-misc"));
    const field = fields.find((row) => row.el === document.getElementById("t-selM-input"));
    const result = await AFY.runner.fillOne({
      fieldId: field.id, kind: field.kind, label: field.label, value: "上海市",
    });
    eq(result.status, "ok", "这是一次成功，不该要人工复核");
    return eq(result.verdict, "site_wording");
  });

  test("分组消歧 · 漏写分组时按约定取第一个（内资企业那一支）", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selK-input"), [
      "有限公司",
      "有限责任公司",
    ]);
    // 内资/外资各有一个「有限公司」，字面完全相同 → 取文档序第一个 = 内资企业那一支。
    ok(result.ok, `应当成功，实际：${result.reason || ""} ${JSON.stringify(result.available || [])}`);
    return eq(result.picked, "有限公司 / 有限责任公司");
  });

  const industryField = () => {
    const fields = AFY.page.inventory(document.getElementById("fixture"));
    return fields.find((row) => row.el === document.getElementById("t-autoL"));
  };

  test("自动补全 · 档案只写长文案时，自动用括号前那段当查询词搜再选中", async () => {
    hideSyntheticPanels();
    // 旧行为：长文案被原样打进输入框 → 站点搜不到 → 清空 →「请输入正确的行业表述！」。
    // 现在 fillOne 会先用括号前那段（信息科技）去搜，再在候选里精确选中整串。
    // 这条测试断言的就是这个推导：档案不必拆成两段也能填进去。
    const field = industryField();
    const result = await AFY.runner.fillOne({
      fieldId: field.id,
      kind: field.kind,
      label: field.label,
      value: "信息科技 (主营行业:通信、信息、数码科技(12678))",
    });
    ok(result.status === "ok", `应当成功，实际：${result.status}/${result.reason || result.verdict || ""}`);
    // 输入框最终显示的是查询词那一侧。
    return eq(result.actual, "信息科技");
  });

  test("自动补全 · 查询词与选中项分开写，就能填进去", async () => {
    hideSyntheticPanels();
    const field = industryField();
    const result = await AFY.runner.fillOne({
      fieldId: field.id,
      kind: field.kind,
      label: field.label,
      value: "信息科技",
      // [查询词, 要选的那一条]
      cascade: ["信息科技", "信息科技 (主营行业:通信、信息、数码科技(12678))"],
    });
    ok(result.status === "ok", `应当成功，实际：${result.status} / ${result.reason || result.verdict || ""}`);
    // 输入框最终显示的是查询词那一侧，回读要按它比。
    return eq(result.actual, "信息科技");
  });

  test("自动补全 · 只写查询词时，多条同名候选要判歧义交给人", async () => {
    hideSyntheticPanels();
    const el = document.getElementById("t-autoL");
    AFY.controls.writeText(el, "信息科技", { commit: false });
    const result = await AFY.selects.pickSuggestion(el, "信息科技");
    eq(result.ok, false, "两条只差主营行业代码，替人选就是替人做业务决定");
    return eq(result.reason, "ambiguous");
  });

  test("探查 · 普通下拉能倒出结构", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.describePanel(document.getElementById("t-selA-input"));
    ok(result.ok, `应当探查成功，实际：${result.reason || ""}`);
    return ok(result.outline.includes("li.el-select-dropdown__item"), "结构里应当出现选项元素");
  });

  test("探查 · 只有提示的弹层也要倒得出来", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.describePanel(document.getElementById("t-selF-input"));
    ok(result.ok, `应当探查成功，实际：${result.reason || ""}`);
    ok(result.outline.includes("li.item"), "结构里应当出现 li.item");
    return eq(result.hints, ["1. 请选择企业类型", "2. 请选择组织形式"]);
  });

  test("择一 · 「有限公司」对不上「有限责任公司」，且要把站点选项交出来", async () => {
    hideSyntheticPanels();
    const result = await AFY.selects.choose(document.getElementById("t-selB-input"), "有限公司");
    eq(result.ok, false, "前缀关系不成立：两边互相都不是对方的前缀");
    eq(result.reason, "no_match");
    return eq(result.available, ["有限责任公司", "股份有限公司"], "必须把合法值摆出来给人改档案");
  });

  // ---------------------------------------------------------------- 可信输入

  test("可信输入 · 没有真实扩展通道时必须安静降级", async () => {
    ok(AFY.trusted, "trusted 模块应当已装载");
    const state = await AFY.trusted.init();
    // 自检页的 chrome 是空壳（AFY_CDP_STATUS 收不到 permission 字段），
    // 通道必须停在不可用、enabled() 为假——本页其余全部下拉用例因此走的都是
    // 合成事件兜底，它们全绿即证明「可信通道不在场时引擎行为不变」。
    eq(AFY.trusted.enabled(), false, "自检环境下不允许 ready");
    ok(state.mode === "unavailable" || state.mode === "off", `mode 应为降级态，实际 ${state.mode}`);
    return `mode=${state.mode}（${state.reason || "无"}），点击将全部走合成事件`;
  });

  test("可信输入 · 跨 iframe 坐标要累加相框偏移与边框", async () => {
    // CDP 的坐标系是**顶层视口**。wujie 子应用在 blob: iframe 里，元素坐标必须
    // 逐层加上宿主 iframe 的位置和 clientLeft/Top，少一层就点到别人身上。
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;left:40px;top:60px;width:320px;height:220px;background:#fff;z-index:9998";
    const frame = document.createElement("iframe");
    frame.style.cssText = "width:280px;height:180px;border:4px solid #888";
    host.append(frame);
    document.body.append(host);
    try {
      // 刻意不设 src：刚插入的 iframe 就是同步可用的 about:blank。设 src 会触发一次
      // 异步导航，期间 contentDocument 瞬时为 null——虚拟时间/无头环境下必踩。
      await AFY.util.until(() => (frame.contentDocument?.body ? true : null), { timeout: 2000, interval: 50 });
      const doc = frame.contentDocument;
      ok(doc?.body, "同源 iframe 的 contentDocument 应当可用");
      doc.body.style.margin = "0";
      const target = doc.createElement("button");
      target.textContent = "点我";
      target.style.cssText = "position:absolute;left:50px;top:30px;width:100px;height:20px;margin:0";
      doc.body.append(target);
      const point = AFY.trusted.pointOf(target);
      ok(point, "同源 iframe 里的元素应当算得出顶层坐标");
      const frameRect = frame.getBoundingClientRect();
      const inner = target.getBoundingClientRect();
      const expectX = frameRect.left + frame.clientLeft + inner.left + inner.width / 2;
      const expectY = frameRect.top + frame.clientTop + inner.top + inner.height / 2;
      ok(
        Math.abs(point.x - expectX) < 1.5 && Math.abs(point.y - expectY) < 1.5,
        `期望 (${expectX.toFixed(1)}, ${expectY.toFixed(1)})，实际 (${point.x.toFixed(1)}, ${point.y.toFixed(1)})`,
      );
      return `顶层坐标 (${point.x.toFixed(1)}, ${point.y.toFixed(1)})，含 4px 边框偏移`;
    } finally {
      host.remove();
    }
  });

  test("跨文档 · 子 frame 里的字段必须被认出来（不能用 instanceof）", async () => {
    // 这一条盯的是一个会让整个扩展「装上去什么都不发生」的坑：
    // 引擎靠同源穿透进 blob: iframe 取元素（本站子应用全在那里），而子文档有自己
    // 的一套内置构造器——`el instanceof Element` 对它们恒为 false。写成 instanceof
    // 时，visible 全判不可见、kindOf 全判 unknown，page.inventory 把子应用的每个
    // 字段都跳过，界面显示「当前业务页面没有可填写字段」，而字段就在眼前。
    // 顶层 fixture 永远测不出这一条，只有真的跨一层文档才暴露。
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;left:20px;top:20px;width:300px;height:160px;background:#fff;z-index:9997";
    const frame = document.createElement("iframe");
    frame.style.cssText = "width:280px;height:140px;border:0";
    host.append(frame);
    document.body.append(host);
    try {
      await AFY.util.until(() => (frame.contentDocument?.body ? true : null), { timeout: 2000, interval: 50 });
      const doc = frame.contentDocument;
      doc.body.style.margin = "0";
      doc.body.innerHTML = `
        <div class="el-form-item is-required">
          <label class="el-form-item__label">子应用里的名称区划：</label>
          <div class="el-select"><div class="el-input"><input id="sub-division" class="el-input__inner"></div></div>
        </div>`;
      const input = doc.getElementById("sub-division");
      eq(AFY.util.isElement(input), true, "跨文档的元素判定");
      eq(AFY.util.visible(input), true, "子文档里的可见控件必须判为可见");
      eq(AFY.controls.kindOf(input), "custom-select", "子文档里的 el-select 必须认出来");
      eq(AFY.labels.labelOf(input), "子应用里的名称区划", "标签要跨文档读得到");
      // 端到端：整页盘点必须把它收进来，否则就是「页面上没有字段」那种静默失败。
      // 先跨过 roots 的 50ms 根缓存——刚插进来的 iframe 文档还不在上一次的根列表里。
      await AFY.util.delay(80);
      const rows = AFY.page.inventory(null).filter((row) => row.el === input);
      eq(rows.length, 1, "page.inventory 必须盘点到子 frame 里的字段");
      return "子 frame 字段：可见 / 分类 / 标签 / 盘点 四项均正常";
    } finally {
      host.remove();
    }
  });

  test("可信输入 · 命中预检要识破遮挡，绝不把点击发出去赌", async () => {
    // CDP 点击走真实 hit-testing：目标被遮罩盖住时，点下去的是遮罩不是目标。
    // 预检不过必须报 occluded，让调用方回退合成事件（合成事件直达目标不怕遮挡）。
    const target = document.createElement("button");
    target.textContent = "目标";
    target.style.cssText = "position:fixed;left:120px;top:120px;width:80px;height:24px;z-index:1";
    const cover = document.createElement("div");
    cover.style.cssText = "position:fixed;left:100px;top:100px;width:200px;height:100px;background:#fff;z-index:9999";
    document.body.append(target, cover);
    try {
      const covered = AFY.trusted.hitTest(target);
      eq(covered.ok, false, "被整块遮住时必须拒绝");
      eq(covered.reason, "occluded");
      cover.remove();
      const clear = AFY.trusted.hitTest(target);
      eq(clear.ok, true, "遮挡移走后应当命中");
      return "遮挡 → occluded；移开 → 命中";
    } finally {
      target.remove();
      cover.remove();
    }
  });

  test("可信输入 · 顶层遮罩盖住 iframe 时必须判 occluded", async () => {
    // 只在元素自己的文档里做命中预检是个盲区：目标在子应用 iframe 里、遮罩在顶层
    // 文档时，子文档查得好好的，CDP 的真实点击却落在顶层遮罩上（而遮罩上的点击
    // 在某些站点等同于「取消」）。这一击还会被报成成功，调用方不回退，于是这次
    // 点击彻底没了着落。预检必须逐层查到顶层。
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;left:30px;top:200px;width:300px;height:160px;background:#fff;z-index:10";
    const frame = document.createElement("iframe");
    frame.style.cssText = "width:280px;height:140px;border:0";
    host.append(frame);
    document.body.append(host);
    const cover = document.createElement("div");
    try {
      await AFY.util.until(() => (frame.contentDocument?.body ? true : null), { timeout: 2000, interval: 50 });
      const doc = frame.contentDocument;
      doc.body.style.margin = "0";
      const target = doc.createElement("button");
      target.textContent = "子应用里的确定";
      target.style.cssText = "position:absolute;left:40px;top:40px;width:120px;height:24px;margin:0";
      doc.body.append(target);
      eq(AFY.trusted.hitTest(target).ok, true, "没有遮挡时应当命中");

      // 顶层文档盖一层遮罩（el-overlay 的形态），完全罩住这个 iframe。
      cover.style.cssText = "position:fixed;left:0;top:180px;width:400px;height:220px;background:rgba(0,0,0,.5);z-index:9999";
      document.body.append(cover);
      const blocked = AFY.trusted.hitTest(target);
      eq(blocked.ok, false, "顶层遮罩盖住子 frame 时必须拒绝");
      eq(blocked.reason, "occluded");
      return `顶层遮挡被识破（hitBy=${blocked.hitBy}）`;
    } finally {
      cover.remove();
      host.remove();
    }
  });

  test("勾选 · 取消勾选不能被补点翻回去", async () => {
    // 合成兜底路径里的补点曾经按「还没勾上」判断方向：档案要求取消勾选时，
    // 第一击成功取消之后这个条件恰好成立，补点又把它勾回来，净效果为零，
    // 于是这个字段永远填不成「否」，而回读只能报「没填上」。
    const box = document.createElement("div");
    box.innerHTML = `
      <label class="el-checkbox">
        <span class="el-checkbox__input"><span class="el-checkbox__inner"></span>
          <input type="checkbox" class="el-checkbox__original" checked>
        </span>
        <span class="el-checkbox__label">是否有分支机构</span>
      </label>`;
    box.style.cssText = "position:fixed;left:400px;top:400px;z-index:9996";
    document.body.append(box);
    try {
      const input = box.querySelector("input");
      eq(input.checked, true, "起始态：已勾选");
      await AFY.controls.clickChoice(input, false); // 目标：取消
      eq(input.checked, false, "取消之后不能被补点翻回勾选");
      await AFY.controls.clickChoice(input, true); // 目标：勾上
      return eq(input.checked, true, "反方向同样要生效");
    } finally {
      box.remove();
    }
  });

  test("组织形式 · radio 型候选必须被认成选项（实测形态）", async () => {
    // 2026-08-06 登录态实测揭出来的真病因：这个弹层的候选是 Element Plus 的 radio，
    // 而「带表单控件的元素不算选项」那条防误判规则把它们全挡住了——弹层明明开着，
    // 一个候选都识别不出来，回读为空报 empty_after_write，人根本看不出断在哪一步。
    hideSyntheticPanels();
    const result = await AFY.selects.enumerate(document.getElementById("t-selR-input"));
    eq(result.status, "ok", `应当读出候选，实际 ${result.status}`);
    // 五个 radio（内资 3 + 外资 2）去重后剩三种文案。
    eq(result.options, ["有限公司", "股份公司", "个人独资企业"], "同名的外资「有限公司/股份公司」去重后只留一份");
    // 关键：那两行静态提示绝不能混进候选——点它没有任何意义，
    // 而「站点实际给的选项：2. 请选择组织形式」这种建议害过人。
    const polluted = result.options.filter((text) => /请选择/.test(text));
    return eq(polluted, [], "静态提示行不许出现在候选里");
  });

  test("组织形式 · 两级同名的 radio 向导要能一路点到底", async () => {
    // 路径 ["有限公司","有限公司"]：第一级内资/外资各有一个同名项（按约定取第一个），
    // 第二级要等接口回来才渲染。这正是档案里那条 organization_form 的真实走法。
    hideSyntheticPanels();
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selR-input"), ["有限公司", "有限公司"]);
    ok(result.ok, `应当选中，实际 reason=${result.reason} level=${result.level} available=${JSON.stringify(result.available || [])}`);
    const l2 = document.getElementById("t-panelR-l2");
    // 决定性断言：**第二级**那一项必须真的选中。上一版只走完流程就算过，
    // 而线上正是「两级都走完、第二级从没被点、引擎报成功」。
    const checkedLabel = [...l2.querySelectorAll(".el-radio")]
      .filter((item) => item.querySelector("input").checked)
      .map((item) => item.querySelector(".el-radio__label").textContent.trim());
    eq(checkedLabel, ["有限公司"], "第二级里被选中的必须正好是「有限公司」");
    return `picked=${result.picked}`;
  });

  test("安全 · 持久授权也只覆盖知情类，法律意思表示照样停", async () => {
    // 用户在设置页开了「自动确认告知后果类提示」之后，仍然**不能**让承诺书、
    // 签名、实名、支付、最终提交自动通过——那不是「默认关闭的功能」，是边界。
    const real = { state: { ...AFY.runner.state } };
    try {
      AFY.runner.authorizeAcknowledge({ persistent: true });
      ok(AFY.runner.state.acknowledgeAuthorized, "持久授权应当生效");
      // 重置本次运行不该把用户对工具的配置也清掉。
      AFY.runner.resetRun();
      ok(AFY.runner.state.acknowledgeAuthorized, "重置只清一次性授权，不清设置页的开关");
      // 而设置页关掉时要能真的收回。
      AFY.runner.revokeAcknowledge();
      eq(AFY.runner.state.acknowledgeAuthorized, false, "设置页关掉就要立刻收回");

      // 边界本身：无论授权与否，这些分类都不是 acknowledge。
      const neverAuto = ["请确认已阅读《告知承诺书》", "请完成人脸识别", "确认支付 500 元"];
      for (const text of neverAuto) {
        eq(AFY.policy.isAcknowledge(text), false, `绝不能落进可授权那一类：${text}`);
      }
      return "持久授权只管知情类；承诺/实名/支付照样停";
    } finally {
      Object.assign(AFY.runner.state, real.state);
    }
  });

  test("安全 · 知情确认可授权代点，但法律意思表示没有开关", () => {
    // 分级的意义：「一经申报无法修改」是站点**告知后果**、等人点头知悉；
    // 而「承诺 / 签署 / 实名 / 支付」是**本人做出承诺或处置财产**。
    // 前者读过之后可以授权由插件代点（否则每页都要人点一次，连续填报形同虚设）；
    // 后者任何开关都不放行——它不是「默认关闭的功能」，是边界。
    const ackOnly = [
      "您选择申报的为有限责任公司名称，请仔细核对投资人信息，一经申报无法修改企业类型",
      "一旦提交将不能修改",
      "该信息无法更改，请谨慎填写",
    ];
    for (const text of ackOnly) {
      eq(AFY.policy.isHumanOnly(text), false, `不该被判成「永不自动」：${text.slice(0, 20)}`);
      ok(AFY.policy.isAcknowledge(text), `应当判为知情确认：${text.slice(0, 20)}`);
    }
    const neverAuto = [
      "请确认已阅读《告知承诺书》并对所填内容的真实性作出承诺",
      "请完成人脸识别以继续",
      "点击确定即完成电子签名",
      "本次操作将支付 500 元",
      "确认放弃本次申报？",
    ];
    for (const text of neverAuto) {
      ok(AFY.policy.isHumanOnly(text), `必须永不自动：${text.slice(0, 20)}`);
      eq(AFY.policy.isAcknowledge(text), false, `永不自动的绝不能降级成知情类：${text.slice(0, 20)}`);
    }
    // 两类同时命中时按更严的处理——这条守的是「降级路径不能被绕过」。
    const mixed = "您承诺所填内容真实，一经申报无法修改";
    ok(AFY.policy.isHumanOnly(mixed), "混合措辞要按更严的那一类");
    return eq(AFY.policy.isAcknowledge(mixed), false, "混合措辞绝不能落进可授权那一类");
  });

  test("安全 · 「一经申报无法修改」绝不能被当成纯提示直接点掉", () => {
    // 站点原话（2026-08-06 实测）：「您选择申报的为有限责任公司名称，请仔细核对
    // 投资人信息，一经申报无法修改企业类型」。当时词表里只有「不可撤销」四个字，
    // 措辞对不上，于是这个不可逆后果的确认被当成**纯提示**自动点掉了——人连它
    // 说过什么都不知道。穷举具体词追不上站点措辞，所以认句式。
    //
    // 这里不关心它最终落进 human 还是 acknowledge（两者的区别见上一条用例），
    // 只守一件事：**绝不能落进「无脑点掉」那一类**。
    const mustStop = [
      "您选择申报的为有限责任公司名称，请仔细核对投资人信息，一经申报无法修改企业类型",
      "一旦提交将不能修改",
      "该信息无法更改，请谨慎填写",
      "请确认已阅读《告知承诺书》",
      "提交后不可撤销",
    ];
    for (const text of mustStop) {
      ok(
        AFY.policy.isHumanOnly(text) || AFY.policy.isAcknowledge(text),
        `不能被当成纯提示点掉：${text.slice(0, 24)}`,
      );
    }
    // 反面同样要守住：普通提示和校验提示不能被这条规则拖住，
    // 否则每翻一页都要停，连续填报就没意义了。
    const mayPass = ["本步骤信息已暂存", "名称核验通过，是否继续", "字号包含生僻字，请修改字号"];
    for (const text of mayPass) {
      eq(AFY.policy.isHumanOnly(text), false, `不该拦：${text}`);
      eq(AFY.policy.isAcknowledge(text), false, `也不该判成知情确认：${text}`);
    }
    return `${mustStop.length} 条不可逆措辞全部拦下，${mayPass.length} 条普通提示照常放行`;
  });

  test("弹窗推进 · 认得出「继续申请」，但绝不碰最终提交", () => {
    // 名称申报的备用字号弹窗，推进按钮写的是「继续申请」——页面级的 NEXT 词表
    // （下一步/继续）认不出它，所以弹窗单列一套措辞。
    ok(AFY.policy.isDialogProceed("继续申请"), "「继续申请」要能认出来");
    ok(AFY.policy.isDialogProceed("继续"), "常见措辞也要认");
    // 而终局动作永远走那条要人连点两次授权的路，措辞再像「继续」也不放行。
    for (const text of ["提交", "提交申报", "确认提交", "完成申报"]) {
      eq(AFY.policy.isDialogProceed(text), false, `最终提交不许被当成推进：${text}`);
    }

    // 按钮挑选：唯一命中才认，认不准就交给人。
    const box = document.createElement("div");
    box.className = "el-dialog";
    box.innerHTML = `
      <div class="el-dialog__body">备用字号录入</div>
      <div class="el-dialog__footer">
        <button class="el-button">返回修改</button>
        <button class="el-button el-button--primary">继续申请</button>
      </div>`;
    box.style.cssText = "position:fixed;left:500px;top:300px;z-index:9994";
    document.body.append(box);
    try {
      const proceed = AFY.dialogs.proceedButton(box);
      ok(proceed, "应当挑出「继续申请」");
      eq(proceed.label, "继续申请");
      // 关闭类按钮不该被当成推进。
      return eq(AFY.policy.isDialogProceed("返回修改"), false, "「返回修改」不是推进");
    } finally {
      box.remove();
    }
  });

  test("按钮盘点 · 被禁用的「下一步」要单独记下来，不能只是消失", () => {
    // 实测卡在这里：四个字段全填好、名称也生成了，「下一步」却从可点清单里消失。
    // 而 actions 只收「可见且可点」的，于是「按钮没了」和「按钮被禁用了」长得一样
    // ——可前者是页面已经变了，后者是站点在等校验，下一步完全不同。
    const box = document.createElement("div");
    box.innerHTML = `
      <button class="el-button">返回首页</button>
      <button class="el-button" disabled>下一步</button>
      <button class="el-button" style="display:none">提交申报</button>`;
    box.style.cssText = "position:fixed;left:600px;top:500px;z-index:9995";
    document.body.append(box);
    try {
      const active = AFY.page.actions(box).map((item) => item.label);
      eq(active, ["返回首页"], "禁用和隐藏的都不该进可点清单");
      const inactive = AFY.page.inactiveActions(box);
      const next = inactive.find((item) => item.role === "next");
      ok(next, "被禁用的「下一步」必须被记下来");
      eq(next.why, "disabled", "要说清是禁用还是隐藏");
      const submit = inactive.find((item) => item.label === "提交申报");
      return eq(submit?.why, "hidden", "隐藏的也要如实标注");
    } finally {
      box.remove();
    }
  });

  test("翻页判据 · 标签随填写内容变化时，页面身份不能跟着变", () => {
    // 实测那一幕：名称申报页那三个拼音选项的标签是**动态生成的名称预览**——
    // 字号填了叫「上海愿力成像信息科技」，字号被站点清空就变成「上海」。
    // 身份若认标签，页面明明没动、身份却变了，于是又一次「以为翻页成功」，
    // 在同一页上重填一遍，把已填的值冲掉。身份必须认控件标记。
    const stage = { id: "name_declaration", label: "名称申报" };
    const filled = {
      stage,
      fields: [
        { id: "afy-1", label: "名称字号" },
        { id: "afy-5", label: "上海愿力成像信息科技" },
        { id: "afy-6", label: "（上海）信息科技" },
      ],
    };
    // 站点清空字号之后：同一批控件，标签全变了。
    const cleared = {
      stage,
      fields: [
        { id: "afy-1", label: "名称字号" },
        { id: "afy-5", label: "上海" },
        { id: "afy-6", label: "（上海）" },
      ],
    };
    eq(AFY.runner.identityOf(cleared), AFY.runner.identityOf(filled), "同一批控件必须是同一个页面身份");
    // 而真的换了一批控件时，身份必须变——否则会把真推进误判成原地打转。
    const nextPage = {
      stage,
      fields: [{ id: "afy-20", label: "股东名称" }, { id: "afy-21", label: "出资额" }],
    };
    return ok(
      AFY.runner.identityOf(nextPage) !== AFY.runner.identityOf(filled),
      "换了一批控件时身份必须变",
    );
  });

  test("翻页判据 · 弹层开关不能改变「页面身份」", () => {
    // 这次「填两遍、把填好的值冲掉」的病根：判断「翻过去了吗」时把弹层里的字段
    // 也算进了页面身份。组织形式那个 popover 一开，弹层里十几个 radio 就被盘进
    // 字段清单（7 → 19），关掉又变回 7——身份跟着抖，「原地未动」就被判成
    // 「进了新页」，于是在同一页上重填一遍。
    hideSyntheticPanels();
    const panel = document.getElementById("t-panelR");
    const stage = { id: "name_declaration", label: "名称申报" };
    const pageFields = [
      { label: "名称字号", el: document.getElementById("t-tradename") },
      { label: "组织形式", el: document.getElementById("t-selR-input") },
    ];
    const closed = { stage, fields: pageFields };
    // 弹层打开：它里面的 radio 也会被盘进来。
    panel.style.display = "block";
    try {
      const panelFields = [...panel.querySelectorAll(".el-radio")].map((label) => ({
        label: label.querySelector(".el-radio__label").textContent.trim(),
        el: label.querySelector("input"),
      }));
      ok(panelFields.length >= 5, "fixture 里弹层要真的有一批候选，否则测不到什么");
      const opened = { stage, fields: [...pageFields, ...panelFields] };
      return eq(
        AFY.runner.identityOf(opened),
        AFY.runner.identityOf(closed),
        "弹层开着和关着必须是同一个页面身份",
      );
    } finally {
      panel.style.display = "none";
    }
  });

  test("连续填报 · 中途停下再继续时，已填过的页不会重来", async () => {
    // 实测那一幕：连续填报因为「等你确认一个提示」中途返回，用户确认后再继续——
    // 而 seenPages 当时是 autoRun 的局部变量，新一轮从零开始，于是同一页又填一遍，
    // 把已经填好的名称字号冲成空。记录必须跨调用保留在 state 里。
    const real = { seen: new Set(AFY.runner.state.seenPages), running: AFY.runner.state.autoRunning };
    try {
      AFY.runner.state.seenPages.clear();
      const identity = AFY.runner.identityOf({
        stage: { id: "name_declaration", label: "名称申报" },
        fields: [{ label: "名称字号" }],
      });
      AFY.runner.state.seenPages.add(identity);
      ok(AFY.runner.state.seenPages.has(identity), "记录要留在 state 上，跨一次调用还在");
      // 重置本次运行才清空——那是用户明确表示「换一件重来」。
      AFY.runner.resetRun();
      return eq(AFY.runner.state.seenPages.size, 0, "重置本次运行时才清空已填页记录");
    } finally {
      AFY.runner.state.seenPages.clear();
      real.seen.forEach((item) => AFY.runner.state.seenPages.add(item));
      AFY.runner.state.autoRunning = real.running;
    }
  });

  test("连续填报 · 已经填过的页不会再填第二遍", async () => {
    // 端到端那一半（fill/advance）是模块闭包里的常量，桩件穿不进去，硬造 DOM 也
    // 测不出什么。真正要守的决策只有一条：**这一页的身份见过就不再填**。
    // 直接验证它——身份判据本身由上一条用例守着，两条合起来覆盖这次的病根。
    const stage = { id: "name_declaration", label: "名称申报" };
    const fields = [{ label: "名称字号" }, { label: "组织形式" }];
    const first = AFY.runner.identityOf({ stage, fields });
    // 站点在同一步里做了局部更新（点掉一个提示框），指纹会变、身份不变。
    const afterDialog = AFY.runner.identityOf({ stage, fields: [...fields] });
    eq(afterDialog, first, "同一页在弹窗开关前后必须是同一个身份");
    // 真的翻到下一批字段时，身份必须变——否则会把真推进误判成原地打转。
    const nextPage = AFY.runner.identityOf({
      stage: { id: "investor", label: "投资人信息" },
      fields: [{ label: "股东名称" }],
    });
    return ok(nextPage !== first, "真的进了下一步时身份必须变");
  });

  test("连续填报 · 走到提交页必须停手，绝不替人按下去", async () => {
    // 这是硬边界：提交一次申报是不可撤销的意思表示，自动流程只负责把人送到这一步。
    const real = {
      snapshot: AFY.page.snapshot,
      fill: AFY.runner.fill,
      advance: AFY.runner.advance,
      origins: AFY.site.businessOrigins,
    };
    let filled = 0;
    AFY.site.businessOrigins = [...real.origins, location.origin];
    try {
      AFY.page.snapshot = () => ({
        stage: { id: "confirm", label: "确认提交" },
        fields: [],
        actions: [{ role: "submit", label: "提交申报" }],
        errors: [],
        dialog: null,
        fingerprint: "fp-submit",
      });
      AFY.runner.fill = async () => {
        filled += 1;
        return { ok: true, results: [], failures: [], skipped: [], missingRequired: [], hints: [], readyToAdvance: true };
      };
      AFY.runner.advance = async () => {
        throw new Error("走到提交页之后绝不该再翻页");
      };
      const result = await AFY.runner.autoRun({ dossier: {}, maxPages: 6 });
      eq(result.pause, "reached_submit");
      eq(filled, 0, "提交页连填都不该填，更不该点提交");
      return ok(result.message.includes("不碰提交"), "要明说自动流程不碰提交");
    } finally {
      Object.assign(AFY.page, { snapshot: real.snapshot });
      Object.assign(AFY.runner, { fill: real.fill, advance: real.advance });
      AFY.site.businessOrigins = real.origins;
    }
  });

  test("计划 · 「备用字号」不能被模糊匹配吃到主字号的值", async () => {
    // 实测：名称申报的确认弹窗里有「备用字号1 / 备用字号2」（非必填，本意是让人
    // 另填两个**不同的**备选名称），标签含「字号」于是被 contains:字号 映射到
    // enterprise.name.trade_name，主字号被原样填了进去——同一个名字提交三遍。
    const dossier = { enterprise: { name: { trade_name: "愿力成象" } } };
    const fields = [
      { id: "f1", label: "名称字号", kind: "text", occurrence: 0 },
      { id: "f2", label: "备用字号1", kind: "text", occurrence: 0 },
      { id: "f3", label: "备用字号2", kind: "text", occurrence: 0 },
    ];
    const { plan, skipped } = AFY.planner.build(fields, dossier);
    eq(plan.map((item) => item.label), ["名称字号"], "只有主字号该进计划");
    // 关键是**没拿主字号的值去填它们**。有了专属别名之后，它们各自落在自己的
    // 备用键上、因为档案里没有这条事实而如实报 no_fact——这比笼统的
    // 「需要你决定」更准确，也更好办：补上档案就能填。
    const alt = skipped.filter((item) => item.label.startsWith("备用字号"));
    eq(alt.map((item) => item.canonicalKey), [
      "enterprise.name.alternative_1",
      "enterprise.name.alternative_2",
    ], "必须落在备用键上，绝不能是 trade_name");
    return eq(alt.map((item) => item.reason), ["no_fact", "no_fact"], "档案里没有就如实说没有");
  });

  test("计划 · 档案里真有备用字号时要填进去（负向规则不能误伤）", async () => {
    // 上一条的反面：拦的是「模糊匹配落到主字段」，不是「备用字段本身」。
    // 档案里备好了 alternative_1/2，「备用字号1」应当精确命中它并正常填写。
    const dossier = {
      enterprise: {
        name: { trade_name: "愿力成象", alternative_1: "愿力智算", alternative_2: "愿力数科" },
      },
    };
    const fields = [
      { id: "f1", label: "备用字号1", kind: "text", occurrence: 0 },
      { id: "f2", label: "备用字号2", kind: "text", occurrence: 0 },
    ];
    const { plan } = AFY.planner.build(fields, dossier);
    eq(plan.map((item) => item.value), ["愿力智算", "愿力数科"], "各填各的备用名，不能是主字号");
    return eq(
      plan.map((item) => item.canonicalKey),
      ["enterprise.name.alternative_1", "enterprise.name.alternative_2"],
      "要落在备用键上",
    );
  });

  test("组织形式 · 第二级找不到时绝不能回头点第一级", async () => {
    // 对抗性评审在真实浏览器里复现出来的：第二级（有限责任公司/有限公司）里没有
    // 「股份公司」，而兜底会把候选池放宽到整个弹层，于是命中**第一级**的
    // 「股份公司」并点下去——把已经选对的企业类型改掉，组织形式仍然是空，
    // 还返回成功。三条红线一次踩全：点错了、没停下来问人、报了假成功。
    hideSyntheticPanels();
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selR-input"), [
      "有限公司",
      "股份公司", // 第二级里没有这个值
    ]);
    eq(result.ok, false, "第二级没有这个值就必须失败，不许去别的级里找");
    eq(result.reason, "no_match");
    eq(result.level, 1, "要说清卡在第几级");
    eq(result.picked, ["有限公司"], "第一级的成果要如实保留，不能把第一级的候选当成第二级的结果");
    // 决定性证据：第一级那个已经选对的答案不许被改掉。
    const l1 = document.getElementById("t-panelR").querySelector("li");
    const checked = [...l1.querySelectorAll(".el-radio")]
      .filter((item) => item.querySelector("input").checked)
      .map((item) => item.querySelector(".el-radio__label").textContent.trim());
    eq(checked, ["有限公司"], "第一级必须还是「有限公司」，绝不能被改成「股份公司」");
    // 摆给人看的候选必须是这一级的，否则人会问「股份公司明明在里面」。
    return eq(result.available, ["有限责任公司", "有限公司"], "候选只能列本级的合法值");
  });

  test("组织形式 · 路径里带分组名（被跳过的那一级）不能把分组索引带偏", async () => {
    // 同一轮评审复现：分组索引用的是**路径下标**而不是已提交的步数，
    // 跳过「内资企业」这个分组标题后两者错位，于是在两组的弹层里去要 groups[2]，
    // 白等 4500ms 超时报 empty——而目标当时就在第二组里。
    // 而 ["内资企业", …] 正是 site-profile 注释里推荐用户写的形状。
    hideSyntheticPanels();
    const started = Date.now();
    const result = await AFY.selects.chooseSteps(document.getElementById("t-selR-input"), [
      "内资企业", // h2 分组标题，不是候选，会被跳过
      "有限公司",
      "有限公司",
    ]);
    ok(result.ok, `带分组名的路径要能走通，实际 reason=${result.reason} level=${result.level}`);
    ok(Date.now() - started < 4000, `不该等到超时（用了 ${Date.now() - started}ms）`);
    const l2 = document.getElementById("t-panelR-l2");
    const checked = [...l2.querySelectorAll(".el-radio")]
      .filter((item) => item.querySelector("input").checked)
      .map((item) => item.querySelector(".el-radio__label").textContent.trim());
    eq(checked, ["有限公司"], "第二级要真的选中");
    return `skipped=${JSON.stringify(result.skippedLevels)} picked=${result.picked}`;
  });

  test("组织形式 · 合成兜底必须真的能选中 radio（不能只派发事件）", async () => {
    // 2026-08-06 实测最后一关：第二级在 popover 可滚动区底部，可信点击的命中预检
    // 判它出了视口、拒绝派发，回退到合成事件——而 dispatchEvent(new MouseEvent)
    // **不触发默认行为**，label 里那个隐藏 input 根本不会被选中。
    // 结果是「点了、chooseSteps 报成功、就是没选中」，只有回读说一句「值是空的」。
    // 自检环境没有可信通道，走的正是这条合成路径。
    hideSyntheticPanels();
    const panel = document.getElementById("t-panelR");
    panel.style.display = "block";
    try {
      const label = [...panel.querySelectorAll(".el-radio")].find(
        (item) => item.querySelector(".el-radio__label").textContent.trim() === "股份公司",
      );
      const input = label.querySelector("input");
      eq(input.checked, false, "起始态：没选中");
      await AFY.selects.clickNode(label);
      return ok(input.checked, "合成兜底之后这一项必须真的处于选中状态");
    } finally {
      panel.style.display = "none";
    }
  });

  test("组织形式 · 点了却没选中要如实报失败，不能当成功", async () => {
    // 比「没点上」更糟的是「没点上却报成功」：chooseSteps 照常走完，最后只有回读
    // 说一句 empty_after_write，人根本看不出是哪一级断的。
    // 这里造一个吃掉点击的候选（阻止默认行为 + 不更新 checked），验证它被抓住。
    hideSyntheticPanels();
    const panel = document.getElementById("t-panelR");
    const swallow = (event) => {
      event.preventDefault();
      event.stopPropagation();
    };
    // 第一级的「个人独资企业」装成点不动的样子。
    const target = [...panel.querySelectorAll(".el-radio")].find(
      (item) => item.querySelector(".el-radio__label").textContent.trim() === "个人独资企业",
    );
    // mousedown 也要拦：fixture 在 mousedown 里就模拟组件把 checked 设上了，
    // 只拦 click 的话这一项照样会变成选中，用例就测不到「点不动」那条路。
    target.addEventListener("mousedown", swallow, true);
    target.addEventListener("click", swallow, true);
    try {
      const result = await AFY.selects.chooseSteps(document.getElementById("t-selR-input"), ["个人独资企业"]);
      eq(result.ok, false, "点不动就必须报失败，不许返回成功");
      eq(result.reason, "click_not_applied");
      return ok(result.detail?.includes("没有变成选中状态"), "要说清是「点到了但没生效」，而不是「找不到」");
    } finally {
      target.removeEventListener("mousedown", swallow, true);
      target.removeEventListener("click", swallow, true);
      panel.style.display = "none";
    }
  });

  test("组织形式 · 弹层已经开着（自己的）时不能反被自己挡在门外", async () => {
    // 2026-08-06 实测那一幕：observe 阶段枚举把组织形式的弹层打开了、收尾没关掉
    // （本站 orgDescPopper 不吃 Escape 也不吃外部点击），轮到 fill 时报
    // blocked_by_stale_panel——挡路的正是它自己。而触发器是个开关，六种展开姿势
    // 轮流去点会让弹层在开/关之间摆动，收尾那一刻正好是关的。
    // 判据必须是「几何上锚定在这个触发器上」，不能是「新出现的」。
    hideSyntheticPanels();
    const trigger = document.getElementById("t-selR-input");
    const panel = document.getElementById("t-panelR");
    // 让弹层在展开之前就可见，并把它摆到触发器正下方（popper 的真实位置关系）。
    const rect = trigger.getBoundingClientRect();
    panel.style.display = "block";
    panel.style.position = "fixed";
    panel.style.left = `${rect.left}px`;
    panel.style.top = `${rect.bottom + 4}px`;
    panel.style.width = `${Math.max(rect.width, 220)}px`;
    try {
      ok(AFY.selects.isAnchoredTo(panel, trigger), "正下方的弹层应当被判为锚定在这个触发器上");
      const result = await AFY.selects.chooseSteps(trigger, ["有限公司", "有限公司"]);
      ok(result.ok, `本来就开着的自家弹层要能直接用，实际 reason=${result.reason}`);
      return `picked=${result.picked}`;
    } finally {
      panel.style.cssText = "display:none";
    }
  });

  test("弹层归属 · 锚定判据不能把别人的弹层认成自己的", async () => {
    // 几何判据放宽之后必须守住反面：另一个字段的弹层即使含有同样的文案，
    // 只要不在本触发器附近就绝不认——相邻两个区划下拉的候选高度重合。
    hideSyntheticPanels();
    const trigger = document.getElementById("t-selR-input");
    const other = document.getElementById("t-panelA"); // 里面有「上海市」，不是它的
    other.style.display = "block";
    other.style.position = "fixed";
    other.style.left = "10px";
    other.style.top = "10px";
    other.style.width = "200px";
    try {
      // 先确认这两者确实离得远（否则这条用例证明不了任何事）。
      const t = trigger.getBoundingClientRect();
      const o = other.getBoundingClientRect();
      const overlapX = Math.min(o.right, t.right) - Math.max(o.left, t.left);
      const gap = o.top >= t.bottom ? o.top - t.bottom : t.top - o.bottom;
      ok(overlapX <= 0 || gap > Math.max(48, t.height * 2), `fixture 必须真的离得远，实际 overlapX=${overlapX.toFixed(0)} gap=${gap.toFixed(0)}`);
      return eq(AFY.selects.isAnchoredTo(other, trigger), false, "远处的弹层不许被认成自己的");
    } finally {
      other.style.cssText = "display:none";
    }
  });

  test("面板 · 探查结果不能被接口嗅探状态盖掉", async () => {
    // 实测踩过：两者共用 view.probe，paint() 里 Object.assign(view, patch, { probe })
    // 每次都用嗅探状态覆盖探查结果 → 探查明明成功（诊断 JSON 里 ok:true），
    // 界面却永远打「探查失败：未知原因」。诊断工具自己变黑盒，最不能接受。
    // render 需要先 mount，且收起态不渲染卡片——两条都踩到才测得到东西。
    AFY.panel.mount(() => {}, { startCollapsed: false });
    AFY.panel.render({
      snapshot: null,
      plan: [],
      skipped: [],
      results: [],
      hints: [],
      // 常驻的嗅探状态与本次探查结果同时存在——这正是线上那一幕。
      probe: { blocked: 3, lastPath: "/dicname", lastMessage: "请登录" },
      probeResult: {
        ok: true,
        label: "组织形式",
        panelClass: "el-popover orgDescPopper",
        candidates: ["有限公司"],
        hints: [],
        outline: "label.el-radio",
      },
    });
    const text = document.getElementById(AFY.dom.PANEL_HOST_ID)?.shadowRoot?.textContent || "";
    ok(text.includes("弹层结构"), "应当渲染出探查结果卡片");
    ok(!text.includes("探查失败"), "成功的探查绝不能被显示成失败");
    return ok(text.includes("orgDescPopper"), "容器类名要如实显示出来");
  });

  test("manifest · debugger 必须声明在 permissions，不能放 optional_permissions", async () => {
    // 实测踩过：按「敏感权限就该用时再申请」的直觉把 debugger 放进
    // optional_permissions，结果 Chrome 在 permissions.request 时直接报
    // 「Only permissions specified in the manifest may be requested.」——
    // 它在「不允许作为可选权限」的名单上。表现是设置页那个开关**根本点不动**，
    // 而 manifest 本身能正常加载，不看这条用例就只能等用户来报错。
    // 只信真正读到的 manifest 文件。刻意**不**用 chrome.runtime.getManifest()——
    // 自检环境里那是 CHROME_STUB，等于自己证明自己。
    // 用 file:// 直接打开本页时 fetch 会被拦，那种情况如实说「没验到」，不假装通过。
    let manifest = null;
    try {
      manifest = await (await fetch("../../manifest.json")).json();
    } catch {
      manifest = null;
    }
    if (!manifest) {
      return "本环境读不到 manifest.json（多半是用 file:// 打开的），这条未验证；装进 Chrome 后重跑本页即可";
    }
    const required = manifest.permissions || [];
    const optional = manifest.optional_permissions || [];
    ok(required.includes("debugger"), "debugger 必须在 permissions 里");
    ok(!optional.includes("debugger"), "debugger 绝不能出现在 optional_permissions 里");
    // 顺带守住其余同类：这些也都不允许做可选权限。
    const NEVER_OPTIONAL = ["debugger", "declarativeNetRequest", "experimental", "mdns", "proxy"];
    const offenders = optional.filter((item) => NEVER_OPTIONAL.includes(item));
    return eq(offenders, [], "optional_permissions 里不许出现不可选的权限");
  });

  test("可信输入 · 重置必须能把降级状态清回去（release 不能抛）", async () => {
    // 这一条盯的是一个把「唯一恢复出口」堵死的错误：release() 曾引用一个不存在的
    // 变量，调用即抛 ReferenceError。而用户点掉调试横幅之后被记成永久否决，
    // 面板「重置」正是设计里唯一能恢复的路径——它一抛，通道就再也回不来了。
    const before = { mode: AFY.trusted.state.mode, reason: AFY.trusted.state.reason };
    AFY.trusted.state.mode = "unavailable";
    AFY.trusted.state.reason = "user_detached";
    await AFY.trusted.release(); // 不抛就算过；自检环境没有真扩展通道，消息发不出去也没关系
    const cleared = AFY.trusted.state.mode === "unknown";
    Object.assign(AFY.trusted.state, before);
    return ok(cleared, `重置后应当回到 unknown 以便重新探测，实际 ${AFY.trusted.state.mode}`);
  });

  test("弹层归属 · 空窗期里不能把别人的弹层按文案认领走", async () => {
    // 相邻两个区划下拉的候选高度重合。上一个字段留下一个关不掉的弹层、里面也有
    // 「上海市」时，如果在展开循环内就按文案认领，本控件的弹层还没渲染出来的那
    // 段空窗里就会认领到别人的弹层，于是**在别人的弹层上点选项**——本字段回读
    // 会发现没填上，而被误点的那个字段已经被悄悄改掉，回读体系覆盖不到这次误伤。
    //
    // 用下拉P（弹层延迟 200ms 才出现）才能真的撞上这道守卫：弹层立刻出现的控件
    // 根本不会经过空窗期，那样的用例是恒真的。
    hideSyntheticPanels();
    const stale = document.getElementById("t-panelA"); // 里面有「上海市」
    const own = document.getElementById("t-panelP");
    stale.style.display = "block"; // 冒充上一个字段没关掉的弹层
    try {
      const el = document.getElementById("t-selP-input");
      const result = await AFY.selects.choose(el, "上海市"); // 它自己的弹层里没有这个值
      eq(result.ok, false, "绝不能借用别人的弹层完成选择");
      eq(result.reason, "no_match", "应当在**自己**的弹层里找不到，而不是误点别人的");
      // 决定性证据：报出来的候选必须是它自己弹层里的那两个。
      eq(result.available, ["杭州市", "南京市"], "候选必须来自自己的弹层");
      // 它自己的弹层确实被打开过——否则这条用例只是碰巧没认领，等于什么都没验证。
      return eq(own.style.display, "block", "自己的弹层应当已经展开（说明确实走过了空窗期）");
    } finally {
      stale.style.display = "none";
      own.style.display = "none";
    }
  });

  // ------------------------------------------------- 剩余文件的解析检查
  //
  // 上面的用例只覆盖了引擎的 13 个文件。main.js / service-worker.js / net-probe.js /
  // popup.js / options.js 里只要有一个语法错，Chrome 会静默不装载那一部分——
  // 用户看到的是「面板不出现」或「弹窗一直转圈」，没有任何指向。这里给它们一个
  // chrome 空壳，逐个真加载一遍，语法错会以 window error 的形式被抓住。

  const CHROME_STUB = {
    runtime: {
      id: "selftest",
      getManifest: () => ({ version: "selftest" }),
      sendMessage: () => Promise.resolve({ ok: true, journalSize: 0, platform: {} }),
      onMessage: { addListener: () => {} },
      openOptionsPage: () => {},
    },
    storage: {
      local: { get: () => Promise.resolve({}), set: () => Promise.resolve() },
      // 否决记号落在 session 区（SW 回收后仍在，浏览器重启才清）。
      session: {
        get: () => Promise.resolve({}),
        set: () => Promise.resolve(),
        remove: () => Promise.resolve(),
      },
      onChanged: { addListener: () => {} },
    },
    tabs: {
      query: () => Promise.resolve([{ id: 1, url: "https://online.yct.sh.gov.cn/" }]),
      create: () => {},
      sendMessage: () => Promise.resolve({ ok: true }),
    },
    permissions: {
      contains: () => Promise.resolve(true),
      request: () => Promise.resolve(true),
      remove: () => Promise.resolve(true),
      onAdded: { addListener: () => {} },
    },
  };

  const EXTRA_FILES = [
    ["../main-world/net-probe.js", "MAIN world 接口嗅探"],
    ["../content/main.js", "引擎入口"],
    ["../background/service-worker.js", "后台 service worker"],
    ["popup.js", "扩展弹窗"],
    ["options.js", "设置页"],
  ];

  const errors = [];
  window.addEventListener("error", (event) => {
    errors.push(`${event.filename?.split("/").pop() || "?"}: ${event.message}`);
  });
  // 也要收 promise 里的失败：options.js 顶层的 load() 是 async，它抛错不会
  // 走 window.onerror，只看 error 事件会让它假通过。
  window.addEventListener("unhandledrejection", (event) => {
    errors.push(`未处理的 rejection: ${String(event.reason?.message || event.reason)}`);
  });

  const loadScript = (src) =>
    new Promise((resolve) => {
      const node = document.createElement("script");
      node.src = src;
      node.onload = () => resolve(true);
      node.onerror = () => resolve(false);
      document.body.append(node);
    });

  for (const [src, label] of EXTRA_FILES) {
    test(`解析检查 · ${label}`, async () => {
      const before = errors.length;
      const loaded = await loadScript(src);
      // 加载后让同步抛出的错误有机会冒到 window.onerror。
      await new Promise((resolve) => setTimeout(resolve, 60));
      ok(loaded, `${src} 取不到`);
      const fresh = errors.slice(before);
      if (fresh.length) throw new Error(fresh.join(" | "));
      return `${src} 解析并执行通过`;
    });
  }

  test("接口嗅探 · 打了补丁之后裸调用 fetch 仍然正常", async () => {
    // 这一条盯的是一个真实事故：把 originalFetch.apply(this, …) 写成不带兜底，
    // 站点代码里严格模式下的裸 fetch(url) 会拿到 this === undefined，
    // Chrome 直接抛 "Illegal invocation"——扩展一装上整个站点就打不开。
    ok(window.__afy_yct_probe__, "嗅探器应当已经装载");
    const bare = fetch; // 故意脱离 window 调用
    const response = await bare("data:text/plain,ok");
    return eq(await response.text(), "ok");
  });

  // ---------------------------------------------------------------- 运行

  const run = async () => {
    if (!AFY) {
      summary.className = "fail";
      summary.textContent = "引擎根本没有装载：globalThis.__AFY 不存在。多半是 namespace.js 报错了，看控制台。";
      return;
    }
    fixture.innerHTML = FORM_HTML;
    wireSyntheticSelects();

    // 解析检查阶段要用：给出 chrome 空壳，并把 booted 置真，
    // 这样 main.js 会在入口处直接返回，只验证它能被解析、不会真的挂上面板。
    //
    // 必须**合并**而不是「不存在才赋值」：普通网页上的 window.chrome 本来就存在
    // （是 {loadTimes, csi} 那个），只判存在会让空壳根本装不上，
    // 于是 service-worker.js 一读 chrome.runtime.onMessage 就报 undefined。
    globalThis.chrome = Object.assign({}, globalThis.chrome, CHROME_STUB);
    AFY.booted = true;

    let passed = 0;
    for (const item of cases) {
      const row = document.createElement("tr");
      let cell = "";
      let cls = "ok";
      try {
        // 单条超时。没有它的话，一个永不 resolve 的用例会让整页停在「运行中…」，
        // 连是哪一条挂住都看不出来——排查成本比这条用例本身高得多。
        const note = await Promise.race([
          item.fn(),
          new Promise((_, reject) => setTimeout(() => reject(new Error("用例超时（20s）")), 20000)),
        ]);
        cell = typeof note === "string" ? note : "通过";
        passed += 1;
      } catch (error) {
        cls = "bad";
        cell = String(error?.message || error);
      }
      row.innerHTML =
        `<td class="${cls}">${cls === "ok" ? "PASS" : "FAIL"}</td>` +
        `<td>${item.name}</td><td><code></code></td>`;
      row.querySelector("code").textContent = cell;
      rows.append(row);
    }

    summary.className = passed === cases.length ? "pass" : "fail";
    summary.textContent =
      passed === cases.length
        ? `全部通过：${passed}/${cases.length}。引擎这一层可以正常运行。`
        : `${passed}/${cases.length} 通过，${cases.length - passed} 项失败——先修掉再去业务页上跑。`;
  };

  void run();
})();
