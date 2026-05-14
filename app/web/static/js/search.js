(function () {
    let currentSource = null;
    let currentHistoryId = null;
    const form = document.getElementById('search-form');
    const input = document.getElementById('q');
    const parsedPanel = document.getElementById('parsed-panel');
    const shopStatus = document.getElementById('shop-status');
    const grid = document.getElementById('result-grid');
    const resultCount = document.getElementById('result-count');
    const sortMode = document.getElementById('sort-mode');
    const maxPriceFilter = document.getElementById('max-price-filter');
    const shopToggles = document.getElementById('shop-toggles');

    if (!form) return;

    const shopsSeen = new Set();
    const disabledShops = new Set();

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }
    function cssEscape(s) {
        return String(s).replace(/(["\\\[\]])/g, '\\$1');
    }

    function ensureShopToggle(slug) {
        if (shopsSeen.has(slug)) return;
        shopsSeen.add(slug);
        const lbl = document.createElement('label');
        lbl.className = 'shop-toggle';
        lbl.dataset.shop = slug;
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = true;
        cb.addEventListener('change', () => {
            if (cb.checked) disabledShops.delete(slug); else disabledShops.add(slug);
            applyFiltersAndSort();
        });
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(' ' + slug));
        shopToggles.appendChild(lbl);
    }

    function renderStatus(slug, kind, message) {
        const label = kind === 'shop_started' ? '검색 중…'
            : kind === 'shop_completed' ? '완료'
            : kind === 'shop_failed' ? '실패' : '';
        const suffix = message ? ' (' + escapeHtml(message) + ')' : '';
        return '<span class="shop-status shop-status-' + kind + '" data-shop="' + slug + '">' +
               escapeHtml(slug) + ': ' + label + suffix + '</span>';
    }
    function resetStatusForSlug(slug, kind, message) {
        ensureShopToggle(slug);
        let chip = shopStatus.querySelector('[data-shop="' + cssEscape(slug) + '"]');
        const wrapper = document.createElement('div');
        wrapper.innerHTML = renderStatus(slug, kind, message);
        const newChip = wrapper.firstElementChild;
        if (chip) chip.replaceWith(newChip); else shopStatus.appendChild(newChip);
    }

    function clearGroupHeaders() {
        grid.querySelectorAll('.is-group-header').forEach(n => n.remove());
    }

    function applyFiltersAndSort() {
        clearGroupHeaders();
        const cards = Array.from(grid.querySelectorAll('.result-card'));
        const maxPrice = parseInt(maxPriceFilter.value || '0', 10);
        const mode = sortMode.value;

        // Filter visibility
        for (const card of cards) {
            const slug = card.dataset.shop;
            const price = parseInt(card.dataset.price || '0', 10);
            const hideShop = disabledShops.has(slug);
            const hidePrice = maxPrice > 0 && price > 0 && price > maxPrice;
            card.classList.toggle('is-hidden', hideShop || hidePrice);
        }

        // Sort
        function priceKey(card) {
            const p = parseInt(card.dataset.price || '0', 10);
            return p || (mode === 'price_asc' ? Number.MAX_SAFE_INTEGER : -1);
        }
        function scoreKey(card) {
            const s = parseFloat(card.dataset.matchScore || '');
            return Number.isFinite(s) ? s : -1;
        }
        let sortFn;
        if (mode === 'price_asc') sortFn = (a, b) => priceKey(a) - priceKey(b);
        else if (mode === 'price_desc') sortFn = (a, b) => priceKey(b) - priceKey(a);
        else if (mode === 'score_desc') sortFn = (a, b) => scoreKey(b) - scoreKey(a);
        else if (mode === 'shop') sortFn = (a, b) => {
            const cmp = (a.dataset.shop || '').localeCompare(b.dataset.shop || '');
            return cmp !== 0 ? cmp : priceKey(a) - priceKey(b);
        };
        cards.sort(sortFn);

        // Reattach in sorted order. For 'shop' mode, insert group-header rows
        if (mode === 'shop') {
            let lastShop = null;
            for (const card of cards) {
                if (card.dataset.shop !== lastShop) {
                    lastShop = card.dataset.shop;
                    const hdr = document.createElement('div');
                    hdr.className = 'result-card is-group-header';
                    hdr.textContent = lastShop;
                    grid.appendChild(hdr);
                }
                grid.appendChild(card);
            }
        } else {
            for (const card of cards) grid.appendChild(card);
        }
    }

    async function refreshParsedPanel(q) {
        const fd = new FormData();
        fd.append('q', q);
        const res = await fetch('/parse', { method: 'POST', body: fd });
        parsedPanel.innerHTML = await res.text();
        bindParsedFormHandlers();
    }

    function bindParsedFormHandlers() {
        const form = parsedPanel.querySelector('#parsed-form');
        if (!form) return;
        const applyBtn = form.querySelector('#apply-edits');
        const resetBtn = form.querySelector('#reset-edits');
        if (applyBtn) {
            applyBtn.addEventListener('click', () => {
                const params = new URLSearchParams();
                const q = input.value.trim();
                if (q) params.set('q', q);
                params.set('use_edits', '1');
                ['category','color','size','material','material_pct','fit','max_price','free_text'].forEach(name => {
                    const el = form.querySelector('[name="'+name+'"]');
                    if (el && el.value) params.set(name, el.value);
                });
                startSearchRaw(params.toString());
            });
        }
        if (resetBtn) {
            resetBtn.addEventListener('click', () => {
                let original = {};
                try { original = JSON.parse(form.dataset.original || '{}'); } catch (_) {}
                ['category','color','size','material','material_pct','fit','max_price','free_text'].forEach(name => {
                    const el = form.querySelector('[name="'+name+'"]');
                    if (el) el.value = original[name] == null ? '' : original[name];
                });
            });
        }
    }

    function startSearchRaw(qs) {
        if (currentSource) currentSource.close();
        grid.innerHTML = '';
        shopStatus.innerHTML = '';
        shopToggles.innerHTML = '';
        shopsSeen.clear();
        disabledShops.clear();
        resultCount.textContent = '0';
        const es = new EventSource('/search/stream?' + qs);
        currentSource = es;
        currentHistoryId = null;
        let count = 0;
        es.addEventListener('meta', e => {
            try { currentHistoryId = JSON.parse(e.data).history_id; } catch (_) {}
        });
        es.addEventListener('shop_started', e => { const d = JSON.parse(e.data); resetStatusForSlug(d.slug, 'shop_started'); });
        es.addEventListener('shop_completed', e => { const d = JSON.parse(e.data); resetStatusForSlug(d.slug, 'shop_completed'); });
        es.addEventListener('shop_failed', e => { const d = JSON.parse(e.data); resetStatusForSlug(d.slug, 'shop_failed', d.message); });
        es.addEventListener('result', e => {
            grid.insertAdjacentHTML('beforeend', e.data);
            count += 1;
            resultCount.textContent = String(count);
            const lastCard = grid.lastElementChild;
            if (lastCard && lastCard.dataset && lastCard.dataset.shop) ensureShopToggle(lastCard.dataset.shop);
            applyFiltersAndSort();
        });
        es.addEventListener('score_update', e => {
            try {
                const d = JSON.parse(e.data);
                if (!d.product_url) return;
                const card = grid.querySelector('[data-product-url="' + cssEscape(d.product_url) + '"]');
                if (!card) return;
                if (typeof d.score === 'number') card.dataset.matchScore = String(d.score);
                let reasonNode = card.querySelector('.matched-reason');
                if (!reasonNode) {
                    reasonNode = document.createElement('div');
                    reasonNode.className = 'matched-reason';
                    card.querySelector('.result-body').appendChild(reasonNode);
                }
                if (d.reason) reasonNode.textContent = d.reason;
                applyFiltersAndSort();
            } catch (_) {}
        });
        es.addEventListener('done', () => { es.close(); currentSource = null; });
        es.onerror = () => { es.close(); currentSource = null; };
    }

    function startSearch(q, forceRefresh) {
        if (currentSource) currentSource.close();
        grid.innerHTML = '';
        shopStatus.innerHTML = '';
        shopToggles.innerHTML = '';
        shopsSeen.clear();
        disabledShops.clear();
        resultCount.textContent = '0';
        if (!q.trim()) return;

        let url = '/search/stream?q=' + encodeURIComponent(q);
        if (forceRefresh) url += '&refresh=1';
        const es = new EventSource(url);
        currentSource = es;
        currentHistoryId = null;
        let count = 0;

        es.addEventListener('meta', e => {
            try {
                const data = JSON.parse(e.data);
                currentHistoryId = data.history_id;
            } catch (_) { /* ignore */ }
        });
        es.addEventListener('shop_started', e => {
            const data = JSON.parse(e.data);
            resetStatusForSlug(data.slug, 'shop_started');
        });
        es.addEventListener('shop_completed', e => {
            const data = JSON.parse(e.data);
            resetStatusForSlug(data.slug, 'shop_completed');
        });
        es.addEventListener('shop_failed', e => {
            const data = JSON.parse(e.data);
            resetStatusForSlug(data.slug, 'shop_failed', data.message);
        });
        es.addEventListener('result', e => {
            grid.insertAdjacentHTML('beforeend', e.data);
            count += 1;
            resultCount.textContent = String(count);
            const lastCard = grid.lastElementChild;
            if (lastCard && lastCard.dataset && lastCard.dataset.shop) {
                ensureShopToggle(lastCard.dataset.shop);
            }
            applyFiltersAndSort();
        });
        es.addEventListener('score_update', e => {
            try {
                const d = JSON.parse(e.data);
                if (!d.product_url) return;
                const card = grid.querySelector('[data-product-url="' + cssEscape(d.product_url) + '"]');
                if (!card) return;
                if (typeof d.score === 'number') {
                    card.dataset.matchScore = String(d.score);
                }
                let reasonNode = card.querySelector('.matched-reason');
                if (!reasonNode) {
                    reasonNode = document.createElement('div');
                    reasonNode.className = 'matched-reason';
                    card.querySelector('.result-body').appendChild(reasonNode);
                }
                if (d.reason) reasonNode.textContent = d.reason;
                applyFiltersAndSort();
            } catch (_) { /* ignore */ }
        });
        es.addEventListener('done', () => {
            es.close();
            currentSource = null;
        });
        es.onerror = () => {
            es.close();
            currentSource = null;
        };
    }

    form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        const q = input.value.trim();
        refreshParsedPanel(q);
        startSearch(q, false);
    });

    const refreshBtn = document.getElementById('force-refresh');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', function () {
            const q = input.value.trim();
            refreshParsedPanel(q);
            startSearch(q, true);
        });
    }

    sortMode.addEventListener('change', applyFiltersAndSort);
    maxPriceFilter.addEventListener('input', applyFiltersAndSort);

    // Delegate click logging on result cards
    const _clickDebounce = new Map();
    grid.addEventListener('click', ev => {
        const anchor = ev.target.closest('a.result-title');
        if (!anchor) return;
        if (!currentHistoryId) return;
        const card = anchor.closest('.result-card');
        const slug = card ? card.getAttribute('data-shop') : '';
        const url = anchor.getAttribute('href') || '';
        const key = slug + '|' + url;
        const now = Date.now();
        if (_clickDebounce.has(key) && now - _clickDebounce.get(key) < 1000) return;
        _clickDebounce.set(key, now);
        const fd = new FormData();
        fd.append('history_id', String(currentHistoryId));
        fd.append('shop_slug', slug);
        fd.append('product_url', url);
        navigator.sendBeacon ? navigator.sendBeacon('/click', fd) : fetch('/click', { method: 'POST', body: fd, keepalive: true });
    });

    if (input.value.trim()) {
        setTimeout(() => form.dispatchEvent(new Event('submit')), 50);
    }

    window.addEventListener('beforeunload', () => {
        if (currentSource) currentSource.close();
    });
})();
