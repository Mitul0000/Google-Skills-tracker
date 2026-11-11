let currentFile = null;
let eventSource = null;
let resultsData = null;

// Initialize elements
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const startScanBtn = document.getElementById("start-scan-btn");
const uploadError = document.getElementById("upload-error");
const forceRescanCheckbox = document.getElementById("force-rescan-checkbox");
const progressCard = document.getElementById("progress-card");
const progressFill = document.getElementById("progress-fill");
const progressSubtext = document.getElementById("progress-subtext");
const speedLabel = document.getElementById("speed-label");
const etaLabel = document.getElementById("eta-label");
const cancelScanBtn = document.getElementById("cancel-scan-btn");
const liveFeed = document.getElementById("live-feed");
const dashboardCard = document.getElementById("dashboard-card");
const clearDataBtn = document.getElementById("clear-data-btn");
const downloadSampleLink = document.getElementById("download-sample-link");
const globalSearchInput = document.getElementById("global-search");
const filterChangedOnly = document.getElementById("filter-changed-only");
const hideEmptyCheckbox = document.getElementById("hide-empty");

// Counters
const cntComplete = document.getElementById("cnt-complete");
const cntPartial = document.getElementById("cnt-partial");
const cntNone = document.getElementById("cnt-none");
const cntPrivate = document.getElementById("cnt-private");
const cntCache = document.getElementById("cnt-cache");
const cntErrors = document.getElementById("cnt-errors");

// Load initial campaign config
async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    if (res.ok) {
      const data = await res.json();
      document.getElementById("campaign-title").textContent = `Required Badges (${data.campaign_name})`;
      const footerList = document.getElementById("badges-footer-list");
      footerList.innerHTML = "";
      data.badges.forEach((b) => {
        const span = document.createElement("span");
        span.className = "badge-tag";
        span.textContent = b;
        footerList.appendChild(span);
      });
    }
  } catch (err) {
    console.error("Failed to load config:", err);
  }
}

// Check existing session status on load
async function checkInitialStatus() {
  try {
    const res = await fetch("/api/status");
    if (res.ok) {
      const data = await res.json();
      if (data.scan_state === "running") {
        showProgressView();
        connectSSE();
      } else {
        // Check if results exist
        loadResults();
      }
    }
  } catch (err) {
    console.error("Failed to check status:", err);
  }
}

// File Dropzone handlers
dropzone.addEventListener("click", () => fileInput.click());

dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("dragover");
});

dropzone.addEventListener("dragleave", () => {
  dropzone.classList.remove("dragover");
});

dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
    handleFile(e.dataTransfer.files[0]);
  }
});

fileInput.addEventListener("change", (e) => {
  if (e.target.files && e.target.files.length > 0) {
    handleFile(e.target.files[0]);
  }
});

function handleFile(file) {
  uploadError.style.display = "none";
  if (!file.name.toLowerCase().endsWith(".csv")) {
    showError("Please select a valid CSV file (.csv)");
    return;
  }
  if (file.size > 2 * 1024 * 1024) {
    showError("File size exceeds 2 MB limit");
    return;
  }

  currentFile = file;
  dropzone.innerHTML = `
    <div class="upload-icon" style="color: var(--success);">✓</div>
    <p style="font-weight: 600;">${file.name}</p>
    <p style="font-size: 13px; color: var(--text-muted);">${(file.size / 1024).toFixed(1)} KB — Click or drop another file to replace</p>
  `;
  startScanBtn.disabled = false;
}

function showError(msg) {
  uploadError.textContent = msg;
  uploadError.style.display = "block";
}

// Start Scan
startScanBtn.addEventListener("click", async () => {
  if (!currentFile) return;

  startScanBtn.disabled = true;
  uploadError.style.display = "none";

  const formData = new FormData();
  formData.append("file", currentFile);

  const force = forceRescanCheckbox.checked ? "true" : "false";

  try {
    const res = await fetch(`/api/upload?force_rescan=${force}`, {
      method: "POST",
      body: formData,
    });

    const data = await res.json();
    if (!res.ok) {
      showError(data.detail?.message || "Failed to start verification.");
      startScanBtn.disabled = false;
      return;
    }

    showProgressView();
    connectSSE();
  } catch (err) {
    showError("Network error while uploading file.");
    startScanBtn.disabled = false;
  }
});

// SSE Connection
function connectSSE() {
  if (eventSource) {
    eventSource.close();
  }

  eventSource = new EventSource("/api/progress");

  eventSource.addEventListener("progress", (e) => {
    const data = JSON.parse(e.data);
    updateProgressUI(data);
  });

  eventSource.addEventListener("row", (e) => {
    const row = JSON.parse(e.data);
    appendLiveFeed(row);
  });

  eventSource.addEventListener("complete", (e) => {
    const data = JSON.parse(e.data);
    updateProgressUI(data);
    eventSource.close();
    eventSource = null;
    setTimeout(() => {
      loadResults();
    }, 600);
  });

  eventSource.onerror = (err) => {
    console.warn("SSE connection interrupted, checking status...", err);
    // Poll status fallback
    setTimeout(async () => {
      const res = await fetch("/api/status");
      if (res.ok) {
        const s = await res.json();
        if (s.scan_state === "running") {
          connectSSE();
        } else {
          loadResults();
        }
      }
    }, 2000);
  };
}

function showProgressView() {
  progressCard.style.display = "block";
  dashboardCard.style.display = "none";
  liveFeed.innerHTML = "";
  progressFill.style.width = "0%";
}

function updateProgressUI(data) {
  const percent = data.percent || 0;
  progressFill.style.width = `${percent}%`;
  progressSubtext.textContent = `${data.done} of ${data.total} profiles checked (${percent}%)`;
  speedLabel.textContent = `${data.current_rate || 0} req/s`;
  etaLabel.textContent = data.eta_seconds ? `ETA: ${Math.round(data.eta_seconds)}s` : "ETA: --";

  const c = data.counts || {};
  cntComplete.textContent = c.complete || 0;
  cntPartial.textContent = c.partial || 0;
  cntNone.textContent = c.none || 0;
  cntPrivate.textContent = c.private || 0;
  cntCache.textContent = c.from_cache || 0;
  cntErrors.textContent = c.errors || 0;
}

function appendLiveFeed(row) {
  const div = document.createElement("div");
  div.style.marginBottom = "4px";

  const shortUuid = row.uuid ? row.uuid.substring(0, 8) : "row";
  let statusBadge = "";
  if (row.status === "complete") {
    statusBadge = `<span style="color: var(--success); font-weight:600;">10/10 Complete</span>`;
  } else if (row.status === "partial") {
    statusBadge = `<span style="color: var(--warning); font-weight:600;">${row.score}/10 Partial</span>`;
  } else if (row.status === "none") {
    statusBadge = `<span style="color: var(--error);">0/10 None</span>`;
  } else if (row.status === "private") {
    statusBadge = `<span style="color: var(--private-color);">Private</span>`;
  } else {
    statusBadge = `<span style="color: var(--error);">Error: ${row.error_reason || row.status}</span>`;
  }

  const cacheNote = row.from_cache ? ` <span style="color: var(--primary); font-size:11px;">(cached)</span>` : "";
  const nameNote = row.profile_name ? ` [${row.profile_name}]` : "";

  div.innerHTML = `• ${shortUuid}... ${nameNote} → ${statusBadge}${cacheNote}`;
  liveFeed.appendChild(div);
  liveFeed.scrollTop = liveFeed.scrollHeight;
}

// Cancel Scan
cancelScanBtn.addEventListener("click", async () => {
  if (confirm("Cancel the current scan? Finished results will be preserved.")) {
    try {
      await fetch("/api/scan/cancel", { method: "POST" });
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      loadResults();
    } catch (err) {
      console.error(err);
    }
  }
});

// Load and Render Results Dashboard
async function loadResults() {
  try {
    const res = await fetch("/api/results");
    if (!res.ok) return;
    const data = await res.json();
    if (!data.all || data.all.length === 0) {
      dashboardCard.style.display = "none";
      return;
    }

    resultsData = data;
    renderDashboard(data);
  } catch (err) {
    console.error("Failed to load results:", err);
  }
}

function renderDashboard(data) {
  dashboardCard.style.display = "block";
  progressCard.style.display = "none";
  startScanBtn.disabled = false;

  const s = data.summary;
  document.getElementById("sum-total").textContent = s.total_rows;
  document.getElementById("sum-unique").textContent = s.unique_profiles;
  document.getElementById("sum-rate").textContent = `${s.completion_rate}%`;
  document.getElementById("sum-avg").textContent = s.average_score.toFixed(1);
  document.getElementById("sum-private").textContent = s.private_count;
  document.getElementById("sum-failed").textContent = s.errors_count;

  // Build Pie Slices
  const slices = [];
  for (let score = 10; score >= 0; score--) {
    const count = (data.by_score[score] || []).length;
    if (count > 0) {
      slices.push({
        label: `${score}/10 Badges`,
        value: count,
        color: Charts.colors[score],
        key: `score-${score}`,
      });
    }
  }

  if (data.private.length > 0) {
    slices.push({
      label: "Private Profiles",
      value: data.private.length,
      color: Charts.colors.private,
      key: "section-private",
    });
  }

  if (data.errors.length > 0) {
    slices.push({
      label: "Errors / Unparseable",
      value: data.errors.length,
      color: Charts.colors.errors,
      key: "section-errors",
    });
  }

  // Render SVG Pie Chart
  const chartContainer = document.getElementById("chart-container");
  chartContainer.innerHTML = "";
  const pieSvg = Charts.createPieChart(slices, {
    size: 260,
    innerRadius: 70, // modern donut look
    onClick: (key) => {
      const el = document.getElementById(key);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        const body = el.querySelector(".bucket-body");
        if (body && !body.classList.contains("open")) {
          toggleBucket(el);
        }
      }
    },
  });
  chartContainer.appendChild(pieSvg);

  // Render Legend
  const legendContainer = document.getElementById("chart-legend");
  legendContainer.innerHTML = "";
  const totalProfiles = slices.reduce((acc, sl) => acc + sl.value, 0);

  slices.forEach((sl) => {
    const item = document.createElement("div");
    item.className = "legend-item";
    const percent = ((sl.value / totalProfiles) * 100).toFixed(1);
    item.innerHTML = `
      <span class="legend-color" style="background: ${sl.color};"></span>
      <span style="font-weight: 500;">${sl.label}:</span>
      <span>${sl.value} (${percent}%)</span>
    `;
    item.addEventListener("click", () => {
      const el = document.getElementById(sl.key);
      if (el) {
        el.scrollIntoView({ behavior: "smooth" });
        const body = el.querySelector(".bucket-body");
        if (body && !body.classList.contains("open")) {
          toggleBucket(el);
        }
      }
    });
    legendContainer.appendChild(item);
  });

  // Render Buckets
  renderBuckets();
}

function renderBuckets() {
  const container = document.getElementById("buckets-container");
  container.innerHTML = "";

  if (!resultsData) return;

  const searchTerm = (globalSearchInput.value || "").toLowerCase().trim();
  const changedOnly = filterChangedOnly.checked;
  const hideEmpty = hideEmptyCheckbox ? hideEmptyCheckbox.checked : true;

  const totalProfiles = resultsData.summary.unique_profiles || 1;

  // 1. Render Score Buckets 10 down to 0
  for (let score = 10; score >= 0; score--) {
    const items = filterItems(resultsData.by_score[score] || [], searchTerm, changedOnly);
    if (hideEmpty && items.length === 0) {
      continue;
    }
    const bucketEl = createBucketSection(
      `score-${score}`,
      `${score} of 10 Badges Completed`,
      items,
      Charts.colors[score],
      totalProfiles
    );
    container.appendChild(bucketEl);
  }

  // 2. Private Section
  const privateItems = filterItems(resultsData.private || [], searchTerm, changedOnly);
  if (!hideEmpty || privateItems.length > 0) {
    const privEl = createBucketSection(
      "section-private",
      "Private / Inaccessible Profiles",
      privateItems,
      Charts.colors.private,
      totalProfiles
    );
    container.appendChild(privEl);
  }

  // 3. Errors Section
  const errorItems = filterItems(resultsData.errors || [], searchTerm, changedOnly);
  if (!hideEmpty || errorItems.length > 0) {
    const errEl = createBucketSection(
      "section-errors",
      "Errors (Could Not Check)",
      errorItems,
      Charts.colors.errors,
      totalProfiles,
      true // isError
    );
    container.appendChild(errEl);
  }
}

function filterItems(items, term, changedOnly) {
  return items.filter((p) => {
    if (changedOnly && (p.prev_score === null || p.prev_score === undefined || p.prev_score === p.score)) {
      return false;
    }
    if (!term) return true;
    const searchString = `${p.profile_name || ""} ${p.uuid} ${p.url} ${(p.verified_badges || []).map(b => b.title).join(" ")} ${(p.missing_badges || []).join(" ")}`.toLowerCase();
    return searchString.includes(term);
  });
}

function createBucketSection(id, title, items, color, totalCount, isError = false) {
  const sec = document.createElement("div");
  sec.className = "bucket-section";
  sec.id = id;

  const count = items.length;
  const pct = ((count / totalCount) * 100).toFixed(1);

  const header = document.createElement("div");
  header.className = "bucket-header";

  // Create mini donut SVG for this bucket
  const miniDonut = Charts.createPieChart([
    { label: title, value: count, color: color },
    { label: "Other", value: Math.max(0, totalCount - count), color: "#e0e0e0" }
  ], { size: 28, innerRadius: 8 });

  header.innerHTML = `
    <div class="bucket-title">
      <div id="mini-donut-${id}"></div>
      <span>${title}</span>
      <span class="bucket-badge" style="background: ${color}20; color: ${color};">${count} (${pct}%)</span>
    </div>
    <div style="font-size: 13px; color: var(--text-muted);">
      ${count === 0 ? "No profiles" : "▼ Toggle"}
    </div>
  `;

  // Attach mini donut
  setTimeout(() => {
    const el = document.getElementById(`mini-donut-${id}`);
    if (el) el.appendChild(miniDonut);
  }, 0);

  const body = document.createElement("div");
  body.className = "bucket-body";

  if (count > 0) {
    // Actions inside bucket: Search & Copy links
    const actionsBar = document.createElement("div");
    actionsBar.style.display = "flex";
    actionsBar.style.justifyContent = "space-between";
    actionsBar.style.alignItems = "center";
    actionsBar.style.marginBottom = "12px";

    actionsBar.innerHTML = `
      <span style="font-size: 13px; color: var(--text-muted);">${count} participant profiles</span>
      <div style="display: flex; gap: 8px;">
        <button class="btn btn-secondary copy-links-btn" style="padding: 4px 10px; font-size: 12px;">Copy Links</button>
      </div>
    `;

    actionsBar.querySelector(".copy-links-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      const urls = items.map((i) => i.url).join("\n");
      navigator.clipboard.writeText(urls);
      alert(`Copied ${items.length} profile URLs to clipboard!`);
    });

    body.appendChild(actionsBar);

    // Profile table
    const table = document.createElement("table");
    table.className = "profile-table";
    table.innerHTML = `
      <thead>
        <tr>
          <th>Participant</th>
          <th>Profile URL</th>
          <th>Status / Score</th>
          <th>Missing Required Badges</th>
        </tr>
      </thead>
      <tbody>
        ${items.map((p) => {
          let scoreDiffHtml = "";
          if (p.prev_score !== null && p.prev_score !== undefined) {
            if (p.score > p.prev_score) {
              scoreDiffHtml = `<div class="score-diff up">↑ Up from ${p.prev_score}/10</div>`;
            } else if (p.score === p.prev_score) {
              scoreDiffHtml = `<div class="score-diff same">No change</div>`;
            } else {
              scoreDiffHtml = `<div class="score-diff">Down from ${p.prev_score}/10</div>`;
            }
          }

          const missingTags = (p.missing_badges || []).map(b => `<span class="badge-tag missing">${b}</span>`).join(" ");

          return `
            <tr>
              <td>
                <strong>${p.profile_name || "Anonymous / Not parsed"}</strong>
                ${p.error_reason ? `<div style="font-size:12px; color:var(--error);">${p.error_reason}</div>` : ""}
              </td>
              <td>
                <a href="${p.url}" target="_blank" rel="noopener noreferrer" style="color: var(--primary); text-decoration: none; font-size: 13px;">
                  ${p.uuid} ↗
                </a>
              </td>
              <td>
                <strong>${p.score}/10</strong>
                ${scoreDiffHtml}
              </td>
              <td style="max-width: 450px;">
                ${p.score === 10 ? '<span style="color: var(--success); font-weight:600;">All 10 verified!</span>' : (missingTags || "None")}
              </td>
            </tr>
          `;
        }).join("")}
      </tbody>
    `;
    body.appendChild(table);
  } else {
    body.innerHTML = `<div style="color: var(--text-muted); font-size: 13px; font-style: italic;">No profiles found in this category.</div>`;
  }

  // Open by default if it has profiles and score == 10
  if (count > 0 && id === "score-10") {
    header.classList.add("open");
    body.classList.add("open");
  }

  header.addEventListener("click", () => toggleBucket(sec));

  sec.appendChild(header);
  sec.appendChild(body);
  return sec;
}

function toggleBucket(bucketEl) {
  const header = bucketEl.querySelector(".bucket-header");
  const body = bucketEl.querySelector(".bucket-body");
  const isOpen = body.classList.contains("open");
  if (isOpen) {
    body.classList.remove("open");
    header.classList.remove("open");
  } else {
    body.classList.add("open");
    header.classList.add("open");
  }
}

// Global search and filter listener
globalSearchInput.addEventListener("input", () => renderBuckets());
filterChangedOnly.addEventListener("change", () => renderBuckets());
if (hideEmptyCheckbox) {
  hideEmptyCheckbox.addEventListener("change", () => renderBuckets());
}

// Re-scan Button
document.getElementById("btn-rescan").addEventListener("click", () => {
  window.scrollTo({ top: 0, behavior: "smooth" });
  startScanBtn.disabled = false;
  dropzone.scrollIntoView({ behavior: "smooth" });
});

// Clear Data Button
clearDataBtn.addEventListener("click", async () => {
  if (confirm("Are you sure you want to delete all your session data and profiles? This cannot be undone.")) {
    try {
      await fetch("/api/session", { method: "DELETE" });
      location.reload();
    } catch (err) {
      alert("Failed to clear data.");
    }
  }
});

// Download Sample CSV
if (downloadSampleLink) {
  downloadSampleLink.addEventListener("click", (e) => {
    e.preventDefault();
    const sampleContent = `Google Skills Profile URL,[INFO]
https://www.skills.google/public_profiles/be556f7b-ab4b-4be7-82fb-9be45202ddd7,All skill badges completed
https://www.skills.google/public_profiles/8f0d00c3-7674-4042-ba15-d26a3fae84f9,Some skill badges completed
https://www.skills.google/public_profiles/2fafe410-5b6c-463b-9a1d-61dbfb43e9ac,None of the skill badges completed
https://www.skills.google/public_profiles/172773ae-1097-499b-a064-0be32bd06b81,Account which is now private
https://www.skills.google/public_profiles/9f130a25-e289-41ea-a90a-febbf37b8dc1,Randomly Chosen Profile
https://www.skills.google/public_profiles/f0633f18-fd81-4cc0-b375-1454da352885,Randomly Chosen Profile
`;
    const blob = new Blob([sampleContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "sample_input.csv";
    a.click();
    URL.revokeObjectURL(url);
  });
}

// Start app
loadConfig();
checkInitialStatus();
