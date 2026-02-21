(function (global) {
    'use strict';

    const SIDEO = global.SIDEO || (global.SIDEO = {});

    const utils = SIDEO.utils || (SIDEO.utils = {});
    utils.getCookie = function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    };

    utils.escapeHtml = function escapeHtml(str) {
        return String(str)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    };

    utils.renderAIMarkup = function renderAIMarkup(text) {
        // Sanitiza TODO y luego habilita solo **negrita**.
        let safe = utils.escapeHtml(text || '');
        safe = safe.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        return safe;
    };

    utils.debounce = function debounce(fn, waitMs) {
        let t = null;
        return function debounced() {
            const args = arguments;
            if (t) {
                clearTimeout(t);
            }
            t = setTimeout(function () {
                t = null;
                fn.apply(null, args);
            }, waitMs || 120);
        };
    };

    const charts = SIDEO.charts || (SIDEO.charts = {});

    charts.palette = ['#0f172a', '#2563eb', '#16a34a', '#eab308', '#dc2626', '#7c3aed', '#0ea5e9'];

    charts._getCanvasAttrRatio = function _getCanvasAttrRatio(canvas, fallbackRatio) {
        try {
            const w = Number(canvas.getAttribute('width'));
            const h = Number(canvas.getAttribute('height'));
            if (w > 0 && h > 0) return w / h;
        } catch (e) {
            // ignore
        }
        return fallbackRatio || 2;
    };

    charts._destroyChartJs = function _destroyChartJs(canvas) {
        if (!canvas) return;
        try {
            if (canvas._chartjs && typeof canvas._chartjs.destroy === 'function') {
                canvas._chartjs.destroy();
            }
        } catch (e) {
            // ignore
        }
        canvas._chartjs = null;
    };

    charts._hasChartJs = function _hasChartJs() {
        return !!global.Chart;
    };

    charts._resolveCanvas = function _resolveCanvas(canvasOrId) {
        if (!canvasOrId) return null;
        if (typeof canvasOrId === 'string') {
            return document.getElementById(canvasOrId);
        }
        return canvasOrId;
    };

    charts._num = function _num(value) {
        if (value === null || value === undefined || value === '') return null;
        const n = Number(value);
        return Number.isFinite(n) ? n : null;
    };

    charts._getName = function _getName(item) {
        if (!item) return '';
        return String(item.name ?? item.candidatos ?? item.CANDIDATOS ?? item.label ?? '');
    };

    charts._getCost = function _getCost(item) {
        if (!item) return null;
        return charts._num(item.cost ?? item.costo ?? item.COSTO);
    };

    charts._getTotal = function _getTotal(item) {
        if (!item) return null;
        return charts._num(item.total ?? item.ventaja ?? item.VENTAJA);
    };

    charts._getRatio = function _getRatio(item, cost, total) {
        if (item && item.ratio != null) return charts._num(item.ratio);
        if (item && item.RATIO != null) return charts._num(item.RATIO);

        const c = cost != null ? cost : charts._getCost(item);
        const t = total != null ? total : charts._getTotal(item);
        if (c == null || t == null || t === 0) return null;
        return c / t;
    };

    charts._setupHiDPICanvas = function _setupHiDPICanvas(canvas) {
        const dpr = global.devicePixelRatio || 1;

        // CSS size
        const rect = canvas.getBoundingClientRect();
        const displayWidth = Math.max(1, Math.round(rect.width));
        const ratio = (canvas.height && canvas.width) ? (canvas.height / canvas.width) : (3 / 8);
        const displayHeight = Math.max(1, Math.round(displayWidth * ratio));

        const needResize = canvas.width !== Math.round(displayWidth * dpr) || canvas.height !== Math.round(displayHeight * dpr);
        if (needResize) {
            canvas.width = Math.round(displayWidth * dpr);
            canvas.height = Math.round(displayHeight * dpr);
        }

        const ctx = canvas.getContext('2d');
        // Dibujamos en coordenadas CSS (px) y escalamos por dpr.
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

        return {
            ctx,
            dpr,
            width: displayWidth,
            height: displayHeight,
        };
    };

    charts.drawBarChart = function drawBarChart(canvasOrId, items, opts) {
        const canvas = charts._resolveCanvas(canvasOrId);
        if (!canvas) return;

        const options = opts || {};
        const yLabel = options.yLabel || 'Costo/Ventaja (S/ por punto)';

        const itemsSafe = Array.isArray(items) ? items : [];
        const { ctx, width, height } = charts._setupHiDPICanvas(canvas);

        ctx.clearRect(0, 0, width, height);

        const paddingLeft = 48;
        const paddingRight = 16;
        const paddingTop = 18;
        const paddingBottom = 44;

        const plotW = Math.max(1, width - paddingLeft - paddingRight);
        const plotH = Math.max(1, height - paddingTop - paddingBottom);

        const ratios = itemsSafe.map(i => {
            const ratio = charts._getRatio(i);
            return ratio != null ? ratio : 0;
        });
        const maxV = Math.max.apply(null, ratios.concat([1]));

        // Fondo
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, width, height);

        // Ejes
        ctx.strokeStyle = '#111827';
        ctx.lineWidth = 1.2;

        ctx.beginPath();
        ctx.moveTo(paddingLeft, paddingTop + plotH);
        ctx.lineTo(paddingLeft + plotW, paddingTop + plotH);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(paddingLeft, paddingTop);
        ctx.lineTo(paddingLeft, paddingTop + plotH);
        ctx.stroke();

        // Barras
        const barGap = 10;
        const count = Math.max(1, itemsSafe.length);
        const barW = Math.max(14, (plotW - barGap * (count - 1)) / count);

        itemsSafe.forEach((item, idx) => {
            const ratio = charts._getRatio(item);
            const v = ratio != null ? ratio : 0;
            const h = (v / maxV) * plotH;
            const x = paddingLeft + idx * (barW + barGap);
            const y = paddingTop + plotH - h;

            ctx.fillStyle = charts.palette[idx % charts.palette.length];
            ctx.globalAlpha = (v > 0) ? 0.90 : 0.25;
            ctx.fillRect(x, y, barW, h);
            ctx.globalAlpha = 1;

            // Etiqueta
            ctx.fillStyle = '#4b5563';
            ctx.font = '11px "Segoe UI", system-ui, -apple-system, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            const label = charts._getName(item);
            ctx.fillText(label, x + barW / 2, paddingTop + plotH + 6);
        });

        // Título eje Y
        ctx.save();
        ctx.translate(14, paddingTop + plotH / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#111827';
        ctx.font = '11px "Segoe UI", system-ui, -apple-system, sans-serif';
        ctx.fillText(yLabel, 0, 0);
        ctx.restore();
    };

    charts.drawScatter = function drawScatter(canvasOrId, items, opts) {
        const canvas = charts._resolveCanvas(canvasOrId);
        if (!canvas) return;

        const options = opts || {};
        const showGrid = options.showGrid !== false;
        const xLabel = options.xLabel || 'Costo (S/)';
        const yLabel = options.yLabel || 'Ventaja total';
        const pointRadius = (options.pointRadius != null) ? Number(options.pointRadius) : 4;

        const itemsSafe = Array.isArray(items) ? items : [];
        const points = itemsSafe
            .map(function (p) {
                const cost = charts._getCost(p);
                const total = charts._getTotal(p);
                if (cost == null || total == null) return null;
                if (cost === 0 && total === 0) return null;
                return {
                    name: charts._getName(p),
                    cost: cost,
                    total: total,
                    ratio: charts._getRatio(p, cost, total),
                };
            })
            .filter(Boolean);

        const { ctx, width, height } = charts._setupHiDPICanvas(canvas);
        ctx.clearRect(0, 0, width, height);

        if (points.length === 0) return;

        const maxCost = Math.max.apply(null, points.map(p => p.cost || 0).concat([1]));
        const maxTotal = Math.max.apply(null, points.map(p => p.total || 0).concat([1]));

        const paddingLeft = 64;
        const paddingBottom = 56;
        const paddingTop = 28;
        const paddingRight = 24;

        const plotW = Math.max(1, width - paddingLeft - paddingRight);
        const plotH = Math.max(1, height - paddingTop - paddingBottom);

        function xScale(cost) {
            return paddingLeft + (Number(cost) / (maxCost || 1)) * plotW;
        }

        function yScale(total) {
            return paddingTop + plotH - (Number(total) / (maxTotal || 1)) * plotH;
        }

        // Fondo
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, width, height);

        // Ejes
        ctx.strokeStyle = '#111827';
        ctx.lineWidth = 1.3;

        ctx.beginPath();
        ctx.moveTo(paddingLeft, paddingTop + plotH);
        ctx.lineTo(paddingLeft + plotW, paddingTop + plotH);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(paddingLeft, paddingTop + plotH);
        ctx.lineTo(paddingLeft, paddingTop);
        ctx.stroke();

        // Rejilla y ticks
        const xDivs = 5;
        const yDivs = 5;

        ctx.font = '11px "Segoe UI", system-ui, -apple-system, sans-serif';
        ctx.fillStyle = '#4b5563';

        if (showGrid) {
            for (let i = 0; i <= xDivs; i++) {
                const v = (maxCost / xDivs) * i;
                const x = xScale(v);
                ctx.strokeStyle = '#e5e7eb';
                ctx.setLineDash([4, 4]);
                ctx.beginPath();
                ctx.moveTo(x, paddingTop);
                ctx.lineTo(x, paddingTop + plotH);
                ctx.stroke();
                ctx.setLineDash([]);

                ctx.textAlign = 'center';
                ctx.textBaseline = 'top';
                ctx.fillText(v.toFixed(0), x, paddingTop + plotH + 6);
            }

            for (let i = 0; i <= yDivs; i++) {
                const v = (maxTotal / yDivs) * i;
                const y = yScale(v);
                ctx.strokeStyle = '#e5e7eb';
                ctx.setLineDash([4, 4]);
                ctx.beginPath();
                ctx.moveTo(paddingLeft, y);
                ctx.lineTo(paddingLeft + plotW, y);
                ctx.stroke();
                ctx.setLineDash([]);

                ctx.textAlign = 'right';
                ctx.textBaseline = 'middle';
                ctx.fillText(v.toFixed(0), paddingLeft - 8, y);
            }
        }

        // Etiquetas
        ctx.fillStyle = '#111827';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(xLabel, paddingLeft + plotW / 2, paddingTop + plotH + 30);

        ctx.save();
        ctx.translate(paddingLeft - 40, paddingTop + plotH / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(yLabel, 0, 0);
        ctx.restore();

        const originX = xScale(0);
        const originY = yScale(0);

        const colors = options.colors || charts.palette.concat(['#0f172a']);

        points.forEach((p, idx) => {
            const x = xScale(p.cost);
            const y = yScale(p.total);
            const color = colors[idx % colors.length];

            // Línea origen -> punto
            ctx.strokeStyle = color;
            ctx.globalAlpha = options.lineAlpha != null ? Number(options.lineAlpha) : 0.7;
            ctx.lineWidth = options.lineWidth != null ? Number(options.lineWidth) : 2;
            ctx.beginPath();
            ctx.moveTo(originX, originY);
            ctx.lineTo(x, y);
            ctx.stroke();

            // Punto
            ctx.globalAlpha = 1;
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(x, y, pointRadius, 0, Math.PI * 2);
            ctx.fill();

            // Etiqueta
            ctx.fillStyle = '#111827';
            ctx.font = '11px "Segoe UI", system-ui, -apple-system, sans-serif';
            const alignRight = x > width - 140;
            ctx.textAlign = alignRight ? 'right' : 'left';
            ctx.textBaseline = 'bottom';
            const dx = alignRight ? -8 : 8;
            const name = (p && p.name) ? String(p.name) : '';
            ctx.fillText(name, x + dx, y - 6);
        });

        // Guardar geometría para interacción (tooltip/click)
        canvas._cbaPlot = {
            originX,
            originY,
            points: points.map((p, idx) => {
                const x = xScale(p.cost);
                const y = yScale(p.total);
                return {
                    name: p.name,
                    cost: p.cost,
                    total: p.total,
                    ratio: p.ratio,
                    x,
                    y,
                    color: colors[idx % colors.length]
                };
            })
        };
    };

    charts.renderRatioBarChart = function renderRatioBarChart(canvasOrId, items, opts) {
        const canvas = charts._resolveCanvas(canvasOrId);
        if (!canvas) return;

        const itemsSafe = Array.isArray(items) ? items : [];
        const options = opts || {};

        if (!charts._hasChartJs()) {
            charts.drawBarChart(canvas, itemsSafe, { yLabel: options.yLabel || 'Costo/Ventaja (S/ por punto)' });
            return;
        }

        charts._destroyChartJs(canvas);

        const labels = itemsSafe.map(i => charts._getName(i));
        const values = itemsSafe.map(i => {
            const ratio = charts._getRatio(i);
            return ratio != null ? Number(ratio) : null;
        });
        const colors = itemsSafe.map((_i, idx) => charts.palette[idx % charts.palette.length]);

        const aspectRatio = charts._getCanvasAttrRatio(canvas, 520 / 280);
        const yLabel = options.yLabel || 'Costo/Ventaja (S/ por punto)';

        canvas._chartjs = new global.Chart(canvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: yLabel,
                        data: values,
                        backgroundColor: colors,
                        borderColor: colors,
                        borderWidth: 0,
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                aspectRatio: aspectRatio,
                animation: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function (ctx) {
                                const v = ctx.raw;
                                if (v === null || v === undefined) return 'Sin dato';
                                return 'S/ ' + Number(v).toFixed(6);
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: { color: '#4b5563', font: { size: 11 } },
                        grid: { display: false },
                    },
                    y: {
                        beginAtZero: true,
                        ticks: { color: '#4b5563', font: { size: 11 } },
                        grid: { color: '#e5e7eb', borderDash: [4, 4] },
                        title: {
                            display: true,
                            text: yLabel,
                            color: '#111827',
                            font: { size: 11, weight: '600' },
                        }
                    }
                }
            }
        });
    };

    charts.renderCostBenefitScatter = function renderCostBenefitScatter(canvasOrId, items, opts) {
        const canvas = charts._resolveCanvas(canvasOrId);
        if (!canvas) return;

        const itemsSafe = Array.isArray(items) ? items : [];
        const points = itemsSafe
            .map(function (p) {
                const cost = charts._getCost(p);
                const total = charts._getTotal(p);
                if (cost == null || total == null) return null;
                if (cost === 0 && total === 0) return null;
                return {
                    name: charts._getName(p),
                    cost: cost,
                    total: total,
                    ratio: charts._getRatio(p, cost, total),
                };
            })
            .filter(Boolean);
        const options = opts || {};

        if (!charts._hasChartJs()) {
            charts.drawScatter(canvas, itemsSafe, {
                xLabel: options.xLabel || 'Costo (S/)',
                yLabel: options.yLabel || 'Ventaja total',
                showGrid: options.showGrid !== false,
                lineAlpha: options.lineAlpha,
                lineWidth: options.lineWidth,
                pointRadius: options.pointRadius,
            });
            return;
        }

        charts._destroyChartJs(canvas);

        if (points.length === 0) return;

        const aspectRatio = charts._getCanvasAttrRatio(canvas, 980 / 380);

        const datasets = points.map(function (p, idx) {
            const color = charts.palette[idx % charts.palette.length];
            return {
                label: String(p.name || ''),
                data: [
                    { x: 0, y: 0 },
                    {
                        x: Number(p.cost),
                        y: Number(p.total),
                        _meta: {
                            name: p.name,
                            cost: p.cost,
                            total: p.total,
                            ratio: p.ratio,
                        }
                    }
                ],
                showLine: true,
                borderColor: color,
                backgroundColor: color,
                borderWidth: 2,
                pointRadius: [0, options.pointRadius != null ? Number(options.pointRadius) : 4],
                pointHoverRadius: [0, 6],
                pointHitRadius: [0, 10],
                tension: 0,
            };
        });

        const xLabel = options.xLabel || 'Costo (S/)';
        const yLabel = options.yLabel || 'Ventaja total';

        canvas._chartjs = new global.Chart(canvas, {
            type: 'line',
            data: { datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                aspectRatio: aspectRatio,
                animation: false,
                interaction: { mode: 'nearest', intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        filter: function (ctx) {
                            // Solo el punto final (no el origen)
                            return ctx.dataIndex === 1;
                        },
                        callbacks: {
                            title: function (items) {
                                const it = items && items[0];
                                return it ? (it.dataset && it.dataset.label ? it.dataset.label : '') : '';
                            },
                            label: function (ctx) {
                                const raw = ctx.raw || {};
                                const meta = raw._meta || {};
                                const cost = meta.cost != null ? Number(meta.cost).toFixed(2) : '-';
                                const total = meta.total != null ? String(meta.total) : '-';
                                const ratio = meta.ratio != null ? Number(meta.ratio).toFixed(6) : '-';
                                return [
                                    'Costo: S/ ' + cost,
                                    'Ventaja total: ' + total,
                                    'Costo/Ventaja: S/ ' + ratio,
                                ];
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        type: 'linear',
                        beginAtZero: true,
                        title: { display: true, text: xLabel, color: '#111827', font: { size: 11, weight: '600' } },
                        ticks: { color: '#4b5563', font: { size: 11 } },
                        grid: { color: '#e5e7eb', borderDash: [4, 4] },
                    },
                    y: {
                        type: 'linear',
                        beginAtZero: true,
                        title: { display: true, text: yLabel, color: '#111827', font: { size: 11, weight: '600' } },
                        ticks: { color: '#4b5563', font: { size: 11 } },
                        grid: { color: '#e5e7eb', borderDash: [4, 4] },
                    }
                }
            }
        });

        if (options.legendContainer) {
            try {
                options.legendContainer.innerHTML = '';
                points.forEach(function (p, idx) {
                    const color = charts.palette[idx % charts.palette.length];

                    const item = document.createElement('div');
                    item.style.display = 'flex';
                    item.style.alignItems = 'center';
                    item.style.marginBottom = '4px';

                    const swatch = document.createElement('span');
                    swatch.style.display = 'inline-block';
                    swatch.style.width = '10px';
                    swatch.style.height = '10px';
                    swatch.style.borderRadius = '999px';
                    swatch.style.marginRight = '6px';
                    swatch.style.backgroundColor = color;

                    const label = document.createElement('span');
                    label.textContent = String(p.name || '');

                    item.appendChild(swatch);
                    item.appendChild(label);
                    options.legendContainer.appendChild(item);
                });
            } catch (e) {
                // ignore
            }
        }

        if (options.tooltipElement) {
            charts.attachChartJsClickTooltip(canvas._chartjs, canvas, options.tooltipElement);
        }
    };

    charts.attachChartJsClickTooltip = function attachChartJsClickTooltip(chart, canvas, tooltipElement) {
        if (!chart || !canvas || !tooltipElement) return;
        if (canvas._chartjsClickTooltipBound) return;
        canvas._chartjsClickTooltipBound = true;

        function hideTip() {
            tooltipElement.style.display = 'none';
        }

        function showTip(x, y, html) {
            tooltipElement.innerHTML = html;
            tooltipElement.style.display = 'block';

            const wrap = canvas.parentElement;
            const wrapRect = wrap.getBoundingClientRect();
            const tipRect = tooltipElement.getBoundingClientRect();

            let left = x + 12;
            let top = y + 12;

            left = Math.min(left, wrapRect.width - tipRect.width - 8);
            top = Math.min(top, wrapRect.height - tipRect.height - 8);
            left = Math.max(8, left);
            top = Math.max(8, top);

            tooltipElement.style.left = left + 'px';
            tooltipElement.style.top = top + 'px';
        }

        canvas.addEventListener('click', function (evt) {
            const elements = chart.getElementsAtEventForMode(evt, 'nearest', { intersect: false }, true);
            if (!elements || elements.length === 0) {
                hideTip();
                return;
            }

            const el = elements[0];
            const datasetIndex = el.datasetIndex;
            const index = el.index;
            if (index !== 1) {
                hideTip();
                return;
            }

            const ds = chart.data.datasets[datasetIndex];
            const raw = (ds && ds.data && ds.data[index]) ? ds.data[index] : {};
            const meta = raw._meta || {};

            const rect = canvas.getBoundingClientRect();
            const x = evt.clientX - rect.left;
            const y = evt.clientY - rect.top;

            const ratio = (meta.ratio !== null && meta.ratio !== undefined)
                ? Number(meta.ratio).toFixed(6)
                : '-';
            const cost = (meta.cost !== null && meta.cost !== undefined)
                ? Number(meta.cost).toFixed(2)
                : '-';
            const total = (meta.total !== null && meta.total !== undefined)
                ? String(meta.total)
                : '-';
            const name = String(meta.name || ds.label || '');

            const html =
                '<div class="cba-tooltip__title">' + utils.escapeHtml(name) + '</div>' +
                '<div class="cba-tooltip__row"><span class="k">Costo:</span> <span class="v">S/ ' + utils.escapeHtml(cost) + '</span></div>' +
                '<div class="cba-tooltip__row"><span class="k">Ventaja total:</span> <span class="v">' + utils.escapeHtml(total) + '</span></div>' +
                '<div class="cba-tooltip__row"><span class="k">Costo/Ventaja:</span> <span class="v">S/ ' + utils.escapeHtml(ratio) + '</span></div>';

            showTip(x, y, html);
        });

        canvas.addEventListener('mouseleave', hideTip);
        global.addEventListener('scroll', hideTip, { passive: true });
        global.addEventListener('resize', hideTip);
    };

    charts.attachScatterClickTooltip = function attachScatterClickTooltip(canvasOrId, tooltipOrId) {
        const canvas = charts._resolveCanvas(canvasOrId);
        const tip = (typeof tooltipOrId === 'string') ? document.getElementById(tooltipOrId) : tooltipOrId;
        if (!canvas || !tip) return;
        if (canvas._cbaTooltipBound) return;
        canvas._cbaTooltipBound = true;

        function distPointToSegment(px, py, ax, ay, bx, by) {
            const abx = bx - ax;
            const aby = by - ay;
            const apx = px - ax;
            const apy = py - ay;
            const abLen2 = abx * abx + aby * aby;
            let t = 0;
            if (abLen2 > 0) {
                t = (apx * abx + apy * aby) / abLen2;
                t = Math.max(0, Math.min(1, t));
            }
            const cx = ax + t * abx;
            const cy = ay + t * aby;
            const dx = px - cx;
            const dy = py - cy;
            return Math.sqrt(dx * dx + dy * dy);
        }

        function hideTip() {
            tip.style.display = 'none';
        }

        function showTip(x, y, html) {
            tip.innerHTML = html;
            tip.style.display = 'block';

            const wrap = canvas.parentElement;
            const wrapRect = wrap.getBoundingClientRect();
            const tipRect = tip.getBoundingClientRect();

            let left = x + 12;
            let top = y + 12;

            left = Math.min(left, wrapRect.width - tipRect.width - 8);
            top = Math.min(top, wrapRect.height - tipRect.height - 8);
            left = Math.max(8, left);
            top = Math.max(8, top);

            tip.style.left = left + 'px';
            tip.style.top = top + 'px';
        }

        canvas.addEventListener('click', function (evt) {
            const plot = canvas._cbaPlot;
            if (!plot || !plot.points || plot.points.length === 0) return;

            const rect = canvas.getBoundingClientRect();
            const x = evt.clientX - rect.left;
            const y = evt.clientY - rect.top;

            let best = null;
            let bestD = Infinity;

            for (let i = 0; i < plot.points.length; i++) {
                const p = plot.points[i];
                const dLine = distPointToSegment(x, y, plot.originX, plot.originY, p.x, p.y);
                const dx = x - p.x;
                const dy = y - p.y;
                const dPoint = Math.sqrt(dx * dx + dy * dy);
                const d = Math.min(dLine, dPoint);
                if (d < bestD) {
                    bestD = d;
                    best = p;
                }
            }

            const threshold = 10;
            if (!best || bestD > threshold) {
                hideTip();
                return;
            }

            const ratio = (best.ratio !== null && best.ratio !== undefined)
                ? Number(best.ratio).toFixed(6)
                : '-';
            const cost = (best.cost !== null && best.cost !== undefined)
                ? Number(best.cost).toFixed(2)
                : '-';
            const total = (best.total !== null && best.total !== undefined)
                ? String(best.total)
                : '-';

            const html =
                '<div class="cba-tooltip__title">' + utils.escapeHtml(best.name || '') + '</div>' +
                '<div class="cba-tooltip__row"><span class="k">Costo:</span> <span class="v">S/ ' + utils.escapeHtml(cost) + '</span></div>' +
                '<div class="cba-tooltip__row"><span class="k">Ventaja total:</span> <span class="v">' + utils.escapeHtml(total) + '</span></div>' +
                '<div class="cba-tooltip__row"><span class="k">Costo/Ventaja:</span> <span class="v">S/ ' + utils.escapeHtml(ratio) + '</span></div>';

            showTip(x, y, html);
        });

        canvas.addEventListener('mouseleave', hideTip);
        global.addEventListener('scroll', hideTip, { passive: true });
        global.addEventListener('resize', hideTip);
    };

    charts.drawCostBenefitChart = function drawCostBenefitChart(canvasOrId, legendOrId, chartData) {
        const canvas = charts._resolveCanvas(canvasOrId);
        const legendContainer = (typeof legendOrId === 'string') ? document.getElementById(legendOrId) : legendOrId;
        if (!canvas) return;

        const itemsSafe = Array.isArray(chartData) ? chartData : [];
        const points = itemsSafe
            .map(function (p) {
                const cost = charts._getCost(p);
                const total = charts._getTotal(p);
                if (cost == null || total == null) return null;
                if (cost === 0 && total === 0) return null;
                return {
                    name: charts._getName(p),
                    cost: cost,
                    total: total,
                    ratio: charts._getRatio(p, cost, total),
                };
            })
            .filter(Boolean);

        const { ctx, width, height } = charts._setupHiDPICanvas(canvas);

        ctx.clearRect(0, 0, width, height);
        if (legendContainer) legendContainer.innerHTML = '';

        if (points.length === 0) return;

    const maxCost = Math.max.apply(null, points.map(p => p.cost || 0).concat([1]));
    const maxTotal = Math.max.apply(null, points.map(p => p.total || 0).concat([1]));

        const paddingLeft = 64;
        const paddingBottom = 56;
        const paddingTop = 32;
        const paddingRight = 32;

        const plotW = Math.max(1, width - paddingLeft - paddingRight);
        const plotH = Math.max(1, height - paddingTop - paddingBottom);

        function xScale(cost) {
            return paddingLeft + (Number(cost) / (maxCost || 1)) * plotW;
        }

        function yScale(total) {
            return paddingTop + plotH - (Number(total) / (maxTotal || 1)) * plotH;
        }

        // Fondo
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, width, height);

        // Ejes
        ctx.strokeStyle = '#111827';
        ctx.lineWidth = 1.4;

        ctx.beginPath();
        ctx.moveTo(paddingLeft, paddingTop + plotH);
        ctx.lineTo(paddingLeft + plotW, paddingTop + plotH);
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(paddingLeft, paddingTop + plotH);
        ctx.lineTo(paddingLeft, paddingTop);
        ctx.stroke();

        ctx.font = '11px "Segoe UI", system-ui, -apple-system, sans-serif';
        ctx.fillStyle = '#111827';

        // Etiquetas de ejes
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText('Costo (S/)', paddingLeft + plotW / 2, paddingTop + plotH + 32);

        ctx.save();
        ctx.translate(paddingLeft - 40, paddingTop + plotH / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('Total de ventajas', 0, 0);
        ctx.restore();

        // Rejilla
        const xDivs = 5;
        const yDivs = 5;
        const xStepVal = maxCost / xDivs || 1;
        const yStepVal = maxTotal / yDivs || 1;

        ctx.strokeStyle = '#e5e7eb';
        ctx.lineWidth = 0.7;

        for (let i = 0; i <= xDivs; i++) {
            const v = xStepVal * i;
            const x = xScale(v);

            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            ctx.moveTo(x, paddingTop);
            ctx.lineTo(x, paddingTop + plotH);
            ctx.stroke();
            ctx.setLineDash([]);

            ctx.strokeStyle = '#9ca3af';
            ctx.beginPath();
            ctx.moveTo(x, paddingTop + plotH);
            ctx.lineTo(x, paddingTop + plotH + 4);
            ctx.stroke();
            ctx.strokeStyle = '#e5e7eb';

            ctx.fillStyle = '#4b5563';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            ctx.fillText(v.toFixed(0), x, paddingTop + plotH + 6);
        }

        for (let i = 0; i <= yDivs; i++) {
            const v = yStepVal * i;
            const y = yScale(v);

            ctx.strokeStyle = '#e5e7eb';
            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            ctx.moveTo(paddingLeft, y);
            ctx.lineTo(paddingLeft + plotW, y);
            ctx.stroke();
            ctx.setLineDash([]);

            ctx.strokeStyle = '#9ca3af';
            ctx.beginPath();
            ctx.moveTo(paddingLeft - 4, y);
            ctx.lineTo(paddingLeft, y);
            ctx.stroke();

            ctx.fillStyle = '#4b5563';
            ctx.textAlign = 'right';
            ctx.textBaseline = 'middle';
            ctx.fillText(v.toFixed(0), paddingLeft - 6, y);
        }

        const originX = xScale(0);
        const originY = yScale(0);

        const colors = ['#2563eb', '#16a34a', '#eab308', '#dc2626', '#7c3aed', '#0ea5e9'];

        points.forEach((p, index) => {
            const x = xScale(p.cost);
            const y = yScale(p.total);
            const color = colors[index % colors.length];

            ctx.strokeStyle = color;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(originX, originY);
            ctx.lineTo(x, y);
            ctx.stroke();

            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(x, y, 4, 0, Math.PI * 2);
            ctx.fill();

            ctx.fillStyle = '#111827';
            if (x > width - 70) {
                ctx.textAlign = 'right';
                ctx.textBaseline = 'bottom';
                ctx.fillText(p.name, x - 6, y - 4);
            } else {
                ctx.textAlign = 'left';
                ctx.textBaseline = 'bottom';
                ctx.fillText(p.name, x + 6, y - 4);
            }

            if (legendContainer) {
                const item = document.createElement('div');
                item.style.display = 'flex';
                item.style.alignItems = 'center';
                item.style.marginBottom = '4px';

                const swatch = document.createElement('span');
                swatch.style.display = 'inline-block';
                swatch.style.width = '10px';
                swatch.style.height = '10px';
                swatch.style.borderRadius = '999px';
                swatch.style.marginRight = '6px';
                swatch.style.backgroundColor = color;

                const label = document.createElement('span');
                label.textContent = p.name;

                item.appendChild(swatch);
                item.appendChild(label);
                legendContainer.appendChild(item);
            }
        });
    };

    const ai = SIDEO.ai || (SIDEO.ai = {});

    ai.bindAuditButton = function bindAuditButton(params) {
        const button = (typeof params.buttonId === 'string') ? document.getElementById(params.buttonId) : params.button;
        const output = (typeof params.outputId === 'string') ? document.getElementById(params.outputId) : params.output;
        const status = (typeof params.statusId === 'string') ? document.getElementById(params.statusId) : params.status;
        const url = params.url;

        if (!button || !output || !url) return;

        output.textContent = '';

        button.addEventListener('click', async function () {
            button.disabled = true;
            if (status) {
                status.style.display = 'block';
                status.textContent = params.loadingText || 'Procesando...';
            }
            output.textContent = '';

            try {
                const payload = (typeof params.buildPayload === 'function') ? params.buildPayload() : (params.payload || {});
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': utils.getCookie('csrftoken')
                    },
                    body: JSON.stringify(payload || {})
                });
                const data = await resp.json();
                if (!data.ok) {
                    throw new Error(data.error || 'No se pudo procesar la solicitud.');
                }
                output.innerHTML = utils.renderAIMarkup(data.content || '');
                if (status) status.textContent = 'Listo.';
            } catch (e) {
                output.textContent = '';
                if (status) status.textContent = (e && e.message) ? e.message : 'Error.';
            } finally {
                button.disabled = false;
            }
        });
    };

    utils.syncLayoutHeaderOffset = function syncLayoutHeaderOffset() {
        const root = document.documentElement;
        if (!root) return;

        const header = document.querySelector('.sideo-header');
        if (!header) return;

        const rect = header.getBoundingClientRect();
        const height = Math.max(0, Math.round(rect.height || 0));
        if (!height) return;

        // Mantener un pequeño margen visual para que el sidebar no quede pegado.
        root.style.setProperty('--layout-header-offset', String(height + 8) + 'px');

        const sidebar = document.querySelector('.layout--home .dashboard-sidebar, .layout--panel .dashboard-sidebar, .dashboard-sidebar');
        if (sidebar) {
            const sidebarRect = sidebar.getBoundingClientRect();
            const sidebarTop = Math.max(0, Math.round(sidebarRect.top || 0));

            // Reservar un espacio abajo para evitar cortes por redondeo / barras del navegador.
            const bottomGutter = 16;
            root.style.setProperty('--layout-sidebar-offset', String(sidebarTop + bottomGutter) + 'px');

            const viewportH = Math.max(
                0,
                Math.round(
                    (document.documentElement && document.documentElement.clientHeight) ||
                    (global.innerHeight || 0)
                )
            );

            if (viewportH > 0) {
                const available = Math.max(260, viewportH - sidebarTop - bottomGutter);
                root.style.setProperty('--layout-sidebar-height', String(available) + 'px');
            }
        }
    };

    (function initLayoutHeaderOffsetWatcher() {
        if (typeof document === 'undefined') return;

        const update = (utils.debounce)
            ? utils.debounce(utils.syncLayoutHeaderOffset, 80)
            : utils.syncLayoutHeaderOffset;

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function () {
                update();
            });
        } else {
            update();
        }

        // Asegurar cálculo con layout ya asentado (fuentes, wrap, etc.).
        global.requestAnimationFrame(function () {
            update();
            global.requestAnimationFrame(function () {
                update();
            });
        });

        global.addEventListener('load', update);

        global.addEventListener('resize', update);

        // Cuando el sidebar entra/sale de sticky al hacer scroll, su `top` cambia.
        // Recalcular para que el alto disponible sea correcto (con o sin header visible).
        try {
            global.addEventListener('scroll', update, { passive: true });
        } catch (e) {
            global.addEventListener('scroll', update);
        }

        // Si el header cambia de alto (wrap del texto, usuario largo, etc.), recalcular.
        if (global.ResizeObserver) {
            try {
                const header = document.querySelector('.sideo-header');
                if (!header) return;
                const ro = new global.ResizeObserver(function () {
                    update();
                });
                ro.observe(header);
            } catch (e) {
                // ignore
            }
        }
    })();
})(window);
