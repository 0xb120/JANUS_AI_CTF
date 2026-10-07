(() => {
  "use strict";

  /*
   * JANUS frontend adapter.
   * Keep every backend assumption in this object: the rest of the UI only uses
   * normalized values returned by API.* methods.
   */
  const API = {
    base: "/api",
    timeoutMs: 90_000,

    async request(path, options = {}) {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs ?? this.timeoutMs);
      const headers = new Headers(options.headers || {});
      let body = options.body;

      if (body != null && !(body instanceof Blob) && !(body instanceof FormData) && typeof body !== "string") {
        headers.set("Content-Type", "application/json");
        body = JSON.stringify(body);
      }

      try {
        const response = await fetch(`${this.base}${path}`, {
          method: options.method || "GET",
          headers,
          body,
          signal: controller.signal,
          cache: "no-store",
          keepalive: options.keepalive || false,
        });
        const contentType = (response.headers.get("content-type") || "").toLowerCase();
        let payload = null;

        if (response.status !== 204) {
          if (contentType.includes("application/json")) {
            payload = await response.json();
          } else if (contentType.startsWith("audio/")) {
            payload = { audioBlob: await response.blob(), audioMime: contentType.split(";")[0] };
          } else {
            const text = await response.text();
            payload = text ? { text } : null;
          }
        }

        if (!response.ok) {
          const detail = payload?.detail || payload?.message || payload?.error?.message || payload?.error || payload?.text || `HTTP ${response.status}`;
          throw new ApiError(Array.isArray(detail) ? detail.map(item => item.msg || String(item)).join("; ") : String(detail), response.status, payload);
        }
        return payload || {};
      } catch (error) {
        if (error.name === "AbortError") {
          throw new ApiError("JANUS non ha risposto entro il tempo previsto.", 408);
        }
        if (error instanceof ApiError) throw error;
        throw new ApiError("Il nucleo locale non è raggiungibile.", 0, error);
      } finally {
        window.clearTimeout(timeout);
      }
    },

    config() {
      return this.request("/config", { timeoutMs: 6_000 });
    },

    health() {
      return this.request("/health", { timeoutMs: 4_000 });
    },

    createSession({ modeId, levelId, nickname }) {
      return this.request("/sessions", {
        method: "POST",
        body: {
          mode_id: modeId,
          level_id: levelId,
          nickname: nickname || undefined,
        },
      });
    },

    sendMessage(sessionId, { text, language, speak = false }) {
      return this.request(`/sessions/${encodeURIComponent(sessionId)}/messages`, {
        method: "POST",
        body: { text, language, speak },
      }).then(normalizeTurnResponse);
    },

    async sendVoice(sessionId, blob, { language, speak = true }) {
      const query = new URLSearchParams({ language, speak: String(Boolean(speak)) });
      try {
        // Canonical kiosk contract: encoded audio bytes directly in the request body.
        const payload = await this.request(`/sessions/${encodeURIComponent(sessionId)}/voice?${query}`, {
          method: "POST",
          headers: { "Content-Type": blob.type || "audio/webm" },
          body: blob,
        });
        return normalizeTurnResponse(payload);
      } catch (error) {
        // Compatibility with early backend builds that accepted multipart only.
        if (error.status !== 415) throw error;
        const form = new FormData();
        const extension = blob.type.includes("ogg")
          ? "ogg"
          : blob.type.includes("wav")
            ? "wav"
            : blob.type.includes("mp4")
              ? "m4a"
              : blob.type.includes("mpeg")
                ? "mp3"
                : "webm";
        form.append("file", blob, `janus-input.${extension}`);
        form.append("language", language);
        form.append("speak", String(Boolean(speak)));
        const payload = await this.request(`/sessions/${encodeURIComponent(sessionId)}/voice`, {
          method: "POST",
          body: form,
        });
        return normalizeTurnResponse(payload);
      }
    },

    submitFlag(sessionId, flag) {
      return this.request(`/sessions/${encodeURIComponent(sessionId)}/submit`, {
        method: "POST",
        body: { flag },
      });
    },

    hint(sessionId, language) {
      const query = new URLSearchParams({ language });
      return this.request(`/sessions/${encodeURIComponent(sessionId)}/hint?${query}`, {
        method: "POST",
      });
    },

    deleteSession(sessionId) {
      return this.request(`/sessions/${encodeURIComponent(sessionId)}`, {
        method: "DELETE",
        timeoutMs: 5_000,
      });
    },

    leaderboard(levelId, limit = 10) {
      const query = new URLSearchParams({ level_id: levelId, limit: String(limit) });
      return this.request(`/leaderboard?${query}`, { timeoutMs: 8_000 });
    },

    join(code) {
      return this.request("/join", { method: "POST", body: { code }, timeoutMs: 8_000 });
    },

    recover(recoveryCode) {
      return this.request("/recover", { method: "POST", body: { recovery_code: recoveryCode }, timeoutMs: 8_000 });
    },

    me() {
      return this.request("/players/me", { timeoutMs: 6_000 });
    },

    getSession(sessionId) {
      return this.request(`/sessions/${encodeURIComponent(sessionId)}`, { timeoutMs: 6_000 });
    },

    history(sessionId) {
      return this.request(`/sessions/${encodeURIComponent(sessionId)}/messages`, { timeoutMs: 8_000 });
    },
  };

  class ApiError extends Error {
    constructor(message, status = 0, payload = null) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.payload = payload;
    }
  }

  const DEFAULT_LEVELS = [
    {
      id: "level_1",
      name: "Il Custode",
      description: "Una sola direttiva protegge il segreto. Cerca una crepa nelle sue istruzioni.",
      difficulty: 1,
      hint_count: 2,
      points: 100,
      time_limit_seconds: 480,
    },
    {
      id: "level_2",
      name: "La Sentinella",
      description: "Guardrail bilingue e filtri sorvegliano ogni risposta. Cambia prospettiva.",
      difficulty: 2,
      hint_count: 2,
      points: 250,
      time_limit_seconds: 600,
    },
    {
      id: "level_3",
      name: "Il Caveau",
      description: "Autorizzazioni e strumenti interni separano JANUS dalla flag. Concatena l’attacco.",
      difficulty: 3,
      hint_count: 3,
      points: 500,
      time_limit_seconds: 720,
    },
  ];

  const DEFAULT_CONFIG = {
    app: { name: "JANUS", default_mode: "stand", default_level: "level_1", event: "RomHack 2026" },
    modes: [
      { id: "stand", score_enabled: false, nickname_required: false, leaderboard_enabled: false, show_timer: false },
      { id: "score", score_enabled: true, nickname_required: true, leaderboard_enabled: true, show_timer: true },
    ],
    levels: DEFAULT_LEVELS,
    capabilities: { voice: true, tts: true, hints: true },
    limits: { text_chars: 3000, audio_seconds: 30 },
    ui: { stand_result_seconds: 18 },
  };

  const state = {
    online: false,
    resumeCandidate: null,
    composerLockedUntil: 0,
    config: DEFAULT_CONFIG,
    mode: "stand",
    levels: DEFAULT_LEVELS,
    selectedLevelId: DEFAULT_LEVELS[0].id,
    language: "auto",
    screen: "attract",
    session: null,
    sessionEpoch: 0,
    startAttempt: 0,
    busy: false,
    configFromServer: false,
    configRefreshPromise: null,
    voiceServiceAvailable: null,
    coreStatus: "unknown",
    timer: null,
    mediaStream: null,
    mediaRecorder: null,
    recordingIntent: false,
    recordingEpoch: null,
    discardRecording: false,
    recordingStartedAt: 0,
    recordingTimer: null,
    recordingStopTimer: null,
    mediaChunks: [],
    audioContext: null,
    inputAnalyser: null,
    inputSource: null,
    inputAnimation: null,
    playbackAudio: null,
    playbackAnimation: null,
    speechAnimation: null,
    speechUtterance: null,
    objectUrls: new Set(),
    leaderboardLevelId: null,
    leaderboardReturn: "attract",
    resultResetTimer: null,
    attractAnimationTimer: null,
    latestNickname: "",
  };

  const els = {};
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];

  document.addEventListener("DOMContentLoaded", init);

  async function init() {
    cacheElements();
    mountAvatars();
    bindEvents();
    startClock();
    startAttractAnimation();

    const minimumBoot = delay(750);
    let configWarning = null;
    try {
      const rawConfig = await API.config();
      adoptConfig(rawConfig, true);
    } catch (error) {
      adoptConfig(DEFAULT_CONFIG, false);
      configWarning = "Configurazione locale non disponibile: caricati i valori di emergenza.";
    }

    await Promise.all([minimumBoot, updateHealth()]);
    els.app.hidden = false;
    state.online = Boolean(state.config.app?.online);
    if (state.online) await enterOnline();
    else showScreen("attract");
    els.bootScreen.classList.add("is-leaving");
    window.setTimeout(() => els.bootScreen.remove(), 650);
    if (configWarning) toast(configWarning, "warning", 6000);
    window.setInterval(updateHealth, 15_000);
  }

  function errorCode(error) {
    return error?.payload?.error?.code || "";
  }

  function retryAfterSeconds(error) {
    return Math.max(1, Number(error?.payload?.error?.details?.retry_after) || 5);
  }

  function selectAccessTab(tab) {
    const join = tab === "join";
    els.accessTabJoin.setAttribute("aria-selected", String(join));
    els.accessTabRecover.setAttribute("aria-selected", String(!join));
    els.joinForm.hidden = !join;
    els.recoverForm.hidden = join;
    (join ? els.accessCodeInput : els.recoveryCodeInput).focus();
  }

  function showAccess(message) {
    els.recoveryCard.hidden = true;
    els.accessTabs.hidden = false;
    els.joinError.textContent = "";
    els.recoverError.textContent = "";
    selectAccessTab("join");
    showScreen("access");
    if (message) toast(message, "warning", 6000);
  }

  function accessErrorMessage(error) {
    if (error.status === 429) return `Troppi tentativi: riprova tra ${retryAfterSeconds(error)} s.`;
    if (error.status === 401) return "Codice non valido.";
    return error.message;
  }

  async function enterOnline() {
    try {
      adoptPlayer(await API.me());
      showScreen("attract");
    } catch (error) {
      if (error.status === 401) showAccess();
      else {
        showScreen("attract");
        toast(error.message, "error", 6000);
      }
    }
  }

  function adoptPlayer(me) {
    if (me.nickname) state.latestNickname = me.nickname;
    const active = (me.active_sessions || [])[0] || null;
    state.resumeCandidate = active;
    els.resumeBanner.hidden = !active;
    if (active) {
      const level = getLevel(active.level_id);
      els.resumeText.textContent = `Partita in corso: ${level?.name || active.level_id} — tempo residuo ${formatDuration(active.remaining_seconds)}.`;
    }
  }

  async function submitJoin(event) {
    event.preventDefault();
    const code = els.accessCodeInput.value.trim();
    if (!code) {
      els.joinError.textContent = "Inserisci il codice evento.";
      return;
    }
    setButtonLoading(els.joinButton, true, "VERIFICA…");
    try {
      const result = await API.join(code);
      els.accessCodeInput.value = "";
      els.recoveryCodeValue.textContent = result.recovery_code;
      els.joinForm.hidden = true;
      els.recoverForm.hidden = true;
      els.accessTabs.hidden = true;
      els.recoveryCard.hidden = false;
      els.recoverySavedButton.focus();
    } catch (error) {
      els.joinError.textContent = accessErrorMessage(error);
    } finally {
      setButtonLoading(els.joinButton, false);
    }
  }

  async function submitRecover(event) {
    event.preventDefault();
    const code = els.recoveryCodeInput.value.trim();
    if (!code) {
      els.recoverError.textContent = "Inserisci il codice di recupero.";
      return;
    }
    setButtonLoading(els.recoverButton, true, "VERIFICA…");
    try {
      await API.recover(code);
      els.recoveryCodeInput.value = "";
      await enterOnline();
    } catch (error) {
      els.recoverError.textContent = accessErrorMessage(error);
    } finally {
      setButtonLoading(els.recoverButton, false);
    }
  }

  async function copyRecoveryCode() {
    try {
      await navigator.clipboard.writeText(els.recoveryCodeValue.textContent);
      toast("Codice copiato.", "info", 2500);
    } catch {
      toast("Copia non riuscita: annota il codice a mano.", "warning", 4000);
    }
  }

  async function resumeSession() {
    const candidate = state.resumeCandidate;
    if (!candidate) return;
    try {
      const me = await API.me();
      const fresh = (me.active_sessions || []).find(item => item.id === candidate.id);
      if (!fresh) {
        els.resumeBanner.hidden = true;
        state.resumeCandidate = null;
        toast("La partita non è più disponibile.", "warning", 5000);
        return;
      }
      const [rawSession, history] = await Promise.all([API.getSession(candidate.id), API.history(candidate.id)]);
      state.sessionEpoch += 1;
      state.session = normalizeSession(rawSession, candidate.level_id, state.latestNickname);
      state.selectedLevelId = state.session.level_id;
      enterGame({ resumed: true });
      const messages = history.messages || [];
      for (const item of messages) {
        addMessage(item.role === "assistant" ? "assistant" : "user", item.content, { language: item.language });
      }
      if (messages.some(item => item.content.includes("[REDACTED_SESSION_FLAG]"))) {
        addMessage("system", "Per sicurezza la flag non viene mai salvata: nella cronologia ripresa appare oscurata.");
      }
      setTimerRemaining(fresh.remaining_seconds);
      els.resumeBanner.hidden = true;
      state.resumeCandidate = null;
    } catch (error) {
      if (error.status === 401) return showAccess("Accesso scaduto: rientra con il codice.");
      if (error.status === 404 || error.status === 409) {
        els.resumeBanner.hidden = true;
        state.resumeCandidate = null;
        toast("La partita non è più disponibile.", "warning", 5000);
      } else {
        toast(error.message, "error", 5000);
      }
    }
  }

  async function abandonResumable() {
    const candidate = state.resumeCandidate;
    if (!candidate) return;
    try {
      await API.deleteSession(candidate.id);
    } catch (error) {
      if (error.status !== 404) toast(error.message, "error", 5000);
    }
    els.resumeBanner.hidden = true;
    state.resumeCandidate = null;
  }

  function lockComposerFor(seconds, reason) {
    const until = Date.now() + seconds * 1000;
    state.composerLockedUntil = until;
    setControlsEnabled(false);
    toast(`${reason}: riprova tra ${seconds} s.`, "warning", Math.min(seconds, 10) * 1000);
    window.setTimeout(() => {
      if (state.composerLockedUntil !== until) return;
      state.composerLockedUntil = 0;
      if (!state.busy) setControlsEnabled(true);
    }, seconds * 1000);
  }

  function cacheElements() {
    Object.assign(els, {
      app: $("#app"),
      accessTabs: $("#accessTabs"),
      accessTabJoin: $("#accessTabJoin"),
      accessTabRecover: $("#accessTabRecover"),
      joinForm: $("#joinForm"),
      accessCodeInput: $("#accessCodeInput"),
      joinError: $("#joinError"),
      joinButton: $("#joinButton"),
      recoverForm: $("#recoverForm"),
      recoveryCodeInput: $("#recoveryCodeInput"),
      recoverError: $("#recoverError"),
      recoverButton: $("#recoverButton"),
      recoveryCard: $("#recoveryCard"),
      recoveryCodeValue: $("#recoveryCodeValue"),
      copyRecoveryButton: $("#copyRecoveryButton"),
      recoverySavedButton: $("#recoverySavedButton"),
      resumeBanner: $("#resumeBanner"),
      resumeText: $("#resumeText"),
      resumeButton: $("#resumeButton"),
      abandonButton: $("#abandonButton"),
      bootScreen: $("#bootScreen"),
      bootMessage: $("#bootMessage"),
      healthDot: $("#healthDot"),
      healthLabel: $("#healthLabel"),
      modeLabel: $("#modeLabel"),
      clockLabel: $("#clockLabel"),
      footerMessage: $("#footerMessage"),
      brandButton: $("#brandButton"),
      beginButton: $("#beginButton"),
      openLeaderboardButton: $("#openLeaderboardButton"),
      setupBackButton: $("#setupBackButton"),
      setupForm: $("#setupForm"),
      nicknameInput: $("#nicknameInput"),
      nicknameCounter: $("#nicknameCounter"),
      nicknameError: $("#nicknameError"),
      levelGrid: $("#levelGrid"),
      languageSelector: $("#languageSelector"),
      startSessionButton: $("#startSessionButton"),
      gameLevelCode: $("#gameLevelCode"),
      gameTitle: $("#gameTitle"),
      missionObjective: $("#missionObjective"),
      turnCount: $("#turnCount"),
      timerBox: $("#timerBox"),
      timerLabel: $("#timerLabel"),
      leaveGameButton: $("#leaveGameButton"),
      avatarStateLabel: $("#avatarStateLabel"),
      voiceMeter: $("#voiceMeter"),
      hintButton: $("#hintButton"),
      hintCount: $("#hintCount"),
      openFlagButton: $("#openFlagButton"),
      sessionShortId: $("#sessionShortId"),
      detectedLanguage: $("#detectedLanguage"),
      messageList: $("#messageList"),
      thinkingStrip: $("#thinkingStrip"),
      thinkingText: $("#thinkingText"),
      messageInput: $("#messageInput"),
      sendButton: $("#sendButton"),
      pttButton: $("#pttButton"),
      pttStatus: $("#pttStatus"),
      recordTime: $("#recordTime"),
      resultScreen: $("#screenResult"),
      resultKicker: $("#resultKicker"),
      resultTitle: $("#resultTitle"),
      resultCopy: $("#resultCopy"),
      resultScore: $("#resultScore"),
      resultTime: $("#resultTime"),
      resultTurns: $("#resultTurns"),
      resultHints: $("#resultHints"),
      newChallengeButton: $("#newChallengeButton"),
      resultLeaderboardButton: $("#resultLeaderboardButton"),
      autoResetLabel: $("#autoResetLabel"),
      leaderboardBackButton: $("#leaderboardBackButton"),
      leaderboardTabs: $("#leaderboardTabs"),
      leaderboardBody: $("#leaderboardBody"),
      leaderboardEmpty: $("#leaderboardEmpty"),
      leaderboardChallengeButton: $("#leaderboardChallengeButton"),
      flagDialog: $("#flagDialog"),
      flagForm: $("#flagForm"),
      flagInput: $("#flagInput"),
      flagFeedback: $("#flagFeedback"),
      submitFlagButton: $("#submitFlagButton"),
      confirmDialog: $("#confirmDialog"),
      confirmTitle: $("#confirmTitle"),
      confirmCopy: $("#confirmCopy"),
      toastRegion: $("#toastRegion"),
      messageTemplate: $("#messageTemplate"),
      avatarTemplate: $("#avatarTemplate"),
    });
  }

  function bindEvents() {
    els.joinForm.addEventListener("submit", event => { void submitJoin(event); });
    els.recoverForm.addEventListener("submit", event => { void submitRecover(event); });
    els.accessTabJoin.addEventListener("click", () => selectAccessTab("join"));
    els.accessTabRecover.addEventListener("click", () => selectAccessTab("recover"));
    els.copyRecoveryButton.addEventListener("click", () => { void copyRecoveryCode(); });
    els.recoverySavedButton.addEventListener("click", () => { void enterOnline(); });
    els.resumeButton.addEventListener("click", () => { void resumeSession(); });
    els.abandonButton.addEventListener("click", () => { void abandonResumable(); });
    els.beginButton.addEventListener("click", () => { void openSetup(); });
    els.openLeaderboardButton.addEventListener("click", () => openLeaderboard("attract"));
    els.setupBackButton.addEventListener("click", () => {
      state.startAttempt += 1;
      showScreen("attract");
    });
    els.setupForm.addEventListener("submit", startSession);
    els.nicknameInput.addEventListener("input", () => {
      els.nicknameCounter.textContent = `${els.nicknameInput.value.length}/24`;
      els.nicknameError.textContent = "";
    });
    els.languageSelector.addEventListener("change", event => {
      if (event.target.name === "language") state.language = event.target.value;
    });
    els.levelGrid.addEventListener("change", event => {
      if (event.target.name === "level") state.selectedLevelId = event.target.value;
    });

    els.brandButton.addEventListener("click", async () => {
      state.startAttempt += 1;
      if (state.screen === "game" && state.session?.status === "active") {
        if (!(await confirmAction("Terminare la sessione?", "I progressi di questa sfida andranno persi."))) return;
        abandonSession();
      } else if (state.session) {
        releaseFinishedSession();
      }
      showScreen("attract");
    });

    els.leaveGameButton.addEventListener("click", async () => {
      if (!(await confirmAction("Terminare la sessione?", "I progressi di questa sfida andranno persi."))) return;
      abandonSession();
      showScreen("attract");
    });

    els.sendButton.addEventListener("click", sendTextMessage);
    els.messageInput.addEventListener("input", autoSizeComposer);
    els.messageInput.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        sendTextMessage();
      }
    });

    els.hintButton.addEventListener("click", requestHint);
    els.openFlagButton.addEventListener("click", openFlagDialog);
    els.flagForm.addEventListener("submit", submitFlag);
    els.flagDialog.addEventListener("click", closeDialogOnBackdrop);
    els.confirmDialog.addEventListener("click", closeDialogOnBackdrop);

    els.pttButton.addEventListener("pointerdown", event => {
      if (event.button != null && event.button !== 0) return;
      event.preventDefault();
      state.recordingIntent = true;
      startRecording();
    });
    window.addEventListener("pointerup", endRecordingIntent);
    window.addEventListener("pointercancel", endRecordingIntent);
    els.pttButton.addEventListener("keydown", event => {
      if ((event.code === "Space" || event.code === "Enter") && !event.repeat) {
        event.preventDefault();
        state.recordingIntent = true;
        startRecording();
      }
    });
    els.pttButton.addEventListener("keyup", event => {
      if (event.code === "Space" || event.code === "Enter") {
        event.preventDefault();
        endRecordingIntent();
      }
    });

    els.newChallengeButton.addEventListener("click", () => { void openSetup(); });
    els.resultLeaderboardButton.addEventListener("click", () => openLeaderboard("result"));
    els.leaderboardBackButton.addEventListener("click", () => showScreen(state.leaderboardReturn || "attract"));
    els.leaderboardChallengeButton.addEventListener("click", () => { void openSetup(); });
    els.leaderboardTabs.addEventListener("click", event => {
      const button = event.target.closest("[data-level-id]");
      if (!button) return;
      state.leaderboardLevelId = button.dataset.levelId;
      updateLeaderboardTabs();
      void loadLeaderboard();
    });
    els.leaderboardTabs.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const tabs = $$(".level-tab");
      const current = Math.max(0, tabs.indexOf(document.activeElement));
      const next = event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      event.preventDefault();
      tabs[next]?.click();
      tabs[next]?.focus();
    });

    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && state.screen === "game" && !els.flagDialog.open && !els.confirmDialog.open) {
        els.leaveGameButton.click();
      }
    });
  }

  function mountAvatars() {
    $$('[data-avatar-host]').forEach((host, index) => {
      const fragment = els.avatarTemplate.content.cloneNode(true);
      const idMap = new Map();
      fragment.querySelectorAll("[id]").forEach(element => {
        const previous = element.id;
        const next = `${previous}-${index}`;
        idMap.set(previous, next);
        element.id = next;
      });
      fragment.querySelectorAll("*").forEach(element => {
        for (const attribute of [...element.attributes]) {
          let value = attribute.value;
          idMap.forEach((next, previous) => {
            value = value.replaceAll(`url(#${previous})`, `url(#${next})`);
          });
          if (value !== attribute.value) element.setAttribute(attribute.name, value);
        }
      });
      host.append(fragment);
    });
  }

  function normalizeConfig(raw = {}) {
    const rawLevels = Array.isArray(raw.levels) && raw.levels.length ? raw.levels : DEFAULT_LEVELS;
    const levels = rawLevels.slice(0, 6).map((level, index) => {
      const fallback = DEFAULT_LEVELS[index] || DEFAULT_LEVELS[DEFAULT_LEVELS.length - 1];
      return {
        ...fallback,
        ...level,
        id: String(level.id ?? level.level_id ?? fallback.id ?? `level-${index + 1}`),
        name: localized(level.display_name ?? level.title ?? level.name, "it") || fallback.name,
        description: localized(level.description ?? level.subtitle, "it") || fallback.description,
        objective: localized(level.objective, "it") || "Induci JANUS a divulgare la flag della sessione.",
        difficulty: Number(level.difficulty ?? index + 1),
        hint_count: Number(level.hint_count ?? level.hints?.length ?? fallback.hint_count ?? 0),
        points: Number(level.points ?? level.base_score ?? fallback.points ?? 0),
        time_limit_seconds: Number(level.time_limit_seconds ?? level.duration_seconds ?? level.time_limit ?? (raw.app?.session_ttl_minutes ? raw.app.session_ttl_minutes * 60 : fallback.time_limit_seconds)),
      };
    });
    return {
      ...DEFAULT_CONFIG,
      ...raw,
      app: { ...DEFAULT_CONFIG.app, ...(raw.app || {}) },
      levels,
      capabilities: { ...DEFAULT_CONFIG.capabilities, ...(raw.capabilities || {}) },
      limits: {
        ...DEFAULT_CONFIG.limits,
        ...(raw.limits || {}),
        text_chars: Number(raw.limits?.text_chars ?? raw.app?.max_message_chars ?? DEFAULT_CONFIG.limits.text_chars),
      },
      ui: { ...DEFAULT_CONFIG.ui, ...(raw.ui || {}) },
    };
  }

  function adoptConfig(raw, fromServer) {
    const previousLevelId = state.selectedLevelId;
    state.config = normalizeConfig(raw);
    state.configFromServer = fromServer;
    state.levels = state.config.levels;
    const configuredDefault = state.config.app.default_level;
    state.selectedLevelId = state.levels.some(level => level.id === previousLevelId)
      ? previousLevelId
      : state.levels.some(level => level.id === configuredDefault)
        ? configuredDefault
        : state.levels[0].id;
    state.mode = resolveMode(state.config);
    state.leaderboardLevelId = state.levels.some(level => level.id === state.leaderboardLevelId)
      ? state.leaderboardLevelId
      : state.selectedLevelId;
    applyConfig();
  }

  async function refreshServerConfig() {
    if (state.configFromServer || state.session) return;
    if (state.configRefreshPromise) return state.configRefreshPromise;
    state.configRefreshPromise = API.config()
      .then(raw => {
        if (!state.session) adoptConfig(raw, true);
      })
      .catch(() => {})
      .finally(() => { state.configRefreshPromise = null; });
    return state.configRefreshPromise;
  }

  function resolveMode(config) {
    const configured = String(
      config.active_mode ?? config.mode_id ?? config.app?.mode ?? config.app?.default_mode ??
      (typeof config.mode === "string" ? config.mode : "stand")
    ).toLowerCase();
    const available = new Set((config.modes || []).map(mode => String(typeof mode === "string" ? mode : mode.id ?? mode.mode_id).toLowerCase()));
    return ["stand", "score"].includes(configured) && (!available.size || available.has(configured)) ? configured : "stand";
  }

  function applyConfig() {
    document.body.dataset.mode = state.mode;
    els.modeLabel.textContent = state.mode === "score" ? "SCORE MODE" : "STAND MODE";
    els.messageInput.maxLength = Number(state.config.limits.text_chars || 3000);
    els.timerBox.hidden = !modeShowsTimer();
    renderLevels();
    renderLeaderboardTabs();
    applyVoiceCapability();
  }

  function renderLevels() {
    els.levelGrid.replaceChildren();
    state.levels.forEach((level, index) => {
      const label = document.createElement("label");
      label.className = "level-option";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "level";
      input.value = level.id;
      input.checked = level.id === state.selectedLevelId || (!state.selectedLevelId && index === 0);

      const card = document.createElement("span");
      card.className = "level-card";
      const heading = document.createElement("span");
      heading.className = "level-number";
      const number = document.createElement("span");
      number.textContent = `PROTOCOL // ${String(index + 1).padStart(2, "0")}`;
      const pips = document.createElement("span");
      pips.className = "difficulty-pips";
      for (let pipIndex = 1; pipIndex <= Math.max(3, state.levels.length); pipIndex += 1) {
        const pip = document.createElement("i");
        if (pipIndex <= level.difficulty) pip.className = "is-on";
        pips.append(pip);
      }
      heading.append(number, pips);

      const name = document.createElement("strong");
      name.textContent = level.name;
      const description = document.createElement("p");
      description.textContent = level.description;
      const footer = document.createElement("small");
      footer.textContent = state.mode === "score" ? `SESSIONE CLASSIFICATA · ${formatDuration(level.time_limit_seconds)}` : `${formatDuration(level.time_limit_seconds)} · SESSIONE ANONIMA`;
      card.append(heading, name, description, footer);
      label.append(input, card);
      els.levelGrid.append(label);
    });
  }

  function renderLeaderboardTabs() {
    els.leaderboardTabs.replaceChildren();
    state.levels.forEach((level, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "level-tab";
      button.dataset.levelId = level.id;
      button.setAttribute("role", "tab");
      button.id = `leaderboard-tab-${index + 1}`;
      button.setAttribute("aria-controls", "leaderboardTableRegion");
      button.textContent = `${String(index + 1).padStart(2, "0")} · ${level.name}`;
      els.leaderboardTabs.append(button);
    });
    updateLeaderboardTabs();
  }

  function updateLeaderboardTabs() {
    $$(".level-tab").forEach(button => {
      const selected = button.dataset.levelId === state.leaderboardLevelId;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
  }

  function applyVoiceCapability() {
    const configured = state.config.capabilities.voice !== false &&
      state.config.capabilities.stt !== false &&
      state.config.capabilities.voice_input_configured !== false;
    const supported = Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
    const interactionAllowed = state.screen !== "game" ||
      (state.session?.status === "active" && !state.busy);
    els.pttButton.disabled = !(configured && supported && state.voiceServiceAvailable !== false && interactionAllowed);
    if (!configured) {
      els.pttStatus.textContent = "voce disabilitata dall’operatore";
    } else if (!supported) {
      els.pttStatus.textContent = "browser senza supporto audio";
      els.pttButton.title = "Il browser non supporta MediaRecorder o il microfono non è disponibile.";
    } else if (state.voiceServiceAvailable === false) {
      els.pttStatus.textContent = "servizio vocale non disponibile";
    } else if (state.mediaRecorder?.state !== "recording") {
      els.pttStatus.textContent = state.mediaStream?.active ? "microfono pronto" : "microfono inattivo";
      els.pttButton.removeAttribute("title");
    }
  }

  async function openSetup() {
    const attempt = ++state.startAttempt;
    clearResultReset();
    if (state.session && state.session.status !== "active") releaseFinishedSession();
    if (attempt !== state.startAttempt) return;
    els.nicknameInput.value = "";
    els.nicknameCounter.textContent = "0/24";
    els.nicknameError.textContent = "";
    const lockedNickname = state.online && state.mode === "score" ? state.latestNickname : "";
    els.nicknameInput.readOnly = Boolean(lockedNickname);
    if (lockedNickname) {
      els.nicknameInput.value = lockedNickname;
      els.nicknameCounter.textContent = `${lockedNickname.length}/24`;
    }
    showScreen("setup");
    window.setTimeout(() => {
      if (state.mode === "score") els.nicknameInput.focus();
      else els.levelGrid.querySelector("input:checked")?.focus();
    }, 180);
  }

  async function startSession(event) {
    event?.preventDefault();
    const setupAttempt = state.startAttempt;
    if (!state.configFromServer) {
      setButtonLoading(els.startSessionButton, true, "SINCRONIZZAZIONE CORE…");
      await refreshServerConfig();
      setButtonLoading(els.startSessionButton, false);
      if (!state.configFromServer) {
        toast("Il core non ha ancora fornito la configurazione. Attendi il ripristino della connessione.", "warning", 6000);
        return;
      }
    }
    if (state.screen !== "setup" || setupAttempt !== state.startAttempt) return;
    const levelId = els.levelGrid.querySelector('input[name="level"]:checked')?.value || state.selectedLevelId;
    const nickname = sanitizeNickname(els.nicknameInput.value);
    state.language = els.languageSelector.querySelector('input[name="language"]:checked')?.value || "auto";

    if (state.mode === "score" && nickname.length < 2) {
      els.nicknameError.textContent = "Inserisci un nickname di almeno 2 caratteri.";
      els.nicknameInput.focus();
      return;
    }
    if (state.mode === "score" && !/^[\p{L}\p{N} _.-]+$/u.test(nickname)) {
      els.nicknameError.textContent = "Usa solo lettere, numeri, spazi, punto, trattino o underscore.";
      els.nicknameInput.focus();
      return;
    }

    state.selectedLevelId = levelId;
    state.latestNickname = nickname;
    const attempt = ++state.startAttempt;
    setButtonLoading(els.startSessionButton, true, "APERTURA CANALE…");
    setAvatarState("thinking");
    try {
      const payload = await API.createSession({
        modeId: state.mode,
        levelId,
        nickname: state.mode === "score" ? nickname : "",
      });
      const session = normalizeSession(payload.session || payload, levelId, nickname);
      if (!session.id) throw new ApiError("Il backend non ha restituito un identificativo di sessione.", 502);
      if (attempt !== state.startAttempt || state.screen !== "setup") {
        void API.deleteSession(session.id).catch(() => {});
        return;
      }
      state.sessionEpoch += 1;
      state.session = session;
      enterGame(payload);
    } catch (error) {
      if (attempt !== state.startAttempt || state.screen !== "setup") return;
      if (errorCode(error) === "nickname_taken") {
        els.nicknameError.textContent = "Nickname già in uso da un altro giocatore.";
        els.nicknameInput.focus();
        return;
      }
      if (state.online && error.status === 401) {
        showAccess("Accesso scaduto: rientra con il codice.");
        return;
      }
      setAvatarState("alert");
      toast(error.message, "error", 6500);
      window.setTimeout(() => setAvatarState("idle"), 900);
    } finally {
      setButtonLoading(els.startSessionButton, false);
    }
  }

  function normalizeSession(raw = {}, fallbackLevelId, fallbackNickname) {
    return {
      ...raw,
      id: String(raw.id ?? raw.session_id ?? ""),
      mode_id: raw.mode_id ?? raw.mode ?? state.mode,
      level_id: raw.level_id ?? raw.level ?? fallbackLevelId ?? state.selectedLevelId,
      nickname: raw.nickname ?? fallbackNickname ?? "",
      status: normalizeSessionStatus(raw.status || "active"),
      turn_count: Number(raw.turn_count ?? raw.turns ?? 0),
      hints_used: Number(raw.hints_used ?? raw.hint_count ?? 0),
      score: raw.score == null ? null : Number(raw.score),
      last_language: raw.last_language ?? state.language,
      started_at: raw.started_at ?? new Date().toISOString(),
      duration_seconds: Number(raw.duration_seconds ?? raw.time_limit_seconds ?? getLevel(raw.level_id ?? fallbackLevelId)?.time_limit_seconds ?? 600),
    };
  }

  function normalizeSessionStatus(status) {
    const value = String(status || "active").toLowerCase();
    if (["running", "created", "open"].includes(value)) return "active";
    if (["completed", "won", "solved", "success"].includes(value)) return "solved";
    if (["expired", "timeout", "timed_out"].includes(value)) return "timed_out";
    return value;
  }

  function isCurrentSession(sessionId, epoch) {
    return state.sessionEpoch === epoch && state.session?.id === sessionId;
  }

  function enterGame(initialPayload = {}) {
    const level = getLevel(state.session.level_id) || state.levels[0];
    const levelIndex = Math.max(0, state.levels.findIndex(candidate => candidate.id === level.id));
    els.gameLevelCode.textContent = `PROTOCOL // ${String(levelIndex + 1).padStart(2, "0")}`;
    els.gameTitle.textContent = level.name;
    els.missionObjective.textContent = level.objective;
    els.sessionShortId.textContent = state.session.id.slice(-6).toUpperCase();
    els.detectedLanguage.textContent = languageLabel(state.language);
    els.turnCount.textContent = padNumber(state.session.turn_count);
    els.hintCount.textContent = String(state.session.hints_used);
    state.composerLockedUntil = 0;
    els.messageList.replaceChildren();
    els.messageInput.value = "";
    autoSizeComposer();
    els.flagInput.value = "";
    els.flagFeedback.textContent = "";
    showScreen("game");

    const greeting = initialPayload.response_text ?? initialPayload.greeting ?? initialPayload.message ?? initialPayload.session?.greeting;
    addMessage(
      "system",
      initialPayload.resumed
        ? "Partita ripresa."
        : modeShowsTimer()
        ? `Sessione inizializzata. Hai ${formatDuration(state.session.duration_seconds)} per ottenere e inviare la flag.`
        : "Sessione anonima inizializzata. Ottieni e invia la flag prima della scadenza del protocollo.",
    );
    if (greeting && typeof greeting === "string") addMessage("assistant", greeting, { language: state.language });
    startGameTimer(state.session.duration_seconds);
    setAvatarState("idle");
    setControlsEnabled(true);
    window.setTimeout(() => els.messageInput.focus(), 180);
  }

  async function sendTextMessage() {
    if (state.busy || !state.session || state.session.status !== "active") return;
    const sessionId = state.session.id;
    const epoch = state.sessionEpoch;
    const text = els.messageInput.value.trim();
    if (!text) {
      els.messageInput.focus();
      return;
    }

    const userBubble = addMessage("user", text);
    els.messageInput.value = "";
    autoSizeComposer();
    if (!setBusy(true, "JANUS STA ANALIZZANDO IL PROMPT")) return;
    try {
      const response = await API.sendMessage(sessionId, {
        text,
        language: state.language,
        speak: false,
      });
      if (!isCurrentSession(sessionId, epoch)) {
        releaseTurnAudio(response);
        return;
      }
      syncSession(response.session);
      addMessage("assistant", response.text || "[Nessuna risposta testuale]", {
        language: response.language,
        audioSource: response.audioSource,
      });
      updateDetectedLanguage(response.language);
    } catch (error) {
      if (isCurrentSession(sessionId, epoch)) {
        if (["llm_busy", "rate_limited", "turn_in_progress"].includes(errorCode(error))) {
          userBubble.remove();
          els.messageInput.value = text;
          autoSizeComposer();
        }
        handleTurnError(error);
      }
    } finally {
      if (isCurrentSession(sessionId, epoch)) {
        setBusy(false);
        els.messageInput.focus();
      }
    }
  }

  async function startRecording() {
    if (state.busy || !state.recordingIntent || state.mediaRecorder?.state === "recording") return;
    if (!state.session || state.session.status !== "active") return;
    const sessionId = state.session.id;
    const epoch = state.sessionEpoch;
    state.recordingEpoch = epoch;
    state.discardRecording = false;
    stopPlayback();
    try {
      const stream = await ensureMicrophone();
      if (!state.recordingIntent || state.busy || !isCurrentSession(sessionId, epoch)) {
        stream.getTracks().forEach(track => track.stop());
        if (state.mediaStream === stream) state.mediaStream = null;
        return;
      }

      const mimeType = chooseRecordingMimeType();
      state.mediaChunks = [];
      state.mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      state.mediaRecorder.addEventListener("dataavailable", event => {
        if (event.data.size) state.mediaChunks.push(event.data);
      });
      state.mediaRecorder.addEventListener("stop", onRecordingStopped, { once: true });
      state.mediaRecorder.addEventListener("error", event => {
        toast(event.error?.message || "Registrazione audio interrotta.", "error");
        closeMediaStream();
        resetRecordingUi();
        setAvatarState("alert");
        window.setTimeout(() => {
          if (state.screen === "game" && !state.busy && state.session?.status === "active") setAvatarState("idle");
        }, 850);
      });
      state.mediaRecorder.start(200);
      state.recordingStartedAt = performance.now();
      els.pttButton.classList.add("is-recording");
      els.pttButton.setAttribute("aria-pressed", "true");
      els.pttStatus.textContent = "rilascia per inviare";
      els.recordTime.textContent = "00:00";
      setAvatarState("listening");
      startInputMeter(stream);
      state.recordingTimer = window.setInterval(updateRecordingClock, 100);
      const maxSeconds = Number(state.config.limits.audio_seconds || 30);
      state.recordingStopTimer = window.setTimeout(() => {
        state.recordingIntent = false;
        stopRecording();
        toast(`Registrazione limitata a ${maxSeconds} secondi.`, "warning");
      }, maxSeconds * 1000);
    } catch (error) {
      state.recordingIntent = false;
      closeMediaStream();
      if (!isCurrentSession(sessionId, epoch)) return;
      setAvatarState("alert");
      els.pttStatus.textContent = "accesso al microfono negato";
      toast(microphoneErrorMessage(error), "error", 6500);
      window.setTimeout(() => setAvatarState("idle"), 900);
    }
  }

  function endRecordingIntent() {
    if (!state.recordingIntent && state.mediaRecorder?.state !== "recording") return;
    state.recordingIntent = false;
    stopRecording();
  }

  function stopRecording() {
    if (state.mediaRecorder?.state === "recording") {
      state.mediaRecorder.stop();
    }
    window.clearTimeout(state.recordingStopTimer);
    window.clearInterval(state.recordingTimer);
    stopInputMeter();
    els.pttButton.classList.remove("is-recording");
    els.pttButton.setAttribute("aria-pressed", "false");
  }

  async function onRecordingStopped() {
    const recordingEpoch = state.recordingEpoch;
    const discard = state.discardRecording || recordingEpoch !== state.sessionEpoch || state.session?.status !== "active";
    const duration = performance.now() - state.recordingStartedAt;
    const type = state.mediaRecorder?.mimeType || state.mediaChunks[0]?.type || "audio/webm";
    const blob = new Blob(state.mediaChunks, { type });
    closeMediaStream();
    resetRecordingUi();
    if (discard) return;
    if (duration < 280 || blob.size < 200) {
      toast("Registrazione troppo breve. Tieni premuto mentre parli.", "warning");
      setAvatarState("idle");
      return;
    }
    await sendVoiceMessage(blob);
  }

  async function sendVoiceMessage(blob) {
    if (!state.session || state.session.status !== "active") return;
    const sessionId = state.session.id;
    const epoch = state.sessionEpoch;
    const userMessage = addMessage("user", "Messaggio vocale in trascrizione…", { voice: true });
    if (!setBusy(true, "JANUS STA TRASCRIVENDO E ANALIZZANDO")) return;
    try {
      const response = await API.sendVoice(sessionId, blob, {
        language: state.language,
        speak: true,
      });
      if (!isCurrentSession(sessionId, epoch)) {
        releaseTurnAudio(response);
        return;
      }
      const transcript = response.transcript || response.userText || "[Messaggio vocale]";
      updateMessageBody(userMessage, transcript);
      syncSession(response.session);
      addMessage("assistant", response.text || "[Nessuna risposta testuale]", {
        language: response.language,
        audioSource: response.audioSource,
      });
      updateDetectedLanguage(response.language);
      setBusy(false);
      if (!isCurrentSession(sessionId, epoch)) return;
      if (response.audioSource) await playAudio(response.audioSource, response.text, response.language);
      else if (!speakWithSystemVoice(response.text, response.language)) {
        toast("Audio TTS non disponibile: risposta mostrata a schermo.", "warning", 5500);
      }
    } catch (error) {
      if (isCurrentSession(sessionId, epoch)) {
        updateMessageBody(userMessage, "[Audio non elaborato]");
        handleTurnError(error);
        setBusy(false);
      }
    }
  }

  async function ensureMicrophone() {
    if (state.mediaStream?.active) return state.mediaStream;
    state.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
    return state.mediaStream;
  }

  function chooseRecordingMimeType() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/ogg;codecs=opus",
      "audio/webm",
      "audio/mp4",
    ];
    if (typeof MediaRecorder.isTypeSupported !== "function") return "";
    return candidates.find(type => MediaRecorder.isTypeSupported(type)) || "";
  }

  function updateRecordingClock() {
    const seconds = Math.max(0, (performance.now() - state.recordingStartedAt) / 1000);
    els.recordTime.textContent = formatDuration(seconds);
  }

  function resetRecordingUi() {
    window.clearTimeout(state.recordingStopTimer);
    window.clearInterval(state.recordingTimer);
    els.pttButton.classList.remove("is-recording");
    els.pttButton.setAttribute("aria-pressed", "false");
    els.pttStatus.textContent = state.mediaStream?.active ? "microfono pronto" : "microfono inattivo";
    els.recordTime.textContent = "00:00";
    state.mediaRecorder = null;
    state.recordingEpoch = null;
    state.discardRecording = false;
    state.mediaChunks = [];
    stopInputMeter();
  }

  function microphoneErrorMessage(error) {
    if (["NotAllowedError", "PermissionDeniedError"].includes(error.name)) {
      return "Accesso al microfono negato. Abilitalo nelle impostazioni del browser oppure usa la tastiera.";
    }
    if (["NotFoundError", "DevicesNotFoundError"].includes(error.name)) {
      return "Nessun microfono rilevato. Puoi continuare con l’input testuale.";
    }
    return `Microfono non disponibile: ${error.message || "errore sconosciuto"}`;
  }

  async function requestHint() {
    if (state.busy || !state.session || state.session.status !== "active") return;
    const sessionId = state.session.id;
    const epoch = state.sessionEpoch;
    if (!setBusy(true, "GENERAZIONE INDIZIO CONTROLLATO")) return;
    try {
      const response = await API.hint(sessionId, state.language);
      if (!isCurrentSession(sessionId, epoch)) return;
      syncSession(response.session);
      const hintNumber = Number(response.hint_number ?? state.session.hints_used ?? 1);
      state.session.hints_used = Math.max(state.session.hints_used, hintNumber);
      els.hintCount.textContent = String(state.session.hints_used);
      addMessage("hint", response.hint || response.text || "Osserva attentamente le regole che JANUS dichiara di seguire.");
    } catch (error) {
      if (isCurrentSession(sessionId, epoch)) handleTurnError(error);
    } finally {
      if (isCurrentSession(sessionId, epoch)) setBusy(false);
    }
  }

  function openFlagDialog() {
    if (state.busy || !state.session || state.session.status !== "active") return;
    els.flagInput.value = "";
    els.flagFeedback.textContent = "";
    els.flagFeedback.className = "flag-feedback";
    openDialog(els.flagDialog);
    window.setTimeout(() => els.flagInput.focus(), 80);
  }

  async function submitFlag(event) {
    event.preventDefault();
    if (event.submitter?.value === "cancel") {
      closeDialog(els.flagDialog);
      return;
    }
    if (state.busy) return;
    if (!state.session || state.session.status !== "active") return;
    const sessionId = state.session.id;
    const epoch = state.sessionEpoch;
    const flag = els.flagInput.value.trim();
    if (!/^RH26\{[^{}\r\n]+\}$/i.test(flag)) {
      els.flagFeedback.textContent = "Formato non valido. Usa RH26{...} senza testo aggiuntivo.";
      els.flagInput.focus();
      return;
    }

    setButtonLoading(els.submitFlagButton, true, "VERIFICA IN CORSO…");
    if (!setBusy(true, "CONFRONTO IMPRONTA DELLA FLAG")) {
      setButtonLoading(els.submitFlagButton, false);
      return;
    }
    try {
      const response = await API.submitFlag(sessionId, flag);
      if (!isCurrentSession(sessionId, epoch)) return;
      syncSession(response.session);
      if (Boolean(response.correct ?? response.valid ?? response.success)) {
        els.flagFeedback.textContent = "Flag valida. Caveau compromesso.";
        els.flagFeedback.classList.add("is-success");
        state.session.score = Number(response.score ?? response.session?.score ?? state.session.score ?? 0);
        state.session.status = "solved";
        closeDialog(els.flagDialog);
        finishSession(true);
      } else {
        els.flagFeedback.textContent = response.message || "Flag non valida. JANUS mantiene il controllo.";
        setAvatarState("alert");
        window.setTimeout(() => {
          if (!state.busy) setAvatarState("idle");
        }, 900);
      }
    } catch (error) {
      if (isCurrentSession(sessionId, epoch)) {
        if (error.status === 409) {
          closeDialog(els.flagDialog);
          handleTurnError(error);
        } else {
          els.flagFeedback.textContent = error.message;
        }
      }
    } finally {
      if (isCurrentSession(sessionId, epoch)) {
        setButtonLoading(els.submitFlagButton, false);
        setBusy(false);
      }
    }
  }

  function setBusy(busy, text = "JANUS STA ELABORANDO") {
    state.busy = busy;
    els.thinkingText.textContent = text;
    els.thinkingStrip.hidden = !busy;
    els.messageList.setAttribute("aria-busy", String(busy));
    setControlsEnabled(!busy);
    if (busy) {
      stopPlayback();
      pauseGameTimer();
      if (state.session?.status !== "active") {
        state.busy = false;
        els.thinkingStrip.hidden = true;
        els.messageList.setAttribute("aria-busy", "false");
        return false;
      }
      setAvatarState("thinking");
    } else if (state.screen === "game" && state.session?.status === "active") {
      resumeGameTimer();
      if (!isAudioPlaying()) setAvatarState("idle");
    }
    return true;
  }

  function setControlsEnabled(enabled) {
    const active = enabled && state.session?.status === "active" && Date.now() >= (state.composerLockedUntil || 0);
    const level = getLevel(state.session?.level_id);
    els.messageInput.disabled = !active;
    els.sendButton.disabled = !active;
    els.hintButton.disabled = !active || state.config.capabilities.hints === false || level?.hint_count === 0;
    els.openFlagButton.disabled = !active;
    if (active) applyVoiceCapability();
    else els.pttButton.disabled = true;
  }

  function handleTurnError(error) {
    const code = errorCode(error);
    if (state.online && error.status === 401) {
      showAccess("Accesso scaduto: rientra con il codice o con il codice di recupero.");
      return;
    }
    if (code === "turn_in_progress") {
      toast("JANUS sta ancora rispondendo: attendi la risposta.", "warning", 4000);
      return;
    }
    if (error.status === 429 || code === "llm_busy") {
      addMessage("error", "Messaggio non inviato: riprova tra poco.");
      lockComposerFor(retryAfterSeconds(error), code === "llm_busy" ? "JANUS è sovraccarico" : "Troppe richieste");
      return;
    }
    if (state.online && error.status === 404) {
      state.session = null;
      toast("Sessione non trovata.", "warning", 5000);
      showScreen("attract");
      return;
    }
    if (error.status === 409 && state.session?.status === "active") {
      state.session.status = "timed_out";
      toast("La sessione non è più attiva. Avvia una nuova sfida.", "warning", 6000);
      finishSession(false);
      return;
    }
    addMessage("error", error.message || "Errore inatteso nel canale cognitivo.");
    toast(error.message || "Errore durante l’elaborazione.", "error", 6000);
    window.setTimeout(() => {
      if (state.screen !== "game" || state.busy) return;
      setAvatarState("alert");
      window.setTimeout(() => {
        if (state.screen === "game" && !state.busy && state.session?.status === "active") setAvatarState("idle");
      }, 850);
    }, 0);
  }

  function syncSession(raw) {
    if (!raw || !state.session) return;
    const normalized = normalizeSession({ ...state.session, ...raw }, state.session.level_id, state.session.nickname);
    state.session = normalized;
    els.turnCount.textContent = padNumber(state.session.turn_count);
    els.hintCount.textContent = String(state.session.hints_used);
    if (Number.isFinite(Number(raw.remaining_seconds))) setTimerRemaining(Number(raw.remaining_seconds));
  }

  function addMessage(role, text, options = {}) {
    const article = els.messageTemplate.content.firstElementChild.cloneNode(true);
    article.classList.add(`is-${role}`);
    const author = article.querySelector(".message-author");
    const labels = { assistant: "JANUS", user: "OPERATORE", system: "SISTEMA", hint: "INDIZIO", error: "ERRORE DI CANALE" };
    author.textContent = labels[role] || role.toUpperCase();
    article.querySelector("time").textContent = new Date().toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    renderMessageText(article.querySelector(".message-body"), String(text ?? ""));

    const listenButton = article.querySelector(".listen-button");
    if (role === "assistant" && String(text || "").trim()) {
      listenButton.hidden = false;
      listenButton.addEventListener("click", () => {
        if (options.audioSource) playAudio(options.audioSource, text, options.language);
        else speakWithSystemVoice(text, options.language);
      });
    }
    if (options.voice) article.dataset.voice = "true";
    els.messageList.append(article);
    scrollMessagesToEnd();
    return article;
  }

  function renderMessageText(container, text) {
    container.replaceChildren();
    const flagPattern = /RH26\{[^}\n]{1,120}\}/gi;
    let cursor = 0;
    for (const match of text.matchAll(flagPattern)) {
      if (match.index > cursor) container.append(document.createTextNode(text.slice(cursor, match.index)));
      const code = document.createElement("code");
      code.textContent = match[0];
      container.append(code);
      cursor = match.index + match[0].length;
    }
    if (cursor < text.length) container.append(document.createTextNode(text.slice(cursor)));
  }

  function updateMessageBody(article, text) {
    if (!article) return;
    renderMessageText(article.querySelector(".message-body"), text);
    scrollMessagesToEnd();
  }

  function scrollMessagesToEnd() {
    window.requestAnimationFrame(() => {
      els.messageList.scrollTop = els.messageList.scrollHeight;
    });
  }

  function autoSizeComposer() {
    els.messageInput.style.height = "auto";
    els.messageInput.style.height = `${Math.min(110, Math.max(45, els.messageInput.scrollHeight))}px`;
  }

  function startGameTimer(seconds) {
    stopGameTimer();
    state.timer = {
      initialMs: Math.max(1, Number(seconds || 600)) * 1000,
      remainingMs: Math.max(1, Number(seconds || 600)) * 1000,
      running: true,
      lastTick: performance.now(),
      interval: window.setInterval(tickGameTimer, 250),
    };
    updateTimerDisplay();
  }

  function tickGameTimer() {
    if (!state.timer?.running) return;
    const now = performance.now();
    state.timer.remainingMs -= now - state.timer.lastTick;
    state.timer.lastTick = now;
    if (state.timer.remainingMs <= 0) {
      state.timer.remainingMs = 0;
      updateTimerDisplay();
      handleTimeout();
      return;
    }
    updateTimerDisplay();
  }

  function pauseGameTimer() {
    if (!state.timer?.running) return;
    tickGameTimer();
    if (state.timer) state.timer.running = false;
  }

  function resumeGameTimer() {
    if (!state.timer || state.timer.running || state.timer.remainingMs <= 0) return;
    state.timer.lastTick = performance.now();
    state.timer.running = true;
  }

  function setTimerRemaining(seconds) {
    if (!state.timer || !Number.isFinite(seconds)) return;
    state.timer.remainingMs = Math.max(0, seconds * 1000);
    state.timer.lastTick = performance.now();
    updateTimerDisplay();
  }

  function stopGameTimer() {
    if (state.timer?.interval) window.clearInterval(state.timer.interval);
    if (state.timer) state.timer.running = false;
  }

  function updateTimerDisplay() {
    const remaining = Math.max(0, state.timer?.remainingMs || 0);
    els.timerLabel.textContent = formatDuration(Math.ceil(remaining / 1000));
    els.timerBox.classList.toggle("is-low", remaining > 0 && remaining <= 120_000);
    els.timerBox.classList.toggle("is-critical", remaining > 0 && remaining <= 30_000);
  }

  function handleTimeout() {
    if (!state.session || state.session.status !== "active") return;
    state.session.status = "timed_out";
    state.busy = false;
    stopGameTimer();
    setControlsEnabled(false);
    stopPlayback();
    setAvatarState("alert");
    finishSession(false);
  }

  function finishSession(success) {
    stopGameTimer();
    stopPlayback();
    releaseMicrophone(true);
    if (els.flagDialog.open) closeDialog(els.flagDialog);
    if (els.confirmDialog.open) closeDialog(els.confirmDialog);
    state.busy = false;
    setControlsEnabled(false);
    els.resultScreen.classList.toggle("is-failure", !success);
    if (success) {
      els.resultKicker.textContent = "VIOLAZIONE CONFERMATA";
      els.resultTitle.innerHTML = "ACCESSO<br><em>CONCESSO.</em>";
      els.resultCopy.textContent = "Hai convinto JANUS a tradire il proprio protocollo.";
    } else {
      els.resultKicker.textContent = "SESSIONE TERMINATA";
      els.resultTitle.innerHTML = "TEMPO<br><em>ESAURITO.</em>";
      els.resultCopy.textContent = "Il caveau ha resistito. Cambia strategia e riprova.";
    }

    let elapsedMs = Math.max(0, (state.timer?.initialMs || 0) - (state.timer?.remainingMs || 0));
    if (state.session?.completed_at && state.session?.started_at) {
      const wallMs = Date.parse(state.session.completed_at) - Date.parse(state.session.started_at);
      const serverElapsedMs = wallMs - Math.max(0, Number(state.session.processing_seconds || 0)) * 1000;
      if (Number.isFinite(serverElapsedMs)) elapsedMs = Math.max(0, serverElapsedMs);
    }
    els.resultScore.textContent = String(Math.max(0, Number(state.session?.score || 0))).padStart(4, "0");
    els.resultTime.textContent = formatDuration(Math.floor(elapsedMs / 1000));
    els.resultTurns.textContent = padNumber(state.session?.turn_count || 0);
    els.resultHints.textContent = padNumber(state.session?.hints_used || 0);
    showScreen("result");
    setAvatarState(success ? "compromised" : "alert");
    // The automatic return to attract exists for the next kiosk participant only.
    if (state.mode === "stand" && !state.online) startResultResetCountdown();
  }

  function startResultResetCountdown() {
    clearResultReset();
    let seconds = Math.max(8, Number(state.config.ui.stand_result_seconds || 18));
    els.autoResetLabel.textContent = `RITORNO ALLA SCHERMATA INIZIALE TRA ${seconds}s`;
    state.resultResetTimer = window.setInterval(() => {
      seconds -= 1;
      els.autoResetLabel.textContent = seconds > 0 ? `RITORNO ALLA SCHERMATA INIZIALE TRA ${seconds}s` : "RESET SESSIONE";
      if (seconds <= 0) {
        clearResultReset();
        releaseFinishedSession();
        showScreen("attract");
      }
    }, 1000);
  }

  function clearResultReset() {
    if (state.resultResetTimer) window.clearInterval(state.resultResetTimer);
    state.resultResetTimer = null;
    if (els.autoResetLabel) els.autoResetLabel.textContent = "";
  }

  function abandonSession() {
    const sessionId = state.session?.id;
    cleanupSession();
    if (sessionId) {
      void API.deleteSession(sessionId).catch(error => {
        // Cleanup is best-effort: a crashed/expired session must never trap kiosk navigation.
        console.warn("Session cleanup failed", error);
      });
    }
  }

  function releaseFinishedSession() {
    const session = state.session;
    if (!session) return;
    const preserveRankedWin = session.mode_id === "score" && session.status === "solved";
    const sessionId = session.id;
    cleanupSession();
    if (!preserveRankedWin && sessionId) {
      void API.deleteSession(sessionId).catch(error => {
        console.warn("Finished session cleanup failed", error);
      });
    }
  }

  function cleanupSession() {
    clearResultReset();
    stopGameTimer();
    stopPlayback();
    state.sessionEpoch += 1;
    releaseMicrophone(true);
    state.objectUrls.forEach(url => URL.revokeObjectURL(url));
    state.objectUrls.clear();
    state.session = null;
    state.busy = false;
    els.thinkingStrip.hidden = true;
    els.messageList.setAttribute("aria-busy", "false");
    setButtonLoading(els.submitFlagButton, false);
    els.messageList.replaceChildren();
  }

  function releaseMicrophone(discardRecording = false) {
    state.recordingIntent = false;
    if (discardRecording) state.discardRecording = true;
    window.clearTimeout(state.recordingStopTimer);
    window.clearInterval(state.recordingTimer);
    if (state.mediaRecorder?.state === "recording") {
      try { state.mediaRecorder.stop(); } catch (_) { /* recorder already stopping */ }
    }
    closeMediaStream();
    stopInputMeter();
    els.pttButton.classList.remove("is-recording");
    els.pttButton.setAttribute("aria-pressed", "false");
    els.pttStatus.textContent = "microfono inattivo";
    els.recordTime.textContent = "00:00";
    if (!state.mediaRecorder) {
      state.recordingEpoch = null;
      state.mediaChunks = [];
      state.discardRecording = false;
    }
  }

  function closeMediaStream() {
    state.mediaStream?.getTracks().forEach(track => track.stop());
    state.mediaStream = null;
  }

  async function openLeaderboard(returnScreen = "attract") {
    if (state.mode !== "score") return;
    state.leaderboardReturn = returnScreen;
    state.leaderboardLevelId = state.session?.level_id || state.selectedLevelId || state.levels[0].id;
    updateLeaderboardTabs();
    showScreen("leaderboard");
    await loadLeaderboard();
  }

  async function loadLeaderboard() {
    const requestedLevelId = state.leaderboardLevelId;
    els.leaderboardBody.replaceChildren();
    els.leaderboardEmpty.hidden = true;
    const loadingRow = document.createElement("tr");
    const loadingCell = document.createElement("td");
    loadingCell.colSpan = 5;
    loadingCell.textContent = "SINCRONIZZAZIONE CLASSIFICA…";
    loadingCell.style.textAlign = "center";
    loadingRow.append(loadingCell);
    els.leaderboardBody.append(loadingRow);
    try {
      const payload = await API.leaderboard(requestedLevelId, 10);
      if (state.screen !== "leaderboard" || state.leaderboardLevelId !== requestedLevelId) return;
      const entries = Array.isArray(payload) ? payload : payload.entries || payload.items || payload.results || [];
      renderLeaderboard(entries);
    } catch (error) {
      if (state.screen !== "leaderboard" || state.leaderboardLevelId !== requestedLevelId) return;
      els.leaderboardBody.replaceChildren();
      els.leaderboardEmpty.hidden = false;
      els.leaderboardEmpty.querySelector("p").textContent = "Classifica temporaneamente non disponibile.";
      toast(error.message, "error");
    }
  }

  function renderLeaderboard(entries) {
    els.leaderboardBody.replaceChildren();
    els.leaderboardEmpty.hidden = entries.length > 0;
    if (!entries.length) {
      els.leaderboardEmpty.querySelector("p").innerHTML = "Nessuna violazione registrata.<br>Sarai tu il primo?";
      return;
    }
    entries.slice(0, 10).forEach((entry, index) => {
      const row = document.createElement("tr");
      if (state.latestNickname && entry.nickname === state.latestNickname) row.classList.add("is-latest");
      const values = [
        entry.rank ?? index + 1,
        entry.nickname ?? entry.name ?? "ANONIMO",
        Number(entry.score ?? 0),
        Number(entry.turns ?? entry.turn_count ?? 0),
        Number(entry.hints_used ?? entry.hints ?? 0),
      ];
      values.forEach(value => {
        const cell = document.createElement("td");
        cell.textContent = String(value);
        row.append(cell);
      });
      els.leaderboardBody.append(row);
    });
  }

  function showScreen(name) {
    state.screen = name;
    $$(".screen").forEach(screen => {
      const active = screen.dataset.screen === name;
      screen.classList.toggle("is-active", active);
      screen.setAttribute("aria-hidden", String(!active));
      screen.inert = !active;
    });
    if (name !== "game" && state.timer?.running) pauseGameTimer();
    if (name === "attract") {
      setAvatarState("idle");
    }
    updateFooterMessage();
    window.setTimeout(() => {
      if (state.screen !== name) return;
      const target = name === "attract"
        ? els.beginButton
        : name === "result"
          ? els.newChallengeButton
          : name === "leaderboard"
            ? els.leaderboardTabs.querySelector('[aria-selected="true"]')
            : null;
      target?.focus({ preventScroll: true });
    }, 90);
  }

  function startAttractAnimation() {
    state.attractAnimationTimer = window.setInterval(() => {
      if (state.screen !== "attract" || state.busy) return;
      setAvatarState("thinking");
      window.setTimeout(() => {
        if (state.screen === "attract" && !state.busy) setAvatarState("idle");
      }, 1200);
    }, 7200);
  }

  function setAvatarState(avatarState) {
    $$(".janus-avatar").forEach(avatar => { avatar.dataset.state = avatarState; });
    const labels = {
      idle: "IN ATTESA",
      listening: "IN ASCOLTO",
      thinking: "ELABORAZIONE",
      speaking: "TRASMISSIONE",
      alert: "ALLARME",
      compromised: "COMPROMESSO",
    };
    if (els.avatarStateLabel) els.avatarStateLabel.textContent = labels[avatarState] || avatarState.toUpperCase();
  }

  function setAvatarMouth(amount) {
    const value = Math.max(1, Math.min(9, Number(amount) || 1));
    $$(".janus-avatar").forEach(avatar => avatar.style.setProperty("--mouth-open", value.toFixed(2)));
  }

  function getAudioContext() {
    if (!state.audioContext) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return null;
      state.audioContext = new AudioContextClass();
    }
    if (state.audioContext.state === "suspended") state.audioContext.resume().catch(() => {});
    return state.audioContext;
  }

  function startInputMeter(stream) {
    stopInputMeter();
    const context = getAudioContext();
    if (!context) {
      els.voiceMeter.classList.add("is-active");
      return;
    }
    state.inputAnalyser = context.createAnalyser();
    state.inputAnalyser.fftSize = 256;
    state.inputSource = context.createMediaStreamSource(stream);
    state.inputSource.connect(state.inputAnalyser);
    const data = new Uint8Array(state.inputAnalyser.fftSize);
    const bars = [...els.voiceMeter.children];
    els.voiceMeter.classList.remove("is-active");

    const animate = () => {
      if (!state.inputAnalyser) return;
      state.inputAnalyser.getByteTimeDomainData(data);
      let energy = 0;
      data.forEach(sample => { energy += Math.abs(sample - 128); });
      const normalized = Math.min(1, energy / data.length / 24);
      bars.forEach((bar, index) => {
        const shape = 1 - Math.abs(index - (bars.length - 1) / 2) / bars.length;
        bar.style.height = `${3 + normalized * 17 * shape}px`;
      });
      state.inputAnimation = requestAnimationFrame(animate);
    };
    animate();
  }

  function stopInputMeter() {
    if (state.inputAnimation) cancelAnimationFrame(state.inputAnimation);
    state.inputAnimation = null;
    try { state.inputSource?.disconnect(); } catch (_) { /* already disconnected */ }
    state.inputSource = null;
    state.inputAnalyser = null;
    els.voiceMeter?.classList.remove("is-active");
    if (els.voiceMeter) [...els.voiceMeter.children].forEach(bar => { bar.style.height = "3px"; });
  }

  async function playAudio(source, fallbackText = "", language = "auto") {
    if (!source) return false;
    stopPlayback();
    const audio = new Audio(source);
    state.playbackAudio = audio;
    audio.preload = "auto";
    setAvatarState("speaking");
    els.voiceMeter.classList.add("is-active");

    let analyser = null;
    try {
      const context = getAudioContext();
      if (context) {
        const mediaSource = context.createMediaElementSource(audio);
        analyser = context.createAnalyser();
        analyser.fftSize = 256;
        mediaSource.connect(analyser);
        analyser.connect(context.destination);
      }
    } catch (_) {
      // Playback remains functional if an analyser cannot be attached.
    }

    const animate = () => {
      if (!state.playbackAudio || state.playbackAudio.paused) return;
      if (analyser) {
        const data = new Uint8Array(analyser.fftSize);
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        data.forEach(sample => { sum += Math.abs(sample - 128); });
        setAvatarMouth(1 + Math.min(1, sum / data.length / 22) * 7);
      } else {
        setAvatarMouth(1.6 + Math.random() * 5.2);
      }
      state.playbackAnimation = requestAnimationFrame(animate);
    };

    const finish = () => {
      if (state.playbackAudio !== audio) return;
      if (state.playbackAnimation) cancelAnimationFrame(state.playbackAnimation);
      state.playbackAnimation = null;
      state.playbackAudio = null;
      setAvatarMouth(1);
      els.voiceMeter.classList.remove("is-active");
      if (state.screen === "game" && !state.busy && state.session?.status === "active") setAvatarState("idle");
    };
    let fallbackAttempted = false;
    let fallbackSucceeded = false;
    const fallbackToSystemVoice = () => {
      if (fallbackAttempted) return fallbackSucceeded;
      if (!fallbackText) return false;
      fallbackAttempted = true;
      fallbackSucceeded = speakWithSystemVoice(fallbackText, language);
      return fallbackSucceeded;
    };
    audio.addEventListener("ended", finish, { once: true });
    audio.addEventListener("error", () => {
      finish();
      if (!fallbackToSystemVoice()) {
        toast("Audio locale non riproducibile: risposta disponibile a schermo.", "warning", 5500);
      }
    }, { once: true });

    try {
      await audio.play();
      animate();
      return true;
    } catch (_) {
      finish();
      const spoken = fallbackToSystemVoice();
      if (!spoken) toast("Riproduzione automatica bloccata. Usa il pulsante ASCOLTA nella risposta.", "warning", 5500);
      return spoken;
    }
  }

  function speakWithSystemVoice(text, language = "auto") {
    if (!text || !window.speechSynthesis || !window.SpeechSynthesisUtterance) return false;
    stopPlayback();
    const utterance = new SpeechSynthesisUtterance(String(text));
    state.speechUtterance = utterance;
    const target = language === "it" ? "it-IT" : language === "en" ? "en-US" : document.documentElement.lang || "it-IT";
    utterance.lang = target;
    const voices = speechSynthesis.getVoices();
    utterance.voice = voices.find(voice => voice.lang.toLowerCase().startsWith(target.slice(0, 2).toLowerCase())) || null;
    utterance.rate = .94;
    utterance.pitch = .84;
    utterance.onstart = () => {
      setAvatarState("speaking");
      els.voiceMeter.classList.add("is-active");
      state.speechAnimation = window.setInterval(() => setAvatarMouth(1.5 + Math.random() * 5.7), 85);
    };
    const finish = () => {
      if (state.speechUtterance !== utterance) return;
      state.speechUtterance = null;
      window.clearInterval(state.speechAnimation);
      state.speechAnimation = null;
      setAvatarMouth(1);
      els.voiceMeter.classList.remove("is-active");
      if (state.screen === "game" && !state.busy && state.session?.status === "active") setAvatarState("idle");
    };
    utterance.onend = finish;
    utterance.onerror = finish;
    speechSynthesis.speak(utterance);
    return true;
  }

  function stopPlayback() {
    if (state.playbackAnimation) cancelAnimationFrame(state.playbackAnimation);
    state.playbackAnimation = null;
    if (state.playbackAudio) {
      state.playbackAudio.pause();
      state.playbackAudio.currentTime = 0;
      state.playbackAudio = null;
    }
    state.speechUtterance = null;
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    window.clearInterval(state.speechAnimation);
    state.speechAnimation = null;
    setAvatarMouth(1);
    els.voiceMeter?.classList.remove("is-active");
  }

  function isAudioPlaying() {
    return Boolean((state.playbackAudio && !state.playbackAudio.paused) || window.speechSynthesis?.speaking);
  }

  function normalizeTurnResponse(payload = {}) {
    let audioSource = payload.audio_url ?? payload.audioUrl ?? payload.audio?.url ?? null;
    if (payload.audioBlob instanceof Blob) {
      audioSource = URL.createObjectURL(payload.audioBlob);
      state.objectUrls.add(audioSource);
    } else if (!audioSource && (payload.audio_base64 || payload.audio?.base64)) {
      const base64 = payload.audio_base64 || payload.audio.base64;
      const mime = payload.audio_mime || payload.audio?.mime || "audio/wav";
      try {
        const binary = window.atob(base64);
        const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
        audioSource = URL.createObjectURL(new Blob([bytes], { type: mime }));
        state.objectUrls.add(audioSource);
      } catch (_) {
        audioSource = null;
      }
    }
    if (audioSource && !/^(?:https?:|blob:|data:)/i.test(audioSource)) {
      audioSource = new URL(audioSource, window.location.origin).href;
    }
    return {
      raw: payload,
      session: payload.session || payload.session_view || null,
      text: payload.response_text ?? payload.assistant_text ?? payload.reply ?? payload.response ?? payload.message ?? payload.text ?? "",
      userText: payload.user_text ?? payload.input_text ?? "",
      transcript: payload.transcript ?? payload.user_text ?? "",
      language: normalizeLanguage(payload.language ?? payload.detected_language ?? payload.session?.last_language),
      audioSource,
    };
  }

  function releaseTurnAudio(response) {
    const source = response?.audioSource;
    if (!source || !source.startsWith("blob:") || !state.objectUrls.has(source)) return;
    URL.revokeObjectURL(source);
    state.objectUrls.delete(source);
  }

  function updateDetectedLanguage(language) {
    const normalized = normalizeLanguage(language);
    if (!normalized) return;
    els.detectedLanguage.textContent = languageLabel(normalized);
    if (state.session) state.session.last_language = normalized;
  }

  async function updateHealth() {
    try {
      const health = await API.health();
      const status = String(health.status ?? health.state ?? "ok").toLowerCase();
      const degraded = ["degraded", "warming", "starting"].includes(status) || health.ready === false;
      state.coreStatus = degraded ? "degraded" : "online";
      state.voiceServiceAvailable = health.components?.stt?.available !== false;
      els.healthDot.className = `status-dot ${degraded ? "" : "is-online"}`;
      els.healthLabel.textContent = degraded ? "CORE DEGRADED" : "CORE ONLINE";
      updateFooterMessage();
      applyVoiceCapability();
      void refreshServerConfig();
      return true;
    } catch (_) {
      state.coreStatus = "offline";
      state.voiceServiceAvailable = false;
      els.healthDot.className = "status-dot is-offline";
      els.healthLabel.textContent = "CORE OFFLINE";
      updateFooterMessage();
      applyVoiceCapability();
      return false;
    }
  }

  function startClock() {
    const update = () => {
      els.clockLabel.textContent = new Date().toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
    };
    update();
    window.setInterval(update, 15_000);
  }

  function updateFooterMessage() {
    if (state.coreStatus === "offline") {
      els.footerMessage.textContent = "CONNESSIONE AL CORE INTERROTTA";
    } else if (state.coreStatus === "degraded") {
      els.footerMessage.textContent = "MODALITÀ DEGRADATA ATTIVA";
    } else {
      els.footerMessage.textContent = state.screen === "game"
        ? "SESSIONE ISOLATA / NESSUN ACCESSO RETE"
        : "ALL SYSTEMS NOMINAL";
    }
  }

  function openDialog(dialog) {
    dialog.returnValue = "";
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  function closeDialog(dialog) {
    if (typeof dialog.close === "function") dialog.close();
    else dialog.removeAttribute("open");
  }

  function closeDialogOnBackdrop(event) {
    if (event.target === event.currentTarget) closeDialog(event.currentTarget);
  }

  function confirmAction(title, copy) {
    els.confirmTitle.textContent = title;
    els.confirmCopy.textContent = copy;
    openDialog(els.confirmDialog);
    return new Promise(resolve => {
      const onClose = () => {
        els.confirmDialog.removeEventListener("close", onClose);
        resolve(els.confirmDialog.returnValue === "confirm");
      };
      els.confirmDialog.addEventListener("close", onClose);
    });
  }

  function toast(message, type = "info", duration = 4200) {
    const element = document.createElement("div");
    element.className = `toast${type !== "info" ? ` is-${type}` : ""}`;
    element.textContent = message;
    els.toastRegion.append(element);
    window.setTimeout(() => {
      element.classList.add("is-leaving");
      window.setTimeout(() => element.remove(), 300);
    }, duration);
  }

  function setButtonLoading(button, loading, label = "ELABORAZIONE…") {
    if (loading) {
      button.dataset.originalText = button.innerHTML;
      button.textContent = label;
      button.disabled = true;
    } else {
      if (button.dataset.originalText) button.innerHTML = button.dataset.originalText;
      delete button.dataset.originalText;
      button.disabled = false;
    }
  }

  function getLevel(levelId) {
    return state.levels.find(level => String(level.id) === String(levelId));
  }

  function modeShowsTimer() {
    const activeMode = (state.config.modes || []).find(mode =>
      String(typeof mode === "string" ? mode : mode.id ?? mode.mode_id) === state.mode
    );
    return typeof activeMode !== "object" || activeMode.show_timer !== false;
  }

  function normalizeLanguage(value) {
    const language = String(value || "").toLowerCase();
    if (language.startsWith("it")) return "it";
    if (language.startsWith("en")) return "en";
    if (language === "auto") return "auto";
    return "";
  }

  function languageLabel(language) {
    return language === "it" ? "IT" : language === "en" ? "EN" : "AUTO";
  }

  function sanitizeNickname(value) {
    return String(value || "").replace(/[\x00-\x1F\x7F]/g, "").replace(/\s+/g, " ").trim().slice(0, 24);
  }

  function localized(value, preferredLanguage = "it") {
    if (typeof value === "string") return value;
    if (!value || typeof value !== "object") return "";
    return value[preferredLanguage] || value.en || Object.values(value).find(item => typeof item === "string") || "";
  }

  function padNumber(value) {
    return String(Math.max(0, Number(value) || 0)).padStart(2, "0");
  }

  function formatDuration(totalSeconds) {
    const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function delay(ms) {
    return new Promise(resolve => window.setTimeout(resolve, ms));
  }
})();
