const ENTRY =
  "https://online.yct.sh.gov.cn/web-yct-master/?web-name-apply=%2Fweb-name-apply%2F%23%2Fname-declaration#/wujieViews/A1";

const stateBox = document.getElementById("state");

const activeTab = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
};

const send = async (action) => {
  const tab = await activeTab();
  if (!tab?.url?.startsWith("https://online.yct.sh.gov.cn/")) {
    stateBox.textContent = "当前标签页不是一窗通业务页面。请先打开申报入口。";
    return;
  }
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "AFY_ACTION", action });
    window.close();
  } catch {
    stateBox.textContent = "页面上的面板还没就绪，刷新一次业务页面再试。";
  }
};

document.getElementById("observe").addEventListener("click", () => void send("observe"));
document.getElementById("fill").addEventListener("click", () => void send("fill"));
document.getElementById("open-entry").addEventListener("click", () => {
  void chrome.tabs.create({ url: ENTRY });
});
document.getElementById("options").addEventListener("click", () => chrome.runtime.openOptionsPage());

chrome.runtime
  .sendMessage({ type: "AFY_STATE" })
  .then((state) => {
    if (!state?.ok) {
      stateBox.textContent = "无法读取扩展状态。";
      return;
    }
    stateBox.textContent = [
      state.hasDossier ? "企业档案：已导入" : "企业档案：未导入（先去「档案与设置」）",
      `留痕条目：${state.journalSize}`,
      state.platform?.enabled ? `平台：${state.platform.token || "未配对"}` : "平台：未接入（本地模式）",
    ].join("\n");
  })
  // service worker 冷启动时 sendMessage 会 reject。不接住就是一条未处理的 rejection，
  // 而弹窗里会一直停在「读取状态中…」，看不出发生了什么。
  .catch((error) => {
    stateBox.textContent = `无法读取扩展状态：${String(error?.message || error)}`;
  });
