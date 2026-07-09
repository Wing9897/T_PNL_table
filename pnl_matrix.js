const MAX_PRICE_RANGE = 300; // 與後端 params.MAX_PRICE_RANGE 對齊

const __init = () => {
    const $ = (id) => document.getElementById(id);
    const infoBar = $('info-bar');
    const tableContainer = $('table-container');
    const tableHead = $('table-head');
    const tableBody = $('table-body');
    const errorContainer = $('error-container');
    const tooltip = $('tooltip');
    const tooltipContent = $('tooltip-content');
    const hint = $('hint');
    const loading = $('loading');
    const showLoading = (b) => loading.classList.toggle('show', b);

    let currentData = null;
    let currentShares = null;
    let currentCommMode = null;
    let currentCommValue = null;
    let useClosingPrice = true;
    let hypotheticalPrice = null;
    let currentPriceCell = null;

    const computeCell = (buy, sell) => {
        let buyCost, sellRev;
        if (currentCommMode === 'fixed') {
            buyCost = buy * currentShares + currentCommValue;
            sellRev = sell * currentShares - currentCommValue;
        } else {
            buyCost = buy * currentShares * (1 + currentCommValue);
            sellRev = sell * currentShares * (1 - currentCommValue);
        }
        const pnl = sellRev - buyCost;
        const pnl_pct = buyCost !== 0 ? (pnl / buyCost) * 100 : 0;
        return { pnl, pnl_pct };
    };

    const reRenderKeepView = () => {
        const sl = tableContainer.scrollLeft, st = tableContainer.scrollTop;
        renderTable(currentData);
        tableContainer.scrollLeft = sl;
        tableContainer.scrollTop = st;
    };

    const debounce = (fn, delay) => {
        let timer = null;
        return (...args) => {
            clearTimeout(timer);
            timer = setTimeout(() => fn(...args), delay);
        };
    };
    const debouncedRerender = debounce(reRenderKeepView, 200);
    const debouncedRegenerate = debounce(() => doRegenerate(), 400);

    const persistClientSettings = () => {
        const api = window.pywebview && window.pywebview.api;
        if (!api || !api.save_client_settings) return;
        const payload = { shares: currentShares, commission_mode: currentCommMode, use_closing_price: useClosingPrice };
        if (currentCommMode === 'fixed') payload.commission_fixed = currentCommValue;
        else payload.commission_pct = currentCommValue;
        if (!useClosingPrice && hypotheticalPrice != null) payload.hypothetical_price = hypotheticalPrice;
        Promise.resolve(api.save_client_settings(payload)).catch(() => {});
    };
    const debouncedPersistClientSettings = debounce(persistClientSettings, 400);

    const computeRowScore = (buy, referencePrice, varPrice, cvarPrice) => {
        const mid = (varPrice + referencePrice) / 2;
        const halfWidth = Math.abs(referencePrice - varPrice) / 2;
        const sigmaDown = Math.max(1e-9, varPrice - cvarPrice);
        const sigmaUp = Math.max(1e-9, referencePrice - varPrice);
        const isHighSide = buy > mid;
        const dist = isHighSide ? (buy - mid) : (mid - buy);
        const sigma = isHighSide ? sigmaUp : sigmaDown;
        const excess = Math.max(0, dist - halfWidth);
        const z = excess / sigma;
        return { score: 100 * Math.exp(-2 * z * z), isHighSide };
    };

    const computeRowRisk = (r) => {
        const d = currentData;
        const buy = d.price_levels[r];
        const lossProb = d.loss_prob ? d.loss_prob[r] : null;
        const varLoss = buy * currentShares * ((d.var_pct || 0) / 100);
        const cvarLoss = buy * currentShares * ((d.cvar_pct || 0) / 100);
        const { score: rawScore, isHighSide } = computeRowScore(buy, d.reference_price, d.var_price, d.cvar_price);
        const score = Math.round(rawScore);
        let emoji, text, cls;
        if (score >= 80) { emoji = '✅'; text = '值博'; cls = 'tag-good'; }
        else if (score >= 45) { emoji = '⚠️'; text = isHighSide ? '偏貴' : '謹慎'; cls = 'tag-caution'; }
        else { emoji = isHighSide ? '⚠️' : '⛔'; text = isHighSide ? '追高' : '不建議'; cls = isHighSide ? 'tag-chase' : 'tag-bad'; }
        return { buy, lossProb, varLoss, cvarLoss, emoji, text, cls, score };
    };

    const ZOOM_MIN = 0.4, ZOOM_MAX = 2.6;
    let zoom = 1;
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

    const updateZoomLabel = () => {
        const el = $('zoom-level');
        if (el) el.textContent = Math.round(zoom * 100) + '%';
    };

    const applyZoom = (newZoom, clientX, clientY) => {
        newZoom = clamp(newZoom, ZOOM_MIN, ZOOM_MAX);
        if (newZoom === zoom) return;
        const rect = tableContainer.getBoundingClientRect();
        const px = (clientX ?? rect.left + rect.width / 2) - rect.left;
        const py = (clientY ?? rect.top + rect.height / 2) - rect.top;
        const ratio = newZoom / zoom;
        tableContainer.scrollLeft = (tableContainer.scrollLeft + px) * ratio - px;
        tableContainer.scrollTop = (tableContainer.scrollTop + py) * ratio - py;
        zoom = newZoom;
        tableContainer.style.setProperty('--zoom', zoom);
        updateZoomLabel();
    };

    tableContainer.addEventListener('wheel', (e) => {
        e.preventDefault();
        applyZoom(zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12), e.clientX, e.clientY);
    }, { passive: false });

    let isPanning = false;
    let startX, startY, scrollLeftStart, scrollTopStart;

    tableContainer.addEventListener('contextmenu', (e) => e.preventDefault());
    tableContainer.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        isPanning = true;
        tableContainer.classList.add('grabbing');
        startX = e.pageX;
        startY = e.pageY;
        scrollLeftStart = tableContainer.scrollLeft;
        scrollTopStart = tableContainer.scrollTop;
        tooltip.style.display = 'none';
        clearCrosshair();
    });

    const stopPanning = () => {
        if (!isPanning) return;
        isPanning = false;
        tableContainer.classList.remove('grabbing');
    };

    window.addEventListener('mouseup', stopPanning);
    window.addEventListener('mousemove', (e) => {
        if (!isPanning) return;
        e.preventDefault();
        tableContainer.scrollLeft = scrollLeftStart - (e.pageX - startX);
        tableContainer.scrollTop = scrollTopStart - (e.pageY - startY);
    });

    window.addEventListener('keydown', (e) => {
        const step = 60;
        if (e.key === 'ArrowLeft') tableContainer.scrollLeft -= step;
        else if (e.key === 'ArrowRight') tableContainer.scrollLeft += step;
        else if (e.key === 'ArrowUp') tableContainer.scrollTop -= step;
        else if (e.key === 'ArrowDown') tableContainer.scrollTop += step;
        else if (e.key === '+' || e.key === '=') applyZoom(zoom * 1.15);
        else if (e.key === '-' || e.key === '_') applyZoom(zoom / 1.15);
        else if (e.key === '0') resetView();
    });

    const fadeHintOnce = () => {
        hint.style.opacity = '0';
        tableContainer.removeEventListener('mousedown', fadeHintOnce);
        tableContainer.removeEventListener('wheel', fadeHintOnce);
    };
    tableContainer.addEventListener('mousedown', fadeHintOnce);
    tableContainer.addEventListener('wheel', fadeHintOnce);

    const centerOnReferencePrice = () => {
        if (!currentPriceCell) return;
        const cRect = tableContainer.getBoundingClientRect();
        const tRect = currentPriceCell.getBoundingClientRect();
        tableContainer.scrollLeft += (tRect.left - cRect.left) - (cRect.width - tRect.width) / 2;
        tableContainer.scrollTop += (tRect.top - cRect.top) - (cRect.height - tRect.height) / 2;
    };

    const centerWhenReady = () => requestAnimationFrame(() => requestAnimationFrame(centerOnReferencePrice));

    const resetView = () => {
        zoom = 1;
        tableContainer.style.setProperty('--zoom', zoom);
        updateZoomLabel();
        centerWhenReady();
    };

    let hlRow = null;
    const hlColCells = [];

    const clearCrosshair = () => {
        if (hlRow) { hlRow.classList.remove('hl-row'); hlRow = null; }
        while (hlColCells.length) hlColCells.pop().classList.remove('hl-col');
    };

    const positionTooltip = (clientX, clientY) => {
        const pad = 15;
        const tw = tooltip.offsetWidth, th = tooltip.offsetHeight;
        let x = clientX + pad, y = clientY + pad;
        if (x + tw > window.innerWidth) x = clientX - tw - pad;
        if (y + th > window.innerHeight) y = clientY - th - pad;
        tooltip.style.left = Math.max(4, x) + 'px';
        tooltip.style.top = Math.max(4, y) + 'px';
    };

    tableBody.addEventListener('mousemove', (e) => {
        if (isPanning || !currentData) return;
        const td = e.target.closest('td');
        if (!td || td.dataset.c === undefined) return;

        const r = +td.dataset.r, c = +td.dataset.c;
        const d = currentData;
        const buy = d.price_levels[r], sell = d.price_levels[c];

        const tr = td.parentElement;
        if (hlRow !== tr) {
            clearCrosshair();
            hlRow = tr; tr.classList.add('hl-row');
            const headTh = tableHead.querySelectorAll('th')[c + 1];
            if (headTh) { headTh.classList.add('hl-col'); hlColCells.push(headTh); }
            tableBody.querySelectorAll(`td[data-c="${c}"]`).forEach(cell => {
                cell.classList.add('hl-col'); hlColCells.push(cell);
            });
        }

        const { pnl, pnl_pct } = computeCell(buy, sell);
        const prob = d.prob_pct[c];
        const rowRiskCache = currentData._rowRiskCache;
        const risk = rowRiskCache ? rowRiskCache[r] : computeRowRisk(r);
        let riskMark = '';
        if (sell <= d.cvar_price) riskMark = '\n⚠ 賣價落在 CVaR 風險區';
        else if (sell <= d.var_price) riskMark = '\n⚠ 賣價落在 VaR 風險區';
        let riskLines = '';
        if (risk.lossProb !== null) {
            riskLines =
                `\n──（買入價 ${buy.toFixed(2)} 風險）──\n` +
                `蝕錢機率: ${risk.lossProb.toFixed(1)}%\n` +
                `VaR 潛在虧損: -$${risk.varLoss.toFixed(2)}\n` +
                `CVaR 潛在虧損: -$${risk.cvarLoss.toFixed(2)}\n` +
                `做T評分: ${risk.score}/100 (${risk.text})`;
        }
        tooltipContent.textContent =
            `買入: ${buy.toFixed(2)}\n` +
            `賣出: ${sell.toFixed(2)}\n` +
            `損益: ${pnl > 0 ? '+' : ''}$${pnl.toFixed(2)} (${pnl_pct.toFixed(2)}%)\n` +
            `信心(達成賣價): ${prob}%${riskMark}${riskLines}`;
        tooltip.style.display = 'block';
        positionTooltip(e.clientX, e.clientY);
    });

    tableContainer.addEventListener('mouseleave', () => {
        tooltip.style.display = 'none';
        clearCrosshair();
    });

    const applyData = (data) => {
        if (data && data.error) throw new Error(data.error);
        currentData = data;
        if (currentShares === null) currentShares = data.shares;
        if (currentCommMode === null) currentCommMode = data.commission_mode || 'percent';
        if (currentCommValue === null) currentCommValue = (data.commission_value ?? data.commission_pct ?? 0.001);
        if (data.use_closing_price !== undefined) useClosingPrice = data.use_closing_price;
        if (data.hypothetical_price != null) hypotheticalPrice = data.hypothetical_price;
        else if (data.closing_price != null) hypotheticalPrice = data.closing_price;
        render(data);
        centerWhenReady();
    };

    const acquireData = async () => {
        const api = window.pywebview && window.pywebview.api;
        if (api && api.get_data) return await api.get_data();
        const response = await fetch(`matrix_data.json?t=${Date.now()}`);
        if (!response.ok) {
            throw new Error(`HTTP 錯誤! 狀態: ${response.status}。桌面版請執行 python app.py；若用瀏覽器則需先跑 pnl_matrix.py 並透過 http 伺服器開啟。`);
        }
        return await response.json();
    };

    const fetchData = async () => {
        try {
            errorContainer.style.display = 'none';
            showLoading(true);
            applyData(await acquireData());
        } catch (error) {
            console.error('Load error:', error);
            errorContainer.innerHTML = `無法載入數據: <br>${error.message}`;
            errorContainer.style.display = 'block';
            tableHead.innerHTML = '';
            tableBody.innerHTML = '';
        } finally {
            showLoading(false);
        }
    };

    const render = (data) => { syncInfoBar(data); renderTable(data); syncSettingsFromData(data); };

    const syncSettingsFromData = (data) => {
        const lv = data.price_levels;
        const mode = data.step_mode || 'dollar';
        $('set-period').value = data.period;
        $('set-range').value = (lv.length - 1) / 2;
        $('set-step-mode').value = mode;
        $('set-interval').value = mode === 'percent'
            ? (data.interval_pct ?? 0.5)
            : (data.interval ?? (lv.length > 1 ? +(lv[1] - lv[0]).toFixed(2) : 1));
        $('set-confidence').value = +((data.confidence ?? 0.95) * 100).toFixed(2);
        $('set-window').value = data.k_line_window_size ?? 5;
        updateStepLabel(mode);
    };

    const updateStepLabel = (mode) => {
        const label = $('set-step-label');
        const input = $('set-interval');
        if (!label || !input) return;
        if (mode === 'percent') {
            label.textContent = '價格間距 %（參考價）';
            input.step = '0.1'; input.min = '0.01';
        } else {
            label.textContent = '價格間距 $';
            input.step = '0.1'; input.min = '0.01';
        }
    };

    const checkOutOfRange = (data) => {
        const levels = data.price_levels;
        const minLevel = levels[0], maxLevel = levels[levels.length - 1];
        const isOOR = (p) => p < minLevel || p > maxLevel;
        return { var: isOOR(data.var_price), cvar: isOOR(data.cvar_price), minLevel, maxLevel };
    };

    const oorHintHtml = (oor, isOor) => isOor
        ? `<span class="oor-warn" title="此風險價位已超出目前矩陣顯示範圍（${oor.minLevel.toFixed(2)} ~ ${oor.maxLevel.toFixed(2)}），所以表格裡看不到對應的風險色塊。點右邊按鈕可自動擴大「價位格數」涵蓋此範圍。">⚠ 超出範圍 <button type="button" class="oor-fix-btn" data-oor-fix="1">擴大範圍</button></span>`
        : '';

    const showCommValue = () => {
        $('comm-value').value = currentCommMode === 'percent'
            ? +(currentCommValue * 100).toFixed(4)
            : +Number(currentCommValue).toFixed(2);
    };

    const updateCommState = () => {
        currentCommMode = $('comm-mode').value;
        const v = parseFloat($('comm-value').value);
        const num = Number.isFinite(v) && v >= 0 ? v : 0;
        currentCommValue = currentCommMode === 'percent' ? num / 100 : num;
    };

    const syncInfoBar = (data) => {
        const closingPrice = data.closing_price ?? data.reference_price;
        $('ticker-input').value = data.ticker;
        $('use-closing-price').checked = useClosingPrice;
        const hypoInput = $('hypothetical-price');
        hypoInput.disabled = useClosingPrice;
        hypoInput.value = (hypotheticalPrice ?? closingPrice).toFixed(2);
        $('closing-price-label').textContent = `收盤 ${closingPrice.toFixed(2)} (${data.date_end})`;
        $('shares-input').value = currentShares;
        $('comm-mode').value = currentCommMode;
        showCommValue();

        const oor = checkOutOfRange(data);
        $('var-label').textContent = `VaR ${data.confidence * 100}% (日)`;
        $('var-value').textContent = `${data.var_price.toFixed(2)} (-${data.var_pct.toFixed(2)}%)`;
        $('var-oor').innerHTML = oorHintHtml(oor, oor.var);
        $('cvar-value').textContent = `${data.cvar_price.toFixed(2)} (-${data.cvar_pct.toFixed(2)}%)`;
        $('cvar-oor').innerHTML = oorHintHtml(oor, oor.cvar);
        $('mdd-value').textContent = `-${(data.max_drawdown_pct ?? 0).toFixed(2)}%`;
        $('mdd-item').title = `期間內最高點 ${data.mdd_peak_price ?? ''}@${data.mdd_peak_date ?? ''} → 最低點 ${data.mdd_trough_price ?? ''}@${data.mdd_trough_date ?? ''}`;
        $('period-value').textContent = `${data.date_start} ~ ${data.date_end} (${data.sample_days}天)`;
    };

    const expandRangeToCoverRisk = (data) => {
        const step = data.step || 1;
        const worstPrice = Math.min(data.var_price, data.cvar_price);
        const neededSteps = Math.ceil(Math.abs(data.reference_price - worstPrice) / step) + 1;
        $('set-range').value = clamp(neededSteps, 2, MAX_PRICE_RANGE);
        $('settings-panel').classList.add('open');
        doRegenerate();
    };

    const bindInfoBarEvents = () => {
        infoBar.addEventListener('click', (e) => {
            if (e.target.id === 'load-btn') doRegenerate();
            if (e.target.closest('[data-oor-fix]') && currentData) expandRangeToCoverRisk(currentData);
        });

        infoBar.addEventListener('keydown', (e) => {
            if (e.target.id === 'ticker-input' && e.key === 'Enter') {
                e.preventDefault();
                doRegenerate();
            }
        });

        infoBar.addEventListener('change', (e) => {
            const id = e.target.id;
            if (id === 'use-closing-price') {
                useClosingPrice = e.target.checked;
                const hypoInput = $('hypothetical-price');
                hypoInput.disabled = useClosingPrice;
                if (!useClosingPrice && currentData) {
                    const cp = currentData.closing_price ?? currentData.reference_price;
                    if (!hypoInput.value || parseFloat(hypoInput.value) <= 0) {
                        hypoInput.value = cp.toFixed(2);
                        hypotheticalPrice = cp;
                    }
                }
                debouncedPersistClientSettings();
                doRegenerate();
            } else if (id === 'comm-mode') {
                updateCommState();
                showCommValue();
                reRenderKeepView();
                debouncedPersistClientSettings();
            }
        });

        infoBar.addEventListener('input', (e) => {
            const id = e.target.id;
            if (id === 'shares-input') {
                const v = parseInt(e.target.value, 10);
                if (!Number.isFinite(v) || v < 1) return;
                currentShares = v;
                debouncedRerender();
                debouncedPersistClientSettings();
            } else if (id === 'hypothetical-price') {
                if (useClosingPrice) return;
                const v = parseFloat(e.target.value);
                if (!Number.isFinite(v) || v <= 0) return;
                hypotheticalPrice = v;
                debouncedRegenerate();
                debouncedPersistClientSettings();
            } else if (id === 'comm-value') {
                updateCommState();
                debouncedRerender();
                debouncedPersistClientSettings();
            }
        });
    };

    const getHeatmapColor = (pnl_pct) => {
        const intensity = Math.min(Math.abs(pnl_pct) / 10, 1);
        const lightness = 12 + intensity * 22;
        if (pnl_pct > 0) return `hsla(var(--positive-bg-base), ${lightness}%, 0.65)`;
        if (pnl_pct < 0) return `hsla(var(--negative-bg-base), ${lightness}%, 0.65)`;
        return 'transparent';
    };

    const findClosestIndex = (arr, target) =>
        arr.reduce((prev, curr, i) => (Math.abs(curr - target) < Math.abs(arr[prev] - target) ? i : prev), 0);

    const renderTable = (data) => {
        clearCrosshair();
        currentPriceCell = null;
        const priceLevels = data.price_levels;
        const n = priceLevels.length;
        const refIndex = findClosestIndex(priceLevels, data.reference_price);
        const rowRiskCache = priceLevels.map((_, i) => computeRowRisk(i));

        let headHtml = '<tr><th class="corner-th"><div class="corner-inner"><span class="corner-sell">賣出價 →</span><span class="corner-buy">↓ 買入價</span></div></th>';
        priceLevels.forEach((price, index) => {
            let cls = (index === refIndex) ? ' class="current-price-col"' : '';
            let style = '';
            if (price <= data.cvar_price) style = ' style="background-color:var(--cvar-bg-color)"';
            else if (price <= data.var_price) style = ' style="background-color:var(--var-bg-color)"';
            headHtml += `<th${cls}${style}>${price.toFixed(2)}</th>`;
        });
        headHtml += '</tr>';
        tableHead.innerHTML = headHtml;

        let bodyHtml = '';
        for (let rowIndex = 0; rowIndex < n; rowIndex++) {
            const rowCls = (rowIndex === refIndex) ? ' class="current-price-row"' : '';
            const risk = rowRiskCache[rowIndex];
            const riskHtml = (risk.lossProb === null) ? '' :
                `<div class="bh-risk" title="隔日收盤仍低於 ${risk.buy.toFixed(2)} 的歷史機率">蝕 ${risk.lossProb.toFixed(1)}%</div>` +
                `<div class="bh-loss" title="VaR 潛在單日虧損 -$${risk.varLoss.toFixed(2)} / CVaR -$${risk.cvarLoss.toFixed(2)}">VaR -$${risk.varLoss.toFixed(2)}</div>` +
                `<div class="bh-tag ${risk.cls}" title="做T評分：${risk.score}/100">${risk.emoji} ${risk.text} ${risk.score}</div>` +
                `<div class="bh-score-track" title="做T評分 ${risk.score}/100"><div class="bh-score-fill ${risk.cls}" style="width:${risk.score}%"></div></div>`;
            bodyHtml += `<tr${rowCls}><th><div class="bh-price">${risk.buy.toFixed(2)}</div>${riskHtml}</th>`;

            for (let colIndex = 0; colIndex < n; colIndex++) {
                const buy = priceLevels[rowIndex];
                const sell = priceLevels[colIndex];
                const { pnl, pnl_pct } = computeCell(buy, sell);
                const pnlColorClass = pnl > 0 ? 'positive' : 'negative';
                let bg, riskCls = '';
                if (sell <= data.cvar_price) { bg = 'var(--cvar-bg-color)'; riskCls = ' risk-cvar'; }
                else if (sell <= data.var_price) { bg = 'var(--var-bg-color)'; riskCls = ' risk-var'; }
                else bg = getHeatmapColor(pnl_pct);
                const diag = (rowIndex === colIndex) ? ' diagonal' : '';
                const cur = (rowIndex === refIndex && colIndex === refIndex) ? ' data-current="1"' : '';
                bodyHtml +=
                    `<td class="cell${diag}${riskCls}" data-r="${rowIndex}" data-c="${colIndex}"${cur} style="background-color:${bg}">` +
                        `<div class="pnl-amount ${pnlColorClass}">${pnl > 0 ? '+' : ''}$${pnl.toFixed(2)}</div>` +
                        `<div class="pnl-pct ${pnlColorClass}">${pnl_pct.toFixed(2)}%</div>` +
                        `<div class="pnl-prob">信心 ${data.prob_pct[colIndex]}%</div>` +
                    `</td>`;
            }
            bodyHtml += '</tr>';
        }
        tableBody.innerHTML = bodyHtml;
        currentPriceCell = tableBody.querySelector('td[data-current="1"]');
        data._rowRiskCache = rowRiskCache;
    };

    const recalcBtn = $('recalc-btn');
    const recalcStatus = $('recalc-status');
    $('set-step-mode').addEventListener('change', (e) => updateStepLabel(e.target.value));

    const setStatus = (cls, msg) => { recalcStatus.className = cls; recalcStatus.textContent = msg; };

    const doRegenerate = async () => {
        const api = window.pywebview && window.pywebview.api;
        if (!api || !api.regenerate) {
            setStatus('err', '此功能需在桌面版執行 (python app.py)');
            $('settings-panel').classList.add('open');
            return;
        }

        const val = (id) => $(id).value;
        const ticker = ($('ticker-input').value.trim() || (currentData ? currentData.ticker : 'TSLA')).toUpperCase();
        const stepMode = val('set-step-mode');
        const stepVal = parseFloat(val('set-interval')) || (stepMode === 'percent' ? 0.5 : 1);
        const priceRange = clamp(parseInt(val('set-range'), 10) || 30, 2, MAX_PRICE_RANGE);
        const confidencePct = clamp(parseFloat(val('set-confidence')) || 95, 50, 99.9);
        const windowDays = clamp(parseInt(val('set-window'), 10) || 5, 2, 60);

        useClosingPrice = $('use-closing-price').checked;
        const params = {
            ticker,
            period: val('set-period'),
            price_range: priceRange,
            step_mode: stepMode,
            commission_mode: currentCommMode,
            shares: currentShares || 5,
            confidence: +(confidencePct / 100).toFixed(4),
            k_line_window_size: windowDays,
            use_closing_price: useClosingPrice,
        };
        if (stepMode === 'percent') params.interval_pct = stepVal;
        else params.interval = stepVal;
        if (currentCommMode === 'fixed') params.commission_fixed = currentCommValue;
        else params.commission_pct = currentCommValue;

        if (!useClosingPrice) {
            const hypoVal = parseFloat($('hypothetical-price').value);
            if (!Number.isFinite(hypoVal) || hypoVal <= 0) {
                setStatus('err', '請輸入有效的假設價（必須大於 0）');
                return;
            }
            hypotheticalPrice = hypoVal;
            params.hypothetical_price = hypoVal;
        }

        recalcBtn.disabled = true;
        setStatus('', `計算中… (${ticker})`);
        showLoading(true);
        try {
            const data = await api.regenerate(params);
            if (data && data.error) {
                setStatus('err', '失敗：' + data.error);
            } else {
                applyData(data);
                const refNote = data.use_closing_price ? '收盤' : '假設';
                setStatus('ok', `已更新 ${data.ticker} @ ${data.reference_price} (${refNote})`);
            }
        } catch (err) {
            setStatus('err', '失敗：' + err.message);
        } finally {
            recalcBtn.disabled = false;
            showLoading(false);
        }
    };

    recalcBtn.addEventListener('click', doRegenerate);
    bindInfoBarEvents();

    $('reload-btn').addEventListener('click', fetchData);
    $('center-btn').addEventListener('click', resetView);
    $('settings-btn').addEventListener('click', () => $('settings-panel').classList.toggle('open'));
    $('zoom-in').addEventListener('click', () => applyZoom(zoom * 1.2));
    $('zoom-out').addEventListener('click', () => applyZoom(zoom / 1.2));
    updateZoomLabel();

    let booted = false;
    const boot = () => { if (booted) return; booted = true; fetchData(); };
    if (window.pywebview && window.pywebview.api) {
        boot();
    } else {
        window.addEventListener('pywebviewready', boot);
        setTimeout(boot, 800);
        window.addEventListener('load', () => setTimeout(boot, 1200));
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', __init);
} else {
    __init();
}
