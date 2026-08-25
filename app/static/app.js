const state = {
    folders: [],
    papers: [],
    importFolderId: null,
    expandedFolders: new Set(),
    knownFolderIds: new Set(),
    inlineFolder: null,
    renamingFolderId: null,
    renamingPaperId: null,
    selectedFolderId: null,
    selectedPaper: null,
    textData: null,
    textVersions: [],
    currentSegment: -1,
    view: 'text',
    audioSegments: [],
    audioIndex: -1,
    audioPackages: [],
    selectedPackageId: '',
    jobs: [],
    qwenCapabilities: {},
    favoritesExpanded: true,
    localBridgeVerifiedUrl: '',
    localBridgeConfirmedUrl: '',
    pendingManualFile: null,
    knowledgeDialogMode: '',
    knowledgeDialogVersionId: '',
};

const $ = (id) => document.getElementById(id);
const els = {
    folderList: $('folder-list'), search: $('paper-search'), favoriteList: $('favorite-list'), favoritesToggle: $('favorites-toggle'), favoritesCount: $('favorites-count'),
    pdfInput: $('pdf-input'), importButton: $('import-button'), emptyImport: $('empty-import-button'),
    importModal: $('import-modal'), closeImport: $('close-import'), importFolder: $('import-folder-select'), choosePdf: $('choose-pdf-button'),
    contextMenu: $('context-menu'),
    empty: $('empty-view'), paperView: $('paper-view'), breadcrumb: $('breadcrumb'),
    title: $('paper-title'), meta: $('paper-meta'), status: $('paper-status'), pageCount: $('paper-page-count'), originalName: $('paper-original-name'), favoriteButton: $('favorite-button'), text: $('text-view'),
    original: $('original-view'), save: $('save-button'), audio: $('audio-button'),
    optimize: $('optimize-button'), manualText: $('manual-text-button'), manualTextInput: $('manual-text-input'), textVersion: $('text-version-select'), settingsButton: $('settings-button'), settingsModal: $('settings-modal'),
    closeSettings: $('close-settings'), saveSettings: $('save-settings'), resetSettings: $('reset-settings'),
    fontFamily: $('font-family'), fontSize: $('font-size'), lineHeight: $('line-height'),
    qwenApiKey: $('qwen-api-key'), qwenApiBase: $('qwen-api-base'), qwenModel: $('qwen-model'),
    qwenVoiceMode: $('qwen-voice-mode'), qwenVoice: $('qwen-voice'), defaultVoiceField: $('default-voice-field'),
    cloneVoiceField: $('clone-voice-field'), cloneAudio: $('clone-audio'), cloneVoiceSelect: $('clone-voice-select'), submitClone: $('submit-clone'),
    deepseekApiKey: $('deepseek-api-key'), deepseekApiBase: $('deepseek-api-base'), deepseekModel: $('deepseek-model'), deepseekVisionModel: $('deepseek-vision-model'),
    backupInterval: $('backup-interval'), exportBackup: $('export-backup'), importBackup: $('import-backup'), backupFile: $('backup-file'), backupStatus: $('backup-status'), openRecycle: $('open-recycle'),
    player: $('audio-player'), playerProgress: $('player-progress'),
    playbackRate: $('playback-rate'),
    audioPackage: $('audio-package-select'),
    play: $('play-button'), previous: $('previous-button'), next: $('next-button'),
    audioModal: $('audio-modal'), closeAudio: $('close-audio'), audioProvider: $('audio-provider'), audioTextVersion: $('audio-text-version'), localBridgeSettings: $('local-bridge-settings'), localBridgeHost: $('local-bridge-host'), localBridgePort: $('local-bridge-port'), testLocalBridge: $('test-local-bridge'), confirmLocalBridge: $('confirm-local-bridge'), localBridgeStatus: $('local-bridge-status'),
    audioProviderHint: $('audio-provider-hint'), startAudio: $('start-audio'), importAudio: $('import-audio'), audioPackageInput: $('audio-package-input'), audioPackages: $('audio-packages'),
    openJobs: $('open-jobs'), jobsModal: $('jobs-modal'), closeJobs: $('close-jobs'), jobsList: $('jobs-list'),
    openResources: $('open-resources'), resourcesModal: $('resources-modal'), closeResources: $('close-resources'), refreshResources: $('refresh-resources'), cleanupResources: $('cleanup-resources'), resourceStatus: $('resource-status'), resourceGroups: $('resource-groups'),
    recycleModal: $('recycle-modal'), closeRecycle: $('close-recycle'), refreshRecycle: $('refresh-recycle'), recycleStatus: $('recycle-status'), recycleList: $('recycle-list'),
    logsModal: $('logs-modal'), closeLogs: $('close-logs'), logsContent: $('logs-content'),
    track: $('track-fill'), toast: $('toast'), tabs: document.querySelectorAll('.view-tab'),
    optimizeModal: $('optimize-modal'), closeOptimize: $('close-optimize'), visionExtract: $('vision-extract-button'), readingOptimize: $('reading-optimize-button'),
    readingConfirmModal: $('reading-confirm-modal'), closeReadingConfirm: $('close-reading-confirm'), cancelReadingConfirm: $('cancel-reading-confirm'), confirmReadingOptimize: $('confirm-reading-optimize'), readingConfirmText: $('reading-confirm-text'), readingConfirmWarning: $('reading-confirm-warning'),
    knowledgeHint: $('knowledge-hint'), manualKbModal: $('manual-kb-modal'), closeManualKb: $('close-manual-kb'), manualKbIntro: $('manual-kb-intro'), manualKbChoice: $('manual-kb-choice'), manualKbNo: $('manual-kb-no'), manualKbYes: $('manual-kb-yes'), manualKbFields: $('manual-kb-fields'), manualKbTitle: $('manual-kb-title'), manualKbAuthors: $('manual-kb-authors'), manualKbAbstract: $('manual-kb-abstract'), manualKbCancel: $('manual-kb-cancel'), manualKbSubmit: $('manual-kb-submit'),
};

function toast(message, error = false) {
    els.toast.textContent = message;
    els.toast.style.background = error ? '#b33b41' : '#27282c';
    els.toast.classList.add('show');
    setTimeout(() => els.toast.classList.remove('show'), 3000);
}

async function api(path, options = {}) {
    const response = await fetch(path, options);
    if (!response.ok) {
        let detail = response.statusText;
        try { detail = (await response.json()).detail || detail; } catch (_) {}
        throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
}

function folderIcon() {
    return '<svg class="folder-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 6.5A2.5 2.5 0 0 1 6 4h4l2 2h6.5A2.5 2.5 0 0 1 21 8.5v8A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5v-10Z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';
}

function renderFolders() {
    els.folderList.innerHTML = '';
    const children = new Map();
    const papersByFolder = new Map();
    state.folders.forEach((folder) => {
        if (!children.has(folder.parent_id)) children.set(folder.parent_id, []);
        children.get(folder.parent_id).push(folder);
    });

    state.papers.forEach((paper) => {
        const key = paper.folder_id || null;
        if (!papersByFolder.has(key)) papersByFolder.set(key, []);
        papersByFolder.get(key).push(paper);
    });

    const rootRow = document.createElement('div');
    rootRow.className = 'folder-root-row';
    const rootButton = document.createElement('button');
    rootButton.className = `folder-item ${state.selectedFolderId === null ? 'active' : ''}`;
    rootButton.innerHTML = `${folderIcon()}<span>文件夹</span>`;
    rootButton.addEventListener('click', () => { state.selectedFolderId = null; renderFolders(); });
    const addButton = document.createElement('button');
    addButton.className = 'folder-add-button';
    addButton.title = '新建文件夹';
    addButton.textContent = '+';
    addButton.addEventListener('click', (event) => { event.stopPropagation(); beginCreateFolder(null); });
    rootRow.append(rootButton, addButton);
    els.folderList.appendChild(rootRow);

    if (state.inlineFolder?.parentId === null) renderFolderEditor(els.folderList, null);

    const renderPaper = (paper, container) => {
        const button = document.createElement('button');
        button.className = `paper-item paper-nested ${state.selectedPaper?.id === paper.id ? 'active' : ''}`;
        const activeJob = state.jobs.find((job) => job.paper_id === paper.id && ['queued', 'running', 'paused'].includes(job.status));
        const indicator = activeJob ? '<span class="audio-spinner" title="正在后台生成语音"></span>' : (Number(paper.audio_ready) ? '<span class="audio-star" title="已有语音包">★</span>' : '');
        if (state.renamingPaperId === paper.id) {
            button.innerHTML = `<span class="paper-icon">▤</span><input class="paper-inline-input" value="${escapeHtml(paper.title)}" aria-label="论文标题">`;
            button.addEventListener('click', (event) => event.stopPropagation());
            bindPaperRenameInput(button.querySelector('input'), paper);
        } else {
            button.innerHTML = `<span class="paper-icon">▤</span><span class="paper-copy"><span class="paper-name">${escapeHtml(paper.title)}</span></span>${indicator}`;
            button.addEventListener('click', () => openPaper(paper.id));
            button.addEventListener('dblclick', (event) => { event.preventDefault(); beginRenamePaper(paper); });
            button.addEventListener('contextmenu', (event) => showContextMenu(event, 'paper', paper));
        }
        container.appendChild(button);
    };

    const renderFolder = (folder, parentContainer, depth) => {
        const hasChildren = (children.get(folder.id) || []).length > 0 || (papersByFolder.get(folder.id) || []).length > 0 || state.inlineFolder?.parentId === folder.id;
        const expanded = state.expandedFolders.has(folder.id);
        const row = document.createElement('div');
        row.className = 'folder-row';
        row.dataset.folderId = folder.id;
        const toggle = document.createElement('button');
        toggle.className = `folder-toggle ${expanded ? 'expanded' : ''}`;
        toggle.innerHTML = `<span class="arrow">${hasChildren ? '▸' : '·'}</span>`;
        toggle.disabled = !hasChildren;
        toggle.addEventListener('click', (event) => { event.stopPropagation(); toggleFolder(folder.id); });
        row.appendChild(toggle);

        if (state.renamingFolderId === folder.id) {
            const editor = document.createElement('div');
            editor.className = 'folder-editor-row';
            editor.style.flex = '1';
            editor.innerHTML = '<input class="folder-inline-input" aria-label="文件夹名称"><span class="editor-hint">回车保存</span>';
            row.appendChild(editor);
            const input = editor.querySelector('input');
            input.value = folder.name;
            bindFolderRenameInput(input, folder);
        } else {
            const button = document.createElement('button');
            button.className = `folder-item ${state.selectedFolderId === folder.id ? 'active' : ''}`;
            button.style.paddingLeft = `${8 + Math.min(depth, 3) * 5}px`;
            button.innerHTML = `${folderIcon()}<span class="paper-copy">${escapeHtml(folder.name)}</span>`;
            button.addEventListener('click', () => selectFolder(folder, row));
            button.addEventListener('dblclick', (event) => { event.preventDefault(); beginRenameFolder(folder); });
            button.addEventListener('contextmenu', (event) => showContextMenu(event, 'folder', folder));
            row.appendChild(button);
        }
        parentContainer.appendChild(row);

        if (expanded) {
            const nested = document.createElement('div');
            nested.className = 'folder-children';
            if (state.inlineFolder?.parentId === folder.id) renderFolderEditor(nested, folder.id);
            (papersByFolder.get(folder.id) || []).forEach((paper) => renderPaper(paper, nested));
            (children.get(folder.id) || []).forEach((child) => renderFolder(child, nested, depth + 1));
            parentContainer.appendChild(nested);
        }
    };

    (children.get(null) || []).forEach((folder) => renderFolder(folder, els.folderList, 0));

    const unfiled = papersByFolder.get(null) || [];
    if (unfiled.length) {
        const unfiledFolder = { id: '__uncategorized__', name: '未分类论文' };
        const unfiledRow = document.createElement('div');
        unfiledRow.className = 'folder-row';
        const unfiledToggle = document.createElement('button');
        unfiledToggle.className = 'folder-toggle expanded';
        unfiledToggle.innerHTML = '<span class="arrow">▸</span>';
        unfiledToggle.addEventListener('click', () => { unfiledRow.nextElementSibling.hidden = !unfiledRow.nextElementSibling.hidden; unfiledToggle.classList.toggle('expanded'); });
        const unfiledLabel = document.createElement('button');
        unfiledLabel.className = 'folder-item';
        unfiledLabel.innerHTML = `${folderIcon()}<span class="paper-copy">未分类论文</span>`;
        unfiledRow.append(unfiledToggle, unfiledLabel);
        const unfiledChildren = document.createElement('div');
        unfiledChildren.className = 'folder-children';
        unfiled.forEach((paper) => renderPaper(paper, unfiledChildren));
        els.folderList.append(unfiledRow, unfiledChildren);
    }
    renderFavorites();
}

function renderFavorites() {
    const favorites = state.papers.filter((paper) => Number(paper.is_favorite));
    els.favoritesCount.textContent = String(favorites.length);
    els.favoritesToggle.classList.toggle('expanded', state.favoritesExpanded);
    els.favoriteList.hidden = !state.favoritesExpanded;
    els.favoriteList.innerHTML = '';
    favorites.forEach((paper) => {
        const button = document.createElement('button');
        button.className = `paper-item favorite-paper ${state.selectedPaper?.id === paper.id ? 'active' : ''}`;
        const activeJob = state.jobs.some((job) => job.paper_id === paper.id && ['queued', 'running', 'paused'].includes(job.status));
        const indicator = activeJob ? '<span class="audio-spinner"></span>' : (Number(paper.audio_ready) ? '<span class="audio-star">★</span>' : '');
        if (state.renamingPaperId === paper.id) {
            button.innerHTML = `<span class="paper-icon">▤</span><input class="paper-inline-input" value="${escapeHtml(paper.title)}" aria-label="论文标题">`;
            button.addEventListener('click', (event) => event.stopPropagation());
            bindPaperRenameInput(button.querySelector('input'), paper);
        } else {
            button.innerHTML = `<span class="paper-icon">▤</span><span class="paper-copy"><span class="paper-name">${escapeHtml(paper.title)}</span></span>${indicator}`;
            button.addEventListener('click', () => openPaper(paper.id));
            button.addEventListener('contextmenu', (event) => showContextMenu(event, 'paper', paper));
        }
        els.favoriteList.appendChild(button);
    });
}

function renderFolderEditor(container, parentId) {
    const row = document.createElement('div');
    row.className = 'folder-editor-row';
    row.innerHTML = '<input class="folder-inline-input" aria-label="新文件夹名称" placeholder="输入文件夹名称"><span class="editor-hint">回车保存</span>';
    container.appendChild(row);
    const input = row.querySelector('input');
    bindFolderCreateInput(input, parentId);
    window.setTimeout(() => input.focus(), 0);
}

function bindFolderCreateInput(input, parentId) {
    const commit = async () => {
        if (input.dataset.done) return;
        const name = input.value.trim();
        if (!name) { state.inlineFolder = null; renderFolders(); return; }
        input.dataset.done = '1';
        try {
            await api('/api/folders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, parent_id: parentId }) });
            state.inlineFolder = null;
            await refreshAll();
            toast('文件夹已创建');
        } catch (error) { delete input.dataset.done; toast(error.message, true); }
    };
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); commit(); }
        if (event.key === 'Escape') { state.inlineFolder = null; renderFolders(); }
    });
    input.addEventListener('blur', commit);
}

function beginCreateFolder(parentId = null) {
    hideContextMenu();
    state.renamingFolderId = null;
    state.inlineFolder = { parentId };
    if (parentId) state.expandedFolders.add(parentId);
    renderFolders();
}

function bindFolderRenameInput(input, folder) {
    const commit = async () => {
        if (input.dataset.done) return;
        const name = input.value.trim();
        if (!name || name === folder.name) { state.renamingFolderId = null; renderFolders(); return; }
        input.dataset.done = '1';
        try {
            await api(`/api/folders/${folder.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
            state.renamingFolderId = null;
            await refreshAll();
            toast('文件夹已重命名');
        } catch (error) { delete input.dataset.done; toast(error.message, true); }
    };
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); commit(); }
        if (event.key === 'Escape') { state.renamingFolderId = null; renderFolders(); }
    });
    input.addEventListener('blur', commit);
    window.setTimeout(() => { input.focus(); input.select(); }, 0);
}

function beginRenameFolder(folder) {
    hideContextMenu();
    state.inlineFolder = null;
    state.renamingFolderId = folder.id;
    state.expandedFolders.add(folder.id);
    renderFolders();
}

function beginRenamePaper(paper) {
    hideContextMenu();
    state.renamingPaperId = paper.id;
    renderFolders();
}

function bindPaperRenameInput(input, paper) {
    const commit = async () => {
        if (input.dataset.done) return;
        const title = input.value.trim();
        if (!title || title === paper.title) { state.renamingPaperId = null; renderFolders(); return; }
        input.dataset.done = '1';
        try {
            const updated = await api(`/api/papers/${paper.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }) });
            state.renamingPaperId = null;
            if (state.selectedPaper?.id === paper.id) state.selectedPaper = updated;
            await refreshPapers();
            if (state.selectedPaper?.id === paper.id) renderPaper();
            toast('论文标题已更新');
        } catch (error) { delete input.dataset.done; toast(error.message, true); }
    };
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); commit(); }
        if (event.key === 'Escape') { state.renamingPaperId = null; renderFolders(); }
    });
    input.addEventListener('blur', commit);
    window.setTimeout(() => { input.focus(); input.select(); }, 0);
}

function toggleFolder(folderId) {
    if (state.expandedFolders.has(folderId)) state.expandedFolders.delete(folderId);
    else state.expandedFolders.add(folderId);
    renderFolders();
    if (state.expandedFolders.has(folderId)) {
        window.setTimeout(() => document.querySelector(`[data-folder-id="${folderId}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'end' }), 0);
    }
}

function selectFolder(folder, row) {
    state.selectedFolderId = folder.id;
    state.expandedFolders.add(folder.id);
    renderFolders();
    window.setTimeout(() => document.querySelector(`[data-folder-id="${folder.id}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'end' }), 0);
}

function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

async function refreshFolders() {
    const data = await api('/api/folders/tree');
    state.folders = data.folders;
    state.folders.forEach((folder) => {
        if (!state.knownFolderIds.has(folder.id)) state.expandedFolders.add(folder.id);
        state.knownFolderIds.add(folder.id);
    });
    renderFolders();
}

async function refreshPapers() {
    const search = els.search.value.trim();
    const params = new URLSearchParams();
    if (search) params.set('search', search);
    const data = await api(`/api/papers?${params}`);
    state.papers = data.papers;
    renderFolders();
}

async function refreshAll() {
    try { await refreshFolders(); await refreshPapers(); } catch (error) { toast(error.message, true); }
}

async function openPaper(paperId) {
    try {
        const paper = await api(`/api/papers/${paperId}`);
        const text = await api(`/api/papers/${paperId}/text`);
        const versions = await api(`/api/papers/${paperId}/texts`);
        const audio = await api(`/api/papers/${paperId}/audio`);
        state.selectedPaper = paper;
        state.textData = text;
        state.textVersions = versions.versions || [];
        state.currentSegment = -1;
        state.audioPackages = audio.packages || [];
        // renderAudioPackageSelect() will select only a package belonging to
        // the currently displayed text version.
        state.audioSegments = [];
        state.audioIndex = -1;
        renderPaper();
        renderFolders();
    } catch (error) { toast(error.message, true); }
}

function renderPaper() {
    const paper = state.selectedPaper;
    if (!paper) return;
    els.empty.hidden = true;
    els.paperView.hidden = false;
    els.title.textContent = paper.title;
    els.status.textContent = paper.folder_name || '未分类';
    els.pageCount.textContent = `${paper.page_count} 页`;
    els.originalName.textContent = paper.original_filename;
    els.favoriteButton.classList.toggle('active', Number(paper.is_favorite) === 1);
    els.favoriteButton.innerHTML = thumbIcon(Number(paper.is_favorite) === 1);
    els.favoriteButton.title = Number(paper.is_favorite) === 1 ? '取消收藏' : '收藏论文';
    els.breadcrumb.textContent = `论文库 / ${paper.folder_name || '未分类'} / ${paper.title}`;
    renderTextVersionSelect();
    els.text.innerHTML = '';
    (state.textData?.segments || []).forEach((segment, index) => {
        const paragraph = document.createElement('div');
        paragraph.className = 'text-segment';
        paragraph.contentEditable = 'true';
        paragraph.dataset.index = index;
        paragraph.textContent = segment.content;
        paragraph.addEventListener('click', () => jumpToSegment(index));
        els.text.appendChild(paragraph);
    });
    renderAudioPackageSelect();
    els.audio.textContent = '生成完整语音';
    updatePlayerButtons();
}

function renderTextVersionSelect() {
    const versions = state.textVersions || [];
    els.textVersion.innerHTML = versions.map((version) =>
        `<option value="${escapeHtml(version.id)}">${escapeHtml(version.label || version.display_name || version.version_type)}${version.is_active ? ' · 当前' : ''}</option>`
    ).join('');
    if (state.textData?.version?.id) els.textVersion.value = state.textData.version.id;
    els.textVersion.disabled = !versions.length;
    const version = state.textData?.version;
    const isManual = ['manual', 'edited'].includes(version?.version_type);
    els.knowledgeHint.hidden = !(isManual && !Number(version?.kb_enabled));
}

async function loadTextVersion(versionId, activate = true) {
    if (!state.selectedPaper || !versionId) return;
    const text = await api(`/api/papers/${state.selectedPaper.id}/texts/${encodeURIComponent(versionId)}`);
    if (activate) {
        await api(`/api/papers/${state.selectedPaper.id}/texts/${encodeURIComponent(versionId)}/activate`, { method: 'POST' });
        state.selectedPaper.active_text_version_id = versionId;
        state.textVersions = (await api(`/api/papers/${state.selectedPaper.id}/texts`)).versions || state.textVersions;
    }
    state.textData = text;
    state.currentSegment = -1;
    state.audioIndex = -1;
    state.audioSegments = [];
    els.player.pause();
    els.player.removeAttribute('src');
    renderPaper();
}

function thumbIcon(active) {
    return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7.5 10.2v10H4.2a1.7 1.7 0 0 1-1.7-1.7v-6.6a1.7 1.7 0 0 1 1.7-1.7h3.3Zm0 10h8.8a2.4 2.4 0 0 0 2.3-1.8l1.7-6.6a2.1 2.1 0 0 0-2-2.6h-4.2l.6-3.2a2.3 2.3 0 0 0-2.2-2.7h-.7l-4.3 7v9.9Z" fill="${active ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>`;
}

function renderAudioPackageSelect() {
    const currentVersionId = state.textData?.version?.id || '';
    const packages = (state.audioPackages || []).filter((item) => !currentVersionId || item.text_version_id === currentVersionId);
    els.audioPackage.innerHTML = packages.length ? packages.map((item, index) => {
        const label = item.provider === 'qwen_tts' ? 'Qwen TTS' : item.provider === 'local_bridge' ? '本地大模型 TTS' : 'Edge TTS';
        return `<option value="${escapeHtml(item.id)}">${escapeHtml(item.text_version_name || item.version_type || '文本')} · ${label} · ${escapeHtml(item.model || '')}</option>`;
    }).join('') : '<option value="">尚未生成语音包</option>';
    els.audioPackage.disabled = !packages.length;
    const selected = packages.find((item) => item.id === state.selectedPackageId) || packages[0];
    if (selected) {
        els.audioPackage.value = selected.id;
        state.selectedPackageId = selected.id;
        state.audioSegments = selected.segments || [];
    } else {
        state.selectedPackageId = '';
        state.audioSegments = [];
    }
}

async function toggleFavorite() {
    if (!state.selectedPaper) return;
    try {
        state.selectedPaper = await api(`/api/papers/${state.selectedPaper.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_favorite: !Number(state.selectedPaper.is_favorite) }) });
        renderPaper();
        await refreshPapers();
        toast(Number(state.selectedPaper.is_favorite) ? '已收藏论文' : '已取消收藏');
    } catch (error) { toast(error.message, true); }
}

function jumpToSegment(index) {
    state.currentSegment = index;
    document.querySelectorAll('.text-segment').forEach((element, current) => element.classList.toggle('active', current === index));
    const element = document.querySelector(`.text-segment[data-index="${index}"]`);
    if (element) element.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (state.audioSegments[index]) {
        state.audioIndex = index;
        els.player.src = state.audioSegments[index].url;
        els.player.play().catch(() => {});
    }
    updatePlayerButtons();
}

function updatePlayerButtons() {
    const count = state.textData?.segments?.length || 0;
    els.playerProgress.textContent = count ? `${Math.max(state.currentSegment + 1, 0)} / ${count}` : '0 / 0';
    els.previous.disabled = state.audioIndex <= 0;
    els.next.disabled = state.audioIndex < 0 || state.audioIndex >= state.audioSegments.length - 1;
    els.play.disabled = state.audioIndex < 0;
    updatePlayButton();
}

function updatePlayButton() {
    const playing = !els.player.paused && !els.player.ended;
    els.play.textContent = playing ? 'Ⅱ' : '▶';
    els.play.title = playing ? '暂停' : '播放';
    els.play.setAttribute('aria-label', playing ? '暂停' : '播放');
}

function openImportDialog() {
    els.importFolder.innerHTML = '<option value="">未分类论文</option>';
    state.folders.forEach((folder) => {
        const option = document.createElement('option');
        option.value = folder.id;
        option.textContent = `${folder.parent_id ? '　' : ''}${folder.name}`;
        els.importFolder.appendChild(option);
    });
    els.importFolder.value = state.selectedFolderId || '';
    els.importModal.hidden = false;
}

function closeImportDialog() {
    els.importModal.hidden = true;
}

async function importPaper(file, folderId = null) {
    const form = new FormData();
    form.append('file', file);
    const query = folderId ? `?folder_id=${encodeURIComponent(folderId)}` : '';
    toast('正在导入论文...');
    const paper = await api(`/api/papers/import${query}`, { method: 'POST', body: form });
    await refreshAll();
    await openPaper(paper.id);
    toast('论文导入完成');
}

async function saveText() {
    if (!state.selectedPaper) return;
    const content = [...document.querySelectorAll('.text-segment')].map((element) => element.innerText.trim()).filter(Boolean).join('\n\n');
    els.save.disabled = true;
    try {
        const source = state.textData?.version;
        const sourceType = ['raw', 'manual', 'deepseek_vision', 'optimized'].includes(source?.version_type) ? source.version_type : 'manual';
        const labels = { raw: '自动识别', manual: '手动导入', deepseek_vision: 'DeepSeek 读图识别', optimized: '朗读优化' };
        state.textData = await api(`/api/papers/${state.selectedPaper.id}/text`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: content, version_type: sourceType, source_version_id: source?.id || null, display_name: labels[sourceType], provider: source?.provider || 'manual', model: source?.llm_model || null }) });
        state.textVersions = (await api(`/api/papers/${state.selectedPaper.id}/texts`)).versions || [];
        renderPaper();
        await refreshPapers();
        toast('文本已另存为新版本，旧文本和语音包仍然保留');
    } catch (error) { toast(error.message, true); } finally { els.save.disabled = false; }
}

async function generateAudio() {
    if (!state.selectedPaper) return;
    renderAudioTextVersions();
    renderAudioPackages();
    updateAudioProviderUI();
    els.audioModal.hidden = false;
}

function getLocalBridgeUrl() {
    const host = els.localBridgeHost.value.trim().replace(/^https?:\/\//i, '').replace(/\/+$/, '');
    const port = els.localBridgePort.value.trim();
    return host && port ? `http://${host}:${port}` : '';
}

function updateAudioProviderUI() {
    const localBridge = els.audioProvider.value === 'local_bridge';
    els.localBridgeSettings.hidden = !localBridge;
    if (localBridge) {
        els.audioProviderHint.textContent = '发送任务到本地大模型 TTS 中介。PDF 朗读器不会跟踪生成进度，完成后请使用“导入语音”导入 ZIP。';
        const url = getLocalBridgeUrl();
        if (state.localBridgeVerifiedUrl !== url) state.localBridgeConfirmedUrl = '';
        const verified = Boolean(url && state.localBridgeVerifiedUrl === url);
        const confirmed = Boolean(verified && state.localBridgeConfirmedUrl === url);
        els.confirmLocalBridge.disabled = !verified || confirmed;
        els.startAudio.textContent = '发送任务';
        els.importAudio.hidden = false;
        els.startAudio.disabled = !confirmed;
        if (!verified && ['连接已通过', '已确认，可生成'].includes(els.localBridgeStatus.textContent)) {
            els.localBridgeStatus.textContent = '地址已变化，请重新测试';
            els.localBridgeStatus.className = 'bridge-status';
        }
        if (verified && !confirmed && ['已确认，可生成', '已确认，可发送'].includes(els.localBridgeStatus.textContent)) {
            els.localBridgeStatus.textContent = '连接已通过，请确认使用';
            els.localBridgeStatus.className = 'bridge-status success';
        }
    } else {
        els.audioProviderHint.textContent = els.audioProvider.value === 'qwen_tts' ? '使用设置中选择的 Qwen TTS 模型与音色，任务会在后台完整生成。' : 'Edge TTS 免费使用，任务会在后台完整生成。';
        els.startAudio.textContent = '生成完整语音';
        els.importAudio.hidden = true;
        els.startAudio.disabled = false;
    }
}

async function testLocalBridge() {
    const baseUrl = getLocalBridgeUrl();
    if (!baseUrl) { toast('请填写中介服务 IP 和端口', true); return; }
    els.testLocalBridge.disabled = true;
    els.localBridgeStatus.textContent = '连接测试中…';
    els.localBridgeStatus.className = 'bridge-status';
    try {
        await api('/api/local-tts/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ base_url: baseUrl }) });
        state.localBridgeVerifiedUrl = baseUrl;
        state.localBridgeConfirmedUrl = '';
        els.localBridgeStatus.textContent = '连接已通过，请确认使用';
        els.localBridgeStatus.className = 'bridge-status success';
        const settings = readSettingsForm();
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
        updateAudioProviderUI();
        toast('本地 TTS 中介连接测试通过');
    } catch (error) {
        state.localBridgeVerifiedUrl = '';
        els.localBridgeStatus.textContent = `连接失败：${error.message}`;
        els.localBridgeStatus.className = 'bridge-status error';
        updateAudioProviderUI();
    } finally { els.testLocalBridge.disabled = false; }
}

function confirmLocalBridge() {
    const baseUrl = getLocalBridgeUrl();
    if (!baseUrl || state.localBridgeVerifiedUrl !== baseUrl) {
        toast('请先测试当前中介地址', true);
        return;
    }
    state.localBridgeConfirmedUrl = baseUrl;
    els.localBridgeStatus.textContent = '已确认，可发送';
    els.localBridgeStatus.className = 'bridge-status success';
    updateAudioProviderUI();
    toast('已确认使用本地 TTS 中介');
}

function renderAudioTextVersions() {
    els.audioTextVersion.innerHTML = (state.textVersions || []).map((version) =>
        `<option value="${escapeHtml(version.id)}">${escapeHtml(version.label || version.version_type)}${version.id === state.textData?.version?.id ? ' · 当前阅读' : ''}</option>`
    ).join('');
    if (state.textData?.version?.id) els.audioTextVersion.value = state.textData.version.id;
}

function renderAudioPackages() {
    const packages = state.audioPackages || [];
    if (!packages.length) {
        els.audioPackages.innerHTML = '<div class="empty-list">这篇论文还没有生成语音包</div>';
        return;
    }
    els.audioPackages.innerHTML = packages.map((item) => {
        const label = item.provider === 'qwen_tts' ? '在线 Qwen TTS' : item.provider === 'local_bridge' ? '本地大模型 TTS' : '本地 Edge TTS';
        const created = item.created_at ? new Date(item.created_at).toLocaleString() : '';
        return `<div class="package-item"><div class="package-row"><div class="package-info"><div class="package-title">${escapeHtml(item.text_version_name || item.version_type || '文本')} · ${label} · ${escapeHtml(item.model || '')}</div><div class="package-meta">${escapeHtml(item.voice_id || '')} ${escapeHtml(created)}</div></div><button class="package-delete" data-package-id="${escapeHtml(item.id)}">删除语音包</button></div></div>`;
    }).join('');
    els.audioPackages.querySelectorAll('[data-package-id]').forEach((button) => button.addEventListener('click', () => deleteAudioPackage(button.dataset.packageId)));
}

async function deleteAudioPackage(packageId) {
    if (!state.selectedPaper || !window.confirm('确定删除这个语音包吗？对应的音频文件会一并删除。')) return;
    try {
        await api(`/api/papers/${state.selectedPaper.id}/audio/${encodeURIComponent(packageId)}`, { method: 'DELETE' });
        await openPaper(state.selectedPaper.id);
        renderAudioPackages();
        await refreshPapers();
        toast('语音包已删除');
    } catch (error) { toast(error.message, true); }
}

async function startAudioGeneration() {
    if (!state.selectedPaper) return;
    const provider = els.audioProvider.value;
    const settings = readSettingsForm();
    if (provider === 'local_bridge') {
        const bridgeUrl = getLocalBridgeUrl();
        if (!bridgeUrl || state.localBridgeConfirmedUrl !== bridgeUrl) {
            toast('请先测试并确认使用本地 TTS 中介', true);
            updateAudioProviderUI();
            return;
        }
    }
    const payload = provider === 'qwen_tts' ? {
        provider, model: settings.qwenModel, voice: settings.qwenVoice,
        voice_id: settings.qwenVoiceMode === 'clone' ? settings.cloneVoiceId : settings.qwenVoice,
        api_key: settings.qwenApiKey, api_base: settings.qwenApiBase, speed: 1,
    } : provider === 'local_bridge' ? {
        provider, model: settings.qwenModel, voice: settings.qwenVoice, voice_id: settings.qwenVoice,
        speed: 1, bridge_url: getLocalBridgeUrl(),
    } : { provider, model: 'edge-tts', voice: 'zh-CN-XiaoxiaoNeural', speed: 1 };
    payload.text_version_id = els.audioTextVersion.value || state.textData?.version?.id || null;
    if (provider === 'qwen_tts' && !payload.api_key) { toast('请先在设置中填写 Qwen API Key', true); return; }
    if (provider === 'qwen_tts' && settings.qwenVoiceMode === 'clone' && (!settings.cloneVoiceId || settings.cloneVoiceModel !== settings.qwenModel)) {
        toast('请先为当前模型提交音源并创建克隆音色', true);
        return;
    }
    els.startAudio.disabled = true;
    try {
        if (provider === 'local_bridge') {
            const result = await api(`/api/papers/${state.selectedPaper.id}/audio/bridge-submit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            els.audioModal.hidden = true;
            toast(`任务已发送到中介：${result.bridge_job_id || result.job?.id || '已提交'}，完成后请导入语音包`);
        } else {
            await api(`/api/papers/${state.selectedPaper.id}/audio`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            els.audioModal.hidden = true;
            await refreshJobs();
            toast('语音任务已进入后台，可在设置中的“后台任务”查看');
        }
    } catch (error) { toast(error.message, true); }
    finally { els.startAudio.disabled = false; }
}

async function importAudioPackages() {
    const files = [...(els.audioPackageInput.files || [])];
    if (!files.length) return;
    const form = new FormData();
    files.forEach((file) => form.append('files', file, file.name));
    els.importAudio.disabled = true;
    try {
        const result = await api('/api/audio/import-packages', { method: 'POST', body: form });
        const imported = result.imported || [];
        const failed = result.failed || [];
        const successText = imported.length ? `成功导入 ${imported.length} 个语音包` : '没有导入成功的语音包';
        const matchedText = imported.length
            ? `，已自动匹配：${imported.map((item) => `${item.paper_title} · ${item.text_version_label || '文本版本'}`).join('；')}`
            : '';
        const failedText = failed.length ? `，${failed.length} 个失败：${failed.map((item) => `${item.filename}（${item.error}）`).join('；')}` : '';
        if (state.selectedPaper) await openPaper(state.selectedPaper.id);
        await refreshPapers();
        renderAudioPackages();
        toast(successText + matchedText + failedText, Boolean(failed.length && !imported.length));
    } catch (error) {
        toast(error.message, true);
    } finally {
        els.importAudio.disabled = false;
        els.audioPackageInput.value = '';
    }
}

const SETTINGS_KEY = 'pdf-library-settings';
const DEFAULT_SETTINGS = {
    fontFamily: 'serif', fontSize: '18', lineHeight: '2',
    qwenApiKey: '', qwenApiBase: 'https://dashscope.aliyuncs.com/compatible-mode/v1', qwenModel: 'cosyvoice-v2',
    qwenVoiceMode: 'default', qwenVoice: 'Cherry', cloneVoiceId: '', cloneVoiceModel: '', cloneVoiceName: '',
    deepseekApiKey: '', deepseekApiBase: 'https://api.deepseek.com', deepseekModel: 'deepseek-v4-flash', deepseekVisionModel: 'deepseek-v4-flash-vision-exp', localBridgeHost: '127.0.0.1', localBridgePort: '47840', backupIntervalDays: 30,
};

function loadSettings() {
    try { return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}') }; }
    catch (_) { return { ...DEFAULT_SETTINGS }; }
}

function applyReadingSettings(settings) {
    const fonts = { serif: 'Georgia, "Songti SC", "SimSun", serif', sans: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif', mono: 'Consolas, "Microsoft YaHei", monospace' };
    document.documentElement.style.setProperty('--reading-font', fonts[settings.fontFamily] || fonts.serif);
    document.documentElement.style.setProperty('--reading-size', `${settings.fontSize || 18}px`);
    document.documentElement.style.setProperty('--reading-line-height', settings.lineHeight || 2);
}

function fillSelect(select, values, selected) {
    const items = [...new Set(values.filter(Boolean))];
    if (selected && !items.includes(selected)) items.unshift(selected);
    select.innerHTML = items.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join('');
    if (selected) select.value = selected;
}

function fillQwenModels(models, capabilities, selected) {
    state.qwenCapabilities = capabilities || {};
    const items = [...new Set(models.filter(Boolean))];
    if (selected && !items.includes(selected)) items.unshift(selected);
    els.qwenModel.innerHTML = items.map((model) => {
        const capability = state.qwenCapabilities[model] || { default_voice: true, clone_voice: false };
        const icons = `${capability.default_voice ? '🤖' : ''}${capability.clone_voice ? '👤' : ''}`;
        return `<option value="${escapeHtml(model)}">${icons} ${escapeHtml(model)}</option>`;
    }).join('');
    if (selected) els.qwenModel.value = selected;
    updateQwenVoiceModes();
}

function updateQwenVoiceModes() {
    const capability = state.qwenCapabilities[els.qwenModel.value] || { default_voice: true, clone_voice: false };
    const previous = els.qwenVoiceMode.value;
    const options = [];
    if (capability.default_voice) options.push('<option value="default">默认音色</option>');
    if (capability.clone_voice) options.push('<option value="clone">克隆音色</option>');
    els.qwenVoiceMode.innerHTML = options.join('');
    if ([...els.qwenVoiceMode.options].some((option) => option.value === previous)) els.qwenVoiceMode.value = previous;
    const clone = els.qwenVoiceMode.value === 'clone';
    els.defaultVoiceField.hidden = clone;
    els.cloneVoiceField.hidden = !clone;
}

async function refreshQwenVoices() {
    const capability = state.qwenCapabilities[els.qwenModel.value] || { default_voice: true };
    if (!capability.default_voice) return;
    try {
        const data = await api(`/api/tts-voices?model=${encodeURIComponent(els.qwenModel.value)}`);
        fillSelect(els.qwenVoice, data.voices || [], els.qwenVoice.value);
    } catch (_) {}
}

async function refreshCloneVoices(preferredId = '') {
    const targetModel = els.qwenModel.value;
    els.cloneVoiceSelect.innerHTML = '<option value="">请选择已提交音色</option>';
    if (!targetModel) return;
    try {
        const data = await api(`/api/voice-clones?target_model=${encodeURIComponent(targetModel)}`);
        const voices = data.voices || [];
        els.cloneVoiceSelect.innerHTML = '<option value="">请选择已提交音色</option>' + voices.map((voice) => `<option value="${escapeHtml(voice.id)}" data-name="${escapeHtml(voice.name || voice.id)}" data-model="${escapeHtml(voice.target_model || targetModel)}">${escapeHtml(voice.name || voice.id)}</option>`).join('');
        const selected = preferredId || els.cloneVoiceId || '';
        if (selected && voices.some((voice) => voice.id === selected)) {
            els.cloneVoiceSelect.value = selected;
        } else {
            els.cloneVoiceSelect.value = '';
            els.cloneVoiceId = '';
            els.cloneVoiceModel = '';
            els.cloneVoiceName = '';
        }
    } catch (_) {
        els.cloneVoiceSelect.innerHTML = '<option value="">读取已提交音色失败</option>';
    }
}

function readSettingsForm() {
    const selectedClone = els.cloneVoiceSelect.options[els.cloneVoiceSelect.selectedIndex];
    return {
        fontFamily: els.fontFamily.value, fontSize: els.fontSize.value, lineHeight: els.lineHeight.value,
        qwenApiKey: els.qwenApiKey.value.trim(), qwenApiBase: els.qwenApiBase.value.trim(), qwenModel: els.qwenModel.value,
        qwenVoiceMode: els.qwenVoiceMode.value, qwenVoice: els.qwenVoice.value, cloneVoiceId: els.cloneVoiceId || '', cloneVoiceModel: els.cloneVoiceModel || '',
        cloneVoiceName: selectedClone?.dataset?.name || els.cloneVoiceName || '', deepseekApiKey: els.deepseekApiKey.value.trim(),
        deepseekApiBase: els.deepseekApiBase.value.trim(), deepseekModel: els.deepseekModel.value, deepseekVisionModel: els.deepseekVisionModel.value,
        localBridgeHost: els.localBridgeHost.value.trim(), localBridgePort: els.localBridgePort.value.trim(),
        backupIntervalDays: Number(els.backupInterval.value || 30),
    };
}

function renderSettingsForm(settings = loadSettings()) {
    els.fontFamily.value = settings.fontFamily;
    els.fontSize.value = String(settings.fontSize);
    els.lineHeight.value = String(settings.lineHeight);
    els.qwenApiKey.value = settings.qwenApiKey;
    els.qwenApiBase.value = settings.qwenApiBase;
    const initialModel = settings.qwenModel || 'cosyvoice-v2';
    const initialModelLower = initialModel.toLowerCase();
    fillQwenModels([initialModel], { [initialModel]: { default_voice: true, clone_voice: initialModelLower.includes('cosyvoice') || initialModelLower.includes('-vc') || initialModelLower.includes('voice-clone') } }, initialModel);
    els.qwenVoiceMode.value = settings.qwenVoiceMode;
    els.qwenVoice.value = settings.qwenVoice;
    els.cloneVoiceId = settings.cloneVoiceId || '';
    els.cloneVoiceModel = settings.cloneVoiceModel || '';
    els.cloneVoiceName = settings.cloneVoiceName || '';
    els.deepseekApiKey.value = settings.deepseekApiKey;
    els.deepseekApiBase.value = settings.deepseekApiBase;
    fillSelect(els.deepseekModel, [settings.deepseekModel || 'deepseek-v4-flash'], settings.deepseekModel || 'deepseek-v4-flash');
    fillSelect(els.deepseekVisionModel, [settings.deepseekVisionModel || 'deepseek-v4-flash-vision-exp'], settings.deepseekVisionModel || 'deepseek-v4-flash-vision-exp');
    els.localBridgeHost.value = settings.localBridgeHost || '127.0.0.1';
    els.localBridgePort.value = String(settings.localBridgePort || '47840');
    els.backupInterval.value = String(settings.backupIntervalDays ?? 30);
    els.qwenVoiceMode.dispatchEvent(new Event('change'));
    refreshCloneVoices(settings.cloneVoiceId || '');
}

async function refreshBackupSettings() {
    try {
        const settings = await api('/api/backups/settings');
        els.backupInterval.value = String(settings.interval_days ?? 30);
        els.backupStatus.textContent = settings.last_backup_at ? `上次备份：${new Date(settings.last_backup_at).toLocaleString()}；备份包括论文、文件夹、文本版本、原 PDF、语音包和音源文件。` : '尚未创建备份；备份包括论文、文件夹、文本版本、原 PDF、语音包和音源文件。';
    } catch (_) {}
}

async function exportLibraryBackup() {
    els.exportBackup.disabled = true;
    try {
        const result = await api('/api/backups/create', { method: 'POST' });
        const link = document.createElement('a');
        link.href = result.download_url;
        link.download = result.filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        await refreshBackupSettings();
        toast('完整论文库备份已生成');
    } catch (error) { toast(error.message, true); }
    finally { els.exportBackup.disabled = false; }
}

async function importLibraryBackup(file) {
    if (!file || !window.confirm('导入备份会覆盖当前论文库。系统会先自动生成恢复前安全备份，确定继续吗？')) return;
    const form = new FormData();
    form.append('file', file);
    els.importBackup.disabled = true;
    try {
        await api('/api/backups/import', { method: 'POST', body: form });
        state.selectedPaper = null;
        state.textData = null;
        state.audioPackages = [];
        state.audioSegments = [];
        els.paperView.hidden = true;
        els.empty.hidden = false;
        await refreshAll();
        await refreshBackupSettings();
        toast('论文库导入完成，请重新打开论文');
    } catch (error) { toast(error.message, true); }
    finally { els.importBackup.disabled = false; els.backupFile.value = ''; }
}

function closeSettings() { els.settingsModal.hidden = true; }
function openSettings() { renderSettingsForm(); els.settingsModal.hidden = false; refreshBackupSettings(); }

async function fetchProviderModels(button) {
    const provider = button.dataset.provider;
    const key = provider === 'qwen' ? els.qwenApiKey.value.trim() : els.deepseekApiKey.value.trim();
    const base = provider === 'qwen' ? els.qwenApiBase.value.trim() : els.deepseekApiBase.value.trim();
    if (!key || !base) { toast('请先填写 API Key 和 Base URL', true); return; }
    button.disabled = true;
    button.textContent = '获取中…';
    try {
        const data = await api('/api/fetch-models', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: key, api_base: base }) });
        const models = provider === 'qwen' ? (data.tts_models || []) : (data.llm_models || data.all_models || []);
        if (!models.length) throw new Error('接口没有返回可用模型');
        const select = provider === 'qwen' ? els.qwenModel : els.deepseekModel;
        if (provider === 'qwen') { fillQwenModels(models, data.tts_capabilities || {}, select.value); await refreshQwenVoices(); }
        else {
            fillSelect(select, models, select.value);
            fillSelect(els.deepseekVisionModel, data.vision_models || ['deepseek-v4-flash-vision-exp'], els.deepseekVisionModel.value);
        }
        toast(`${provider === 'qwen' ? 'Qwen' : 'DeepSeek'} 模型列表已更新`);
    } catch (error) { toast(`获取模型失败：${error.message}`, true); }
    finally { button.disabled = false; button.textContent = '获取模型'; }
}

async function submitCloneVoice() {
    const file = els.cloneAudio.files[0];
    if (!file) { toast('请先选择音源素材', true); return; }
    if (!els.qwenApiKey.value.trim()) { toast('请先填写 Qwen API Key', true); return; }
    const form = new FormData();
    form.append('file', file);
    form.append('name', file.name.replace(/\.[^.]+$/, '').slice(0, 16));
    form.append('api_key', els.qwenApiKey.value.trim());
    form.append('api_base', els.qwenApiBase.value.trim());
    form.append('target_model', els.qwenModel.value);
    els.submitClone.disabled = true;
    try {
        const result = await api('/api/voice-clones', { method: 'POST', body: form });
        els.cloneVoiceId = result.id;
        els.cloneVoiceModel = els.qwenModel.value;
        els.cloneVoiceName = result.name || file.name;
        await refreshCloneVoices(result.id);
        const settings = readSettingsForm();
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
        els.cloneAudio.value = '';
        toast('Qwen 克隆音色已创建并保存');
    } catch (error) { toast(error.message, true); }
    finally { els.submitClone.disabled = false; }
}

function optimizeText() {
    if (!state.selectedPaper || !state.textData?.version) return;
    els.optimizeModal.hidden = false;
}

function closeOptimizeDialog() { els.optimizeModal.hidden = true; }

function openReadingConfirmDialog() {
    if (!state.selectedPaper || !state.textData?.version) return;
    const version = state.textData.version;
    const label = version.display_name || version.label || ({ raw: '自动识别', manual: '手动导入', deepseek_vision: '读图识别', optimized: '朗读优化' }[version.version_type] || '当前文本');
    els.readingConfirmText.textContent = `当前文本为“${label}”，是否在此基础上优化朗读文本？`;
    els.readingConfirmWarning.hidden = !['raw', 'organized'].includes(version.version_type);
    closeOptimizeDialog();
    els.readingConfirmModal.hidden = false;
}

function closeReadingConfirmDialog() { els.readingConfirmModal.hidden = true; }

async function runReadingOptimization() {
    if (!state.selectedPaper || !state.textData?.version) return;
    const settings = loadSettings();
    if (!settings.deepseekApiKey) { toast('请先在设置中填写 DeepSeek API Key', true); closeOptimizeDialog(); openSettings(); return; }
    const content = [...document.querySelectorAll('.text-segment')].map((element) => element.innerText.trim()).filter(Boolean).join('\n\n');
    els.readingOptimize.disabled = true;
    try {
        const result = await api('/api/llm-process', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: content, api_key: settings.deepseekApiKey, api_base: settings.deepseekApiBase, model: settings.deepseekModel || 'deepseek-v4-flash' }) });
        const sourceVersion = state.textData.version;
        state.textData = await api(`/api/papers/${state.selectedPaper.id}/text`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: result.processed_text, version_type: 'optimized', source_version_id: sourceVersion.id, display_name: '朗读优化', provider: 'deepseek', model: settings.deepseekModel || 'deepseek-v4-flash' }) });
        state.textVersions = (await api(`/api/papers/${state.selectedPaper.id}/texts`)).versions || [];
        renderPaper();
        await refreshPapers();
        closeOptimizeDialog();
        toast('朗读优化已保存为新的文本版本');
    } catch (error) { toast(`朗读优化失败：${error.message}`, true); }
    finally { els.readingOptimize.disabled = false; }
}

async function runVisionExtraction() {
    if (!state.selectedPaper) return;
    const settings = loadSettings();
    if (!settings.deepseekApiKey) { toast('请先在设置中填写 DeepSeek API Key', true); closeOptimizeDialog(); openSettings(); return; }
    els.visionExtract.disabled = true;
    try {
        await api(`/api/papers/${state.selectedPaper.id}/vision-extract`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: settings.deepseekApiKey, api_base: settings.deepseekApiBase, model: settings.deepseekVisionModel || 'deepseek-v4-flash-vision-exp' }) });
        closeOptimizeDialog();
        toast('DeepSeek 读图任务已开始，可在后台任务中查看进度');
        await refreshJobs();
    } catch (error) { toast(`启动读图识别失败：${error.message}`, true); }
    finally { els.visionExtract.disabled = false; }
}

async function importManualText(file) {
    if (!state.selectedPaper || !file) return;
    closeOptimizeDialog();
    const form = new FormData();
    form.append('file', file);
    form.append('kb_enabled', 'false');
    try {
        await api(`/api/papers/${state.selectedPaper.id}/manual-text`, { method: 'POST', body: form });
        await openPaper(state.selectedPaper.id);
        await refreshPapers();
        toast('手动正文已导入为新的文本版本');
    } catch (error) { toast(`导入手动正文失败：${error.message}`, true); }
}

function resetKnowledgeDialog() {
    state.pendingManualFile = null;
    state.knowledgeDialogMode = '';
    state.knowledgeDialogVersionId = '';
    els.manualKbChoice.hidden = false;
    els.manualKbFields.hidden = true;
    els.manualKbSubmit.hidden = true;
    els.manualKbSubmit.textContent = '保存并导入';
    els.manualKbTitle.value = '';
    els.manualKbAuthors.value = '';
    els.manualKbAbstract.value = '';
    els.manualKbIntro.textContent = '是否要为这份手动导入文本增加知识库信息？';
}

function openManualKnowledgeDialog(file) {
    resetKnowledgeDialog();
    closeOptimizeDialog();
    state.pendingManualFile = file;
    state.knowledgeDialogMode = 'import';
    els.manualKbModal.hidden = false;
}

function openKnowledgeEditor() {
    const version = state.textData?.version;
    if (!state.selectedPaper || !version || !['manual', 'edited'].includes(version.version_type)) return;
    resetKnowledgeDialog();
    state.knowledgeDialogMode = 'edit';
    state.knowledgeDialogVersionId = version.id;
    els.manualKbChoice.hidden = true;
    els.manualKbFields.hidden = false;
    els.manualKbSubmit.hidden = false;
    els.manualKbSubmit.textContent = '保存知识库信息';
    els.manualKbIntro.textContent = '为当前手动导入文本补充论文标题、作者和摘要后，它会成为 AstrBot 知识库的优先文本。';
    els.manualKbTitle.value = version.kb_title || '';
    els.manualKbAuthors.value = version.kb_authors || '';
    els.manualKbAbstract.value = version.kb_abstract || '';
    els.manualKbModal.hidden = false;
}

async function submitKnowledgeDialog() {
    const title = els.manualKbTitle.value.trim();
    const authors = els.manualKbAuthors.value.trim();
    const abstract = els.manualKbAbstract.value.trim();
    if (!title || !abstract) { toast('论文标题和摘要不能为空', true); return; }
    els.manualKbSubmit.disabled = true;
    try {
        if (state.knowledgeDialogMode === 'import') {
            const form = new FormData();
            form.append('file', state.pendingManualFile);
            form.append('kb_enabled', 'true');
            form.append('kb_title', title);
            form.append('kb_authors', authors);
            form.append('kb_abstract', abstract);
            await api('/api/papers/' + state.selectedPaper.id + '/manual-text', { method: 'POST', body: form });
            await openPaper(state.selectedPaper.id);
            await refreshPapers();
            toast('手动正文和知识库信息已保存');
        } else if (state.knowledgeDialogMode === 'edit') {
            await api('/api/papers/' + state.selectedPaper.id + '/texts/' + encodeURIComponent(state.knowledgeDialogVersionId) + '/knowledge', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: true, title, authors, abstract }),
            });
            await openPaper(state.selectedPaper.id);
            await refreshPapers();
            toast('知识库信息已保存');
        }
        els.manualKbModal.hidden = true;
        resetKnowledgeDialog();
    } catch (error) { toast(error.message, true); }
    finally { els.manualKbSubmit.disabled = false; }
}

async function createFolder() {
    beginCreateFolder(null);
}

async function deleteFolder(folder) {
    const warning = `确定删除文件夹“${folder.name}”吗？\n\n删除文件夹会同时删除其中的所有子文件夹、论文、原 PDF、文本和音频，且无法恢复。`;
    if (!window.confirm(warning)) return;
    try {
        await api(`/api/folders/${folder.id}`, { method: 'DELETE' });
        if (state.selectedPaper) {
            state.selectedPaper = null;
            state.textData = null;
            state.audioSegments = [];
            state.audioIndex = -1;
            els.paperView.hidden = true;
            els.empty.hidden = false;
            els.breadcrumb.textContent = '论文库 / 未选择论文';
        }
        if (state.selectedFolderId === folder.id) state.selectedFolderId = null;
        await refreshAll();
        toast('文件夹及其中论文已删除');
    } catch (error) { toast(error.message, true); }
}

async function deletePaper() {
    if (!state.selectedPaper || !window.confirm(`确定删除“${state.selectedPaper.title}”吗？原 PDF、文本和音频都会删除。`)) return;
    try { await api(`/api/papers/${state.selectedPaper.id}`, { method: 'DELETE' }); state.selectedPaper = null; state.textData = null; state.audioSegments = []; state.audioIndex = -1; els.paperView.hidden = true; els.empty.hidden = false; els.breadcrumb.textContent = '论文库 / 未选择论文'; await refreshAll(); toast('论文及其本地资源已删除'); }
    catch (error) { toast(error.message, true); }
}

function hideContextMenu() { els.contextMenu.hidden = true; els.contextMenu.innerHTML = ''; }

function showContextMenu(event, type, item) {
    event.preventDefault();
    event.stopPropagation();
    const actions = type === 'folder'
        ? [{ label: '重命名文件夹', action: () => beginRenameFolder(item) }, { label: '删除文件夹', action: () => deleteFolder(item), danger: true }]
        : [{ label: '打开论文', action: () => openPaper(item.id) }, { label: '重命名论文', action: () => beginRenamePaper(item) }, { label: '删除论文', action: () => deletePaperById(item), danger: true }];
    els.contextMenu.innerHTML = actions.map((entry, index) => `<button data-menu-index="${index}" class="${entry.danger ? 'danger-action' : ''}">${entry.label}</button>`).join('');
    actions.forEach((entry, index) => els.contextMenu.querySelector(`[data-menu-index="${index}"]`).addEventListener('click', () => { hideContextMenu(); entry.action(); }));
    els.contextMenu.style.left = `${Math.min(event.clientX, window.innerWidth - 175)}px`;
    els.contextMenu.style.top = `${Math.min(event.clientY, window.innerHeight - 100)}px`;
    els.contextMenu.hidden = false;
}

async function deletePaperById(paper) {
    if (!window.confirm(`确定删除“${paper.title}”吗？原 PDF、文本和音频都会删除，且无法恢复。`)) return;
    try {
        await api(`/api/papers/${paper.id}`, { method: 'DELETE' });
        if (state.selectedPaper?.id === paper.id) {
            state.selectedPaper = null;
            state.textData = null;
            state.audioSegments = [];
            state.audioIndex = -1;
            els.paperView.hidden = true;
            els.empty.hidden = false;
            els.breadcrumb.textContent = '论文库 / 未选择论文';
        }
        await refreshAll();
        toast('论文及其本地资源已删除');
    } catch (error) { toast(error.message, true); }
}

async function refreshJobs() {
    try {
        const data = await api('/api/jobs');
        const previous = new Map(state.jobs.map((job) => [job.id, job.status]));
        state.jobs = data.jobs || [];
        renderFolders();
        if (!els.jobsModal.hidden) renderJobs();
        const newlyFinished = state.jobs.some((job) => ['completed', 'completed_with_errors'].includes(job.status) && ['queued', 'running', 'paused'].includes(previous.get(job.id)));
        if (newlyFinished) {
            await refreshPapers();
            if (state.selectedPaper) await openPaper(state.selectedPaper.id);
        }
    } catch (_) {}
}

function renderJobs() {
    if (!state.jobs.length) { els.jobsList.innerHTML = '<div class="empty-list">暂无后台任务</div>'; return; }
    const paperNames = new Map(state.papers.map((paper) => [paper.id, paper.title]));
    els.jobsList.innerHTML = state.jobs.map((job) => {
        const progress = Math.round(Number(job.progress || 0) * 100);
        const active = ['queued', 'running', 'paused'].includes(job.status);
        const pauseAction = job.status === 'paused' ? 'resume' : 'pause';
        const pauseText = job.status === 'paused' ? '继续' : '暂停';
        const retry = job.status === 'completed_with_errors' ? `<button data-job-action="retry" data-job-id="${job.id}">重试失败片段</button>` : '';
        return `<div class="job-item"><div class="job-row"><div class="job-info"><div class="job-title">${escapeHtml(paperNames.get(job.paper_id) || job.paper_id)}</div><div class="job-meta">${escapeHtml(job.message || '等待处理')} · ${progress}%</div></div><span class="job-status">${escapeHtml(job.status)}</span></div><div class="job-progress"><span style="width:${progress}%"></span></div><div class="job-actions">${active ? `<button data-job-action="${pauseAction}" data-job-id="${job.id}">${pauseText}</button><button data-job-action="stop" data-job-id="${job.id}">停止</button>` : ''}${retry}<button data-job-action="logs" data-job-id="${job.id}">Logs</button><button class="danger-action" data-job-action="delete" data-job-id="${job.id}">删除记录</button></div></div>`;
    }).join('');
    els.jobsList.querySelectorAll('[data-job-action]').forEach((button) => button.addEventListener('click', () => handleJobAction(button.dataset.jobId, button.dataset.jobAction)));
}

const RESOURCE_LABELS = { text: '文本版本', audio: '语音包', job: '后台任务日志', backup: '论文备份', voice: '声音克隆音源' };

function formatResourceSize(bytes) {
    const size = Number(bytes || 0);
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
    return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function renderResources(data) {
    const resources = data.resources || [];
    const grouped = new Map();
    resources.forEach((item) => {
        if (!grouped.has(item.resource_type)) grouped.set(item.resource_type, []);
        grouped.get(item.resource_type).push(item);
    });
    if (!resources.length) {
        els.resourceGroups.innerHTML = '<div class="empty-list">当前没有可管理的资源</div>';
    } else {
        els.resourceGroups.innerHTML = [...grouped.entries()].map(([resourceType, entries]) => `<section class="resource-group"><h3>${escapeHtml(RESOURCE_LABELS[resourceType] || resourceType)} <span>${entries.length}</span></h3><div class="resource-list">${entries.map((item) => {
            const type = RESOURCE_LABELS[item.resource_type] || item.resource_type;
            const paper = item.paper_title ? ` · ${item.paper_title}` : '';
            const protectedText = item.protected ? '已保护 · 点击解锁' : '可清理 · 点击保护';
            const disabled = item.can_delete ? '' : ' disabled';
            const status = item.resource_type === 'job' ? `${item.status || ''} · ${item.log_count || 0} 条日志` : formatResourceSize(item.size);
            return `<div class="resource-item"><div class="resource-item-main"><div class="resource-title">${escapeHtml(type)} · ${escapeHtml(item.label || item.resource_id)}</div><div class="resource-meta">${escapeHtml(paper)}${escapeHtml(status ? ` · ${status}` : '')}</div></div><div class="resource-actions"><button class="resource-protect ${item.protected ? 'is-protected' : ''}" data-resource-action="protect" data-resource-type="${escapeHtml(item.resource_type)}" data-resource-id="${escapeHtml(item.resource_id)}" data-protected="${item.protected ? 'true' : 'false'}">${protectedText}</button><button class="danger-action" data-resource-action="delete" data-resource-type="${escapeHtml(item.resource_type)}" data-resource-id="${escapeHtml(item.resource_id)}"${disabled}>清理</button></div></div>`;
        }).join('')}</div></section>`).join('');
    }
    els.resourceGroups.querySelectorAll('[data-resource-action]').forEach((button) => button.addEventListener('click', () => handleResourceAction(button.dataset.resourceType, button.dataset.resourceId, button.dataset.resourceAction, button.dataset.protected === 'true')));
}

function renderRecycleBin(data) {
    const recycle = data.recycle_bin || [];
    els.recycleList.innerHTML = recycle.length ? recycle.map((item) => `<div class="resource-item recycle-item"><div class="resource-item-main"><div class="resource-title">${escapeHtml(RESOURCE_LABELS[item.resource_type] || item.resource_type)} · ${escapeHtml(item.label)}</div><div class="resource-meta">删除于 ${escapeHtml(new Date(item.deleted_at).toLocaleString())} · ${item.days_left} 天后自动清除</div></div><div class="resource-actions"><button class="secondary-button" data-recycle-action="restore" data-recycle-id="${escapeHtml(item.id)}">恢复</button><button class="danger-action" data-recycle-action="purge" data-recycle-id="${escapeHtml(item.id)}">立即清除</button></div></div>`).join('') : '<div class="empty-list">回收站为空</div>';
    els.recycleList.querySelectorAll('[data-recycle-action]').forEach((button) => button.addEventListener('click', () => handleRecycleAction(button.dataset.recycleId, button.dataset.recycleAction)));
}

async function refreshResources() {
    try {
        const data = await api('/api/resources');
        renderResources(data);
        els.resourceStatus.textContent = `共 ${data.resources?.length || 0} 项当前资源`;
    } catch (error) { els.resourceStatus.textContent = error.message; }
}

async function refreshRecycleBin() {
    try {
        const data = await api('/api/recycle-bin');
        renderRecycleBin(data);
        els.recycleStatus.textContent = `${data.recycle_bin?.length || 0} 项待处理资源`;
    } catch (error) { els.recycleStatus.textContent = error.message; }
}

async function handleResourceAction(resourceType, resourceId, action, isProtected) {
    try {
        if (action === 'protect') {
            await api(`/api/resources/${encodeURIComponent(resourceType)}/${encodeURIComponent(resourceId)}/protection`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ protected: !isProtected }) });
            toast(isProtected ? '资源已解锁，可清理' : '资源已保护');
        } else {
            if (!window.confirm('确定将这项资源移入回收站吗？30 天内仍可恢复。')) return;
            await api(`/api/resources/${encodeURIComponent(resourceType)}/${encodeURIComponent(resourceId)}`, { method: 'DELETE' });
            toast('资源已移入回收站');
            await refreshAll();
        }
        await refreshResources();
    } catch (error) { toast(error.message, true); }
}

async function cleanupResources() {
    if (!window.confirm('确定一键清除所有当前可清理资源吗？资源会先移入回收站，30 天内仍可恢复。')) return;
    try {
        const result = await api('/api/resources/cleanup', { method: 'POST' });
        const skipped = result.skipped?.length || 0;
        toast(`已移入回收站 ${result.count || 0} 项${skipped ? `，跳过 ${skipped} 项` : ''}`);
        await refreshAll();
        await refreshResources();
    } catch (error) { toast(error.message, true); }
}

async function handleRecycleAction(entryId, action) {
    try {
        if (action === 'purge' && !window.confirm('立即清除后将无法恢复，确定继续吗？')) return;
        await api(`/api/recycle-bin/${encodeURIComponent(entryId)}${action === 'restore' ? '/restore' : ''}`, { method: action === 'restore' ? 'POST' : 'DELETE' });
        toast(action === 'restore' ? '资源已恢复' : '资源已永久清除');
        await refreshAll();
        await refreshResources();
        await refreshRecycleBin();
    } catch (error) { toast(error.message, true); }
}

async function handleJobAction(jobId, action) {
    try {
        if (action === 'logs') {
            const data = await api(`/api/jobs/${jobId}/logs`);
            els.logsContent.textContent = (data.logs || []).map((item) => `[${item.created_at}] ${String(item.level).toUpperCase()} ${item.message}`).join('\n') || '暂无日志';
            els.logsModal.hidden = false;
            return;
        }
        if (action === 'delete') await api(`/api/jobs/${jobId}`, { method: 'DELETE' });
        else if (action === 'retry') {
            const settings = loadSettings();
            await api(`/api/jobs/${jobId}/retry`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: settings.qwenApiKey || '' }) });
        } else await api(`/api/jobs/${jobId}/${action}`, { method: 'POST' });
        await refreshJobs();
    } catch (error) { toast(error.message, true); }
}

els.importButton.addEventListener('click', openImportDialog);
els.emptyImport.addEventListener('click', openImportDialog);
els.closeImport.addEventListener('click', closeImportDialog);
els.choosePdf.addEventListener('click', () => els.pdfInput.click());
els.importModal.addEventListener('click', (event) => { if (event.target === els.importModal) closeImportDialog(); });
els.pdfInput.addEventListener('change', () => {
    const file = els.pdfInput.files[0];
    const folderId = els.importFolder.value || null;
    if (file) { closeImportDialog(); importPaper(file, folderId).catch((error) => toast(error.message, true)); }
    els.pdfInput.value = '';
});
els.save.addEventListener('click', saveText);
els.audio.addEventListener('click', generateAudio);
els.textVersion.addEventListener('change', () => loadTextVersion(els.textVersion.value).catch((error) => toast(`切换文本版本失败：${error.message}`, true)));
els.manualText.addEventListener('click', () => els.manualTextInput.click());
els.manualTextInput.addEventListener('change', () => { const file = els.manualTextInput.files[0]; if (file) openManualKnowledgeDialog(file); els.manualTextInput.value = ''; });
els.audioPackage.addEventListener('change', async () => {
    const selected = state.audioPackages.find((item) => item.id === els.audioPackage.value);
    if (!selected) return;
    state.selectedPackageId = selected.id;
    if (selected.text_version_id && selected.text_version_id !== state.textData?.version?.id) {
        try { await loadTextVersion(selected.text_version_id, false); } catch (error) { toast(`加载语音对应文本失败：${error.message}`, true); }
    }
    state.audioSegments = selected.segments || [];
    state.audioIndex = -1;
    els.player.pause();
    els.player.removeAttribute('src');
    updatePlayerButtons();
});
els.favoriteButton.addEventListener('click', toggleFavorite);
els.favoritesToggle.addEventListener('click', () => { state.favoritesExpanded = !state.favoritesExpanded; renderFavorites(); });
els.closeAudio.addEventListener('click', () => { els.audioModal.hidden = true; });
els.audioModal.addEventListener('click', (event) => { if (event.target === els.audioModal) els.audioModal.hidden = true; });
els.startAudio.addEventListener('click', startAudioGeneration);
els.importAudio.addEventListener('click', () => els.audioPackageInput.click());
els.audioPackageInput.addEventListener('change', importAudioPackages);
els.audioProvider.addEventListener('change', updateAudioProviderUI);
els.localBridgeHost.addEventListener('input', updateAudioProviderUI);
els.localBridgePort.addEventListener('input', updateAudioProviderUI);
els.testLocalBridge.addEventListener('click', testLocalBridge);
els.confirmLocalBridge.addEventListener('click', confirmLocalBridge);
els.optimize.addEventListener('click', optimizeText);
els.closeOptimize.addEventListener('click', closeOptimizeDialog);
els.optimizeModal.addEventListener('click', (event) => { if (event.target === els.optimizeModal) closeOptimizeDialog(); });
els.visionExtract.addEventListener('click', runVisionExtraction);
els.readingOptimize.addEventListener('click', openReadingConfirmDialog);
els.closeReadingConfirm.addEventListener('click', closeReadingConfirmDialog);
els.cancelReadingConfirm.addEventListener('click', closeReadingConfirmDialog);
els.confirmReadingOptimize.addEventListener('click', () => { closeReadingConfirmDialog(); runReadingOptimization(); });
els.readingConfirmModal.addEventListener('click', (event) => { if (event.target === els.readingConfirmModal) closeReadingConfirmDialog(); });
els.knowledgeHint.addEventListener('click', openKnowledgeEditor);
els.closeManualKb.addEventListener('click', () => { els.manualKbModal.hidden = true; resetKnowledgeDialog(); });
els.manualKbCancel.addEventListener('click', () => { els.manualKbModal.hidden = true; resetKnowledgeDialog(); });
els.manualKbModal.addEventListener('click', (event) => { if (event.target === els.manualKbModal) { els.manualKbModal.hidden = true; resetKnowledgeDialog(); } });
els.manualKbNo.addEventListener('click', () => {
    const file = state.pendingManualFile;
    els.manualKbModal.hidden = true;
    resetKnowledgeDialog();
    if (file) importManualText(file);
});
els.manualKbYes.addEventListener('click', () => {
    els.manualKbChoice.hidden = true;
    els.manualKbFields.hidden = false;
    els.manualKbSubmit.hidden = false;
    els.manualKbIntro.textContent = '请填写论文的标题、作者和摘要信息。';
});
els.manualKbSubmit.addEventListener('click', submitKnowledgeDialog);
els.settingsButton.addEventListener('click', openSettings);
els.openJobs.addEventListener('click', async () => { closeSettings(); els.jobsModal.hidden = false; await refreshJobs(); renderJobs(); });
els.openResources.addEventListener('click', async () => { closeSettings(); els.resourcesModal.hidden = false; await refreshResources(); });
els.openRecycle.addEventListener('click', async () => { closeSettings(); els.recycleModal.hidden = false; await refreshRecycleBin(); });
els.closeResources.addEventListener('click', () => { els.resourcesModal.hidden = true; });
els.refreshResources.addEventListener('click', refreshResources);
els.cleanupResources.addEventListener('click', cleanupResources);
els.resourcesModal.addEventListener('click', (event) => { if (event.target === els.resourcesModal) els.resourcesModal.hidden = true; });
els.closeRecycle.addEventListener('click', () => { els.recycleModal.hidden = true; });
els.refreshRecycle.addEventListener('click', refreshRecycleBin);
els.recycleModal.addEventListener('click', (event) => { if (event.target === els.recycleModal) els.recycleModal.hidden = true; });
els.closeJobs.addEventListener('click', () => { els.jobsModal.hidden = true; });
els.jobsModal.addEventListener('click', (event) => { if (event.target === els.jobsModal) els.jobsModal.hidden = true; });
els.closeLogs.addEventListener('click', () => { els.logsModal.hidden = true; });
els.logsModal.addEventListener('click', (event) => { if (event.target === els.logsModal) els.logsModal.hidden = true; });
els.closeSettings.addEventListener('click', closeSettings);
els.settingsModal.addEventListener('click', (event) => { if (event.target === els.settingsModal) closeSettings(); });
els.saveSettings.addEventListener('click', async () => {
    const settings = readSettingsForm();
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    applyReadingSettings(settings);
    try { await api('/api/backups/settings', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ interval_days: settings.backupIntervalDays }) }); } catch (error) { toast(`自动备份设置保存失败：${error.message}`, true); return; }
    closeSettings();
    toast('设置已保存');
});
els.resetSettings.addEventListener('click', async () => {
    localStorage.removeItem(SETTINGS_KEY);
    renderSettingsForm(DEFAULT_SETTINGS);
    applyReadingSettings(DEFAULT_SETTINGS);
    try { await api('/api/backups/settings', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ interval_days: 30 }) }); } catch (_) {}
    toast('已恢复默认设置');
});
els.exportBackup.addEventListener('click', exportLibraryBackup);
els.importBackup.addEventListener('click', () => els.backupFile.click());
els.backupFile.addEventListener('change', () => importLibraryBackup(els.backupFile.files[0]));
els.qwenVoiceMode.addEventListener('change', () => { const clone = els.qwenVoiceMode.value === 'clone'; els.defaultVoiceField.hidden = clone; els.cloneVoiceField.hidden = !clone; });
els.qwenModel.addEventListener('change', () => { updateQwenVoiceModes(); refreshQwenVoices(); refreshCloneVoices(); });
document.querySelectorAll('.fetch-models').forEach((button) => button.addEventListener('click', () => fetchProviderModels(button)));
els.submitClone.addEventListener('click', submitCloneVoice);
els.cloneVoiceSelect.addEventListener('change', () => {
    const option = els.cloneVoiceSelect.options[els.cloneVoiceSelect.selectedIndex];
    els.cloneVoiceId = els.cloneVoiceSelect.value || '';
    els.cloneVoiceModel = option?.dataset?.model || '';
    els.cloneVoiceName = option?.dataset?.name || '';
});
els.playbackRate.addEventListener('change', () => { els.player.playbackRate = Number(els.playbackRate.value); });
document.addEventListener('click', hideContextMenu);
document.addEventListener('scroll', hideContextMenu, true);
els.search.addEventListener('input', () => refreshPapers().catch((error) => toast(error.message, true)));
els.tabs.forEach((tab) => tab.addEventListener('click', () => {
    els.tabs.forEach((item) => item.classList.toggle('active', item === tab));
    state.view = tab.dataset.view;
    els.text.hidden = state.view !== 'text';
    els.original.hidden = state.view !== 'original';
    if (state.view === 'original' && state.selectedPaper) els.original.src = `/api/papers/${state.selectedPaper.id}/original`;
}));

els.player.addEventListener('ended', () => { if (state.audioIndex + 1 < state.audioSegments.length) jumpToSegment(state.audioIndex + 1); updatePlayButton(); });
els.player.addEventListener('play', updatePlayButton);
els.player.addEventListener('pause', updatePlayButton);
els.player.addEventListener('timeupdate', () => { if (els.player.duration) els.track.style.width = `${(els.player.currentTime / els.player.duration) * 100}%`; });
els.play.addEventListener('click', () => { if (els.player.paused) els.player.play().catch(() => {}); else els.player.pause(); updatePlayButton(); });
els.previous.addEventListener('click', () => jumpToSegment(state.audioIndex - 1));
els.next.addEventListener('click', () => jumpToSegment(state.audioIndex + 1));

renderSettingsForm(loadSettings());
applyReadingSettings(loadSettings());
els.player.playbackRate = Number(els.playbackRate.value);
refreshAll().then(refreshJobs);
window.setInterval(refreshJobs, 2000);
