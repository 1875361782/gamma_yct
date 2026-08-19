<script setup lang="ts">
import { computed, nextTick, onUnmounted, reactive, ref } from 'vue'
import {
  cancelCase,
  createCase,
  getCase,
  patchPayload,
  startCase,
  uploadFile,
  validateCase,
  type CaseRecord,
} from './api'
import {
  mid,
  nextSlot,
  promptFor,
  type ChatMsg,
  type CollectSlot,
} from './chatFlow'
import { emptyPayload, type Payload } from './payloadDefaults'

type View = 'home' | 'chat' | 'progress' | 'done'

const view = ref<View>('home')
const input = ref('')
const plusOpen = ref(false)
const caseId = ref('')
const payload = reactive<Payload>(emptyPayload())
const messages = ref<ChatMsg[]>([])
const slot = ref<CollectSlot>('companyType')
const companyTypeLabel = ref('有限责任公司')
const caseRec = ref<CaseRecord | null>(null)
const chatEl = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const uploadTarget = ref<'idFront' | 'idBack' | 'address'>('idFront')
let pollTimer: number | undefined

const PROGRESS_STEPS = [
  { key: 'name', label: '企业名称核验' },
  { key: 'basic', label: '基础信息填报' },
  { key: 'shareholder', label: '股东信息填报' },
  { key: 'officer', label: '高管信息填报' },
  { key: 'beneficiary', label: '受益人信息填报' },
  { key: 'material', label: '材料上传' },
  { key: 'submit', label: '文本生成与暂存' },
]

const progressPct = computed(() => {
  const s = caseRec.value?.status || ''
  const map: Record<string, number> = {
    awaiting_qr_login: 12,
    logging_in: 18,
    logged_in: 25,
    name_checking: 40,
    filling_establishment: 55,
    choosing_process: 68,
    filling_addons: 80,
    ready_for_user_submit: 100,
    need_human: 70,
    failed: 70,
  }
  return map[s] ?? 8
})

const doneCount = computed(() => {
  const s = caseRec.value?.status || ''
  if (s === 'ready_for_user_submit') return 7
  if (s === 'filling_addons' || s === 'choosing_process') return 4
  if (s === 'filling_establishment') return 3
  if (s === 'name_checking' || s === 'name_failed') return 1
  if (s === 'logged_in' || s === 'awaiting_qr_login') return 0
  return 0
})

const qrUrl = computed(() => {
  const base = caseRec.value?.artifacts?.qrImageUrl
  if (!base) return ''
  return `${base}?t=${caseRec.value?.updatedAt || Date.now()}`
})

function scrollChat() {
  nextTick(() => {
    if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight
  })
}

function pushBot(text: string) {
  messages.value.push({ id: mid(), role: 'bot', kind: 'text', text })
  scrollChat()
}

function pushUser(text: string) {
  messages.value.push({ id: mid(), role: 'user', kind: 'text', text })
  scrollChat()
}

async function ensureCase() {
  if (caseId.value) return
  const c = await createCase()
  caseId.value = c.caseId
}

async function persist(step = 1) {
  await ensureCase()
  payload.name.fullNamePreview =
    payload.name.fullNamePreview ||
    `上海${payload.name.tradeName}${payload.name.industry}${payload.name.orgForm}`
  payload.registration.address.district =
    payload.registration.address.district || payload.registration.district
  await patchPayload(caseId.value, payload as unknown as Record<string, unknown>, step)
}

function askSlot(s: CollectSlot) {
  slot.value = s
  if (s === 'companyType') {
    messages.value.push({ id: mid(), role: 'bot', kind: 'companyType' })
    scrollChat()
    return
  }
  const text = promptFor(s, payload.registration.isCentralizedVenue)
  if (text) pushBot(text)
}

function startRegister() {
  view.value = 'chat'
  messages.value = []
  Object.assign(payload, emptyPayload())
  caseId.value = ''
  companyTypeLabel.value = '有限责任公司'
  pushBot(
    'Hi，我是伽马智能助手，很高兴为您服务！接下来将由我协助您完成上海企业开办信息收集。\n\n登录一网通办时，请在后续进度页使用随申办扫码（无需在此填写账号密码）。\n\n我们先开始填写注册信息。',
  )
  askSlot('companyType')
}

function introOnly() {
  view.value = 'chat'
  messages.value = []
  pushBot(
    '我是伽马智能助手，可以协助您：\n1. 收集上海企业开办所需信息\n2. 核验资料完整性\n3. 在官方页面自动填表并暂存\n4. 指引您本人扫码登录、提交与电子签名\n\n点击首页「我想进行公司注册」即可开始。',
  )
}

function contactCs() {
  view.value = 'chat'
  messages.value = []
  pushBot('如需人工协助，请联系运营同学处理 need_human 办件，或通过贵司内部客服渠道沟通。')
}

function taxHelp() {
  view.value = 'chat'
  messages.value = []
  pushBot(
    '公司报税需在完成设立并取得税号后办理。本助手一期聚焦开办信息填报与暂存；税务发票申领可在开办配套中勾选，具体申报请在电子税务局完成。',
  )
}

const companyTypePick = ref<'one' | 'llc'>('llc')

function onConfirmCompanyType() {
  const choice = companyTypePick.value
  companyTypeLabel.value = choice === 'one' ? '一人有限公司' : '有限责任公司'
  payload.name.orgForm = '有限公司'
  pushUser(`请选择您的公司注册类型：${companyTypeLabel.value}`)
  persist(1).then(() => askSlot('tradeName'))
}

function onCancelCompanyType() {
  pushBot('已取消类型选择。您可以随时重新选择，或返回首页。')
}

async function handleText(raw: string) {
  const text = raw.trim()
  if (!text) return
  input.value = ''
  pushUser(text)

  const s = slot.value
  try {
    if (s === 'askVerify') {
      if (/^(是|确认|好|立即核验|核验)/.test(text)) {
        await runVerify()
      } else {
        pushBot('如需开始核验，请回复「是」。')
      }
      return
    }

    if (s === 'tradeName') payload.name.tradeName = text
    else if (s === 'industry') payload.name.industry = text
    else if (s === 'district') {
      payload.registration.district = text
      payload.registration.address.district = text
    } else if (s === 'addressDetail') payload.registration.address.detail = text
    else if (s === 'centralized') {
      const yes = /^(是|对|集中)/.test(text)
      payload.registration.isCentralizedVenue = yes
      payload.registration.addressType = yes ? 'centralized' : 'lease'
    } else if (s === 'capital') {
      const n = Number(text.replace(/[^\d.]/g, ''))
      if (!n) {
        pushBot('请输入有效数字，例如 100')
        return
      }
      payload.company.registeredCapital = n
      payload.investors[0].amount = n
      payload.investors[0].ratio = 100
    } else if (s === 'personName') {
      payload.investors[0].name = text
      payload.personnel.legalRepresentative.name = text
    } else if (s === 'personId') {
      payload.investors[0].idNumber = text
      payload.personnel.legalRepresentative.idNumber = text
    } else if (s === 'personMobile') {
      payload.investors[0].mobile = text
      payload.personnel.legalRepresentative.mobile = text
    } else if (s === 'businessScope') {
      payload.company.businessScope = text
    } else if (s === 'uploadIdFront' || s === 'uploadIdBack' || s === 'uploadAddress') {
      pushBot('请通过底部「+」选择图片上传。')
      return
    } else if (s === 'companyType') {
      pushBot('请在上方卡片中选择类型并点确认。')
      return
    }

    await persist(2)
    const n = nextSlot(s, payload.registration.isCentralizedVenue)
    if (n) askSlot(n)
  } catch (e) {
    pushBot(`保存失败：${e}`)
  }
}

async function runVerify() {
  messages.value.push({ id: mid(), role: 'bot', kind: 'processing' })
  scrollChat()
  try {
    await persist(9)
    const v = await validateCase(caseId.value)
    // remove processing bubble
    messages.value = messages.value.filter((m) => m.kind !== 'processing')
    if (!v.ok) {
      messages.value.push({
        id: mid(),
        role: 'bot',
        kind: 'verifyFail',
        errors: v.errors,
      })
      slot.value = 'askVerify'
      scrollChat()
      return
    }
    messages.value.push({
      id: mid(),
      role: 'bot',
      kind: 'verifyOk',
      companyTypeLabel: companyTypeLabel.value,
      mobile: payload.personnel.legalRepresentative.mobile,
      summary: payload.name.fullNamePreview || `上海${payload.name.tradeName}${payload.name.industry}有限公司`,
    })
    scrollChat()
  } catch (e) {
    messages.value = messages.value.filter((m) => m.kind !== 'processing')
    pushBot(`核验异常：${e}`)
  }
}

async function onVerifyConfirm() {
  pushUser('确认')
  try {
    await startCase(caseId.value)
    view.value = 'progress'
    caseRec.value = await getCase(caseId.value)
    startPoll()
  } catch (e) {
    pushBot(`启动办理失败：${e}`)
  }
}

function onVerifyCancel() {
  pushUser('取消')
  pushBot('收到！本次任务已终止，是否立即重新开始企业注册？（回复「是」或返回首页）')
  slot.value = 'askVerify'
  // special: if they say 是 we could restart — handle in handleText by checking last bot
  messages.value.push({
    id: mid(),
    role: 'bot',
    kind: 'text',
    text: '回复「重新开始」将清空并回到类型选择。',
  })
}

async function handleSend() {
  const t = input.value.trim()
  if (t === '重新开始') {
    input.value = ''
    startRegister()
    return
  }
  await handleText(t)
}

function openPlus() {
  plusOpen.value = !plusOpen.value
}

function pickUpload(kind: 'idFront' | 'idBack' | 'address') {
  uploadTarget.value = kind
  plusOpen.value = false
  fileInput.value?.click()
}

async function onFilePicked(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0]
  ;(ev.target as HTMLInputElement).value = ''
  if (!file) return
  try {
    await ensureCase()
    const { fileId } = await uploadFile(caseId.value, file)
    const sizeLabel =
      file.size > 1024 * 1024
        ? `${(file.size / 1024 / 1024).toFixed(2)}mb`
        : `${Math.max(1, Math.round(file.size / 1024))}kb`
    const ext = (file.name.split('.').pop() || 'IMG').toUpperCase()

    if (uploadTarget.value === 'idFront') {
      payload.attachments.idCards[0].frontFileId = fileId
      messages.value.push({ id: mid(), role: 'user', kind: 'file', name: file.name, sizeLabel, ext })
      await persist(8)
      askSlot('uploadIdBack')
    } else if (uploadTarget.value === 'idBack') {
      payload.attachments.idCards[0].backFileId = fileId
      messages.value.push({ id: mid(), role: 'user', kind: 'file', name: file.name, sizeLabel, ext })
      await persist(8)
      const n = nextSlot('uploadIdBack', payload.registration.isCentralizedVenue)
      if (n) askSlot(n)
    } else {
      payload.attachments.addressProof = [fileId]
      payload.registration.addressProofFileIds = [fileId]
      messages.value.push({ id: mid(), role: 'user', kind: 'file', name: file.name, sizeLabel, ext })
      await persist(8)
      askSlot('askVerify')
    }
    scrollChat()
  } catch (e) {
    pushBot(`上传失败：${e}`)
  }
}

function startPoll() {
  stopPoll()
  pollTimer = window.setInterval(async () => {
    if (!caseId.value) return
    try {
      caseRec.value = await getCase(caseId.value)
      const s = caseRec.value.status
      if (s === 'ready_for_user_submit') {
        stopPoll()
        view.value = 'done'
      }
      if (s === 'failed' || s === 'cancelled' || s === 'offline_required') stopPoll()
      if (s === 'name_failed') {
        stopPoll()
        view.value = 'chat'
        pushBot(caseRec.value.customerMessage + ' 请重新输入字号。')
        askSlot('tradeName')
      }
    } catch {
      /* ignore */
    }
  }, 2500)
}

function stopPoll() {
  if (pollTimer) window.clearInterval(pollTimer)
  pollTimer = undefined
}

async function goBack() {
  if (view.value === 'home') return
  if (view.value === 'progress') {
    await cancelCase(caseId.value).catch(() => undefined)
    stopPoll()
  }
  if (view.value === 'done' || view.value === 'progress' || view.value === 'chat') {
    view.value = 'home'
    plusOpen.value = false
  }
}

function goHome() {
  stopPoll()
  view.value = 'home'
  plusOpen.value = false
}

onUnmounted(stopPoll)
</script>

<template>
  <div class="layout">
    <aside class="sidebar" aria-label="侧栏">
      <button class="side-btn brand" type="button" title="首页" @click="goHome">γ</button>
      <button class="side-btn" type="button" title="新对话" @click="startRegister">＋</button>
      <button class="side-btn" type="button" title="返回" @click="goBack">‹</button>
      <div class="side-spacer" />
      <button class="side-btn" type="button" title="更多">···</button>
    </aside>

    <div class="main">
      <header class="main-top">
        <h1>伽马注册</h1>
      </header>

      <!-- HOME -->
      <div v-if="view === 'home'" class="main-scroll">
        <div class="stage">
          <div class="home-hero">
            <div class="robot" aria-hidden="true">🤖</div>
            <h2>伽马智能助手</h2>
            <p>开始与AI助手对话，完成您的需求</p>
          </div>
          <div class="suggest-list">
            <button type="button" @click="introOnly">你好，请介绍一下你的功能</button>
            <button type="button" @click="startRegister">我想进行公司注册</button>
            <button type="button" @click="contactCs">如何联系人工客服？</button>
            <button type="button" @click="taxHelp">帮我了解公司报税的流程</button>
          </div>
        </div>
      </div>

      <!-- CHAT -->
      <div v-else-if="view === 'chat'" ref="chatEl" class="main-scroll">
        <div class="stage chat-stage">
          <template v-for="m in messages" :key="m.id">
            <div v-if="m.role === 'bot'" class="bot-row">
              <div class="bot-avatar">··</div>
              <div>
                <div class="bot-meta">伽马智能助手</div>

                <div v-if="m.kind === 'text'" class="bubble-bot">{{ m.text }}</div>

                <div v-else-if="m.kind === 'companyType'" class="bubble-bot">
                  请先选择您的公司注册类型：
                  <div class="widget-card">
                    <div class="radio-list">
                      <label><input v-model="companyTypePick" name="ctype" type="radio" value="one" /> 一人有限公司</label>
                      <label><input v-model="companyTypePick" name="ctype" type="radio" value="llc" /> 有限责任公司</label>
                    </div>
                    <div class="widget-actions">
                      <button type="button" class="cancel" @click="onCancelCompanyType">取消</button>
                      <button type="button" class="confirm" @click="onConfirmCompanyType">确认</button>
                    </div>
                  </div>
                </div>

                <div v-else-if="m.kind === 'processing'">
                  <div class="bubble-bot">正在为您核验信息，请稍等片刻。</div>
                  <div class="processing"><i class="spinner" /> 处理中...</div>
                </div>

                <div v-else-if="m.kind === 'verifyOk'" class="verify-card ok">
                  <div class="head">✓ 已完成核验</div>
                  <div class="body">
                    您的信息已核验完成，请确认是否有误，如若无误则可点击「确认」立即开始企业注册！
                    <p><span class="label">您的公司注册类型：</span>{{ m.companyTypeLabel }}</p>
                    <p><span class="label">拟登记名称：</span>{{ m.summary }}</p>
                    <p><span class="label">您的手机号：</span>{{ m.mobile }}</p>
                    <div class="widget-actions">
                      <button type="button" class="cancel" @click="onVerifyCancel">取消</button>
                      <button type="button" class="confirm" @click="onVerifyConfirm">确认</button>
                    </div>
                  </div>
                </div>

                <div v-else-if="m.kind === 'verifyFail'" class="verify-card fail">
                  <div class="head">! 核验失败</div>
                  <div class="body">
                    <p>请根据以下问题修改后，回复「是」重新核验：</p>
                    <ul>
                      <li v-for="e in m.errors" :key="e">{{ e }}</li>
                    </ul>
                  </div>
                </div>
              </div>
            </div>

            <div v-else-if="m.kind === 'text'" class="bubble-user">{{ m.text }}</div>
            <div v-else-if="m.kind === 'file'" class="bubble-user file-bubble">
              <div class="file-chip">
                <span class="icon">{{ m.ext.slice(0, 3) }}</span>
                <span>
                  <div>{{ m.name }}</div>
                  <div class="file-meta">{{ m.ext }} {{ m.sizeLabel }}</div>
                </span>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- PROGRESS -->
      <div v-else-if="view === 'progress'" class="main-scroll">
        <div class="stage progress-page">
          <h2>企业注册中</h2>
          <p class="desc">系统正在为您自动化填报并暂存，请耐心等待！最终提交与签名需您本人完成。</p>
          <div class="bar"><i :style="{ width: progressPct + '%' }" /></div>
          <div class="pct">{{ progressPct }}%</div>

          <div v-if="caseRec?.status === 'awaiting_qr_login' || caseRec?.status === 'logging_in'" class="qr-box">
            <p>请使用随申办扫描二维码登录</p>
            <img v-if="qrUrl" :src="qrUrl" alt="登录二维码" />
            <p v-else>二维码生成中…</p>
          </div>

          <p v-if="caseRec" class="status-line">{{ caseRec.customerMessage }}</p>
          <p v-if="caseRec?.reason" class="err-line">{{ caseRec.reason }}</p>

          <ul class="step-list">
            <li v-for="(st, i) in PROGRESS_STEPS" :key="st.key" :class="i < doneCount ? 'done' : 'todo'">
              <span class="mark">{{ i < doneCount ? '✓' : '' }}</span>
              {{ st.label }}
            </li>
          </ul>
        </div>
      </div>

      <!-- DONE -->
      <div v-else class="main-scroll">
        <div class="stage done-page">
          <div class="stars">★ ★ ★ ★ ★</div>
          <div class="shield">✓</div>
          <h2>恭喜</h2>
          <p>
            开办信息已填入并暂存完成。请您本人登录
            <a href="https://yct.sh.gov.cn/portal_yct/" target="_blank" rel="noopener">上海企业登记在线</a>
            核对后提交，并完成电子签名；结果可后续在一网通办查询。
          </p>
          <p v-if="caseRec?.result?.approvedName" class="name-line">
            名称：{{ caseRec.result.approvedName }}
          </p>
          <button type="button" class="primary-btn" @click="goHome">返回主页</button>
        </div>
      </div>

      <!-- floating composer (DeepSeek-style) -->
      <div v-if="view === 'home' || view === 'chat'" class="composer-dock">
        <div class="composer">
          <button class="icon-btn" type="button" aria-label="语音">🎤</button>
          <input
            v-model="input"
            type="text"
            placeholder="有什么问题尽管问我吧"
            @keydown.enter.prevent="handleSend"
          />
          <button class="icon-btn" type="button" aria-label="更多" @click="openPlus">＋</button>
          <button class="send" type="button" :disabled="!input.trim()" @click="handleSend">✈</button>
        </div>
        <div v-if="plusOpen && view === 'chat'" class="plus-panel">
          <h3>
            选择您要上传的文件
            <button class="icon-btn" type="button" @click="plusOpen = false">✕</button>
          </h3>
          <div class="plus-grid">
            <button type="button" @click="pickUpload(slot === 'uploadIdBack' ? 'idBack' : slot === 'uploadAddress' ? 'address' : 'idFront')">
              <span class="tile" style="background: #3b82f6">IMG</span>
              图片
            </button>
            <button type="button" disabled>
              <span class="tile" style="background: #a855f7">VID</span>
              视频
            </button>
            <button type="button" disabled>
              <span class="tile" style="background: #2563eb">W</span>
              WORD
            </button>
            <button type="button" disabled>
              <span class="tile" style="background: #16a34a">X</span>
              表格
            </button>
            <button type="button" disabled>
              <span class="tile" style="background: #ea580c">P</span>
              PPT
            </button>
            <button type="button" @click="pickUpload(slot === 'uploadAddress' ? 'address' : 'idFront')">
              <span class="tile" style="background: #dc2626">PDF</span>
              PDF
            </button>
            <button type="button" disabled>
              <span class="tile" style="background: #0ea5e9">T</span>
              TXT
            </button>
            <button type="button" disabled>
              <span class="tile" style="background: #2563eb">🔗</span>
              链接
            </button>
          </div>
          <p class="plus-hint">一期证件请上传 jpg/jpeg 图片（与官方要求一致）。</p>
        </div>
      </div>
    </div>

    <input ref="fileInput" type="file" accept=".jpg,.jpeg,image/jpeg,.pdf" hidden @change="onFilePicked" />
  </div>
</template>
