const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const SESSION_KEY = "taskmaster_studio_session";
const PROJECT_KEY = "taskmaster_studio_project";
const PARTNER_CHAT_KEY = "taskmaster_studio_partner_chat";
const PARTNER_CONVERSATIONS_KEY = "taskmaster_studio_conversations";
const ID_TOKEN_KEY = "taskmaster_studio_id_token";
const REFRESH_TOKEN_KEY = "taskmaster_studio_refresh_token";
const LANGUAGE_KEY = "taskmaster_studio_language";
const MAX_SESSION_DOCUMENTS = 12;
const MAX_DOCUMENT_UPLOAD_BYTES = 25 * 1024 * 1024;
const MAX_LARGE_DOCUMENT_UPLOAD_BYTES = 600 * 1024 * 1024;
const DOCUMENT_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024;
const CONNECTION_CATALOG = [
  { plugin_id: "google.drive", title: "Google Drive", provider: "Google" },
  { plugin_id: "google.gmail", title: "Gmail", provider: "Google" },
  { plugin_id: "google.calendar", title: "Google Calendar", provider: "Google" },
  { plugin_id: "github", title: "GitHub", provider: "GitHub" },
];
const sessionId = localStorage.getItem(SESSION_KEY) || `browser_${crypto.randomUUID().replaceAll("-", "")}`;
localStorage.setItem(SESSION_KEY, sessionId);
function conversationStorageKey(owner = state?.identity?.user_id || sessionId) { return `${PARTNER_CONVERSATIONS_KEY}:${owner}`; }

const restoredConversations = readPartnerConversations(sessionId);
const state = { projectId: localStorage.getItem(PROJECT_KEY), partnerConversations: restoredConversations, activeConversationId: null, activeCatalogAgent: null, partnerMessages: [], partnerPhase: "discovery", entryMode: "radar", language: localStorage.getItem(LANGUAGE_KEY) === "en" ? "en" : "es", languageTranslating: false, documents: [], documentUploads: [], agents: [], builds: [], connections: [], identity: null, identityConfig: { mode: "local" }, authReady: true, attachedDocumentIds: [], partnerPending: false, partnerTypingVisible: false, entryTransitionPending: false, runtimeLoaded: false, buildRuntime: "", runtime: { mode: "local", label: "Comprobando Gemini", provider: "Vertex AI", model: "gemini-3.7-flash", model_calls_enabled: false } };
const buildPollers = new Map();
const conversationSyncTimers = new Map();
const activeDocumentUploads = new Map();
const terminalBuildStates = new Set(["completed", "failed", "stopped"]);

const UI_COPY = {
  "Saltar al contenido": "Skip to content", "Nuevo chat": "New chat", "Ir al taller": "Open Studio", "Diseñar · probar · publicar": "Design · test · publish",
  "AGENTES APROBADOS": "APPROVED AGENTS", "Todavía no has publicado agentes.": "You have not published any agents yet.", "Conversaciones": "Conversations",
  "Todavía no hay conversaciones guardadas.": "There are no saved conversations yet.", "Conexiones": "Connections", "Selecciona un servicio para conectarlo.": "Select a service to connect it.",
  "CAPACIDADES DEL RADAR": "RADAR CAPABILITIES", "Analista de oportunidades": "Opportunity analyst", "GitHub · Drive · tendencias verificadas": "GitHub · Drive · verified trends",
  "Investigación profunda": "Deep research", "Fuentes visibles · actualidad · contraste": "Visible sources · freshness · comparison", "Diseño y construcción": "Design and construction",
  "Framework automático · aprobación humana": "Automatic framework · human approval", "Efectos externos": "External effects", "Bloqueados hasta autorizar cada acción": "Blocked until each action is authorized",
  "Iniciar sesión": "Sign in", "Comprobando cuenta": "Checking account", "Espacio personal": "Personal space", "Gestionar archivos": "Manage files", "Cerrar sesión": "Sign out", "Salir de esta cuenta": "Leave this account",
  "Escribe un mensaje…": "Type a message…", "Gemini no está conectado": "Gemini is not connected", "Subir archivo": "Upload file", "PDF, Office, texto o datos": "PDF, Office, text or data",
  "Subir imagen": "Upload image", "PNG, JPG o WEBP": "PNG, JPG or WEBP", "Enter para enviar · Shift + Enter para nueva línea": "Enter to send · Shift + Enter for a new line",
  "Analizar mi portafolio": "Analyze my portfolio", "Investigar tendencias": "Research trends", "Descubrir una oportunidad": "Discover an opportunity", "Diseñar un Taskmaster": "Design a Taskmaster",
  "Automatizar un proceso": "Automate a process", "Evaluar una arquitectura": "Evaluate an architecture", "Aprobación humana": "Human approval", "Nada se construye sin confirmar": "Nothing is built without confirmation",
  "Experiencia, activos y vacíos": "Experience, assets and gaps", "Documentos, requisitos y contexto": "Documents, requirements and context", "Web verificada": "Verified web", "Tendencias, adopción y fuentes": "Trends, adoption and sources",
  "Explorando": "Exploring", "Aclarando": "Clarifying", "Alineados": "Aligned", "Conversando": "Conversing", "Tú": "You", "Copiar": "Copy", "Gemini está respondiendo": "Gemini is responding",
  "Archivos de la sesión": "Session files", "Gestionar archivos": "Manage files", "INSPECCIÓN SEGURA": "SAFE INSPECTION", "Documento": "Document", "Trabajando…": "Working…",
  "ANÁLISIS VISUAL": "VISUAL ANALYSIS", "Gráfico": "Chart", "Preparando gráfico interactivo…": "Preparing interactive chart…", "Ver datos": "View data", "Categoría": "Category", "Valor": "Value",
  "LECTURA PROFUNDA DEL DATASET": "DEEP DATASET ANALYSIS", "Panel analítico": "Analytics dashboard", "Barras": "Bars", "Tendencia": "Trend", "Área": "Area", "Composición": "Composition", "Correlación": "Correlation", "Ranking": "Ranking",
  "Diseño aprobado": "Design approved", "Espacio preparado": "Workspace prepared", "Taskmaster construido": "Taskmaster built", "Autorización humana": "Human authorization", "Verificaciones": "Verifications", "Entrega completada": "Delivery completed",
  "RADAR DE PROYECTOS · SOCIO COLABORATIVO": "PROJECT RADAR · COLLABORATIVE PARTNER", "TASKMASTER STUDIO · INGENIERO DE AGENTES": "TASKMASTER STUDIO · AGENT ENGINEER",
  "¿Qué proyecto deberíamos construir?": "What project should we build?", "Construye tu Taskmaster": "Build your Taskmaster", "Escribe un mensaje para explorar una oportunidad…": "Type a message to explore an opportunity…",
  "Describe el agente, sus usuarios o el proceso que quieres automatizar…": "Describe the agent, its users, or the process you want to automate…",
  "Gemini contrasta tu portafolio de GitHub, el contexto de tus documentos y tendencias actuales en fuentes verificables para encontrar oportunidades de sistemas con evidencia.": "Gemini compares your GitHub portfolio, document context, and current trends from verifiable sources to find evidence-backed system opportunities.",
  "Describe el trabajo que quieres delegar. Gemini aclarará el objetivo y el Ingeniero de agentes seleccionará el framework, preparará las pruebas y pedirá tu aprobación antes de construir.": "Describe the work you want to delegate. Gemini will clarify the goal, and the Agent Engineer will select the framework, prepare tests, and request your approval before building.",
  "El análisis es de solo lectura. Las fuentes no disponibles se declaran y ningún proyecto se crea sin tu confirmación.": "The analysis is read-only. Unavailable sources are disclosed, and no project is created without your confirmation.",
  "El Socio colaborativo diseña contigo. El Ingeniero construye solo después de tu confirmación y solicita permiso antes de ejecutar pruebas o efectos externos.": "The Collaborative Partner designs with you. The Engineer builds only after your confirmation and requests permission before running tests or external effects.",
  "Consulta, adjunta o elimina los documentos e imágenes disponibles en tu espacio personal.": "Inspect, attach, or delete the documents and images available in your personal space.",
  "Crear una conversación nueva": "Create a new conversation", "Cerrar historial": "Close history", "Abrir historial": "Open history", "Abrir configuración": "Open settings", "Configuración": "Settings",
  "Cambiar cuenta": "Switch account", "Cambiar cuenta de Google": "Switch Google account", "Iniciar sesión con Google": "Sign in with Google", "Accede a tu espacio personal": "Access your personal space",
  "Cerrar gestor de archivos": "Close file manager", "Cerrar inspección": "Close inspector", "Vista previa del archivo adjunto": "Attachment preview", "Enviar mensaje": "Send message", "Adjuntar": "Attach",
};
const UI_COPY_REVERSE = Object.fromEntries(Object.entries(UI_COPY).map(([es, en]) => [en, es]));

function operationKey(prefix) { return `${prefix}-${crypto.randomUUID()}`; }
function newConversationId() { return `chat_${crypto.randomUUID()}`; }
function uiText(value) {
  const clean = String(value ?? "").trim();
  if (!clean) return value;
  const translated = state.language === "en" ? UI_COPY[clean] : UI_COPY_REVERSE[clean];
  if (!translated) {
    const files = clean.match(/^(\d+) archivos cargados$/);
    const visualizations = clean.match(/^(\d+) visualizaciones$/);
    if (state.language === "en" && files) return `${files[1]} uploaded files`;
    if (state.language === "en" && visualizations) return `${visualizations[1]} visualizations`;
    const uploaded = clean.match(/^(\d+) uploaded files$/);
    if (state.language === "es" && uploaded) return `${uploaded[1]} archivos cargados`;
    return value;
  }
  return translated;
}
function localizeInterface(root = document.body) {
  document.documentElement.lang = state.language;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach((node) => {
    if (node.parentElement?.closest(".turn-content,pre,code,[data-no-translate]")) return;
    const original = node.nodeValue || ""; const clean = original.trim(); if (!clean) return;
    const translated = uiText(clean); if (translated === clean) return;
    node.nodeValue = original.replace(clean, translated);
  });
  root.querySelectorAll?.("[placeholder],[aria-label],[title]").forEach((element) => {
    ["placeholder", "aria-label", "title"].forEach((attribute) => {
      const value = element.getAttribute(attribute); if (!value) return;
      const translated = uiText(value); if (translated !== value) element.setAttribute(attribute, translated);
    });
  });
  const languageButton = $("#language-action");
  if (languageButton) {
    const title = state.language === "es" ? "English" : "Español";
    const copy = state.language === "es" ? "Translate page and conversations" : "Traducir página y conversaciones";
    if ($("#language-action-title").textContent !== title) $("#language-action-title").textContent = title;
    if ($("#language-action-copy").textContent !== copy) $("#language-action-copy").textContent = copy;
    languageButton.setAttribute("aria-label", state.language === "es" ? "Translate to English" : "Traducir al español");
    languageButton.disabled = state.languageTranslating;
  }
}
function localizedMessageContent(message) {
  const sourceLanguage = message.sourceLanguage || "es";
  if (sourceLanguage === state.language) return message.content;
  return message.translations?.[state.language] || message.content;
}
function localizedArtifact(artifact, sourceLanguage = "es") {
  if (sourceLanguage === state.language) return artifact;
  const translated = artifact?.translations?.[state.language];
  if (!translated) return artifact;
  return {
    ...artifact,
    ...translated,
    columns: translated.columns || artifact.columns,
    insights: translated.insights || artifact.insights,
    metrics: (artifact.metrics || []).map((metric, index) => ({
      ...metric,
      label: translated.metricLabels?.[index] || metric.label,
    })),
  };
}
function conversationTranslationTargets() {
  const targets = [];
  state.partnerMessages.forEach((message) => {
    const sourceLanguage = message.sourceLanguage || "es";
    if (sourceLanguage === state.language) return;
    if (message.content && !message.translations?.[state.language]) {
      targets.push({
        text: message.content,
        apply(value) { message.translations = { ...(message.translations || {}), [state.language]: value }; },
      });
    }
    (message.artifacts || []).forEach((artifact) => {
      if (artifact?.type !== "chart") return;
      artifact.translations ||= {};
      const translated = artifact.translations[state.language] ||= { columns: [], insights: [], metricLabels: [] };
      const add = (text, current, apply) => { if (text && !current) targets.push({ text, apply }); };
      add(artifact.title, translated.title, (value) => { translated.title = value; });
      add(artifact.description, translated.description, (value) => { translated.description = value; });
      (artifact.columns || []).forEach((text, index) => add(text, translated.columns[index], (value) => { translated.columns[index] = value; }));
      (artifact.insights || []).forEach((text, index) => add(text, translated.insights[index], (value) => { translated.insights[index] = value; }));
      (artifact.metrics || []).forEach((metric, index) => add(metric.label, translated.metricLabels[index], (value) => { translated.metricLabels[index] = value; }));
    });
  });
  return targets;
}
async function translateCurrentConversation() {
  const targets = conversationTranslationTargets();
  if (!targets.length || state.languageTranslating) return;
  state.languageTranslating = true; localizeInterface();
  try {
    for (let index = 0; index < targets.length; index += 8) {
      const batch = targets.slice(index, index + 8);
      const payload = await api("/api/v1/collaborative/translations", {
        method: "POST", background: true,
        body: JSON.stringify({ texts: batch.map((target) => target.text), target_language: state.language }),
      });
      batch.forEach((target, offset) => target.apply(payload.translations[offset]));
    }
    persistPartnerHistory(); renderPartnerConversation();
  } catch (error) { handle(error); }
  finally { state.languageTranslating = false; localizeInterface(); }
}
async function toggleLanguage() {
  state.language = state.language === "es" ? "en" : "es";
  localStorage.setItem(LANGUAGE_KEY, state.language);
  $("#account-settings").removeAttribute("open");
  renderWelcomeMode(); renderConversationHistory(); renderAgentCatalog(); renderConnectionCatalog(); renderAttachments(); renderAccountIdentity(); renderPartnerConversation();
  localizeInterface();
  await translateCurrentConversation();
}
function identityHeaders(extra = {}) {
  const headers = { "X-Studio-Session": sessionId, ...extra };
  const idToken = localStorage.getItem(ID_TOKEN_KEY);
  if (idToken) headers.Authorization = `Bearer ${idToken}`;
  return headers;
}
async function refreshIdentitySession() {
  const legacyRefreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
  const options = {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(legacyRefreshToken ? { refresh_token: legacyRefreshToken } : {}),
  };
  const response = await fetch("/api/v1/collaborative/auth/refresh", options);
  if (!response.ok) return false;
  const payload = await response.json();
  if (!payload.id_token) return false;
  localStorage.setItem(ID_TOKEN_KEY, payload.id_token);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  return true;
}
async function api(path, options = {}) {
  const background = Boolean(options.background);
  const requestOptions = { ...options }; delete requestOptions.background;
  const headers = identityHeaders(options.headers || {});
  if (options.body) headers["Content-Type"] = "application/json";
  if (options.idempotent) headers["Idempotency-Key"] = operationKey(options.idempotent);
  if (!background) setLoading(true);
  try {
    let response = await fetch(path, { ...requestOptions, headers, credentials: "same-origin" });
    if (response.status === 401 && path !== "/api/v1/collaborative/auth/refresh" && await refreshIdentitySession()) {
      response = await fetch(path, { ...requestOptions, headers: identityHeaders(options.headers || {}), credentials: "same-origin" });
    }
    if (response.status === 204) return {};
    const raw = await response.text();
    let payload = {};
    try { payload = raw ? JSON.parse(raw) : {}; }
    catch {
      if (!response.ok) throw new Error("El servidor no pudo completar la solicitud. Inténtalo nuevamente.");
      throw new Error("El servidor devolvió una respuesta inesperada.");
    }
    if (!response.ok) throw new Error(payload.error?.message || "No se pudo completar la acción.");
    return payload;
  } finally { if (!background) setLoading(false); }
}
function setLoading(value) { $("#loading").hidden = !value; $$('button, textarea, input, select').forEach((item) => { item.disabled = value; }); if (!value) renderRuntimeInfo(); }
function notify(message, kind = "info") { const notice = $("#notice"); notice.textContent = message; notice.className = `notice ${kind}`; notice.hidden = false; setTimeout(() => { notice.hidden = true; }, 5000); }
function handle(error) { console.error(error); notify(error.message || "Ocurrió un error seguro.", "error"); }

function showOAuthReturnNotice() {
  const parameters = new URLSearchParams(window.location.search);
  const identity = parameters.get("identity");
  if (identity === "connected") notify("Tu espacio personal quedó abierto con Google.", "success");
  else if (identity === "error") notify("No se pudo completar el acceso con Google.", "error");
  const outcome = parameters.get("connection");
  if (outcome) {
    const provider = parameters.get("provider") === "google.drive" ? "Google Drive" : "el servicio";
    if (outcome === "connected") notify(`${provider} quedó conectado con permisos de solo lectura.`, "success");
    else notify(`No se pudo completar la conexión con ${provider}. Puedes reintentarlo desde Conexiones.`, "error");
  }
  if (!identity && !outcome) return;
  parameters.delete("identity"); parameters.delete("connection"); parameters.delete("provider");
  const query = parameters.toString();
  window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
}

async function loadRuntimeInfo() {
  try {
    const response = await fetch("/api/v1/meta");
    if (!response.ok) throw new Error("runtime metadata unavailable");
    const payload = await response.json();
    if (payload.runtime_ui) state.runtime = payload.runtime_ui;
    state.buildRuntime = payload.build_orchestration?.runtime || "";
    if (payload.identity) state.identityConfig = payload.identity;
    await initializeIdentity();
  } catch (error) {
    console.warn("Using safe local runtime label.", error);
  }
  state.runtimeLoaded = true;
  renderRuntimeInfo();
  if (!state.authReady) return;
  await loadConversationMemory();
  await loadBuildHistory();
  await loadDocumentLibrary();
  await loadAgentCatalog();
  await loadIdentity();
  await loadConnections();
}
function renderRuntimeInfo() {
  const chatReady = state.runtimeLoaded && state.authReady && state.runtime.mode === "gemini" && state.runtime.model_calls_enabled !== false;
  $$('[data-runtime-model]').forEach((item) => { item.textContent = state.runtime.label; });
  $$('[data-runtime-provider]').forEach((item) => { item.textContent = ` · ${state.runtime.provider}`; });
  $("#runtime-badge")?.classList.toggle("cloud-active", state.runtime.mode === "gemini");
  const gate = $("#runtime-gate");
  gate.hidden = chatReady;
  gate.textContent = state.runtimeLoaded && !state.authReady ? "Inicia sesión para abrir tu espacio personal, conversaciones, agentes y conexiones." : state.runtimeLoaded ? "Gemini no está conectado. Inicia el estudio con scripts\\start_local.ps1 antes de enviar el primer mensaje." : "Comprobando Vertex AI antes de habilitar el chat…";
  const welcomeInput = $("#project-description"); const partnerInput = $("#partner-message-input");
  welcomeInput.disabled = !chatReady; partnerInput.disabled = !chatReady || state.partnerPending;
  welcomeInput.placeholder = chatReady ? "Escribe un mensaje…" : "Gemini no está conectado";
  partnerInput.placeholder = chatReady ? "Escribe un mensaje…" : "Gemini no está conectado";
  $("#project-form").querySelector('button[type="submit"]').disabled = !chatReady;
  $("#partner-message-form").querySelector('button[type="submit"]').disabled = !chatReady || state.partnerPending;
  $$('[data-example]').forEach((button) => { button.disabled = !chatReady; });
}
function chatIsReady() { return state.runtimeLoaded && state.authReady && state.runtime.mode === "gemini" && state.runtime.model_calls_enabled !== false; }

async function initializeIdentity() {
  if (state.identityConfig.mode !== "identity_platform") { state.authReady = true; renderAccountIdentity(); return; }
  state.authReady = false; renderAccountIdentity();
  let idToken = localStorage.getItem(ID_TOKEN_KEY);
  if (!idToken && await refreshIdentitySession()) idToken = localStorage.getItem(ID_TOKEN_KEY);
  if (!idToken) return;
  try {
    let response = await fetch("/api/v1/collaborative/identity", { headers: identityHeaders(), credentials: "same-origin" });
    if (response.status === 401 && await refreshIdentitySession()) {
      response = await fetch("/api/v1/collaborative/identity", { headers: identityHeaders(), credentials: "same-origin" });
    }
    if (!response.ok) throw new Error("identity token rejected");
    const user = await response.json();
    state.authReady = true; state.identity = user;
    renderAccountIdentity();
    state.partnerConversations = readPartnerConversations(user.user_id);
    // Keep the entry screen on startup. Previous chats remain selectable from
    // history, but signing in must not open the most recent one automatically.
    state.activeConversationId = null; state.partnerMessages = []; state.partnerPhase = "discovery"; state.attachedDocumentIds = [];
  } catch (error) {
    console.warn("Stored identity expired.", error);
    localStorage.removeItem(ID_TOKEN_KEY); state.identity = null; renderAccountIdentity();
  }
}

function renderAccountIdentity() {
  const authenticated = Boolean(state.identity?.authenticated);
  const displayName = authenticated
    ? (state.identity.display_name || state.identity.email || "Usuario verificado")
    : "Iniciar sesión";
  const email = authenticated ? (state.identity.email || "Cuenta verificada") : "Accede a tu espacio personal";
  $(".sidebar-account").classList.toggle("is-signed-out", !authenticated);
  $("#account-display-name").textContent = displayName;
  $("#account-email").textContent = email;
  $("#account-switch").setAttribute("aria-label", authenticated ? "Cambiar cuenta de Google" : "Iniciar sesión con Google");
  const fallback = $("#account-avatar-fallback");
  fallback.textContent = displayName.trim().charAt(0).toUpperCase() || "U";
  const image = $("#account-avatar-image");
  const picture = authenticated ? state.identity.picture_url : "";
  if (picture) { image.src = picture; image.hidden = false; fallback.hidden = true; }
  else { image.removeAttribute("src"); image.hidden = true; fallback.hidden = false; }
  $("#logout-action").hidden = !authenticated;
}

async function clearBrowserIdentity() {
  await fetch("/api/v1/collaborative/auth/logout", { method: "POST", credentials: "same-origin" });
  localStorage.removeItem(ID_TOKEN_KEY); localStorage.removeItem(REFRESH_TOKEN_KEY);
}

async function switchIdentityAccount() {
  if (state.identityConfig.mode !== "identity_platform") {
    notify("El inicio de sesión con Google estará disponible en la aplicación publicada.", "info");
    return;
  }
  if (state.identity?.authenticated) await clearBrowserIdentity();
  window.location.assign("/api/v1/collaborative/auth/google/start");
}

async function logoutIdentity() {
  if (!state.identity?.authenticated) return;
  await clearBrowserIdentity();
  window.location.reload();
}

async function createProject(event) {
  event.preventDefault();
  if (state.entryTransitionPending) return;
  const input = $("#project-description");
  const submit = $("#project-form").querySelector('button[type="submit"]');
  const message = input.value.trim();
  if (!message) return;
  state.entryTransitionPending = true;
  input.disabled = true;
  submit.disabled = true;
  try {
    await transitionWelcomeToConversation();
    input.value = "";
    $("#char-count").textContent = "0 / 6000";
    await sendPartnerMessage(message);
  } finally {
    state.entryTransitionPending = false;
    input.disabled = !chatIsReady();
  }
}

function transitionDelay(milliseconds) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return Promise.resolve();
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function transitionWelcomeToConversation() {
  const welcome = $("#welcome-view");
  const chat = $("#partner-chat-view");
  if (welcome.hidden) return;
  // Taskmaster Studio is a single persistent canvas. Replace the welcome
  // content in place instead of replaying the general chat transition.
  if (state.entryMode === "builder") {
    renderPartnerConversation();
    showPartnerChat();
    return;
  }
  document.body.classList.add("chat-transitioning");
  welcome.classList.add("welcome-exit");
  await transitionDelay(280);
  // The hidden chat may still contain markup from the previously opened
  // conversation. Render the current (usually empty) state before revealing
  // it so an old draft never flashes during the first-turn transition.
  renderPartnerConversation();
  showPartnerChat();
  chat.classList.add("chat-entering");
  await transitionDelay(620);
  welcome.classList.remove("welcome-exit");
  chat.classList.remove("chat-entering");
  document.body.classList.remove("chat-transitioning");
}

async function sendPartnerMessage(message) {
  if (state.activeCatalogAgent) {
    await sendCatalogAgentMessage(message);
    return;
  }
  if (!chatIsReady()) { notify("Gemini no está conectado. Reinicia el estudio con la verificación de Vertex AI.", "error"); return; }
  const firstConversationTurn = !state.partnerMessages.some((item) => item.role === "user");
  const firstTurnInBuilder = state.entryMode === "builder" && firstConversationTurn;
  const requestMessage = firstTurnInBuilder
    ? `Quiero diseñar y construir un agente Taskmaster en Taskmaster Studio. Mi solicitud es: ${message}`
    : message;
  const history = state.partnerMessages.filter((item) => ["user", "assistant"].includes(item.role)).slice(-16).map(({ role, content, toolActivity }) => ({ role, content, evidence: Array.isArray(toolActivity) ? toolActivity.slice(0, 8).map((entry) => `${entry.capability || "unknown"} | ${entry.status || "unknown"} | ${entry.path || "."} | ${entry.query || ""}`.slice(0, 500)) : [] }));
  state.partnerMessages.push({ role: "user", content: message, sourceLanguage: state.language });
  state.partnerPending = true;
  state.partnerTypingVisible = !firstConversationTurn;
  persistPartnerHistory();
  showPartnerChat();
  if (firstConversationTurn) $("#partner-chat-view").classList.add("first-turn-lifting");
  renderPartnerConversation();
  if (firstConversationTurn) {
    await transitionDelay(560);
    $("#partner-chat-view").classList.remove("first-turn-lifting");
    state.partnerTypingVisible = true;
    renderPartnerConversation();
  }
  try {
    const payload = await api("/api/v1/collaborative/messages", {
      method: "POST",
      background: true,
      body: JSON.stringify({ message: requestMessage, history, conversation_id: state.activeConversationId, document_ids: state.attachedDocumentIds, language: state.language }),
    });
    state.partnerMessages.push({ role: "assistant", content: payload.reply, sourceLanguage: state.language, model: payload.model, provider: payload.provider, intent: payload.intent, agentDraft: payload.agent_draft, toolActivity: payload.tool_activity, connectionOffers: payload.connection_offers || [], artifacts: payload.artifacts || [], revealResponse: true });
    state.partnerPhase = payload.phase;
    persistPartnerHistory();
  } catch (error) {
    handle(error);
  } finally {
    state.partnerPending = false;
    state.partnerTypingVisible = false;
    $("#partner-chat-view").classList.remove("first-turn-lifting");
    renderPartnerConversation();
  }
}

async function sendCatalogAgentMessage(message) {
  if (!message.trim() || state.partnerPending || !state.activeCatalogAgent) return;
  const agent = state.activeCatalogAgent;
  state.partnerMessages.push({ role: "user", content: message, sourceLanguage: state.language });
  state.partnerPending = true;
  state.partnerTypingVisible = true;
  persistPartnerHistory();
  showPartnerChat();
  renderPartnerConversation();
  try {
    const payload = await api(`/api/v1/collaborative/agents/${encodeURIComponent(agent.id)}/messages`, {
      method: "POST",
      idempotent: "catalog-agent-run",
      body: JSON.stringify({ message, document_ids: state.attachedDocumentIds, language: state.language }),
    });
    const stepSummary = Array.isArray(payload.steps) && payload.steps.length
      ? `\n\n**${state.language === "en" ? "Controlled execution" : "Ejecución controlada"}**\n${payload.steps.map((step) => `- ${step.name}: ${step.detail}`).join("\n")}`
      : "";
    state.partnerMessages.push({
      role: "assistant",
      sourceLabel: agent.name,
      content: `${payload.reply}${stepSummary}`,
      sourceLanguage: state.language,
      model: payload.model,
      provider: "Taskmaster Runtime",
      artifacts: payload.artifacts || [],
      revealResponse: true,
    });
    persistPartnerHistory();
  } catch (error) {
    handle(error);
  } finally {
    state.partnerPending = false;
    state.partnerTypingVisible = false;
    renderPartnerConversation();
  }
}

function showPartnerChat() {
  document.body.classList.add("chat-active");
  $("#welcome-view").hidden = true;
  $("#partner-chat-view").hidden = false;
}

function showWelcome() {
  document.body.classList.remove("chat-active");
  $("#partner-chat-view").hidden = true;
  $("#welcome-view").hidden = false;
  renderWelcomeMode();
}

function renderWelcomeMode() {
  const builder = state.entryMode === "builder";
  const english = state.language === "en";
  $("#taskmaster-studio-access").setAttribute("aria-pressed", String(builder));
  document.body.classList.toggle("taskmaster-studio-mode", builder);
  $("#welcome-eyebrow").textContent = builder ? "TASKMASTER STUDIO · INGENIERO DE AGENTES" : "RADAR DE PROYECTOS · SOCIO COLABORATIVO";
  $("#welcome-title").textContent = builder ? "Construye tu Taskmaster" : "¿Qué proyecto deberíamos construir?";
  $("#welcome-lead").textContent = builder
    ? "Describe el trabajo que quieres delegar. Gemini aclarará el objetivo y el Ingeniero de agentes seleccionará el framework, preparará las pruebas y pedirá tu aprobación antes de construir."
    : "Gemini contrasta tu portafolio de GitHub, el contexto de tus documentos y tendencias actuales en fuentes verificables para encontrar oportunidades de sistemas con evidencia.";
  $("#project-description").placeholder = builder ? "Describe el agente, sus usuarios o el proceso que quieres automatizar…" : "Escribe un mensaje para explorar una oportunidad…";
  $("#welcome-examples").innerHTML = builder
    ? english
      ? `<button type="button" data-example="I want to create a Taskmaster agent that organizes a multi-step workflow and requests approval before acting."><span>✦</span> Design a Taskmaster</button><button type="button" data-example="Help me turn this manual process into a safe agent with tools, memory, and tests."><span>⌁</span> Automate a process</button><button type="button" data-example="I want to assess an agent's architecture and risks before building it."><span>◇</span> Evaluate an architecture</button>`
      : `<button type="button" data-example="Quiero crear un agente Taskmaster que organice un flujo de trabajo de varios pasos y solicite aprobación antes de actuar."><span>✦</span> Diseñar un Taskmaster</button><button type="button" data-example="Ayúdame a convertir este proceso manual en un agente seguro, con herramientas, memoria y pruebas."><span>⌁</span> Automatizar un proceso</button><button type="button" data-example="Quiero evaluar la arquitectura y los riesgos de un agente antes de construirlo."><span>◇</span> Evaluar una arquitectura</button>`
    : english
      ? `<button type="button" data-example="Analyze my GitHub repositories and tell me which project opportunity would best complement my portfolio."><span>⌘</span> Analyze my portfolio</button><button type="button" data-example="Research trending systems projects this year and propose opportunities backed by verified sources."><span>◎</span> Research trends</button><button type="button" data-example="Review my GitHub projects, Google Drive documents, and current trends to recommend what project to build."><span>◇</span> Discover an opportunity</button>`
      : `<button type="button" data-example="Analiza mis repositorios de GitHub y dime qué oportunidad de proyecto complementaría mejor mi portafolio."><span>⌘</span> Analizar mi portafolio</button><button type="button" data-example="Investiga proyectos en tendencia este año en el área de sistemas y propón oportunidades respaldadas por fuentes verificadas."><span>◎</span> Investigar tendencias</button><button type="button" data-example="Revisa mis proyectos de GitHub, mis documentos de Google Drive y tendencias actuales para recomendarme qué proyecto construir."><span>◇</span> Descubrir una oportunidad</button>`;
  $("#welcome-sources").innerHTML = builder
    ? `<article><span>✦</span><div><strong>Gemini 3.7 Flash</strong><small>Descubre y especifica contigo</small></div></article><article><span>⌁</span><div><strong>Agents CLI + ADK</strong><small>Estructura, herramientas y pruebas</small></div></article><article><span>◇</span><div><strong>Aprobación humana</strong><small>Nada se construye sin confirmar</small></div></article>`
    : `<article><span>⌘</span><div><strong>GitHub</strong><small>Experiencia, activos y vacíos</small></div></article><article><span>▤</span><div><strong>Google Drive</strong><small>Documentos, requisitos y contexto</small></div></article><article><span>◎</span><div><strong>Web verificada</strong><small>Tendencias, adopción y fuentes</small></div></article>`;
  $("#welcome-trust").textContent = builder
    ? "El Socio colaborativo diseña contigo. El Ingeniero construye solo después de tu confirmación y solicita permiso antes de ejecutar pruebas o efectos externos."
    : "El análisis es de solo lectura. Las fuentes no disponibles se declaran y ningún proyecto se crea sin tu confirmación.";
  bindExampleButtons();
}

function openTaskmasterStudio() {
  state.entryMode = "builder";
  state.activeConversationId = newConversationId(); state.partnerMessages = []; state.partnerPhase = "discovery"; state.attachedDocumentIds = [];
  renderConversationHistory(); renderAttachments(); showWelcome();
  document.body.classList.remove("sidebar-open");
  $("#project-description").focus();
}

function renderPartnerConversation() {
  const phaseLabels = { discovery: "Explorando", clarification: "Aclarando", alignment: "Alineados" };
  const turns = state.partnerMessages.map((item, index) => {
    const visibleContent = localizedMessageContent(item);
    if (item.role === "user") return `<article class="partner-turn user-turn"><div class="turn-label">Tú</div><div class="turn-content">${formatChatText(visibleContent)}</div></article>`;
    if (item.kind === "agent_build") return renderAgentBuild(item, index);
    return `<article class="partner-turn assistant-turn${item.revealResponse ? " response-arrival" : ""}"><div class="partner-avatar" aria-hidden="true">${item.sourceLabel === "Studio" ? "C" : "✦"}</div><div><div class="turn-label">${escapeHtml(item.sourceLabel || "Gemini")}</div><div class="turn-content">${formatChatText(visibleContent)}</div>${renderChartArtifacts(item.artifacts, index)}${renderAgentDraft(item, index)}${renderConnectionOffers(item.connectionOffers)}${renderToolActivity(item.toolActivity)}<div class="turn-meta"><small>${escapeHtml(item.model || state.runtime.model)} · ${escapeHtml(item.provider || "Vertex AI")}</small><button class="copy-response" type="button" data-copy-index="${index}" aria-label="Copiar respuesta">Copiar</button></div></div></article>`;
  }).join("");
  const typingTurn = state.partnerPending && state.partnerTypingVisible
    ? `<article class="partner-turn assistant-turn typing-turn inline-typing-turn" aria-live="polite"><div class="partner-avatar" aria-hidden="true">✦</div><div><div class="turn-label">Gemini</div><div class="typing-dots" aria-label="Gemini está respondiendo"><span></span><span></span><span></span></div></div></article>`
    : "";
  $("#partner-conversation").innerHTML = turns + typingTurn;
  drawChartArtifacts();
  // The marker is consumed by this render. Keeping it out of later renders
  // prevents old answers from replaying their entrance animation.
  state.partnerMessages.forEach((item) => { if (item.revealResponse) item.revealResponse = false; });
  $("#partner-message-input").disabled = state.partnerPending || !chatIsReady();
  $("#partner-message-form").querySelector('button[type="submit"]').disabled = state.partnerPending || !chatIsReady();
  $("#partner-chat-view").dataset.phase = phaseLabels[state.partnerPhase] || "Conversando";
  requestAnimationFrame(() => {
    // The transcript owns its scroll. Never move the page or the composer:
    // the input remains docked while only previous turns move underneath it.
    const conversation = $("#partner-conversation");
    conversation.scrollTo({ top: conversation.scrollHeight, behavior: "smooth" });
    $("#partner-message-input").focus({ preventScroll: true });
  });
  resumeBuildPolling();
  localizeInterface($("#partner-chat-view"));
}

function renderChartArtifacts(artifacts, messageIndex) {
  if (!Array.isArray(artifacts) || !artifacts.length) return "";
  const sourceLanguage = state.partnerMessages[messageIndex]?.sourceLanguage || "es";
  const cards = artifacts.map((originalArtifact, artifactIndex) => {
    const artifact = localizedArtifact(originalArtifact, sourceLanguage);
    if (artifact?.type !== "chart" || !Array.isArray(artifact.rows)) return "";
    const rows = artifact.rows.map((row) => { const [x, y] = chartPointValues(row); return `<tr><td>${escapeHtml(x)}</td><td>${escapeHtml(y)}</td></tr>`; }).join("");
    const metrics = (artifact.metrics || []).map((metric) => `<li><span>${escapeHtml(metric.label)}</span><strong>${escapeHtml(metric.value)}</strong></li>`).join("");
    const insights = (artifact.insights || []).map((insight) => `<li>${escapeHtml(insight)}</li>`).join("");
    return `<section class="chat-chart-card" data-chart-variant="${escapeHtml(artifact.variant || "comparison")}"><header><div><span>ANÁLISIS VISUAL</span><strong>${escapeHtml(artifact.title || "Gráfico")}</strong></div><b>${escapeHtml(chartTypeLabel(artifact.chart_type))}</b></header>${metrics ? `<ul class="chart-metric-strip">${metrics}</ul>` : ""}<div class="chat-chart-canvas" data-chart-message="${messageIndex}" data-chart-artifact="${artifactIndex}" role="img" aria-label="${escapeHtml(artifact.description || artifact.title || "Gráfico de datos")}"><span>Preparando gráfico interactivo…</span></div><p>${escapeHtml(artifact.description || "")}</p>${insights ? `<ul class="chart-insights">${insights}</ul>` : ""}<footer><small>${escapeHtml(artifact.source_name || "Dataset")} · ${escapeHtml(artifact.sheet || "Datos")}</small><details><summary>Ver datos</summary><div class="chat-chart-table"><table><thead><tr><th>${escapeHtml(artifact.columns?.[0] || "Categoría")}</th><th>${escapeHtml(artifact.columns?.[1] || "Valor")}</th></tr></thead><tbody>${rows}</tbody></table></div></details></footer></section>`;
  }).join("");
  return `<section class="chat-chart-dashboard"><header><div><span>LECTURA PROFUNDA DEL DATASET</span><strong>Panel analítico</strong></div><b>${artifacts.length} visualizaciones</b></header><div class="chat-chart-grid">${cards}</div></section>`;
}

function chartPointValues(row) {
  return Array.isArray(row) ? [row[0], row[1]] : [row?.x, row?.y];
}

function chartTypeLabel(type) {
  return ({ bar: "Barras", horizontal_bar: "Ranking", line: "Tendencia", area: "Área", pie: "Composición", donut: "Composición", scatter: "Correlación" })[type] || "Gráfico";
}

function chartPalette(artifact) {
  const fallback = ["#8b7cf6", "#55d4df", "#ff7aa2", "#f3b65a", "#6ee7a8", "#7ca8ff", "#c792ff", "#ff9f68"];
  const supplied = Array.isArray(artifact?.palette) ? artifact.palette.filter((color) => /^#[0-9a-f]{6}$/i.test(color)) : [];
  return supplied.length ? supplied.slice(0, 8) : fallback;
}

function formatChartValue(value) {
  return new Intl.NumberFormat(state.language === "en" ? "en-US" : "es-CO", { notation: Math.abs(value) >= 100000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(value);
}

let googleChartsReady = null;
function ensureGoogleCharts() {
  if (googleChartsReady) return googleChartsReady;
  googleChartsReady = new Promise((resolve) => {
    if (!window.google?.charts) { resolve(false); return; }
    google.charts.load("current", { packages: ["corechart"], language: "es" });
    google.charts.setOnLoadCallback(() => resolve(true));
  });
  return googleChartsReady;
}

async function drawChartArtifacts() {
  const targets = [...document.querySelectorAll(".chat-chart-canvas")];
  if (!targets.length || !(await ensureGoogleCharts())) return;
  for (const target of targets) {
    const message = state.partnerMessages[Number(target.dataset.chartMessage)];
    const originalArtifact = message?.artifacts?.[Number(target.dataset.chartArtifact)];
    const artifact = localizedArtifact(originalArtifact, message?.sourceLanguage || "es");
    if (!artifact || target.dataset.chartDrawn === "true") continue;
    const rows = (artifact.rows || []).map((row) => { const [x, y] = chartPointValues(row); return [x, Number(y)]; });
    if (!rows.length || rows.some((row) => !Number.isFinite(row[1]))) continue;
    const palette = chartPalette(artifact);
    const decoratedBars = ["bar", "horizontal_bar"].includes(artifact.chart_type);
    const header = decoratedBars
      ? [artifact.columns?.[0] || "Categoría", artifact.columns?.[1] || "Valor", { role: "style" }, { role: "annotation" }]
      : [artifact.columns?.[0] || "Categoría", artifact.columns?.[1] || "Valor"];
    const chartRows = decoratedBars
      ? rows.map((row, index) => [row[0], row[1], `color: ${palette[index % palette.length]}`, formatChartValue(row[1])])
      : rows;
    const data = google.visualization.arrayToDataTable([header, ...chartRows]);
    const constructors = { bar: "ColumnChart", horizontal_bar: "BarChart", line: "LineChart", area: "AreaChart", pie: "PieChart", donut: "PieChart", scatter: "ScatterChart" };
    const Constructor = google.visualization[constructors[artifact.chart_type] || "ColumnChart"];
    const chart = new Constructor(target);
    chart.draw(data, {
      backgroundColor: "transparent",
      colors: palette,
      chartArea: { left: artifact.chart_type === "horizontal_bar" ? 105 : 62, top: 28, width: artifact.chart_type === "horizontal_bar" ? "70%" : "77%", height: "68%" },
      fontName: "Inter, system-ui, sans-serif",
      legend: { position: ["pie", "donut"].includes(artifact.chart_type) ? "right" : "none", textStyle: { color: "#b9bac5", fontSize: 11 } },
      hAxis: { textStyle: { color: "#8e919e", fontSize: 10 }, gridlines: { color: "#252834" }, baselineColor: "#383b49" },
      vAxis: { textStyle: { color: "#8e919e", fontSize: 10 }, gridlines: { color: "#252834" }, baselineColor: "#383b49", minValue: 0 },
      tooltip: { textStyle: { color: "#171922", fontSize: 12 } },
      annotations: { alwaysOutside: true, textStyle: { color: "#d9d7e8", fontSize: 9, auraColor: "none" } },
      pieHole: artifact.chart_type === "donut" ? 0.58 : 0,
      pieSliceText: "percentage",
      sliceVisibilityThreshold: 0.02,
      curveType: ["line", "area"].includes(artifact.chart_type) ? "function" : "none",
      areaOpacity: artifact.chart_type === "area" ? 0.28 : 0,
      pointSize: artifact.chart_type === "scatter" ? 7 : 4,
      trendlines: artifact.chart_type === "scatter" ? { 0: { type: "linear", color: "#f3b65a", lineWidth: 2, opacity: 0.75, showR2: true, visibleInLegend: false } } : undefined,
      animation: { startup: true, duration: 520, easing: "out" },
    });
    target.dataset.chartDrawn = "true";
  }
}

function renderToolActivity(activity) {
  if (!Array.isArray(activity) || !activity.length) return "";
  const labels = { completed: "Operación completada", blocked: "Operación bloqueada", unavailable: "Operación no disponible" };
  return `<div class="tool-activity-list" aria-label="Actividad de herramientas">${activity.map((item) => { const detail = item.query ? `${item.path || "."} · “${item.query}”` : `${item.path || "."} · ${item.kind || "unknown"}`; return `<div class="tool-activity ${escapeHtml(item.status || "unavailable")}"><span>◇</span><div><strong>${escapeHtml(item.capability || "workspace.read")} · ${escapeHtml(labels[item.status] || "Lectura")}</strong><small>${escapeHtml(detail)}</small></div></div>${renderDriveItems(item.items)}`; }).join("")}</div>`;
}

function isDriveReadableMime(mimeType) {
  const normalized = String(mimeType || "").toLowerCase();
  if (normalized.startsWith("text/")) return true;
  return new Set([
    "application/json",
    "application/xml",
    "application/pdf",
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ]).has(normalized);
}

function renderDriveItems(items) {
  if (!Array.isArray(items) || !items.length) return "";
  return `<div class="drive-result-grid">${items.map((item) => {
    const type = item.item_type || "file";
    const isFolder = type === "folder";
    const isEmail = type === "email";
    const isEvent = type === "event";
    const isRepository = type === "repository";
    const isImage = String(item.mime_type || "").toLowerCase().startsWith("image/");
    const parsedDate = item.modified_time ? new Date(item.modified_time) : null;
    const date = parsedDate && !Number.isNaN(parsedDate.valueOf())
      ? parsedDate.toLocaleString("es-CO", { dateStyle: "medium", timeStyle: isEvent ? "short" : undefined })
      : (item.modified_time || "");
    const label = isFolder ? "Carpeta" : isEmail ? (item.subtitle || "Correo") : isEvent ? (item.subtitle || "Evento") : isRepository ? (item.subtitle || "Repositorio de GitHub") : isImage ? "Imagen" : isDriveReadableMime(item.mime_type) ? "Documento legible" : "Archivo";
    const icon = isFolder ? "▰" : isEmail ? "✉" : isEvent ? "◫" : isRepository ? "⌘" : isImage ? "▧" : "▤";
    const canRead = Boolean(item.id) && (isEmail || (!isFolder && !isEvent && !isRepository && isDriveReadableMime(item.mime_type)));
    const openLabel = isRepository ? "Ver repositorio" : "Abrir";
    return `<article class="drive-result-card${isRepository ? " repository-result-card" : ""}"><span class="drive-result-icon" aria-hidden="true">${icon}</span><div class="drive-result-details"><strong title="${escapeHtml(item.name || "Resultado")}">${escapeHtml(item.name || "Resultado")}</strong><small>${escapeHtml(label)}${date ? ` · ${escapeHtml(date)}` : ""}</small></div><div class="drive-result-actions">${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${openLabel}</a>` : ""}${canRead ? `<button type="button" ${isEmail ? `data-gmail-read-id="${escapeHtml(item.id)}" data-gmail-read-subject="${escapeHtml(item.name || "correo")}"` : `data-drive-read-id="${escapeHtml(item.id)}" data-drive-read-name="${escapeHtml(item.name || "documento")}"`}>Leer</button>` : ""}</div></article>`;
  }).join("")}</div>`;
}

function renderConnectionOffers(offers) {
  if (!Array.isArray(offers) || !offers.length) return "";
  return `<div class="connection-offers" aria-label="Conexiones propuestas">${offers.map((offer) => `<section class="connection-offer"><div class="connection-provider">${escapeHtml(offer.provider)}</div><strong>${escapeHtml(offer.title)}</strong><p>${escapeHtml(offer.description)}</p><small>Permisos solicitados: ${escapeHtml((offer.permissions || []).join(", ") || "ninguno")}</small><button type="button" data-connect-plugin="${escapeHtml(offer.plugin_id)}">${escapeHtml(offer.action_label)}</button></section>`).join("")}</div>`;
}

async function continuePartnerChat(event) {
  event.preventDefault();
  const input = $("#partner-message-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = ""; input.style.height = "auto"; $("#partner-char-count").textContent = "0 / 6000";
  await sendPartnerMessage(message);
}

function resetPartnerChat() {
  // Reserve the conversation before the first message. Otherwise, a delayed
  // history refresh can interpret a null id as "open the latest chat" and
  // replace the blank conversation the user just requested.
  state.entryMode = "radar"; state.activeConversationId = newConversationId(); state.partnerMessages = []; state.partnerPhase = "discovery"; state.attachedDocumentIds = [];
  renderConversationHistory();
  renderAttachments();
  showWelcome(); $("#project-description").focus();
}

function readPartnerConversations(owner = sessionId) {
  try {
    const parsed = JSON.parse(localStorage.getItem(conversationStorageKey(owner)) || "[]");
    if (Array.isArray(parsed) && parsed.length) return parsed.filter(validConversation).slice(0, 40);
    const legacy = JSON.parse(localStorage.getItem(PARTNER_CHAT_KEY) || "[]");
    if (!Array.isArray(legacy) || !legacy.length) return [];
    const messages = legacy.filter(validPartnerMessage).slice(-32);
    if (!messages.length) return [];
    const first = messages.find((item) => item.role === "user")?.content || "Conversación recuperada";
    return [{ id: `chat_${crypto.randomUUID()}`, title: conversationTitle(first), messages, documentIds: [], phase: "discovery", updatedAt: new Date().toISOString() }];
  } catch { return []; }
}
function validPartnerMessage(item) { return ["user", "assistant"].includes(item?.role) && typeof item?.content === "string"; }
function validConversation(item) { return typeof item?.id === "string" && typeof item?.title === "string" && Array.isArray(item?.messages) && item.messages.every(validPartnerMessage); }
function conversationTitle(value) { const clean = String(value).trim().replace(/\s+/g, " "); return clean.length > 42 ? `${clean.slice(0, 42)}…` : clean || "Nueva conversación"; }
function renderAgentDraft(message, index) {
  const draft = message.agentDraft; if (message.intent !== "agent_creation" || !draft) return "";
  const framework = draft.recommended_framework;
  const missing = Array.isArray(draft.missing_information) ? draft.missing_information : [];
  const ready = Boolean(draft.ready_to_create && draft.name && draft.purpose);
  const created = Boolean(message.createdProjectId);
  const readiness = Math.max(0, Math.min(100, Number(draft.readiness || 0)));
  const builderLabel = state.buildRuntime === "antigravity_sdk" ? "Constructor: Antigravity SDK" : "Constructor seguro";
  const metrics = [
    ["Entradas", Array.isArray(draft.inputs) ? draft.inputs.length : 0],
    ["Resultados", Array.isArray(draft.outputs) ? draft.outputs.length : 0],
    ["Pasos", Array.isArray(draft.workflow) ? draft.workflow.length : 0],
  ];
  const metricsPanel = `<div class="draft-metrics">${metrics.map(([label, value]) => `<div><strong>${value}</strong><span>${label}</span></div>`).join("")}</div>`;
  const frameworkPanel = framework
    ? `<section class="draft-section draft-framework"><div class="draft-section-heading"><span>Framework recomendado</span><b class="draft-status ready">Seleccionado</b></div><div class="draft-framework-summary"><strong>${escapeHtml(framework.label)}</strong><em>${escapeHtml(framework.language)} · ${Number(framework.confidence || 0)}% de confianza</em></div><p>${escapeHtml(framework.reason)}</p></section>`
    : `<section class="draft-section draft-framework pending"><div class="draft-section-heading"><span>Framework recomendado</span><b class="draft-status">Pendiente</b></div><div class="draft-framework-summary"><strong>Aún no seleccionado</strong><em>Sin confianza calculada</em></div><p>Se elegirá cuando estén definidos la misión, las entradas, los resultados y al menos un paso del flujo.</p></section>`;
  const integrations = Array.isArray(draft.external_actions) && draft.external_actions.length
    ? `<section class="draft-section draft-integrations"><div class="draft-section-heading"><span>Accesos y herramientas</span><b class="draft-status warning">Requiere configuración</b></div><ul>${draft.external_actions.map((value) => `<li>${escapeHtml(value)}</li>`).join("")}</ul><small>Estos accesos todavía no están conectados. Cada uno requerirá permisos explícitos y aprobación antes de producir efectos externos.</small></section>`
    : `<section class="draft-section draft-capabilities"><div class="draft-section-heading"><span>Accesos y herramientas</span><b class="draft-status">Sin conectar</b></div><p>El agente todavía no tiene acceso a directorios, Internet, documentos privados, correo ni tickets.</p></section>`;
  const missingPanel = missing.length ? `<section class="draft-missing"><span>Decisiones pendientes</span><div>${missing.slice(0, 4).map((value) => `<em>${escapeHtml(value)}</em>`).join("")}</div></section>` : "";
  return `<section class="agent-draft-card"><header><div><span>DISEÑO PROPUESTO POR GEMINI</span><strong>${escapeHtml(draft.name || "Sin nombre todavía")}</strong></div><b>${readiness}%</b></header><div class="draft-progress" aria-label="Diseño completado al ${readiness}%"><i style="width:${readiness}%"></i></div>${draft.purpose ? `<section class="draft-purpose"><span>Objetivo</span><p>${escapeHtml(draft.purpose)}</p></section>` : ""}${metricsPanel}${frameworkPanel}${integrations}${missingPanel}<footer><small class="draft-builder-runtime">${escapeHtml(builderLabel)}</small>${created ? `<span class="draft-created">✓ Construcción iniciada · ${escapeHtml(framework?.label || "framework pendiente")}</span>` : ready && framework ? `<button type="button" data-create-agent-index="${index}">Aprobar diseño y construir en laboratorio</button>` : `<span>Continúa conversando con Gemini para completar el diseño.</span>`}</footer></section>`;
}
function renderAgentBuild(message, index) {
  const build = message.build || {};
  const events = Array.isArray(build.events) ? build.events : [];
  const terminal = terminalBuildStates.has(build.state);
  const expanded = Boolean(message.activityExpanded) || !terminal;
  const runtimeLabel = build.builder_runtime === "antigravity_sdk" ? "SDK Antigravity" : "Constructor local seguro";
  const stages = buildProgressStages(build, events);
  const eventMarkup = stages.map((stage, stageIndex) => {
    const statusIcon = stage.status === "complete" ? "✓" : stage.status === "failed" ? "×" : stage.status === "active" ? "" : String(stageIndex + 1);
    return `<li class="build-stage ${escapeHtml(stage.status)}"><span>${statusIcon}</span><div><strong>${escapeHtml(stage.label)}</strong><small>${escapeHtml(stage.detail)}</small></div></li>`;
  }).join("");
  const waiting = build.state === "awaiting_test_approval";
  const approval = waiting ? `<section class="build-approval"><span>APROBACIÓN HUMANA REQUERIDA</span><strong>¿Autorizar las verificaciones aisladas?</strong><p>Sin red, sin credenciales y sin acciones externas. Rechazar conserva los archivos y detiene el proceso.</p><div><button type="button" class="secondary" data-build-decision="rejected" data-build-index="${index}">Detener</button><button type="button" data-build-decision="approved" data-build-index="${index}">Autorizar pruebas</button></div></section>` : "";
  const tests = Array.isArray(build.tests) && build.tests.length ? `<div class="build-tests">${build.tests.map((test) => `<span class="${test.passed ? "pass" : "fail"}">${test.passed ? "✓" : "×"} ${escapeHtml(test.name)}</span>`).join("")}</div>` : "";
  const capabilities = Array.isArray(build.capabilities) && build.capabilities.length ? `<div class="build-capabilities"><span>Capacidades activas</span>${build.capabilities.map((capability) => `<b>${escapeHtml(capability)}</b>`).join("")}</div>` : "";
  const plugins = Array.isArray(build.plugins) && build.plugins.length ? `<div class="build-capabilities build-plugins"><span>Plugins seleccionados</span>${build.plugins.map((plugin) => `<b class="${escapeHtml(plugin.availability)}">${escapeHtml(plugin.title)} · ${plugin.availability === "available" ? "listo" : "requiere conexión"}</b>`).join("")}</div>` : `<div class="build-capabilities"><span>Plugins</span><b>Ninguno necesario</b></div>`;
  const complete = build.state === "completed" ? `<section class="build-result"><span>TASKMASTER GUARDADO EN PROYECTOS</span><strong>${escapeHtml(build.agent_name || "Taskmaster")}</strong><p>${Number(build.file_count || 0)} archivos · ${build.tests?.filter((test) => test.passed).length || 0}/${build.tests?.length || 0} verificaciones aprobadas · ${escapeHtml(build.framework?.label || "Framework seleccionado")}</p><p class="build-project-path">${escapeHtml(build.project_directory || "projects/")}</p>${capabilities}${plugins}${tests}<div><button type="button" class="secondary" data-toggle-build-index="${index}">${expanded ? "Ocultar actividad" : "Ver actividad"}</button><button type="button" data-open-built-agent-index="${index}">Usar Taskmaster</button></div></section>` : "";
  const stopped = ["failed", "stopped"].includes(build.state) ? `<section class="build-result build-stopped"><span>${build.state === "failed" ? "DETENIDO DE FORMA SEGURA" : "CONSTRUCCIÓN DETENIDA"}</span><strong>${escapeHtml(build.error || "No se realizaron efectos externos.")}</strong><button type="button" class="secondary" data-toggle-build-index="${index}">${expanded ? "Ocultar actividad" : "Ver actividad"}</button></section>` : "";
  return `<article class="partner-turn assistant-turn builder-turn"><div class="partner-avatar builder-avatar" aria-hidden="true">⌘</div><div><div class="builder-heading"><div><div class="turn-label">${escapeHtml(build.builder || "Ingeniero de agentes")}</div><strong>${escapeHtml(build.agent_name || "Construyendo agente")}</strong></div><span>${escapeHtml(runtimeLabel)}</span></div><p class="builder-boundary">Gemini terminó el diseño. Este constructor trabaja solo sobre la especificación confirmada y no muestra razonamiento privado.</p>${expanded && eventMarkup ? `<ol class="build-progress" aria-label="Progreso de construcción">${eventMarkup}</ol>` : ""}${approval}${complete}${stopped}<div class="turn-meta"><small>${escapeHtml(build.framework?.label || "Selección automática")} · ${escapeHtml(build.state || "queued")}</small></div></div></article>`;
}
function buildProgressStages(build, events) {
  const definitions = [
    { label: "Diseño aprobado", phases: ["handoff", "analysis", "framework"], fallback: "Contrato recibido por el Ingeniero de agentes." },
    { label: "Espacio preparado", phases: ["queue", "workspace"], fallback: "Preparando el entorno aislado." },
    { label: "Taskmaster construido", phases: ["generation", "policies"], fallback: "Generando archivos y políticas." },
    { label: "Autorización humana", phases: ["approval"], fallback: "Esperando la decisión para verificar." },
    { label: "Verificaciones", phases: ["testing"], fallback: "Ejecutando pruebas autorizadas." },
    { label: "Entrega completada", phases: ["completed"], fallback: "Guardando el Taskmaster en Proyectos." },
  ];
  const terminalComplete = build.state === "completed";
  const failedBuild = ["failed", "stopped"].includes(build.state);
  const stageData = definitions.map((definition) => {
    const matching = events.filter((event) => definition.phases.includes(event.phase));
    const latest = matching.at(-1);
    const passed = matching.some((event) => event.status === "passed");
    const failed = matching.some((event) => ["failed", "stopped"].includes(event.status));
    return { ...definition, latest, passed, failed };
  });
  let current = stageData.findIndex((stage) => !stage.passed);
  if (build.state === "awaiting_test_approval") current = 3;
  if (terminalComplete) current = -1;
  if (failedBuild && current < 0) current = Math.max(0, stageData.findIndex((stage) => stage.failed));
  return stageData.map((stage, index) => ({
    label: stage.label,
    detail: stage.latest?.message || stage.fallback,
    status: terminalComplete || stage.passed || (current >= 0 && index < current) ? "complete" : stage.failed || (failedBuild && index === current) ? "failed" : index === current ? "active" : "pending",
  }));
}
function buildPhaseLabel(phase) {
  return ({ handoff: "Relevo", analysis: "Especificación", framework: "Framework", workspace: "Espacio aislado", generation: "Archivos", policies: "Gobernanza", testing: "Laboratorio", approval: "Decisión humana", completed: "Entrega" })[phase] || phase;
}
function persistPartnerHistory() {
  const first = state.partnerMessages.find((item) => item.role === "user")?.content || "Nueva conversación";
  if (!state.activeConversationId) state.activeConversationId = newConversationId();
  const stored = { id: state.activeConversationId, title: conversationTitle(first), messages: state.partnerMessages.slice(-32), documentIds: [...state.attachedDocumentIds], phase: state.partnerPhase, agentId: state.activeCatalogAgent?.id || "", updatedAt: new Date().toISOString() };
  state.partnerConversations = [stored, ...state.partnerConversations.filter((item) => item.id !== stored.id)].slice(0, 40);
  localStorage.setItem(conversationStorageKey(), JSON.stringify(state.partnerConversations));
  localStorage.removeItem(PARTNER_CHAT_KEY); renderConversationHistory();
  scheduleConversationSync(stored);
}

function serializeConversation(conversation) {
  const allowedMessageKeys = ["role", "content", "sourceLanguage", "translations", "model", "provider", "intent", "agentDraft", "toolActivity", "connectionOffers", "artifacts", "kind", "createdProjectId", "buildId", "build", "activityExpanded", "sourceLabel"];
  const compactMessage = (message) => {
    const serialized = Object.fromEntries(allowedMessageKeys.filter((key) => message[key] !== undefined).map((key) => [key, message[key]]));
    if (serialized.build && Array.isArray(serialized.build.events)) {
      serialized.build = { ...serialized.build, events: serialized.build.events.slice(-16) };
    }
    return serialized;
  };
  return {
    title: conversation.title,
    phase: conversation.phase || "discovery",
    document_ids: conversation.documentIds || [],
    agent_id: conversation.agentId || null,
    messages: conversation.messages.slice(-32).map(compactMessage),
  };
}

function normalizeRemoteConversation(item) {
  return {
    id: item.id,
    title: item.title,
    messages: Array.isArray(item.messages) ? item.messages.filter(validPartnerMessage) : [],
    documentIds: Array.isArray(item.document_ids) ? item.document_ids : [],
    phase: item.phase || "discovery",
    agentId: item.agent_id || "",
    updatedAt: item.updated_at || new Date(0).toISOString(),
  };
}

function scheduleConversationSync(conversation) {
  if (!conversation?.id) return;
  clearTimeout(conversationSyncTimers.get(conversation.id));
  const timer = setTimeout(async () => {
    conversationSyncTimers.delete(conversation.id);
    try {
      await api(`/api/v1/collaborative/conversations/${encodeURIComponent(conversation.id)}`, {
        method: "PUT",
        background: true,
        body: JSON.stringify(serializeConversation(conversation)),
      });
    } catch (error) {
      console.warn("Conversation remains available in this browser; server sync paused.", error);
    }
  }, 250);
  conversationSyncTimers.set(conversation.id, timer);
}

async function loadConversationMemory() {
  try {
    const payload = await api("/api/v1/collaborative/conversations", { background: true });
    const remote = (payload.conversations || []).map(normalizeRemoteConversation).filter(validConversation);
    // A successful server read is authoritative. Re-uploading browser-only
    // records here can resurrect conversations that were deleted elsewhere.
    state.partnerConversations = remote.sort((left, right) => Date.parse(right.updatedAt || 0) - Date.parse(left.updatedAt || 0)).slice(0, 40);
    const active = state.partnerConversations.find((item) => item.id === state.activeConversationId);
    if (active) {
      state.partnerMessages = [...active.messages];
      state.partnerPhase = active.phase || "discovery";
      state.attachedDocumentIds = [...(active.documentIds || [])];
    }
    localStorage.setItem(conversationStorageKey(), JSON.stringify(state.partnerConversations));
    renderConversationHistory();
    renderAttachments();
  } catch (error) {
    console.warn("Using browser conversation memory until server sync is available.", error);
  }
}

async function loadBuildHistory() {
  try {
    const payload = await api("/api/v1/collaborative/builds", { background: true });
    state.builds = Array.isArray(payload.builds) ? payload.builds : [];
    reconcileOrphanBuilds();
  } catch (error) {
    console.warn("Build recovery is temporarily unavailable.", error);
  }
}

function reconcileOrphanBuilds() {
  const claimed = new Set(state.partnerConversations.flatMap((conversation) => conversation.messages.map((message) => message.buildId || message.build?.build_id).filter(Boolean)));
  const conversations = [...state.partnerConversations].sort((left, right) => Date.parse(right.updatedAt || 0) - Date.parse(left.updatedAt || 0));
  for (const build of state.builds) {
    if (!build?.build_id || claimed.has(build.build_id)) continue;
    const conversation = conversations.find((item) => [...item.messages].reverse().some((message) => message.role === "assistant" && message.agentDraft?.ready_to_create && message.agentDraft?.name === build.agent_name && !message.createdProjectId));
    if (!conversation) continue;
    const source = [...conversation.messages].reverse().find((message) => message.role === "assistant" && message.agentDraft?.ready_to_create && message.agentDraft?.name === build.agent_name && !message.createdProjectId);
    source.createdProjectId = build.project_id;
    source.buildId = build.build_id;
    conversation.messages.push({ role: "assistant", kind: "agent_build", content: "Construcción del agente", build, activityExpanded: true });
    conversation.updatedAt = new Date().toISOString();
    claimed.add(build.build_id);
    scheduleConversationSync(conversation);
  }
  localStorage.setItem(conversationStorageKey(), JSON.stringify(state.partnerConversations));
  const active = state.partnerConversations.find((item) => item.id === state.activeConversationId);
  if (active) {
    state.partnerMessages = [...active.messages];
    renderPartnerConversation();
  }
}
function renderConversationHistory() {
  $("#conversation-history").innerHTML = state.partnerConversations.map((item) => {
    const active = item.id === state.activeConversationId;
    return `<div class="history-entry ${active ? "active" : ""}"><button class="history-select" type="button" data-conversation-id="${escapeHtml(item.id)}" title="${escapeHtml(item.title)}"><span aria-hidden="true">${active ? "●" : "◌"}</span><strong>${escapeHtml(item.title)}</strong></button><button class="history-delete" type="button" data-delete-conversation="${escapeHtml(item.id)}" aria-label="Eliminar ${escapeHtml(item.title)}">×</button></div>`;
  }).join("");
  $("#history-empty").hidden = state.partnerConversations.length > 0;
}

async function loadAgentCatalog() {
  try {
    const payload = await api("/api/v1/collaborative/agents", { background: true });
    state.agents = Array.isArray(payload.agents) ? payload.agents : [];
    renderAgentCatalog();
  } catch (error) {
    console.warn("Agent catalog unavailable.", error);
  }
}

async function loadIdentity() {
  try {
    state.identity = await api("/api/v1/collaborative/identity", { background: true });
    const label = state.identity.authenticated ? (state.identity.email || "Usuario verificado") : "Sesión local de desarrollo";
    const target = $("#identity-label"); if (target) target.textContent = label;
    const detail = $("#identity-detail"); if (detail) detail.textContent = state.identity.authenticated ? "Datos y conexiones aislados" : "Sin cuentas externas reales";
    renderAccountIdentity();
  } catch (error) { console.warn("Identity metadata unavailable.", error); }
}

async function loadConnections() {
  try {
    const payload = await api("/api/v1/collaborative/connections", { background: true });
    state.connections = payload.connections || [];
    renderConnections();
  } catch (error) { console.warn("Connection catalog unavailable.", error); }
}

function renderConnections() {
  const target = $("#connection-catalog"); if (!target) return;
  const currentByPlugin = new Map();
  for (const item of state.connections) {
    if (!currentByPlugin.has(item.plugin_id)) currentByPlugin.set(item.plugin_id, item);
  }
  const catalog = CONNECTION_CATALOG.map((available) => ({ ...available, connection: currentByPlugin.get(available.plugin_id) }));
  target.innerHTML = catalog.map(({ plugin_id, title, connection }) => {
    const status = connection?.status || "not_connected";
    const connected = status === "connected";
    const pending = status === "pending";
    const action = connected || pending
      ? `data-revoke-connection="${escapeHtml(connection.id)}"`
      : `data-connect-plugin="${escapeHtml(plugin_id)}"`;
    const verb = connected ? "Desconectar" : pending ? "Cancelar autorización de" : status === "not_connected" || status === "revoked" ? "Conectar" : "Reintentar conexión con";
    return `<button class="connection-icon-button ${connected ? "is-connected" : pending ? "is-pending" : ""}" type="button" ${action} aria-label="${escapeHtml(`${verb} ${title}`)}" title="${escapeHtml(`${title} · ${connection?.account_label || connectionStatusLabel(status)}`)}"><span class="connection-brand" aria-hidden="true">${connectionBrandIcon(plugin_id)}</span><i aria-hidden="true"></i></button>`;
  }).join("");
  $("#connection-empty").hidden = true;
}

function connectionBrandIcon(pluginId) {
  const icons = {
    "google.drive": `<svg viewBox="0 0 24 24"><path fill="#0F9D58" d="M8.1 3h5.2l7.7 13.3h-5.2z"/><path fill="#F4B400" d="M8.1 3 3 11.9l2.6 4.4 7.7-13.3z"/><path fill="#4285F4" d="M5.6 16.3h15L18 21H3z"/></svg>`,
    "google.gmail": `<svg viewBox="0 0 24 24"><path fill="#EA4335" d="M3 5.2 12 12l9-6.8v2.7l-9 6.8-9-6.8z"/><path fill="#4285F4" d="M3 7.9v10.9H7V11z"/><path fill="#34A853" d="M17 11v7.8h4V7.9z"/><path fill="#C5221F" d="M3 5.2V8l4 3V8.2z"/><path fill="#FBBC04" d="M21 5.2V8l-4 3V8.2z"/></svg>`,
    "google.calendar": `<svg viewBox="0 0 24 24"><path fill="#fff" d="M4 4h16v16H4z"/><path fill="#4285F4" d="M4 4h16v5H4z"/><path fill="#34A853" d="M4 9h5v11H4z"/><path fill="#FBBC04" d="M9 16h11v4H9z"/><path fill="#EA4335" d="M16 9h4v7h-4z"/><text x="12.3" y="15.2" text-anchor="middle" font-size="6.4" font-weight="800" fill="#4285F4">31</text></svg>`,
    github: `<svg viewBox="0 0 24 24"><path fill="#fff" d="M12 2.7a9.6 9.6 0 0 0-3 18.7c.5.1.7-.2.7-.5v-1.8c-2.8.6-3.4-1.2-3.4-1.2-.5-1.2-1.1-1.5-1.1-1.5-.9-.6.1-.6.1-.6 1 0 1.6 1 1.6 1 .9 1.6 2.4 1.1 3 .9.1-.7.4-1.1.7-1.3-2.3-.3-4.7-1.1-4.7-5.1 0-1.1.4-2 1-2.8-.1-.3-.4-1.3.1-2.7 0 0 .9-.3 2.8 1.1a9.7 9.7 0 0 1 5.1 0c1.9-1.3 2.8-1.1 2.8-1.1.5 1.4.2 2.4.1 2.7.6.8 1 1.7 1 2.8 0 4-2.4 4.8-4.7 5.1.4.3.7 1 .7 1.9v2.8c0 .3.2.6.7.5A9.6 9.6 0 0 0 12 2.7z"/></svg>`,
  };
  return icons[pluginId] || `<span>↗</span>`;
}

function connectionStatusLabel(status) { return ({ connected: "Conectada", pending: "Autorización pendiente", setup_required: "Configuración requerida", error: "Requiere atención", revoked: "Desconectada", not_connected: "Disponible" })[status] || status; }

async function startConnection(pluginId) {
  const popup = window.open("", "studio-oauth", "popup=yes,width=560,height=720,resizable=yes,scrollbars=yes");
  try {
    const payload = await api(`/api/v1/collaborative/connections/${encodeURIComponent(pluginId)}/start`, { method: "POST" });
    state.connections = [payload.connection, ...state.connections.filter((item) => item.plugin_id !== pluginId)];
    renderConnections();
    if (payload.authorization_url) {
      if (popup) { popup.location.replace(payload.authorization_url); popup.focus(); }
      else window.location.assign(payload.authorization_url);
      return;
    }
    if (popup) popup.close();
    notify(payload.connection.message, payload.connection.status === "setup_required" ? "info" : "error");
  } catch (error) { if (popup) popup.close(); handle(error); }
}

window.addEventListener("message", async (event) => {
  if (event.origin !== window.location.origin || event.data?.type !== "studio-oauth-result") return;
  const provider = ({ "google.drive": "Google Drive", "google.gmail": "Gmail", "google.calendar": "Google Calendar" })[event.data.provider] || "el servicio";
  if (event.data.outcome === "connected") {
    notify(`${provider} quedó conectado a esta cuenta con permisos de solo lectura.`, "success");
    await loadConnections();
  } else {
    notify(`No se pudo completar la conexión con ${provider}. Puedes reintentarlo.`, "error");
  }
});

async function revokeConnection(connectionId) {
  const connection = state.connections.find((item) => item.id === connectionId); if (!connection) return;
  const verb = connection.status === "connected" ? "Desconectar" : "Cancelar la conexión pendiente con";
  if (!window.confirm(`${verb} ${connection.title}?`)) return;
  try {
    const revoked = await api(`/api/v1/collaborative/connections/${encodeURIComponent(connectionId)}`, { method: "DELETE" });
    state.connections = state.connections.map((item) => item.id === revoked.id ? revoked : item); renderConnections();
  } catch (error) { handle(error); }
}
function agentIcon(icon) { return ({ spark: "✦", workflow: "⌁", document: "▤", research: "⌕", operations: "⚙", shield: "◇" })[icon] || "✦"; }
function renderAgentCatalog() {
  $("#agent-count").textContent = String(state.agents.length);
  $("#agent-catalog-empty").hidden = state.agents.length > 0;
  $("#agent-catalog").innerHTML = state.agents.map((agent) => `<article class="catalog-entry"><button type="button" class="catalog-open" data-open-agent="${escapeHtml(agent.id)}"><span>${agentIcon(agent.icon)}</span><div><strong>${escapeHtml(agent.name)}</strong><small>${escapeHtml(agent.framework_label)} · v${Number(agent.version || 1)}</small></div></button><button type="button" class="catalog-delete" data-delete-agent="${escapeHtml(agent.id)}" aria-label="Archivar ${escapeHtml(agent.name)}">×</button></article>`).join("");
}
function handleAgentCatalog(event) {
  const remove = event.target.closest("[data-delete-agent]");
  if (remove) { archiveAgent(remove.dataset.deleteAgent); return; }
  const open = event.target.closest("[data-open-agent]"); if (!open) return;
  const agent = state.agents.find((item) => item.id === open.dataset.openAgent); if (!agent) return;
  openCatalogAgent(agent);
}

function openCatalogAgent(agent) {
  state.activeCatalogAgent = agent;
  state.entryMode = "runtime";
  state.activeConversationId = null;
  state.partnerPhase = "runtime";
  state.attachedDocumentIds = [];
  state.partnerMessages = [{
    role: "assistant",
    sourceLabel: agent.name,
    sourceLanguage: "es",
    content: `**${agent.name} está activo.**\n\n${agent.purpose}\n\nProyecto persistente: ${agent.artifact_directory}\nFramework: ${agent.framework_label}\n\nEscribe una solicitud para ejecutar este Taskmaster. Sus herramientas respetarán las conexiones y aprobaciones disponibles.`,
    provider: "Taskmaster Runtime",
    model: state.runtime.model,
    revealResponse: true,
  }];
  showPartnerChat();
  renderPartnerConversation();
  renderConversationHistory();
  renderAttachments();
  document.body.classList.remove("sidebar-open");
  translateCurrentConversation();
}
async function archiveAgent(agentId) {
  const agent = state.agents.find((item) => item.id === agentId); if (!agent || !window.confirm(`¿Archivar “${agent.name}”? El paquete generado no se eliminará.`)) return;
  try { await api(`/api/v1/collaborative/agents/${encodeURIComponent(agentId)}`, { method: "DELETE", background: true }); state.agents = state.agents.filter((item) => item.id !== agentId); renderAgentCatalog(); }
  catch (error) { handle(error); }
}
function handleConversationHistory(event) {
  const deleteButton = event.target.closest("[data-delete-conversation]");
  if (deleteButton) { deleteConversation(deleteButton.dataset.deleteConversation); return; }
  const selectButton = event.target.closest("[data-conversation-id]"); if (!selectButton) return;
  const conversation = state.partnerConversations.find((item) => item.id === selectButton.dataset.conversationId); if (!conversation) return;
  state.activeConversationId = conversation.id; state.partnerMessages = [...conversation.messages]; state.partnerPhase = conversation.phase || "discovery";
  state.activeCatalogAgent = state.agents.find((agent) => agent.id === conversation.agentId)
    || state.agents.find((agent) => conversation.messages.some((message) => message.sourceLabel === agent.name))
    || null;
  if (state.activeCatalogAgent && !conversation.agentId) conversation.agentId = state.activeCatalogAgent.id;
  state.entryMode = state.activeCatalogAgent ? "runtime" : "radar";
  state.attachedDocumentIds = [...(conversation.documentIds || [])];
  showPartnerChat(); renderPartnerConversation(); renderConversationHistory(); renderAttachments(); document.body.classList.remove("sidebar-open"); translateCurrentConversation();
}
function deleteConversation(conversationId) {
  const conversation = state.partnerConversations.find((item) => item.id === conversationId); if (!conversation) return;
  if (!window.confirm(`¿Eliminar “${conversation.title}”?`)) return;
  state.partnerConversations = state.partnerConversations.filter((item) => item.id !== conversationId);
  localStorage.setItem(conversationStorageKey(), JSON.stringify(state.partnerConversations));
  clearTimeout(conversationSyncTimers.get(conversationId)); conversationSyncTimers.delete(conversationId);
  api(`/api/v1/collaborative/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE", background: true }).catch((error) => console.warn("Server deletion will need to be retried.", error));
  if (state.activeConversationId === conversationId) {
    const next = state.partnerConversations[0]; state.activeConversationId = next?.id || null; state.partnerMessages = next ? [...next.messages] : []; state.partnerPhase = next?.phase || "discovery"; state.attachedDocumentIds = next ? [...(next.documentIds || [])] : [];
    state.activeCatalogAgent = next ? state.agents.find((agent) => agent.id === next.agentId) || null : null;
    if (next) { showPartnerChat(); renderPartnerConversation(); } else showWelcome();
  }
  renderConversationHistory();
  renderAttachments();
}

async function loadDocumentLibrary() {
  try {
    const payload = await api("/api/v1/collaborative/documents", { background: true });
    state.documents = payload.documents || [];
    const available = new Set(state.documents.map((item) => item.id));
    state.attachedDocumentIds = state.attachedDocumentIds.filter((id) => available.has(id));
    renderAttachments();
  } catch (error) {
    console.warn("Document library unavailable.", error);
  }
}

async function uploadDocuments(files) {
  const remaining = Math.max(0, MAX_SESSION_DOCUMENTS - state.documents.length - state.documentUploads.filter((item) => item.status === "uploading").length);
  if (!remaining) { notify("La sesión ya contiene 12 archivos. Elimina uno para cargar otro.", "error"); return; }
  const selected = [...files].slice(0, remaining);
  if (files.length > remaining) notify(`Se cargarán ${remaining} archivos; la sesión admite un máximo de 12.`, "error");
  const uploads = selected.map((file) => ({
    id: `upload_${crypto.randomUUID()}`,
    file,
    name: file.name,
    size_bytes: file.size,
    progress: 0,
    status: file.size > MAX_LARGE_DOCUMENT_UPLOAD_BYTES ? "failed" : "uploading",
    error: file.size > MAX_LARGE_DOCUMENT_UPLOAD_BYTES ? "El archivo supera el límite de 600 MB." : "",
  }));
  state.documentUploads.push(...uploads);
  renderAttachments();
  for (const upload of uploads) {
    if (upload.status === "failed") continue;
    try {
      const payload = await uploadDocumentWithProgress(upload);
      state.documentUploads = state.documentUploads.filter((item) => item.id !== upload.id);
      state.documents = [payload, ...state.documents.filter((item) => item.id !== payload.id)];
      if (!state.attachedDocumentIds.includes(payload.id) && state.attachedDocumentIds.length < MAX_SESSION_DOCUMENTS) state.attachedDocumentIds.push(payload.id);
      renderAttachments();
      if (state.partnerMessages.length) persistPartnerHistory();
    } catch (error) {
      if (upload.status === "cancelled") continue;
      upload.status = "failed";
      upload.error = error.message || `No se pudo leer ${upload.name}.`;
      renderAttachments();
      handle(error);
    }
  }
}

function closeAttachmentMenus() { $$(".attachment-menu[open]").forEach((menu) => menu.removeAttribute("open")); }

function uploadDocumentWithProgress(upload) {
  if (upload.file.size > MAX_DOCUMENT_UPLOAD_BYTES) return uploadLargeDocument(upload);
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const form = new FormData(); form.append("file", upload.file);
    activeDocumentUploads.set(upload.id, request);
    request.open("POST", "/api/v1/collaborative/documents");
    request.withCredentials = true;
    Object.entries(identityHeaders()).forEach(([name, value]) => request.setRequestHeader(name, value));
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      upload.progress = Math.min(99, Math.round((event.loaded / event.total) * 100));
      renderAttachments();
    });
    request.addEventListener("load", () => {
      activeDocumentUploads.delete(upload.id);
      let payload = {};
      try { payload = request.responseText ? JSON.parse(request.responseText) : {}; }
      catch {
        reject(new Error(request.status === 413 ? "El archivo supera el límite de 25 MB." : "El servidor devolvió una respuesta inesperada."));
        return;
      }
      if (request.status < 200 || request.status >= 300) { reject(new Error(payload.error?.message || `No se pudo leer ${upload.name}.`)); return; }
      upload.progress = 100;
      renderAttachments();
      resolve(payload);
    });
    request.addEventListener("error", () => { activeDocumentUploads.delete(upload.id); reject(new Error(`No se pudo cargar ${upload.name}.`)); });
    request.addEventListener("abort", () => { activeDocumentUploads.delete(upload.id); reject(new Error("Carga cancelada.")); });
    request.send(form);
  });
}

async function uploadLargeDocument(upload) {
  const extension = upload.name.split(".").pop()?.toLowerCase();
  if (!new Set(["csv", "xlsx"]).has(extension)) {
    throw new Error("Las cargas mayores de 25 MB admiten archivos CSV y XLSX.");
  }
  const controller = new AbortController();
  activeDocumentUploads.set(upload.id, controller);
  let serverUploadId = "";
  try {
    const started = await api("/api/v1/collaborative/document-uploads", {
      method: "POST",
      body: JSON.stringify({ filename: upload.name, size_bytes: upload.file.size }),
      background: true,
    });
    serverUploadId = started.id;
    upload.serverUploadId = serverUploadId;
    let offset = Number(started.received_bytes || 0);
    while (offset < upload.file.size) {
      const chunk = upload.file.slice(offset, Math.min(offset + DOCUMENT_UPLOAD_CHUNK_BYTES, upload.file.size));
      const response = await fetch(`/api/v1/collaborative/document-uploads/${encodeURIComponent(serverUploadId)}?offset=${offset}`, {
        method: "PUT",
        credentials: "same-origin",
        headers: identityHeaders({ "Content-Type": "application/octet-stream" }),
        body: chunk,
        signal: controller.signal,
      });
      const raw = await response.text();
      let payload = {};
      try { payload = raw ? JSON.parse(raw) : {}; }
      catch { throw new Error("El servidor devolvió una respuesta inesperada durante la carga."); }
      if (!response.ok) throw new Error(payload.error?.message || "No se pudo cargar un bloque del archivo.");
      offset = Number(payload.received_bytes || offset + chunk.size);
      upload.progress = Math.min(99, Math.round((offset / upload.file.size) * 100));
      renderAttachments();
    }
    const result = await api(`/api/v1/collaborative/document-uploads/${encodeURIComponent(serverUploadId)}/complete`, {
      method: "POST",
      body: JSON.stringify({}),
      background: true,
    });
    upload.progress = 100;
    return result;
  } catch (error) {
    if (serverUploadId) {
      api(`/api/v1/collaborative/document-uploads/${encodeURIComponent(serverUploadId)}`, { method: "DELETE", background: true }).catch(() => {});
    }
    throw error;
  } finally {
    activeDocumentUploads.delete(upload.id);
  }
}

function cancelDocumentUpload(uploadId) {
  const upload = state.documentUploads.find((item) => item.id === uploadId);
  if (upload) upload.status = "cancelled";
  activeDocumentUploads.get(uploadId)?.abort();
  if (upload?.serverUploadId) {
    api(`/api/v1/collaborative/document-uploads/${encodeURIComponent(upload.serverUploadId)}`, { method: "DELETE", background: true }).catch(() => {});
  }
  state.documentUploads = state.documentUploads.filter((item) => item.id !== uploadId);
  renderAttachments();
}

async function deleteDocument(documentId) {
  const document = state.documents.find((item) => item.id === documentId); if (!document) return;
  if (!window.confirm(`¿Eliminar “${document.name}” de esta sesión?`)) return;
  try {
    await api(`/api/v1/collaborative/documents/${encodeURIComponent(documentId)}`, { method: "DELETE", background: true });
    state.documents = state.documents.filter((item) => item.id !== documentId);
    state.attachedDocumentIds = state.attachedDocumentIds.filter((id) => id !== documentId);
    state.partnerConversations = state.partnerConversations.map((item) => ({ ...item, documentIds: (item.documentIds || []).filter((id) => id !== documentId) }));
    localStorage.setItem(conversationStorageKey(), JSON.stringify(state.partnerConversations));
    state.partnerConversations.forEach(scheduleConversationSync);
    renderAttachments();
  } catch (error) { handle(error); }
}

function renderAttachments() {
  const uploads = state.documentUploads.map((item) => `<article class="document-card ${escapeHtml(item.status)}"><b aria-hidden="true">▤</b><div class="document-card-copy"><strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong><small>${item.status === "failed" ? escapeHtml(item.error) : `Cargando · ${item.progress}%`}</small><span class="document-progress"><i style="width:${item.progress}%"></i></span></div><button type="button" class="document-icon-action danger-action" data-cancel-upload="${escapeHtml(item.id)}" aria-label="${item.status === "failed" ? "Quitar" : "Cancelar carga de"} ${escapeHtml(item.name)}">×</button></article>`).join("");
  const documents = [...state.documents].sort((left, right) => Number(state.attachedDocumentIds.includes(right.id)) - Number(state.attachedDocumentIds.includes(left.id))).map((item) => {
    const attached = state.attachedDocumentIds.includes(item.id);
    const icon = item.media_type ? "▧" : "▤";
    return `<article class="document-card ready${attached ? " attached" : ""}"><b aria-hidden="true">${icon}</b><button type="button" class="document-card-copy" data-inspect-document="${escapeHtml(item.id)}" aria-label="Inspeccionar ${escapeHtml(item.name)}"><strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong><small>${Math.max(1, Math.round(item.size_bytes / 1024))} KB · ${attached ? "Adjunto a este chat" : "Disponible en la sesión"}</small></button><div class="document-card-actions"><button type="button" class="document-icon-action" data-toggle-document="${escapeHtml(item.id)}" aria-label="${attached ? "Quitar del chat" : "Adjuntar al chat"} ${escapeHtml(item.name)}" title="${attached ? "Quitar del chat" : "Adjuntar al chat"}">${attached ? "✓" : "+"}</button><button type="button" class="document-icon-action" data-inspect-document="${escapeHtml(item.id)}" aria-label="Inspeccionar ${escapeHtml(item.name)}" title="Inspeccionar">⌕</button><button type="button" class="document-icon-action danger-action" data-delete-document="${escapeHtml(item.id)}" aria-label="Eliminar ${escapeHtml(item.name)}" title="Eliminar">×</button></div></article>`;
  }).join("");
  const count = state.documents.length;
  const markup = uploads || documents ? `<div class="document-tray-heading"><strong>Archivos de la sesión</strong><small>${count} / 12</small></div><div class="document-cards">${uploads}${documents}</div>` : "";
  ["#welcome-attachments", "#chat-attachments"].forEach((selector) => { const target = $(selector); if (target) target.innerHTML = markup; });
  const manager = $("#file-manager-attachments");
  if (manager) manager.innerHTML = markup || '<p class="file-manager-empty">Todavía no hay archivos cargados.</p>';
  const countLabel = $("#managed-file-count");
  if (countLabel) countLabel.textContent = `${count} ${count === 1 ? "archivo cargado" : "archivos cargados"}`;
}

async function openFileManager() {
  $("#account-settings").removeAttribute("open");
  await loadDocumentLibrary();
  renderAttachments();
  const dialog = $("#file-manager");
  if (!dialog.open) dialog.showModal();
}

function handleAttachmentClick(event) {
  const cancel = event.target.closest("[data-cancel-upload]");
  if (cancel) { cancelDocumentUpload(cancel.dataset.cancelUpload); return; }
  const inspect = event.target.closest("[data-inspect-document]");
  if (inspect) { inspectDocument(inspect.dataset.inspectDocument); return; }
  const toggle = event.target.closest("[data-toggle-document]");
  if (toggle) { toggleDocumentAttachment(toggle.dataset.toggleDocument); return; }
  const remove = event.target.closest("[data-delete-document]");
  if (remove) deleteDocument(remove.dataset.deleteDocument);
}

function toggleDocumentAttachment(documentId) {
  if (state.attachedDocumentIds.includes(documentId)) state.attachedDocumentIds = state.attachedDocumentIds.filter((id) => id !== documentId);
  else {
    if (state.attachedDocumentIds.length >= MAX_SESSION_DOCUMENTS) { notify("Puedes adjuntar hasta 12 archivos a una conversación. Quita uno para añadir otro.", "error"); return; }
    state.attachedDocumentIds.push(documentId);
  }
  renderAttachments();
  if (state.partnerMessages.length) persistPartnerHistory();
}

async function inspectDocument(documentId) {
  const document = state.documents.find((item) => item.id === documentId);
  if (!document) return;
  const dialog = $("#document-inspector");
  const image = $("#document-inspector-image");
  $("#document-inspector-title").textContent = document.name;
  $("#document-inspector-meta").textContent = "Preparando vista segura…";
  $("#document-inspector-content").textContent = "Cargando contenido extraído…";
  image.hidden = true; image.removeAttribute("src");
  if (!dialog.open) dialog.showModal();
  try {
    const payload = await api(`/api/v1/collaborative/documents/${encodeURIComponent(documentId)}`, { background: true });
    $("#document-inspector-meta").textContent = `${Math.max(1, Math.round(payload.size_bytes / 1024))} KB · ${payload.characters.toLocaleString()} caracteres${payload.truncated ? " · vista recortada" : ""}`;
    $("#document-inspector-content").textContent = payload.content || "El documento no contiene texto visible.";
    if (payload.media?.data_base64 && payload.media?.mime_type) {
      image.src = `data:${payload.media.mime_type};base64,${payload.media.data_base64}`;
      image.hidden = false;
    }
  } catch (error) { dialog.close(); handle(error); }
}
function openChatHome() {
  state.entryMode = "radar"; state.activeCatalogAgent = null; state.activeConversationId = null; state.partnerMessages = []; state.partnerPhase = "discovery"; state.attachedDocumentIds = [];
  renderConversationHistory(); renderAttachments(); showWelcome();
}
function enableComposerKeyboard(textarea, submit) {
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); submit.requestSubmit(); }
  });
  textarea.addEventListener("input", () => { textarea.style.height = "auto"; textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`; });
}
async function handlePartnerConversationAction(event) {
  const gmailReadButton = event.target.closest("[data-gmail-read-id]");
  if (gmailReadButton) {
    await sendPartnerMessage(`Lee en mi Gmail el correo «${gmailReadButton.dataset.gmailReadSubject}» con id ${gmailReadButton.dataset.gmailReadId} y resume su contenido.`);
    return;
  }
  const driveReadButton = event.target.closest("[data-drive-read-id]");
  if (driveReadButton) {
    await sendPartnerMessage(`Lee en mi Google Drive el archivo «${driveReadButton.dataset.driveReadName}» con id ${driveReadButton.dataset.driveReadId} y resume su contenido.`);
    return;
  }
  const connectionButton = event.target.closest("[data-connect-plugin]");
  if (connectionButton) { await startConnection(connectionButton.dataset.connectPlugin); return; }
  const createButton = event.target.closest("[data-create-agent-index]");
  if (createButton) { await startAgentBuild(Number(createButton.dataset.createAgentIndex)); return; }
  const decisionButton = event.target.closest("[data-build-decision]");
  if (decisionButton) { await decideAgentBuild(Number(decisionButton.dataset.buildIndex), decisionButton.dataset.buildDecision); return; }
  const openBuiltButton = event.target.closest("[data-open-built-agent-index]");
  if (openBuiltButton) {
    const message = state.partnerMessages[Number(openBuiltButton.dataset.openBuiltAgentIndex)];
    const agentId = message?.build?.catalog_agent_id;
    if (!agentId) return;
    if (!state.agents.some((agent) => agent.id === agentId)) await loadAgentCatalog();
    const agent = state.agents.find((item) => item.id === agentId);
    if (agent) openCatalogAgent(agent);
    return;
  }
  const toggleButton = event.target.closest("[data-toggle-build-index]");
  if (toggleButton) { const message = state.partnerMessages[Number(toggleButton.dataset.toggleBuildIndex)]; if (message) { message.activityExpanded = !message.activityExpanded; persistPartnerHistory(); renderPartnerConversation(); } return; }
  const button = event.target.closest("[data-copy-index]"); if (!button) return;
  const message = state.partnerMessages[Number(button.dataset.copyIndex)]; if (!message) return;
  await navigator.clipboard.writeText(localizedMessageContent(message)); button.textContent = state.language === "en" ? "Copied" : "Copiado"; setTimeout(() => { button.textContent = state.language === "en" ? "Copy" : "Copiar"; }, 1500);
}

function handleConnectionCatalog(event) {
  const connect = event.target.closest("[data-connect-plugin]");
  if (connect) { startConnection(connect.dataset.connectPlugin); return; }
  const button = event.target.closest("[data-revoke-connection]");
  if (button) revokeConnection(button.dataset.revokeConnection);
}
async function startAgentBuild(messageIndex) {
  const source = state.partnerMessages[messageIndex]; const draft = source?.agentDraft;
  if (!draft?.ready_to_create || source.createdProjectId) return;
  if (!window.confirm(`Gemini propone construir «${draft.name}» con ${draft.recommended_framework?.label || "el framework recomendado"}. ¿Confirmas el relevo al Ingeniero de agentes?`)) return;
  try {
    const payload = await api("/api/v1/collaborative/builds", { method: "POST", idempotent: "chat-agent-build", body: JSON.stringify({ agent_draft: draft, confirmation: "CONSTRUIR_AGENTE" }) });
    source.createdProjectId = payload.project_id; source.buildId = payload.build_id;
    state.partnerMessages.push({ role: "assistant", kind: "agent_build", content: "Construcción del agente", build: payload, activityExpanded: true });
    persistPartnerHistory(); renderPartnerConversation(); scheduleBuildPoll(payload.build_id, 250);
  } catch (error) { handle(error); }
}
async function decideAgentBuild(messageIndex, decision) {
  const message = state.partnerMessages[messageIndex]; const buildId = message?.build?.build_id; if (!buildId) return;
  try {
    message.build = await api(`/api/v1/collaborative/builds/${encodeURIComponent(buildId)}/test-decision`, { method: "POST", idempotent: "chat-build-decision", body: JSON.stringify({ decision }) });
    persistPartnerHistory(); renderPartnerConversation();
    if (!terminalBuildStates.has(message.build.state)) scheduleBuildPoll(buildId, 250);
  } catch (error) { handle(error); }
}
function resumeBuildPolling() {
  state.partnerMessages.filter((item) => item.kind === "agent_build" && item.build?.build_id && !terminalBuildStates.has(item.build.state)).forEach((item) => scheduleBuildPoll(item.build.build_id, 450));
}
function scheduleBuildPoll(buildId, delay = 500) {
  if (buildPollers.has(buildId)) return;
  const timer = setTimeout(async () => {
    buildPollers.delete(buildId);
    try {
      const payload = await api(`/api/v1/collaborative/builds/${encodeURIComponent(buildId)}`, { background: true });
      const message = state.partnerMessages.find((item) => item.kind === "agent_build" && item.build?.build_id === buildId);
      if (!message) return;
      const previousState = message.build.state;
      const previousSnapshot = JSON.stringify(message.build);
      const changed = previousSnapshot !== JSON.stringify(payload);
      message.build = payload;
      if (payload.state === "completed") message.activityExpanded = false;
      if (payload.state === "completed" && previousState !== "completed") loadAgentCatalog();
      if (changed) { persistPartnerHistory(); renderPartnerConversation(); }
      if (!terminalBuildStates.has(payload.state) && payload.state !== "awaiting_test_approval") scheduleBuildPoll(buildId, 500);
      if (previousState !== payload.state && payload.state === "awaiting_test_approval") notify("El Ingeniero de agentes solicita autorización para ejecutar las pruebas.");
    } catch (error) { console.warn("Build polling paused.", error); scheduleBuildPoll(buildId, 1500); }
  }, delay);
  buildPollers.set(buildId, timer);
}

function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character])); }
function formatInline(value) {
  return escapeHtml(value)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}
function markdownTableCells(line) {
  const normalized = String(line ?? "").trim().replace(/^\|/, "").replace(/\|$/, "");
  return normalized.split(/(?<!\\)\|/).map((cell) => cell.replace(/\\\|/g, "|").trim());
}
function isMarkdownTableDivider(line) {
  const cells = markdownTableCells(line);
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}
function renderMarkdownTable(lines, start) {
  if (start + 1 >= lines.length || !lines[start].includes("|") || !isMarkdownTableDivider(lines[start + 1])) return null;
  const header = markdownTableCells(lines[start]);
  const rows = [];
  let cursor = start + 2;
  while (cursor < lines.length && lines[cursor].trim() && lines[cursor].includes("|")) {
    const cells = markdownTableCells(lines[cursor]);
    if (cells.length < 2) break;
    rows.push(cells);
    cursor += 1;
  }
  const width = Math.max(header.length, ...rows.map((row) => row.length));
  const pad = (row) => Array.from({ length: width }, (_, index) => row[index] || "");
  const heading = pad(header).map((cell) => `<th scope="col">${formatInline(cell)}</th>`).join("");
  const body = rows.map((row) => `<tr>${pad(row).map((cell, index) => `<${index === 0 ? "th scope=\"row\"" : "td"}>${formatInline(cell)}</${index === 0 ? "th" : "td"}>`).join("")}</tr>`).join("");
  return { html: `<div class="chat-table-wrap" role="region" aria-label="Tabla comparativa" tabindex="0"><table><thead><tr>${heading}</tr></thead><tbody>${body}</tbody></table></div>`, next: cursor };
}
function formatChatText(value) {
  const lines = String(value ?? "").split("\n"); const output = []; let paragraph = []; let list = null; let code = false; let codeLines = [];
  const closeParagraph = () => { if (paragraph.length) { output.push(`<p>${paragraph.map(formatInline).join("<br>")}</p>`); paragraph = []; } };
  const closeList = () => { if (list) { output.push(`</${list}>`); list = null; } };
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (line.trim().startsWith("```")) { closeParagraph(); closeList(); if (code) { output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`); codeLines = []; } code = !code; index += 1; continue; }
    if (code) { codeLines.push(line); index += 1; continue; }
    const table = renderMarkdownTable(lines, index);
    if (table) { closeParagraph(); closeList(); output.push(table.html); index = table.next; continue; }
    const heading = line.match(/^\s*(#{1,4})\s+(.+?)\s*#*\s*$/);
    if (heading) { closeParagraph(); closeList(); const level = Math.min(4, heading[1].length + 1); output.push(`<h${level}>${formatInline(heading[2])}</h${level}>`); index += 1; continue; }
    if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { closeParagraph(); closeList(); output.push("<hr>"); index += 1; continue; }
    const bullet = line.match(/^\s*[-*]\s+(.+)/); const numbered = line.match(/^\s*(\d+)[.)]\s+(.+)/);
    if (bullet || numbered) { closeParagraph(); const nextList = bullet ? "ul" : "ol"; if (list !== nextList) { closeList(); list = nextList; output.push(`<${list}>`); } output.push(numbered ? `<li value="${Number(numbered[1])}">${formatInline(numbered[2])}</li>` : `<li>${formatInline(bullet[1])}</li>`); index += 1; continue; }
    if (!line.trim()) { closeParagraph(); closeList(); index += 1; continue; }
    paragraph.push(line); index += 1;
  }
  if (code) output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`); closeParagraph(); closeList(); return output.join("");
}
$("#project-form").addEventListener("submit", createProject);
$("#partner-message-form").addEventListener("submit", continuePartnerChat);
$("#header-new-chat").addEventListener("click", resetPartnerChat);
$("#account-switch").addEventListener("click", switchIdentityAccount);
$("#manage-session-files").addEventListener("click", openFileManager);
$("#language-action").addEventListener("click", toggleLanguage);
$("#logout-action").addEventListener("click", logoutIdentity);
$("#sidebar-new-chat").addEventListener("click", resetPartnerChat);
$("#partner-conversation").addEventListener("click", handlePartnerConversationAction);
$("#conversation-history").addEventListener("click", handleConversationHistory);
$("#agent-catalog").addEventListener("click", handleAgentCatalog);
$("#taskmaster-studio-access").addEventListener("click", openTaskmasterStudio);
$("#connection-catalog").addEventListener("click", handleConnectionCatalog);
$("#sidebar-toggle").addEventListener("click", () => document.body.classList.add("sidebar-open"));
$("#sidebar-close").addEventListener("click", () => document.body.classList.remove("sidebar-open"));
$("#home-button").addEventListener("click", openChatHome);
$("#welcome-document-input").addEventListener("change", (event) => { closeAttachmentMenus(); uploadDocuments(event.target.files); event.target.value = ""; });
$("#chat-document-input").addEventListener("change", (event) => { closeAttachmentMenus(); uploadDocuments(event.target.files); event.target.value = ""; });
$("#welcome-image-input").addEventListener("change", (event) => { closeAttachmentMenus(); uploadDocuments(event.target.files); event.target.value = ""; });
$("#chat-image-input").addEventListener("change", (event) => { closeAttachmentMenus(); uploadDocuments(event.target.files); event.target.value = ""; });
$("#welcome-attachments").addEventListener("click", handleAttachmentClick);
$("#chat-attachments").addEventListener("click", handleAttachmentClick);
$("#file-manager-attachments").addEventListener("click", handleAttachmentClick);
$("#document-inspector").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.close(); });
$(".document-inspector-close").addEventListener("click", () => $("#document-inspector").close());
$("#file-manager").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.close(); });
$("#file-manager-close").addEventListener("click", () => $("#file-manager").close());
const builderCanvas = $("#main-content");
const builderWelcome = $("#welcome-view");
let builderPointerFrame = 0;
builderCanvas.addEventListener("pointermove", (event) => {
  if (!document.body.classList.contains("taskmaster-studio-mode")) return;
  if (builderPointerFrame) cancelAnimationFrame(builderPointerFrame);
  builderPointerFrame = requestAnimationFrame(() => {
    const bounds = builderCanvas.getBoundingClientRect();
    const x = Math.max(0, Math.min(100, ((event.clientX - bounds.left) / bounds.width) * 100));
    const y = Math.max(0, Math.min(100, ((event.clientY - bounds.top) / bounds.height) * 100));
    [builderCanvas, builderWelcome].forEach((item) => {
      item.style.setProperty("--grid-focus-x", `${x.toFixed(2)}%`);
      item.style.setProperty("--grid-focus-y", `${y.toFixed(2)}%`);
    });
    builderPointerFrame = 0;
  });
}, { passive: true });
builderCanvas.addEventListener("pointerleave", () => {
  [builderCanvas, builderWelcome].forEach((item) => {
    item.style.setProperty("--grid-focus-x", "50%");
    item.style.setProperty("--grid-focus-y", "44%");
  });
});
function bindExampleButtons() {
  $$('[data-example]').forEach((button) => button.addEventListener("click", () => {
    $("#project-description").value = button.dataset.example;
    $("#project-description").dispatchEvent(new Event("input"));
    $("#project-description").focus();
  }));
}
bindExampleButtons();
$("#project-description").addEventListener("input", (event) => { $("#char-count").textContent = `${event.target.value.length} / 6000`; });
$("#partner-message-input").addEventListener("input", (event) => { $("#partner-char-count").textContent = `${event.target.value.length} / 6000`; });
enableComposerKeyboard($("#project-description"), $("#project-form"));
enableComposerKeyboard($("#partner-message-input"), $("#partner-message-form"));
let localeFrame = 0;
new MutationObserver(() => {
  if (localeFrame) return;
  localeFrame = requestAnimationFrame(() => { localeFrame = 0; localizeInterface(); });
}).observe(document.body, { childList: true, subtree: true });
localizeInterface();
renderConversationHistory();
if (state.partnerMessages.length) { showPartnerChat(); renderPartnerConversation(); }
loadRuntimeInfo();
showOAuthReturnNotice();
