const app = document.getElementById('app');

const DEFAULT_MARKDOWN = '# New note\n\nStart typing — click **Save Note** to persist it at this URL.';

const CLS = {
    headerRow: 'flex justify-between items-center gap-3 max-w-[1200px] mx-auto mb-5',
    h2: 'm-0 text-xl sm:text-2xl text-gray-800 truncate min-w-0',
    primaryBtn: 'shrink-0 px-4 sm:px-6 py-2 sm:py-2.5 bg-black text-white text-sm sm:text-base font-semibold rounded-md hover:bg-gray-800 transition-colors',
    list: 'max-w-[1200px] mx-auto list-none p-0 space-y-3',
    item: 'flex flex-col sm:flex-row sm:justify-between sm:items-center gap-1 sm:gap-4 px-4 py-3 sm:px-5 sm:py-4 bg-white rounded-lg shadow-sm',
    itemLink: 'text-gray-800 font-medium hover:text-black hover:underline truncate',
    itemTime: 'text-gray-500 text-xs sm:text-sm shrink-0',
    itemBody: 'flex-1 min-w-0',
    snippet: 'text-gray-600 text-sm mt-1 line-clamp-2',
    notice: 'text-center text-gray-500 py-10 px-5 bg-white rounded-lg shadow-sm',
    error: 'text-center text-red-600 py-10 px-5 bg-white rounded-lg shadow-sm',
    searchInput: 'block w-full max-w-[1200px] mx-auto mb-5 px-4 py-3 text-base bg-white border border-gray-300 rounded-lg shadow-sm focus:outline-none focus:border-gray-500',
    editor: 'max-w-[1200px] mx-auto bg-white rounded-lg shadow-md overflow-hidden',
};

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
}

function formatDate(s) {
    if (!s) return '';
    // SQLite datetime('now') stores UTC as "YYYY-MM-DD HH:MM:SS" without a tz marker.
    return new Date(s.replace(' ', 'T') + 'Z').toLocaleString();
}

function previewFor(note) {
    if (note.title) return note.title;
    const text = (note.preview || '').replace(/^#+\s*/, '').split('\n')[0].trim();
    return text || note.id;
}

async function renderHome() {
    app.innerHTML = `
        <div class="${CLS.headerRow}">
            <h2 class="${CLS.h2}">My Local Vault</h2>
            <button id="new-note-btn" class="${CLS.primaryBtn}">New note</button>
        </div>
        <ul id="recent-notes" class="${CLS.list}"></ul>
    `;

    document.getElementById('new-note-btn').addEventListener('click', () => {
        window.location.href = '/notes/' + crypto.randomUUID();
    });

    const list = document.getElementById('recent-notes');
    try {
        const res = await fetch('/api/v1/notes');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const notes = await res.json();
        if (!notes.length) {
            list.innerHTML = `<li class="${CLS.notice}">No notes yet — tap <strong>New note</strong> to create one.</li>`;
            return;
        }
        list.innerHTML = notes.map(n => `
            <li class="${CLS.item}">
                <a href="/notes/${encodeURIComponent(n.id)}" class="${CLS.itemLink} flex-1 min-w-0">${escapeHtml(previewFor(n))}</a>
                <time class="${CLS.itemTime}">${escapeHtml(formatDate(n.created_at))}</time>
            </li>
        `).join('');
    } catch (err) {
        list.innerHTML = `<li class="${CLS.error}">Failed to load notes: ${escapeHtml(err.message)}</li>`;
    }
}

function renderNote(noteId) {
    app.innerHTML = `
        <div class="${CLS.headerRow}">
            <h2 id="note-title" class="${CLS.h2} flex-1">${escapeHtml(noteId)}</h2>
            <button id="save-btn" class="${CLS.primaryBtn}">Save Note</button>
        </div>
        <div id="editor-container" class="${CLS.editor}"></div>
    `;

    const apiUrl = '/api/v1/notes/' + encodeURIComponent(noteId);
    const editor = new toastui.Editor({
        el: document.querySelector('#editor-container'),
        height: '650px',
        initialEditType: 'markdown',
        previewStyle: window.matchMedia('(min-width: 768px)').matches ? 'vertical' : 'tab',
        initialValue: DEFAULT_MARKDOWN,
        usageStatistics: false
    });

    fetch(apiUrl)
        .then(res => {
            if (res.status === 404) return null;
            if (!res.ok) throw new Error('HTTP ' + res.status);
            return res.text();
        })
        .then(md => { if (md !== null) editor.setMarkdown(md); })
        .catch(() => editor.setMarkdown('# Error\n\nCould not load this note.'));

    const saveBtn = document.getElementById('save-btn');
    saveBtn.addEventListener('click', async () => {
        try {
            const res = await fetch(apiUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: editor.getMarkdown() })
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            saveBtn.textContent = 'Saved';
            setTimeout(() => { saveBtn.textContent = 'Save Note'; }, 1200);
        } catch (err) {
            alert('Save failed: ' + err.message);
        }
    });
}

async function renderSearch() {
    app.innerHTML = `
        <div class="${CLS.headerRow}">
            <h2 class="${CLS.h2}">Search</h2>
        </div>
        <input id="search-input" class="${CLS.searchInput}" type="search" placeholder="Search your notes…" autocomplete="off" autofocus />
        <ul id="search-results" class="${CLS.list}">
            <li class="${CLS.notice}">Type to search your notes.</li>
        </ul>
    `;

    const input = document.getElementById('search-input');
    const results = document.getElementById('search-results');
    let timer = null;
    let currentReqId = 0;

    async function runSearch(q) {
        if (!q.trim()) {
            results.innerHTML = `<li class="${CLS.notice}">Type to search your notes.</li>`;
            return;
        }
        const reqId = ++currentReqId;
        try {
            const res = await fetch('/api/v1/search?q=' + encodeURIComponent(q) + '&limit=10');
            if (reqId !== currentReqId) return; // stale
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const hits = await res.json();
            if (!hits.length) {
                results.innerHTML = `<li class="${CLS.notice}">No notes match "${escapeHtml(q)}".</li>`;
                return;
            }
            results.innerHTML = hits.map(n => `
                <li class="${CLS.item}">
                    <div class="${CLS.itemBody}">
                        <a href="/notes/${encodeURIComponent(n.id)}" class="${CLS.itemLink} block">${escapeHtml(previewFor(n))}</a>
                        <div class="${CLS.snippet}">${escapeHtml(n.preview || '')}</div>
                    </div>
                    <time class="${CLS.itemTime}">${escapeHtml(formatDate(n.created_at))}</time>
                </li>
            `).join('');
        } catch (err) {
            if (reqId !== currentReqId) return;
            results.innerHTML = `<li class="${CLS.error}">Search failed: ${escapeHtml(err.message)}</li>`;
        }
    }

    input.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => runSearch(input.value), 200);
    });
}

function route() {
    if (window.location.pathname.match(/^\/search\/?$/)) return renderSearch();
    const m = window.location.pathname.match(/^\/notes\/([^\/]+)\/?$/);
    if (m) return renderNote(decodeURIComponent(m[1]));
    return renderHome();
}

route();
