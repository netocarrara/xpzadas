const state = { bucket: "", dashboard: null, view: "homeView", selectedParty: "all" };

const els = {
  bucket: document.querySelector("#bucket"),
  adminToggle: document.querySelector("#adminToggle"),
  openRubinot: document.querySelector("#openRubinot"),
  fetchRanking: document.querySelector("#fetchRanking"),
  statusPanel: document.querySelector("#statusPanel"),
  statusTitle: document.querySelector("#statusTitle"),
  statusText: document.querySelector("#statusText"),
  highest: document.querySelector("#highest"),
  highestMeta: document.querySelector("#highestMeta"),
  bestGain: document.querySelector("#bestGain"),
  bestGainMeta: document.querySelector("#bestGainMeta"),
  worstGain: document.querySelector("#worstGain"),
  worstGainMeta: document.querySelector("#worstGainMeta"),
  basis: document.querySelector("#basis"),
  timeMeta: document.querySelector("#timeMeta"),
  partyHint: document.querySelector("#partyHint"),
  rankHint: document.querySelector("#rankHint"),
  sourceStat: document.querySelector("#sourceStat"),
  rankStat: document.querySelector("#rankStat"),
  partyStat: document.querySelector("#partyStat"),
  adminStat: document.querySelector("#adminStat"),
  partyFilter: document.querySelector("#partyFilter"),
  parties: document.querySelector("#parties"),
  players: document.querySelector("#players"),
  adminPanel: document.querySelector("#adminPanel"),
  loginForm: document.querySelector("#loginForm"),
  loginUser: document.querySelector("#loginUser"),
  loginPassword: document.querySelector("#loginPassword"),
  importForm: document.querySelector("#importForm"),
  importPayload: document.querySelector("#importPayload"),
  importRanking: document.querySelector("#importRanking"),
  openRubinotJson: document.querySelector("#openRubinotJson"),
  copyCollectScript: document.querySelector("#copyCollectScript"),
  copyBookmarklet: document.querySelector("#copyBookmarklet"),
  bookmarkletLink: document.querySelector("#bookmarkletLink"),
  partyForm: document.querySelector("#partyForm"),
  partyName: document.querySelector("#partyName"),
  partyTarget: document.querySelector("#partyTarget"),
  partyMembers: document.querySelector("#partyMembers"),
  partyNotes: document.querySelector("#partyNotes"),
  partyHighlight: document.querySelector("#partyHighlight"),
  clearPartyForm: document.querySelector("#clearPartyForm"),
  logoutAdmin: document.querySelector("#logoutAdmin")
};

let isAdmin = false;
let publicUpdate = false;
let browserVerification = false;

function showStatus(title, text, needsVerification = false) {
  els.statusPanel.hidden = false;
  els.statusTitle.textContent = title;
  els.statusText.textContent = text;
  els.openRubinot.hidden = !needsVerification || !browserVerification;
}

function hideStatus() {
  els.statusPanel.hidden = true;
  els.openRubinot.hidden = true;
}

function isCloudflareError(text) {
  return /cloudflare|just a moment|403|cf_clearance|verificacao|verification|sessao/i.test(text || "");
}

function formatXp(value) {
  if (value === null || value === undefined) return "base inicial";
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  if (number >= 1_000_000_000) return `${(number / 1_000_000_000).toFixed(2)}bi`;
  if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(2)}kk`;
  if (number >= 1_000) return `${(number / 1_000).toFixed(1)}k`;
  return String(Math.trunc(number));
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#039;"
  }[char]));
}

function bucketLabel(bucket) {
  const [category, world] = String(bucket || "").split(":");
  const label = category === "7" ? "Daily XP" : `Categoria ${category || "-"}`;
  return `${label} - ${world || "todos"}`;
}

function signedXp(value) {
  if (value === null || value === undefined) return "base inicial";
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  if (number < 0) return `-${formatXp(Math.abs(number))}`;
  return `+${formatXp(number)}`;
}

function moveText(value) {
  if (!value) return `<span class="neutral">=</span>`;
  if (value > 0) return `<span class="positive">+${value}</span>`;
  return `<span class="negative">${value}</span>`;
}

function performanceClass(status) {
  if (status === "Excelente") return "excellent";
  if (status === "Boa") return "good";
  if (status === "Ruim") return "bad";
  return "waiting";
}

function percent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0%";
  return `${Math.round(number * 100)}%`;
}

function deathText(count) {
  const number = Number(count || 0);
  if (!number) return "sem mortes";
  return number === 1 ? "1 queda" : `${number} quedas`;
}

function setView(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach((item) => item.classList.toggle("active", item.id === view));
  document.querySelectorAll(".nav-tab").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  if (view === "partyView") {
    els.adminPanel.hidden = !isAdmin;
  }
}

function setLeader(prefix, row, mode) {
  const name = els[prefix];
  const meta = els[`${prefix}Meta`];
  if (!row) {
    name.textContent = "-";
    meta.textContent = "sem dados ainda";
    return;
  }
  name.textContent = row.name;
  meta.textContent = mode === "gain" ? signedXp(row.gainSinceLast) : `rank #${row.rank} - ${formatXp(row.points)}`;
}

function renderBuckets(data) {
  const selected = state.bucket || data.bucket;
  els.bucket.innerHTML = "";
  for (const bucket of data.buckets.length ? data.buckets : [data.bucket]) {
    const option = document.createElement("option");
    option.value = bucket;
    option.textContent = bucketLabel(bucket);
    option.selected = bucket === selected;
    els.bucket.append(option);
  }
}

function renderPlayers(data) {
  const source = data.source?.includes("supabase") ? "Supabase" : "local";
  els.rankHint.textContent = `Top ${data.players.length} de ${data.totalPlayers || data.players.length} personagens - ${source}`;
  els.sourceStat.textContent = source;
  els.rankStat.textContent = `Top ${data.players.length}`;
  els.partyStat.textContent = String(data.parties?.length || 0);
  els.players.innerHTML = "";

  for (const player of data.players) {
    const tr = document.createElement("tr");
    const gainClass = player.gainSinceLast > 0 ? "positive" : player.gainSinceLast < 0 ? "negative" : "neutral";
    tr.innerHTML = `
      <td>#${player.rank}</td>
      <td><strong>${escapeHtml(player.name)}</strong></td>
      <td>${escapeHtml(player.vocation || "-")}</td>
      <td>${player.level || "-"} ${moveText(player.levelMove)}</td>
      <td>${formatXp(player.points)}</td>
      <td class="${gainClass}">${signedXp(player.gainSinceLast)}</td>
      <td>${moveText(player.rankMove)}</td>
    `;
    els.players.append(tr);
  }
}

function renderParties(data) {
  state.dashboard = data;
  els.partyHint.textContent = data.hasPrevious ? "analise desde a base das 10:30" : "aguardando a segunda leitura do ciclo";
  els.parties.innerHTML = "";
  const selectedParty = state.selectedParty || "all";
  els.partyFilter.innerHTML = "";
  [{ name: "all", label: "Todas as PTs" }, ...data.parties.map((party) => ({ name: party.name, label: party.name }))].forEach((item) => {
    const option = document.createElement("option");
    option.value = item.name;
    option.textContent = item.label;
    option.selected = item.name === selectedParty;
    els.partyFilter.append(option);
  });

  if (!data.parties.length) {
    els.parties.innerHTML = `<article class="party empty-party"><h3>Nenhuma PT cadastrada</h3><p>Entre como admin para cadastrar uma PT com 4 ou 5 integrantes.</p></article>`;
    return;
  }

  const highlighted = data.parties.find((party) => party.highlight) || null;
  const orderedParties = highlighted ? [highlighted, ...data.parties.filter((party) => party !== highlighted)] : data.parties;
  const visibleParties = selectedParty === "all" ? orderedParties : orderedParties.filter((party) => party.name === selectedParty);

  for (const party of visibleParties) {
    const score = data.hasPrevious ? party.gainSinceLast : party.totalDaily;
    const target = party.targetXp ? formatXp(party.targetXp) : "media das PTs";
    const missing = party.missingToTarget === null || party.missingToTarget === undefined ? "sem meta" : formatXp(party.missingToTarget);
    const projected = party.projectedGain === null || party.projectedGain === undefined ? "aguardando" : formatXp(party.projectedGain);
    const projectedMissing = party.projectedMissing === null || party.projectedMissing === undefined ? "sem meta" : formatXp(party.projectedMissing);
    const targetStatus = party.targetStatus === "alcanca" ? "alcanca" : party.targetStatus === "nao alcanca" ? "nao alcanca" : "sem meta";
    const scoreClass = score < 0 ? "negative" : "positive";
    const members = party.members.map((member) => `
      <tr>
        <td><strong>${escapeHtml(member.name)}</strong></td>
        <td>${escapeHtml(member.vocation || "-")}</td>
        <td>${member.level || "-"} ${moveText(member.levelMove)}</td>
        <td class="${member.gainSinceLast < 0 ? "negative" : "positive"}">${signedXp(member.gainSinceLast)}</td>
        <td>${member.levelUps || 0}</td>
        <td class="${member.xpLost ? "negative" : "neutral"}">${formatXp(member.xpLost || 0)}</td>
        <td>${deathText(member.deathCount || 0)}</td>
      </tr>
    `).join("");

    const article = document.createElement("article");
    article.className = `party ${performanceClass(party.status)}${party.highlight ? " mine" : ""}`;
    article.innerHTML = `
      <div class="party-header">
        <div>
          <span class="party-kicker">${party.highlight ? "Minha PT" : "PT monitorada"}</span>
          <h3>${escapeHtml(party.name)}</h3>
          <p>${escapeHtml(party.notes || "")}</p>
        </div>
        <div class="party-score ${scoreClass}">${signedXp(score)}</div>
      </div>
      <div class="party-metrics">
        <span class="badge ${performanceClass(party.status)}">${party.status}</span>
        <span>Saldo <strong>${signedXp(party.gainSinceLast)}</strong></span>
        <span>Ganho bruto <strong>${formatXp(party.grossGain)}</strong></span>
        <span>Perda XP <strong>${formatXp(party.xpLost)}</strong></span>
        <span>Mortes <strong>${deathText(party.deathCount)}</strong></span>
        <span>Level ups <strong>${party.levelUps || 0}</strong></span>
        <span>Media/membro <strong>${formatXp(party.averagePerMember)}</strong></span>
        <span>Meta <strong>${target}</strong></span>
        <span>Falta agora <strong>${missing}</strong></span>
        <span>Projecao 24h <strong>${projected}</strong></span>
        <span>Constancia <strong>${targetStatus}</strong></span>
        <span>Falta projetada <strong>${projectedMissing}</strong></span>
        <span>Ciclo <strong>${percent(party.cycleProgress)}</strong></span>
      </div>
      <div class="mini-table">
        <table>
          <thead><tr><th>Char</th><th>Vocacao</th><th>Level</th><th>Saldo XP</th><th>Ups</th><th>Perda</th><th>Mortes</th></tr></thead>
          <tbody>${members || `<tr><td colspan="7">Nenhum membro encontrado no top carregado.</td></tr>`}</tbody>
        </table>
      </div>
      ${party.missing.length ? `<div class="missing">Nao achei: ${party.missing.map(escapeHtml).join(", ")}</div>` : ""}
      ${isAdmin ? `<div class="party-actions"><button type="button" data-edit="${escapeHtml(party.name)}">Editar</button><button class="secondary" type="button" data-delete="${escapeHtml(party.name)}">Remover</button></div>` : ""}
    `;
    els.parties.append(article);
  }
}

function renderDashboard(data) {
  state.bucket = data.bucket;
  state.rubinotImportUrl = data.rubinotImportUrl || "";
  state.rubinotPageUrl = data.rubinotPageUrl || "https://rubinot.com.br/highscores";
  renderBuckets(data);
  setLeader("highest", data.leaders.highest, "total");
  setLeader("bestGain", data.leaders.bestGain, "gain");
  setLeader("worstGain", data.leaders.worstGain, "gain");
  els.basis.textContent = data.hasPrevious ? "ciclo 10:30" : "base do ciclo";
  els.timeMeta.textContent = data.update?.message || data.updatedAt || data.checkedAt || "aguardando snapshot";
  if (data.supabaseConfigured && data.supabaseError) {
    showStatus("Supabase com erro", data.supabaseError);
  } else if (!data.supabaseConfigured) {
    showStatus("Supabase desligado", "Configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY na hospedagem para salvar dados sem reset.");
  } else if (data.update?.supabase?.error) {
    showStatus("Nao salvei no Supabase", data.update.supabase.error);
  }
  renderPlayers(data);
  renderParties(data);
}

async function loadDashboard() {
  const url = state.bucket ? `/api/dashboard?bucket=${encodeURIComponent(state.bucket)}` : "/api/dashboard";
  const response = await fetch(url, { cache: "no-store" });
  const data = await response.json();
  renderDashboard(data);
}

function setAdminUi(authenticated) {
  isAdmin = authenticated;
  els.adminToggle.textContent = authenticated ? "Painel admin" : "Admin";
  els.adminStat.textContent = authenticated ? "ativo" : "bloqueado";
  els.loginForm.hidden = authenticated;
  els.importForm.hidden = !authenticated;
  els.partyForm.hidden = !authenticated;
  els.fetchRanking.disabled = !authenticated && !publicUpdate;
  els.fetchRanking.title = els.fetchRanking.disabled ? "Entre como admin para atualizar o ranking." : "";
  if (state.view !== "partyView") els.adminPanel.hidden = true;
}

async function loadAuth() {
  const response = await fetch("/api/auth", { cache: "no-store" });
  const data = await response.json();
  publicUpdate = Boolean(data.publicUpdate);
  browserVerification = Boolean(data.browserVerification);
  setAdminUi(Boolean(data.authenticated));
}

function partyPayloadFromForm() {
  return {
    name: els.partyName.value.trim(),
    target_xp: els.partyTarget.value.trim(),
    members: els.partyMembers.value.split(/\n|,|;/).map((item) => item.trim()).filter(Boolean),
    notes: els.partyNotes.value.trim(),
    highlight: els.partyHighlight.checked
  };
}

function clearPartyForm() {
  els.partyName.value = "";
  els.partyTarget.value = "";
  els.partyMembers.value = "";
  els.partyNotes.value = "";
  els.partyHighlight.checked = false;
}

async function saveParty(event) {
  event.preventDefault();
  const response = await fetch("/api/parties", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(partyPayloadFromForm())
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    showStatus("Nao salvei a PT", data.error || "Verifique os campos.");
    return;
  }
  clearPartyForm();
  if (data.supabase?.error) {
    showStatus("PT salva so localmente", data.supabase.error);
  } else {
    showStatus("PT salva", data.supabase?.saved ? "A configuracao foi salva no Supabase." : "A configuracao foi atualizada no site.");
  }
  await loadDashboard();
}

async function loginAdmin(event) {
  event.preventDefault();
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user: els.loginUser.value.trim(), password: els.loginPassword.value })
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    showStatus("Login recusado", data.error || "Usuario ou senha invalidos.");
    return;
  }
  els.loginPassword.value = "";
  setAdminUi(true);
  els.adminPanel.hidden = false;
  showStatus("Admin ativo", "Voce pode cadastrar e editar PTs.");
  await loadDashboard();
}

async function logoutAdmin() {
  await fetch("/api/logout", { method: "POST" });
  setAdminUi(false);
  clearPartyForm();
  await loadDashboard();
}

async function deleteParty(name) {
  const response = await fetch("/api/delete-party", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name })
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    showStatus("Nao removi a PT", data.error || "Tente novamente.");
    return;
  }
  if (data.supabase?.error) {
    showStatus("PT removida so localmente", data.supabase.error);
  } else {
    showStatus("PT removida", `${name} saiu do comparativo.`);
  }
  await loadDashboard();
}

function editParty(name) {
  const party = state.dashboard?.parties?.find((item) => item.name === name);
  if (!party) return;
  els.adminPanel.hidden = false;
  els.partyName.value = name;
  els.partyTarget.value = party.targetXp ? formatXp(party.targetXp) : "";
  els.partyMembers.value = [...party.members.map((item) => item.name), ...party.missing].join("\n");
  els.partyNotes.value = party.notes || "";
  els.partyHighlight.checked = Boolean(party.highlight);
  els.partyName.focus();
  showStatus("Editando PT", "Ajuste os campos e salve novamente.");
}

async function updateRanking() {
  if (!isAdmin && !publicUpdate) {
    setView("partyView");
    els.adminPanel.hidden = false;
    showStatus("Login necessario", "Entre como admin para atualizar o ranking online.");
    return;
  }
  els.fetchRanking.disabled = true;
  els.fetchRanking.textContent = "Atualizando...";
  hideStatus();
  els.rankHint.textContent = "Consultando RubinOT e salvando leitura...";
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);
  try {
    const response = await fetch("/api/update-ranking", { method: "POST", cache: "no-store", signal: controller.signal });
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || "Falha ao consultar RubinOT.");
    renderDashboard(data);
    showStatus(data.update?.duplicate ? "Sem leitura nova ainda" : "Ranking atualizado", data.update?.message || "Leitura salva.");
  } catch (error) {
    const message = error.name === "AbortError" ? "A consulta demorou demais no Render." : error.message || String(error);
    await loadDashboard().catch(() => {});
    const cloudflareBlocked = isCloudflareError(message);
    showStatus(
      cloudflareBlocked || error.name === "AbortError" ? "Use a importacao manual" : "Nao consegui atualizar",
      cloudflareBlocked || error.name === "AbortError"
        ? (
            browserVerification
              ? "A consulta invisivel foi bloqueada. Clique em Resolver Cloudflare uma vez e depois atualize de novo."
              : "Clique em Abrir JSON do RubinOT, copie tudo do popup e cole em Importar leitura."
          )
        : message,
      cloudflareBlocked
    );
  } finally {
    clearTimeout(timeout);
    els.fetchRanking.disabled = false;
    els.fetchRanking.textContent = "Atualizar ranking";
  }
}

async function openRubinot() {
  els.openRubinot.disabled = true;
  els.openRubinot.textContent = "Abrindo...";
  try {
    const response = await fetch("/api/open-rubinot", { method: "POST", cache: "no-store" });
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || "Falha ao abrir RubinOT.");
    showStatus("Verificacao aberta", data.message || "Resolva o Cloudflare e clique em Atualizar ranking.");
  } catch (error) {
    showStatus("Nao abriu a verificacao", error.message || String(error), true);
  } finally {
    els.openRubinot.disabled = false;
    els.openRubinot.textContent = "Resolver Cloudflare";
  }
}

async function importRanking(event) {
  event.preventDefault();
  const payload = els.importPayload.value.trim();
  if (!payload) {
    showStatus("JSON vazio", "Cole a resposta da requisicao highscores do RubinOT.");
    return;
  }

  els.importRanking.disabled = true;
  els.importRanking.textContent = "Importando...";
  try {
    JSON.parse(payload);
    const response = await fetch("/api/import-ranking", {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload })
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || "Falha ao importar leitura.");
    els.importPayload.value = "";
    renderDashboard(data);
    showStatus(data.update?.duplicate ? "Sem leitura nova ainda" : "Leitura importada", data.update?.message || "Ranking importado.");
  } catch (error) {
    showStatus("Nao consegui importar", error.message || String(error), false);
  } finally {
    els.importRanking.disabled = false;
    els.importRanking.textContent = "Importar leitura";
  }
}

function openRubinotJson() {
  const url = state.rubinotPageUrl || "https://rubinot.com.br/highscores";
  const popup = window.open(url, "rubinotHighscores", "popup=yes,width=980,height=720");
  if (!popup) {
    showStatus("Popup bloqueado", "Permita popups para este site e tente abrir o RubinOT de novo.");
    return;
  }
  popup.focus();
  showStatus("RubinOT aberto", "Clique em Copiar script, cole no Console do RubinOT e depois cole o JSON aqui.");
}

async function copyCollectScript() {
  const apiPath = new URL(state.rubinotImportUrl || "https://rubinot.com.br/api/highscores?world=27&category=experience&vocation=0").pathname
    + new URL(state.rubinotImportUrl || "https://rubinot.com.br/api/highscores?world=27&category=experience&vocation=0").search;
  const script = `(async()=>{const r=await fetch(${JSON.stringify(apiPath)},{credentials:"include"});const t=await r.text();if(!r.ok)throw new Error("HTTP "+r.status+": "+t);try{await navigator.clipboard.writeText(t);console.log("JSON do RubinOT copiado. Volte ao Rankzada e cole em Importar leitura.");}catch(e){console.log(t)}})();`;
  try {
    await navigator.clipboard.writeText(script);
    showStatus("Script copiado", "No RubinOT, aperte F12, abra Console, cole o script e aperte Enter. Ele copia o JSON para sua area de transferencia.");
  } catch (error) {
    els.importPayload.value = script;
    showStatus("Copie o script", "Nao consegui acessar a area de transferencia. Copiei o script para o campo abaixo; copie ele manualmente e rode no Console do RubinOT.");
  }
}

async function loadBookmarklet() {
  const response = await fetch("/api/import-bookmarklet", { cache: "no-store" });
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || "Falha ao gerar atalho.");
  return data.bookmarklet;
}

async function copyBookmarklet() {
  els.copyBookmarklet.disabled = true;
  els.copyBookmarklet.textContent = "Gerando...";
  try {
    const bookmarklet = await loadBookmarklet();
    els.bookmarkletLink.href = bookmarklet;
    els.bookmarkletLink.hidden = false;
    await navigator.clipboard.writeText(bookmarklet);
    showStatus("Atalho copiado", "Crie um favorito e cole isso no campo URL. Depois, no RubinOT, clique no favorito para enviar direto ao Rankzada.");
  } catch (error) {
    showStatus("Nao consegui gerar atalho", error.message || String(error));
  } finally {
    els.copyBookmarklet.disabled = false;
    els.copyBookmarklet.textContent = "Copiar atalho";
  }
}

document.querySelectorAll(".nav-tab").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});
els.openRubinot.addEventListener("click", openRubinot);
els.fetchRanking.addEventListener("click", updateRanking);
els.openRubinotJson.addEventListener("click", openRubinotJson);
els.copyCollectScript.addEventListener("click", copyCollectScript);
els.copyBookmarklet.addEventListener("click", copyBookmarklet);
els.adminToggle.addEventListener("click", () => {
  setView("partyView");
  els.adminPanel.hidden = !els.adminPanel.hidden;
});
els.loginForm.addEventListener("submit", loginAdmin);
els.importForm.addEventListener("submit", importRanking);
els.partyForm.addEventListener("submit", saveParty);
els.clearPartyForm.addEventListener("click", clearPartyForm);
els.logoutAdmin.addEventListener("click", logoutAdmin);
els.parties.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.dataset.delete) deleteParty(target.dataset.delete);
  if (target.dataset.edit) editParty(target.dataset.edit);
});
els.bucket.addEventListener("change", () => {
  state.bucket = els.bucket.value;
  loadDashboard();
});
els.partyFilter.addEventListener("change", () => {
  state.selectedParty = els.partyFilter.value;
  if (state.dashboard) renderParties(state.dashboard);
});

loadAuth().finally(() => loadDashboard()).catch((error) => {
  els.rankHint.textContent = error.message || String(error);
});
