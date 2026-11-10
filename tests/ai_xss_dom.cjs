'use strict';

// Runs the shipped JavaScript without a browser, HTTP, or external dependencies.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const app = join(__dirname, '..', 'app');
const script = new vm.Script(readFileSync(join(app, 'static', 'ai-habits.js'), 'utf8'));
const csrfToken = 'offline-test-token';

class Node {
    constructor(tag) {
        this.tag = tag;
        this.children = [];
        this.listeners = new Map();
        this.classes = new Set();
        this.classList = {
            add: (...names) => names.forEach(name => this.classes.add(name)),
            remove: (...names) => names.forEach(name => this.classes.delete(name))
        };
    }

    set innerHTML(_value) { assert.fail('Unexpected HTML parsing sink'); }
    set outerHTML(_value) { assert.fail('Unexpected HTML parsing sink'); }
    insertAdjacentHTML() { assert.fail('Unexpected HTML parsing sink'); }

    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(name, handler) { this.listeners.set(name, handler); }
    click() { return this.listeners.get('click')({ target: this }); }

    set textContent(value) { this.children = [String(value)]; }
    get textContent() {
        return this.children.map(child => typeof child === 'string' ? child : child.textContent).join('');
    }

    *elements() {
        yield this;
        for (const child of this.children) if (child instanceof Node) yield* child.elements();
    }
}

function page(replies = []) {
    const nodes = new Map();
    const calls = [];
    const alerts = [];
    let reloads = 0;
    function get(id) {
        if (!nodes.has(id)) nodes.set(id, new Node(id));
        return nodes.get(id);
    }
    const sandbox = vm.createContext({
        document: {
            getElementById: get,
            createElement: tag => new Node(tag),
            createTextNode: text => text,
            querySelector(selector) {
                assert.equal(selector, 'meta[name="csrf-token"]');
                return { getAttribute: name => name === 'content' ? csrfToken : null };
            },
            addEventListener(name, handler) {
                assert.equal(name, 'DOMContentLoaded');
                handler();
            }
        },
        window: { location: { reload: () => reloads++ } },
        setTimeout: handler => handler(),
        alert: message => alerts.push(message),
        async fetch(url, request) {
            calls.push({ url, body: JSON.parse(request.body) });
            assert.equal(request.method, 'POST');
            assert.equal(request.headers['X-CSRFToken'], csrfToken);
            assert.equal(request.headers['Content-Type'], 'application/json');
            assert.ok(replies.length, 'Only queued offline responses are allowed');
            const reply = replies.shift();
            if (reply instanceof Error) throw reply;
            return { ok: reply.ok ?? true, json: async () => reply.data };
        }
    });
    return { get, sandbox, calls, alerts, reloads: () => reloads };
}

function buttons(root) {
    return [...root.elements()].filter(node => node.tag === 'button');
}

function assertSafeNodes(root) {
    const nodes = [...root.elements()].slice(1);
    for (const node of nodes) {
        assert.ok(['div', 'span', 'p', 'i', 'button'].includes(node.tag));
        for (const key of Object.keys(node)) {
            assert.doesNotMatch(key, /^(on|href|src|dataset)/i);
        }
    }
}

test('stored greeting is written as text by the shipped template script', () => {
    const browser = page();
    const home = readFileSync(join(app, 'templates', 'home.html'), 'utf8');
    const inline = home.match(/<script>([\s\S]*?)<\/script>/)[1];
    const username = '\\x3cimg src=x onerror=alert(1)\\x3e </script> "&';
    const json = JSON.stringify(username).replaceAll('<', '\\u003c').replaceAll('>', '\\u003e');
    vm.runInContext(inline.replace('{{ current_user.username|tojson }}', json), browser.sandbox);
    assert.ok(browser.get('greetingText').textContent.endsWith(', ' + username + '!'));
    assert.deepEqual(browser.get('greetingText').children, [browser.get('greetingText').textContent]);
});

test('recommendation list renders raw strings, preserves selection and cancel', async () => {
    const goal = '阅读 "<svg onload=alert(1)> & 更好';
    const name = '习惯 " onclick="alert(1)';
    const icon = '" onmouseover="alert(1)';
    const reason = '<script>alert(1)</script> &lt;img&gt;\n第二行';
    const browser = page([
        { data: { success: true, suggestions: [
            { name: '普通中文', reason: 'Less < More > & "quotes"' },
            { name, reason, icon, frequency: 'Twice a week', time_period: 'Evening' }
        ] } },
        { data: { success: true } }
    ]);
    script.runInContext(browser.sandbox);
    browser.get('aiGoalInput').value = goal;
    browser.get('aiRecommendBtn').click();
    await browser.get('aiSubmitGoal').click();
    const result = browser.get('aiResults');
    assertSafeNodes(result);
    assert.equal(buttons(result).length, 2);
    assert.ok(result.textContent.includes('普通中文Less < More > & "quotes"'));
    assert.ok(result.textContent.includes(name + reason));
    browser.get('aiCloseModal').click();
    assert.ok(browser.get('aiRecommendModal').classes.has('hidden'));
    assert.deepEqual(browser.calls, [{ url: '/ai/recommend', body: { goal } }]);
    browser.get('aiRecommendBtn').click();
    await buttons(result)[1].click();
    assert.deepEqual(browser.calls[1], {
        url: '/habits/new',
        body: { habit_name: name, icon, frequency: 'Twice a week', time_period: 'Evening',
                note: 'Created via AI Recommend' }
    });
    assert.equal(browser.reloads(), 1);
});

test('recommendation empty result and request error are rendered as text', async () => {
    const message = '<svg onload=alert(1)> javascript:alert(1)';
    const browser = page([
        { data: { success: true, suggestions: [] } },
        { ok: false, data: { message } }
    ]);
    script.runInContext(browser.sandbox);
    browser.get('aiGoalInput').value = 'test';
    const button = browser.get('aiSubmitGoal');
    await button.click();
    assert.equal(browser.get('aiResults').textContent, 'No suggestions found.');
    await button.click();
    assert.equal(browser.get('aiResults').textContent, 'Error: ' + message);
    assertSafeNodes(browser.get('aiResults'));
    assert.equal(button.disabled, false);
});

test('quick create keeps preview inert and sends the original note only after Add', async () => {
    const text = '<img src=x onerror=alert(1)> 我要练习';
    const habit = { habit_name: '<svg onload=alert(1)>', frequency: 'Twice a week',
        time_period: 'Morning', icon: 'fas fa-book', note: '" onclick="alert(1) &lt;script&gt;' };
    const browser = page([{ data: { success: true, habit } }, { data: { success: true } }]);
    script.runInContext(browser.sandbox);
    browser.get('aiQuickCreate').value = text;
    await browser.get('aiQuickCreateBtn').click();
    assert.deepEqual(browser.calls, [{ url: '/ai/parse-habit', body: { text } }]);
    const preview = browser.get('aiQuickCreateResult');
    assertSafeNodes(preview);
    assert.equal(preview.textContent, habit.habit_name + 'Twice a weekMorningAdd');
    assert.equal(buttons(preview).length, 1);
    await buttons(preview)[0].click();
    assert.deepEqual(browser.calls[1], { url: '/habits/new', body: habit });
    assert.equal(browser.reloads(), 1);
});

test('quick create failures are text and have no Add action', async () => {
    const message = '<img src=x onerror=alert(1)>';
    const browser = page([{ data: { success: false, message } }, new Error(message)]);
    script.runInContext(browser.sandbox);
    browser.get('aiQuickCreate').value = 'test';
    const button = browser.get('aiQuickCreateBtn');
    const preview = browser.get('aiQuickCreateResult');
    await button.click();
    assert.equal(preview.textContent, message);
    await button.click();
    assert.equal(preview.textContent, 'Error: ' + message);
    assertSafeNodes(preview);
    assert.equal(buttons(preview).length, 0);
    assert.equal(button.disabled, false);
});
