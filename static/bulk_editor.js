(() => {
    const ACCEPTED_IMAGE_TYPES = new Set(['image/png', 'image/jpeg']);
    const ACCEPTED_IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg']);
    const ACCEPTED_ZIP_TYPES = new Set(['application/zip', 'application/x-zip-compressed', 'multipart/x-zip']);
    const FONT_CSS = {
        'arial.ttf': 'Arial, Helvetica, sans-serif',
        'georgia.ttf': 'Georgia, serif',
        'times.ttf': '"Times New Roman", Times, serif',
    };

    const state = {
        sampleFile: null,
        sampleImage: null,
        batchFiles: [],
        batchPreviewUrls: [],
        regions: [],
        selectedId: null,
        nextRegionId: 1,
        interaction: null,
    };

    const els = {
        sampleUpload: document.getElementById('sampleUpload'),
        sampleFile: document.getElementById('sampleFile'),
        sampleSummary: document.getElementById('sampleSummary'),
        batchUpload: document.getElementById('batchUpload'),
        batchFiles: document.getElementById('batchFiles'),
        batchSummary: document.getElementById('batchSummary'),
        batchPreviewGrid: document.getElementById('batchPreviewGrid'),
        canvas: document.getElementById('editorCanvas'),
        canvasPlaceholder: document.getElementById('canvasPlaceholder'),
        regionList: document.getElementById('regionList'),
        clearRegionsBtn: document.getElementById('clearRegionsBtn'),
        deleteRegionBtn: document.getElementById('deleteRegionBtn'),
        centerTextBtn: document.getElementById('centerTextBtn'),
        processBatchBtn: document.getElementById('processBatchBtn'),
        bulkProgress: document.getElementById('bulkProgress'),
        bulkStatus: document.getElementById('bulkStatus'),
        bulkFill: document.getElementById('bulkFill'),
        bulkDetail: document.getElementById('bulkDetail'),
        controls: {
            regionX: document.getElementById('regionX'),
            regionY: document.getElementById('regionY'),
            regionWidth: document.getElementById('regionWidth'),
            regionHeight: document.getElementById('regionHeight'),
            coverColor: document.getElementById('coverColor'),
            replacementText: document.getElementById('replacementText'),
            textFontSize: document.getElementById('textFontSize'),
            textFontFamily: document.getElementById('textFontFamily'),
            textColor: document.getElementById('textColor'),
            textX: document.getElementById('textX'),
            textY: document.getElementById('textY'),
        },
    };

    const ctx = els.canvas.getContext('2d');

    init();

    function init() {
        bindUploadZone(els.sampleUpload, els.sampleFile, files => loadSample(files[0]));
        bindUploadZone(els.batchUpload, els.batchFiles, files => loadBatch(files));

        els.canvas.addEventListener('pointerdown', onPointerDown);
        window.addEventListener('pointermove', onPointerMove);
        window.addEventListener('pointerup', onPointerUp);

        els.regionList.addEventListener('click', event => {
            const item = event.target.closest('[data-region-id]');
            if (!item) return;
            selectRegion(item.dataset.regionId);
        });

        els.clearRegionsBtn.addEventListener('click', () => {
            state.regions = [];
            state.selectedId = null;
            updateInterface();
        });

        els.deleteRegionBtn.addEventListener('click', deleteSelectedRegion);
        els.centerTextBtn.addEventListener('click', centerSelectedText);
        els.processBatchBtn.addEventListener('click', processBatch);

        bindRegionControlInputs();
        updateInterface();
    }

    function bindUploadZone(zone, input, onFiles) {
        zone.addEventListener('click', () => input.click());
        zone.addEventListener('dragover', event => {
            event.preventDefault();
            zone.classList.add('dragover');
        });
        zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
        zone.addEventListener('drop', event => {
            event.preventDefault();
            zone.classList.remove('dragover');
            onFiles(Array.from(event.dataTransfer.files || []));
            input.value = '';
        });
        input.addEventListener('change', event => {
            onFiles(Array.from(event.target.files || []));
            input.value = '';
        });
    }

    function getFileExtension(file) {
        const name = file.name || '';
        const dot = name.lastIndexOf('.');
        return dot >= 0 ? name.slice(dot).toLowerCase() : '';
    }

    function isImageFile(file) {
        return ACCEPTED_IMAGE_TYPES.has(file.type) || ACCEPTED_IMAGE_EXTENSIONS.has(getFileExtension(file));
    }

    function isZipFile(file) {
        return ACCEPTED_ZIP_TYPES.has(file.type) || getFileExtension(file) === '.zip';
    }

    function getValidImageFiles(files) {
        const valid = [];
        const rejected = [];

        files.forEach(file => {
            if (isImageFile(file)) valid.push(file);
            else rejected.push(file.name || 'Unsupported file');
        });

        if (rejected.length) {
            alert('Only PNG and JPG images are supported. Skipped: ' + rejected.join(', '));
        }

        return valid;
    }

    function getValidBatchFiles(files) {
        const valid = [];
        const rejected = [];

        files.forEach(file => {
            if (isImageFile(file) || isZipFile(file)) valid.push(file);
            else rejected.push(file.name || 'Unsupported file');
        });

        if (rejected.length) {
            alert('Only PNG, JPG, and ZIP files are supported. Skipped: ' + rejected.join(', '));
        }

        return valid;
    }

    function loadSample(file) {
        const valid = getValidImageFiles(file ? [file] : []);
        if (!valid.length) return;

        const sample = valid[0];
        const reader = new FileReader();
        reader.onerror = () => alert('Could not read the sample image.');
        reader.onload = event => {
            const image = new Image();
            image.onerror = () => alert('Could not load the sample image.');
            image.onload = () => {
                state.sampleFile = sample;
                state.sampleImage = image;
                state.regions = [];
                state.selectedId = null;
                state.nextRegionId = 1;
                els.canvas.width = image.naturalWidth;
                els.canvas.height = image.naturalHeight;
                els.canvas.style.display = 'block';
                els.canvasPlaceholder.style.display = 'none';
                els.sampleSummary.textContent = `${sample.name} - ${image.naturalWidth} x ${image.naturalHeight}px`;
                updateInterface();
            };
            image.src = event.target.result;
        };
        reader.readAsDataURL(sample);
    }

    function loadBatch(files) {
        state.batchFiles = getValidBatchFiles(files);
        renderBatchPreview();
        updateInterface();
    }

    function renderBatchPreview() {
        state.batchPreviewUrls.forEach(url => URL.revokeObjectURL(url));
        state.batchPreviewUrls = [];
        els.batchPreviewGrid.innerHTML = '';

        if (!state.batchFiles.length) {
            els.batchSummary.textContent = 'No batch images selected';
            return;
        }

        const imageCount = state.batchFiles.filter(isImageFile).length;
        const zipCount = state.batchFiles.filter(isZipFile).length;
        const summaryParts = [];
        if (imageCount) summaryParts.push(`${imageCount} image${imageCount === 1 ? '' : 's'}`);
        if (zipCount) summaryParts.push(`${zipCount} ZIP archive${zipCount === 1 ? '' : 's'}`);
        els.batchSummary.textContent = summaryParts.join(' and ') + ' selected';

        state.batchFiles.slice(0, 8).forEach(file => {
            const item = document.createElement('div');

            if (isZipFile(file)) {
                item.className = 'batch-thumb batch-thumb-zip';
                item.innerHTML = `<strong>ZIP</strong><span title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>`;
            } else {
                const url = URL.createObjectURL(file);
                state.batchPreviewUrls.push(url);
                item.className = 'batch-thumb';
                item.innerHTML = `<img src="${url}" alt=""><span title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>`;
            }

            els.batchPreviewGrid.appendChild(item);
        });

        if (state.batchFiles.length > 8) {
            const more = document.createElement('div');
            more.className = 'batch-thumb batch-thumb-more';
            more.textContent = `+${state.batchFiles.length - 8} more`;
            els.batchPreviewGrid.appendChild(more);
        }
    }

    function bindRegionControlInputs() {
        const controls = els.controls;

        controls.regionX.addEventListener('input', () => updateSelectedRegionFromControls('region'));
        controls.regionY.addEventListener('input', () => updateSelectedRegionFromControls('region'));
        controls.regionWidth.addEventListener('input', () => updateSelectedRegionFromControls('region'));
        controls.regionHeight.addEventListener('input', () => updateSelectedRegionFromControls('region'));
        controls.coverColor.addEventListener('input', () => updateSelectedRegionFromControls('cover'));
        controls.replacementText.addEventListener('input', () => updateSelectedRegionFromControls('text-soft'));
        controls.textFontSize.addEventListener('input', () => updateSelectedRegionFromControls('text'));
        controls.textFontFamily.addEventListener('change', () => updateSelectedRegionFromControls('text'));
        controls.textColor.addEventListener('input', () => updateSelectedRegionFromControls('text'));
        controls.textX.addEventListener('input', () => updateSelectedRegionFromControls('text'));
        controls.textY.addEventListener('input', () => updateSelectedRegionFromControls('text'));
    }

    function onPointerDown(event) {
        if (!state.sampleImage) return;
        event.preventDefault();

        const point = getCanvasPoint(event);
        const hit = hitTest(point);

        if (hit) {
            selectRegion(hit.region.id);
            state.interaction = {
                mode: hit.mode,
                handle: hit.handle,
                start: point,
                initial: cloneRegion(hit.region),
            };
        } else {
            const region = createRegion(point);
            state.regions.push(region);
            selectRegion(region.id);
            state.interaction = {
                mode: 'draw',
                start: point,
                initial: cloneRegion(region),
            };
        }

        els.canvas.setPointerCapture?.(event.pointerId);
        updateInterface();
    }

    function onPointerMove(event) {
        if (!state.interaction || !state.sampleImage) return;
        event.preventDefault();

        const point = getCanvasPoint(event);
        const selected = getSelectedRegion();
        if (!selected) return;

        if (state.interaction.mode === 'draw') {
            setRegionFromPoints(selected, state.interaction.start, point);
            selected.text.x = selected.region.x + 8;
            selected.text.y = selected.region.y + 8;
        } else if (state.interaction.mode === 'move') {
            moveRegion(selected, point);
        } else if (state.interaction.mode === 'resize') {
            resizeRegion(selected, point);
        }

        updateInterface();
    }

    function onPointerUp(event) {
        if (!state.interaction) return;

        const selected = getSelectedRegion();
        if (selected && (selected.region.width < 6 || selected.region.height < 6)) {
            state.regions = state.regions.filter(region => region.id !== selected.id);
            state.selectedId = null;
        }

        state.interaction = null;
        try {
            els.canvas.releasePointerCapture?.(event.pointerId);
        } catch (err) {
            // Pointer capture may already be released by the browser.
        }
        updateInterface();
    }

    function createRegion(point) {
        const x = Math.round(point.x);
        const y = Math.round(point.y);
        return {
            id: `region-${state.nextRegionId++}`,
            type: 'cover_text',
            region: { x, y, width: 1, height: 1 },
            cover: { color: '#ffffff' },
            text: {
                content: '',
                fontSize: 32,
                fontFamily: 'georgia.ttf',
                color: '#000000',
                x: x + 8,
                y: y + 8,
            },
        };
    }

    function cloneRegion(region) {
        return JSON.parse(JSON.stringify(region));
    }

    function getSelectedRegion() {
        return state.regions.find(region => region.id === state.selectedId) || null;
    }

    function selectRegion(id) {
        state.selectedId = id;
        updateInterface();
    }

    function deleteSelectedRegion() {
        if (!state.selectedId) return;
        state.regions = state.regions.filter(region => region.id !== state.selectedId);
        state.selectedId = state.regions[0]?.id || null;
        updateInterface();
    }

    function centerSelectedText() {
        const selected = getSelectedRegion();
        if (!selected) return;

        const lines = getTextLines(selected.text.content);
        const fontSize = selected.text.fontSize || 32;
        ctx.save();
        ctx.font = `${fontSize}px ${FONT_CSS[selected.text.fontFamily] || FONT_CSS['arial.ttf']}`;
        const textWidth = Math.max(0, ...lines.map(line => ctx.measureText(line).width));
        ctx.restore();

        const lineHeight = fontSize * 1.2;
        selected.text.x = Math.round(selected.region.x + Math.max(0, selected.region.width - textWidth) / 2);
        selected.text.y = Math.round(selected.region.y + Math.max(0, selected.region.height - (lines.length * lineHeight)) / 2);
        updateInterface();
    }

    function setRegionFromPoints(region, start, end) {
        const x = Math.round(Math.min(start.x, end.x));
        const y = Math.round(Math.min(start.y, end.y));
        const width = Math.round(Math.abs(end.x - start.x));
        const height = Math.round(Math.abs(end.y - start.y));

        region.region.x = clamp(x, 0, els.canvas.width);
        region.region.y = clamp(y, 0, els.canvas.height);
        region.region.width = clamp(width, 1, els.canvas.width - region.region.x);
        region.region.height = clamp(height, 1, els.canvas.height - region.region.y);
    }

    function moveRegion(region, point) {
        const initial = state.interaction.initial;
        const dx = Math.round(point.x - state.interaction.start.x);
        const dy = Math.round(point.y - state.interaction.start.y);
        const nextX = clamp(initial.region.x + dx, 0, els.canvas.width - initial.region.width);
        const nextY = clamp(initial.region.y + dy, 0, els.canvas.height - initial.region.height);
        const movedX = nextX - initial.region.x;
        const movedY = nextY - initial.region.y;

        region.region.x = nextX;
        region.region.y = nextY;
        region.text.x = clamp(initial.text.x + movedX, 0, els.canvas.width);
        region.text.y = clamp(initial.text.y + movedY, 0, els.canvas.height);
    }

    function resizeRegion(region, point) {
        const initial = state.interaction.initial.region;
        const minSize = 6;
        let left = initial.x;
        let top = initial.y;
        let right = initial.x + initial.width;
        let bottom = initial.y + initial.height;
        const handle = state.interaction.handle || '';

        if (handle.includes('w')) left = clamp(point.x, 0, right - minSize);
        if (handle.includes('e')) right = clamp(point.x, left + minSize, els.canvas.width);
        if (handle.includes('n')) top = clamp(point.y, 0, bottom - minSize);
        if (handle.includes('s')) bottom = clamp(point.y, top + minSize, els.canvas.height);

        region.region.x = Math.round(left);
        region.region.y = Math.round(top);
        region.region.width = Math.round(right - left);
        region.region.height = Math.round(bottom - top);
    }

    function hitTest(point) {
        const ordered = [...state.regions].reverse();

        for (const region of ordered) {
            const handle = hitHandle(region, point);
            if (handle) return { region, mode: 'resize', handle };
        }

        for (const region of ordered) {
            if (point.x >= region.region.x &&
                point.x <= region.region.x + region.region.width &&
                point.y >= region.region.y &&
                point.y <= region.region.y + region.region.height) {
                return { region, mode: 'move' };
            }
        }

        return null;
    }

    function hitHandle(region, point) {
        const size = getHandleSize();
        const handles = getRegionHandles(region);
        return Object.keys(handles).find(handle => {
            const pos = handles[handle];
            return Math.abs(point.x - pos.x) <= size && Math.abs(point.y - pos.y) <= size;
        });
    }

    function getRegionHandles(region) {
        const box = region.region;
        return {
            nw: { x: box.x, y: box.y },
            ne: { x: box.x + box.width, y: box.y },
            sw: { x: box.x, y: box.y + box.height },
            se: { x: box.x + box.width, y: box.y + box.height },
        };
    }

    function getCanvasPoint(event) {
        const rect = els.canvas.getBoundingClientRect();
        const scaleX = els.canvas.width / Math.max(rect.width, 1);
        const scaleY = els.canvas.height / Math.max(rect.height, 1);
        return {
            x: clamp((event.clientX - rect.left) * scaleX, 0, els.canvas.width),
            y: clamp((event.clientY - rect.top) * scaleY, 0, els.canvas.height),
        };
    }

    function updateSelectedRegionFromControls(mode) {
        const selected = getSelectedRegion();
        if (!selected || !state.sampleImage) return;

        const controls = els.controls;

        if (mode === 'region') {
            selected.region.x = clamp(readNumber(controls.regionX, selected.region.x), 0, els.canvas.width - selected.region.width);
            selected.region.y = clamp(readNumber(controls.regionY, selected.region.y), 0, els.canvas.height - selected.region.height);
            selected.region.width = clamp(readNumber(controls.regionWidth, selected.region.width), 1, els.canvas.width - selected.region.x);
            selected.region.height = clamp(readNumber(controls.regionHeight, selected.region.height), 1, els.canvas.height - selected.region.y);
        }

        if (mode === 'cover') {
            selected.cover.color = controls.coverColor.value;
        }

        if (mode === 'text' || mode === 'text-soft') {
            selected.text.content = controls.replacementText.value;
            selected.text.fontSize = clamp(readNumber(controls.textFontSize, selected.text.fontSize), 1, 400);
            selected.text.fontFamily = controls.textFontFamily.value;
            selected.text.color = controls.textColor.value;
            selected.text.x = clamp(readNumber(controls.textX, selected.text.x), 0, els.canvas.width);
            selected.text.y = clamp(readNumber(controls.textY, selected.text.y), 0, els.canvas.height);
        }

        updateInterface({ syncControls: mode !== 'text-soft' });
    }

    function updateInterface(options = {}) {
        const syncControls = options.syncControls !== false;
        renderCanvas();
        renderRegionList();
        if (syncControls) syncRegionControls();
        updateButtons();
    }

    function renderCanvas() {
        if (!state.sampleImage) {
            ctx.clearRect(0, 0, els.canvas.width, els.canvas.height);
            return;
        }

        ctx.clearRect(0, 0, els.canvas.width, els.canvas.height);
        ctx.drawImage(state.sampleImage, 0, 0);

        state.regions.forEach(region => drawRegionAction(region));
        state.regions.forEach((region, index) => drawRegionFrame(region, index));
    }

    function drawRegionAction(region) {
        const box = region.region;
        ctx.save();
        ctx.fillStyle = region.cover.color || '#ffffff';
        ctx.fillRect(box.x, box.y, box.width, box.height);

        const content = region.text.content || '';
        if (content.trim()) {
            const fontSize = region.text.fontSize || 32;
            const lineHeight = fontSize * 1.2;
            ctx.font = `${fontSize}px ${FONT_CSS[region.text.fontFamily] || FONT_CSS['arial.ttf']}`;
            ctx.fillStyle = region.text.color || '#000000';
            ctx.textBaseline = 'top';
            getTextLines(content).forEach((line, lineIndex) => {
                ctx.fillText(line, region.text.x, region.text.y + (lineIndex * lineHeight));
            });
        }
        ctx.restore();
    }

    function drawRegionFrame(region, index) {
        const selected = region.id === state.selectedId;
        const box = region.region;
        const displayScale = getDisplayScale();
        const lineWidth = Math.max(1, 2 * displayScale);
        const labelSize = Math.max(10 * displayScale, 10);
        const label = `#${index + 1} ${box.x},${box.y} ${box.width}x${box.height}`;

        ctx.save();
        ctx.lineWidth = lineWidth;
        ctx.strokeStyle = selected ? '#2563eb' : '#0f172a';
        ctx.setLineDash(selected ? [] : [6 * displayScale, 4 * displayScale]);
        ctx.strokeRect(box.x, box.y, box.width, box.height);

        ctx.font = `${labelSize}px Arial, sans-serif`;
        const metrics = ctx.measureText(label);
        const labelWidth = metrics.width + (10 * displayScale);
        const labelHeight = labelSize + (6 * displayScale);
        const labelY = Math.max(0, box.y - labelHeight);

        ctx.setLineDash([]);
        ctx.fillStyle = selected ? '#2563eb' : 'rgba(15, 23, 42, 0.85)';
        ctx.fillRect(box.x, labelY, labelWidth, labelHeight);
        ctx.fillStyle = '#ffffff';
        ctx.textBaseline = 'top';
        ctx.fillText(label, box.x + (5 * displayScale), labelY + (3 * displayScale));

        if (selected) drawHandles(region);
        ctx.restore();
    }

    function drawHandles(region) {
        const size = getHandleSize();
        const handles = getRegionHandles(region);
        ctx.fillStyle = '#ffffff';
        ctx.strokeStyle = '#2563eb';
        ctx.lineWidth = Math.max(1, 2 * getDisplayScale());

        Object.values(handles).forEach(handle => {
            ctx.beginPath();
            ctx.rect(handle.x - size, handle.y - size, size * 2, size * 2);
            ctx.fill();
            ctx.stroke();
        });
    }

    function renderRegionList() {
        if (!state.regions.length) {
            els.regionList.innerHTML = '<div class="region-empty">No regions yet</div>';
            return;
        }

        els.regionList.innerHTML = state.regions.map((region, index) => {
            const box = region.region;
            const selected = region.id === state.selectedId ? ' selected' : '';
            return `
                <button type="button" class="region-list-item${selected}" data-region-id="${region.id}">
                    <span>Region ${index + 1}</span>
                    <strong>${box.x}, ${box.y} - ${box.width} x ${box.height}</strong>
                </button>
            `;
        }).join('');
    }

    function syncRegionControls() {
        const selected = getSelectedRegion();
        const controls = Object.values(els.controls);
        controls.forEach(control => { control.disabled = !selected; });
        els.deleteRegionBtn.disabled = !selected;
        els.centerTextBtn.disabled = !selected;

        if (!selected) {
            els.controls.replacementText.value = '';
            return;
        }

        els.controls.regionX.value = selected.region.x;
        els.controls.regionY.value = selected.region.y;
        els.controls.regionWidth.value = selected.region.width;
        els.controls.regionHeight.value = selected.region.height;
        els.controls.coverColor.value = selected.cover.color;
        els.controls.replacementText.value = selected.text.content;
        els.controls.textFontSize.value = selected.text.fontSize;
        els.controls.textFontFamily.value = selected.text.fontFamily;
        els.controls.textColor.value = selected.text.color;
        els.controls.textX.value = selected.text.x;
        els.controls.textY.value = selected.text.y;
    }

    function updateButtons() {
        els.clearRegionsBtn.disabled = state.regions.length === 0;
        els.processBatchBtn.disabled = !state.sampleImage || state.batchFiles.length === 0 || state.regions.length === 0;
    }

    async function processBatch() {
        if (!state.sampleImage) return alert('Upload a sample certificate first.');
        if (!state.batchFiles.length) return alert('Upload certificate images for batch processing.');
        if (!state.regions.length) return alert('Draw at least one region before processing.');

        const formData = new FormData();
        formData.append('edits', JSON.stringify({
            version: 1,
            source: {
                width: els.canvas.width,
                height: els.canvas.height,
            },
            actions: serializeRegions(),
        }));

        state.batchFiles.forEach(file => formData.append('certificates', file, file.name));

        els.bulkProgress.style.display = 'block';
        els.bulkFill.style.width = '35%';
        els.bulkStatus.textContent = `Processing ${state.batchFiles.length} upload${state.batchFiles.length === 1 ? '' : 's'}...`;
        els.bulkDetail.textContent = '';
        els.processBatchBtn.disabled = true;

        try {
            const response = await fetch('/bulk-editor/process', {
                method: 'POST',
                body: formData,
            });

            if (!response.ok) {
                let message = 'Batch processing failed.';
                try {
                    const result = await response.json();
                    message = result.error || message;
                } catch (err) {
                    message = await response.text();
                }
                throw new Error(message);
            }

            const blob = await response.blob();
            els.bulkFill.style.width = '100%';
            els.bulkStatus.textContent = 'Done';
            els.bulkDetail.textContent = 'Edited certificates exported as a ZIP archive.';
            downloadBlob(blob, 'bulk_edited_certificates.zip');
        } catch (err) {
            els.bulkFill.style.width = '0%';
            els.bulkStatus.textContent = 'Export failed';
            els.bulkDetail.textContent = err.message;
        } finally {
            updateButtons();
        }
    }

    function serializeRegions() {
        return state.regions.map(region => ({
            type: 'cover_text',
            region: {
                x: Math.round(region.region.x),
                y: Math.round(region.region.y),
                width: Math.round(region.region.width),
                height: Math.round(region.region.height),
            },
            cover: {
                color: region.cover.color,
            },
            text: {
                content: region.text.content,
                fontSize: Math.round(region.text.fontSize),
                fontFamily: region.text.fontFamily,
                color: region.text.color,
                x: Math.round(region.text.x),
                y: Math.round(region.text.y),
            },
        }));
    }

    function downloadBlob(blob, filename) {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    function getDisplayScale() {
        const rect = els.canvas.getBoundingClientRect();
        return els.canvas.width / Math.max(rect.width, 1);
    }

    function getHandleSize() {
        return Math.max(6 * getDisplayScale(), 6);
    }

    function getTextLines(content) {
        const lines = String(content || '').split(/\r?\n/);
        return lines.length ? lines : [''];
    }

    function readNumber(input, fallback) {
        const value = Number(input.value);
        return Number.isFinite(value) ? Math.round(value) : fallback;
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function escapeHtml(value) {
        return String(value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }
})();
