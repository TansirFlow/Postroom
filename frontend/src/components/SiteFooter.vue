<script setup>
/**
 * 站点页脚：悬挂 ICP 备案号。
 *
 * 备案号由后端 .env 的 ICP_LICENSE 提供（默认空 → 不渲染任何东西），
 * 因此仓库里不会带任何人的备案信息，部署后由管理员自行配置。
 */
import { computed, onMounted } from 'vue'
import { loadSiteInfo, state } from '../api'

const icp = computed(() => state.site.icp)
const href = computed(() => state.site.icpUrl || 'https://beian.miit.gov.cn/')

onMounted(loadSiteInfo)
</script>

<template>
  <footer v-if="icp" class="site-footer">
    <a :href="href" target="_blank" rel="noopener noreferrer">{{ icp }}</a>
  </footer>
</template>
