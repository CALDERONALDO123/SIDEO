(function (global) {
    'use strict';

    const SIDEO = global.SIDEO || (global.SIDEO = {});
    const home = SIDEO.home || (SIDEO.home = {});

    function toNumber(value) {
        if (value === null || value === undefined || value === '') return null;
        const n = Number(value);
        return Number.isFinite(n) ? n : null;
    }

    function getCost(item) {
        if (!item) return null;
        return toNumber(item.cost ?? item.costo ?? item.COSTO);
    }

    function getTotal(item) {
        if (!item) return null;
        return toNumber(item.total ?? item.ventaja ?? item.VENTAJA);
    }

    function getRatio(item, cost, total) {
        if (!item) return null;
        if (item.ratio !== null && item.ratio !== undefined) return toNumber(item.ratio);
        if (item.RATIO !== null && item.RATIO !== undefined) return toNumber(item.RATIO);
        const c = cost != null ? cost : getCost(item);
        const t = total != null ? total : getTotal(item);
        if (c == null || t == null || t === 0) return null;
        return c / t;
    }

    function isZeroRow(cost, total) {
        return cost === 0 && total === 0;
    }

    function setAboutExpanded(section, nav, toggle, isExpanded) {
        section.hidden = !isExpanded;
        if (nav) nav.setAttribute('aria-expanded', String(isExpanded));
        if (toggle) toggle.setAttribute('aria-expanded', String(isExpanded));
    }

    home.initAboutToggle = function initAboutToggle() {
        const nav = document.getElementById('aboutSideoNav');
        const toggle = document.getElementById('aboutSideoToggle');
        const section = document.getElementById('about-sideo');

        if (!section || (!nav && !toggle)) return;

        if (toggle) {
            toggle.addEventListener('click', function () {
                const willShow = section.hidden;
                setAboutExpanded(section, nav, toggle, willShow);
                if (willShow) {
                    section.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            });
        }

        if (nav) {
            nav.addEventListener('click', function (event) {
                event.preventDefault();
                const willShow = section.hidden;
                setAboutExpanded(section, nav, toggle, willShow);
                if (willShow) {
                    section.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            });
        }

        if (global.location && global.location.hash === '#about-sideo') {
            setAboutExpanded(section, nav, toggle, true);
            global.requestAnimationFrame(function () {
                section.scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        }
    };

    home.parseDashboardPayload = function parseDashboardPayload() {
        const source = document.getElementById('homeDashboardPayload');
        if (!source) return [];

        try {
            const parsed = JSON.parse(source.textContent || '[]');
            if (typeof parsed === 'string') {
                try {
                    const reparsed = JSON.parse(parsed);
                    return Array.isArray(reparsed) ? reparsed : [];
                } catch (error) {
                    return [];
                }
            }
            return Array.isArray(parsed) ? parsed : [];
        } catch (error) {
            return [];
        }
    };

    home.drawCharts = function drawCharts(payload) {
        const ratioCanvas = document.getElementById('homeRatioChart');
        const scatterCanvas = document.getElementById('homeScatterChart');
        if (!ratioCanvas || !scatterCanvas) return;
        if (!global.SIDEO || !SIDEO.charts) return;
        const items = Array.isArray(payload) ? payload : [];

        const hasRatios = items.some(function (item) {
            const cost = getCost(item);
            const total = getTotal(item);
            if (cost == null || total == null) return false;
            if (isZeroRow(cost, total)) return false;
            return getRatio(item, cost, total) != null;
        });

        const hasCostBenefit = items.some(function (item) {
            const cost = getCost(item);
            const total = getTotal(item);
            if (cost == null || total == null) return false;
            return !isZeroRow(cost, total);
        });

        const ratioEmpty = document.querySelector('.dashboard-chart-card__empty[data-chart="ratio"]');
        const scatterEmpty = document.querySelector('.dashboard-chart-card__empty[data-chart="scatter"]');

        if (ratioEmpty) ratioEmpty.hidden = !!hasRatios;
        if (scatterEmpty) scatterEmpty.hidden = !!hasCostBenefit;

        if (hasRatios) {
            SIDEO.charts.renderRatioBarChart(ratioCanvas, items);
        }

        if (hasCostBenefit) {
            SIDEO.charts.renderCostBenefitScatter(scatterCanvas, items, {
                xLabel: 'Costo (S/)',
                yLabel: 'Ventaja total',
                lineAlpha: 0.55,
                lineWidth: 3,
                pointRadius: 5,
            });
        }
    };

    home.initCharts = function initCharts() {
        const payload = home.parseDashboardPayload();
        const draw = function () {
            home.drawCharts(payload);
        };

        draw();

        if (home._resizeHandlerBound) return;

        const onResize = (SIDEO.utils && SIDEO.utils.debounce)
            ? SIDEO.utils.debounce(draw, 150)
            : draw;
        global.addEventListener('resize', onResize);
        home._resizeHandlerBound = true;
    };

    home.init = function init() {
        home.initAboutToggle();
        home.initCharts();

        (function initNotebookLast() {
            const winnerNode = document.getElementById('homeNotebookWinner');
            const textNode = document.getElementById('homeNotebookText');
            if (!winnerNode || !textNode) return;

            let winner = '';
            let text = '';
            try {
                winner = String(global.localStorage.getItem('cba.notebook.last.winner') || '').trim();
                text = String(global.localStorage.getItem('cba.notebook.last.text') || '');
            } catch (e) {
                winner = '';
                text = '';
            }

            winnerNode.textContent = winner || '-';

            const trimmed = String(text || '').trim();
            if (!trimmed) {
                textNode.innerHTML = '<span class="text-muted">Aún no hay texto guardado en la libreta.</span>';
                return;
            }

            if (global.SIDEO && SIDEO.utils && typeof SIDEO.utils.renderAIMarkup === 'function') {
                textNode.innerHTML = SIDEO.utils.renderAIMarkup(trimmed);
            } else {
                textNode.textContent = trimmed;
            }
        })();
    };

    document.addEventListener('DOMContentLoaded', home.init);
})(window);
