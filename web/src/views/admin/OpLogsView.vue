<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Search } from '@lucide/vue'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingState from '@/components/common/LoadingState.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PaginationBar from '@/components/common/PaginationBar.vue'
import { fetchOperationLogs } from '@/api'
import { formatDate } from '@/lib/format'
import type { OperationLog, PageResult } from '@/types/api'

const loading = ref(true)
const logs = ref<PageResult<OperationLog>>({ list: [], total: 0, page: 1, page_size: 20 })
const keyword = ref('')
const failedOnly = ref(false)

async function load() {
  loading.value = true
  try {
    logs.value = await fetchOperationLogs({
      page: logs.value.page,
      page_size: logs.value.page_size,
      keyword: keyword.value || undefined,
      failed_only: failedOnly.value ? 1 : undefined,
    })
  } finally {
    loading.value = false
  }
}

function search() {
  logs.value.page = 1
  load()
}

onMounted(load)
</script>

<template>
  <div>
    <PageHeader title="操作记录" description="后台里每一次新建、修改、删除、登录都会记在这里：谁、什么时候、做了什么、成没成功" />

    <div class="mb-4 flex flex-wrap items-center gap-2">
      <div class="relative w-full max-w-xs">
        <Search class="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input v-model="keyword" class="pl-9" placeholder="搜索操作、对象、账号或 IP" @keyup.enter="search" />
      </div>
      <Button variant="secondary" @click="search">搜索</Button>
      <label class="ml-1 flex items-center gap-2 text-sm text-muted-foreground">
        <input v-model="failedOnly" type="checkbox" class="size-4 accent-foreground" @change="search" />
        只看失败的
      </label>
    </div>

    <LoadingState v-if="loading" variant="table" :rows="8" />

    <div v-else-if="logs.list.length === 0" class="rounded-2xl border bg-card">
      <EmptyState title="暂无记录" description="后台的改动会自动记录在这里" />
    </div>

    <template v-else>
      <div class="overflow-x-auto rounded-2xl border bg-card shadow-sm">
        <Table class="min-w-[720px]">
          <TableHeader>
            <TableRow class="hover:bg-transparent">
              <TableHead class="w-40">时间</TableHead>
              <TableHead class="w-24">账号</TableHead>
              <TableHead>操作</TableHead>
              <TableHead>对象</TableHead>
              <TableHead class="w-28">结果</TableHead>
              <TableHead class="hidden w-32 lg:table-cell">IP</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow v-for="l in logs.list" :key="l.id">
              <TableCell class="whitespace-nowrap text-xs text-muted-foreground">{{ formatDate(l.created_at, true) }}</TableCell>
              <TableCell class="text-sm">{{ l.username || '—' }}</TableCell>
              <TableCell class="text-sm font-medium">{{ l.action }}</TableCell>
              <TableCell class="max-w-72 truncate text-sm text-muted-foreground" :title="l.summary">{{ l.summary || '—' }}</TableCell>
              <TableCell class="text-xs">
                <span v-if="l.success" class="text-muted-foreground">成功</span>
                <span v-else class="font-medium text-destructive" :title="l.message">失败{{ l.message ? '：' + l.message : '' }}</span>
              </TableCell>
              <TableCell class="hidden text-xs text-muted-foreground lg:table-cell">{{ l.ip }}</TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
      <PaginationBar
        v-model:page="logs.page"
        :total="logs.total"
        :page-size="logs.page_size"
        class="mt-4"
        @update:page="load"
      />
    </template>
  </div>
</template>
