const DEFAULT_RISK_LEVELS = ["read", "write", "execute", "network", "external_side_effect", "configuration", "delegation", "memory", "mcp"];
export const PERMISSION_PROFILES = ["chat", "research", "coding", "media", "ops"];
export const PERMISSION_PROFILE_PRESETS = ["fast", "balanced", "strict"];

function createDefaultProfileOverrides() {
  return {
    chat: createProfileOverride({ allowed_risk_levels: ["read"] }),
    research: createProfileOverride({ allowed_risk_levels: ["read", "network"] }),
    coding: createProfileOverride({
      allowed_risk_levels: ["read", "write", "execute", "network", "external_side_effect", "configuration", "delegation", "memory"],
      denied_risk_levels: ["mcp"],
    }),
    media: createProfileOverride({ allowed_risk_levels: ["read", "network", "external_side_effect"] }),
    ops: createProfileOverride({
      approval_mode: "ask",
      approval_required_risk_levels: ["external_side_effect", "configuration", "mcp"],
    }),
  };
}

function createProfileOverride(overrides = {}) {
  return {
    enabled: true,
    approval_mode: null,
    allowed_tools: ["*"],
    denied_tools: [],
    allowed_risk_levels: [...DEFAULT_RISK_LEVELS],
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
    allowed_risk_levels: [...DEFAULT_RISK_LEVELS],
    denied_risk_levels: [],
    approval_required_tools: [],
    approval_required_risk_levels: [],
    profile_overrides: createDefaultProfileOverrides(),
    risk_level_options: [...DEFAULT_RISK_LEVELS],
    approval_mode_options: ["ask", "auto", "block"],
  };
}

export function createDefaultPermissionsForm() {
  return {
    enabled: true,
    approvalMode: "auto",
    approvalTimeoutSeconds: 300,
    allowedTools: "*",
    deniedTools: "",
    allowedRiskLevels: [...DEFAULT_RISK_LEVELS],
    deniedRiskLevels: [],
    approvalRequiredTools: "",
    approvalRequiredRiskLevels: [],
    profileOverrides: createDefaultProfileOverrides(),
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
    profile_overrides: normalizeProfileOverrides(payload.profile_overrides || payload.profileOverrides || defaults.profile_overrides),
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
  settingsState.permissionsForm.profileOverrides = normalizeProfileOverrides(permissions.profile_overrides);
}

export function serializeProfileOverrides(profileOverrides) {
  const normalized = normalizeProfileOverrides(profileOverrides);
  return Object.fromEntries(PERMISSION_PROFILES.map((profile) => [profile, normalized[profile]]));
}

export function applyPermissionProfilePreset(settingsState, preset) {
  const profiles = createDefaultProfileOverrides();
  if (preset === "fast") {
    for (const profile of PERMISSION_PROFILES) {
      profiles[profile].approval_mode = "auto";
      profiles[profile].approval_required_risk_levels = [];
    }
  } else if (preset === "strict") {
    profiles.chat.approval_mode = "auto";
    profiles.research.approval_mode = "ask";
    profiles.research.approval_required_risk_levels = ["network"];
    profiles.coding.approval_mode = "ask";
    profiles.coding.approval_required_risk_levels = ["write", "execute", "external_side_effect", "configuration"];
    profiles.media.approval_mode = "ask";
    profiles.media.approval_required_risk_levels = ["external_side_effect"];
    profiles.ops.approval_mode = "ask";
    profiles.ops.approval_required_risk_levels = ["external_side_effect", "configuration", "mcp"];
  }
  settingsState.permissionsForm.profileOverrides = profiles;
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
  const defaults = createDefaultProfileOverrides();
  const source = value && typeof value === "object" ? value : {};
  return Object.fromEntries(PERMISSION_PROFILES.map((profile) => {
    const override = source[profile] && typeof source[profile] === "object" ? source[profile] : {};
    const base = defaults[profile] || createProfileOverride();
    return [profile, {
      ...base,
      enabled: override.enabled !== false,
      approval_mode: override.approval_mode ?? override.approvalMode ?? base.approval_mode,
      allowed_tools: normalizeList(override.allowed_tools || override.allowedTools || base.allowed_tools),
      denied_tools: normalizeList(override.denied_tools || override.deniedTools || base.denied_tools),
      allowed_risk_levels: normalizeList(override.allowed_risk_levels || override.allowedRiskLevels || base.allowed_risk_levels),
      denied_risk_levels: normalizeList(override.denied_risk_levels || override.deniedRiskLevels || base.denied_risk_levels),
      approval_required_tools: normalizeList(override.approval_required_tools || override.approvalRequiredTools || base.approval_required_tools),
      approval_required_risk_levels: normalizeList(override.approval_required_risk_levels || override.approvalRequiredRiskLevels || base.approval_required_risk_levels),
    }];
  }));
}

function positiveNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}
