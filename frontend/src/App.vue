<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authState, logout, roleLabel } from './session'
import ToastHost from './components/ToastHost.vue'

const route = useRoute()
const router = useRouter()

const isLogin = computed(() => route.name === 'login')
const isStandalone = computed(() => route.name === 'login' || route.name === 'public-map')
const user = authState.user

watch(
  () => authState.version.value,
  () => {
    if (!authState.isAuthenticated.value && !isStandalone.value) {
      router.replace({ name: 'login', query: { next: route.fullPath } })
    }
  },
)

function onLogout() {
  logout()
  router.replace({ name: 'login' })
}
</script>

<template>
  <div v-if="isStandalone">
    <div class="public-bar">
      <span class="brand-title" style="color: var(--ink)">年度物种调查 · 公开分布数据</span>
      <router-link v-if="route.name === 'public-map'" class="btn sm" to="/login">内部核查登录</router-link>
      <router-link v-else class="btn sm" to="/public-map">返回公开视图</router-link>
    </div>
    <router-view />
  </div>
  <div v-else class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true"></div>
        <div>
          <div class="brand-title">季节调查完整性<br />与覆盖数据服务</div>
          <div class="brand-sub">Coverage · Validity</div>
        </div>
      </div>

      <nav class="nav">
        <div class="nav-label">调查视图</div>
        <router-link to="/" :class="{ active: route.name === 'overview' }">
          <span class="nav-dot"></span>站点覆盖总览
        </router-link>
        <router-link to="/compare" :class="{ active: route.name === 'compare' }">
          <span class="nav-dot"></span>规则证书 vs 概率估计
        </router-link>
        <router-link to="/trends" :class="{ active: route.name === 'trends' }">
          <span class="nav-dot"></span>趋势输入清单
        </router-link>
        <router-link to="/trace" :class="{ active: route.name === 'trace' }">
          <span class="nav-dot"></span>单元回溯链
        </router-link>

        <div class="nav-label">空间发布</div>
        <router-link to="/spatial" :class="{ active: route.name === 'spatial' }">
          <span class="nav-dot"></span>空间泛化工作台
        </router-link>
        <router-link to="/spatial/compare" :class="{ active: route.name === 'spatial-compare' }">
          <span class="nav-dot"></span>共同范围比较
        </router-link>
        <router-link to="/public-map" :class="{ active: route.name === 'public-map' }">
          <span class="nav-dot"></span>公开分布视图
        </router-link>

        <div class="nav-label">证据与规则</div>
        <router-link to="/records" :class="{ active: route.name === 'records' }">
          <span class="nav-dot"></span>观测档案
        </router-link>
        <router-link to="/conflicts" :class="{ active: route.name === 'conflicts' }">
          <span class="nav-dot"></span>判读冲突
        </router-link>
        <router-link to="/rulebooks" :class="{ active: route.name === 'rulebooks' }">
          <span class="nav-dot"></span>样区规则册与模型
        </router-link>
      </nav>

      <div class="sidebar-foot">
        <div v-if="user">
          <div class="user-line">{{ user.display_name }} · {{ user.username }}</div>
          <div class="user-role">{{ roleLabel(user.role) }}</div>
        </div>
        <button class="btn sm" style="background: rgba(255,255,255,0.1); border-color: rgba(255,255,255,0.2); color: #e5efe9" @click="onLogout">
          退出登录
        </button>
        <div class="tiny" style="color: #7f978c; line-height: 1.4">
          仅交付覆盖、有效性与公开分布数据<br />不派发巡护任务 · 不预约人员器材
        </div>
      </div>
    </aside>

    <main class="main">
      <router-view :key="route.fullPath" />
    </main>
  </div>
  <ToastHost />
</template>
