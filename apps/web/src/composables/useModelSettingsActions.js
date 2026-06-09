import { normalizeMediaSettings } from "./settingsNormalizers";

function normalizeLlmSettings(payload = {}) {
  const llm = payload?.llm || {};
  return {
    effective_request: llm.effective_request && typeof llm.effective_request === "object" ? llm.effective_request : null,
  };
}

export function useModelSettingsActions({ settingsState, requestSettingsJson, copy, setSettingsSuccess, loadProviderSettings }) {
  async function loadModelSettings() {
    settingsState.modelsLoading = true;
    settingsState.mediaLoading = true;
    settingsState.llmLoading = true;
    settingsState.modelsError = "";
    settingsState.mediaError = "";
    settingsState.llmError = "";
    try {
      const [models, media, llm] = await Promise.all([
        requestSettingsJson("/api/settings/models"),
        requestSettingsJson("/api/settings/media"),
        requestSettingsJson("/api/settings/llm"),
      ]);
      settingsState.models = models;
      settingsState.media = normalizeMediaSettings(media);
      settingsState.llm = normalizeLlmSettings(llm);
      const activeProvider = (settingsState.models.providers || []).find((provider) => provider.is_default);
      settingsState.selectedTextProviderId = activeProvider?.id || settingsState.models.providers?.[0]?.id || "";
      for (const provider of settingsState.models.providers || []) {
        const selectedModel = provider.selected_model || provider.models?.[0] || "";
        settingsState.modelSelections[provider.id] = selectedModel;
        settingsState.customModels[provider.id] = "";
      }
      for (const category of Object.keys(settingsState.media.sections || {})) {
        const section = settingsState.media.sections[category] || {};
        settingsState.mediaSelections[category] = {
          enabled: Boolean(section.enabled),
          providerId: section.provider_id || settingsState.media.providers?.[0]?.id || "",
          model: section.model || "",
        };
        settingsState.mediaCustomModels[category] = "";
      }
    } catch (error) {
      settingsState.modelsError = error?.message || copy.value.notices.modelLoadFailed;
      settingsState.mediaError = error?.message || copy.value.notices.mediaModelLoadFailed;
      settingsState.llmError = error?.message || copy.value.notices.llmSettingsLoadFailed;
    } finally {
      settingsState.modelsLoading = false;
      settingsState.mediaLoading = false;
      settingsState.llmLoading = false;
    }
  }

  async function selectModel(providerId, model) {
    const normalizedModel = String(model || "").trim();
    if (!normalizedModel) {
      settingsState.modelsError = copy.value.notices.modelRequired;
      return;
    }

    settingsState.modelsLoading = true;
    settingsState.modelsError = "";
    settingsState.modelsNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/models/select", {
        method: "POST",
        body: JSON.stringify({ provider_id: providerId, model: normalizedModel }),
      });
      setSettingsSuccess(
        "modelsNotice",
        payload.restart_required ? copy.value.notices.modelRestartRequired : copy.value.notices.modelApplied,
      );
      settingsState.customModels[providerId] = "";
      settingsState.modelSelections[providerId] = normalizedModel;
      await loadModelSettings();
      await loadProviderSettings?.();
    } catch (error) {
      settingsState.modelsError = error?.message || copy.value.notices.modelSelectFailed;
    } finally {
      settingsState.modelsLoading = false;
    }
  }

  async function saveLlmSettings() {
    settingsState.llmLoading = true;
    settingsState.llmError = "";
    settingsState.llmNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/llm", {
        method: "PUT",
        body: JSON.stringify({}),
      });
      settingsState.llm = normalizeLlmSettings(payload);
      setSettingsSuccess(
        "llmNotice",
        payload.restart_required ? copy.value.notices.modelRestartRequired : copy.value.notices.llmSettingsSaved,
      );
    } catch (error) {
      settingsState.llmError = error?.message || copy.value.notices.llmSettingsSaveFailed;
    } finally {
      settingsState.llmLoading = false;
    }
  }

  async function saveMediaModel(category, modelOverride = "") {
    const selection = settingsState.mediaSelections[category] || {};
    const normalizedModel = String(modelOverride || selection.model || "").trim();
    if (selection.enabled && !normalizedModel) {
      settingsState.mediaError = copy.value.notices.modelRequired;
      return;
    }
    settingsState.mediaLoading = true;
    settingsState.mediaError = "";
    settingsState.mediaNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/media", {
        method: "PUT",
        body: JSON.stringify({
          category,
          enabled: Boolean(selection.enabled),
          provider_id: selection.providerId,
          model: normalizedModel,
        }),
      });
      settingsState.media = normalizeMediaSettings(payload.media);
      settingsState.mediaSelections[category] = {
        enabled: Boolean(settingsState.media.sections[category]?.enabled),
        providerId: settingsState.media.sections[category]?.provider_id || selection.providerId || "",
        model: settingsState.media.sections[category]?.model || normalizedModel,
      };
      settingsState.mediaCustomModels[category] = "";
      setSettingsSuccess(
        "mediaNotice",
        payload.restart_required ? copy.value.notices.mediaModelRestartRequired : copy.value.notices.mediaModelApplied,
      );
    } catch (error) {
      settingsState.mediaError = error?.message || copy.value.notices.mediaModelSaveFailed;
    } finally {
      settingsState.mediaLoading = false;
    }
  }

  return {
    loadModelSettings,
    selectModel,
    saveLlmSettings,
    saveMediaModel,
  };
}
