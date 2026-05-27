function createProfileOverride(overrides = {}) {
  return {
    enabled: true,
    approval_mode: null,
    allowed_tools: ["*"],
    denied_tools: [],
    allowed_risk_levels: [],
    denied_risk_levels: [],
    approval_required_tools: [],
    approval_required_risk_levels: [],
    ...overrides,
  };
}

export function createDefaultPermissionsState() {
  return {
    enabled: true,
    approval_mode: "auto",
    approval_timeout_seconds: 300,
    allowed_tools: ["*"],
    denied_tools: [],
    allowed_risk_levels: [],
    denied_risk_levels: [],
    approval_required_tools: [],
    approval_required_risk_levels: [],
    profile_overrides: {},
    risk_level_options: [],
    approval_mode_options: [],
  };
}

export function createDefaultPermissionsForm() {
  return {
    enabled: true,
    approvalMode: "auto",
    approvalTimeoutSeconds: 300,
    allowedTools: "*",
    deniedTools: "",
    allowedRiskLevels: [],
    deniedRiskLevels: [],
    approvalRequiredTools: "",
    approvalRequiredRiskLevels: [],
  };
}

export function normalizePermissionsSettings(value) {
  const defaults = createDefaultPermissionsState();
  const payload = value && typeof value === "object" ? value : {};
  const riskOptions = normalizeList(payload.risk_level_options || payload.riskLevelOptions || defaults.risk_level_options);
  return {
    ...defaults,
    enabled: payload.enabled !== false,
    approval_mode: String(payload.approval_mode ?? payload.approvalMode ?? defaults.approval_mode).trim(),
    approval_timeout_seconds: positiveNumber(payload.approval_timeout_seconds ?? payload.approvalTimeoutSeconds, defaults.approval_timeout_seconds),
    allowed_tools: normalizeList(payload.allowed_tools || payload.allowedTools || defaults.allowed_tools),
    denied_tools: normalizeList(payload.denied_tools || payload.deniedTools),
    allowed_risk_levels: normalizeList(payload.allowed_risk_levels || payload.allowedRiskLevels || defaults.allowed_risk_levels),
    denied_risk_levels: normalizeList(payload.denied_risk_levels || payload.deniedRiskLevels),
    approval_required_tools: normalizeList(payload.approval_required_tools || payload.approvalRequiredTools),
    approval_required_risk_levels: normalizeList(payload.approval_required_risk_levels || payload.approvalRequiredRiskLevels),
    profile_overrides: normalizeProfileOverrides(payload.profile_overrides || payload.profileOverrides),
    risk_level_options: riskOptions.length ? riskOptions : defaults.risk_level_options,
    approval_mode_options: normalizeList(payload.approval_mode_options || payload.approvalModeOptions || defaults.approval_mode_options),
  };
}

export function syncPermissionsForm(settingsState) {
  const permissions = settingsState.permissions || createDefaultPermissionsState();
  settingsState.permissionsForm.enabled = permissions.enabled;
  settingsState.permissionsForm.approvalMode = permissions.approval_mode || "auto";
  settingsState.permissionsForm.approvalTimeoutSeconds = permissions.approval_timeout_seconds;
  settingsState.permissionsForm.allowedTools = permissions.allowed_tools.join("\n");
  settingsState.permissionsForm.deniedTools = permissions.denied_tools.join("\n");
  settingsState.permissionsForm.allowedRiskLevels = [...permissions.allowed_risk_levels];
  settingsState.permissionsForm.deniedRiskLevels = [...permissions.denied_risk_levels];
  settingsState.permissionsForm.approvalRequiredTools = permissions.approval_required_tools.join("\n");
  settingsState.permissionsForm.approvalRequiredRiskLevels = [...permissions.approval_required_risk_levels];
}

export function splitPermissionList(value) {
  return String(value || "")
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeList(value) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item || "").trim()).filter(Boolean);
  }
  if (typeof value === "string") {
    return splitPermissionList(value);
  }
  return [];
}

function normalizeProfileOverrides(value) {
  const source = value && typeof value === "object" ? value : {};
  return Object.fromEntries(Object.entries(source).flatMap(([profile, override]) => {
    const profileName = String(profile || "").trim();
    if (!profileName || !override || typeof override !== "object") {
      return [];
    }
    const base = createProfileOverride();
    return [[profileName, {
      ...base,
      enabled: override.enabled !== false,
      approval_mode: override.approval_mode ?? override.approvalMode ?? base.approval_mode,
      allowed_tools: normalizeList(override.allowed_tools || override.allowedTools || base.allowed_tools),
      denied_tools: normalizeList(override.denied_tools || override.deniedTools || base.denied_tools),
      allowed_risk_levels: normalizeList(override.allowed_risk_levels || override.allowedRiskLevels || base.allowed_risk_levels),
      denied_risk_levels: normalizeList(override.denied_risk_levels || override.deniedRiskLevels || base.denied_risk_levels),
      approval_required_tools: normalizeList(override.approval_required_tools || override.approvalRequiredTools || base.approval_required_tools),
      approval_required_risk_levels: normalizeList(override.approval_required_risk_levels || override.approvalRequiredRiskLevels || base.approval_required_risk_levels),
    }]];
  }));
}

function positiveNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}
