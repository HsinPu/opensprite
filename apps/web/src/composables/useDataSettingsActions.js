export function useDataSettingsActions({ settingsState, requestSettingsJson, copy }) {
  function emptyTimeline(sessionId = "") {
    return {
      session_id: sessionId,
      messages: [],
      runs: [],
      entries: [],
    };
  }

  async function loadDataSettings() {
    settingsState.dataLoading = true;
    settingsState.dataError = "";
    try {
      const [status, sessions] = await Promise.all([
        requestSettingsJson("/api/storage/status"),
        requestSettingsJson("/api/sessions?channel=all&limit=20&messages=5"),
      ]);
      settingsState.dataStatus = status;
      settingsState.dataSessions = Array.isArray(sessions.sessions) ? sessions.sessions : [];
    } catch (error) {
      settingsState.dataError = error?.message || copy.value.notices.dataLoadFailed;
    } finally {
      settingsState.dataLoading = false;
    }
  }

  async function loadDataSessionTimeline(sessionId) {
    const normalizedSessionId = String(sessionId || "").trim();
    settingsState.dataSelectedSessionId = normalizedSessionId;
    settingsState.dataTimeline = emptyTimeline(normalizedSessionId);
    if (!normalizedSessionId) {
      return;
    }

    settingsState.dataTimelineLoading = true;
    settingsState.dataTimelineError = "";
    try {
      const params = new URLSearchParams({ session_id: normalizedSessionId, messages: "200", runs: "50" });
      const payload = await requestSettingsJson(`/api/sessions/timeline?${params.toString()}`);
      settingsState.dataTimeline = {
        session_id: payload.session_id || normalizedSessionId,
        messages: Array.isArray(payload.messages) ? payload.messages : [],
        runs: Array.isArray(payload.runs) ? payload.runs : [],
        entries: Array.isArray(payload.entries) ? payload.entries : [],
      };
    } catch (error) {
      settingsState.dataTimelineError = error?.message || copy.value.notices.dataTimelineLoadFailed;
    } finally {
      settingsState.dataTimelineLoading = false;
    }
  }

  return {
    loadDataSettings,
    loadDataSessionTimeline,
  };
}
