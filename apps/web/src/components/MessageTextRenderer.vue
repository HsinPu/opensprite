<template>
  <div class="message__rendered">
    <template v-for="block in blocks" :key="block.id">
      <h2 v-if="block.type === 'heading'" class="message__heading" :data-level="block.level">
        <template v-for="segment in block.segments" :key="segment.id">
          <code v-if="segment.type === 'code'">{{ segment.text }}</code>
          <a v-else-if="segment.type === 'link'" :href="segment.href" target="_blank" rel="noreferrer">{{ segment.text }}</a>
          <strong v-else-if="segment.type === 'strong'">{{ segment.text }}</strong>
          <span v-else>{{ segment.text }}</span>
        </template>
      </h2>

      <p v-else-if="block.type === 'paragraph'" class="message__paragraph">
        <template v-for="segment in block.segments" :key="segment.id">
          <code v-if="segment.type === 'code'">{{ segment.text }}</code>
          <a v-else-if="segment.type === 'link'" :href="segment.href" target="_blank" rel="noreferrer">{{ segment.text }}</a>
          <strong v-else-if="segment.type === 'strong'">{{ segment.text }}</strong>
          <span v-else>{{ segment.text }}</span>
        </template>
      </p>

      <blockquote v-else-if="block.type === 'quote'" class="message__quote">
        <template v-for="segment in block.segments" :key="segment.id">
          <code v-if="segment.type === 'code'">{{ segment.text }}</code>
          <a v-else-if="segment.type === 'link'" :href="segment.href" target="_blank" rel="noreferrer">{{ segment.text }}</a>
          <strong v-else-if="segment.type === 'strong'">{{ segment.text }}</strong>
          <span v-else>{{ segment.text }}</span>
        </template>
      </blockquote>

      <ul v-else-if="block.type === 'list' && !block.ordered" class="message__list">
        <li v-for="item in block.items" :key="item.id">
          <template v-for="segment in item.segments" :key="segment.id">
            <code v-if="segment.type === 'code'">{{ segment.text }}</code>
            <a v-else-if="segment.type === 'link'" :href="segment.href" target="_blank" rel="noreferrer">{{ segment.text }}</a>
            <strong v-else-if="segment.type === 'strong'">{{ segment.text }}</strong>
            <span v-else>{{ segment.text }}</span>
          </template>
        </li>
      </ul>

      <ol v-else-if="block.type === 'list'" class="message__list message__list--ordered">
        <li v-for="item in block.items" :key="item.id">
          <template v-for="segment in item.segments" :key="segment.id">
            <code v-if="segment.type === 'code'">{{ segment.text }}</code>
            <a v-else-if="segment.type === 'link'" :href="segment.href" target="_blank" rel="noreferrer">{{ segment.text }}</a>
            <strong v-else-if="segment.type === 'strong'">{{ segment.text }}</strong>
            <span v-else>{{ segment.text }}</span>
          </template>
        </li>
      </ol>

      <div v-else-if="block.type === 'table'" class="message__table-wrap">
        <table class="message__table">
          <thead>
            <tr>
              <th v-for="cell in block.headers" :key="cell.id">{{ cell.text }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in block.rows" :key="row.id">
              <td v-for="cell in row.cells" :key="cell.id">{{ cell.text }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <details v-else-if="block.type === 'json'" class="message__json-card">
        <summary>
          <span>{{ copy.message.jsonTitle }}</span>
          <small>{{ block.summary }}</small>
        </summary>
        <pre><code>{{ block.code }}</code></pre>
      </details>

      <div v-else-if="block.type === 'code'" class="message__code-block">
        <div class="message__code-head">
          <span>{{ block.language || copy.message.codeBlock }}</span>
        </div>
        <pre><code>{{ block.code }}</code></pre>
      </div>
    </template>
  </div>
</template>

<script setup>
defineProps({
  blocks: {
    type: Array,
    default: () => [],
  },
  copy: {
    type: Object,
    required: true,
  },
});
</script>
