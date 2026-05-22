<template>
  <div class="message-list">
    <article
      v-for="message in messages"
      :key="message.id"
      class="message"
      :class="`message--${message.role}`"
    >
      <div class="message__avatar">{{ message.role === "user" ? copy.message.userAvatar : copy.message.assistantAvatar }}</div>
      <div class="message__content">
        <div class="message__meta">
          {{ message.meta || (message.role === "user" ? displayName : "OpenSprite") }}
        </div>
        <div v-if="message.textBlocks.length" class="message__bubble">
          <MessageTextRenderer :blocks="message.textBlocks" :copy="copy" />
        </div>
        <div v-if="message.content.length" class="message__parts">
          <template v-for="part in message.content" :key="part.id">
            <div v-if="part.type === 'text'" class="message__bubble">
              <MessageTextRenderer :blocks="part.textBlocks" :copy="copy" />
            </div>
            <div v-else class="message__artifact" :data-type="part.type" :data-status="part.status || undefined">
              <div class="message__artifact-header">
                <span class="message__artifact-type">{{ artifactTypeLabel(part.type) }}</span>
                <small v-if="part.status" class="message__artifact-status">{{ artifactStatusLabel(part.status) }}</small>
              </div>
              <strong>{{ part.title || artifactTypeLabel(part.type) }}</strong>
              <p v-if="part.detail">{{ part.detail }}</p>
            </div>
          </template>
        </div>
      </div>
    </article>
  </div>
</template>

<script setup>
import { computed } from "vue";

import MessageTextRenderer from "./MessageTextRenderer.vue";

const props = defineProps({
  copy: {
    type: Object,
    required: true,
  },
  entries: {
    type: Array,
    default: () => [],
  },
  messages: {
    type: Array,
    required: true,
  },
  displayName: {
    type: String,
    required: true,
  },
});

const INTERNAL_BLOCK_RE = /<\s*(think|thinking|system-reminder)\b[^>]*>[\s\S]*?<\s*\/\s*\1\s*>/gi;
const INTERNAL_OPEN_BLOCK_RE = /<\s*(think|thinking|system-reminder)\b[^>]*>[\s\S]*$/i;

function sanitizeVisibleText(value) {
  return String(value || "")
    .replace(INTERNAL_BLOCK_RE, "")
    .replace(INTERNAL_OPEN_BLOCK_RE, "")
    .trim();
}

function normalizeTextPart(part, index) {
  const text = sanitizeVisibleText(part?.text || part?.detail || "");
  if (!text) {
    return null;
  }
  return {
    id: part?.id || `text-${index}`,
    type: "text",
    text,
    textBlocks: buildMessageBlocks(text, `part-${index}`),
  };
}

function normalizeEntry(entry, index) {
  const role = entry.role === "user" ? "user" : "assistant";
  const content = Array.isArray(entry.content)
    ? entry.content.map(normalizeTextPart).filter(Boolean)
    : [];
  const text = sanitizeVisibleText(entry.text || "");

  if (!text && content.length === 0) {
    return null;
  }

  return {
    id: entry.id || `entry-${index}`,
    role,
    text,
    textBlocks: buildMessageBlocks(text, `entry-${index}`),
    meta: entry.meta || (role === "user" ? props.displayName : "OpenSprite"),
    content,
  };
}

function isChatEntry(entry) {
  const runId = String(entry?.runId || entry?.run_id || "").trim();
  if (runId) {
    return false;
  }
  const entryId = String(entry?.id || entry?.entry_id || entry?.entryId || "").trim();
  if (entryId.startsWith("run:")) {
    return false;
  }
  return entry?.role === "user" || entry?.role === "assistant";
}

function normalizeMessage(message) {
  const text = sanitizeVisibleText(message.text);
  return {
    ...message,
    text,
    textBlocks: buildMessageBlocks(text, message.id || "message"),
    content: [],
  };
}

const messages = computed(() => {
  if (props.entries.length) {
    return props.entries.filter(isChatEntry).map(normalizeEntry).filter(Boolean);
  }

  return props.messages.map(normalizeMessage).filter((message) => message.text.trim());
});

function artifactTypeLabel(type) {
  const labels = props.copy.message.artifactTypes || {};
  return labels[type] || type;
}

function artifactStatusLabel(status) {
  const labels = props.copy.run?.statusLabels || {};
  return labels[status] || status;
}

function buildMessageBlocks(value, keyPrefix) {
  const text = String(value || "").replace(/\r\n/g, "\n").trim();
  if (!text) {
    return [];
  }
  const jsonBlock = maybeJsonBlock(text, `${keyPrefix}:json`);
  if (jsonBlock) {
    return [jsonBlock];
  }
  return parseMarkdownBlocks(text, keyPrefix);
}

function maybeJsonBlock(text, id) {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) {
    return null;
  }
  try {
    const parsed = JSON.parse(trimmed);
    return {
      id,
      type: "json",
      summary: jsonSummary(parsed),
      code: JSON.stringify(parsed, null, 2),
    };
  } catch {
    return null;
  }
}

function jsonSummary(value) {
  if (Array.isArray(value)) {
    return props.copy.message.jsonArray(value.length);
  }
  if (value && typeof value === "object") {
    const keys = Object.keys(value);
    return props.copy.message.jsonObject(keys.slice(0, 4).join(", "), keys.length);
  }
  return props.copy.message.jsonValue;
}

function parseMarkdownBlocks(text, keyPrefix) {
  const lines = text.split("\n");
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed) {
      index += 1;
      continue;
    }

    const fence = trimmed.match(/^```([A-Za-z0-9_-]*)\s*$/);
    if (fence) {
      const language = fence[1] || "";
      const codeLines = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      blocks.push(codeBlock(codeLines.join("\n"), language, `${keyPrefix}:code-${blocks.length}`));
      continue;
    }

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      blocks.push({
        id: `${keyPrefix}:heading-${blocks.length}`,
        type: "heading",
        level: heading[1].length,
        text: heading[2].trim(),
        segments: inlineSegments(heading[2].trim(), `${keyPrefix}:heading-${blocks.length}`),
      });
      index += 1;
      continue;
    }

    if (isMarkdownTable(lines, index)) {
      const tableLines = [lines[index]];
      index += 2;
      while (index < lines.length && lines[index].includes("|")) {
        tableLines.push(lines[index]);
        index += 1;
      }
      blocks.push(tableBlock(tableLines, `${keyPrefix}:table-${blocks.length}`));
      continue;
    }

    const unordered = trimmed.match(/^[-*]\s+(.+)$/);
    const ordered = trimmed.match(/^\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const orderedList = Boolean(ordered);
      const items = [];
      while (index < lines.length) {
        const itemMatch = lines[index].trim().match(orderedList ? /^\d+[.)]\s+(.+)$/ : /^[-*]\s+(.+)$/);
        if (!itemMatch) {
          break;
        }
        const itemText = itemMatch[1].trim();
        items.push({
          id: `${keyPrefix}:list-${blocks.length}-${items.length}`,
          text: itemText,
          segments: inlineSegments(itemText, `${keyPrefix}:list-${blocks.length}-${items.length}`),
        });
        index += 1;
      }
      blocks.push({
        id: `${keyPrefix}:list-${blocks.length}`,
        type: "list",
        ordered: orderedList,
        items,
      });
      continue;
    }

    const quote = trimmed.match(/^>\s?(.*)$/);
    if (quote) {
      const quoteLines = [];
      while (index < lines.length) {
        const quoteMatch = lines[index].trim().match(/^>\s?(.*)$/);
        if (!quoteMatch) {
          break;
        }
        quoteLines.push(quoteMatch[1]);
        index += 1;
      }
      const quoteText = quoteLines.join(" ").trim();
      blocks.push({
        id: `${keyPrefix}:quote-${blocks.length}`,
        type: "quote",
        text: quoteText,
        segments: inlineSegments(quoteText, `${keyPrefix}:quote-${blocks.length}`),
      });
      continue;
    }

    const paragraphLines = [];
    while (index < lines.length && !isBlockStart(lines, index)) {
      if (!lines[index].trim()) {
        break;
      }
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    const paragraph = paragraphLines.join(" ").trim();
    if (paragraph) {
      blocks.push({
        id: `${keyPrefix}:paragraph-${blocks.length}`,
        type: "paragraph",
        text: paragraph,
        segments: inlineSegments(paragraph, `${keyPrefix}:paragraph-${blocks.length}`),
      });
    }
  }
  return blocks;
}

function codeBlock(code, language, id) {
  const jsonBlock = language.toLowerCase() === "json" ? maybeJsonBlock(code, id) : null;
  if (jsonBlock) {
    return jsonBlock;
  }
  return {
    id,
    type: "code",
    language,
    code,
  };
}

function isBlockStart(lines, index) {
  const trimmed = lines[index]?.trim() || "";
  if (!trimmed) {
    return false;
  }
  return /^```/.test(trimmed)
    || /^(#{1,3})\s+/.test(trimmed)
    || /^[-*]\s+/.test(trimmed)
    || /^\d+[.)]\s+/.test(trimmed)
    || /^>\s?/.test(trimmed)
    || isMarkdownTable(lines, index);
}

function isMarkdownTable(lines, index) {
  const current = lines[index]?.trim() || "";
  const next = lines[index + 1]?.trim() || "";
  return current.includes("|") && /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(next);
}

function tableBlock(tableLines, id) {
  const headers = splitTableRow(tableLines[0]).map((text, index) => ({ id: `${id}:h-${index}`, text }));
  const rows = tableLines.slice(1).map((line, rowIndex) => {
    const cells = splitTableRow(line).map((text, cellIndex) => ({
      id: `${id}:r-${rowIndex}-${cellIndex}`,
      text,
    }));
    return { id: `${id}:r-${rowIndex}`, cells };
  });
  return {
    id,
    type: "table",
    headers,
    rows,
  };
}

function splitTableRow(line) {
  return String(line || "")
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function inlineSegments(text, idPrefix) {
  const segments = [];
  const pattern = /(`[^`]+`)|(\[([^\]]+)\]\((https?:\/\/[^)\s]+)\))|(\*\*([^*\n][\s\S]*?[^*\n])\*\*)/g;
  let cursor = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      segments.push({ id: `${idPrefix}:t-${segments.length}`, type: "text", text: text.slice(cursor, match.index) });
    }
    if (match[1]) {
      segments.push({ id: `${idPrefix}:c-${segments.length}`, type: "code", text: match[1].slice(1, -1) });
    } else if (match[2]) {
      segments.push({ id: `${idPrefix}:l-${segments.length}`, type: "link", text: match[3], href: match[4] });
    } else {
      segments.push({ id: `${idPrefix}:s-${segments.length}`, type: "strong", text: match[6] });
    }
    cursor = pattern.lastIndex;
  }
  if (cursor < text.length) {
    segments.push({ id: `${idPrefix}:t-${segments.length}`, type: "text", text: text.slice(cursor) });
  }
  return segments.length ? segments : [{ id: `${idPrefix}:t-0`, type: "text", text }];
}
</script>
