"""Case status constants and transition helpers."""

DRAFT = "draft"
READY_TO_SUBMIT = "ready_to_submit"
AWAITING_QR_LOGIN = "awaiting_qr_login"
LOGGING_IN = "logging_in"
LOGGED_IN = "logged_in"
NAME_CHECKING = "name_checking"
NAME_FAILED = "name_failed"
HUMAN_CONFIRMATION_REQUIRED = "human_confirmation_required"
NAME_NEXT_REACHED = "name_next_reached"
FILLING_ESTABLISHMENT = "filling_establishment"
CHOOSING_PROCESS = "choosing_process"
FILLING_ADDONS = "filling_addons"
READY_FOR_USER_SUBMIT = "ready_for_user_submit"
SIGNING = "signing"
AWAITING_ALIPAY_QR = "awaiting_alipay_qr"
AWAITING_SIGN_FACE_QR = "awaiting_sign_face_qr"
SIGN_DONE = "sign_done"
OFFLINE_REQUIRED = "offline_required"
FAILED = "failed"
NEED_HUMAN = "need_human"
CANCELLED = "cancelled"

TERMINAL = {
    READY_FOR_USER_SUBMIT,
    NAME_NEXT_REACHED,
    SIGN_DONE,
    OFFLINE_REQUIRED,
    FAILED,
    CANCELLED,
}

CUSTOMER_MESSAGES = {
    DRAFT: "请继续填写开办信息",
    READY_TO_SUBMIT: "信息已校验通过，可开始办理",
    AWAITING_QR_LOGIN: "请使用随申办扫描二维码登录一网通办",
    LOGGING_IN: "正在确认登录状态",
    LOGGED_IN: "已登录，开始办理",
    NAME_CHECKING: "正在进行企业名称核验",
    NAME_FAILED: "名称未通过，请修改字号或备选名称后重试",
    HUMAN_CONFIRMATION_REQUIRED: "页面需要本人确认，请在浏览器中完成后再继续",
    NAME_NEXT_REACHED: "名称申报信息已填写并进入下一步页面，请本人核对后继续办理",
    FILLING_ESTABLISHMENT: "正在填写企业设立信息",
    CHOOSING_PROCESS: "正在确认登记办理方式",
    FILLING_ADDONS: "正在填写开办配套信息",
    READY_FOR_USER_SUBMIT: "自动填报已完成（已进入进度跟踪页）。请继续完成电子签名",
    SIGNING: "正在办理电子签名…",
    AWAITING_ALIPAY_QR: "请使用支付宝扫描二维码完成签名登录",
    AWAITING_SIGN_FACE_QR: "请扫码完成人脸认证以签署材料",
    SIGN_DONE: "电子签名已完成，可前往一网通办查看进度",
    OFFLINE_REQUIRED: "当前住所不适配全程网办，请查看说明或更换住所后重试",
    NEED_HUMAN: "办理遇到需人工确认的页面，请联系协助并查看截图",
    FAILED: "办理失败",
    CANCELLED: "已取消",
}
