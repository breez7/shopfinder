(function () {
    let currentSource = null;
    const form = document.getElementById('search-form');
    const input = document.getElementById('q');
    const parsedPanel = document.getElementById('parsed-panel');
    const shopStatus = document.getElementById('shop-status');
    const grid = document.getElementById('result-grid');
    const resultCount = document.getElementById('result-count');

    if (!form) return;

    function resetStatusForSlug(slug, kind, message) {
        let chip = shopStatus.querySelector('[data-shop="' + slug + '"]');
        const wrapper = document.createElement('div');
        wrapper.innerHTML = renderStatus(slug, kind, message);
        const newChip = wrapper.firstElementChild;
        if (chip) {
            chip.replaceWith(newChip);
        } else {
            shopStatus.appendChild(newChip);
        }
    }

    function renderStatus(slug, kind, message) {
        const label = kind === 'shop_started' ? '검색 중…'
            : kind === 'shop_completed' ? '완료'
            : kind === 'shop_failed' ? '실패' : '';
        const suffix = message ? ' (' + escapeHtml(message) + ')' : '';
        return '<span class="shop-status shop-status-' + kind + '" data-shop="' + slug + '">' +
               escapeHtml(slug) + ': ' + label + suffix + '</span>';
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }

    async function refreshParsedPanel(q) {
        const fd = new FormData();
        fd.append('q', q);
        const res = await fetch('/parse', { method: 'POST', body: fd });
        parsedPanel.innerHTML = await res.text();
    }

    function startSearch(q) {
        if (currentSource) currentSource.close();
        grid.innerHTML = '';
        shopStatus.innerHTML = '';
        resultCount.textContent = '0';
        if (!q.trim()) return;

        const url = '/search/stream?q=' + encodeURIComponent(q);
        const es = new EventSource(url);
        currentSource = es;
        let count = 0;

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
        startSearch(q);
    });

    window.addEventListener('beforeunload', () => {
        if (currentSource) currentSource.close();
    });
})();
