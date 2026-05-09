// Chart Configuration
const commonChartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
        x: { display: false },
        y: { 
            display: true, 
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: { color: '#6c7a89', font: { family: 'JetBrains Mono', size: 9 } }
        }
    },
    elements: {
        line: { tension: 0.4, borderWidth: 2 },
        point: { radius: 0 }
    },
    animation: { duration: 0 }
};

const MAX_DATA_POINTS = 30;

// Init Charts
let charts = {};

function initChart(ctxId, color, label) {
    const ctx = document.getElementById(ctxId).getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, 100);
    gradient.addColorStop(0, color.replace(')', ', 0.5)').replace('rgb', 'rgba'));
    gradient.addColorStop(1, 'transparent');

    return new Chart(ctx, {
        type: 'line',
        data: {
            labels: Array(MAX_DATA_POINTS).fill(''),
            datasets: [{
                label: label,
                data: Array(MAX_DATA_POINTS).fill(0),
                borderColor: color,
                backgroundColor: gradient,
                fill: true
            }]
        },
        options: {
            ...commonChartOptions,
            scales: {
                ...commonChartOptions.scales,
                y: { ...commonChartOptions.scales.y, min: 0, suggestedMax: 100 }
            }
        }
    });
}

function updateChart(chart, newValue) {
    const data = chart.data.datasets[0].data;
    data.push(newValue);
    data.shift();
    chart.update();
}

// Global Init
document.addEventListener('DOMContentLoaded', () => {
    // Initialize Charts
    charts.cpu = initChart('cpuChart', '#00f3ff', 'CPU %');
    charts.mem = initChart('memChart', '#ff00ff', 'MEM %');
    charts.disk = initChart('diskChart', '#00ff9d', 'DISK %');
    
    // Net chart needs special handling (autoscale Y)
    const netCtx = document.getElementById('netChart').getContext('2d');
    charts.net = new Chart(netCtx, {
        type: 'line',
        data: {
            labels: Array(MAX_DATA_POINTS).fill(''),
            datasets: [
                { label: 'UP', data: Array(MAX_DATA_POINTS).fill(0), borderColor: '#00ff9d', borderWidth: 1, pointRadius: 0 },
                { label: 'DOWN', data: Array(MAX_DATA_POINTS).fill(0), borderColor: '#ff00ff', borderWidth: 1, pointRadius: 0 }
            ]
        },
        options: commonChartOptions
    });

    // Start Polling
    fetchStats();
    setInterval(fetchStats, 1000);
});

// Format Helper
function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatTime(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

// Data Fetching
// Detect if we're running via app-proxy or directly
const API_BASE = (() => {
    const path = window.location.pathname;
    // If accessed via app-proxy, use the full proxy path
    if (path.includes('/app-proxy/')) {
        const match = path.match(/\/app-proxy\/\d+/);
        return match ? match[0] : '';
    }
    return '';
})();

async function fetchStats() {
    try {
        const url = API_BASE ? `${API_BASE}/api/stats` : 'api/stats';
        const response = await fetch(url);
        const data = await response.json();

        if (data.error) throw new Error(data.error);

        updateUI(data);
    } catch (e) {
        console.error("Stats Fetch Error:", e);
    }
}

// UI Updates
function updateUI(data) {
    // Header
    document.getElementById('hostname').textContent = data.system.hostname.toUpperCase();
    document.getElementById('uptime').textContent = formatTime(data.system.uptime);
    
    // CPU
    const cpuPct = data.cpu.total;
    document.getElementById('cpu-bar').style.width = `${cpuPct}%`;
    document.getElementById('cpu-text').textContent = `${cpuPct.toFixed(1)}%`;
    document.getElementById('cpu-cores').textContent = data.cpu.count;
    document.getElementById('cpu-freq').textContent = `${(data.cpu.freq / 1000).toFixed(1)} GHz`;
    updateChart(charts.cpu, cpuPct);
    
    // Memory
    const memPct = data.memory.percent;
    document.getElementById('mem-bar').style.width = `${memPct}%`;
    document.getElementById('mem-text').textContent = `${memPct.toFixed(1)}%`;
    document.getElementById('mem-used').textContent = formatBytes(data.memory.used);
    document.getElementById('mem-total').textContent = formatBytes(data.memory.total);
    updateChart(charts.mem, memPct);
    
    // Disk
    const diskPct = data.disk.percent;
    document.getElementById('disk-bar').style.width = `${diskPct}%`;
    document.getElementById('disk-text').textContent = `${diskPct.toFixed(1)}%`;
    document.getElementById('disk-free').textContent = formatBytes(data.disk.free);
    
    // Calculate Disk IO Rate (simple diff would need prev state, here we just show total or mocked rate if not available)
    // psutil gives total counters. To get rate, we need to store previous value.
    // For now, let's just show total read/write bytes or implement rate calc later.
    // Simplification: Just show "Active" if bytes change? No, let's skip IO rate for this iteration 
    // and just show Total Read/Write roughly or just space.
    // Actually, let's implement a simple rate calc in JS global scope
    if (!window.lastDiskIO) window.lastDiskIO = { r: 0, w: 0, t: Date.now() };
    const now = Date.now();
    const dt = (now - window.lastDiskIO.t) / 1000;
    if (dt > 0) {
        const rRate = (data.disk.read_bytes - window.lastDiskIO.r) / dt;
        const wRate = (data.disk.write_bytes - window.lastDiskIO.w) / dt;
        document.getElementById('disk-io').textContent = `${formatBytes(rRate+wRate)}/s`;
        updateChart(charts.disk, Math.min(((rRate+wRate)/10000000)*100, 100)); // Visual scale
    }
    window.lastDiskIO = { r: data.disk.read_bytes, w: data.disk.write_bytes, t: now };

    // Network
    if (!window.lastNet) window.lastNet = { s: 0, r: 0, t: Date.now() };
    const netDt = (now - window.lastNet.t) / 1000;
    if (netDt > 0) {
        const txRate = (data.network.bytes_sent - window.lastNet.s) / netDt;
        const rxRate = (data.network.bytes_recv - window.lastNet.r) / netDt;
        
        document.getElementById('net-up').textContent = `${formatBytes(txRate)}/s`;
        document.getElementById('net-down').textContent = `${formatBytes(rxRate)}/s`;
        
        // Update Chart
        const netChart = charts.net;
        netChart.data.datasets[0].data.push(txRate / 1024); // KB/s
        netChart.data.datasets[0].data.shift();
        netChart.data.datasets[1].data.push(rxRate / 1024); // KB/s
        netChart.data.datasets[1].data.shift();
        netChart.update();
    }
    window.lastNet = { s: data.network.bytes_sent, r: data.network.bytes_recv, t: now };

    // Processes
    const tbody = document.getElementById('proc-list');
    tbody.innerHTML = data.processes.map(p => `
        <tr>
            <td>${p.pid}</td>
            <td>${p.name.substring(0, 20)}</td>
            <td style="color: var(--primary)">${p.cpu_percent.toFixed(1)}%</td>
            <td style="color: var(--secondary)">${p.memory_percent.toFixed(1)}%</td>
        </tr>
    `).join('');

    // System Info
    document.getElementById('sys-os').textContent = data.system.os;
    // Temp
    let temp = '--';
    if (data.sensors && Object.keys(data.sensors).length > 0) {
        // Try to find a CPU temp
        for (const key in data.sensors) {
            if (data.sensors[key][0] && data.sensors[key][0].current) {
                temp = data.sensors[key][0].current;
                break;
            }
        }
    }
    document.getElementById('sys-temp').textContent = temp !== '--' ? `${temp}°C` : '--';
    
    // Boot Time
    const bootDate = new Date(Date.now() - (data.system.uptime * 1000));
    document.getElementById('sys-boot').textContent = bootDate.toLocaleTimeString();
}

// =============================================================================
// Log Viewer
// =============================================================================

let currentLogType = null;

function initLogViewer() {
    const btnBlackbox = document.getElementById('btn-blackbox-logs');
    const btnMonitor = document.getElementById('btn-monitor-logs');
    const btnRefresh = document.getElementById('btn-refresh-logs');
    const btnClose = document.getElementById('btn-close-logs');
    const modal = document.getElementById('log-modal');
    const linesSelect = document.getElementById('log-lines-select');

    if (btnBlackbox) {
        btnBlackbox.addEventListener('click', () => openLogViewer('blackbox'));
    }

    if (btnMonitor) {
        btnMonitor.addEventListener('click', () => openLogViewer('monitor'));
    }

    if (btnRefresh) {
        btnRefresh.addEventListener('click', () => {
            if (currentLogType) fetchLogs(currentLogType);
        });
    }

    if (btnClose) {
        btnClose.addEventListener('click', closeLogViewer);
    }

    if (linesSelect) {
        linesSelect.addEventListener('change', () => {
            if (currentLogType) fetchLogs(currentLogType);
        });
    }

    // Close on backdrop click
    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeLogViewer();
        });
    }

    // Close on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal && modal.classList.contains('active')) {
            closeLogViewer();
        }
    });
}

function openLogViewer(type) {
    currentLogType = type;
    const modal = document.getElementById('log-modal');
    const title = document.getElementById('log-modal-title');

    if (type === 'blackbox') {
        title.textContent = 'BLACKBOX SERVICE LOGS';
    } else if (type === 'monitor') {
        title.textContent = 'SYSTEM MONITOR LOGS';
    }

    modal.classList.add('active');
    fetchLogs(type);
}

function closeLogViewer() {
    const modal = document.getElementById('log-modal');
    modal.classList.remove('active');
    currentLogType = null;
}

async function fetchLogs(type) {
    const logContent = document.getElementById('log-content');
    const logStatus = document.getElementById('log-status');
    const linesSelect = document.getElementById('log-lines-select');
    const lines = linesSelect ? linesSelect.value : 200;

    logContent.textContent = 'Loading...';
    logStatus.textContent = 'Fetching logs...';

    try {
        const endpoint = type === 'blackbox' ? 'api/logs/blackbox' : 'api/logs/monitor';
        const url = API_BASE ? `${API_BASE}/${endpoint}?lines=${lines}` : `${endpoint}?lines=${lines}`;

        const response = await fetch(url);
        const data = await response.json();

        if (data.success) {
            logContent.textContent = data.logs || '(Empty log)';

            // Scroll to bottom to show latest logs
            const modalBody = document.querySelector('.log-modal-body');
            if (modalBody) {
                modalBody.scrollTop = modalBody.scrollHeight;
            }

            // Update status
            if (type === 'monitor' && data.total_lines) {
                logStatus.textContent = `Showing ${data.lines} of ${data.total_lines} lines from ${data.source}`;
            } else {
                logStatus.textContent = `Showing ${data.lines} lines from ${data.source}`;
            }
        } else {
            logContent.textContent = `Error: ${data.error}`;
            logStatus.textContent = 'Failed to fetch logs';
        }
    } catch (e) {
        console.error('Log fetch error:', e);
        logContent.textContent = `Error fetching logs: ${e.message}`;
        logStatus.textContent = 'Connection error';
    }
}

// Initialize log viewer when DOM is ready
document.addEventListener('DOMContentLoaded', initLogViewer);
