export function useDataSettingsActions({ settingsState, requestSettingsJson, copy }) {
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

  return {
    loadDataSettings,
  };
}
