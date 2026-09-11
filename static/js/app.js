const app = document.getElementById('app');

const DEFAULT_MARKDOWN = '# New note\n\nStart typing — click **Save Note** to persist it at this URL.';

const CLS = {
    headerRow: 'flex justify-between items-center gap-3 max-w-[1200px] mx-auto mb-5',
    h2: 'm-0 text-xl sm:text-2xl text-gray-800 truncate min-w-0',
    primaryBtn: 'shrink-0 px-4 sm:px-6 py-2 sm:py-2.5 bg-black text-white text-sm sm:text-base font-semibold rounded-md hover:bg-gray-800 transition-colors',
    dangerBtn: 'shrink-0 px-4 sm:px-6 py-2 sm:py-2.5 bg-white text-red-600 text-sm sm:text-base font-semibold rounded-md border border-red-200 hover:bg-red-50 transition-colors',
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
    pathBadge: 'text-gray-400 text-xs font-mono truncate',
    pathRow: 'max-w-[1200px] mx-auto mb-4 flex items-center gap-2',
    pathLabel: 'text-gray-500 text-sm shrink-0',
    pathInput: 'flex-1 min-w-0 px-3 py-1.5 text-sm font-mono bg-white border border-gray-300 rounded-md focus:outline-none focus:border-gray-500',
    viewToggle: 'max-w-[1200px] mx-auto mb-4 flex gap-2',
    toggleBtn: 'px-3 py-1.5 text-sm font-medium rounded-md border transition-colors',
    toggleBtnActive: 'bg-black text-white border-black',
    toggleBtnInactive: 'bg-white text-gray-600 border-gray-300 hover:bg-gray-100',
    groupHeading: 'max-w-[1200px] mx-auto mt-6 mb-2 px-1 text-xs font-semibold text-gray-500 uppercase tracking-wide font-mono first:mt-0',
    pagination: 'max-w-[1200px] mx-auto mt-5 flex justify-between items-center gap-3',
    pageInfo: 'text-gray-500 text-sm',
    pageBtn: 'px-3 py-1.5 text-sm font-medium rounded-md border bg-white text-gray-700 border-gray-300 hover:bg-gray-100 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-white',
    settingsCard: 'max-w-[1200px] mx-auto bg-white rounded-lg shadow-sm p-5 sm:p-6',
    fieldLabel: 'block text-sm font-medium text-gray-700 mb-1',
    fieldInput: 'block w-full px-3 py-2 text-sm font-mono bg-white border border-gray-300 rounded-md focus:outline-none focus:border-gray-500',
    fieldHelp: 'text-gray-500 text-xs mt-1',
};

const PAGE_SIZE = 10;

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

function noteItemHtml(n) {
    return `
        <div class="${CLS.item}">
            <div class="${CLS.itemBody}">
                <a href="/notes/${encodeURIComponent(n.id)}" class="${CLS.itemLink} block">${escapeHtml(previewFor(n))}</a>
                <div class="${CLS.pathBadge}">${escapeHtml(n.path || '/')} · ${escapeHtml(n.id)}</div>
            </div>
            <time class="${CLS.itemTime}">${escapeHtml(formatDate(n.created_at))}</time>
        </div>
    `;
}

function groupByPath(notes) {
    const groups = new Map();
    for (const n of notes) {
        const key = n.path || '/';
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(n);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

function renderNoteList(list, notes, mode) {
    if (!notes.length) {
        list.innerHTML = `<div class="${CLS.notice}">No notes yet — tap <strong>New note</strong> to create one.</div>`;
        return;
    }
    if (mode === 'path') {
        list.innerHTML = groupByPath(notes).map(([path, group]) => `
            <h3 class="${CLS.groupHeading}">${escapeHtml(path)}</h3>
            ${group.map(noteItemHtml).join('')}
        `).join('');
        return;
    }
    list.innerHTML = notes.map(noteItemHtml).join('');
}

async function renderHome() {
    app.innerHTML = `
        <div class="${CLS.headerRow}">
            <h2 class="${CLS.h2}">My Local Vault</h2>
            <button id="new-note-btn" class="${CLS.primaryBtn}">New note</button>
        </div>
        <div class="${CLS.viewToggle}">
            <button id="view-title-btn" class="${CLS.toggleBtn} ${CLS.toggleBtnActive}">By Title</button>
            <button id="view-path-btn" class="${CLS.toggleBtn} ${CLS.toggleBtnInactive}">By Path</button>
        </div>
        <div id="recent-notes" class="${CLS.list}"></div>
        <div class="${CLS.pagination}">
            <button id="prev-page-btn" class="${CLS.pageBtn}">Prev</button>
            <span id="page-info" class="${CLS.pageInfo}"></span>
            <button id="next-page-btn" class="${CLS.pageBtn}">Next</button>
        </div>
    `;

    document.getElementById('new-note-btn').addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        try {
            const res = await fetch('/api/v1/notes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content: DEFAULT_MARKDOWN, path: '/' })
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const { id } = await res.json();
            window.location.href = '/notes/' + encodeURIComponent(id);
        } catch (err) {
            btn.disabled = false;
            alert('Could not create note: ' + err.message);
        }
    });

    const list = document.getElementById('recent-notes');
    const titleBtn = document.getElementById('view-title-btn');
    const pathBtn = document.getElementById('view-path-btn');
    const prevBtn = document.getElementById('prev-page-btn');
    const nextBtn = document.getElementById('next-page-btn');
    const pageInfo = document.getElementById('page-info');
    let mode = 'title';
    let notes = [];
    let offset = 0;
    let total = 0;
    let currentReqId = 0;
    // "By Path" groups across every note, not just one page — fetched in full and
    // cached here, separately from the paginated "By Title" state above. Without
    // this, two notes sharing a path could land on different 10-note pages and
    // never appear grouped together.
    let allNotes = null;
    let allNotesReqId = 0;

    async function fetchAllNotes() {
        const fetched = [];
        let fetchOffset = 0;
        for (;;) {
            const res = await fetch(`/api/v1/notes?limit=100&offset=${fetchOffset}`);
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            fetched.push(...data.items);
            if (data.items.length === 0 || fetched.length >= data.total) break;
            fetchOffset += data.items.length;
        }
        return fetched;
    }

    async function showAllByPath() {
        if (allNotes !== null) {
            // Already have the full set from a prior "By Path" visit this session.
            renderNoteList(list, allNotes, 'path');
            pageInfo.textContent = allNotes.length === 0
                ? 'No notes'
                : `${allNotes.length} note${allNotes.length === 1 ? '' : 's'}, grouped by path`;
            return;
        }
        const reqId = ++allNotesReqId;
        list.innerHTML = `<div class="${CLS.notice}">Loading…</div>`;
        pageInfo.textContent = 'Loading…';
        prevBtn.disabled = true;
        nextBtn.disabled = true;
        try {
            const fetched = await fetchAllNotes();
            if (reqId !== allNotesReqId) return; // stale — user switched away
            allNotes = fetched;
            renderNoteList(list, allNotes, 'path');
            pageInfo.textContent = allNotes.length === 0
                ? 'No notes'
                : `${allNotes.length} note${allNotes.length === 1 ? '' : 's'}, grouped by path`;
        } catch (err) {
            if (reqId !== allNotesReqId) return; // stale
            list.innerHTML = `<div class="${CLS.error}">Failed to load notes: ${escapeHtml(err.message)}</div>`;
            pageInfo.textContent = '';
        }
    }

    function setMode(next) {
        mode = next;
        titleBtn.className = `${CLS.toggleBtn} ${mode === 'title' ? CLS.toggleBtnActive : CLS.toggleBtnInactive}`;
        pathBtn.className = `${CLS.toggleBtn} ${mode === 'path' ? CLS.toggleBtnActive : CLS.toggleBtnInactive}`;
        if (mode === 'path') {
            ++currentReqId; // invalidate any in-flight paginated request
            // Not paginated — the whole point is to show every note's path at once.
            prevBtn.disabled = true;
            nextBtn.disabled = true;
            showAllByPath();
            return;
        }
        ++allNotesReqId; // cancel any in-flight full fetch, it's no longer relevant
        renderNoteList(list, notes, mode);
        const shownFrom = total === 0 ? 0 : offset + 1;
        const shownTo = offset + notes.length;
        pageInfo.textContent = total === 0 ? 'No notes' : `${shownFrom}–${shownTo} of ${total}`;
        prevBtn.disabled = offset === 0;
        nextBtn.disabled = shownTo >= total;
    }

    async function loadPage(nextOffset) {
        const reqId = ++currentReqId;
        try {
            const res = await fetch(`/api/v1/notes?limit=${PAGE_SIZE}&offset=${nextOffset}`);
            if (reqId !== currentReqId || mode !== 'title') return; // stale or path mode — a newer page request won the race
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            if (reqId !== currentReqId || mode !== 'title') return; // stale or path mode

            // Requested page is past the last valid one (e.g. notes were
            // deleted elsewhere since the last load) — clamp back instead of
            // rendering an impossible "11-10 of 10" range.
            if (data.items.length === 0 && data.total > 0 && data.offset > 0) {
                const lastPageOffset = Math.floor((data.total - 1) / PAGE_SIZE) * PAGE_SIZE;
                if (lastPageOffset !== data.offset) return loadPage(lastPageOffset);
            }

            notes = data.items;
            offset = data.offset;
            total = data.total;
            allNotes = null; // the full-set cache used by "By Path" is now stale
            renderNoteList(list, notes, mode);

            const shownFrom = total === 0 ? 0 : offset + 1;
            const shownTo = offset + notes.length;
            pageInfo.textContent = total === 0 ? 'No notes' : `${shownFrom}–${shownTo} of ${total}`;
            prevBtn.disabled = offset === 0;
            nextBtn.disabled = shownTo >= total;
        } catch (err) {
            if (reqId !== currentReqId || mode !== 'title') return; // stale or path mode
            list.innerHTML = `<div class="${CLS.error}">Failed to load notes: ${escapeHtml(err.message)}</div>`;
            pageInfo.textContent = '';
            prevBtn.disabled = true;
            nextBtn.disabled = true;
        }
    }

    titleBtn.addEventListener('click', () => setMode('title'));
    pathBtn.addEventListener('click', () => setMode('path'));
    prevBtn.addEventListener('click', () => loadPage(Math.max(0, offset - PAGE_SIZE)));
    nextBtn.addEventListener('click', () => loadPage(offset + PAGE_SIZE));

    await loadPage(0);
}

async function renderNote(noteId) {
    const apiUrl = '/api/v1/notes/' + encodeURIComponent(noteId);

    // Notes are only ever created server-side (via the "New note" button's
    // POST), so a 404 here means this id doesn't exist — show that instead
    // of a silently-editable blank note whose Save would just 404 too.
    let initialContent;
    try {
        const res = await fetch(apiUrl);
        if (res.status === 404) {
            app.innerHTML = `
                <div class="${CLS.headerRow}">
                    <h2 class="${CLS.h2} flex-1">${escapeHtml(noteId)}</h2>
                </div>
                <div class="${CLS.error}">This note doesn't exist. <a href="/">Go home</a> to create a new one.</div>
            `;
            return;
        }
        if (!res.ok) throw new Error('HTTP ' + res.status);
        initialContent = await res.text();
    } catch (err) {
        app.innerHTML = `<div class="${CLS.error}">Failed to load this note: ${escapeHtml(err.message)}</div>`;
        return;
    }

    app.innerHTML = `
        <div class="${CLS.headerRow}">
            <h2 id="note-title" class="${CLS.h2} flex-1">${escapeHtml(noteId)}</h2>
            <button id="delete-btn" class="${CLS.dangerBtn}">Delete</button>
            <button id="save-btn" class="${CLS.primaryBtn}">Save Note</button>
        </div>
        <div class="${CLS.pathRow}">
            <label for="path-input" class="${CLS.pathLabel}">Path</label>
            <input id="path-input" class="${CLS.pathInput}" type="text" value="/" placeholder="/" autocomplete="off" />
        </div>
        <div id="editor-container" class="${CLS.editor}"></div>
    `;

    const pathInput = document.getElementById('path-input');
    const editor = new toastui.Editor({
        el: document.querySelector('#editor-container'),
        height: '650px',
        initialEditType: 'markdown',
        previewStyle: window.matchMedia('(min-width: 768px)').matches ? 'vertical' : 'tab',
        initialValue: initialContent,
        usageStatistics: false
    });

    fetch(apiUrl + '/meta')
        .then(res => (res.ok ? res.json() : null))
        .then(meta => { if (meta) pathInput.value = meta.path || '/'; })
        .catch(() => {});

    const saveBtn = document.getElementById('save-btn');
    const deleteBtn = document.getElementById('delete-btn');

    saveBtn.addEventListener('click', async () => {
        saveBtn.disabled = true;
        deleteBtn.disabled = true;
        try {
            const res = await fetch(apiUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    content: editor.getMarkdown(),
                    path: pathInput.value.trim() || '/'
                })
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            saveBtn.textContent = 'Saved';
            setTimeout(() => { saveBtn.textContent = 'Save Note'; }, 1200);
        } catch (err) {
            alert('Save failed: ' + err.message);
        } finally {
            saveBtn.disabled = false;
            deleteBtn.disabled = false;
        }
    });

    deleteBtn.addEventListener('click', async () => {
        if (!confirm('Delete this note? This cannot be undone.')) return;
        saveBtn.disabled = true;
        deleteBtn.disabled = true;
        try {
            const res = await fetch(apiUrl, { method: 'DELETE' });
            if (!res.ok && res.status !== 404) throw new Error('HTTP ' + res.status);
            window.location.href = '/';
        } catch (err) {
            saveBtn.disabled = false;
            deleteBtn.disabled = false;
            alert('Delete failed: ' + err.message);
        }
    });
}

async function renderSearch() {
    app.innerHTML = `
        <div class="${CLS.headerRow}">
            <h2 class="${CLS.h2}">Search</h2>
        </div>
        <div class="${CLS.viewToggle}">
            <button id="mode-keyword-btn" class="${CLS.toggleBtn} ${CLS.toggleBtnActive}">Keyword</button>
            <button id="mode-semantic-btn" class="${CLS.toggleBtn} ${CLS.toggleBtnInactive}">Semantic</button>
        </div>
        <input id="search-input" class="${CLS.searchInput}" type="search" placeholder="Search your notes…" autocomplete="off" autofocus />
        <ul id="search-results" class="${CLS.list}">
            <li class="${CLS.notice}">Type to search your notes.</li>
        </ul>
    `;

    const input = document.getElementById('search-input');
    const results = document.getElementById('search-results');
    const keywordBtn = document.getElementById('mode-keyword-btn');
    const semanticBtn = document.getElementById('mode-semantic-btn');
    let timer = null;
    let currentReqId = 0;
    let mode = 'keyword';

    const ENDPOINTS = {
        keyword: '/api/v1/search',
        semantic: '/api/v1/search/semantic',
    };

    async function runSearch(q) {
        if (!q.trim()) {
            results.innerHTML = `<li class="${CLS.notice}">Type to search your notes.</li>`;
            return;
        }
        const reqId = ++currentReqId;
        try {
            const res = await fetch(ENDPOINTS[mode] + '?q=' + encodeURIComponent(q) + '&limit=10');
            if (reqId !== currentReqId || mode !== 'title') return; // stale or path mode
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const hits = await res.json();
            if (reqId !== currentReqId || mode !== 'title') return; // stale or path mode
            if (!hits.length) {
                results.innerHTML = `<li class="${CLS.notice}">No notes match "${escapeHtml(q)}".</li>`;
                return;
            }
            results.innerHTML = hits.map(n => `
                <li class="${CLS.item}">
                    <div class="${CLS.itemBody}">
                        <a href="/notes/${encodeURIComponent(n.id)}" class="${CLS.itemLink} block">${escapeHtml(previewFor(n))}</a>
                        <div class="${CLS.pathBadge}">${escapeHtml(n.path || '/')}</div>
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

    function setMode(next) {
        if (mode === next) return;
        mode = next;
        keywordBtn.className = `${CLS.toggleBtn} ${mode === 'keyword' ? CLS.toggleBtnActive : CLS.toggleBtnInactive}`;
        semanticBtn.className = `${CLS.toggleBtn} ${mode === 'semantic' ? CLS.toggleBtnActive : CLS.toggleBtnInactive}`;
        runSearch(input.value);
    }

    keywordBtn.addEventListener('click', () => setMode('keyword'));
    semanticBtn.addEventListener('click', () => setMode('semantic'));
    input.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => runSearch(input.value), 200);
    });
}

async function renderSettings() {
    app.innerHTML = `
        <div class="${CLS.headerRow}">
            <h2 class="${CLS.h2}">Settings</h2>
        </div>
        <div id="settings-body" class="${CLS.settingsCard}">
            <div class="${CLS.notice}">Loading…</div>
        </div>
    `;

    const body = document.getElementById('settings-body');
    const FIELDS = [
        { key: 'ollama_url', label: 'Ollama URL', help: 'Base URL of the Ollama server used to generate embeddings.' },
        { key: 'ollama_embedding_model', label: 'Ollama embedding model', help: 'Model name to use for embeddings, e.g. nomic-embed-text.' },
        { key: 'ollama_chat_model', label: 'Ollama chat model', help: 'Model used to classify and name folders when reorganizing notes.' },
        { key: 'qdrant_url', label: 'Qdrant URL', help: 'Base URL of the Qdrant vector database.' },
        { key: 'qdrant_collection', label: 'Qdrant collection', help: 'Name of the Qdrant collection notes are stored in.' },
    ];

    let cfg;
    try {
        const res = await fetch('/api/v1/config');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        cfg = await res.json();
    } catch (err) {
        body.innerHTML = `<div class="${CLS.error}">Failed to load settings: ${escapeHtml(err.message)}</div>`;
        return;
    }

    body.innerHTML = `
        <form id="settings-form" class="space-y-4">
            ${FIELDS.map(f => `
                <div>
                    <label for="field-${f.key}" class="${CLS.fieldLabel}">${escapeHtml(f.label)}</label>
                    <input id="field-${f.key}" type="text" value="${escapeHtml(cfg[f.key] || '')}" class="${CLS.fieldInput}" autocomplete="off" />
                    <p class="${CLS.fieldHelp}">${escapeHtml(f.help)}</p>
                </div>
            `).join('')}
            <div class="flex items-center gap-3 pt-2">
                <button type="submit" class="${CLS.primaryBtn}">Save settings</button>
                <span id="settings-status" class="text-sm text-gray-500"></span>
            </div>
        </form>
        <hr class="my-6 border-gray-200" />
        <div>
            <h3 class="text-base font-semibold text-gray-800 mb-1">Reorganize notes</h3>
            <p class="${CLS.fieldHelp} mb-3">Asks the chat model above to file every note into a folder (and subfolder, where it makes sense), one note at a time. Re-running can move notes you've already organized.</p>
            <div class="flex items-center gap-3">
                <button id="reorganize-btn" class="${CLS.primaryBtn}">Reorganize notes</button>
            </div>
            <p id="reorganize-result" class="${CLS.fieldHelp} mt-2"></p>
        </div>
    `;

    document.getElementById('settings-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const status = document.getElementById('settings-status');
        const submitBtn = e.target.querySelector('button[type="submit"]');
        submitBtn.disabled = true;
        status.textContent = 'Saving…';
        try {
            const payload = {};
            for (const f of FIELDS) payload[f.key] = document.getElementById(`field-${f.key}`).value.trim();
            const res = await fetch('/api/v1/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            status.textContent = 'Saved';
            setTimeout(() => { status.textContent = ''; }, 1500);
        } catch (err) {
            status.textContent = 'Failed: ' + err.message;
        } finally {
            submitBtn.disabled = false;
        }
    });

    const reorganizeBtn = document.getElementById('reorganize-btn');
    const reorganizeResult = document.getElementById('reorganize-result');
    reorganizeBtn.addEventListener('click', async () => {
        reorganizeBtn.disabled = true;
        reorganizeResult.textContent = 'Reorganizing… classifying each note with the configured chat model, so it can take a while.';
        try {
            const res = await fetch('/api/v1/notes/reorganize', { method: 'POST' });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const r = await res.json();
            reorganizeResult.textContent =
                `Classified ${r.classified} note(s) — moved ${r.moved}, ` +
                `${r.unchanged} left unchanged` +
                (r.failed ? `, ${r.failed} failed to classify.` : '.');
        } catch (err) {
            reorganizeResult.textContent = 'Reorganize failed: ' + err.message;
        } finally {
            reorganizeBtn.disabled = false;
        }
    });
}

function route() {
    if (window.location.pathname.match(/^\/search\/?$/)) return renderSearch();
    if (window.location.pathname.match(/^\/settings\/?$/)) return renderSettings();
    const m = window.location.pathname.match(/^\/notes\/([^\/]+)\/?$/);
    if (m) return renderNote(decodeURIComponent(m[1]));
    return renderHome();
}

route();
