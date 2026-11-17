<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login } from '../session'

const route = useRoute()
const router = useRouter()
const username = ref('yq.li')
const password = ref('Survey#2026')
const loading = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  loading.value = true
  try {
    await login(username.value.trim(), password.value)
    const next = typeof route.query.next === 'string' ? route.query.next : '/'
    router.replace(next)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '登录失败'
  } finally {
    loading.value = false
  }
}

function useAccount(u: string, p: string) {
  username.value = u
  password.value = p
}
</script>

<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="login-head">
        <div class="brand-mark" aria-hidden="true"></div>
        <div>
          <div class="eyebrow">Reserve Survey Integrity</div>
          <h1>季节调查完整性与覆盖数据服务</h1>
          <p class="page-sub">
            按站点—样区—日期组装调查单元，签发有效调查 / 受限调查 / 未有效调查 / 待核证书，
            并并列展示规则证书与经历史校准的检测概率估计。
          </p>
        </div>
      </div>

      <form class="stack" @submit.prevent="submit">
        <div class="field">
          <label for="username">账号</label>
          <input id="username" v-model="username" class="input" autocomplete="username" />
        </div>
        <div class="field">
          <label for="password">密码</label>
          <input
            id="password"
            v-model="password"
            type="password"
            class="input"
            autocomplete="current-password"
          />
        </div>
        <div v-if="error" class="callout danger">{{ error }}</div>
        <button class="btn primary" type="submit" :disabled="loading">
          {{ loading ? '登录中…' : '登录' }}
        </button>
      </form>

      <div class="divider"></div>
      <div class="stack-sm">
        <div class="tiny muted">演示账号（种子数据）</div>
        <div class="account-row">
          <button class="btn sm" @click="useAccount('yq.li', 'Survey#2026')">
            研究人员 yq.li
          </button>
          <button class="btn sm" @click="useAccount('m.cao', 'Audit#2026')">
            审核员 m.cao
          </button>
          <button class="btn sm" @click="useAccount('l.lin', 'View#2026')">
            只读访客 l.lin
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    radial-gradient(900px 420px at 15% 10%, #e4ece5, transparent 70%),
    radial-gradient(700px 420px at 88% 88%, #ece7d8, transparent 70%),
    var(--bg);
}
.login-card {
  width: 100%;
  max-width: 470px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 14px;
  box-shadow: 0 24px 60px -32px rgba(27, 38, 33, 0.5);
  padding: 26px 26px 22px;
}
.login-head { display: flex; gap: 14px; margin-bottom: 20px; }
.login-head .brand-mark { width: 38px; height: 38px; border-radius: 10px; }
.login-head h1 { font-size: 19px; line-height: 1.3; margin-top: 2px; }
.account-row { display: flex; gap: 6px; flex-wrap: wrap; }
</style>
