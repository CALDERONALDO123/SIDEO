(function (global) {
    'use strict';

    const SIDEO = global.SIDEO || (global.SIDEO = {});
    const home = SIDEO.home || (SIDEO.home = {});

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
            return item && item.ratio !== null && item.ratio !== undefined;
        });

        const hasCostBenefit = items.some(function (item) {
            return item && item.cost !== null && item.cost !== undefined && item.total !== null && item.total !== undefined;
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
    };

    document.addEventListener('DOMContentLoaded', home.init);
})(window);
