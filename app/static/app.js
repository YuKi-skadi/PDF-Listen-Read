// State
const state = {
    docId: null,
    chunks: [],
    currentIndex: 0,
    isPlaying: false,
    isPaused: false,
    isGenerating: false,
    audioUrls: {},
    speed: 1.0,
    useLLM: false,
    inputMode: 'pdf', // 'pdf' or 'text'
    pendingFile: null,
    preloadNext: null,
    abortController: null
};

// DOM Elements
const elements = {
    // Config
    apiKey: document.getElementById('api-key'),
    apiBase: document.getElementById('api-base'),
    fetchModelsBtn: document.getElementById('fetch-models-btn'),
    ttsModel: document.getElementById('tts-model'),
    llmModel: document.getElementById('llm-model'),
    voice: document.getElementById('voice'),
    ttsMode: document.getElementById('tts-mode'),
    stopGenBtn: document.getElementById('stop-gen-btn'),
    downloadFullBtn: document.getElementById('download-full-btn'),
    
    // Input
    pdfInput: document.getElementById('pdf-input'),
    uploadArea: document.getElementById('upload-area'),
    uploadPlaceholder: document.getElementById('upload-placeholder'),
    uploadSuccess: document.getElementById('upload-success'),
    filenameDisplay: document.getElementById('filename-display'),
    changeFileBtn: document.getElementById('change-file-btn'),
    textInput: document.getElementById('text-input'),
    processBtn: document.getElementById('process-btn'),
    tabBtns: document.querySelectorAll('.tab-btn'),
    
    // Playback
    progressText: document.getElementById('progress-text'),
    progressFill: document.getElementById('progress-fill'),
    prevBtn: document.getElementById('prev-btn'),
    playBtn: document.getElementById('play-btn'),
    nextBtn: document.getElementById('next-btn'),
    stopBtn: document.getElementById('stop-btn'),
    playIcon: document.getElementById('play-icon'),
    pauseIcon: document.getElementById('pause-icon'),
    speedSlider: document.getElementById('speed-slider'),
    speedValue: document.getElementById('speed-value'),
    
    // Content
    contentTitle: document.getElementById('content-title'),
    textContent: document.getElementById('text-content'),
    emptyState: document.getElementById('empty-state'),
    llmToggle: document.getElementById('llm-toggle'),
    
    // Status
    statusMessage: document.getElementById('status-message'),
    
    // Audio
    audioPlayer: document.getElementById('audio-player')
};

// Utility functions
function setStatus(message, type = '') {
    elements.statusMessage.textContent = message;
    elements.statusMessage.className = 'status-message ' + type;
}

function updateProgress() {
    const total = state.chunks.length;
    const current = state.currentIndex + 1;
    elements.progressText.textContent = `${current} / ${total} 段`;
    elements.progressFill.style.width = `${(current / total) * 100}%`;
}

function updateButtons() {
    const hasChunks = state.chunks.length > 0;
    elements.prevBtn.disabled = !hasChunks || state.currentIndex === 0;
    elements.nextBtn.disabled = !hasChunks || state.currentIndex >= state.chunks.length - 1;
    elements.playBtn.disabled = !hasChunks;
    elements.stopBtn.disabled = !hasChunks;
}

function highlightChunk(index) {
    document.querySelectorAll('.text-chunk').forEach((el, i) => {
        el.classList.remove('active');
        if (i < index) {
            el.classList.add('completed');
        } else {
            el.classList.remove('completed');
        }
    });
    
    const activeEl = document.querySelector(`.text-chunk[data-index="${index}"]`);
    if (activeEl) {
        activeEl.classList.add('active');
        activeEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

function renderChunks(chunks) {
    elements.textContent.innerHTML = '';
    chunks.forEach((chunk, index) => {
        const div = document.createElement('div');
        div.className = 'text-chunk';
        div.dataset.index = index;
        div.contentEditable = true;
        div.innerHTML = `<p>${chunk.text}</p>`;
        
        // Update state on edit
        div.addEventListener('input', (e) => {
            state.chunks[index].text = e.target.innerText;
        });
        
        div.addEventListener('click', () => jumpToChunk(index));
        elements.textContent.appendChild(div);
    });
}

// API functions
async function fetchModels() {
    setStatus('正在获取模型列表...', 'loading');
    
    try {
        const response = await fetch('/api/fetch-models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                api_key: elements.apiKey.value,
                api_base: elements.apiBase.value
            })
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '获取模型失败');
        }
        
        const data = await response.json();
        
        // Populate TTS models dropdown
        elements.ttsModel.innerHTML = '';
        if (data.tts_models.length > 0) {
            data.tts_models.forEach(model => {
                const option = document.createElement('option');
                option.value = model;
                option.textContent = model;
                elements.ttsModel.appendChild(option);
            });
            // Select first TTS model by default
            elements.ttsModel.value = data.tts_models[0];
            // Fetch voices for selected model
            await fetchVoices(elements.ttsModel.value);
        } else {
            elements.ttsModel.innerHTML = '<option value="cosyvoice-v2">cosyvoice-v2</option>';
        }
        
        // Populate LLM models dropdown
        elements.llmModel.innerHTML = '';
        if (data.llm_models.length > 0) {
            data.llm_models.forEach(model => {
                const option = document.createElement('option');
                option.value = model;
                option.textContent = model;
                elements.llmModel.appendChild(option);
            });
            elements.llmModel.value = data.llm_models[0];
        } else {
            elements.llmModel.innerHTML = '<option value="qwen-turbo">qwen-turbo</option>';
        }
        
        setStatus(`获取成功: ${data.tts_models.length} 个 TTS 模型, ${data.llm_models.length} 个 LLM 模型`, 'success');
        
        // Apply saved model selections
        applySavedModels();
    } catch (error) {
        setStatus(`错误: ${error.message}`, 'error');
        throw error;
    }
}

async function fetchVoices(model) {
    if (!model) return;
    
    try {
        const response = await fetch(`/api/tts-voices?model=${encodeURIComponent(model)}`);
        if (!response.ok) throw new Error('获取语音列表失败');
        
        const data = await response.json();
        
        // Populate voices dropdown
        elements.voice.innerHTML = '';
        data.voices.forEach(voice => {
            const option = document.createElement('option');
            option.value = voice;
            option.textContent = voice;
            elements.voice.appendChild(option);
        });
        
        if (data.voices.length > 0) {
            elements.voice.value = data.voices[0];
        }
    } catch (error) {
        console.error('Failed to fetch voices:', error);
    }
}

async function uploadPDF(file) {
    setStatus('正在处理 PDF...', 'loading');
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        const response = await fetch('/api/upload-pdf', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '上传失败');
        }
        
        const data = await response.json();
        state.docId = data.doc_id;
        state.chunks = [];
        
        setStatus(`PDF 处理完成: ${data.page_count} 页, ${data.chunk_count} 段`, 'success');
        return data;
    } catch (error) {
        setStatus(`错误: ${error.message}`, 'error');
        throw error;
    }
}

async function loadDocument(docId) {
    try {
        const response = await fetch(`/api/document/${docId}`);
        if (!response.ok) throw new Error('加载文档失败');
        
        const data = await response.json();
        state.chunks = data.chunks;
        state.currentIndex = 0;
        
        renderChunks(state.chunks);
        elements.textContent.style.display = 'block';
        elements.emptyState.style.display = 'none';
        elements.contentTitle.textContent = data.filename;
        
        updateProgress();
        updateButtons();
        highlightChunk(0);
    } catch (error) {
        setStatus(`错误: ${error.message}`, 'error');
    }
}

async function generateTTS(chunkIndex) {
    const chunk = state.chunks[chunkIndex];
    if (!chunk) return null;
    
    // Check cache
    if (state.audioUrls[chunkIndex]) {
        return state.audioUrls[chunkIndex];
    }
    
    setStatus(`正在生成语音 (${chunkIndex + 1}/${state.chunks.length})...`, 'loading');
    state.isGenerating = true;
    elements.stopGenBtn.style.display = 'inline-block';
    
    const isLocal = elements.ttsMode.value === 'local';
    
    // Create new AbortController for this request
    state.abortController = new AbortController();
    
    try {
        const response = await fetch('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                text: chunk.text,
                doc_id: state.docId,
                api_key: elements.apiKey.value,
                api_base: elements.apiBase.value,
                model: elements.ttsModel.value,
                voice: elements.voice.value,
                use_local_tts: isLocal
            }),
            signal: state.abortController.signal
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'TTS 生成失败');
        }
        
        const data = await response.json();
        state.audioUrls[chunkIndex] = data.audio_url;
        return data.audio_url;
    } catch (error) {
        if (error.name === 'AbortError') {
            setStatus('生成已停止', 'success');
            return null;
        }
        setStatus(`TTS 错误: ${error.message}`, 'error');
        throw error;
    } finally {
        state.isGenerating = false;
        state.abortController = null;
        elements.stopGenBtn.style.display = 'none';
    }
}

// Preload next chunk audio
function preloadNextChunk(index) {
    if (index + 1 < state.chunks.length && !state.audioUrls[index + 1]) {
        generateTTS(index + 1).then(() => {
            console.log(`Preloaded chunk ${index + 1}`);
        }).catch(e => console.warn('Preload failed:', e));
    }
}

// Playback functions
async function playChunk(index) {
    if (index >= state.chunks.length) {
        stopPlayback();
        elements.downloadFullBtn.style.display = 'inline-block';
        setStatus('播放完成', 'success');
        return;
    }
    
    state.currentIndex = index;
    highlightChunk(index);
    updateProgress();
    updateButtons();
    
    try {
        const audioUrl = await generateTTS(index);
        if (!audioUrl) return;
        
        elements.audioPlayer.src = audioUrl;
        elements.audioPlayer.playbackRate = state.speed;
        await elements.audioPlayer.play();
        
        state.isPlaying = true;
        state.isPaused = false;
        updatePlayButton();
        
        // Preload next chunk
        preloadNextChunk(index);
    } catch (error) {
        stopPlayback();
    }
}

function playNext() {
    if (state.currentIndex < state.chunks.length - 1) {
        playChunk(state.currentIndex + 1);
    } else {
        stopPlayback();
    }
}

function playPrev() {
    if (state.currentIndex > 0) {
        playChunk(state.currentIndex - 1);
    }
}

function startPlayback() {
    if (state.chunks.length === 0) return;
    playChunk(state.currentIndex);
}

function pausePlayback() {
    elements.audioPlayer.pause();
    state.isPaused = true;
    updatePlayButton();
}

function resumePlayback() {
    elements.audioPlayer.play();
    state.isPaused = false;
    updatePlayButton();
}

function stopPlayback() {
    elements.audioPlayer.pause();
    elements.audioPlayer.currentTime = 0;
    state.isPlaying = false;
    state.isPaused = false;
    updatePlayButton();
    updateButtons();
}

function jumpToChunk(index) {
    const wasPlaying = state.isPlaying;
    stopPlayback();
    state.currentIndex = index;
    highlightChunk(index);
    updateProgress();
    updateButtons();
    
    if (wasPlaying) {
        startPlayback();
    }
}

function updatePlayButton() {
    if (state.isPlaying && !state.isPaused) {
        elements.playIcon.style.display = 'none';
        elements.pauseIcon.style.display = 'block';
    } else {
        elements.playIcon.style.display = 'block';
        elements.pauseIcon.style.display = 'none';
    }
}

// Event handlers
function handlePlayClick() {
    if (!state.isPlaying) {
        startPlayback();
    } else if (state.isPaused) {
        resumePlayback();
    } else {
        pausePlayback();
    }
}

// Tab switching
elements.tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
        elements.tabBtns.forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        
        btn.classList.add('active');
        state.inputMode = btn.dataset.tab;
        document.getElementById(`${state.inputMode}-tab`).classList.add('active');
        
        updateProcessButtonState();
    });
});

function updateProcessButtonState() {
    if (state.inputMode === 'pdf') {
        elements.processBtn.disabled = !state.pendingFile;
    } else {
        elements.processBtn.disabled = !elements.textInput.value.trim();
    }
}

elements.textInput.addEventListener('input', updateProcessButtonState);

// Audio events
elements.audioPlayer.addEventListener('ended', () => {
    playNext();
});

elements.audioPlayer.addEventListener('error', () => {
    setStatus('音频播放错误', 'error');
    stopPlayback();
});

// Upload area click
elements.uploadArea.addEventListener('click', (e) => {
    if (e.target !== elements.changeFileBtn) {
        elements.pdfInput.click();
    }
});

// File input change
elements.pdfInput.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    
    elements.uploadPlaceholder.style.display = 'none';
    elements.uploadSuccess.style.display = 'block';
    elements.filenameDisplay.textContent = file.name;
    elements.processBtn.disabled = false;
    
    // Store file for processing
    state.pendingFile = file;
});

// Drag and drop
elements.uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    elements.uploadArea.classList.add('dragover');
});

elements.uploadArea.addEventListener('dragleave', () => {
    elements.uploadArea.classList.remove('dragover');
});

elements.uploadArea.addEventListener('drop', async (e) => {
    e.preventDefault();
    elements.uploadArea.classList.remove('dragover');
    
    const file = e.dataTransfer.files[0];
    if (file && file.type === 'application/pdf') {
        elements.pdfInput.files = e.dataTransfer.files;
        elements.uploadPlaceholder.style.display = 'none';
        elements.uploadSuccess.style.display = 'block';
        elements.filenameDisplay.textContent = file.name;
        elements.processBtn.disabled = false;
        state.pendingFile = file;
    }
});

// Change file button
elements.changeFileBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    elements.pdfInput.click();
});

// Process button
elements.processBtn.addEventListener('click', async () => {
    elements.processBtn.disabled = true;
    
    try {
        if (state.inputMode === 'pdf') {
            if (!state.pendingFile) return;
            await uploadPDF(state.pendingFile);
        } else {
            const text = elements.textInput.value.trim();
            if (!text) return;
            await processTextInput(text);
        }
        await loadDocument(state.docId);
    } catch (error) {
        elements.processBtn.disabled = false;
    }
});

async function processTextInput(text) {
    setStatus('正在处理文本...', 'loading');
    
    try {
        const response = await fetch('/api/process-text', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '处理文本失败');
        }
        
        const data = await response.json();
        state.docId = data.doc_id;
        state.chunks = [];
        
        setStatus(`文本处理完成: ${data.chunk_count} 段`, 'success');
    } catch (error) {
        setStatus(`错误: ${error.message}`, 'error');
        throw error;
    }
}

// Fetch models button
elements.fetchModelsBtn.addEventListener('click', fetchModels);

// Stop generation button
elements.stopGenBtn.addEventListener('click', () => {
    if (state.abortController) {
        state.abortController.abort();
    }
    state.isGenerating = false;
    elements.stopGenBtn.style.display = 'none';
});

// Download full audio button
elements.downloadFullBtn.addEventListener('click', async () => {
    if (!state.docId) return;
    setStatus('正在合并音频...', 'loading');
    try {
        const response = await fetch(`/api/download-full-audio/${state.docId}`);
        if (!response.ok) throw new Error('下载失败');
        
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${state.docId}_full_audio.mp3`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        setStatus('下载已开始', 'success');
    } catch (error) {
        setStatus(`下载错误: ${error.message}`, 'error');
    }
});

// TTS mode change
elements.ttsMode.addEventListener('change', async (e) => {
    if (e.target.value === 'local') {
        // Load edge-tts voices
        elements.ttsModel.innerHTML = '<option value="edge-tts">edge-tts (本地)</option>';
        elements.voice.innerHTML = '';
        const localVoices = ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-YunjianNeural", "zh-CN-XiaoyiNeural"];
        localVoices.forEach(v => {
            const opt = document.createElement('option');
            opt.value = v;
            opt.textContent = v;
            elements.voice.appendChild(opt);
        });
        elements.voice.value = localVoices[0];
    } else {
        // Restore cloud models
        await fetchModels();
    }
});

// TTS model change - fetch voices (only in cloud mode)
elements.ttsModel.addEventListener('change', async (e) => {
    if (elements.ttsMode.value === 'cloud') {
        await fetchVoices(e.target.value);
    }
});

// Playback controls
elements.playBtn.addEventListener('click', handlePlayClick);
elements.prevBtn.addEventListener('click', playPrev);
elements.nextBtn.addEventListener('click', playNext);
elements.stopBtn.addEventListener('click', stopPlayback);

// Speed slider
elements.speedSlider.addEventListener('input', (e) => {
    state.speed = parseFloat(e.target.value);
    elements.speedValue.textContent = state.speed.toFixed(1) + 'x';
    elements.audioPlayer.playbackRate = state.speed;
});

// LLM toggle
elements.llmToggle.addEventListener('change', (e) => {
    state.useLLM = e.target.checked;
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    
    switch (e.code) {
        case 'Space':
            e.preventDefault();
            handlePlayClick();
            break;
        case 'ArrowLeft':
            playPrev();
            break;
        case 'ArrowRight':
            playNext();
            break;
        case 'KeyS':
            stopPlayback();
            break;
    }
});

// Load saved config from localStorage
function loadConfig() {
    const saved = localStorage.getItem('pdf-listen-config');
    if (saved) {
        try {
            const config = JSON.parse(saved);
            elements.apiKey.value = config.apiKey || '';
            elements.apiBase.value = config.apiBase || elements.apiBase.value;
            elements.ttsMode.value = config.ttsMode || 'local';
            
            // Store model preferences for after fetch
            state.savedTtsModel = config.ttsModel;
            state.savedLlmModel = config.llmModel;
            state.savedVoice = config.voice;
        } catch (e) {}
    }
    
    // Initialize local TTS voices if selected by default
    if (elements.ttsMode.value === 'local') {
        elements.ttsModel.innerHTML = '<option value="edge-tts">edge-tts (本地)</option>';
        elements.voice.innerHTML = '';
        const localVoices = ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-YunjianNeural", "zh-CN-XiaoyiNeural"];
        localVoices.forEach(v => {
            const opt = document.createElement('option');
            opt.value = v;
            opt.textContent = v;
            elements.voice.appendChild(opt);
        });
        elements.voice.value = localVoices[0];
    }
}

// Save config to localStorage
function saveConfig() {
    const config = {
        apiKey: elements.apiKey.value,
        apiBase: elements.apiBase.value,
        ttsMode: elements.ttsMode.value,
        ttsModel: elements.ttsModel.value,
        llmModel: elements.llmModel.value,
        voice: elements.voice.value
    };
    localStorage.setItem('pdf-listen-config', JSON.stringify(config));
}

// Apply saved model selections after fetch
function applySavedModels() {
    if (state.savedTtsModel) {
        const option = elements.ttsModel.querySelector(`option[value="${state.savedTtsModel}"]`);
        if (option) elements.ttsModel.value = state.savedTtsModel;
    }
    if (state.savedLlmModel) {
        const option = elements.llmModel.querySelector(`option[value="${state.savedLlmModel}"]`);
        if (option) elements.llmModel.value = state.savedLlmModel;
    }
    if (state.savedVoice) {
        const option = elements.voice.querySelector(`option[value="${state.savedVoice}"]`);
        if (option) elements.voice.value = state.savedVoice;
    }
}

// Save config on change
[elements.apiKey, elements.apiBase, elements.ttsMode, elements.ttsModel, elements.llmModel, elements.voice].forEach(el => {
    el.addEventListener('change', saveConfig);
});

// Initialize
loadConfig();
