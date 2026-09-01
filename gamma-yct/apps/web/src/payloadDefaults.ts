export function emptyPayload() {
  return {
    schemaVersion: '1.0',
    enterpriseType: 'limited_company',
    preApproval: false,
    name: {
      adminDivision: '上海',
      tradeName: '',
      industry: '',
      orgForm: '有限公司',
      fullNamePreview: '',
      alternatives: [
        { tradeName: '', industry: '', orgForm: '有限公司' },
        { tradeName: '', industry: '', orgForm: '有限公司' },
      ],
    },
    registration: {
      district: '',
      addressType: 'lease' as 'centralized' | 'lease' | 'self_owned' | 'other',
      isCentralizedVenue: false,
      address: {
        province: '上海市',
        city: '上海市',
        district: '',
        street: '',
        road: '',
        lane: '',
        number: '',
        numberEnd: '',
        oddEvenMode: '不连号',
        building: '',
        room: '',
        detail: '',
        propertyCertNo: '',
        postalCode: '',
      },
      authority: '',
      acceptPoint: '',
      preferredProcess: 'full_electronic',
      addressProofFileIds: [] as string[],
    },
    company: {
      registeredCapital: 100,
      capitalCurrency: 'CNY',
      capitalUnit: '万元',
      businessTermType: 'long_term' as const,
      businessTermYears: null as number | null,
      businessScope: '',
      businessScopeCodes: [] as string[],
    },
    investors: [
      {
        type: 'natural_person' as const,
        name: '',
        idType: 'id_card',
        idNumber: '',
        mobile: '',
        amount: 100,
        ratio: 100,
        investmentMethod: '货币',
        address: '',
        ethnicity: '',
      },
    ],
    personnel: {
      legalRepresentative: {
        name: '',
        idType: 'id_card',
        idNumber: '',
        mobile: '',
        fromInvestorIndex: 0,
      },
      directors: [] as unknown[],
      supervisors: [] as unknown[],
      establishSupervisor: false,
      manager: null,
      financeOfficer: null,
      liaisonOfficer: null as null | {
        name: string
        idType: string
        idNumber: string
        mobile: string
        email: string
        address: string
      },
    },
    beneficialOwner: {
      name: '',
      mobile: '',
      idValidUntil: '',
      ownershipRatio: 0,
      address: '',
    },
    seals: {
      needPhysical: true,
      types: ['公章', '财务章', '法人章'],
      deliveryInfo: {},
    },
    tax: { needInvoice: true, invoiceTypes: [] as string[] },
    socialInsurance: { needEmployeeInsurance: false },
    housingFund: { need: false },
    bankAppointment: { need: false, bankName: null as string | null, branchName: null as string | null },
    attachments: {
      idCards: [{ personKey: 'legalRepresentative', frontFileId: '', backFileId: '' }],
      addressProof: [] as string[],
      other: [] as string[],
    },
  }
}

export type Payload = ReturnType<typeof emptyPayload>

export const STEPS = [
  '主体与路径',
  '企业名称',
  '住所与登记机关',
  '出资与股东',
  '人员任职',
  '经营范围',
  '开办配套',
  '证件上传',
  '确认提交',
]
