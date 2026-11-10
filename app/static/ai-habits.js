'use strict';

document.addEventListener('DOMContentLoaded', () => {
    const byId = id => document.getElementById(id);
    const token = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

    function element(tag, classes, text) {
        const node = document.createElement(tag);
        node.className = classes;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    async function postJson(url, payload) {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || 'Request failed.');
        return data;
    }

    function addButton(classes, habit) {
        const button = element('button', classes);
        button.type = 'button';
        button.append(element('i', 'fas fa-plus mr-1'), document.createTextNode('Add'));
        button.addEventListener('click', async () => {
            try {
                const result = await postJson('/habits/new', {
                    habit_name: String(habit.habit_name),
                    icon: String(habit.icon || 'fas fa-star'),
                    frequency: String(habit.frequency || 'Every day this week'),
                    time_period: String(habit.time_period || 'Morning'),
                    note: habit.note
                });
                if (result.success) window.location.reload();
                else alert(result.message);
            } catch (error) {
                alert(error.message);
            }
        });
        return button;
    }

    const modal = byId('aiRecommendModal');
    const modalContent = byId('aiRecommendModalContent');
    const recommend = byId('aiSubmitGoal');
    const recommendations = byId('aiResults');
    const recommendError = message => recommendations.replaceChildren(
        element('div', 'text-center py-4 text-red-500', message)
    );

    function hideModal() {
        modalContent.classList.remove('scale-100', 'opacity-100');
        modalContent.classList.add('scale-95', 'opacity-0');
        setTimeout(() => modal.classList.add('hidden'), 300);
    }

    byId('aiRecommendBtn')?.addEventListener('click', () => {
        modal.classList.remove('hidden');
        setTimeout(() => {
            modalContent.classList.remove('scale-95', 'opacity-0');
            modalContent.classList.add('scale-100', 'opacity-100');
        }, 10);
    });
    byId('aiCloseModal').addEventListener('click', hideModal);
    modal.addEventListener('click', event => {
        if (event.target === modal) hideModal();
    });

    recommend.addEventListener('click', async () => {
        const goal = byId('aiGoalInput').value.trim();
        if (!goal) { alert('Please describe your goal.'); return; }
        const loading = element('div', 'text-center py-4');
        loading.append(
            element('i', 'fas fa-spinner fa-spin text-3xl text-purple-600'),
            element('p', 'mt-2 text-gray-500', 'Analyzing...')
        );
        recommendations.replaceChildren(loading);
        recommend.disabled = true;
        try {
            const data = await postJson('/ai/recommend', { goal });
            if (!data.success || !Array.isArray(data.suggestions) || !data.suggestions.length) {
                recommendError(data.message || 'No suggestions found.');
                return;
            }
            const list = element('div', 'space-y-3');
            for (const suggestion of data.suggestions) {
                const card = element('div', 'bg-gray-50 rounded-lg p-4 border border-gray-200');
                const row = element('div', 'flex items-center justify-between');
                const description = document.createElement('div');
                description.append(element('span', 'text-lg font-semibold text-gray-800', suggestion.name));
                if (suggestion.reason) {
                    description.append(element('p', 'text-sm text-gray-500 mt-1', suggestion.reason));
                }
                row.append(description, addButton(
                    'ml-2 bg-blue-500 text-white px-3 py-1 rounded text-sm hover:bg-blue-600 create-from-ai whitespace-nowrap',
                    { ...suggestion, habit_name: suggestion.name, note: 'Created via AI Recommend' }
                ));
                card.append(row);
                list.append(card);
            }
            recommendations.replaceChildren(list);
        } catch (error) {
            recommendError('Error: ' + error.message);
        } finally {
            recommend.disabled = false;
        }
    });

    const quickCreate = byId('aiQuickCreateBtn');
    const preview = byId('aiQuickCreateResult');
    const previewError = message => preview.replaceChildren(
        element('div', 'text-red-500 text-sm mt-1', message)
    );

    quickCreate?.addEventListener('click', async () => {
        const text = byId('aiQuickCreate').value.trim();
        if (!text) { alert('Please enter a habit description.'); return; }
        const loading = element('div', 'text-center py-2');
        loading.append(
            element('i', 'fas fa-spinner fa-spin text-yellow-600'),
            document.createTextNode(' Parsing...')
        );
        preview.classList.remove('hidden');
        preview.replaceChildren(loading);
        quickCreate.disabled = true;
        try {
            const data = await postJson('/ai/parse-habit', { text });
            if (!data.success) {
                previewError(data.message || 'Parse failed');
                return;
            }
            const habit = data.habit;
            const card = element(
                'div', 'bg-green-50 border border-green-200 rounded-lg p-3 mt-2 flex items-center justify-between'
            );
            const description = document.createElement('div');
            description.append(
                element('span', 'font-semibold text-green-800', habit.habit_name),
                element('span', 'text-sm text-gray-500 ml-2', habit.frequency || ''),
                element('span', 'text-sm text-gray-500 ml-2', habit.time_period || '')
            );
            card.append(description, addButton(
                'bg-green-500 text-white px-3 py-1 rounded text-sm hover:bg-green-600 quick-create-save font-semibold',
                { ...habit, note: habit.note || 'Created via Quick Create' }
            ));
            preview.replaceChildren(card);
        } catch (error) {
            previewError('Error: ' + error.message);
        } finally {
            quickCreate.disabled = false;
        }
    });
});
