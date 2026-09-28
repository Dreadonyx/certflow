const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = readFileSync(path.join(__dirname, '../templates/index.html'), 'utf8');
const handler = html.slice(html.indexOf('        // ── Preview ─'), html.indexOf('        // ── Batch download'));

test('preview click displays the generated image without a JavaScript error', async () => {
    let click;
    const alerts = [];
    const image = 'data:image/png;base64,preview';
    const elements = {
        previewBtn: {addEventListener: (_, callback) => { click = callback; }},
        previewImage: {scrollIntoView() {}},
        previewArea: {style: {display: 'none'}},
    };
    const values = ['Alice', 'IT', 'Gold', '2026'];
    const fields = [{column: 3, X: 10, Y: 20}];
    const context = {
        document: {getElementById: id => elements[id]},
        parseParticipants: () => [{name: 'Alice', values}],
        templateImage: 'template',
        getSettings: () => ({fields}),
        alert: message => alerts.push(message),
        fetch: async (url, options) => {
            assert.equal(url, '/generate');
            const payload = JSON.parse(options.body);
            assert.deepEqual(payload.values, values);
            assert.deepEqual(payload.fields, fields);
            return {json: async () => ({success: true, image})};
        },
    };
    vm.runInNewContext(handler, context);
    await click();
    assert.deepEqual(alerts, []);
    assert.equal(elements.previewImage.src, image);
    assert.equal(elements.previewArea.style.display, 'block');
});
