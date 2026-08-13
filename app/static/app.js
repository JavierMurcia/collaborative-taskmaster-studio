const stateUrl = "/api/state";
const labels = { ready: "LISTO PARA INVESTIGAR", running: "EJECUCIÓN CONTROLADA", waiting_for_approval: "ESPERANDO APROBACIÓN", recovered: "RECUPERADO", failed: "REQUIERE ATENCIÓN" };
const eventLabels = { action_result: "Acción evaluada", memory_validated: "Memoria validada", mission_recovered: "Verificación independiente: éxito", mission_failed: "Misión detenida", approval_resumed_mission: "Decisión humana incorporada" };

async function request(path, body = {}) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) alert(data.error || "No se pudo completar la acción.");
  render(data);
}
async function load() { render(await (await fetch(stateUrl)).json()); }

function render(data) {
  const { mission, metrics, memory, approval } = data;
  document.querySelector("#mission-status").textContent = labels[mission.status] || mission.status;
  document.querySelector(".status").className = `status ${mission.status}`;
  document.querySelector("#budget").textContent = `${metrics.budget_remaining} acciones disponibles`;
  const items = [["COLA", metrics.queue_depth.toLocaleString(), "pedidos pendientes"], ["LATENCIA", `${metrics.latency_ms} ms`, "p95 actual"], ["ERRORES", `${metrics.error_rate_percent}%`, "tasa de error"], ["CAPACIDAD", metrics.global_capacity, "workers globales"]];
  const target = document.querySelector("#metrics"); target.innerHTML = "";
  items.forEach(([label, value, note]) => { const node = document.querySelector("#metric-template").content.cloneNode(true); node.querySelector("span").textContent = label; node.querySelector("strong").textContent = value; node.querySelector("small").textContent = note; target.append(node); });
  document.querySelector("#plan").innerHTML = mission.plan.map((step, i) => `<div class="step"><b>${String(i + 1).padStart(2, "0")}</b><div><strong>${pretty(step.action)}</strong><p>${step.purpose}</p></div></div>`).join("");
  document.querySelector("#trajectory").innerHTML = mission.trajectory.slice().reverse().map(event => `<li><span>${String(event.sequence).padStart(2, "0")}</span><div><strong>${eventLabels[event.event] || pretty(event.event)}</strong><p>${describe(event)}</p></div></li>`).join("") || `<li class="empty">La trayectoria aparecerá cuando Sentinel comience a investigar.</li>`;
  document.querySelector("#stored-count").textContent = memory.stored.length;
  document.querySelector("#quarantined-count").textContent = memory.quarantined.length;
  document.querySelector("#quarantine").innerHTML = memory.quarantined.map(item => `<p><strong>Entrada aislada</strong>${item.evidence.message}</p>`).join("") || `<p class="muted">No hay entradas en cuarentena.</p>`;
  const approvalContent = document.querySelector("#approval-content");
  if (approval && approval.status === "pending") approvalContent.innerHTML = `<div class="approval-card"><span>RIESGO ALTO · ${approval.action}</span><p>${approval.impact}</p><small>${approval.rationale}</small><div><button class="approve" data-action="approve">Aprobar</button><button class="reject" data-action="reject">Rechazar</button></div></div>`;
  else if (approval) approvalContent.innerHTML = `<div class="approval-empty"><strong>Decisión registrada: ${approval.status === "approved" ? "aprobada" : "rechazada"}</strong><p>${approval.decision_note || "La decisión está en la trayectoria."}</p></div>`;
  else approvalContent.textContent = "No hay acciones de alto riesgo pendientes.";
}
function pretty(value) { return value.replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase()); }
function describe(event) { if (event.result) return event.result.message; if (event.event === "planner_selected") return event.mode === "vertex_gemini" ? `Plan generado por ${event.model} en Vertex AI.` : "Plan conservador determinista (modo local)."; if (event.event === "planner_fallback") return `Vertex no estuvo disponible; se aplicó fallback seguro: ${event.reason}`; if (event.reason) return event.reason; if (event.approval) return `${event.approval.action} · ${event.approval.status}`; if (event.metrics) return "Objetivos de cola, latencia y errores alcanzados."; return "Evento registrado en la trayectoria auditable."; }
document.addEventListener("click", event => { const action = event.target.dataset.action; if (!action) return; const map = { reset: ["/api/reset"], investigate: ["/api/investigate"], recover: ["/api/recover"], scale: ["/api/request-scaling", { workers: 1 }], approve: ["/api/approval", { approved: true }], reject: ["/api/approval", { approved: false }] }; request(...map[action]); });
load();
