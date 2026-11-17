/* Local APRS sprites by OH7LZB; provenance and per-symbol terms: SOURCES.txt / COPYRIGHT.md. */
(function () {
    'use strict';
    const base = '/traccar-symbols/';
    const aliases = {
        '/>':'汽车', '\\>':'汽车（备用）', '/j':'吉普车', '/k':'卡车', '\\k':'SUV 越野车',
        '/u':'半挂卡车', '/v':'面包车', '/U':'公交车', '/R':'房车', '/b':'自行车', '/<':'摩托车',
        '/[':'行人', '/-':'住宅', '\\-':'住宅与天线', '/y':'住宅与八木天线', '/s':'船舶', '/Y':'帆船',
        '/X':'直升机', '/^':'飞机', "/'":'小型飞机', '/O':'热气球', '/a':'救护车', '/f':'消防车',
        '/P':'警车', '/!':'警察局', '/h':'医院', '/d':'消防站', '/K':'学校', '/H':'酒店',
        '/#':'数字中继', '\\#':'数字中继（覆盖）', '/r':'中继塔', '/&':'HF 网关', '\\&':'网关',
        '/_':'气象站', '\\_':'气象站（覆盖）', '/;':'露营地', '\\9':'加油站', '\\P':'停车场',
        '\\R':'餐厅', '\\h':'商店', '/=':'火车', '/e':'骑马', '/p':'狗', '/z':'避难所',
        '/$':'电话', '/+':'红十字', '/l':'笔记本电脑', '/L':'电脑', '/I':'网络台站',
        '\\!':'紧急事件', '\\S':'卫星', '\\U':'晴天', '\\(':'多云', '\\*':'降雪'
    };
    function validTable(value) { return value === '/' || value === '\\' || /^[0-9A-Z]$/.test(value); }
    function position(code) {
        const index = code.charCodeAt(0) - 33;
        return `${-(index % 16) * 48}px ${-Math.floor(index / 16) * 48}px`;
    }
    function icon(table, code) {
        const node = document.createElement('span');
        node.className = 'aprs-icon';
        node.style.backgroundImage = `url("${base}aprs-symbols-24-${table === '/' ? 0 : 1}@2x.png")`;
        node.style.backgroundPosition = position(code);
        node.setAttribute('aria-hidden', 'true');
        if (table !== '/' && table !== '\\') {
            const overlay = document.createElement('span');
            overlay.className = 'aprs-icon-overlay';
            overlay.style.backgroundImage = `url("${base}aprs-symbols-24-2@2x.png")`;
            overlay.style.backgroundPosition = position(table);
            node.appendChild(overlay);
        }
        return node;
    }
    async function init() {
        const root = document.getElementById('aprs-symbol-picker');
        if (!root) return;
        const tableInput = document.getElementById('aprs_symbol_table');
        const codeInput = document.getElementById('aprs_symbol');
        const choice = document.getElementById('aprs-symbol-table-choice');
        const search = document.getElementById('aprs-symbol-search');
        const grid = document.getElementById('aprs-symbol-grid');
        const current = document.getElementById('aprs-symbol-current');
        const preview = document.getElementById('aprs-symbol-preview');
        const count = document.getElementById('aprs-symbol-count');
        if (!tableInput || !codeInput) return;
        const option = (value, text, parent) => {
            const node = document.createElement('option'); node.value = value; node.textContent = text; parent.appendChild(node);
        };
        option('/', '普通图标表 /', choice); option('\\', '备用图标表 \\', choice);
        const overlays = document.createElement('optgroup'); overlays.label = '备用图标 + 覆盖字符';
        for (const value of '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ') option(value, `覆盖字符 ${value}`, overlays);
        choice.appendChild(overlays);
        let descriptions;
        try {
            const response = await fetch(base + 'symbols.json?v=0.2.1', {cache:'force-cache'});
            if (!response.ok) throw new Error('Index unavailable');
            descriptions = await response.json();
            await Promise.all([0, 1, 2].map(table => new Promise((resolve, reject) => {
                const img = new Image(); img.onload = resolve; img.onerror = reject;
                img.src = base + `aprs-symbols-24-${table}@2x.png`;
            })));
        } catch (e) {
            current.textContent = '图标资源加载失败，请刷新页面重试；仍可手动填写编码。';
            choice.disabled = search.disabled = true;
            return;
        }
        function description(table, code) {
            const key = (table === '/' ? '/' : '\\') + code;
            return aliases[key] ? aliases[key] + ' · ' + descriptions[key] : descriptions[key];
        }
        function choose(table, code) {
            tableInput.value = table; codeInput.value = code;
            for (const input of [tableInput, codeInput]) {
                input.dispatchEvent(new Event('input', {bubbles:true}));
                input.dispatchEvent(new Event('change', {bubbles:true}));
            }
        }
        function renderGrid() {
            const table = choice.value, query = search.value.trim().toLowerCase();
            const fragment = document.createDocumentFragment();
            let total = 0;
            for (let ascii = 33; ascii <= 126; ascii++) {
                const code = String.fromCharCode(ascii), label = description(table, code);
                if (!label || !(label + ' ' + table + code).toLowerCase().includes(query)) continue;
                const button = document.createElement('button');
                button.type = 'button'; button.className = 'aprs-symbol-option';
                button.dataset.table = table; button.dataset.code = code;
                button.setAttribute('aria-label', `${label}，编码 ${table}${code}`);
                button.setAttribute('aria-pressed', String(tableInput.value === table && codeInput.value === code));
                button.appendChild(icon(table, code));
                const encoding = document.createElement('code'); encoding.textContent = table + code; button.appendChild(encoding);
                const name = document.createElement('small'); name.textContent = label; button.appendChild(name);
                button.addEventListener('click', () => {
                    choose(table, code);
                    // Rendering replaces tiles; restore focus for keyboard users.
                    const selected = Array.from(grid.children).find(x => x.dataset.code === code);
                    if (selected) selected.focus({preventScroll:true});
                });
                fragment.appendChild(button); total++;
            }
            grid.replaceChildren(fragment);
            count.textContent = total ? `${total} 个可选图标 · 点击选择` : '没有匹配图标，请更换关键词。';
        }
        function sync() {
            const table = tableInput.value, code = codeInput.value;
            preview.replaceChildren();
            if (!validTable(table) || !/^[!-~]$/.test(code)) {
                current.textContent = '请输入有效的单字符图标表和图标代码，或从预览表选择。';
                renderGrid(); return;
            }
            choice.value = table;
            const label = description(table, code);
            if (label) preview.appendChild(icon(table, code));
            current.textContent = `${table}${code} · ${label || '未定义图标，暂无预览'}` +
                (table !== '/' && table !== '\\' ? ` · 覆盖 ${table}` : '');
            renderGrid();
        }
        tableInput.addEventListener('input', sync); codeInput.addEventListener('input', sync);
        tableInput.addEventListener('change', sync); codeInput.addEventListener('change', sync);
        choice.addEventListener('change', () => choose(choice.value, /^[!-~]$/.test(codeInput.value) ? codeInput.value : '>'));
        search.addEventListener('input', renderGrid);
        // Enter in the search box should not save/restart the service.
        search.addEventListener('keydown', event => { if (event.key === 'Enter') event.preventDefault(); });
        root.dataset.ready = 'true'; sync();
    }
    events.push(init);
})();
