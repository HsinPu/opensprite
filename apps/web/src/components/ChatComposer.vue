<template>
  <form class="composer" @submit="$emit('submit', $event)">
    <label class="sr-only" for="messageInput">{{ copy.composer.label }}</label>
    <div class="composer__box">
      <textarea
        id="messageInput"
        :ref="setInputRef"
        :value="modelValue"
        rows="1"
        :placeholder="copy.composer.placeholder"
        :readonly="readOnly"
        autocomplete="off"
        @input="handleInput"
        @keydown="$emit('keydown', $event)"
      ></textarea>
      <button class="send-button" type="submit" :aria-label="copy.composer.sendAria" :disabled="disabled">
        {{ copy.composer.send }}
      </button>
    </div>
    <div class="composer__footer">
      <span>{{ copy.composer.disclaimer }}</span>
      <span>{{ runtimeHint }}</span>
    </div>
  </form>
</template>

<script setup>
defineProps({
  copy: {
    type: Object,
    required: true,
  },
  modelValue: {
    type: String,
    required: true,
  },
  disabled: {
    type: Boolean,
    required: true,
  },
  readOnly: {
    type: Boolean,
    required: true,
  },
  runtimeHint: {
    type: String,
    required: true,
  },
  setInputRef: {
    type: Function,
    required: true,
  },
});

const emit = defineEmits(["update:modelValue", "input", "keydown", "submit"]);

function handleInput(event) {
  emit("update:modelValue", event.target.value);
  emit("input", event);
}
</script>
