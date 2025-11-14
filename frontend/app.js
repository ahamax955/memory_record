const state = {
    charts: new Map(),
    polling: null,
};

const devicesEl = document.getElementById("devices");
const summaryEl = document.getElementById("summary");
const heapdumpTable = document.getElementById("heapdump-table");

const deviceIdToDomId = (device) => device.replace(/[^a-zA-Z0-9]/g, "_");

function renderSummary(payload) {
    summaryEl.innerHTML = `
        <div>进程：<strong>${payload.process}</strong></div>
        <div>采样周期：${payload.interval}s | 设备数量：${payload.devices_monitored}</div>
        <div>阈值：<span class="threshold">${payload.threshold} MB</span></div>
        <div>服务启动时间：${new Date(payload.start_time).toLocaleString()}</div>
    `;
}

function ensureDeviceCard(device) {
    const domId = deviceIdToDomId(device.device);
    let card = document.getElementById(`card-${domId}`);
    if (card) {
        return card;
    }

    card = document.createElement("article");
    card.className = "card";
    card.id = `card-${domId}`;
    card.innerHTML = `
        <header>
            <div>
                <div>${device.device}</div>
                <span class="status-text"></span>
            </div>
            <span class="latest-value"></span>
        </header>
        <canvas id="chart-${domId}" height="160"></canvas>
    `;
    devicesEl.appendChild(card);

    const ctx = card.querySelector("canvas").getContext("2d");
    const chart = new Chart(ctx, {
        type: "line",
        data: {
            labels: [],
            datasets: [
                {
                    label: "Memory (MB)",
                    data: [],
                    fill: false,
                    borderColor: "rgba(56, 189, 248, 0.9)",
                    tension: 0.2,
                },
            ],
        },
        options: {
            responsive: true,
            animation: false,
            scales: {
                x: {
                    ticks: { color: "#94a3b8" },
                },
                y: {
                    ticks: { color: "#94a3b8" },
                },
            },
            plugins: {
                legend: { display: false },
            },
        },
    });
    state.charts.set(device.device, chart);
    return card;
}

function updateDeviceCard(device, threshold) {
    const card = ensureDeviceCard(device);
    const chart = state.charts.get(device.device);
    const statusText = card.querySelector(".status-text");
    const latestValue = card.querySelector(".latest-value");

    const lastValue = device.latest_mb ? device.latest_mb.toFixed(1) : "--";
    latestValue.textContent = `${lastValue} MB`;

    const indicatorClass =
        device.status === "success" ? "status-success" : "status-error";
    statusText.innerHTML = `
        <span class="status-dot ${indicatorClass}"></span>
        ${device.status}
        ${
            device.last_updated
                ? " | 更新时间 " + new Date(device.last_updated).toLocaleTimeString()
                : ""
        }
    `;

    if (device.history.length > 0) {
        chart.data.labels = device.history.map((item) =>
            new Date(item.time).toLocaleTimeString()
        );
        chart.data.datasets[0].data = device.history.map((item) => item.value);
        chart.update("none");
    }
}

function renderHeapdumps(files) {
    heapdumpTable.innerHTML = "";
    if (!files.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 3;
        cell.textContent = "暂无数据";
        heapdumpTable.appendChild(row);
        row.appendChild(cell);
        return;
    }

    files.forEach((file) => {
        const row = document.createElement("tr");
        row.innerHTML = `
            <td>${file.name}</td>
            <td>${file.size_mb} MB</td>
            <td>${new Date(file.modified).toLocaleString()}</td>
        `;
        heapdumpTable.appendChild(row);
    });
}

async function fetchStatus() {
    const [statusRes, heapdumpRes] = await Promise.all([
        fetch("/api/status"),
        fetch("/api/heapdumps"),
    ]);
    const status = await statusRes.json();
    const heapdumps = await heapdumpRes.json();
    renderSummary(status);
    status.devices.forEach((device) => updateDeviceCard(device, status.threshold));
    renderHeapdumps(heapdumps.files || []);
}

async function init() {
    await fetchStatus();
    state.polling = setInterval(fetchStatus, 5000);
}

document.addEventListener("DOMContentLoaded", init);

