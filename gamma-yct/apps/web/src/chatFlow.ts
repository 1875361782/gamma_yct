/** Guided dialogue steps that fill CasePayload (no 随申办 password). */

export type CollectSlot =
  | 'companyType'
  | 'tradeName'
  | 'industry'
  | 'district'
  | 'addressDetail'
  | 'centralized'
  | 'capital'
  | 'personName'
  | 'personId'
  | 'personMobile'
  | 'businessScope'
  | 'uploadIdFront'
  | 'uploadIdBack'
  | 'uploadAddress'
  | 'askVerify'

export const SLOT_ORDER: CollectSlot[] = [
  'companyType',
  'tradeName',
  'industry',
  'district',
  'addressDetail',
  'centralized',
  'capital',
  'personName',
  'personId',
  'personMobile',
  'businessScope',
  'uploadIdFront',
  'uploadIdBack',
  'uploadAddress',
  'askVerify',
]

export function promptFor(slot: CollectSlot, isCentralized: boolean): string {
  const map: Record<CollectSlot, string> = {
    companyType: '请先选择您的公司注册类型：',
    tradeName: '请输入企业字号（例如：云图）：',
    industry: '请输入行业特点用语（例如：科技、网络科技）：',
    district: '请输入住所所在区（例如：浦东新区）：',
    addressDetail: '请输入详细住所地址：',
    centralized: '住所是否为集中登记地？（回复「是」或「否」）',
    capital: '请输入注册资本（万元，数字）：',
    personName: '请输入法定代表人 / 股东姓名：',
    personId: '请输入身份证号码：',
    personMobile: '请输入手机号码：',
    businessScope: '请输入经营范围：',
    uploadIdFront: '请点击下方「+」上传身份证正面图片（jpg/jpeg）：',
    uploadIdBack: '请继续上传身份证反面图片：',
    uploadAddress: '非集中登记地请提供邮政编码；若有不动产权证书收件编号也可一并填写（官方一般不要求上传住所证明扫描件）：',
    askVerify: '恭喜您完成信息的上传，系统将对您上传的信息进行核验，是否立即核验！（回复「是」开始）',
  }
  if (slot === 'uploadAddress' && isCentralized) {
    return ''
  }
  return map[slot]
}

export function nextSlot(current: CollectSlot, isCentralized: boolean): CollectSlot | null {
  const i = SLOT_ORDER.indexOf(current)
  for (let j = i + 1; j < SLOT_ORDER.length; j++) {
    const s = SLOT_ORDER[j]
    if (s === 'uploadAddress' && isCentralized) continue
    return s
  }
  return null
}

export type ChatMsg =
  | { id: string; role: 'bot' | 'user'; kind: 'text'; text: string }
  | { id: string; role: 'bot'; kind: 'companyType' }
  | { id: string; role: 'bot'; kind: 'processing' }
  | {
      id: string
      role: 'bot'
      kind: 'verifyOk'
      companyTypeLabel: string
      mobile: string
      summary: string
    }
  | { id: string; role: 'bot'; kind: 'verifyFail'; errors: string[] }
  | { id: string; role: 'user'; kind: 'file'; name: string; sizeLabel: string; ext: string }

let _seq = 0
export function mid() {
  _seq += 1
  return `m${Date.now()}_${_seq}`
}
