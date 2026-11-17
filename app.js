/**
 * Production Test Jig Monitor
 * Real-time station metrics & automated quality verification
 */

(() => {
  'use strict';

  // ==============================================================================
  // CONFIGURATION (Zero external branding in UI)
  // ==============================================================================
  const APP_CONFIG = {
    // Backend REST API Data Endpoint
    API_URL: "https://biqnkyuwdgyuzlgfmtmp.supabase.co/rest/v1/jig_test_results?select=*&order=id.desc",
    API_KEY: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJpcW5reXV3ZGd5dXpsZ2ZtdG1wIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTM4NzcyNjEsImV4cCI6MjEwOTQ1MzI2MX0.6aY9E4raqTKfLlmPQyVxijwcfh_yWH-4942SkKcEW-A",
    
    // Auto-refresh cadence (60 seconds)
    REFRESH_INTERVAL_SECONDS: 60,

    // Hardcoded Notification Recipients
    EMAIL_RECIPIENTS: ["ravil.indiesemic@gmail.com", "dharmveer@indiesemic.com"],

    // GitHub Actions Workflow Details
    GITHUB_OWNER: "P-Dharm",
    GITHUB_REPO: "Neo_test_jig-Neo_pi_backup",
    WORKFLOW_FILE: "email_report.yml",
    WORKFLOW_REF: "testing_ravil"
  };

  // State Management
  let allRecords = [];
  let countdownRemaining = APP_CONFIG.REFRESH_INTERVAL_SECONDS;
  let countdownTimer = null;
  let isFetching = false;

  // DOM Elements
  const elOverallTotal = document.getElementById('overallTotal');
  const elOverallSuccess = document.getElementById('overallSuccess');
  const elOverallFailure = document.getElementById('overallFailure');
  const elOverallYield = document.getElementById('overallYield');

  const elJig1Total = document.getElementById('jig1Total');
  const elJig1Success = document.getElementById('jig1Success');
  const elJig1Failure = document.getElementById('jig1Failure');
  const elJig1Yield = document.getElementById('jig1Yield');
  const elJig1Bar = document.getElementById('jig1Bar');

  const elJig2Total = document.getElementById('jig2Total');
  const elJig2Success = document.getElementById('jig2Success');
  const elJig2Failure = document.getElementById('jig2Failure');
  const elJig2Yield = document.getElementById('jig2Yield');
  const elJig2Bar = document.getElementById('jig2Bar');

  const elJig3Total = document.getElementById('jig3Total');
  const elJig3Success = document.getElementById('jig3Success');
  const elJig3Failure = document.getElementById('jig3Failure');
  const elJig3Yield = document.getElementById('jig3Yield');
  const elJig3Bar = document.getElementById('jig3Bar');

  const elTableBody = document.getElementById('tableBody');
  const elRecordCountDisplay = document.getElementById('recordCountDisplay');
  const elLastUpdatedLabel = document.getElementById('lastUpdatedLabel');
  const elCountdownText = document.getElementById('countdownText');
  const elSyncStatusIndicator = document.getElementById('syncStatusIndicator');

  const elSearchInput = document.getElementById('searchInput');
  const elFilterJig = document.getElementById('filterJig');
  const elFilterResult = document.getElementById('filterResult');

  const btnRefresh = document.getElementById('btnRefresh');
  const btnDownloadCsv = document.getElementById('btnDownloadCsv');
  const btnEmailCsv = document.getElementById('btnEmailCsv');

  // Modal Elements
  const elEmailModal = document.getElementById('emailModal');
  const btnCloseEmailModal = document.getElementById('btnCloseEmailModal');
  const btnTriggerAction = document.getElementById('btnTriggerAction');
  const elGhTokenInput = document.getElementById('ghTokenInput');
  const elEmailStatusBox = document.getElementById('emailStatusBox');

  // ==============================================================================
  // JIG & RESULT HELPERS
  // ==============================================================================
  function parseJigNumber(jigId) {
    if (!jigId) return null;
    const str = String(jigId).trim().toLowerCase();
    if (str.includes('1') || str.endsWith('01')) return 1;
    if (str.includes('2') || str.endsWith('02')) return 2;
    if (str.includes('3') || str.endsWith('03')) return 3;
    return null;
  }

  function isPass(resultStr) {
    if (!resultStr) return false;
    const val = String(resultStr).trim().toUpperCase();
    return val === 'PASS' || val === 'SUCCESS';
  }

  function formatNumber(val, decimals = 2) {
    if (val === null || val === undefined || val === '') return '-';
    const num = Number(val);
    return isNaN(num) ? val : num.toFixed(decimals);
  }

  // ==============================================================================
  // DATA FETCHING (Periodic every 1 minute)
  // ==============================================================================
  async function fetchData() {
    if (isFetching) return;
    isFetching = true;
    countdownRemaining = APP_CONFIG.REFRESH_INTERVAL_SECONDS;
    updateCountdownDisplay();

    try {
      const response = await fetch(APP_CONFIG.API_URL, {
        method: 'GET',
        headers: {
          'apikey': APP_CONFIG.API_KEY,
          'Authorization': `Bearer ${APP_CONFIG.API_KEY}`,
          'Content-Type': 'application/json'
        }
      });

      if (!response.ok) {
        throw new Error(`Server returned HTTP ${response.status}`);
      }

      const data = await response.json();
      allRecords = Array.isArray(data) ? data : [];

      // Update statistics and UI
      updateMetrics();
      renderTable();

      const now = new Date();
      const timeStr = now.toLocaleTimeString();
      elLastUpdatedLabel.textContent = `Last updated: ${timeStr}`;
      elSyncStatusIndicator.classList.remove('stale');
    } catch (err) {
      console.error('Data sync error:', err);
      elSyncStatusIndicator.classList.add('stale');
      elLastUpdatedLabel.textContent = `Sync error (${new Date().toLocaleTimeString()})`;
    } finally {
      isFetching = false;
    }
  }

  function updateCountdownDisplay() {
    elCountdownText.textContent = `Auto-refresh: ${countdownRemaining}s`;
  }

  function startCountdown() {
    if (countdownTimer) clearInterval(countdownTimer);
    countdownTimer = setInterval(() => {
      countdownRemaining--;
      if (countdownRemaining <= 0) {
        fetchData();
      } else {
        updateCountdownDisplay();
      }
    }, 1000);
  }

  // ==============================================================================
  // METRICS & CONTAINERS UPDATE
  // ==============================================================================
  function updateMetrics() {
    const totalTested = allRecords.length;
    let totalSuccess = 0;
    let totalFailure = 0;

    const jigStats = {
      1: { total: 0, success: 0, failure: 0 },
      2: { total: 0, success: 0, failure: 0 },
      3: { total: 0, success: 0, failure: 0 }
    };

    allRecords.forEach(r => {
      const pass = isPass(r.result);
      if (pass) {
        totalSuccess++;
      } else {
        totalFailure++;
      }

      const jNum = parseJigNumber(r.jig_id);
      if (jNum && jigStats[jNum]) {
        jigStats[jNum].total++;
        if (pass) {
          jigStats[jNum].success++;
        } else {
          jigStats[jNum].failure++;
        }
      }
    });

    // Top Overall Statistics
    elOverallTotal.textContent = totalTested.toLocaleString();
    elOverallSuccess.textContent = totalSuccess.toLocaleString();
    elOverallFailure.textContent = totalFailure.toLocaleString();
    const overallYieldPct = totalTested > 0 ? ((totalSuccess / totalTested) * 100).toFixed(1) : '0.0';
    elOverallYield.textContent = `${overallYieldPct}%`;

    // 3 Jig Containers
    // Jig 1
    const j1 = jigStats[1];
    elJig1Total.textContent = j1.total.toLocaleString();
    elJig1Success.textContent = j1.success.toLocaleString();
    elJig1Failure.textContent = j1.failure.toLocaleString();
    const j1Yield = j1.total > 0 ? ((j1.success / j1.total) * 100).toFixed(1) : '0.0';
    elJig1Yield.textContent = `${j1Yield}% Yield`;
    elJig1Bar.style.width = `${j1Yield}%`;

    // Jig 2
    const j2 = jigStats[2];
    elJig2Total.textContent = j2.total.toLocaleString();
    elJig2Success.textContent = j2.success.toLocaleString();
    elJig2Failure.textContent = j2.failure.toLocaleString();
    const j2Yield = j2.total > 0 ? ((j2.success / j2.total) * 100).toFixed(1) : '0.0';
    elJig2Yield.textContent = `${j2Yield}% Yield`;
    elJig2Bar.style.width = `${j2Yield}%`;

    // Jig 3
    const j3 = jigStats[3];
    elJig3Total.textContent = j3.total.toLocaleString();
    elJig3Success.textContent = j3.success.toLocaleString();
    elJig3Failure.textContent = j3.failure.toLocaleString();
    const j3Yield = j3.total > 0 ? ((j3.success / j3.total) * 100).toFixed(1) : '0.0';
    elJig3Yield.textContent = `${j3Yield}% Yield`;
    elJig3Bar.style.width = `${j3Yield}%`;
  }

  // ==============================================================================
  // TABLE RENDERING (Latest records on top)
  // ==============================================================================
  function renderTable() {
    const searchTerm = elSearchInput.value.trim().toLowerCase();
    const jigFilter = elFilterJig.value;
    const resultFilter = elFilterResult.value;

    const filtered = allRecords.filter(r => {
      // Jig filter
      if (jigFilter !== 'ALL') {
        const jNum = parseJigNumber(r.jig_id);
        if (String(jNum) !== jigFilter) return false;
      }

      // Result filter
      const pass = isPass(r.result);
      if (resultFilter === 'PASS' && !pass) return false;
      if (resultFilter === 'FAIL' && pass) return false;

      // Text Search
      if (searchTerm) {
        const searchPool = [
          r.id,
          r.sr,
          r.jig_id,
          r.timestamp,
          r.mac,
          r.ficr_mac,
          r.result,
          r.comments
        ].join(' ').toLowerCase();

        if (!searchPool.includes(searchTerm)) return false;
      }

      return true;
    });

    elRecordCountDisplay.textContent = `Showing ${filtered.length} of ${allRecords.length} records`;

    if (filtered.length === 0) {
      elTableBody.innerHTML = `
        <tr class="loading-row">
          <td colspan="14">${allRecords.length === 0 ? 'No test records found' : 'No records match active filters'}</td>
        </tr>
      `;
      return;
    }

    // Build rows (newest first - allRecords is pre-sorted by id.desc)
    const rowsHtml = filtered.map(r => {
      const pass = isPass(r.result);
      const badgeClass = pass ? 'pass' : 'fail';
      const badgeText = pass ? 'PASS' : (r.result || 'FAIL');

      return `
        <tr>
          <td>#${r.id ?? '-'}</td>
          <td>${r.sr ?? '-'}</td>
          <td><strong>${r.jig_id || '-'}</strong></td>
          <td>${r.timestamp || '-'}</td>
          <td class="mac-cell">${r.mac || '-'}</td>
          <td>${r.rssi_dbm !== null && r.rssi_dbm !== undefined ? r.rssi_dbm : '-'}</td>
          <td>${r.ble || '-'}</td>
          <td>${r.imu_acctest || '-'}</td>
          <td>${r.mic_result || '-'}</td>
          <td>${formatNumber(r.active_stream_ma, 2)}</td>
          <td>${formatNumber(r.sleep_current_ma, 2)}</td>
          <td>${formatNumber(r.V_charging, 2)}</td>
          <td><span class="badge-result ${badgeClass}">${badgeText}</span></td>
          <td>${r.comments || '-'}</td>
        </tr>
      `;
    }).join('');

    elTableBody.innerHTML = rowsHtml;
  }

  // ==============================================================================
  // CSV GENERATION & DIRECT DOWNLOAD
  // ==============================================================================
  function buildCsvContent(records) {
    const headers = [
      "ID", "SR", "Jig ID", "Timestamp", "MAC", "FICR MAC",
      "RSSI (dBm)", "BLE", "IMU Acc Test", "Mic Result",
      "LED Delta (mA)", "Amber LED Delta (mA)", "Active Stream (mA)",
      "V Charging (V)", "Sleep Current (mA)", "Client Firmware",
      "Result", "Comments", "Created At"
    ];

    const escapeCsv = (val) => {
      if (val === null || val === undefined) return '""';
      const str = String(val).replace(/"/g, '""');
      return `"${str}"`;
    };

    const lines = [headers.map(escapeCsv).join(",")];

    records.forEach(r => {
      const row = [
        r.id ?? '',
        r.sr ?? '',
        r.jig_id ?? '',
        r.timestamp ?? '',
        r.mac ?? '',
        r.ficr_mac ?? '',
        r.rssi_dbm ?? '',
        r.ble ?? '',
        r.imu_acctest ?? '',
        r.mic_result ?? '',
        r.led_delta_ma ?? '',
        r.amber_led_delta_ma ?? '',
        r.active_stream_ma ?? '',
        r.V_charging ?? '',
        r.sleep_current_ma ?? '',
        r.client_firmware ?? '',
        r.result ?? '',
        r.comments ?? '',
        r.created_at ?? ''
      ];
      lines.push(row.map(escapeCsv).join(","));
    });

    return lines.join("\r\n");
  }

  function downloadCsv() {
    if (!allRecords.length) {
      alert("No data available to download.");
      return;
    }

    const csvData = buildCsvContent(allRecords);
    const blob = new Blob([csvData], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    
    const now = new Date();
    const dateStr = now.toISOString().slice(0, 10).replace(/-/g, "");
    const timeStr = now.toTimeString().slice(0, 8).replace(/:/g, "");
    
    link.setAttribute("href", url);
    link.setAttribute("download", `test_jig_results_${dateStr}_${timeStr}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  // ==============================================================================
  // EMAIL DISPATCH VIA GITHUB ACTIONS
  // ==============================================================================
  function openEmailModal() {
    const savedToken = localStorage.getItem("gh_pat_token") || "";
    elGhTokenInput.value = savedToken;
    elEmailStatusBox.className = "status-box hidden";
    elEmailStatusBox.textContent = "";
    elEmailModal.classList.add("open");
  }

  function closeEmailModal() {
    elEmailModal.classList.remove("open");
  }

  async function triggerEmailDispatch() {
    const token = elGhTokenInput.value.trim();
    if (token) {
      localStorage.setItem("gh_pat_token", token);
    }

    if (!token) {
      elEmailStatusBox.className = "status-box info";
      elEmailStatusBox.innerHTML = `
        A GitHub Personal Access Token (PAT) is required to trigger the workflow directly from this dashboard.
        Alternatively, click <strong>"Open GitHub Actions"</strong> below to run it directly in GitHub.
      `;
      return;
    }

    btnTriggerAction.disabled = true;
    elEmailStatusBox.className = "status-box info";
    elEmailStatusBox.textContent = "Triggering GitHub Actions SMTP dispatch workflow...";

    try {
      const dispatchUrl = `https://api.github.com/repos/${APP_CONFIG.GITHUB_OWNER}/${APP_CONFIG.GITHUB_REPO}/actions/workflows/${APP_CONFIG.WORKFLOW_FILE}/dispatches`;

      const resp = await fetch(dispatchUrl, {
        method: "POST",
        headers: {
          "Accept": "application/vnd.github+json",
          "Authorization": `Bearer ${token}`,
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          ref: APP_CONFIG.WORKFLOW_REF,
          inputs: {
            trigger_source: "web_dashboard"
          }
        })
      });

      if (resp.status === 204) {
        elEmailStatusBox.className = "status-box success";
        elEmailStatusBox.innerHTML = `
          <strong>Workflow triggered successfully!</strong><br>
          The SMTP service is compiling all CSV records and sending to:<br>
          • <code>${APP_CONFIG.EMAIL_RECIPIENTS.join("</code><br>• <code>")}</code>
        `;
      } else {
        const errorJson = await resp.json().catch(() => ({}));
        elEmailStatusBox.className = "status-box error";
        elEmailStatusBox.textContent = `GitHub Error (${resp.status}): ${errorJson.message || resp.statusText}`;
      }
    } catch (err) {
      elEmailStatusBox.className = "status-box error";
      elEmailStatusBox.textContent = `Dispatch failed: ${err.message}`;
    } finally {
      btnTriggerAction.disabled = false;
    }
  }

  // ==============================================================================
  // EVENT LISTENERS & INITIALIZATION
  // ==============================================================================
  function setupEventListeners() {
    btnRefresh.addEventListener('click', () => {
      fetchData();
    });

    btnDownloadCsv.addEventListener('click', downloadCsv);
    btnEmailCsv.addEventListener('click', openEmailModal);
    btnCloseEmailModal.addEventListener('click', closeEmailModal);

    elEmailModal.addEventListener('click', (e) => {
      if (e.target === elEmailModal) closeEmailModal();
    });

    btnTriggerAction.addEventListener('click', triggerEmailDispatch);

    // Filter controls
    elSearchInput.addEventListener('input', renderTable);
    elFilterJig.addEventListener('change', renderTable);
    elFilterResult.addEventListener('change', renderTable);
  }

  function init() {
    setupEventListeners();
    fetchData();
    startCountdown();
  }

  // Boot on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
