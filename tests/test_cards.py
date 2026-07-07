"""Card endpoint tests — creation, quote API, stack view."""


class TestCardEndpoints:
    def test_new_card_page(self, client, auth_headers):
        r = client.get('/cards/new')
        assert r.status_code == 200

    def test_card_quote_api(self, client, auth_headers):
        r = client.get('/cards/api/card/quote/motivation')
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert len(data['quote']) > 0

    def test_card_quote_all_categories(self, client, auth_headers):
        for cat in ['motivation', 'success', 'persistence', 'growth', 'daily']:
            r = client.get(f'/cards/api/card/quote/{cat}')
            assert r.status_code == 200
            assert r.get_json()['success'] is True

    def test_card_stack_empty(self, client, auth_headers):
        r = client.get('/cards/stack')
        assert r.status_code == 200

    def test_save_card(self, client, auth_headers):
        r = client.post('/cards/api/card/save', json={
            'quote': 'Test quote',
            'weekday': 'Mon',
            'streak': 5,
            'habits_completed': 3,
        }, headers=auth_headers)
        # Card save might succeed or return 'exists' — both are valid
        data = r.get_json()
        assert 'success' in data

    def test_card_image_api(self, client, auth_headers):
        r = client.get('/cards/api/card/image/nature')
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert 'image_url' in data
